"""Weekend history: append-only, content-addressed, transactional (§5, Increment 11).

Two append-only tabs (created only by the Increment 12 migration, never here):

* ``weekend_history``    one row per film per collection (HISTORY_HEADERS);
* ``weekend_collections`` the event log: begin / commit / observe / reject / abort.

A *collection* is one complete, verified weekend chart. Its ``collection_id``
comes from the chart's content (source, weekend, content hash) — never from
the time it was collected — so retries and concurrent runs compute the same
ids. ``record_id`` = hash(collection_id, TN slug).

Protocol (``collect``):

1. Only a ``verified`` weekend ``Chart`` is collected. Anything else appends
   one ``reject`` event (status + issues) and **no history rows**.
2. Read the event log and history. A failed read stops with no writes (it is
   never treated as "no history").
3. Decide: already committed → append one ``observe`` event (retrieval
   evidence, zero rows); ``begin`` without ``commit`` (an earlier crash) →
   resume, appending only the missing ``record_id``s; otherwise ``begin``
   (new revision) then all rows in one ``append_rows``.
4. Verify by reading back: every expected ``record_id`` must be present with
   exactly the expected cell values. Only then append ``commit``. A failed
   verification leaves the collection uncommitted (invisible) for the next run.
5. Uncommitted collections for the weekend older than 48 hours whose content
   is not today's chart get an ``abort`` event. Nothing is ever deleted.

Readers (``read_weekend``) see only committed collections, de-duplicate by
``record_id`` (first occurrence) and check the committed row count. The
weekend's *current* version is the committed collection observed most
recently (begin/commit/observe retrieval time; revision breaks ties), so a
revision supersedes the earlier snapshot without erasing it, and a source
that reverts to earlier figures is followed. A failed or rejected newer
collection never affects an earlier committed one.

Estimates vs actuals: The Numbers does not label them, and nothing here
pretends to know. ``collection_status`` is a collection-policy status:
``confirmed_by_recollection`` when the current content was retrieved at least
twice and at least once on or after the Tuesday after the weekend [CAL];
otherwise ``provisional``. Identical figures seen twice are not proof of an
official final; every retrieval stays in the event log as evidence.

Missing data: a weekend with no committed collection is ``unknown`` — never
"dropped off the chart", never a decline, never a broken streak
(``film_series`` / ``streak``).

Collection is off unless ``FARAMIR_COLLECT_HISTORY=true``; nothing in this
module fetches pages. Every write goes through ``sheet.append_values``
(runtime write guard).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from faramir.sheet import SheetReadError, append_values, read_values
from faramir.tn_chart import Chart, ChartRow

HISTORY_TAB, COLLECTIONS_TAB = "weekend_history", "weekend_collections"
SOURCE = "the-numbers"
ORPHAN_AFTER = timedelta(hours=48)
CONFIRM_WEEKDAY_OFFSET = 4  # the Tuesday after the weekend: Friday + 4 days [CAL]

HISTORY_HEADERS = [
    "record_id", "collection_id", "source", "source_url", "chart_type", "weekend_start", "weekend_end",
    "position", "source_film_key", "title_reported", "slug_year",
    "tmdb_id", "imdb_id", "identity_confidence", "identity_method",
    "engagement", "first_us_release", "engagement_start", "total_gross_scope",
    "source_rank", "source_rank_text", "derived_rank", "derived_rank_basis",
    "prev_rank_reported", "prev_status",
    "weekend_gross", "pct_change_reported", "theaters", "theater_avg", "total_gross", "days_in_release",
    "retrieved_at_utc", "run_id", "parser_version",
]
COLLECTION_HEADERS = [
    "event_id", "collection_id", "source", "weekend_start", "event", "revision",
    "expected_rows", "rows_hash", "verified_rows", "reported_count", "reported_total", "parsed_total",
    "content_hash", "chart_status", "status_detail", "source_url", "http_status", "retrieved_at_utc",
    "at_utc", "run_id", "parser_version", "error",
]
EVENTS = ("begin", "commit", "observe", "reject", "abort")
# Columns that describe this collection attempt, not the content; excluded from verification and
# from duplicate comparison (a resumed or concurrent append may carry another run's values).
ATTEMPT_COLUMNS = ("retrieved_at_utc", "run_id")


class HistoryReadError(RuntimeError):
    """History could not be read; never interpreted as 'no history'."""


class HistoryNotInitialized(HistoryReadError):
    """The history tabs do not exist (created only by the approved migration)."""


def collection_enabled() -> bool:
    return os.environ.get("FARAMIR_COLLECT_HISTORY", "").strip().lower() == "true"


def _sha(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:20]


def collection_id(chart: Chart) -> str:
    return _sha(SOURCE, chart.weekend_start, chart.content_hash())


def record_id(coll_id: str, film_key: str) -> str:
    return _sha(coll_id, film_key)


def event_id(coll_id: str, event: str, run_id: str) -> str:
    # begin/commit are idempotent per collection; observe/reject/abort are per run.
    return _sha(coll_id, event) if event in ("begin", "commit") else _sha(coll_id, event, run_id)


def rows_hash(record_ids) -> str:
    return _sha(*sorted(record_ids))


def _cell(v) -> str:
    if v is None:
        return ""  # unavailable stays blank: never 0
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


# ── rows ─────────────────────────────────────────────────────────────────

def history_row(chart: Chart, row: ChartRow, coll_id: str, identity: dict | None, retrieved_at: str,
                run_id: str) -> list[str]:
    """One history row. Identity comes only from a state row matched by TN slug (never by title)
    and keeps its class: a legacy identity stays legacy."""
    ident = identity or {}
    values = {
        "record_id": record_id(coll_id, row.tn_slug), "collection_id": coll_id, "source": SOURCE,
        "source_url": chart.source_url, "chart_type": chart.chart_type,
        "weekend_start": chart.weekend_start, "weekend_end": chart.weekend_end, "position": row.position,
        "source_film_key": row.tn_slug, "title_reported": row.title_reported, "slug_year": row.slug_year,
        "tmdb_id": ident.get("tmdb_id"), "imdb_id": ident.get("imdb_id"),
        "identity_confidence": ident.get("identity_confidence") or "unmatched",
        "identity_method": ident.get("identity_method"),
        "engagement": row.engagement, "first_us_release": row.first_us_release,
        "engagement_start": row.engagement_start, "total_gross_scope": row.total_gross_scope,
        "source_rank": row.source_rank, "source_rank_text": row.source_rank_text,
        "derived_rank": row.derived_rank, "derived_rank_basis": "faramir_gross_desc",
        "prev_rank_reported": row.prev_rank_reported, "prev_status": row.prev_status,
        "weekend_gross": row.gross, "pct_change_reported": row.weekly_change_pct, "theaters": row.theaters,
        "theater_avg": row.theater_avg, "total_gross": row.total_gross, "days_in_release": row.days_in_release,
        "retrieved_at_utc": retrieved_at, "run_id": run_id, "parser_version": chart.parser_version,
    }
    return [_cell(values[h]) for h in HISTORY_HEADERS]


def _content(values: list[str]) -> tuple:
    return tuple(v for h, v in zip(HISTORY_HEADERS, values) if h not in ATTEMPT_COLUMNS)


# ── reading ──────────────────────────────────────────────────────────────

def _read(gc, sheet_id: str, tab: str, headers: list[str]) -> list[dict]:
    from faramir.sheet import TabMissing

    try:
        values = read_values(gc, sheet_id, tab)
    except TabMissing as exc:
        raise HistoryNotInitialized(f"{tab} does not exist — created only by the approved migration") from exc
    except SheetReadError as exc:
        raise HistoryReadError(str(exc)) from exc
    if not values or [str(h) for h in values[0][:len(headers)]] != headers:
        raise HistoryReadError(f"{tab}: header does not match the expected layout")
    return [dict(zip(headers, [str(c) for c in r] + [""] * (len(headers) - len(r)))) for r in values[1:] if any(r)]


@dataclass
class Log:
    events: list[dict]                       # de-duplicated by event_id (first occurrence)
    history: list[dict]                      # physical rows (duplicates kept; readers de-duplicate)

    def for_collection(self, coll_id: str, event: str | None = None) -> list[dict]:
        return [e for e in self.events if e["collection_id"] == coll_id and (event is None or e["event"] == event)]

    def rows(self, coll_id: str) -> list[dict]:
        return [r for r in self.history if r["collection_id"] == coll_id]


def read_log(gc, sheet_id: str) -> Log:
    events, seen = [], set()
    for e in _read(gc, sheet_id, COLLECTIONS_TAB, COLLECTION_HEADERS):
        if e["event_id"] not in seen:
            seen.add(e["event_id"])
            events.append(e)
    return Log(events, _read(gc, sheet_id, HISTORY_TAB, HISTORY_HEADERS))


# ── collecting ───────────────────────────────────────────────────────────

@dataclass
class Plan:
    action: str                              # disabled | reject | observe | begin | resume
    chart: Chart
    collection_id: str
    revision: int | None = None
    rows: list[list[str]] = field(default_factory=list)       # every expected row
    missing: list[list[str]] = field(default_factory=list)    # rows still to append
    reason: str = ""
    orphans: list[str] = field(default_factory=list)


@dataclass
class Result:
    status: str          # disabled | rejected | unchanged | committed | resumed_committed | unverified | dry_run | read_failed
    collection_id: str | None = None
    revision: int | None = None
    rows_written: int = 0
    detail: str = ""
    orphans_aborted: list[str] = field(default_factory=list)


def plan_collection(log: Log, chart: Chart, identities: dict[str, dict], retrieved_at: str, run_id: str,
                    now: datetime) -> Plan:
    coll = collection_id(chart)
    if chart.chart_type != "weekend" or not chart.verified:
        codes = sorted({i.code for i in chart.issues})
        return Plan("reject", chart, coll, reason=f"chart {chart.status}" + (f": {', '.join(codes)}" if codes else "")
                    + ("" if chart.chart_type == "weekend" else f"; chart type {chart.chart_type!r}"))
    keys = [r.tn_slug for r in chart.rows]
    if len(set(keys)) != len(keys):
        return Plan("reject", chart, coll, reason="duplicate_film_key: a TN slug appears twice on the chart")
    rows = [history_row(chart, r, coll, identities.get(r.tn_slug), retrieved_at, run_id) for r in chart.rows]

    weekend = chart.weekend_start
    begun = [e for e in log.events if e["weekend_start"] == weekend and e["event"] == "begin"]
    committed_ids = {e["collection_id"] for e in log.events if e["event"] == "commit"}
    aborted = {e["collection_id"] for e in log.events if e["event"] == "abort"}
    orphans = [e["collection_id"] for e in begun
               if e["collection_id"] not in committed_ids and e["collection_id"] != coll
               and e["collection_id"] not in aborted
               and (now - (_parse_utc(e["at_utc"]) or now)) > ORPHAN_AFTER]

    if coll in committed_ids:
        return Plan("observe", chart, coll, rows=rows, orphans=orphans)
    mine = [e for e in begun if e["collection_id"] == coll]
    if mine:
        present = {r["record_id"] for r in log.rows(coll)}
        missing = [r for r in rows if r[0] not in present]
        return Plan("resume", chart, coll, revision=int(mine[0]["revision"] or 0), rows=rows, missing=missing,
                    orphans=orphans)
    revision = max((int(e["revision"] or 0) for e in begun), default=0) + 1
    return Plan("begin", chart, coll, revision=revision, rows=rows, missing=rows, orphans=orphans)


def _event(plan: Plan, event: str, run_id: str, retrieved_at: str, now: datetime, http_status, **extra) -> list[str]:
    c = plan.chart
    values = {
        "event_id": event_id(plan.collection_id, event, run_id), "collection_id": plan.collection_id,
        "source": SOURCE, "weekend_start": c.weekend_start, "event": event, "revision": plan.revision,
        "expected_rows": len(plan.rows) if plan.rows else None,
        "rows_hash": rows_hash(r[0] for r in plan.rows) if plan.rows else None,
        "reported_count": c.reported_count, "reported_total": c.reported_total, "parsed_total": c.parsed_total,
        "content_hash": c.content_hash(), "chart_status": c.status, "source_url": c.source_url,
        "http_status": http_status, "retrieved_at_utc": retrieved_at, "at_utc": _utc(now), "run_id": run_id,
        "parser_version": c.parser_version, **extra,
    }
    return [_cell(values.get(h)) for h in COLLECTION_HEADERS]


def _append(gc, sheet_id, tab, rows, op) -> bool:
    return append_values(gc, sheet_id, tab, rows, op)


def verify(log: Log, plan: Plan) -> tuple[bool, str]:
    """Every expected record_id present, with exactly the expected content."""
    by_id: dict[str, list[tuple]] = {}
    for r in log.rows(plan.collection_id):
        by_id.setdefault(r["record_id"], []).append(_content([r[h] for h in HISTORY_HEADERS]))
    for expected in plan.rows:
        found = by_id.get(expected[0], [])
        if not found:
            return False, f"record {expected[0]} missing"
        if _content(expected) not in found:
            return False, f"record {expected[0]} content differs from the chart"
    extra = set(by_id) - {r[0] for r in plan.rows}
    if extra:
        return False, f"{len(extra)} unexpected record id(s) under this collection"
    return True, f"{len(plan.rows)} distinct records verified"


def execute(gc, sheet_id: str, plan: Plan, run_id: str, retrieved_at: str, now: datetime,
            http_status=200) -> Result:
    def ev(event, **extra):
        return _event(plan, event, run_id, retrieved_at, now, http_status, **extra)

    for orphan in plan.orphans:
        o = Plan("abort", plan.chart, orphan)
        _append(gc, sheet_id, COLLECTIONS_TAB,
                [_event(o, "abort", run_id, retrieved_at, now, http_status,
                        status_detail="uncommitted for more than 48 hours and not the current chart content")],
                "history_abort_orphan")

    if plan.action == "reject":
        dry = not _append(gc, sheet_id, COLLECTIONS_TAB,
                          [ev("reject", status_detail=plan.reason,
                              error="; ".join(f"{i.code}: {i.detail}" for i in plan.chart.issues[:10]))],
                          "history_reject")
        return Result("dry_run" if dry else "rejected", plan.collection_id, detail=plan.reason,
                      orphans_aborted=plan.orphans)
    if plan.action == "observe":
        dry = not _append(gc, sheet_id, COLLECTIONS_TAB, [ev("observe", status_detail="identical content re-retrieved")],
                          "history_observe")
        return Result("dry_run" if dry else "unchanged", plan.collection_id, detail="already committed; retrieval recorded",
                      orphans_aborted=plan.orphans)

    resumed = plan.action == "resume"
    first = ev("observe", status_detail="resuming an uncommitted collection; content re-retrieved") if resumed else ev("begin")
    if not _append(gc, sheet_id, COLLECTIONS_TAB, [first], "history_observe" if resumed else "history_begin"):
        return Result("dry_run", plan.collection_id, plan.revision, detail=f"would write {len(plan.missing)} rows")
    if plan.missing:
        if not _append(gc, sheet_id, HISTORY_TAB, plan.missing, "history_append_rows"):
            return Result("dry_run", plan.collection_id, plan.revision, detail=f"would write {len(plan.missing)} rows")

    ok, detail = verify(read_log(gc, sheet_id), plan)
    if not ok:
        return Result("unverified", plan.collection_id, plan.revision, len(plan.missing),
                      detail=f"left uncommitted (invisible to readers) for the next run to resume: {detail}",
                      orphans_aborted=plan.orphans)
    _append(gc, sheet_id, COLLECTIONS_TAB, [ev("commit", verified_rows=len(plan.rows), status_detail=detail)],
            "history_commit")
    return Result("resumed_committed" if resumed else "committed", plan.collection_id, plan.revision,
                  len(plan.missing), detail=detail, orphans_aborted=plan.orphans)


def collect(gc, sheet_id: str, chart: Chart, *, run_id: str, retrieved_at: datetime, now: datetime | None = None,
            identities: dict[str, dict] | None = None, http_status=200) -> Result:
    """Collect one parsed weekend chart. Off unless FARAMIR_COLLECT_HISTORY=true. Never fetches."""
    if not collection_enabled():
        return Result("disabled", detail="FARAMIR_COLLECT_HISTORY is not true; nothing read or written")
    now = now or datetime.now(timezone.utc)
    try:
        log = read_log(gc, sheet_id)
    except HistoryReadError as exc:
        return Result("read_failed", detail=f"no writes: {exc}")
    plan = plan_collection(log, chart, identities or {}, _utc(retrieved_at), run_id, now)
    return execute(gc, sheet_id, plan, run_id, _utc(retrieved_at), now, http_status)


# ── readers ──────────────────────────────────────────────────────────────

@dataclass
class Snapshot:
    weekend_start: str
    status: str                       # available | missing | integrity_error
    collection_id: str | None = None
    revision: int | None = None
    collection_status: str | None = None   # provisional | confirmed_by_recollection (policy, not official)
    rows: list[dict] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)   # every retrieval of this content
    superseded: list[dict] = field(default_factory=list)      # earlier committed versions, kept
    detail: str = ""


def _observations(log: Log, coll: str) -> list[dict]:
    return sorted(({"event": e["event"], "retrieved_at_utc": e["retrieved_at_utc"], "run_id": e["run_id"],
                    "http_status": e["http_status"], "source_url": e["source_url"]}
                   for e in log.for_collection(coll) if e["event"] in ("begin", "observe")),
                  key=lambda o: o["retrieved_at_utc"])


def collection_status(weekend_start: str, observations: list[dict]) -> str:
    """Collection policy (not an official estimate/actual label): confirmed only when this exact
    content was retrieved at least twice, at least once on/after the Tuesday after the weekend."""
    tuesday = date.fromisoformat(weekend_start) + timedelta(days=CONFIRM_WEEKDAY_OFFSET)
    runs = {o["run_id"] for o in observations}
    late = [o for o in observations if (_parse_utc(o["retrieved_at_utc"]) or datetime.min.replace(tzinfo=timezone.utc)).date() >= tuesday]
    return "confirmed_by_recollection" if len(runs) >= 2 and late else "provisional"


def read_weekend(gc, sheet_id: str, weekend_start: str, log: Log | None = None) -> Snapshot:
    """The weekend's current committed version. Raises HistoryReadError on a failed read."""
    log = log or read_log(gc, sheet_id)
    committed = {e["collection_id"]: e for e in log.events if e["event"] == "commit" and e["weekend_start"] == weekend_start}
    if not committed:
        return Snapshot(weekend_start, "missing", detail="no committed collection: unknown, not 'off the chart'")

    def last_seen(coll):
        obs = _observations(log, coll)
        return (obs[-1]["retrieved_at_utc"] if obs else "", int(committed[coll]["revision"] or 0))

    order = sorted(committed, key=last_seen, reverse=True)
    current = order[0]
    rows = collection_rows(log, current)
    expected = int(committed[current]["verified_rows"] or -1)
    obs = _observations(log, current)
    superseded = [{"collection_id": c, "revision": int(committed[c]["revision"] or 0),
                   "last_retrieved_at_utc": last_seen(c)[0]} for c in order[1:]]
    if len(rows) != expected:
        return Snapshot(weekend_start, "integrity_error", current, detail=f"{len(rows)} distinct rows, commit says {expected}")
    return Snapshot(weekend_start, "available", current, int(committed[current]["revision"] or 0),
                    collection_status(weekend_start, obs), rows, obs, superseded)


