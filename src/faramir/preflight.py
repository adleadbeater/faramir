"""Preflight: every run verifies its configuration and the Sheets schema before
any operational write (P0-7, Increment 12).

Fail closed. A failed check halts the run with zero operational writes and one
alert. Nothing here repairs, creates or migrates anything: a workbook that is
not on schema v2 is reported as "run scripts/migrate_sheets.py (separately
approved)" and the run stops.

Checks (in ``run_daily`` order):

1. **Settings.** Live runs need ``FARAMIR_ENABLED=true`` (an unset variable
   means disabled — also enforced by ``RunContext.may_run``), the Sheets id,
   the Slack webhook, bot token and channel, and the TMDB and Anthropic keys.
2. **Corpus.** Schema v2 and health not ABORT (``faramir.corpus``; already
   enforced in ``run_daily``, recorded here).
3. **Sheets schema.** ``faramir_meta`` has ``schema_version == EXPECTED_SCHEMA``
   (written last by the migration, so a half-migrated workbook fails), and the
   required tabs exist with exactly the expected headers:
   ``state`` (the 20-column v2 layout; the legacy 17-column layout is
   refused here — only the migration upgrades it), ``state_meta``,
   ``state_lkg`` (current or legacy layout; it is a backup), ``suggestions``
   (schema v2 with an id on every row, or a fresh empty tab), ``Usage``;
   plus ``weekend_history`` and ``weekend_collections`` when history
   collection is enabled. ``identity_overrides`` is optional; if present it
   must have ``tn_slug`` and ``tmdb_id`` columns.
4. **State integrity.** The P0-4 decision table (``StateStore.open``), run
   by ``run_daily`` right after this module, still before any write.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from faramir.sheet import STATE_HEADERS, USAGE_HEADERS, SheetReadError, TabMissing, read_values

EXPECTED_SCHEMA = 2
META_TAB = "faramir_meta"
META_HEADERS = ["key", "value", "written_at", "run_id"]
LIVE_SETTINGS = ("FARAMIR_SHEET_ID", "SLACK_WEBHOOK_URL", "SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID",
                 "TMDB_API_KEY", "ANTHROPIC_API_KEY")
DRY_RUN_SETTINGS = ("FARAMIR_SHEET_ID", "SLACK_WEBHOOK_URL")


@dataclass
class Preflight:
    ok: bool = True
    problems: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    def fail(self, problem: str) -> None:
        self.ok = False
        self.problems.append(problem)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "problems": self.problems, "checked": self.checked}


def check_settings(live: bool, env=os.environ) -> Preflight:
    p = Preflight()
    if live and env.get("FARAMIR_ENABLED", "").strip().lower() != "true":
        p.fail("FARAMIR_ENABLED is not 'true' — production is disabled (unset means disabled)")
    for k in (LIVE_SETTINGS if live else DRY_RUN_SETTINGS):
        if not env.get(k):
            p.fail(f"missing required setting {k}")
    p.checked.append("settings")
    return p


def _header(values: list[list]) -> list[str]:
    h = [str(c) for c in (values[0] if values else [])]
    while h and h[-1] == "":
        h.pop()
    return h


def schema_version(gc, sheet_id: str) -> str | None:
    """The workbook's schema marker, or None if faramir_meta is absent."""
    try:
        values = read_values(gc, sheet_id, META_TAB)
    except TabMissing:
        return None
    if _header(values)[:2] != META_HEADERS[:2]:
        return "unreadable"
    for r in values[1:]:
        if r and str(r[0]) == "schema_version":
            return str(r[1]) if len(r) > 1 else ""
    return None


def check_sheets(gc, sheet_id: str, collect_history: bool = False) -> Preflight:
    """Schema marker, required tabs and exact headers. Read-only."""
    from faramir.history import COLLECTION_HEADERS, COLLECTIONS_TAB, HISTORY_HEADERS, HISTORY_TAB
    from faramir.sheet import LEGACY_STATE_HEADERS
    from faramir.state_integrity import META_HEADERS as STATE_META_HEADERS
    from faramir.suggestions import SuggestionsReadError, parse as parse_suggestions

    from faramir.config import USAGE_TAB_NAME

    p = Preflight()
    migrate = "run scripts/migrate_sheets.py (separately approved)"
    try:
        version = schema_version(gc, sheet_id)
        if version != str(EXPECTED_SCHEMA):
            p.fail(f"Sheets schema version is {version!r}, expected '{EXPECTED_SCHEMA}' — {migrate}")

        def tab(name, optional=False):
            try:
                return read_values(gc, sheet_id, name)
            except TabMissing:
                if not optional:
                    p.fail(f"required tab {name!r} is missing — {migrate}")
                return None

        state = tab("state")
        if state is not None:
            h = _header(state)
            if h == list(LEGACY_STATE_HEADERS):
                p.fail(f"state tab is on the legacy 17-column layout — {migrate}")
            elif h != list(STATE_HEADERS):
                p.fail(f"state header does not match the v2 layout: {h}")
        meta = tab("state_meta")
        if meta is not None and _header(meta) != list(STATE_META_HEADERS):
            p.fail("state_meta header does not match")
        lkg = tab("state_lkg")
        if lkg is not None and _header(lkg) not in (list(STATE_HEADERS), list(LEGACY_STATE_HEADERS)):
            p.fail("state_lkg header does not match a known state layout")
        sugg = tab("suggestions")
        if sugg is not None and _header(sugg):
            try:
                table = parse_suggestions(sugg)
            except SuggestionsReadError as exc:
                p.fail(f"suggestions tab is unreadable: {exc}")
            else:
                if table.schema != 2 or any(r.sid is None for r in table.rows):
                    p.fail(f"suggestions tab is not on schema v2 (explicit suggestion_id on every row) — {migrate}")
        usage = tab(USAGE_TAB_NAME)
        if usage is not None and _header(usage) != list(USAGE_HEADERS):
            p.fail(f"{USAGE_TAB_NAME} header does not match")
        overrides = tab("identity_overrides", optional=True)
        if overrides is not None and not {"tn_slug", "tmdb_id"} <= set(_header(overrides)):
            p.fail("identity_overrides lacks tn_slug / tmdb_id columns")
        if collect_history:
            for name, headers in ((HISTORY_TAB, HISTORY_HEADERS), (COLLECTIONS_TAB, COLLECTION_HEADERS)):
                values = tab(name)
                if values is not None and _header(values) != list(headers):
                    p.fail(f"{name} header does not match")
    except SheetReadError as exc:
        p.fail(f"Sheets read failed during preflight: {exc}")
    p.checked.append("sheets")
    return p
