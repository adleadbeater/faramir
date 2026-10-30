from __future__ import annotations
"""TMDB API v3 client for Faramir."""

import os
import time

import requests

TMDB_BASE = "https://api.themoviedb.org/3"
TIMEOUT = 10
MAX_ATTEMPTS = 3
RETRY_STATUS = {429, 500, 502, 503, 504}


def _api_key() -> str:
    key = os.environ.get("TMDB_API_KEY")
    if not key:
        raise RuntimeError("TMDB_API_KEY env var not set")
    return key


def _get_json_with_retry(path: str, params: dict) -> dict:
    """GET a TMDB endpoint, retrying 429/5xx and connection errors with backoff.

    Error messages name the path only, never the URL, so the API key can't
    leak into logs.
    """
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = requests.get(f"{TMDB_BASE}{path}", params=params, timeout=TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = exc
        else:
            if resp.status_code not in RETRY_STATUS:
                if resp.status_code >= 400:
                    raise RuntimeError(f"TMDB {path} returned HTTP {resp.status_code}")
                return resp.json()
            last_error = RuntimeError(f"TMDB {path} returned HTTP {resp.status_code}")
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"TMDB {path} failed after {MAX_ATTEMPTS} attempts: {type(last_error).__name__}")


def find_by_imdb(imdb_id: str) -> list[dict]:
    """Resolve an IMDb id to TMDB movie records via /find.

    Returns the ``movie_results`` list (normally exactly one entry). Identity
    comes from the IMDb id alone — never from a title search.
    """
    data = _get_json_with_retry(
        f"/find/{imdb_id}", {"api_key": _api_key(), "external_source": "imdb_id"}
    )
    return data.get("movie_results", [])


def search_movie(title: str, year: int | None = None) -> list[dict]:
    """/search/movie results (first page). Choosing among them is faramir.identity's job."""
    params = {"api_key": _api_key(), "query": title, "include_adult": "false"}
    if year:
        params["year"] = year
    return _get_json_with_retry("/search/movie", params).get("results", [])


def get_release_dates(tmdb_id: int) -> dict:
    """/movie/{id}/release_dates (per-country release dates and types)."""
    return _get_json_with_retry(f"/movie/{tmdb_id}/release_dates", {"api_key": _api_key()})


def resolve_tmdb_id(title: str, year: int | None = None) -> dict | None:
    """Legacy popularity-based title search. No longer used for active films
    (title alone is not identity); kept for scripts that predate Increment 9."""
    """Search /search/movie and return best match dict or None."""
    params = {"api_key": _api_key(), "query": title}
    if year:
        params["year"] = year
    resp = requests.get(f"{TMDB_BASE}/search/movie", params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        return None
    # Accept if only one match or popularity > 5
    if len(results) == 1:
        return results[0]
    high = [r for r in results if r.get("popularity", 0) > 5]
    if high:
        return sorted(high, key=lambda r: r.get("popularity", 0), reverse=True)[0]
    return results[0]


def get_movie_details(tmdb_id: int) -> dict:
    """Fetch /movie/{id} + credits, return normalized dict."""
    data = _get_json_with_retry(f"/movie/{tmdb_id}", {"api_key": _api_key(), "append_to_response": "credits"})

    crew = data.get("credits", {}).get("crew", [])
    cast = data.get("credits", {}).get("cast", [])
    directors = [p["name"] for p in crew if p.get("job") == "Director"]
    lead_cast = [p["name"] for p in cast[:5]]

    collection = data.get("belongs_to_collection") or {}
    production_companies = data.get("production_companies", [])
    distributor = production_companies[0]["name"] if production_companies else ""

    origin_countries = data.get("origin_country", [])
    origin_country = origin_countries[0] if origin_countries else ""

    release_date = data.get("release_date", "")  # full YYYY-MM-DD
    release_year = release_date[:4] if release_date else ""

    return {
        "tmdb_id": tmdb_id,
        "imdb_id": data.get("imdb_id", ""),
        "title": data.get("title", ""),
        "release_date": release_date,
        "release_year": release_year,
        "genres": [g["name"] for g in data.get("genres", [])],
        "director": ", ".join(directors),
        "lead_cast": lead_cast,
        "collection_id": collection.get("id"),
        "collection_name": collection.get("name", ""),
        "origin_country": origin_country,
        "distributor": distributor,
    }


def get_external_ids(tmdb_id: int) -> dict:
    """Fetch /movie/{id}/external_ids."""
    resp = requests.get(
        f"{TMDB_BASE}/movie/{tmdb_id}/external_ids",
        params={"api_key": _api_key()},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()
