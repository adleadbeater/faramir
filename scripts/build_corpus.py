from __future__ import annotations
"""Monthly corpus refresh script for Faramir."""

import json
import logging
import os
import re
import sys
from datetime import date

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("faramir.build_corpus")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

BOM_HEADERS = {"User-Agent": "Mozilla/5.0"}
PAGE_SIZE = 200
BOM_WW_URL = "https://www.boxofficemojo.com/chart/ww_top_lifetime_gross/?area=XWW&offset={offset}"
BOM_DOM_URL = "https://www.boxofficemojo.com/chart/top_lifetime_gross/?area=DOM&offset={offset}"


def _clean_money(val: str) -> int:
    cleaned = re.sub(r"[$,\s]", "", str(val))
    try:
        return int(cleaned)
    except ValueError:
        return 0


def _parse_imdb_id(href: str) -> str | None:
    """Extract imdb_id like tt1234567 from a BOM href."""
    m = re.search(r"/(tt\d+)/", href)
    return m.group(1) if m else None


def scrape_bom_chart(url_template: str, total: int) -> list[dict]:
    """Scrape BOM chart pages. Returns list of {rank, title, imdb_id, gross}."""
    results = []
    seen_ids = set()
    offset = 0
    while len(results) < total:
        url = url_template.format(offset=offset)
        logger.info("Fetching %s", url)
        try:
            resp = requests.get(url, headers=BOM_HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("BOM fetch failed at offset %d: %s", offset, exc)
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        if not table:
            logger.warning("No table found at offset %d", offset)
            break

        rows = table.find_all("tr")
        found_on_page = 0
        for row in rows[1:]:  # skip header
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            # Rank
            rank_text = cells[0].get_text(strip=True)
            try:
                rank = int(rank_text.replace(",", ""))
            except ValueError:
                continue

            # Title with imdb_id from href
            title_cell = cells[1]
            link = title_cell.find("a", href=True)
            if not link:
                continue
            imdb_id = _parse_imdb_id(link["href"])
            title = link.get_text(strip=True)
            if not imdb_id or imdb_id in seen_ids:
                continue

            # Gross — typically 3rd cell for domestic, or look for largest number
            gross = 0
            for cell in cells[2:]:
                val = _clean_money(cell.get_text(strip=True))
                if val > gross:
                    gross = val

            seen_ids.add(imdb_id)
            results.append({"rank": rank, "title": title, "imdb_id": imdb_id, "gross": gross})
            found_on_page += 1

            if len(results) >= total:
                break

        if found_on_page == 0:
            logger.info("No new rows at offset %d, stopping", offset)
            break

        offset += PAGE_SIZE

    return results


def enrich_with_tmdb(entry: dict, best_picture_ids: set) -> dict | None:
    """Resolve TMDB details for a BOM entry. Returns enriched dict or None."""
    from faramir.tmdb import resolve_tmdb_id, get_movie_details, get_external_ids

    imdb_id = entry["imdb_id"]
    title = entry["title"]

    # Try to find by imdb_id via search
    try:
        # Search by title first
        tmdb_match = resolve_tmdb_id(title)
        if not tmdb_match:
            logger.warning("No TMDB match for '%s'", title)
            return None
        tmdb_id = tmdb_match["id"]
    except Exception as exc:
        logger.warning("TMDB resolve failed for '%s': %s", title, exc)
        return None

    try:
        details = get_movie_details(tmdb_id)
    except Exception as exc:
        logger.warning("TMDB details failed for %s (%s): %s", tmdb_id, title, exc)
        return None

    release_year = int(details.get("release_year") or 0)
    is_best_picture = imdb_id in best_picture_ids or details.get("imdb_id", "") in best_picture_ids
    ww = entry.get("gross", 0) or 0
    # Pre-1980: always classic. Pre-2000 with >$500M WW: classic (Independence Day,
    # Jurassic Park, Titanic etc.). Post-2000 iconic blockbusters (Potter, Marvel)
    # are caught by the worldwide_lifetime > 500M check in unexpected_giant_killer
    # directly and don't need this flag — they score the +7 anyway.
    is_classic = release_year > 0 and (
        release_year < 1980
        or (release_year < 2000 and ww > 500_000_000)
    )

    return {
        **details,
        "imdb_id": imdb_id or details.get("imdb_id", ""),
        "domestic_lifetime": 0,
        "worldwide_lifetime": 0,
        "bom_gross": entry["gross"],
        "bom_rank": entry["rank"],
        "is_best_picture": is_best_picture,
        "is_classic": is_classic,
    }


def main():
    from faramir.config import CORPUS_TOP_WW, CORPUS_TOP_DOM
    from faramir.sheet import get_sheet_client, append_corpus_meta

    sheet_id = os.environ.get("FARAMIR_SHEET_ID")

    logger.info("=== Faramir corpus build: %s ===", date.today())

    # Load Best Picture winners
    bp_path = os.path.join(os.path.dirname(__file__), "..", "data", "best_picture_winners.json")
    with open(bp_path) as f:
        best_picture_ids = set(json.load(f))
    logger.info("Loaded %d Best Picture IMDb IDs", len(best_picture_ids))

    # Scrape BOM worldwide top
    logger.info("Scraping BOM worldwide top %d", CORPUS_TOP_WW)
    ww_entries = scrape_bom_chart(BOM_WW_URL, CORPUS_TOP_WW)
    logger.info("Got %d worldwide entries", len(ww_entries))

    # Scrape BOM domestic top
    logger.info("Scraping BOM domestic top %d", CORPUS_TOP_DOM)
    dom_entries = scrape_bom_chart(BOM_DOM_URL, CORPUS_TOP_DOM)
    logger.info("Got %d domestic entries", len(dom_entries))

    # Merge, deduplicate by imdb_id
    all_entries: dict[str, dict] = {}
    for e in dom_entries:
        e["domestic_lifetime"] = e["gross"]
        e["worldwide_lifetime"] = 0
        all_entries[e["imdb_id"]] = e
    for e in ww_entries:
        imdb_id = e["imdb_id"]
        if imdb_id in all_entries:
            all_entries[imdb_id]["worldwide_lifetime"] = e["gross"]
        else:
            e["worldwide_lifetime"] = e["gross"]
            e["domestic_lifetime"] = 0
            all_entries[imdb_id] = e

    logger.info("Unique films to enrich: %d", len(all_entries))

    # Enrich with TMDB
    corpus = []
    for i, (imdb_id, entry) in enumerate(all_entries.items()):
        logger.info("[%d/%d] Enriching %s — %s", i + 1, len(all_entries), imdb_id, entry["title"])
        enriched = enrich_with_tmdb(entry, best_picture_ids)
        if enriched:
            enriched["domestic_lifetime"] = entry.get("domestic_lifetime", 0)
            enriched["worldwide_lifetime"] = entry.get("worldwide_lifetime", 0)
            corpus.append(enriched)

    logger.info("Corpus enriched: %d films", len(corpus))

    # Write corpus.json
    corpus_path = os.path.join(os.path.dirname(__file__), "..", "data", "corpus.json")
    with open(corpus_path, "w") as f:
        json.dump(corpus, f, indent=2, default=str)
    logger.info("Wrote corpus to %s", corpus_path)

    # Append metadata to sheet
    if sheet_id:
        try:
            gc = get_sheet_client()
            append_corpus_meta(gc, sheet_id, {
                "build_date": str(date.today()),
                "total_films": len(corpus),
                "ww_scraped": len(ww_entries),
                "dom_scraped": len(dom_entries),
                "best_picture_count": sum(1 for f in corpus if f.get("is_best_picture")),
                "classic_count": sum(1 for f in corpus if f.get("is_classic")),
            })
        except Exception as exc:
            logger.warning("Could not append corpus_meta to sheet: %s", exc)

    logger.info("=== Corpus build complete ===")


if __name__ == "__main__":
    main()
