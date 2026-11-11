"""Build a search index with GEMINI embeddings (run this once)."""
import argparse
import time

import chromadb
from google import genai
from google.genai import errors, types

from config import GEMINI_COLLECTION, GEMINI_DB_PATH, GEMINI_DIMENSIONS as DIMENSIONS, GEMINI_MODEL
from data import load_data

BATCH_SIZE = 50
PAUSE = 35.0


def embed_batch(client, texts):
    for attempt in range(5):
        try:
            response = client.models.embed_content(
                model=GEMINI_MODEL,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    output_dimensionality=DIMENSIONS,
                ),
            )
            return [e.values for e in response.embeddings]
        except errors.APIError as e:
            if e.code not in (429, 500, 503) or attempt == 4:
                raise
            wait = 15 * (attempt + 1)
            print(f"\nGemini said {e.code} (busy). Waiting {wait}s and trying again...")
            time.sleep(wait)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()

    client = genai.Client()
    db = chromadb.PersistentClient(path=GEMINI_DB_PATH)
    collection = db.get_or_create_collection(
        GEMINI_COLLECTION, metadata={"hnsw:space": "cosine"}, embedding_function=None
    )

    df = load_data(limit=args.limit)

    done = collection.count()
    if done >= len(df):
        print(f"Index already has {done} GIFs. Delete '{GEMINI_DB_PATH}' to rebuild.")
        return
    if done:
        print(f"Resuming: {done} GIFs already indexed.")

    for start in range(done, len(df), BATCH_SIZE):
        batch = df.iloc[start:start + BATCH_SIZE]
        descriptions = batch["description"].tolist()
        vectors = embed_batch(client, descriptions)
        collection.add(
            ids=[str(i) for i in batch.index],
            embeddings=vectors,
            documents=descriptions,
            metadatas=[{"url": u} for u in batch["url"]],
        )
        print(f"Indexed {start + len(batch)}/{len(df)}", end="\r")
        time.sleep(PAUSE)

    print(f"\nDone. {collection.count()} GIFs indexed with Gemini.")


if __name__ == "__main__":
    main()