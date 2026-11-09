"""Local keyword ranking and a benchmark-calibrated policy for GIF search."""
import hashlib
import heapq
import json
import logging
import re
from time import perf_counter

from benchmark.retrieval.keyword_dataset1 import BM25, DATASETS, load_data, tokenize

from .engine import MODEL_NAME, ROOT

POOL_SIZE = 80
POLICY_VERSION = 2
POLICY_PATH = ROOT / ".cache" / "auto-search.json"
PHRASE = re.compile(r'^["“]([^"”]+)["”]$')
logger = logging.getLogger("gifly.auto_search")


def query_group(query):
    return "short" if len(tokenize(query)) <= 3 else "description"


def top_keywords(index, query, limit, allowed=None):
    scores = index.scores(query)
    candidates = (i for i, score in enumerate(scores) if score > 0
                  and (allowed is None or allowed(index.gif_ids[i])))
    best = heapq.nsmallest(limit, candidates, key=lambda i: (-scores[i], i))
    return [index.gif_ids[i] for i in best]


def combine(semantic, keyword, method):
    if method == "keyword":
        return list(dict.fromkeys([*keyword, *semantic]))
    if method != "hybrid" or not keyword:
        return semantic
    scores = {}
    for ranking, weight in ((semantic, 0.8), (keyword, 0.2)):
        for rank, gif_id in enumerate(ranking, 1):
            scores[gif_id] = scores.get(gif_id, 0) + weight / (60 + rank)
    return sorted(scores, key=scores.get, reverse=True)


class AutoSearch:
    def __init__(self, records):
        self.by_id = {row["id"]: row for row in records}
        self.keyword = BM25([{"gif_id": row["id"], "caption": row["description"]} for row in records])
        self.methods = {}

    def choose(self, query):
        if PHRASE.fullmatch(query):
            return "keyword"
        return self.methods.get(query_group(query), "semantic")

    def rank(self, query, method, semantic, category, hidden):
        phrase = PHRASE.fullmatch(query)
        text = phrase.group(1) if phrase else query
        phrase_tokens = " ".join(tokenize(text))

        def allowed(gif_id):
            row = self.by_id[gif_id]
            return (gif_id not in hidden and (category == "all" or row["category"] == category)
                    and (not phrase or f" {phrase_tokens} " in f" {' '.join(tokenize(row['description']))} "))

        keyword = top_keywords(self.keyword, text, POOL_SIZE, allowed)
        order = combine(list(semantic), keyword, method)
        return [semantic[gif_id] if gif_id in semantic else self.by_id[gif_id] for gif_id in order]


def calibrate(fingerprint, production):
    """Evaluate the local methods once per catalog, evaluation set and policy version."""
    import numpy as np
    from benchmark.retrieval import embed
    from benchmark.retrieval.semantic_dataset1 import as_unit_vectors, validate_vector_metadata, vector_paths

    dataset = DATASETS["v2"]
    benchmark_hash = hashlib.sha256()
    for filename in ("corpus.jsonl", "queries.jsonl"):
        benchmark_hash.update((dataset / filename).read_bytes())
    key = {"catalog": fingerprint, "dataset": benchmark_hash.hexdigest(),
           "model": MODEL_NAME, "version": POLICY_VERSION}
    try:
        report = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        if report["key"] == key:
            return report["methods"]
    except (OSError, ValueError, KeyError, TypeError):
        pass

    corpus, queries = load_data("v2")
    captions = [row["caption"] for row in corpus]
    texts = [row["query"] for row in queries]
    paths = vector_paths("minilm", "v2")
    try:
        for kind, path, values in zip(("corpus", "queries"), paths, (captions, texts)):
            validate_vector_metadata("minilm", kind, path, values)
        cv = as_unit_vectors(paths[0], len(corpus), "MiniLM corpus")
        qv = as_unit_vectors(paths[1], len(queries), "MiniLM queries")
    except (OSError, ValueError):
        embed.main(["--model", "minilm", "--full"])
        cv = as_unit_vectors(paths[0], len(corpus), "MiniLM corpus")
        qv = as_unit_vectors(paths[1], len(queries), "MiniLM queries")

    # Merge captions by GIF for lexical ranking, matching the production catalog.
    by_gif = {}
    for row in corpus:
        by_gif.setdefault(row["gif_id"], []).append(row["caption"])
    lexical = BM25([{"gif_id": key, "caption": " ".join(values)} for key, values in by_gif.items()])
    groups = {}
    timings = {}
    for query, scores in zip(queries, qv @ cv.T):
        text = query["query"]
        target = set(query["relevant_ids"])
        keyword = top_keywords(lexical, text, POOL_SIZE)
        group_name = query_group(text)
        samples = timings.setdefault(group_name, [])
        if len(samples) < 25:
            start = perf_counter()
            production.rank(text, "hybrid", {}, "all", set())
            samples.append((perf_counter() - start) * 1000)
        semantic = list(dict.fromkeys(corpus[i]["gif_id"] for i in np.argsort(-scores, kind="stable")))[:POOL_SIZE]
        group = groups.setdefault(group_name, [])
        group.append({
            **{method: next((1 / i for i, gif_id in enumerate(combine(semantic, keyword, method)[:10], 1)
                             if gif_id in target), 0)
               for method in ("semantic", "keyword", "hybrid")},
        })

    methods, metrics = {}, {}
    for name, group in groups.items():
        means = {method: sum(row[method] for row in group) / len(group)
                 for method in ("semantic", "keyword", "hybrid")}
        extra_p95 = float(np.percentile(timings[name], 95))
        winner = max(means, key=means.get)
        if len(group) < 20 or means[winner] - means["semantic"] < 0.01 or extra_p95 > 15:
            winner = "semantic"
        methods[name] = winner
        metrics[name] = {"queries": len(group), "mrr_at_10": means, "live_keyword_p95_ms": extra_p95,
                         "live_latency_samples": len(timings[name])}

    POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = POLICY_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps({"key": key, "methods": methods, "metrics": metrics}, indent=2), encoding="utf-8")
    temp.replace(POLICY_PATH)
    return methods
