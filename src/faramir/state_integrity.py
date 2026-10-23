"""State-tab integrity: checksums, validation, recovery decisions, editor merges.

Pure logic — no Sheets I/O (that lives in sheet.py / state_store.py).

Tabs:
  ``state``      the working state (editors may change only EDITOR_COLUMNS)
  ``state_lkg``  last-known-good copy of a state generation that passed validation
  ``state_meta`` one header row plus three rows keyed ``state``, ``lkg``, ``lock``

Checksums cover system-owned columns only, so an editor setting
``tmdb_id_override`` or ``notes`` never reads as corruption. Editor values are
never dropped: writes and restores take them from the live ``state`` tab
(the editor's latest), and a restore that cannot do so unambiguously stops for
a human instead.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from gspread.utils import numericise_all

STATE_TAB, LKG_TAB, META_TAB = "state", "state_lkg", "state_meta"
META_HEADERS = ["key", "generation", "status", "row_count", "checksum", "written_at", "run_id", "expires_at"]
EDITOR_COLUMNS = ("tmdb_id_override", "notes")
GROSS_PAIRS = (("domestic_yesterday", "domestic_today"), ("worldwide_yesterday", "worldwide_today"))
INT_COLUMNS = ("domestic_yesterday", "domestic_today", "worldwide_yesterday", "worldwide_today",
               "days_in_release", "dom_stale_days", "ww_stale_days")
STATUSES = {"active", "retired"}

# Decision actions
PROCEED = "PROCEED"
RESTORE = "RESTORE"            # interrupted write/restore with a verified backup
ROLL_FORWARD = "ROLL_FORWARD"  # write completed but not confirmed; rows match the expected checksum
ABORT = "ABORT"                # manual intervention required


def _cell(v) -> str:
    return "" if v is None else str(v)


def pad(values: list[list], width: int) -> list[list[str]]:
    return [[_cell(c) for c in r] + [""] * (width - len(r)) for r in values]


def system_columns(header: list[str]) -> list[int]:
    return [i for i, h in enumerate(header) if h not in EDITOR_COLUMNS]


def editor_columns(header: list[str]) -> list[int]:
    return [i for i, h in enumerate(header) if h in EDITOR_COLUMNS]


def checksum(header: list[str], rows: list[list]) -> str:
    """SHA-256 over system-owned columns, rows sorted by tmdb_id; editor columns excluded."""
    idx = system_columns(header)
    rows = pad(rows, len(header))
    key = header.index("tmdb_id") if "tmdb_id" in header else 0
    canon = sorted(([r[i] for i in idx] for r in rows), key=lambda r: (r[idx.index(key)] if key in idx else "", r))
    blob = json.dumps({"header": [header[i] for i in idx], "rows": canon}, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def split(values: list[list]) -> tuple[list[str], list[list[str]]]:
    """Sheet values -> (header, data rows), dropping fully blank trailing rows."""
    if not values:
        return [], []
    header = [_cell(c) for c in values[0]]
    while header and header[-1] == "":
        header.pop()
    rows = pad(values[1:], len(header))
    rows = [r[: len(header)] for r in rows]
    while rows and not any(rows[-1]):
        rows.pop()
    return header, rows


def records(header: list[str], rows: list[list[str]]) -> list[dict]:
    """Rows as dicts with gspread's get_all_records numeric conversion (pipeline input)."""
    return [dict(zip(header, numericise_all(list(r)))) for r in rows]


def row_values(header: list[str], rows: list[dict]) -> list[list[str]]:
    return [[_cell(r.get(h, "")) for h in header] for r in rows]


