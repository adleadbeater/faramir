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
        title, href = "", ""
        for cell in cells:
            a = cell.find("a", href=re.compile(r"/movie/", re.I))
            if a:
                title = a.get_text(strip=True)
                href = a.get("href", "")
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

        slug_match = re.search(r"/movie/([^/#?]+)", href)
        results.append({
            "title": title,
            "tn_slug": slug_match.group(1) if slug_match else "",
            "gross_to_date": gross_to_date,
            "daily_gross": daily_gross,
            "days_in_release": days_in_release,
            "distributor": distributor,
        })

    logger.info("fetch_daily_list: %d entries for %s", len(results), target_date)
    return results


def fetch_weekend_list(year: int, week: int) -> list[dict]:
    """LEGACY — despite its name, this always returns the SUNDAY DAILY chart, never weekend data.

    It requests the Sunday-dated weekend URL, which The Numbers answers with 404 (weekend charts
    are keyed by their Friday), and then falls back to ``fetch_daily_list(sunday)``. Production's
    Monday merge uses it only to add titles missing from the daily list. Kept unchanged so
    production collection does not change; real weekend parsing is ``faramir.tn_chart``
    (Friday URL, no daily substitution) and collection is Increment 11, off by default.
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


def parse_title_page(html: str) -> dict | None:
    """{domestic, international, worldwide} from a BOM title page, or None.

    Reads the "All Releases" summary block (lifetime totals across every run, re-releases included),
    whose labels read "Domestic (41.4%)" / "International (58.6%)" / "Worldwide". Matching the bare
    words instead caught the release table's "Domestic" row, whose first figure is the OPENING
    weekend, and found no international total at all. A total BOM shows as "–" comes back as 0.
    """
    soup = BeautifulSoup(html, "html.parser")
    block = soup.find("div", class_="mojo-performance-summary-table")
    if not block:
        return None
    result = {}
    for section in block.find_all("div", recursive=False):
        label = section.find("span", class_="a-size-small")
        name = re.match(r"\s*(domestic|international|worldwide)\b", label.get_text(" ", strip=True), re.I) if label else None
        if not name:
            continue
        money = section.find("span", class_="money")
        result[name.group(1).lower()] = _clean_money(money.get_text(strip=True)) if money else 0
    if not result.get("worldwide") and not result.get("domestic"):
        return None
    dom, intl = result.get("domestic", 0), result.get("international", 0)
    return {"domestic": dom, "international": intl, "worldwide": result.get("worldwide") or dom + intl}


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

    result = parse_title_page(resp.text)
    if result:
        return result
    logger.warning("scrape_worldwide: could not parse page for %s", imdb_id)
    return None
