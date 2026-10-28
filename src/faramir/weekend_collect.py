"""Fetch a weekend chart from The Numbers, resolve identities and commit it to weekend history.

This is the collection step the history protocol (faramir.history) was built for. It is used by
scripts/backfill_weekends.py and, once enabled, by the weekly run. Identity lookups go through
faramir.identity (TMDB, year-window rules) and are cached so repeat runs make no TMDB calls.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

import requests

from faramir import history, identity as identity_mod
from faramir.tn_chart import Chart, parse_chart, weekend_url

logger = logging.getLogger("faramir.weekend_collect")

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
IDENTIFY_TOP_N = 30   # rows below this rank are stored without a TMDB identity (no strategy uses them)


def fetch_chart(friday: date, raw_dir: str | None = None, pause: float = 1.5) -> tuple[Chart, int]:
    url = weekend_url(friday)
    cached = os.path.join(raw_dir, f"tn_weekend_{friday.isoformat()}.html") if raw_dir else None
    if cached and os.path.exists(cached):   # already downloaded: never fetch the same page twice
        return parse_chart(open(cached, encoding="utf-8").read(), url, "weekend", friday), 200
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    time.sleep(pause)
    if raw_dir:
        os.makedirs(raw_dir, exist_ok=True)
        with open(os.path.join(raw_dir, f"tn_weekend_{friday.isoformat()}.html"), "w", encoding="utf-8") as f:
            f.write(resp.text)
    return parse_chart(resp.text, url, "weekend", friday), resp.status_code


class IdentityCache:
    """slug → identity dict (or None when unresolved), persisted as JSON."""

    def __init__(self, path: str):
        self.path = path
        self.data: dict[str, dict | None] = json.load(open(path)) if os.path.exists(path) else {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)


def resolve_identities(chart: Chart, cache: IdentityCache, top_n: int = IDENTIFY_TOP_N) -> dict[str, dict]:
    from faramir.tmdb import get_movie_details, get_release_dates, search_movie

    out = {}
    end = date.fromisoformat(chart.weekend_end) if chart.weekend_end else date.today()
    for row in chart.rows[:top_n]:
        slug = row.tn_slug
        if not slug:
            continue
        if slug not in cache.data:
            entry = identity_mod.ChartEntry(row.title_reported, slug, row.slug_year, row.days_in_release or 0, end)
            ident = identity_mod.resolve(entry, [], identity_mod.Overrides(), search_movie,
                                         get_movie_details, get_release_dates)
            cache.data[slug] = ({"tmdb_id": ident.tmdb_id, "imdb_id": (ident.details or {}).get("imdb_id"),
                                 "identity_confidence": ident.confidence, "identity_method": ident.method}
                                if ident.status == "resolved" else None)
        if cache.data[slug]:
            out[slug] = cache.data[slug]
    return out


def collect_weekend(gc, sheet_id: str, friday: date, cache: IdentityCache, run_id: str,
                    raw_dir: str | None = None) -> history.Result:
    chart, status = fetch_chart(friday, raw_dir)
    ids = resolve_identities(chart, cache)
    cache.save()
    now = datetime.now(timezone.utc)
    result = history.collect(gc, sheet_id, chart, run_id=run_id, retrieved_at=now, now=now, identities=ids,
                             http_status=status)
    logger.info("weekend %s: %s (%d rows, %d identified)", friday, result.status, len(chart.rows), len(ids))
    return result
