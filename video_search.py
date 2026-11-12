#!/usr/bin/env python3
"""Text-to-video search using cached Gemini Embedding 2 video vectors.

Run from project root:
    python video_search.py --check-only
    python video_search.py --query "a dancing cartoon character" --top-k 5
    python video_search.py
"""
import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

MODEL = "gemini-embedding-2"
DIM = 768
ROOT = Path(__file__).resolve().parent


def load_index(folder):
    vec_path = folder / "video_vectors.npy"
    meta_path = folder / "video_metadata.jsonl"
    if not vec_path.is_file() or not meta_path.is_file():
        raise FileNotFoundError(f"Missing vector/metadata files in {folder}")
    vectors = np.load(vec_path, allow_pickle=False)
    with meta_path.open(encoding="utf-8-sig") as f:
        metadata = [json.loads(line) for line in f if line.strip()]
    if vectors.ndim != 2 or vectors.shape[1] != DIM:
        raise ValueError(f"Expected vectors (N, {DIM}), got {vectors.shape}")
    if len(metadata) != len(vectors):
        raise ValueError(f"Metadata rows ({len(metadata)}) != vector rows ({len(vectors)})")
    if not np.isfinite(vectors).all():
        raise ValueError("Vectors contain NaN/Inf")
    if np.any(np.linalg.norm(vectors, axis=1) == 0):
        raise ValueError("At least one video vector is zero")
    for i, item in enumerate(metadata):
        if item.get("vector_row") != i:
            raise ValueError(f"Metadata line {i+1} has vector_row={item.get('vector_row')}, expected {i}")
        if item.get("model") != MODEL or item.get("dimension") != DIM:
            raise ValueError(f"Metadata line {i+1} uses unexpected model/dimension")
        if item.get("embedding_kind") != "direct_video":
            raise ValueError(f"Metadata line {i+1} is not direct_video")
    return vectors, metadata


def embed_text(query):
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or
            os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true"):
        raise RuntimeError('Set your API key in PowerShell: $env:GEMINI_API_KEY = "YOUR_KEY"')
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Install SDK: python -m pip install -U google-genai") from exc
    client = genai.Client()
    try:
        response = client.models.embed_content(
            model=MODEL,
            contents=query,
            config=types.EmbedContentConfig(output_dimensionality=DIM),
        )
    finally:
        if callable(getattr(client, "close", None)):
            client.close()
    embeddings = getattr(response, "embeddings", None)
    if not embeddings or not embeddings[0].values:
        raise RuntimeError("Gemini returned no text embedding")
    vector = np.asarray(embeddings[0].values, dtype=np.float32)
    if vector.shape != (DIM,) or not np.isfinite(vector).all() or np.linalg.norm(vector) == 0:
        raise ValueError(f"Invalid query vector shape/values: {vector.shape}")
    return vector


def rank(vectors, metadata, query_vector, k, video_dir):
    video_norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    cosine_scores = (vectors / video_norms) @ (query_vector / np.linalg.norm(query_vector))
    
    # Áp dụng trọng số 1.1 cho các video thuộc thư mục user_inbox
    scores = []
    for i, meta in enumerate(metadata):
        rel_path = meta.get("relative_path", "")
        base_score = cosine_scores[i]
        # Kiểm tra nếu đường dẫn chứa user_inbox thì nhân trọng số 1.1
        if "user_inbox" in rel_path.replace("\\", "/"):
            scores.append(base_score * 1.1)
        else:
            scores.append(base_score)
    scores = np.array(scores)

    for rank_num, row in enumerate(np.argsort(-scores)[:min(k, len(scores))], 1):
        relative_path = metadata[int(row)].get("relative_path", "")
        path = video_dir / relative_path
        flag = " [FILE NOT FOUND]" if not path.is_file() else ""
        print(f"{rank_num}. score={scores[row]:.4f} (cosine={cosine_scores[row]:.4f}) | {relative_path}{flag}")
        print(f"   {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", help="Text query; omit for interactive mode")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--check-only", action="store_true", help="Validate files without API call")
    parser.add_argument("--index-dir", type=Path, default=ROOT / "data/processed/gemini_embedding_2")
    # Sửa lại --video-dir trỏ chung về thư mục cha "videos" thay vì cố định vào "inbox"
    parser.add_argument("--video-dir", type=Path, default=ROOT / "data/videos")
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be >= 1")
    try:
        vectors, metadata = load_index(args.index_dir)
        print(f"OK: {len(metadata)} videos, {vectors.shape[1]} dimensions, model={MODEL}")
        missing = sum(not (args.video_dir / item.get("relative_path", "")).is_file() for item in metadata)
        if missing:
            print(f"WARNING: {missing} video files missing under {args.video_dir}; scores still work")
        if args.check_only:
            print("Index validation passed. No API request sent.")
            return 0
        if args.query:
            queries = [args.query]
        else:
            print("Type a search description (blank line to quit):")
            queries = iter(lambda: input("Query> ").strip(), "")
        for query in queries:
            if not query.strip():
                continue
            q = embed_text(query)
            print(f"\nTop {min(args.top_k, len(metadata))} results for: {query}")
            rank(vectors, metadata, q, args.top_k, args.video_dir)
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped")
        return 130


if __name__ == "__main__":
    sys.exit(main())