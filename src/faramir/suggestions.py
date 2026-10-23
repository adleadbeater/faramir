"""Suggestions tab: stable identities, idempotent appends, safe feedback updates.

No schema change: every suggestion's identity is DERIVED from columns the tab
already has — ``run_date | active_tmdb_id | comp_tmdb_id (or "M"+threshold) |
axis | kind`` — so legacy rows get the same id as new ones and nothing is
backfilled, reordered or rewritten.

Appends (``append_new``) are not trusted to be exactly-once: before appending,
and again after any error, the tab is re-read and only ids that are not yet
present are appended. A retry after "succeeded on Google's side but timed out"
therefore appends nothing.

Feedback (``apply_feedback``) writes only the four feedback cells of a row, and
only when the row is identified unambiguously in a fresh read: exactly one row
carries the Slack ts, and that row's derived id is the expected one. Moved rows
are followed; missing, duplicated or mismatched rows are refused and reported.
A read-back after the write detects a row that moved during the write.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from gspread.utils import numericise_all, rowcol_to_a1

from faramir import runtime
from faramir.sheet import (
    DELIVERY_COLUMNS,
    SUGGESTIONS_HEADERS,
    SUGGESTIONS_HEADERS_V2,
    append_values,
    read_values,
    update_cells,
)

logger = logging.getLogger(__name__)

TAB = "suggestions"
FEEDBACK_COLUMNS = ("thumbs_up", "thumbs_down", "thread_replies", "thread_feedback")
MAX_APPEND_ATTEMPTS = 3


class SuggestionsReadError(RuntimeError):
    pass


class SuggestionsWriteIncident(RuntimeError):
    """A verified write landed somewhere other than intended (needs a human)."""


def _norm(value) -> str:
    s = "" if value is None else str(value).strip()
    if s.lower() in ("", "none", "null", "0"):
        return ""
    try:
        return str(int(float(s)))
    except ValueError:
        return s


def suggestion_id(row: dict) -> str | None:
    """Stable id from immutable pick fields; None when the row can't be identified."""
    run_date = str(row.get("run_date") or "").strip()
    active = _norm(row.get("active_tmdb_id"))
    comp = _norm(row.get("comp_tmdb_id"))
    threshold = _norm(row.get("threshold_value"))
    axis = str(row.get("axis") or "").strip()
    kind = str(row.get("kind") or "").strip()
    target = comp or (f"M{threshold}" if threshold else "")
    if not (run_date and active and target and axis and kind):
        return None
    return hashlib.sha1("|".join([run_date, active, target, axis, kind]).encode()).hexdigest()[:16]


@dataclass
class Row:
    number: int  # 1-based sheet row
    values: list[str]
    record: dict
    sid: str | None       # explicit suggestion_id (schema v2) or derived id (v1)
    slack_ts: str
    derived: str | None = None  # id recomputed from the pick columns
    raw: dict = field(default_factory=dict)  # exact cell text by column

    def get(self, column: str, default: str = "") -> str:
        """Exact cell text. Never the numericised record: a Slack ts such as
        1700000001.000100 must not become 1700000001.0001."""
        return self.raw.get(column, default)


@dataclass
class Table:
    header: list[str]
    rows: list[Row]
    schema: int = 1
    unidentifiable: list[int] = field(default_factory=list)
    duplicate_ids: dict[str, list[int]] = field(default_factory=dict)
    duplicate_ts: dict[str, list[int]] = field(default_factory=dict)

    def records(self) -> list[dict]:
        """Rows as gspread get_all_records would return them (suppression input)."""
        return [r.record for r in self.rows]

    def ids(self) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for r in self.rows:
            if r.sid:
                out.setdefault(r.sid, []).append(r.number)
        return out

    def by_ts(self) -> dict[str, list[Row]]:
        out: dict[str, list[Row]] = {}
        for r in self.rows:
            if r.slack_ts:
                out.setdefault(r.slack_ts, []).append(r)
        return out

    def by_id(self) -> dict[str, list[Row]]:
        out: dict[str, list[Row]] = {}
        for r in self.rows:
            if r.sid:
                out.setdefault(r.sid, []).append(r)
        return out

    def ambiguity_report(self) -> dict:
        return {
            "schema": self.schema,
            "rows": len(self.rows),
            "unidentifiable_rows": self.unidentifiable[:50],
            "duplicate_ids": {k: v for k, v in list(self.duplicate_ids.items())[:50]},
            "duplicate_slack_ts": {k: v for k, v in list(self.duplicate_ts.items())[:50]},
        }


