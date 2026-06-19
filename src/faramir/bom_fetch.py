from __future__ import annotations
"""Box Office Mojo data fetching for Faramir."""

import logging
import re
from datetime import date

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BOM_HEADERS = {"User-Agent": "Mozilla/5.0"}


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


def _normalize_entry(raw: dict) -> dict:
    """Normalize a raw BOM entry to standard field names."""
    # The boxoffice library may use different key names depending on version.
    # Try multiple candidates for each field.
    def pick(keys):
        for k in keys:
            v = raw.get(k)
            if v is not None:
                return v
        return None

    title = pick(["title", "Title", "movie", "Movie"]) or ""
    gross_to_date = _clean_money(pick(["gross_to_date", "cumulative", "Cumulative", "total_gross", "Total Gross", "cum_gross"]))
    daily_gross = _clean_money(pick(["daily_gross", "daily", "Daily", "gross", "Gross"]))
    days_in_release = int(pick(["days_in_release", "days", "Days", "day", "Day"]) or 0)
    distributor = pick(["distributor", "Distributor", "studio", "Studio"]) or ""

    return {
        "title": title,
        "gross_to_date": gross_to_date,
        "daily_gross": daily_gross,
        "days_in_release": days_in_release,
        "distributor": distributor,
    }


def fetch_daily_list(target_date: date) -> list[dict]:
    """Fetch daily box office list for target_date using boxoffice library.

    NOTE: The boxoffice library API should be verified at runtime — the method
    name and return shape may differ from what is documented here. We try
    .daily() first, then fall back to common alternatives.
    """
    try:
        from boxoffice import BoxOffice  # type: ignore
        bo = BoxOffice()
        date_str = str(target_date)
        raw_data = None
        for method_name in ("daily", "get_daily", "daily_chart"):
            method = getattr(bo, method_name, None)
            if method:
                try:
                    raw_data = method(date_str)
                    break
                except Exception:
                    continue
        if raw_data is None:
            logger.warning("boxoffice library: no working daily method found")
            return []
        if isinstance(raw_data, list):
            return [_normalize_entry(r) for r in raw_data]
        # Some versions return a dict with a key containing the list
        for key in ("movies", "results", "data", "daily"):
            if key in raw_data:
                return [_normalize_entry(r) for r in raw_data[key]]
        logger.warning("boxoffice daily: unexpected return shape: %s", type(raw_data))
        return []
    except ImportError:
        logger.error("boxoffice library not installed")
        return []
    except Exception as exc:
        logger.error("fetch_daily_list error: %s", exc)
        return []


def fetch_weekend_list(year: int, week: int) -> list[dict]:
    """Fetch weekend box office list for the given year/week."""
    try:
        from boxoffice import BoxOffice  # type: ignore
        bo = BoxOffice()
        raw_data = None
        for method_name in ("weekend", "get_weekend", "weekend_chart"):
            method = getattr(bo, method_name, None)
            if method:
                try:
                    raw_data = method(year, week)
                    break
                except Exception:
                    continue
        if raw_data is None:
            logger.warning("boxoffice library: no working weekend method found")
            return []
        if isinstance(raw_data, list):
            return [_normalize_entry(r) for r in raw_data]
        for key in ("movies", "results", "data", "weekend"):
            if key in raw_data:
                return [_normalize_entry(r) for r in raw_data[key]]
        return []
    except ImportError:
        logger.error("boxoffice library not installed")
        return []
    except Exception as exc:
        logger.error("fetch_weekend_list error: %s", exc)
        return []


def scrape_worldwide(imdb_id: str) -> dict | None:
    """Scrape BOM title page for worldwide lifetime gross breakdown.

    Returns {domestic, international, worldwide} as ints, or None on failure.
    """
    url = f"https://www.boxofficemojo.com/title/{imdb_id}/"
    try:
        resp = requests.get(url, headers=BOM_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("scrape_worldwide request failed for %s: %s", imdb_id, exc)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # BOM summary section has money spans with a label above each
    result = {}
    # Try the summary money blocks — they live in divs with class containing "mojo-performance-summary"
    summary = soup.find("div", class_=re.compile(r"mojo-performance-summary", re.I))
    if not summary:
        # Fallback: scan all span.money elements paired with nearby label text
        pass

    def extract_summary_value(label_text: str) -> int | None:
        """Find a money value near a label matching label_text."""
        for tag in soup.find_all(string=re.compile(label_text, re.I)):
            parent = tag.parent
            # Look sibling or child span with money class
            for sibling in parent.find_next_siblings():
                span = sibling.find("span", class_=re.compile(r"money", re.I)) or sibling
                txt = span.get_text(strip=True)
                val = _clean_money(txt)
                if val:
                    return val
            # Look in parent's parent
            grandparent = parent.parent
            if grandparent:
                span = grandparent.find("span", class_=re.compile(r"money", re.I))
                if span:
                    return _clean_money(span.get_text(strip=True))
        return None

    # Strategy: find all "money" spans and pair with nearby text
    # BOM uses a grid: Domestic / International / Worldwide as headers with $ amounts below
    money_spans = soup.find_all("span", class_=re.compile(r"money", re.I))
    # Also try table-style summary
    for heading in soup.find_all(["h2", "h3", "div", "span", "td", "th"]):
        text = heading.get_text(strip=True).lower()
        if text in ("domestic", "international", "worldwide"):
            # Look for money in next sibling or parent chain
            nxt = heading.find_next(string=re.compile(r"\$[\d,]+"))
            if nxt:
                val = _clean_money(nxt)
                if val:
                    result[text] = val

    # If we got all three, return them
    if "domestic" in result and "international" in result and "worldwide" in result:
        return result

    # Broader fallback: scan the page text for the summary table
    # BOM renders something like: Domestic $123,456,789 \n International $... \n Worldwide $...
    text_blocks = soup.get_text("\n")
    lines = [l.strip() for l in text_blocks.split("\n") if l.strip()]
    for i, line in enumerate(lines):
        lower = line.lower()
        if lower in ("domestic", "international", "worldwide"):
            # Next non-empty line might be the money
            for j in range(i + 1, min(i + 5, len(lines))):
                candidate = lines[j]
                val = _clean_money(candidate)
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
