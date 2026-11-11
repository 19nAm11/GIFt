"""Shared data contract and local semantic retriever. No indexing on requests."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from gift.config import MODEL_NAME
from gift.search import get_model, query_collection

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


COLLECTION_NAME = "gifly_descriptions_v2"
ENCODING_VERSION = "description-v1"
CATEGORIES = {"reactions", "animals", "celebration", "mood", "general"}
DEFAULT_DATA_PATH = ROOT / "data" / ("tgif.json" if (ROOT / "data" / "tgif.json").exists() else "gifs.json")
DATA_PATH = Path(os.environ.get("GIF_DATA_PATH", str(DEFAULT_DATA_PATH)))
DB_PATH = Path(os.environ.get("GIF_DB_PATH", str(ROOT / ".chroma")))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
_clients = {}


def read_catalog(path: Path = DATA_PATH) -> list[dict]:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise ValueError("GIF catalog must be a non-empty JSON array.")
    seen = set()
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("Each GIF must be an object.")
        for key in ("id", "title", "description", "url", "category"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f"Every GIF needs a non-empty {key}.")
        parsed = urlparse(row["url"])
        if parsed.scheme != "https" or not parsed.netloc or any(c.isspace() for c in row["url"]):
            raise ValueError(f"GIF {row['id']} needs a valid HTTPS URL.")
        if row["id"] in seen:
            raise ValueError(f"Duplicate GIF id: {row['id']}")
        if row["category"] not in CATEGORIES:
            raise ValueError(f"Invalid category for {row['id']}.")
        if not isinstance(row.get("tags"), list) or not all(isinstance(tag, str) for tag in row["tags"]):
            raise ValueError(f"GIF {row['id']} needs a list of text tags.")
        if not isinstance(row.get("ratio", 1.2), (int, float)) or not 0.2 <= row.get("ratio", 1.2) <= 5:
            raise ValueError(f"Invalid aspect ratio for {row['id']}.")
        seen.add(row["id"])
    return records


def document_for(row: dict) -> str:
    # Match both notebooks: embed the original description, without title/tags.
    return row["description"]


def metadata_for(row: dict) -> dict:
    return {key: row[key] for key in ("url", "title", "description", "category")} | {"ratio": row.get("ratio", 1.2), "source": row.get("source", "Sample")}


def catalog_fingerprint(records: list[dict]) -> str:
    payload = [(row["id"], document_for(row), metadata_for(row)) for row in sorted(records, key=lambda row: row["id"])]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def get_collection(create: bool = False, path: Path = DB_PATH):
    import chromadb
    from chromadb.config import Settings
    key = str(path.resolve())
    if key not in _clients:
        _clients[key] = chromadb.PersistentClient(path=key, settings=Settings(anonymized_telemetry=False))
    client = _clients[key]
    if create:
        collection = client.get_or_create_collection(
            name=COLLECTION_NAME, embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}}, metadata={"model": MODEL_NAME, "encoding": ENCODING_VERSION},
        )
    else:
        collection = client.get_collection(name=COLLECTION_NAME, embedding_function=None)
    if (collection.metadata or {}).get("model") != MODEL_NAME:
        raise ValueError("Index uses a different model. Create a new index before searching.")
    if (collection.metadata or {}).get("encoding") != ENCODING_VERSION:
        raise ValueError("Index uses a different text format. Run python -m web.ingest to build the current index.")
    return collection


def close_database(path: Path = DB_PATH):
    client = _clients.pop(str(path.resolve()), None)
    if client is not None:
        client.close()


class Retriever:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.collection = get_collection(path=path)
        if self.collection.count() == 0:
            raise ValueError("Index is empty. Run python -m web.ingest first.")
        self.model = get_model()

    def close(self):
        close_database(self.path)

    def search(self, query: str, limit: int, category: str = "all") -> list[dict]:
        where = {"category": category} if category != "all" else None
        count = self.collection.count()
        if count == 0:
            return []
        result = query_collection(
            self.model, self.collection, query, min(limit, count),
            normalize=True, where=where, include=["metadatas", "distances"],
        )
        return [dict(metadata, id=gif_id, score=round(max(-1.0, min(1.0, 1.0 - distance)), 4), similarity=round(max(-1.0, min(1.0, 1.0 - distance)), 4))
                for gif_id, metadata, distance in zip(result["ids"][0], result["metadatas"][0], result["distances"][0])]
