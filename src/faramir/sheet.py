"""Google Sheets helpers for Faramir."""

import json
import logging
import os
from datetime import datetime, timezone

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


# ── Claude usage tracking ──────────────────────────────────────────────────────
# In-memory buffer of per-call usage records for the current run.
# Call buffer_usage() immediately after each messages.create().
# Call flush_usage_to_sheet() once at the end of run() to batch-append all
# records in a single Sheets API call. Fails open — never blocks the pipeline.
_USAGE_LOG: list[dict] = []

USAGE_HEADERS = [
    "timestamp", "call_label", "input_tokens", "output_tokens",
    "cache_created", "cache_read", "model",
]


def buffer_usage(
    call_label: str,
    input_tokens: int,
    output_tokens: int,
    cache_created: int,
    cache_read: int,
    model: str,
) -> None:
    """Append one usage record to the in-memory buffer. Call after every API call."""
    _USAGE_LOG.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "call_label": call_label,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_created": cache_created,
        "cache_read": cache_read,
        "model": model,
    })


def _ensure_usage_tab(sh: gspread.Spreadsheet, tab_name: str) -> gspread.Worksheet:
    """Return the Usage worksheet, creating it with a header row if needed."""
    try:
        ws = sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(tab_name, rows=1000, cols=len(USAGE_HEADERS))
        ws.append_row(USAGE_HEADERS)
        logger.info("Created %s tab", tab_name)
    return ws


def flush_usage_to_sheet(client: gspread.Client, sheet_id: str) -> None:
    """Batch-append all buffered usage records to the Usage tab in one API call.

    Safe to call with an empty buffer (no-op). Fails open: on any Sheets error,
    logs a warning and returns without raising — usage tracking must never block
    a real run.
    """
    from faramir.config import USAGE_TAB_NAME  # avoid circular at module level

    if not _USAGE_LOG:
        return
    try:
        sh = client.open_by_key(sheet_id)
        ws = _ensure_usage_tab(sh, USAGE_TAB_NAME)
        rows = [
            [
                r["timestamp"], r["call_label"], r["input_tokens"], r["output_tokens"],
                r["cache_created"], r["cache_read"], r["model"],
            ]
            for r in _USAGE_LOG
        ]
        ws.append_rows(rows, value_input_option="RAW", insert_data_option="INSERT_ROWS")
        logger.info("Flushed %d Claude usage records to %s tab", len(rows), USAGE_TAB_NAME)
        _USAGE_LOG.clear()
    except Exception as exc:
        logger.warning("Could not flush Claude usage to sheet (non-fatal): %s", exc)


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
