"""Step 2: build the search index (run this once).

Idea: a computer cannot compare sentences directly, but it can compare lists of numbers.
An "embedding model" turns a sentence into a list of 384 numbers (a vector).
Sentences with similar meaning get similar vectors.
We turn every GIF description into a vector and save them in a vector database (Chroma).

Usage:  python -m gift.index --limit 5000
"""
import argparse

import chromadb

from gift.config import BATCH_SIZE, COLLECTION, DB_PATH
from gift.data import add_batch, load_data
from gift.search import get_model


def main():
    # Read the optional --limit option from the command line.
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5000, help="how many GIFs to index")
    args = parser.parse_args()

    # Open (or create) the database. "cosine" = compare vectors by the angle between them.
    client = chromadb.PersistentClient(path=DB_PATH)
    collection = client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    # Do not index twice. To start over, delete the data/chroma_db folder.
    if collection.count() > 0:
        print(f"Index already has {collection.count()} GIFs. Delete the '{DB_PATH}' folder to rebuild.")
        return

    df = load_data(limit=args.limit)
    model = get_model()

    # Go through the data in small batches.
    for start in range(0, len(df), BATCH_SIZE):
        batch = df.iloc[start:start + BATCH_SIZE]
        descriptions = batch["description"].tolist()

        vectors = model.encode(descriptions).tolist()  # sentences -> numbers

        add_batch(collection, batch, vectors)
        print(f"Indexed {min(start + BATCH_SIZE, len(df))}/{len(df)}", end="\r")

    print(f"\nDone. {collection.count()} GIFs indexed.")


if __name__ == "__main__":
    main()
