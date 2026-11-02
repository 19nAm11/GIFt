"""Gemini Embedding 2: embed local MP4/MOV videos and cache every successful result.

Run from GIFt-main-refactored root (PowerShell):
    python -m ingestion.embed_videos --dry-run
    python -m ingestion.embed_videos --limit 1
    python -m ingestion.embed_videos

Requires: pip install google-genai numpy
Environment: GEMINI_API_KEY (or GOOGLE_API_KEY)

Important: This writes NUMERIC VIDEO VECTORS, not text descriptions, and does
not modify an existing database. Keep query embeddings on the same model/dim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "videos" / "inbox"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "gemini_embedding_2"
MODEL = "gemini-embedding-2"
MIMES = {".mp4": "video/mp4", ".mov": "video/quicktime"}
INLINE_MAX_BYTES = 18 * 1024 * 1024  # conservative; use Files API for larger videos


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)


def atomic_jsonl(path: Path, rows: list[dict]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def atomic_array(path: Path, value: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as handle:
        np.save(handle, value, allow_pickle=False)
    os.replace(tmp, path)


def cache_files(cache_dir: Path, digest: str, dim: int) -> tuple[Path, Path]:
    parent = cache_dir / f"{MODEL}_{dim}"
    return parent / f"{digest}.npy", parent / f"{digest}.json"


def verify_vector(values: Any, dim: int) -> np.ndarray:
    array = np.asarray(values, dtype=np.float32)
    if array.ndim != 1 or array.shape[0] != dim:
        raise ValueError(f"Unexpected embedding shape {array.shape}; expected ({dim},)")
    if not np.isfinite(array).all():
        raise ValueError("Embedding contains NaN or Infinity")
    norm = float(np.linalg.norm(array))
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError("Embedding has zero/invalid norm")
    return array / norm  # explicit unit normalization; search with dot product = cosine


def load_cached(cache_dir: Path, digest: str, dim: int) -> np.ndarray | None:
    vector_file, meta_file = cache_files(cache_dir, digest, dim)
    if not vector_file.exists() or not meta_file.exists():
        return None
    try:
        info = json.loads(meta_file.read_text(encoding="utf-8"))
        if (info.get("sha256") != digest or info.get("model") != MODEL
                or info.get("dimension") != dim or info.get("modality") != "video"):
            return None
        values = np.load(vector_file, allow_pickle=False)
        return verify_vector(values, dim)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def save_cache(cache_dir: Path, digest: str, dim: int, vector: np.ndarray) -> None:
    vector_file, meta_file = cache_files(cache_dir, digest, dim)
    # Write vector first and metadata last as a commit marker.
    atomic_array(vector_file, vector)
    atomic_json(meta_file, {
        "sha256": digest,
        "model": MODEL,
        "dimension": dim,
        "modality": "video",
        "created_at_utc": now_utc(),
        "normalized": True,
    })


def wait_until_active(client: Any, remote_file: Any, *, timeout_seconds: int = 300) -> Any:
    start = time.monotonic()
    while True:
        state = getattr(getattr(remote_file, "state", None), "name", None)
        if state == "ACTIVE":
            return remote_file
        if state == "FAILED":
            raise RuntimeError("Gemini Files API could not process this video")
        if time.monotonic() - start > timeout_seconds:
            raise TimeoutError("Gemini Files API processing exceeded 300 seconds")
        time.sleep(3)
        remote_file = client.files.get(name=remote_file.name)


def embed_video(client: Any, path: Path, dimension: int) -> np.ndarray:
    from google.genai import types

    uploaded = None
    try:
        mime = MIMES[path.suffix.lower()]
        if path.stat().st_size <= INLINE_MAX_BYTES:
            content = types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)
        else:
            print("    Large file: uploading via Gemini Files API...", flush=True)
            uploaded = client.files.upload(file=str(path), config={"mime_type": mime})
            uploaded = wait_until_active(client, uploaded)
            if not uploaded.uri:
                raise ValueError("Upload succeeded but has no file URI")
            content = types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime)

        response = client.models.embed_content(
            model=MODEL,
            contents=[content],
            config=types.EmbedContentConfig(output_dimensionality=dimension),
        )
        embeddings = getattr(response, "embeddings", None)
        if not embeddings or len(embeddings) != 1 or not embeddings[0].values:
            raise RuntimeError("Gemini returned no single embedding")
        return verify_vector(embeddings[0].values, dimension)
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception as exc:
                print(f"    Warning: could not delete temporary uploaded file: {type(exc).__name__}")


def is_quota_error(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    message = str(exc).lower()
    return str(code) in ("429", "RESOURCE_EXHAUSTED") or str(status) in (
        "429", "RESOURCE_EXHAUSTED"
    ) or any(token in message for token in ("429", "resource_exhausted", "quota exceeded", "rate limit exceeded"))


def is_transient_error(exc: Exception) -> bool:
    code = str(getattr(exc, "code", ""))
    msg = str(exc).lower()
    return code in {"500", "502", "503", "504"} or any(t in msg for t in (
        "internal server error", "service unavailable", "temporarily unavailable", "503",
    ))


def build_outputs(items: list[dict], out: Path, cache_dir: Path, dim: int, *, status: str) -> dict:
    """Export only complete, valid video/cache pairs in stable sorted order."""
    rows: list[dict] = []
    vectors: list[np.ndarray] = []
    for item in items:
        digest = item["sha256"]
        vector = load_cached(cache_dir, digest, dim)
        if vector is None:
            continue
        rows.append({
            "video_id": f"video_{digest[:20]}",
            "relative_path": item["relative_path"],
            "sha256": digest,
            "file_size_bytes": item["file_size_bytes"],
            "model": MODEL,
            "dimension": dim,
            "vector_row": len(vectors),
            "embedding_kind": "direct_video",
        })
        vectors.append(vector)
    matrix = np.stack(vectors).astype(np.float32) if vectors else np.empty((0, dim), dtype=np.float32)
    # Write metadata first, matrix second. Re-running rebuilds both from cache.
    atomic_jsonl(out / "video_metadata.jsonl", rows)
    atomic_array(out / "video_vectors.npy", matrix)
    summary = {
        "model": MODEL, "dimension": dim, "status": status,
        "files_found": len(items), "vectors_ready": len(rows),
        "files_remaining": len(items) - len(rows),
        "updated_at_utc": now_utc(),
        "output_vectors": str(out / "video_vectors.npy"),
        "output_metadata": str(out / "video_metadata.jsonl"),
        "notes": "Each metadata row corresponds to the same row in video_vectors.npy; video_id is a content hash.",
    }
    atomic_json(out / "progress.json", summary)
    return summary


def run(args: argparse.Namespace, *, client: Any = None) -> dict:
    inp: Path = args.input.resolve()
    out: Path = args.output.resolve()
    if not inp.is_dir():
        raise FileNotFoundError(f"Input folder does not exist: {inp}")
    files = sorted((p for p in inp.rglob("*") if p.is_file() and p.suffix.lower() in MIMES),
                   key=lambda p: p.relative_to(inp).as_posix().lower())
    if not files:
        raise FileNotFoundError(f"No MP4/MOV videos inside {inp}")
    if args.limit < 0 or args.delay < 0 or args.dimension not in (768, 1536, 3072):
        raise ValueError("Require --limit >= 0, --delay >= 0, --dimension = 768/1536/3072")
    if out == inp or out.is_relative_to(inp):
        raise ValueError("Output cannot be inside input video folder")
    cache_dir = out / "cache"
    items = []
    print(f"INPUT: {inp}\nOUTPUT: {out}\nMODEL: {MODEL} ({args.dimension} dims)")
    for p in files:
        digest = sha256_file(p)
        rel = p.relative_to(inp).as_posix()
        cached = load_cached(cache_dir, digest, args.dimension) is not None
        items.append({"path": p, "relative_path": rel, "sha256": digest,
                      "file_size_bytes": p.stat().st_size, "cached": cached})
        print(f"  {'CACHED' if cached else 'NEW   '}  {rel} ({p.stat().st_size / 1024**2:.2f} MB)")
    new_items = [x for x in items if not x["cached"]]
    chosen = new_items[:args.limit] if args.limit else new_items
    print(f"Found {len(items)} videos; cached {len(items)-len(new_items)}; "
          f"new {len(new_items)}; to request this run {len(chosen)}", flush=True)
    if args.dry_run:
        return {"dry_run": True, "requests_planned": len(chosen)}

    out.mkdir(parents=True, exist_ok=True)
    if chosen and client is None:
        from google import genai
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("Set GEMINI_API_KEY in this PowerShell terminal first")
        client = genai.Client(api_key=key)

    status = "complete"
    processed = 0
    last_request_started: float | None = None
    for index, item in enumerate(chosen, 1):
        print(f"[{index}/{len(chosen)}] Embedding {item['relative_path']}", flush=True)
        if last_request_started is not None:
            sleep_s = args.delay - (time.monotonic() - last_request_started)
            if sleep_s > 0:
                time.sleep(sleep_s)
        last_request_started = time.monotonic()
        try:
            vector = embed_video(client, item["path"], args.dimension)
            # Avoid storing a vector for a file that changed while being uploaded.
            if sha256_file(item["path"]) != item["sha256"]:
                raise RuntimeError("Video file changed during embedding; refusing to cache")
            save_cache(cache_dir, item["sha256"], args.dimension, vector)
            processed += 1
            print(f"    SAVED ({args.dimension} values)", flush=True)
        except Exception as exc:
            status = "stopped_quota" if is_quota_error(exc) else "stopped_error"
            atomic_json(out / "last_error.json", {
                "time_utc": now_utc(), "video": item["relative_path"],
                "error_type": type(exc).__name__, "message": str(exc)[:1500],
                "status": status,
            })
            print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            if status == "stopped_quota":
                print("Quota/rate limit reached. Stop now; rerun later to resume from cached vectors.",
                      file=sys.stderr, flush=True)
            break
        finally:
            build_outputs(items, out, cache_dir, args.dimension,
                          status="running" if status == "complete" else status)
    if status == "complete" and len(chosen) < len(new_items):
        status = "partial_limit"
    summary = build_outputs(items, out, cache_dir, args.dimension, status=status)
    summary["api_requests_succeeded_this_run"] = processed
    print(f"\nREADY: {summary['vectors_ready']}/{summary['files_found']} "
          f"| new successful: {processed} | status: {status}")
    print(f"Vectors:  {out / 'video_vectors.npy'}")
    print(f"Metadata: {out / 'video_metadata.jsonl'}")
    if status.startswith("stopped_"):
        raise RuntimeError("Embedding interrupted; completed vectors saved. See last_error.json")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed MP4/MOV video with Gemini Embedding 2")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0, help="Max NEW videos this run; 0 = all")
    parser.add_argument("--dimension", type=int, default=768, choices=[768, 1536, 3072])
    parser.add_argument("--delay", type=float, default=2.0, help="Minimum seconds between starting requests")
    parser.add_argument("--dry-run", action="store_true", help="List videos/cache; never call API")
    args = parser.parse_args()
    try:
        run(args)
    except (RuntimeError, ValueError, FileNotFoundError, OSError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
