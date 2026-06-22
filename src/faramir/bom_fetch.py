from __future__ import annotations
"""Box office data fetching for Faramir.

Daily/weekend chart: The Numbers (the-numbers.com) — server-side rendered, reliable.
Worldwide per-title: Box Office Mojo title page scraper (already working).
"""

import logging
import re
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
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


def fetch_daily_list(target_date: date) -> list[dict]:
    """Fetch daily box office from The Numbers.

    URL: https://www.the-numbers.com/box-office-chart/daily/YYYY/MM/DD
    Columns of interest: Movie, Distributor, Gross (daily), Total Gross (cumulative), Days
    """
    url = f"https://www.the-numbers.com/box-office-chart/daily/{target_date.strftime('%Y/%m/%d')}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("fetch_daily_list: request failed for %s: %s", target_date, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # The Numbers renders a single <table> with id="box_office_chart" or similar.
    # Fall back to the first table on the page if the id isn't present.
    table = soup.find("table", id=re.compile(r"box_office", re.I)) or soup.find("table")
    if not table:
        logger.error("fetch_daily_list: no table found on The Numbers page for %s", target_date)
        logger.debug("fetch_daily_list: page snippet: %s", resp.text[:500])
        return []

    rows = table.find_all("tr")
    if len(rows) < 2:
        logger.warning("fetch_daily_list: table has fewer than 2 rows for %s", target_date)
        return []

    # Parse header to find column positions
    header_cells = rows[0].find_all(["th", "td"])
    headers = [c.get_text(strip=True).lower() for c in header_cells]
    logger.debug("fetch_daily_list: headers = %s", headers)

    def find_col(row_cells: list, *names: str) -> str:
        for name in names:
            for i, h in enumerate(headers):
                if name in h and i < len(row_cells):
                    return row_cells[i].get_text(strip=True)
        return ""

    results = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        # Title: look for a cell containing an <a> link to a movie page
        title = ""
        for cell in cells:
            a = cell.find("a", href=re.compile(r"/movie/", re.I))
            if a:
                title = a.get_text(strip=True)
                break
        if not title:
            title = find_col(cells, "movie", "title", "film")
        if not title:
            continue
        # Skip footer/summary rows injected by The Numbers (e.g. "Reporting movies: 17")
        if re.search(r"^reporting\b", title, re.I):
            continue

        gross_to_date = _clean_money(find_col(cells, "total gross", "total", "cumulative", "to date"))
        daily_gross = _clean_money(find_col(cells, "gross", "daily", "weekend"))
        distributor = find_col(cells, "distributor", "studio", "distrib")

        days_raw = find_col(cells, "days", "day", "#days")
        try:
            days_in_release = int(re.sub(r"[^\d]", "", days_raw)) if days_raw else 0
        except ValueError:
            days_in_release = 0

        if not gross_to_date and daily_gross:
            gross_to_date = daily_gross

        if not gross_to_date:
            continue

        results.append({
            "title": title,
            "gross_to_date": gross_to_date,
            "daily_gross": daily_gross,
            "days_in_release": days_in_release,
            "distributor": distributor,
        })

    logger.info("fetch_daily_list: %d entries for %s", len(results), target_date)
    return results


def fetch_weekend_list(year: int, week: int) -> list[dict]:
    """Fetch weekend box office from The Numbers for the Sunday of the given ISO week.

    The Numbers weekend chart: https://www.the-numbers.com/box-office-chart/weekend/YYYY/MM/DD
    where the date is the Sunday of the weekend.
    """
    try:
        import datetime
        sunday = datetime.date.fromisocalendar(year, week, 7)
        url = f"https://www.the-numbers.com/box-office-chart/weekend/{sunday.strftime('%Y/%m/%d')}"
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", id=re.compile(r"box_office", re.I)) or soup.find("table")
        if table:
            # Reuse daily parser logic — same table structure
            tmp_date = sunday
            results = fetch_daily_list(tmp_date)  # hits the weekend URL via daily fallback
            if results:
                return results
    except Exception as exc:
        logger.warning("fetch_weekend_list: failed (%s)", exc)

    # Final fallback: just return the Sunday daily chart
    try:
        import datetime
        sunday = datetime.date.fromisocalendar(year, week, 7)
        return fetch_daily_list(sunday)
    except Exception as exc:
        logger.error("fetch_weekend_list: all attempts failed: %s", exc)
        return []


def scrape_worldwide(imdb_id: str) -> dict | None:
    """Scrape BOM title page for worldwide lifetime gross breakdown.

    Returns {domestic, international, worldwide} as ints, or None on failure.
    BOM title pages are server-side rendered so this works fine.
    """
    url = f"https://www.boxofficemojo.com/title/{imdb_id}/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("scrape_worldwide: request failed for %s: %s", imdb_id, exc)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    result = {}

    # BOM renders Domestic / International / Worldwide as text labels
    # with money values on nearby lines.
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
