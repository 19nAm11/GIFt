"""Step 3: search the index.

How a search works:
  1. Turn the user's sentence into a vector (same model as in index.py).
  2. Ask the database for the stored vectors closest to it.
  3. Return the GIFs those vectors belong to.

Try it:  python search.py a dog being confused
"""
from functools import lru_cache
from pathlib import Path

from config import COLLECTION, DB_PATH, MODEL_NAME, ROOT

MAX_QUERY_CHARS = 500
MAX_RESULTS = 20

MODEL_CACHE = Path(ROOT) / "web" / ".cache" / "models"
_collection = None


@lru_cache(maxsize=1)
def get_model():
    from sentence_transformers import SentenceTransformer

    try:
        return SentenceTransformer(MODEL_NAME, cache_folder=str(MODEL_CACHE), device="cpu", local_files_only=True)
    except OSError:
        return SentenceTransformer(MODEL_NAME, cache_folder=str(MODEL_CACHE), device="cpu")


def _load():
    """Use the original index when present, or the full web index otherwise."""
    import chromadb

    global _collection
    if _collection is None and Path(DB_PATH).is_dir():
        try:
            _collection = chromadb.PersistentClient(path=DB_PATH).get_collection(COLLECTION)
        except chromadb.errors.NotFoundError:
            pass
    if _collection is not None:
        return get_model(), _collection

    from web.engine import get_collection

    try:
        collection = get_collection()
    except Exception as error:
        raise RuntimeError("No GIF index found. Run `python index.py` or build the full web index first.") from error
    return get_model(), collection


def query_collection(model, collection, query, limit, *, normalize=False, **options):
    vector = model.encode([query], normalize_embeddings=normalize).tolist()
    return collection.query(query_embeddings=vector, n_results=limit, **options)


def search_gif(query, k=10):
    """Find the k GIFs that best match `query`.

    Returns a list of dicts: {"url": ..., "description": ..., "score": ...}
    score is between 0 and 1. Higher = better match.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Query must be a non-empty string.")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"Query must be {MAX_QUERY_CHARS} characters or fewer.")
    if type(k) is not int or not 1 <= k <= MAX_RESULTS:
        raise ValueError(f"Results must be an integer between 1 and {MAX_RESULTS}.")

    model, collection = _load()

    result = query_collection(model, collection, query, k)

    # Chroma returns lists of lists (one inner list per query). We sent one query, so use [0].
    matches = []
    for meta, description, distance in zip(
        result["metadatas"][0], result["documents"][0], result["distances"][0]
    ):
        matches.append({
            "url": meta["url"],
            "description": description,
            "score": 1 - distance,  # Chroma gives distance (small = close). Flip it into a score.
        })
    return matches


if __name__ == "__main__":
    import sys

    # Use the words typed after "python search.py" as the query.
    query = " ".join(sys.argv[1:]) or "a dog being confused"
    for r in search_gif(query):
        print(f"{r['score']:.3f}  {r['description']}  {r['url']}")
