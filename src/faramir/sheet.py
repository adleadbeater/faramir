"""Google Sheets helpers for Faramir."""

import json
import logging
import os
from datetime import datetime, timezone

import gspread

from faramir.runtime import authorize

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
    "dom_stale_days",
    "ww_stale_days",
    # Increment 9: active-film identity (appended; a 17-column legacy tab is upgraded on write)
    "tn_slug",
    "identity_confidence",
    "identity_method",
]
LEGACY_STATE_HEADERS = STATE_HEADERS[:17]

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

# Schema v2 (Increment 6): columns appended to the right of the v1 layout.
# suggestion_id is immutable once written; the delivery_* columns track Slack
# delivery. Existing columns keep their positions.
DELIVERY_COLUMNS = [
    "suggestion_id",
    "delivery_state",
    "delivery_attempts",
    "last_attempt_at",
    "delivery_run_id",
    "data_date",
    "delivery_note",
    # Traceability (Increment 7): the candidate a pick was bound to and the
    # verified figures it rests on. Immutable once written.
    "candidate_id",
    "evidence_json",
]
SUGGESTIONS_HEADERS_V2 = SUGGESTIONS_HEADERS + DELIVERY_COLUMNS


def get_sheet_client() -> gspread.Client:
    """Auth from GOOGLE_SA_JSON env var."""
    sa_json = os.environ.get("GOOGLE_SA_JSON")
    if not sa_json:
        raise RuntimeError("GOOGLE_SA_JSON env var not set")
    return gspread.service_account_from_dict(json.loads(sa_json))


class SheetReadError(RuntimeError):
    """A Sheets read failed. Never treated as an empty tab."""


class TabMissing(SheetReadError):
    """The requested worksheet does not exist."""


def read_values(client: gspread.Client, sheet_id: str, tab: str) -> list[list[str]]:
    """Raw cell values of a tab. Raises (never returns []) on any failure."""
    try:
        ws = client.open_by_key(sheet_id).worksheet(tab)
    except gspread.WorksheetNotFound as exc:
        raise TabMissing(tab) from exc
    except Exception as exc:
        raise SheetReadError(f"{tab}: {type(exc).__name__}: {exc}") from exc
    try:
        return ws.get_all_values()
    except Exception as exc:
        raise SheetReadError(f"{tab}: {type(exc).__name__}: {exc}") from exc


def write_values(client: gspread.Client, sheet_id: str, tab: str, values: list[list[str]], op: str) -> bool:
    """Overwrite a tab in place — never clear() — then trim leftover rows.

    Guarded: returns False without touching Sheets in dry-run; raises
    WriteBlocked when the run is halted or disabled.
    """
    if not authorize("sheets", op, {"tab": tab, "rows": len(values), "values": values}):
        return False
    ws = client.open_by_key(sheet_id).worksheet(tab)
    previous = len(ws.get_all_values())
    ws.update(values=values, range_name="A1")
    if previous > len(values):
        ws.batch_clear([f"A{len(values) + 1}:ZZ{previous}"])
    logger.info("write_values: %s %d rows (%s)", tab, len(values), op)
    return True


def create_tab(client: gspread.Client, sheet_id: str, title: str, values: list[list[str]]) -> bool:
    """Create a worksheet with initial values (state-integrity initialization only). Guarded."""
    if not authorize("sheets", "create_tab", {"tab": title, "rows": len(values), "values": values}):
        return False
    sh = client.open_by_key(sheet_id)
    ws = sh.add_worksheet(title=title, rows=max(100, len(values) + 10), cols=max(26, max((len(r) for r in values), default=1)))
    ws.update(values=values, range_name="A1")
    logger.info("create_tab: %s with %d rows", title, len(values))
    return True


def append_values(client: gspread.Client, sheet_id: str, tab: str, values: list[list[str]], op: str) -> bool:
    """Append rows in ONE API call. Guarded. Not exactly-once: callers must
    reconcile by stable id after any error (see faramir.suggestions)."""
    if not authorize("sheets", op, {"tab": tab, "rows": len(values), "values": values}):
        return False
    ws = client.open_by_key(sheet_id).worksheet(tab)
    ws.append_rows(values, value_input_option="RAW", insert_data_option="INSERT_ROWS")
    logger.info("append_values: %s +%d rows (%s)", tab, len(values), op)
    return True


def update_cells(client: gspread.Client, sheet_id: str, tab: str, updates: list[dict], op: str) -> bool:
    """Write specific A1 ranges only ({"range": "M5:P5", "values": [[...]]}). Guarded."""
    if not updates:
        return True
    if not authorize("sheets", op, {"tab": tab, "updates": updates}):
        return False
    ws = client.open_by_key(sheet_id).worksheet(tab)
    ws.batch_update(updates, value_input_option="RAW")
    logger.info("update_cells: %s %d range(s) (%s)", tab, len(updates), op)
    return True


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
    # Outside the fail-open try: a blocked write must surface, not be swallowed.
    if not authorize("sheets", "flush_usage", {"tab": USAGE_TAB_NAME, "rows": list(_USAGE_LOG)}):
        _USAGE_LOG.clear()
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
    if not authorize("sheets", "append_corpus_meta", {"tab": "corpus_meta", "row": row}):
        return
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
