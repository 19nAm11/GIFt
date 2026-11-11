"""Convert the notebook's TGIF TSV into a reproducible website catalog."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

from data import ensure_tgif

from .engine import ROOT

TGIF_PATH = ROOT / "data" / "tgif.json"


def load_tgif(path: Path, limit: int = 125782) -> list[dict]:
    """Keep all captions per unique URL, then sample reproducibly with seed 42."""
    if limit < 1:
        raise ValueError("limit must be a positive integer")
    rows = []
    by_url = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for line in csv.reader(stream, delimiter="\t"):
            if len(line) != 2:
                continue
            url, description = (value.strip() for value in line)
            parsed = urlparse(url)
            if not description or parsed.scheme != "https" or not parsed.netloc or any(c.isspace() for c in url):
                continue
            if url not in by_url:
                by_url[url] = []
                rows.append((url, by_url[url]))
            if description not in by_url[url]:
                by_url[url].append(description)
    if not rows:
        raise ValueError("No usable TGIF rows found. Expected URL and description separated by a tab.")
    # pandas.DataFrame.sample(random_state=42) uses the same RandomState.choice.
    indices = np.random.RandomState(42).choice(len(rows), size=min(limit, len(rows)), replace=False)
    records = []
    for index in indices:
        url, captions = rows[index]
        description = " ".join(captions)
        records.append({
            "id": "tgif-" + hashlib.sha256(url.encode()).hexdigest()[:24],
            "title": description if len(description) <= 75 else description[:72].rstrip() + "…",
            "description": description,
            "caption_count": len(captions),
            "tags": [],
            "category": "general",  # TGIF does not supply category labels.
            "source": "TGIF",
            "url": url,
            "ratio": 1.33,
        })
    return records


def prepare(path: Path, output: Path = TGIF_PATH, limit: int = 125782) -> list[dict]:
    records = load_tgif(path, limit)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(f"Prepared {len(records)} TGIF records: {output}")
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, help="Path to the TGIF TSV. Omit to download or reuse the shared dataset.")
    parser.add_argument("--limit", type=int, default=125782)
    parser.add_argument("--output", type=Path, default=TGIF_PATH)
    args = parser.parse_args()
    prepare(args.file or Path(ensure_tgif()), args.output, args.limit)
