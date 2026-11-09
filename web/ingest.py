"""Phase 1: persist GIF embeddings once; re-run only when the catalog changes."""
import argparse
import sys
from pathlib import Path

from .engine import DATA_PATH, DB_PATH, catalog_fingerprint, close_database, document_for, get_collection, get_model, metadata_for, read_catalog


def ingest(path: Path = DATA_PATH, db_path: Path | None = None) -> dict:
    records = read_catalog(path)
    db_path = db_path or DB_PATH
    collection = get_collection(create=True, path=db_path)
    existing = collection.get(include=["documents", "metadatas"])
    previous = {key: (doc, meta) for key, doc, meta in zip(existing["ids"], existing["documents"], existing["metadatas"])}
    changed = [row for row in records if previous.get(row["id"]) != (document_for(row), metadata_for(row))]
    if changed:
        model = get_model()
        for offset in range(0, len(changed), 128):
            batch = changed[offset:offset + 128]
            documents = [document_for(row) for row in batch]
            vectors = model.encode(documents, normalize_embeddings=True, show_progress_bar=True).tolist()
            collection.upsert(ids=[row["id"] for row in batch], embeddings=vectors, documents=documents, metadatas=[metadata_for(row) for row in batch])
            print(f"Indexed {min(offset + len(batch), len(changed))}/{len(changed)}")
    removed = sorted(set(previous) - {row["id"] for row in records})
    if removed:
        collection.delete(ids=removed)
    fingerprint = catalog_fingerprint(records)
    if (collection.metadata or {}).get("catalog_fingerprint") != fingerprint:
        collection.modify(metadata=dict(collection.metadata or {}, catalog_fingerprint=fingerprint))
    report = {"indexed": len(changed), "unchanged": len(records) - len(changed), "removed": len(removed), "total": collection.count()}
    print(report)
    return report


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=DATA_PATH, help="JSON catalog; set GIF_DATA_PATH to the same file when serving.")
    args = parser.parse_args()
    try:
        ingest(args.file)
    finally:
        close_database()
