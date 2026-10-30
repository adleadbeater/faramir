"""TMDB facts a title is built and checked against: genres, director, top cast, franchise, keywords,
studios and US rating. Free API; cached per run, and in a file when ``path`` is given (the local store)."""

from __future__ import annotations

import json
import os

VERSION = 3   # v2: keywords, studios, rating, top 5 cast; v3: first film of its collection?


class Credits:
    def __init__(self, path: str | None = None):
        self.path = path
        self.cache = json.load(open(path)) if path and os.path.exists(path) else {}

    def _save(self) -> None:
        if self.path:
            with open(self.path, "w") as f:
                json.dump(self.cache, f, indent=1)

    def by_id(self, tmdb_id) -> dict:
        key = str(tmdb_id)
        if self.cache.get(key, {}).get("v") != VERSION and tmdb_id and int(tmdb_id) > 0:
            try:
                from faramir.tmdb import _api_key, _get_json_with_retry
                d = _get_json_with_retry(f"/movie/{int(tmdb_id)}",
                                         {"api_key": _api_key(), "append_to_response": "credits,keywords,release_dates"})
                crew, cast = d.get("credits", {}).get("crew", []), d.get("credits", {}).get("cast", [])
                us = [r.get("certification") for x in d.get("release_dates", {}).get("results", [])
                      if x.get("iso_3166_1") == "US" for r in x.get("release_dates", []) if r.get("certification")]
                self.cache[key] = {"genres": [g["name"] for g in d.get("genres", [])],
                                   "director": ", ".join(p["name"] for p in crew if p.get("job") == "Director"),
                                   "cast": [p["name"] for p in cast[:5]], "v": VERSION,
                                   "collection": (d.get("belongs_to_collection") or {}).get("name", ""),
                                   "keywords": [k["name"] for k in d.get("keywords", {}).get("keywords", [])],
                                   "companies": [c["name"] for c in d.get("production_companies", [])],
                                   "certification": us[0] if us else "",
                                   "collection_first": self._first_in_collection(d)}
            except Exception:  # noqa: BLE001 — a title is still written; descriptors just have less to stand on
                self.cache.setdefault(key, {})
            self._save()
        return self.cache.get(key, {})

    @staticmethod
    def _first_in_collection(d: dict) -> bool | None:
        """Is this the series' first film? Being in a collection doesn't make a film a sequel: 'Airport'
        (1970) is the first of the Airport Collection (2026-10-30 post called it 'the Airport Sequel')."""
        coll = d.get("belongs_to_collection") or {}
        if not coll.get("id"):
            return None
        try:
            from faramir.tmdb import _api_key, _get_json_with_retry
            parts = _get_json_with_retry(f"/collection/{int(coll['id'])}", {"api_key": _api_key()}).get("parts", [])
            dates = sorted(p.get("release_date") for p in parts if p.get("release_date"))
            return bool(dates) and (d.get("release_date") or "") <= dates[0]
        except Exception:  # noqa: BLE001 — unknown: treated as possibly the first, so never called a sequel
            return None

    def by_title(self, title: str, year: int) -> dict:
        """A film the story names without an id (previous No. 1, rival): TMDB search, recent releases."""
        key = f"title:{title.lower()}"
        if key not in self.cache:
            tid = None
            try:
                from faramir.tmdb import _api_key, _get_json_with_retry
                for y in (year, year - 1):
                    hits = _get_json_with_retry("/search/movie", {"api_key": _api_key(), "query": title,
                                                                  "primary_release_year": y})
                    hits = ([h for h in hits.get("results", []) if h.get("title", "").lower() == title.lower()]
                            or hits.get("results", [])[:1])
                    if hits:
                        tid = hits[0]["id"]
                        break
            except Exception:  # noqa: BLE001
                pass
            self.cache[key] = tid
            self._save()
        return self.by_id(self.cache[key]) if self.cache[key] else {}
