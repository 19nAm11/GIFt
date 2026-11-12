"""Phase 2: serve the website and query the local Chroma index."""
import asyncio
import logging
from random import Random
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Literal

import re

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .engine import MODEL_NAME, ROOT, Retriever, catalog_fingerprint, close_database, read_catalog
from .accounts import AccountStore, COOKIE_NAME, SESSION_SECONDS

from .auto_search import AutoSearch, calibrate
from .tools import benchmark_lock, router as tools_router

logger = logging.getLogger("gifly")


def calibrate_search(auto, fingerprint):
    try:
        with benchmark_lock:
            auto.methods = calibrate(fingerprint, auto)
        logger.info("Automatic search policy ready: %s", auto.methods)
    except Exception:
        logger.exception("Automatic benchmark failed. Semantic search remains available.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.accounts = AccountStore()
    app.state.auto_search = None
    calibration = None
    app.state.catalog = []
    app.state.by_id = {}
    app.state.id_by_url = {}
    app.state.source = "GIF"
    app.state.categories = []
    app.state.caption_count = 0
    app.state.retriever = None
    app.state.message = "Search is not ready. Run python -m web.ingest, then restart the server."
    try:
        records = read_catalog()
        app.state.catalog = records
        app.state.by_id = {row["id"]: row for row in records}
        app.state.id_by_url = {row["url"]: row["id"] for row in records}
        sources = {row.get("source", "Sample") for row in records}
        app.state.source = next(iter(sources)) if len(sources) == 1 else "GIF"
        app.state.categories = sorted({row["category"] for row in records} - {"general"})
        app.state.caption_count = sum(row.get("caption_count", 1) for row in records)
        retriever = Retriever()
        fingerprint = catalog_fingerprint(records)
        if (retriever.collection.metadata or {}).get("catalog_fingerprint") != fingerprint:
            raise ValueError("Catalog changed. Run python -m web.ingest before restarting.")
        app.state.retriever = retriever
    except Exception:
        close_database()
        logger.exception("Search unavailable. Run python -m web.ingest, then restart the server.")
    if app.state.retriever is not None:
        try:
            app.state.auto_search = AutoSearch(records)
            if app.state.source == "TGIF" and len(records) >= 1000:
                calibration = asyncio.create_task(asyncio.to_thread(calibrate_search, app.state.auto_search, fingerprint))
        except Exception:
            logger.exception("Keyword index unavailable. Semantic search remains available.")
    try:
        yield
    finally:
        if calibration is not None:
            await calibration
        if app.state.retriever is not None:
            app.state.retriever.close()



app = FastAPI(title="gifly", lifespan=lifespan)
app.include_router(tools_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://{host}:{port}" for host in ("127.0.0.1", "localhost", "[::1]") for port in (5500, 5501)],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Accept", "Content-Type"],
)
app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")


@app.middleware("http")
async def protect_account_requests(request: Request, call_next):
    # JSON mutations are accepted only from this site or the local preview.
    if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
        origin = request.headers.get("origin")
        allowed = {str(request.base_url).rstrip("/")} | {
            f"http://{host}:{port}" for host in ("127.0.0.1", "localhost", "[::1]") for port in (5500, 5501)
        }
        if origin and origin not in allowed:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "This request origin is not allowed."}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith(("/api/auth", "/api/favorites")):
        response.headers["Cache-Control"] = "no-store"
    return response


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)