def collection_rows(log: Log, coll_id: str) -> list[dict]:
    """Rows of one committed collection (e.g. a superseded revision), de-duplicated. [] if uncommitted."""
    if not log.for_collection(coll_id, "commit"):
        return []
    rows, seen = [], set()
    for r in log.rows(coll_id):
        if r["record_id"] not in seen:
            seen.add(r["record_id"])
            rows.append(r)
    return sorted(rows, key=lambda r: int(r["position"]))


def film_series(gc, sheet_id: str, film_key: str, weekend_starts: list[str], log: Log | None = None) -> list[dict]:
    """Per weekend: charted (with the row), not_on_chart (a complete committed chart without the film)
    or unknown (no committed collection). A missing weekend is never a decline or an exit."""
    log = log or read_log(gc, sheet_id)
    out = []
    for w in weekend_starts:
        snap = read_weekend(gc, sheet_id, w, log)
        if snap.status != "available":
            out.append({"weekend_start": w, "state": "unknown", "reason": snap.status})
            continue
        row = next((r for r in snap.rows if r["source_film_key"] == film_key), None)
        out.append({"weekend_start": w, "state": "charted" if row else "not_on_chart", "row": row,
                    "collection_status": snap.collection_status})
    return out


def streak(series: list[dict], predicate) -> dict:
    """Consecutive weekends (most recent first) where predicate(row) holds. An unknown weekend makes
    the streak ``indeterminate`` from that point — never ``broken``."""
    length = 0
    for entry in sorted(series, key=lambda e: e["weekend_start"], reverse=True):
        if entry["state"] == "unknown":
            return {"length": length, "status": "indeterminate", "at": entry["weekend_start"]}
        if entry["state"] != "charted" or not predicate(entry["row"]):
            return {"length": length, "status": "broken", "at": entry["weekend_start"]}
        length += 1
    return {"length": length, "status": "unbroken", "at": None}