def row_problems(header: list[str], rows: list[list[str]], expected_header: list[str]) -> list[str]:
    problems = []
    if header != expected_header:
        return [f"header mismatch: {header} != expected {expected_header}"]
    col = {h: i for i, h in enumerate(header)}
    seen: set[str] = set()
    for n, r in enumerate(rows, start=2):
        tid = r[col["tmdb_id"]]
        if not tid.isdigit():
            problems.append(f"row {n}: tmdb_id {tid!r} is not an integer")
        elif tid in seen:
            problems.append(f"row {n}: duplicate tmdb_id {tid}")
        seen.add(tid)
        if r[col["status"]] not in STATUSES:
            problems.append(f"row {n}: status {r[col['status']]!r}")
        for c in INT_COLUMNS:
            v = r[col[c]]
            if v and not v.isdigit():
                problems.append(f"row {n}: {c} {v!r} is not a non-negative integer")
        for y, t in GROSS_PAIRS:
            if r[col[y]].isdigit() and r[col[t]].isdigit() and int(r[col[t]]) < int(r[col[y]]):
                problems.append(f"row {n}: {t} < {y}")
    return problems


@dataclass
class TabCheck:
    valid: bool
    problems: list[str]
    header: list[str]
    rows: list[list[str]]
    checksum: str


def check_tab(values: list[list], meta_entry: dict | None, expected_header: list[str]) -> TabCheck:
    header, rows = split(values)
    problems = row_problems(header, rows, expected_header) if values else ["tab is empty (no header)"]
    digest = checksum(header, rows) if header else ""
    if meta_entry is None:
        problems.append("no meta entry")
    else:
        if meta_entry.get("status") not in ("ok", "restored"):
            problems.append(f"meta status {meta_entry.get('status')!r}")
        if str(meta_entry.get("row_count")) != str(len(rows)):
            problems.append(f"row count {len(rows)} != meta {meta_entry.get('row_count')}")
        if meta_entry.get("checksum") != digest:
            problems.append("checksum mismatch")
    return TabCheck(not problems, problems, header, rows, digest)


# ── meta ─────────────────────────────────────────────────────────────────

def parse_meta(values: list[list]) -> dict[str, dict]:
    header, rows = split(values)
    if header != META_HEADERS:
        raise ValueError(f"state_meta header {header} != {META_HEADERS}")
    return {r[0]: dict(zip(META_HEADERS, r)) for r in rows if r and r[0]}


def meta_values(meta: dict[str, dict]) -> list[list[str]]:
    out = [list(META_HEADERS)]
    for key in ("state", "lkg", "lock"):
        entry = meta.get(key, {"key": key})
        out.append([_cell(entry.get(h, key if h == "key" else "")) for h in META_HEADERS])
    return out


def initial_meta(state_values: list[list], now_iso: str, run_id: str) -> dict[str, dict]:
    """Meta for a state tab adopted into the integrity system (generation 1)."""
    header, rows = split(state_values)
    digest = checksum(header, rows)
    base = {"generation": "1", "status": "ok", "row_count": str(len(rows)), "checksum": digest,
            "written_at": now_iso, "run_id": run_id, "expires_at": ""}
    return {
        "state": {"key": "state", **base},
        "lkg": {"key": "lkg", **base},
        "lock": {"key": "lock", "generation": "", "status": "released", "row_count": "", "checksum": "",
                 "written_at": now_iso, "run_id": run_id, "expires_at": ""},
    }


def lock_holder(meta: dict[str, dict], now: datetime) -> str | None:
    """run_id of an unexpired held lock, else None."""
    lock = meta.get("lock") or {}
    if lock.get("status") != "held":
        return None
    try:
        expires = datetime.fromisoformat(lock.get("expires_at", ""))
    except ValueError:
        return lock.get("run_id") or "unknown"  # unparseable expiry: treat as held
    return lock.get("run_id") if expires > now else None


# ── recovery decision ────────────────────────────────────────────────────

@dataclass
class Decision:
    action: str
    reason: str
    state: TabCheck | None = None
    lkg: TabCheck | None = None
    meta: dict = field(default_factory=dict)
    editor_conflicts: list[str] = field(default_factory=list)


