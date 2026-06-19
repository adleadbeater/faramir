"""Google Sheets helpers for Faramir."""

import json
import logging
import os

import gspread

logger = logging.getLogger(__name__)

STATE_HEADERS = [
    "tmdb_id",
    "imdb_id",
    "canonical_title",
    "bom_title_seen",
    "release_date",
    "first_seen",
    "last_seen",
    "status",
    "domestic_yesterday",
    "domestic_today",
    "worldwide_yesterday",
    "worldwide_today",
    "days_in_release",
    "tmdb_id_override",
    "notes",
]

SUGGESTIONS_HEADERS = [
    "run_date",
    "active_tmdb_id",
    "active_title",
    "comp_tmdb_id",
    "comp_title",
    "axis",
    "kind",
    "threshold_value",
    "angle_category",
    "headline",
    "angle",
    "richness_score",
    "slack_ts",
    "thumbs_up",
    "thumbs_down",
    "thread_replies",
    "thread_feedback",
]


def get_sheet_client() -> gspread.Client:
    """Auth from GOOGLE_SA_JSON env var."""
    sa_json = os.environ.get("GOOGLE_SA_JSON")
    if not sa_json:
        raise RuntimeError("GOOGLE_SA_JSON env var not set")
    return gspread.service_account_from_dict(json.loads(sa_json))


def read_state(client: gspread.Client, sheet_id: str) -> list[dict]:
    """Read 'state' tab, return list of dicts keyed by column headers."""
    try:
        sh = client.open_by_key(sheet_id)
        ws = sh.worksheet("state")
        return ws.get_all_records()
    except Exception as exc:
        logger.error("read_state failed: %s", exc)
        return []


def write_state(client: gspread.Client, sheet_id: str, rows: list[dict]) -> None:
    """Write full state tab (clear + rewrite). Preserves header row."""
    sh = client.open_by_key(sheet_id)
    ws = sh.worksheet("state")
    ws.clear()
    # Write headers then data
    data = [STATE_HEADERS]
    for row in rows:
        data.append([str(row.get(h, "")) for h in STATE_HEADERS])
    ws.update("A1", data)
    logger.info("write_state: wrote %d rows", len(rows))


def read_suggestions(client: gspread.Client, sheet_id: str) -> list[dict]:
    """Read 'suggestions' tab."""
    try:
        sh = client.open_by_key(sheet_id)
        ws = sh.worksheet("suggestions")
        return ws.get_all_records()
    except Exception as exc:
        logger.error("read_suggestions failed: %s", exc)
        return []


def append_suggestions(client: gspread.Client, sheet_id: str, rows: list[dict]) -> None:
    """Append rows to suggestions tab."""
    if not rows:
        return
    sh = client.open_by_key(sheet_id)
    ws = sh.worksheet("suggestions")

    # Ensure headers exist
    existing = ws.row_values(1)
    if not existing:
        ws.append_row(SUGGESTIONS_HEADERS)

    for row in rows:
        ws.append_row([str(row.get(h, "")) for h in SUGGESTIONS_HEADERS])
    logger.info("append_suggestions: appended %d rows", len(rows))


def write_suggestions(client: gspread.Client, sheet_id: str, rows: list[dict]) -> None:
    """Overwrite the full suggestions tab (used when updating rows with feedback)."""
    if not rows:
        return
    sh = client.open_by_key(sheet_id)
    ws = sh.worksheet("suggestions")
    ws.clear()
    data = [SUGGESTIONS_HEADERS]
    for row in rows:
        data.append([str(row.get(h, "")) for h in SUGGESTIONS_HEADERS])
    ws.update("A1", data)
    logger.info("write_suggestions: wrote %d rows", len(rows))


def append_corpus_meta(client: gspread.Client, sheet_id: str, row: dict) -> None:
    """Append one row to corpus_meta tab."""
    sh = client.open_by_key(sheet_id)
    try:
        ws = sh.worksheet("corpus_meta")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet("corpus_meta", rows=1000, cols=20)

    existing = ws.row_values(1)
    if not existing:
        headers = list(row.keys())
        ws.append_row(headers)

    ws.append_row(list(str(v) for v in row.values()))
    logger.info("append_corpus_meta: appended row")
