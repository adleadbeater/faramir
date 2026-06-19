from __future__ import annotations
"""Box Office Mojo data fetching for Faramir.

Scrapes BOM directly — no third-party library dependency.
Daily chart:  https://www.boxofficemojo.com/date/YYYY-MM-DD/
Weekend chart: https://www.boxofficemojo.com/weekend/YYYY-WNN/ (ISO week)
Title page:   https://www.boxofficemojo.com/title/ttXXXXXXX/
"""

import logging
import re
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BOM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 20


def _clean_money(value: str) -> int:
    """Strip $, commas, and convert to int. Returns 0 on failure."""
    if not value:
        return 0
    cleaned = re.sub(r"[$,\s]", "", str(value))
    try:
        return int(cleaned)
    except ValueError:
        return 0


def normalize_title(title: str) -> str:
    """Lowercase, strip leading articles, remove non-alphanumeric (except spaces)."""
    t = title.lower().strip()
    for prefix in ("the ", "a ", "an "):
        if t.startswith(prefix):
            t = t[len(prefix):]
            break
    t = re.sub(r"[^a-z0-9 ]", "", t)
    return t.strip()


def _parse_daily_table(soup: BeautifulSoup) -> list[dict]:
    """Parse the main chart table on a BOM daily or weekend page."""
    results = []

    # BOM renders a table with class containing "mojo-body-table" or just a standard <table>
    table = soup.find("table")
    if not table:
        logger.warning("_parse_daily_table: no <table> found on page")
        return []

    rows = table.find_all("tr")
    if not rows:
        return []

    # Parse header row to map column names to indices
    header_row = rows[0]
    headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]

    def col(row_cells: list, *names: str) -> str:
        for name in names:
            for i, h in enumerate(headers):
                if name in h and i < len(row_cells):
                    return row_cells[i].get_text(strip=True)
        return ""

    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        title_cell = None
        for cell in cells:
            a = cell.find("a", href=re.compile(r"/title/|/release/"))
            if a:
                title_cell = a.get_text(strip=True)
                break
        if not title_cell:
            # Fallback: use the col() approach
            title_cell = col(cells, "title", "release", "movie")

        if not title_cell:
            continue

        gross_to_date = _clean_money(col(cells, "total", "cumulative", "to date", "gross to date", "lifetime"))
        daily_gross = _clean_money(col(cells, "daily", "gross", "weekend"))
        days_raw = col(cells, "days", "day")
        try:
            days_in_release = int(re.sub(r"[^\d]", "", days_raw)) if days_raw else 0
        except ValueError:
            days_in_release = 0
        distributor = col(cells, "distributor", "studio", "distrib")

        # gross_to_date fallback: if zero but daily_gross present, use daily_gross
        if not gross_to_date and daily_gross:
            gross_to_date = daily_gross

        if not title_cell or not gross_to_date:
            continue

        results.append({
            "title": title_cell,
            "gross_to_date": gross_to_date,
            "daily_gross": daily_gross,
            "days_in_release": days_in_release,
            "distributor": distributor,
        })

    return results


def fetch_daily_list(target_date: date) -> list[dict]:
    """Scrape BOM daily chart for target_date.

    URL: https://www.boxofficemojo.com/date/YYYY-MM-DD/
    """
    date_str = target_date.strftime("%Y-%m-%d")
    url = f"https://www.boxofficemojo.com/date/{date_str}/"
    try:
        resp = requests.get(url, headers=BOM_HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("fetch_daily_list: request failed for %s: %s", date_str, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = _parse_daily_table(soup)
    logger.info("fetch_daily_list: %d entries for %s", len(results), date_str)
    return results


def fetch_weekend_list(year: int, week: int) -> list[dict]:
    """Scrape BOM weekend chart for the given ISO year/week.

    URL: https://www.boxofficemojo.com/weekend/{YYYY}W{WW}/
    Falls back to scraping the prior Friday–Sunday date range if the ISO URL fails.
    """
    week_str = f"{year}W{week:02d}"
    url = f"https://www.boxofficemojo.com/weekend/{week_str}/"
    try:
        resp = requests.get(url, headers=BOM_HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results = _parse_daily_table(soup)
        if results:
            logger.info("fetch_weekend_list: %d entries for %s", len(results), week_str)
            return results
    except Exception as exc:
        logger.warning("fetch_weekend_list: ISO URL failed (%s), trying date fallback", exc)

    # Fallback: compute the Sunday of that ISO week and fetch the daily chart
    # ISO week: Monday=day 1, Sunday=day 7
    try:
        import datetime
        sunday = datetime.date.fromisocalendar(year, week, 7)
        return fetch_daily_list(sunday)
    except Exception as exc:
        logger.error("fetch_weekend_list: date fallback also failed: %s", exc)
        return []


def scrape_worldwide(imdb_id: str) -> dict | None:
    """Scrape BOM title page for worldwide lifetime gross breakdown.

    Returns {domestic, international, worldwide} as ints, or None on failure.
    """
    url = f"https://www.boxofficemojo.com/title/{imdb_id}/"
    try:
        resp = requests.get(url, headers=BOM_HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("scrape_worldwide: request failed for %s: %s", imdb_id, exc)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    result = {}

    # BOM renders Domestic / International / Worldwide as labels with money values nearby.
    # Walk all text nodes and pair labels with the next money-looking string.
    lines = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]
    for i, line in enumerate(lines):
        lower = line.lower()
        if lower in ("domestic", "international", "worldwide"):
            for j in range(i + 1, min(i + 6, len(lines))):
                val = _clean_money(lines[j])
                if val > 0:
                    result[lower] = val
                    break

    if result:
        dom = result.get("domestic", 0)
        intl = result.get("international", 0)
        ww = result.get("worldwide", dom + intl)
        return {"domestic": dom, "international": intl, "worldwide": ww}

    logger.warning("scrape_worldwide: could not parse page for %s", imdb_id)
    return None