def decide(state_values, lkg_values, meta: dict[str, dict] | None, expected_header, run_id: str, now: datetime) -> Decision:
    """The recovery decision table (see docs/v2/increment4_review/README.md)."""
    if meta is None:
        return Decision(ABORT, "not_initialized: state_meta missing — run scripts/init_state_integrity.py (separately approved)")
    holder = lock_holder(meta, now)
    if holder and holder != run_id:
        return Decision(ABORT, f"concurrent_run: state lock held by {holder} until {meta['lock'].get('expires_at')}", meta=meta)

    st_meta, lkg_meta = meta.get("state"), meta.get("lkg")
    status = (st_meta or {}).get("status")
    state = check_tab(state_values, st_meta, expected_header)
    lkg = check_tab(lkg_values, lkg_meta, expected_header) if lkg_values is not None else None
    d = Decision(PROCEED, "state valid", state, lkg, meta)

    if status in ("ok", "restored"):
        if state.valid:
            return d
        return Decision(ABORT, "unexplained_mismatch: state differs from its confirmed generation ("
                        + "; ".join(state.problems[:5]) + ") — manual review required", state, lkg, meta)

    if status in ("writing", "restoring"):
        header, rows = split(state_values)
        rows_ok = header == expected_header and not row_problems(header, rows, expected_header)
        if status == "writing" and rows_ok and checksum(header, rows) == st_meta.get("checksum") \
                and str(len(rows)) == str(st_meta.get("row_count")):
            return Decision(ROLL_FORWARD, f"write of generation {st_meta.get('generation')} completed but was not confirmed", state, lkg, meta)
        target = int(st_meta.get("generation") or 0) - (1 if status == "writing" else 0)
        if lkg is None or not lkg.valid:
            return Decision(ABORT, f"interrupted_{status}_without_valid_backup: "
                            + ("; ".join(lkg.problems[:5]) if lkg else "state_lkg missing") + " — manual restore required",
                            state, lkg, meta)
        if str(lkg_meta.get("generation")) != str(target):
            return Decision(ABORT, f"backup_generation_mismatch: backup is generation {lkg_meta.get('generation')}, "
                            f"interrupted {status} needs {target} — manual restore required", state, lkg, meta)
        return Decision(RESTORE, f"interrupted {status} of generation {st_meta.get('generation')}; "
                        f"verified backup generation {target}", state, lkg, meta)

    return Decision(ABORT, f"unknown state meta status {status!r} — manual review required", state, lkg, meta)


# ── editor columns ───────────────────────────────────────────────────────

def merge_editor_values(header: list[str], base_rows: list[list[str]], live_values: list[list],
                        strict: bool) -> tuple[list[list[str]], list[str], int]:
    """Overlay the live tab's editor-column values onto ``base_rows`` by tmdb_id.

    Returns (merged rows, conflicts, preserved count). Conflicts:
      * the live tab has the same tmdb_id with different editor values;
      * (strict) the live tab has a non-empty editor value on a tmdb_id that
        ``base_rows`` does not contain — it would be lost.
    """
    lh, lrows = split(live_values) if live_values else ([], [])
    if not lh or "tmdb_id" not in lh:
        return [list(r) for r in base_rows], [], 0
    lcol = {h: i for i, h in enumerate(lh)}
    ed = [h for h in EDITOR_COLUMNS if h in lcol and h in header]
    live: dict[str, tuple] = {}
    conflicts: list[str] = []
    for r in lrows:
        tid = r[lcol["tmdb_id"]]
        vals = tuple(r[lcol[h]] for h in ed)
        if tid in live and live[tid] != vals:
            conflicts.append(f"tmdb_id {tid}: live tab has conflicting editor values {live[tid]} vs {vals}")
        live.setdefault(tid, vals)
    tcol = header.index("tmdb_id")
    base_ids = {r[tcol] for r in base_rows}
    if strict:
        for tid, vals in live.items():
            if tid not in base_ids and any(vals):
                conflicts.append(f"tmdb_id {tid}: editor values {vals} exist only in the live tab and would be lost")
    merged, preserved = [], 0
    for r in base_rows:
        r = list(r)
        vals = live.get(r[tcol])
        if vals is not None:
            for h, v in zip(ed, vals):
                if r[header.index(h)] != v:
                    preserved += 1
                r[header.index(h)] = v
        merged.append(r)
    return merged, conflicts, preserved
