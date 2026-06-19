"""Corpus loading and search utilities for Faramir."""

import bisect
import json
import logging

logger = logging.getLogger(__name__)


def load_corpus(path: str = "data/corpus.json") -> list[dict]:
    """Load corpus from JSON file."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
        logger.info("Loaded %d films from corpus at %s", len(data), path)
        return data
    except FileNotFoundError:
        logger.warning("Corpus file not found at %s, returning empty list", path)
        return []
    except json.JSONDecodeError as exc:
        logger.error("Corpus JSON decode error: %s", exc)
        return []


def build_index(corpus: list[dict]) -> dict:
    """Build sorted indexes for binary search.

    Returns:
        {
            domestic_index: list sorted by domestic_lifetime ascending,
            worldwide_index: list sorted by worldwide_lifetime ascending,
        }
    """
    domestic_index = sorted(
        [f for f in corpus if f.get("domestic_lifetime", 0)],
        key=lambda f: f["domestic_lifetime"],
    )
    worldwide_index = sorted(
        [f for f in corpus if f.get("worldwide_lifetime", 0)],
        key=lambda f: f["worldwide_lifetime"],
    )
    return {"domestic_index": domestic_index, "worldwide_index": worldwide_index}


def films_in_range(index: dict, axis: str, lo: int, hi: int) -> list[dict]:
    """Return films with lifetime gross in [lo, hi] on the given axis.

    Uses binary search on the pre-sorted index list.
    axis: 'domestic' or 'worldwide'
    """
    key = f"{axis}_index"
    sorted_list = index.get(key, [])
    if not sorted_list:
        return []

    field = f"{axis}_lifetime"
    values = [f[field] for f in sorted_list]

    lo_pos = bisect.bisect_left(values, lo)
    hi_pos = bisect.bisect_right(values, hi)
    return sorted_list[lo_pos:hi_pos]


def top_n_by_genre(corpus: list[dict], genre: str, n: int = 10, axis: str = "worldwide") -> list[dict]:
    """Return top N films in corpus matching genre, sorted by axis lifetime desc."""
    field = f"{axis}_lifetime"
    matches = [
        f for f in corpus
        if genre.lower() in [g.lower() for g in f.get("genres", [])]
        and f.get(field, 0) > 0
    ]
    return sorted(matches, key=lambda f: f[field], reverse=True)[:n]