def current_user(request: Request):
    user = app.state.accounts.user_for(request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(401, "Please log in to save your favorite GIFs.")
    return user


def account_response(user):
    return {"user": user, "saved": app.state.accounts.saved(user["id"]) if user else []}


def begin_session(user, request, response):
    token = app.state.accounts.new_session(user["id"], request.cookies.get(COOKIE_NAME))
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")
    return account_response(user)


@app.get("/api/auth/me")
def me(request: Request):
    return account_response(app.state.accounts.user_for(request.cookies.get(COOKIE_NAME)))


@app.post("/api/auth/register", status_code=201)
def register(credentials: Credentials):
    username = credentials.username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise HTTPException(422, "Use 3–32 letters, numbers, dots, underscores or hyphens for your username.")
    try:
        user = app.state.accounts.register(username, credentials.password)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return {"username": user["username"], "message": "Account created. Please log in with your new account."}


@app.post("/api/auth/login")
def login(credentials: Credentials, request: Request, response: Response):
    user = app.state.accounts.login(credentials.username.strip(), credentials.password)
    if user is None:
        raise HTTPException(401, "Incorrect username or password.")
    return begin_session(user, request, response)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    app.state.accounts.logout(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME)
    return {"user": None, "saved": []}


@app.get("/api/favorites")
def favorites(user=Depends(current_user)):
    return {"saved": app.state.accounts.saved(user["id"])}


@app.put("/api/favorites/{gif_id}")
def save_favorite(gif_id: str, user=Depends(current_user)):
    if gif_id not in app.state.by_id:
        raise HTTPException(404, "This GIF is not in the current collection.")
    app.state.accounts.save(user["id"], gif_id, True)
    return {"id": gif_id, "saved": True}


@app.delete("/api/favorites/{gif_id}")
def remove_favorite(gif_id: str, user=Depends(current_user)):
    app.state.accounts.save(user["id"], gif_id, False)
    return {"id": gif_id, "saved": False}


@app.get("/api/health")
def health():
    ready = app.state.retriever is not None
    return {
        "ready": ready, "backend": "chroma", "model": MODEL_NAME,
        "count": len(app.state.catalog), "source": app.state.source,
        "message": "Ready to search." if ready else app.state.message,
        "caption_count": app.state.caption_count,
    }


@app.get("/api/catalog")
def catalog(limit: int = Query(default=24, ge=1, le=60), offset: int = Query(default=0, ge=0),
            category: Literal["all", "reactions", "animals", "celebration", "mood", "general"] = "all",
            ids: list[str] | None = Query(default=None, max_length=60),
            seed: int | None = Query(default=None, ge=0, le=2**32 - 1)):
    if ids is not None:
        records = [app.state.by_id[key] for key in dict.fromkeys(ids) if key in app.state.by_id]
        return {"gifs": records, "total": len(records), "has_more": False, "source": app.state.source}
    records = app.state.catalog
    if category != "all":
        records = [row for row in records if row["category"] == category]
    if seed is not None:
        records = records.copy()
        Random(seed).shuffle(records)
    return {"gifs": records[offset:offset + limit], "total": len(records), "offset": offset,
            "has_more": offset + limit < len(records), "source": app.state.source,
            "categories": app.state.categories}


@app.get("/api/search")
def search(q: str = Query(min_length=1, max_length=300), limit: int = Query(default=24, ge=1, le=60),
           category: Literal["all", "reactions", "animals", "celebration", "mood", "general"] = "all"):
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail="Enter a keyword or a feeling to search for.")
    if app.state.retriever is None:
        raise HTTPException(status_code=503, detail=app.state.message)
    start = perf_counter()
    try:
        results = app.state.retriever.search(query, limit, category)
    except Exception:
        logger.exception("Search failed")
        raise HTTPException(status_code=503, detail="Search is unavailable right now. Please try again.") from None
    return {"query": query, "results": results, "count": len(results), "elapsed_ms": round((perf_counter() - start) * 1000)}


@app.get("/")
@app.get("/index.html", include_in_schema=False)
def index():
    return FileResponse(ROOT / "index.html")


@app.get("/style.css")
def stylesheet():
    return FileResponse(ROOT / "style.css", media_type="text/css")


@app.get("/app.js")
def javascript():
    return FileResponse(ROOT / "app.js", media_type="text/javascript")


@app.get("/api-client.js")
def api_javascript():
    return FileResponse(ROOT / "api-client.js", media_type="text/javascript")


@app.get("/tools.js", include_in_schema=False)
def tools_javascript():
    return FileResponse(ROOT / "tools.js", media_type="text/javascript")
