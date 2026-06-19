from __future__ import annotations
"""TMDB API v3 client for Faramir."""

import os
import requests

TMDB_BASE = "https://api.themoviedb.org/3"
TIMEOUT = 10


def _api_key() -> str:
    key = os.environ.get("TMDB_API_KEY")
    if not key:
        raise RuntimeError("TMDB_API_KEY env var not set")
    return key


def resolve_tmdb_id(title: str, year: int | None = None) -> dict | None:
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
    key = _api_key()
    movie_resp = requests.get(
        f"{TMDB_BASE}/movie/{tmdb_id}",
        params={"api_key": key, "append_to_response": "credits"},
        timeout=TIMEOUT,
    )
    movie_resp.raise_for_status()
    data = movie_resp.json()

    crew = data.get("credits", {}).get("crew", [])
    cast = data.get("credits", {}).get("cast", [])
    directors = [p["name"] for p in crew if p.get("job") == "Director"]
    lead_cast = [p["name"] for p in cast[:5]]

    collection = data.get("belongs_to_collection") or {}
    production_companies = data.get("production_companies", [])
    distributor = production_companies[0]["name"] if production_companies else ""

    origin_countries = data.get("origin_country", [])
    origin_country = origin_countries[0] if origin_countries else ""

    release_year = ""
    rd = data.get("release_date", "")
    if rd:
        release_year = rd[:4]

    return {
        "tmdb_id": tmdb_id,
        "imdb_id": data.get("imdb_id", ""),
        "title": data.get("title", ""),
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