def parse(values: list[list[str]]) -> Table:
    if not values or not any(values[0]):
        return Table(header=[], rows=[])
    header = [str(c) for c in values[0]]
    while header and header[-1] == "":
        header.pop()
    missing = [h for h in SUGGESTIONS_HEADERS if h not in header]
    if missing:
        raise SuggestionsReadError(f"suggestions header is missing columns {missing}")
    partial = [h for h in DELIVERY_COLUMNS if h in header]
    if partial and len(partial) != len(DELIVERY_COLUMNS):
        raise SuggestionsReadError(f"suggestions header has a partial v2 schema {partial}")
    schema = 2 if partial else 1
    rows: list[Row] = []
    for i, raw in enumerate(values[1:], start=2):
        cells = [str(c) for c in raw] + [""] * (len(header) - len(raw))
        cells = cells[: len(header)]
        if not any(cells):
            continue
        record = dict(zip(header, numericise_all(list(cells))))
        derived = suggestion_id(record)
        explicit = cells[header.index("suggestion_id")].strip() if schema == 2 else None
        sid = (explicit or None) if schema == 2 else derived
        rows.append(Row(i, cells, record, sid, cells[header.index("slack_ts")].strip(), derived, dict(zip(header, cells))))
    table = Table(header, rows, schema)
    table.unidentifiable = [r.number for r in rows if r.sid is None]
    table.duplicate_ids = {k: v for k, v in table.ids().items() if len(v) > 1}
    table.duplicate_ts = {k: [r.number for r in v] for k, v in table.by_ts().items() if len(v) > 1}
    return table


def read_table(client, sheet_id: str) -> Table:
    """Read and parse the tab. Raises on any read failure (never 'empty')."""
    return parse(read_values(client, sheet_id, TAB))


def _with_id(row: dict, sid: str, header: list[str]) -> dict:
    """Schema v2 rows carry their id explicitly (an unidentifiable pick gets a
    one-off id so the row is still addressable later)."""
    if "suggestion_id" not in header or row.get("suggestion_id"):
        return row
    if sid.startswith("unidentifiable-"):
        sid = "adhoc-" + hashlib.sha1(repr(sorted(row.items())).encode()).hexdigest()[:12]
    return {**row, "suggestion_id": sid}


def _row_values(row: dict, header: list[str]) -> list[str]:
    return [("" if row.get(h) is None else str(row.get(h, ""))) for h in header]


# ── idempotent append ────────────────────────────────────────────────────

@dataclass
class AppendResult:
    appended: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)
    unidentifiable: int = 0
    unconfirmed_unidentifiable: int = 0
    attempts: int = 0
    duplicates_after: dict[str, list[int]] = field(default_factory=dict)


def append_new(client, sheet_id: str, rows: list[dict]) -> AppendResult:
    """Append suggestions whose derived id is not yet in the tab; safe to retry."""
    result = AppendResult()
    if not rows:
        return result
    wanted: dict[str, dict] = {}
    for row in rows:
        sid = row.get("suggestion_id") or suggestion_id(row)
        if sid is None:
            result.unidentifiable += 1
            wanted[f"unidentifiable-{result.unidentifiable}"] = row  # appended once, cannot be reconciled
        elif sid not in wanted:
            wanted[sid] = row
    dry = bool(runtime.current() and runtime.current().dry_run)

    table = read_table(client, sheet_id)
    present = set(table.ids())
    result.already_present = [s for s in wanted if s in present]
    pending = [s for s in wanted if s not in present]
    header = table.header or list(SUGGESTIONS_HEADERS_V2)

    last_error: Exception | None = None
    while pending and result.attempts < MAX_APPEND_ATTEMPTS:
        result.attempts += 1
        values = [_row_values(_with_id(wanted[s], s, header), header) for s in pending]
        if not table.header:  # empty tab: the header goes in the same single call
            values = [list(header)] + values
        try:
            append_values(client, sheet_id, TAB, values, "append_suggestions")
        except runtime.WriteBlocked:
            raise
        except Exception as exc:  # timeout / API error: the rows may or may not have landed
            last_error = exc
            logger.warning("append attempt %d failed (%s); reconciling before any retry", result.attempts, exc)
        else:
            last_error = None
        if dry:
            result.appended = pending
            return result
        table = read_table(client, sheet_id)  # reconcile against what is actually there
        now_present = set(table.ids())
        landed = [s for s in pending if s in now_present
                  or (s.startswith("unidentifiable-") and last_error is None)]
        # Rows without an identity can't be reconciled, so they are never retried.
        unconfirmed = [s for s in pending if s.startswith("unidentifiable-") and last_error is not None]
        result.unconfirmed_unidentifiable += len(unconfirmed)
        result.appended += landed
        pending = [s for s in pending if s not in landed and s not in unconfirmed]
    if pending:
        raise RuntimeError(f"suggestions append incomplete after {result.attempts} attempts: "
                           f"{len(pending)} row(s) not confirmed ({type(last_error).__name__ if last_error else 'unknown'})")
    result.duplicates_after = {s: n for s, n in table.ids().items() if s in wanted and len(n) > 1}
    if result.duplicates_after:
        logger.error("Suggestions now duplicated (not deleted; review): %s", result.duplicates_after)
    return result


