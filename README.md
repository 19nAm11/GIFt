# GIFt

Semantic GIF and video search. Describe what you want in plain words, such as *"a dog being confused"*, and GIFt finds matching GIFs by meaning rather than by exact keywords.

GIFt ships a browser app called **gifly**, a Streamlit app with like/dislike feedback and a webcam "GIF mirror", an HTTP API, and a benchmark for comparing retrieval methods.

## Features

- **gifly website.** A shuffled home feed with paging, previews and copyable links. One search bar covers GIFs and videos. Exact phrases in quotation marks are prioritised.
- **Automatic GIF ranking.** The server picks between semantic (MiniLM), BM25 and hybrid ranking using a background benchmark on a frozen evaluation set, and falls back to semantic search while it runs.
- **Relevance feedback.** Like and dislike results to re-rank them (Rocchio). Dislikes are remembered for similar searches.
- **Accounts.** Local sign-up and sign-in with per-account favorites, stored in SQLite.
- **Gemini search.** An optional Gemini-embedding index for GIFs, and text-to-video search over local clips.
- **GIF mirror.** In the Streamlit app, a webcam photo is turned into a search phrase for the emotion it shows.
- **Benchmark.** BM25, MiniLM, Qwen3, BGE-M3 and hybrid retrieval compared on a frozen 10,000-caption / 200-query split.

## Quick start

Requires Python 3.11 or 3.12 (developed on 3.13) and about 2 GB of disk for PyTorch and the model. The first run needs internet to download the TGIF descriptions (about 18 MB) and the MiniLM model (about 90 MB). GIF images are always loaded from their original URLs.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m web.prepare_tgif
.\start-web.ps1 -IndexData
```

Open http://127.0.0.1:8000. On later runs, start with `.\start-web.ps1`. The web guide ([web/README.md](web/README.md)) covers configuration, accounts, search modes, storage, the API and tests.

On macOS or Linux, run the same steps by hand: activate the venv, run `python -m web.prepare_tgif`, `python -m web.ingest`, then `uvicorn api:app --host 127.0.0.1 --port 8000`.

## Other ways to run it

| What | Command |
|------|---------|
| Build the small sample index (5,000 GIFs) | `python -m gift.index --limit 5000` |
| Search from the terminal | `python -m gift.search a dog being confused` |
| Streamlit app (Gemini, feedback, GIF mirror) | `streamlit run apps/streamlit_app.py` |
| HTTP API and website | `uvicorn api:app --host 127.0.0.1 --port 8000` |
| Build the Gemini GIF index | `python -m gift.gemini.index` |
| Embed local video clips | `python -m gift.video.embed` |
| Search videos from the terminal | `python -m gift.video.search --query "a dancing cartoon"` |
| Retrieval benchmark | `pip install -r requirements-benchmark.txt`, then `python -m benchmark --help` |

Gemini and video features need `GEMINI_API_KEY` set in the environment. The API serves the website at `/`, search at `/search?q=...&k=9`, and interactive docs at `/docs`.

## How it works

1. **Index (once).** An embedding model (`all-MiniLM-L6-v2`) turns each GIF description into a 384-number vector. Descriptions with similar meaning get similar vectors. The vectors are stored in a local Chroma database.
2. **Search (every query).** The query is embedded with the same model, and Chroma returns the stored vectors closest to it by cosine similarity. The GIFs behind those vectors are shown.

The same model must be used in both steps, or the vectors are not comparable.

## Repository layout

```
.
├── gift/              shared code
│   ├── config.py      paths, model names, settings
│   ├── data.py        download and load the TGIF dataset
│   ├── index.py       build the sample Chroma index
│   ├── search.py      search_gif(query, k)
│   ├── gemini/        Gemini index and search, feedback (Rocchio), emotion
│   └── video/         video embedding and text-to-video search
├── web/               the gifly website: FastAPI backend, plain JS frontend, accounts
├── apps/              Streamlit app
├── api.py             HTTP API entry point (serves web/)
├── benchmark/         retrieval benchmark, datasets, reports
├── notebooks/         learning notebooks (see below)
├── tests/             API tests
└── data/              generated and git-ignored
```

Tests live next to what they test: `web/tests/`, `benchmark/tests/` and `tests/`.

## Testing

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s web/tests -v
.\.venv\Scripts\python.exe -m unittest tests.test_api
.\.venv\Scripts\python.exe -m pytest benchmark/tests
node --test web/tests/api-client.test.mjs
```

## Learning notebooks

New to embeddings and vector search? Start with `notebooks/concepts.ipynb`. It explains embeddings, similarity and vector databases without needing an index (about 20 minutes). Then try `notebooks/gif_search.ipynb`, a step-by-step walkthrough on the real GIF data. It needs the sample index from `python -m gift.index`.

```powershell
python -m ipykernel install --user --name gif-search
jupyter notebook notebooks/
```

Pick the `gif-search` kernel and run cells top to bottom. Notebook outputs are stripped in git.

## Contributing

- Install the local checks once per clone: `pip install pre-commit; pre-commit install`. They block commits of files over 1 MB or anything under `data/`. CI runs the same checks on every pull request.
- Everything generated lives in `data/`, `web/.chroma`, `web/.cache` and `web/.local`, and is git-ignored. Do not commit it.
- Never commit secrets. Put API keys in environment variables or a `.env` file, which is git-ignored.
- Put new shared code under `gift/`. Settings belong in `gift/config.py`.

## Troubleshooting

- **Broken images.** GIFs are hosted on third-party sites and some old links are dead. The site keeps the description and original link when an image fails.
- **"No GIF index found".** Run `python -m gift.index`, or build the full web index with `.\start-web.ps1 -IndexData`.
- **"Search is not ready".** Run `python -m web.ingest`, then restart the server.
- **Re-index the sample index.** Delete `data/chroma_db/` and run `python -m gift.index` again.
- **`ModuleNotFoundError` in a notebook.** The notebook is using the wrong Python. Select the `.venv` or `gif-search` kernel.

## Background

GIFt began as a small local replica of the [Pinecone GIF search article](https://www.pinecone.io/learn/gif-search/), using Chroma instead of Pinecone and the public [TGIF](https://github.com/raingo/TGIF-Release) dataset (about 125,000 GIF descriptions).

## License

MIT. See [LICENSE](LICENSE).
