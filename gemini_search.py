"""Search the Gemini index (fast version of search.py)."""
import time

start_time = time.perf_counter()

import sys

import chromadb
from google import genai
from google.genai import types

from config import DATA_DIR

GEMINI_DB_PATH = f"{DATA_DIR}/chroma_db_gemini"
GEMINI_COLLECTION = "gif-search-gemini"
GEMINI_MODEL = "gemini-embedding-001"
DIMENSIONS = 768

MAX_QUERY_CHARS = 500
MAX_RESULTS = 20

_client = None
_collection = None


def _load():
    global _client, _collection
    if _client is None:
        try:
            db = chromadb.PersistentClient(path=GEMINI_DB_PATH)
            _collection = db.get_collection(GEMINI_COLLECTION, embedding_function=None)
        except Exception as e:
            raise RuntimeError("No Gemini index found. Run `python3 gemini_index.py` first.") from e
        _client = genai.Client()
    return _client, _collection

def embed_query(query):
    """Ask Gemini to turn one search sentence into 768 numbers (a vector)."""
    client, _ = _load()
    response = client.models.embed_content(
        model=GEMINI_MODEL,
        contents=query,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",  # "this is a question, find matches"
            output_dimensionality=DIMENSIONS,
        ),
    )
    return response.embeddings[0].values

def search_gif(query, k=10):
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Query must be a non-empty string.")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"Query must be {MAX_QUERY_CHARS} characters or fewer.")
    if type(k) is not int or not 1 <= k <= MAX_RESULTS:
        raise ValueError(f"Results must be an integer between 1 and {MAX_RESULTS}.")

    _, collection = _load()
    query_vector = embed_query(query)

    result = collection.query(query_embeddings=[query_vector], n_results=k)

    matches = []
    for meta, description, distance in zip(
        result["metadatas"][0], result["documents"][0], result["distances"][0]
    ):
        matches.append({"url": meta["url"], "description": description, "score": 1 - distance})
    return matches


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or "a dog being confused"
    for r in search_gif(query):
        print(f"{r['score']:.3f}  {r['description']}  {r['url']}")

    print(f"\nTotal time: {time.perf_counter() - start_time:.2f} s")
    print(f"torch loaded? {'torch' in sys.modules}")