# ── feedback ─────────────────────────────────────────────────────────────

@dataclass
class FeedbackResult:
    updated: list[str] = field(default_factory=list)   # slack ts
    unchanged: list[str] = field(default_factory=list)
    moved: dict[str, tuple[int, int]] = field(default_factory=dict)
    refused: dict[str, str] = field(default_factory=dict)


def _locate(fresh: Table, ts: str, expected_sid: str | None) -> tuple[Row | None, str | None]:
    matches = fresh.by_ts().get(ts, [])
    if not matches:
        return None, "row with this Slack ts no longer exists"
    if len(matches) > 1:
        return None, f"Slack ts appears on {len(matches)} rows {[m.number for m in matches]}"
    row = matches[0]
    if expected_sid is None or row.sid is None:
        return None, "row cannot be identified (missing pick fields)"
    if row.sid != expected_sid:
        return None, f"row with this ts is a different suggestion (id {row.sid} != {expected_sid})"
    return row, None


def apply_feedback(client, sheet_id: str, original: Table, feedback: dict[str, dict]) -> FeedbackResult:
    """Write feedback values (keyed by Slack ts) into the four feedback cells only.

    ``feedback[ts]`` holds only the fields that were actually fetched; fields
    missing from it are never written (a failed Slack read never blanks data).
    """
    result = FeedbackResult()
    if not feedback:
        return result
    expected = {}
    for ts in feedback:
        rows = original.by_ts().get(ts, [])
        expected[ts] = rows[0].sid if len(rows) == 1 else None
        if len(rows) != 1:
            result.refused[ts] = f"Slack ts appears on {len(rows)} rows in the original read"

    fresh = read_table(client, sheet_id)
    col = {h: i for i, h in enumerate(fresh.header)}
    updates: list[dict] = []
    planned: dict[str, tuple[int, dict[str, str]]] = {}
    for ts, fields in feedback.items():
        if ts in result.refused:
            continue
        row, problem = _locate(fresh, ts, expected[ts])
        if problem:
            result.refused[ts] = problem
            continue
        original_row = original.by_ts()[ts][0]
        if row.number != original_row.number:
            result.moved[ts] = (original_row.number, row.number)
        changes = {k: str(v) for k, v in fields.items() if k in FEEDBACK_COLUMNS and row.values[col[k]] != str(v)}
        if not changes:
            result.unchanged.append(ts)
            continue
        for k, v in changes.items():
            updates.append({"range": rowcol_to_a1(row.number, col[k] + 1), "values": [[v]]})
        planned[ts] = (row.number, changes)

    if not updates:
        return result
    update_cells(client, sheet_id, TAB, updates, "update_feedback_cells")
    if runtime.current() and runtime.current().dry_run:
        result.updated = list(planned)
        return result

    after = read_table(client, sheet_id)
    by_number = {r.number: r for r in after.rows}
    misplaced = []
    for ts, (number, changes) in planned.items():
        r = by_number.get(number)
        if r is None or r.slack_ts != ts or any(r.values[col[k]] != v for k, v in changes.items()):
            misplaced.append(f"ts {ts} at row {number}")
        else:
            result.updated.append(ts)
    if misplaced:
        raise SuggestionsWriteIncident(
            "feedback verification failed — the tab changed during the write; review these rows by hand: "
            + "; ".join(misplaced))
    return result


# ── updates addressed by suggestion id ───────────────────────────────────

@dataclass
class UpdateResult:
    updated: list[str] = field(default_factory=list)
    refused: dict[str, str] = field(default_factory=dict)


