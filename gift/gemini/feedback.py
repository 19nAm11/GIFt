"""Relevance feedback (Rocchio): use likes and dislikes to improve the current search.

Idea: a search is a vector (a list of numbers). When the user likes a GIF, we nudge
that vector TOWARDS the liked GIF's vector. When they dislike one, we nudge it AWAY.
Then we score every GIF again with the new vector.

    new question = ALPHA * original question
                 + BETA  * average(liked GIF vectors)
                 - GAMMA * average(disliked GIF vectors)

Only NumPy is used here: no torch, and no Gemini call. The GIF vectors are the ones
gemini_index.py already saved, loaded once into memory.

Memory: each 👎 is also saved in data/dislike_memory.json together with the search it
came from. In a later search that MEANS the same thing (its vector is close to the old
search's vector), that GIF is hidden. In a different search it shows up as normal,
because a GIF that is wrong for "a puppy playing" may be right for "someone falling".
Nothing is ever deleted from the index. (data/ is git-ignored.)

Try it:  python3 feedback.py
"""
import json
import os

import numpy as np

from gift.config import DATA_DIR

MEMORY_PATH = os.path.join(DATA_DIR, "dislike_memory.json")  # remembered dislikes

# How close two searches must be to count as "the same idea" (cosine similarity, 0 to 1).
# Gemini scores even unrelated sentences quite high, so this needs to be strict.
# Too many GIFs hidden? Raise it. Dislikes not remembered? Lower it.
SIMILAR_SEARCH = 0.80

# Classic Rocchio weights.
ALPHA = 1.0  # the user's own words matter most, so the search never drifts off topic
BETA = 0.75  # a like is strong evidence: "more like THIS"
GAMMA = 0.15  # a dislike is vaguer ("not this", but not what instead), so it pushes gently


def normalize(vector):
    """Make a vector length 1, so comparing vectors only looks at their direction (= meaning)."""
    vector = np.asarray(vector, dtype=np.float32)
    length = np.linalg.norm(vector, axis=-1, keepdims=True)
    return vector / np.maximum(length, 1e-12)  # never divide by zero


def load_index(collection):
    """Copy every GIF from the Chroma collection into memory. Do this ONCE, not on every click.

    Returns a dict:
      ids, urls, descriptions: lists, one item per GIF (same order)
      vectors: a NumPy table, one row (768 numbers) per GIF
      row_of: {gif id: row number}, to find a GIF's row quickly
    """
    data = collection.get(include=["embeddings", "documents", "metadatas"])
    return {
        "ids": list(data["ids"]),
        "urls": [meta["url"] for meta in data["metadatas"]],
        "descriptions": list(data["documents"]),
        "vectors": normalize(data["embeddings"]),
        "row_of": {gif_id: row for row, gif_id in enumerate(data["ids"])},
    }


# ---------- Memory of dislikes across searches ----------
# The memory is a list of {"gif_id": ..., "query": "the search text", "query_vector": [...]}.

def load_memory(path=MEMORY_PATH):
    """Read the remembered dislikes. Empty if there is no file yet."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):  # no file yet, or a broken file: start fresh
        return []


def save_memory(memory, path=MEMORY_PATH):
    """Write the remembered dislikes to disk."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(memory, f)


def remember_dislike(memory, gif_id, query, query_vector):
    """Add one dislike (skips it if this exact search already has it)."""
    memory = forget_dislike(memory, gif_id, query)
    vector = [float(x) for x in query_vector]  # plain numbers, so JSON can save them
    return memory + [{"gif_id": gif_id, "query": query, "query_vector": vector}]


def forget_dislike(memory, gif_id, query):
    """Remove one dislike (used when the user clicks 👎 again to undo it)."""
    return [m for m in memory if not (m["gif_id"] == gif_id and m["query"] == query)]


def _similar(memory, query_vector, threshold):
    """For each memory entry: True if its search is close enough to this one."""
    if not memory:
        return []
    past_searches = normalize([m["query_vector"] for m in memory])
    return list(past_searches @ normalize(query_vector) >= threshold)


def hidden_for(memory, query_vector, threshold=SIMILAR_SEARCH):
    """The GIF ids disliked in earlier searches that mean the same as this one."""
    return {m["gif_id"] for m, close in zip(memory, _similar(memory, query_vector, threshold)) if close}


def forget_similar(memory, query_vector, threshold=SIMILAR_SEARCH):
    """Drop the dislikes that apply to this search (the "Show hidden GIFs again" button)."""
    return [m for m, close in zip(memory, _similar(memory, query_vector, threshold)) if not close]


def adjust_query(query_vector, index, liked_ids=(), disliked_ids=()):
    """Apply the Rocchio formula. Always starts from the ORIGINAL query, so clicks never pile up."""
    new_query = ALPHA * normalize(query_vector)

    liked_rows = [index["row_of"][i] for i in liked_ids if i in index["row_of"]]
    disliked_rows = [index["row_of"][i] for i in disliked_ids if i in index["row_of"]]

    if liked_rows:
        new_query = new_query + BETA * index["vectors"][liked_rows].mean(axis=0)
    if disliked_rows:
        new_query = new_query - GAMMA * index["vectors"][disliked_rows].mean(axis=0)

    return normalize(new_query)


def rank(query_vector, index, k=9, hidden_ids=()):
    """Score EVERY GIF against the query and return the best k (each GIF at most once).

    hidden_ids: GIFs to leave out of THIS ranking (they stay in the index).
    """
    # Cosine similarity for all GIFs in one step: every row is length 1, so a dot product is enough.
    scores = index["vectors"] @ normalize(query_vector)

    # Hidden GIFs get a score of minus infinity, then we drop them.
    hidden_rows = [index["row_of"][i] for i in hidden_ids if i in index["row_of"]]
    scores[hidden_rows] = -np.inf
    order = np.argsort(-scores)  # row numbers, highest score first
    best_rows = order[np.isfinite(scores[order])][:k]
    return [
        {
            "id": index["ids"][row],
            "url": index["urls"][row],
            "description": index["descriptions"][row],
            "score": float(scores[row]),
        }
        for row in best_rows
    ]


def search_with_feedback(query_vector, index, k=9, liked_ids=(), disliked_ids=(), hidden_ids=()):
    """The one function the app calls: adjust the query with the feedback, then rank."""
    return rank(adjust_query(query_vector, index, liked_ids, disliked_ids), index, k, hidden_ids)


if __name__ == "__main__":
    # A tiny made-up demo with 3 GIFs and 2-number vectors, so it runs without Gemini.
    # Think of the numbers as [how much "screaming", how much "confused"].
    class FakeCollection:
        def get(self, include):
            return {
                "ids": ["A", "B", "C"],
                "embeddings": [[1.0, 0.2], [0.3, 1.0], [0.7, 0.7]],
                "documents": ["someone screaming", "a confused cat", "a person pulling their hair"],
                "metadatas": [{"url": "a.gif"}, {"url": "b.gif"}, {"url": "c.gif"}],
            }

    index = load_index(FakeCollection())
    question = [0.6, 0.8]  # "I'm losing my mind": a bit screaming, a bit confused

    for title, liked, disliked in [
        ("No feedback", [], []),
        ("After liking A (screaming)", ["A"], []),
        ("After liking A and disliking B (confused)", ["A"], ["B"]),
    ]:
        print(title)
        for r in search_with_feedback(question, index, k=3, liked_ids=liked, disliked_ids=disliked):
            print(f"  {r['score']:.2f}  {r['description']}")
