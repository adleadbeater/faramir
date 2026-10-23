"""Build an integrity-initialized Faramir workbook on a FakeSpreadsheet."""

from __future__ import annotations

from faramir.sheet import STATE_HEADERS
from faramir.state_integrity import LKG_TAB, META_TAB, STATE_TAB, initial_meta, meta_values, row_values


def state_row(tmdb_id, title, dom_today=140_000_000, dom_yest=130_000_000, ww_today=300_000_000,
              ww_yest=280_000_000, **kw) -> dict:
    row = {
        "tmdb_id": tmdb_id, "imdb_id": f"tt{tmdb_id:07d}", "canonical_title": title, "bom_title_seen": title,
        "release_date": "2026-09-18", "first_seen": "2026-09-20", "last_seen": "2026-10-21", "status": "active",
        "domestic_yesterday": dom_yest, "domestic_today": dom_today, "worldwide_yesterday": ww_yest,
        "worldwide_today": ww_today, "days_in_release": 33, "tmdb_id_override": "", "notes": "",
        "dom_stale_days": 0, "ww_stale_days": 0,
    }
    row.update(kw)
    return row


def init_book(book, rows: list[dict], now_iso: str = "2026-10-21T15:00:00+00:00", run_id: str = "init"):
    """Create state, state_lkg and state_meta exactly as init_state_integrity --apply would."""
    values = [list(STATE_HEADERS)] + row_values(list(STATE_HEADERS), rows)
    book.add_tab(STATE_TAB, values)
    book.add_tab(LKG_TAB, values)
    book.add_tab(META_TAB, meta_values(initial_meta(values, now_iso, run_id)))
    return values