def update_by_id(client, sheet_id: str, changes: dict[str, dict[str, str]], op: str) -> UpdateResult:
    """Write named columns of rows identified by their explicit suggestion_id.

    Fresh read -> each id must be on exactly one row whose derived id still
    matches (pick columns unedited) -> one batch write of only the named
    cells -> read-back verifies every cell landed on the row carrying that id.
    A failed verification raises SuggestionsWriteIncident (manual review).
    Only DELIVERY_COLUMNS, slack_ts and FEEDBACK_COLUMNS may be written.
    """
    result = UpdateResult()
    if not changes:
        return result
    allowed = set(DELIVERY_COLUMNS) | {"slack_ts"} | set(FEEDBACK_COLUMNS)
    allowed.discard("suggestion_id")
    fresh = read_table(client, sheet_id)
    dry = bool(runtime.current() and runtime.current().dry_run)
    if fresh.schema != 2 and not (dry and not fresh.header):  # a dry run may "append" to an empty tab
        raise SuggestionsReadError("suggestions tab is not on schema v2 (run scripts/migrate_suggestions.py)")
    col = {h: i for i, h in enumerate(fresh.header)}
    by_id = fresh.by_id()
    updates, planned = [], {}
    virtual: dict[str, dict] = {}
    for sid, fields in changes.items():
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"update_by_id may not write {sorted(bad)}")
        rows = by_id.get(sid, [])
        if dry and not rows:
            virtual[sid] = fields  # a row this dry run "appended" but never wrote
            continue
        if len(rows) != 1:
            result.refused[sid] = f"suggestion_id on {len(rows)} rows"
            continue
        row = rows[0]
        if row.derived is not None and not row.sid.startswith(row.derived):
            result.refused[sid] = "pick columns no longer match the id (row edited?)"
            continue
        diff = {k: str(v) for k, v in fields.items() if row.values[col[k]] != str(v)}
        for k, v in diff.items():
            updates.append({"range": rowcol_to_a1(row.number, col[k] + 1), "values": [[v]]})
        planned[sid] = (row.number, diff)
    if virtual:
        runtime.authorize("sheets", op, {"tab": TAB, "rows_from_this_dry_run": virtual})
        result.updated += list(virtual)
    if not updates:
        result.updated += list(planned)
        return result
    update_cells(client, sheet_id, TAB, updates, op)
    if dry:
        result.updated += list(planned)
        return result
    after = {r.number: r for r in read_table(client, sheet_id).rows}
    misplaced = []
    for sid, (number, diff) in planned.items():
        r = after.get(number)
        if r is None or r.sid != sid or any(r.values[col[k]] != v for k, v in diff.items()):
            misplaced.append(f"{sid} at row {number}")
        else:
            result.updated.append(sid)
    if misplaced:
        raise SuggestionsWriteIncident("verification failed after a by-id update; review: " + "; ".join(misplaced))
    return result


# ── migration v1 -> v2 (planning; applied by scripts/migrate_suggestions.py) ──

def plan_migration(table: Table, force: bool = False) -> dict:
    """Assign immutable ids and initial delivery columns to every legacy row.

    * unique derived id            -> that id
    * duplicated derived id        -> first keeps it; later ones get "<id>~2", "~3"
      (reported: the same pick was logged more than once)
    * unidentifiable (no pick ids) -> "legacy-<hash of the row's cells + occurrence>"
    delivery_state: "posted" when a Slack ts exists, else "legacy_unknown".
    Only the new columns are planned; no existing cell changes.
    """
    if table.schema == 2 and not force:
        return {"already_migrated": True, "rows": len(table.rows), "assignments": []}
    seen: dict[str, int] = {}
    content_seen: dict[str, int] = {}
    assignments, duplicates, unidentifiable = [], {}, []
    for r in table.rows:
        if r.derived:
            n = seen.get(r.derived, 0) + 1
            seen[r.derived] = n
            sid = r.derived if n == 1 else f"{r.derived}~{n}"
            note = "migrated" if n == 1 else f"migrated; legacy duplicate #{n} of {r.derived}"
            if n > 1:
                duplicates.setdefault(r.derived, []).append(r.number)
        else:
            digest = hashlib.sha1("\x1f".join(r.values).encode()).hexdigest()[:12]
            k = content_seen.get(digest, 0) + 1
            content_seen[digest] = k
            sid = f"legacy-{digest}" + (f"~{k}" if k > 1 else "")
            note = "migrated; legacy row without pick ids"
            unidentifiable.append(r.number)
        state = "posted" if r.slack_ts else "legacy_unknown"
        assignments.append({"row": r.number, "suggestion_id": sid, "delivery_state": state,
                            "delivery_attempts": "", "last_attempt_at": "", "delivery_run_id": "",
                            "data_date": "", "delivery_note": note})
    return {"already_migrated": False, "rows": len(table.rows), "assignments": assignments,
            "legacy_duplicates": {k: v for k, v in duplicates.items()}, "unidentifiable_rows": unidentifiable}
