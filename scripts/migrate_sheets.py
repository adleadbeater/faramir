"""Migrate the production workbook from Faramir v1 to schema v2 (deployment stage S5).

    python scripts/migrate_sheets.py                                   # plan: read-only (default)
    python scripts/migrate_sheets.py --rollback-plan                   # print the rollback procedure
    python scripts/migrate_sheets.py --dry-run --apply --confirm-sheet ID
    FARAMIR_ENABLED=true python scripts/migrate_sheets.py --apply --confirm-sheet ID

Running --apply is a production action: separately approved, with scheduled runs
disabled, against a COPY of the workbook first. The daily run never migrates;
until this script has finished, preflight refuses every run.

Apply steps (each is skipped when already done, so a re-run is a no-op and a
crash at any point is safe to re-run):

1. Validate. ``state`` must be a valid v1 (17-column) or v2 (20-column) tab;
   ``suggestions`` must be the v1 or v2 layout (or empty); an existing tab with
   a v2 name but another header is refused. Nothing is written if this fails.
2. ``suggestions`` → v2: append the delivery columns and ids
   (``migrate_suggestions.py`` logic). Existing cells — picks, Slack ts,
   thumbs, thread feedback — are never written. Read back and verified.
3. State integrity: create ``state_lkg`` (an exact copy of ``state``) then
   ``state_meta`` (``init_state_integrity.py`` logic). Verified.
4. ``state`` → the 20-column layout through the normal state protocol
   (backup to ``state_lkg``, write, read-back checksum, meta confirm). Rows are
   identical; the three new columns start blank; editor values
   (``tmdb_id_override``, ``notes``) are taken from the live tab. Verified.
5. Create the missing tabs with headers only: ``Usage``,
   ``identity_overrides`` (editor-owned, read-only to Faramir),
   ``weekend_history``, ``weekend_collections`` (collection stays off).
6. Verify the whole workbook with the same checks preflight uses (all but the
   schema marker).
7. Only then write ``faramir_meta.schema_version = 2`` — LAST. A crash before
   this leaves preflight failing, never a half-migrated workbook in use.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from faramir import runtime  # noqa: E402
from faramir import sheet as sheet_module  # noqa: E402
from faramir.config import USAGE_TAB_NAME  # noqa: E402
from faramir.history import COLLECTION_HEADERS, COLLECTIONS_TAB, HISTORY_HEADERS, HISTORY_TAB  # noqa: E402
from faramir.preflight import EXPECTED_SCHEMA, META_HEADERS, META_TAB, check_sheets, schema_version  # noqa: E402
from faramir.sheet import (  # noqa: E402
    LEGACY_STATE_HEADERS, STATE_HEADERS, SUGGESTIONS_HEADERS, USAGE_HEADERS, TabMissing, create_tab, read_values,
    update_cells, write_values,
)
from faramir.state_integrity import LKG_TAB, META_TAB as STATE_META_TAB, STATE_TAB, PROCEED, row_problems, split  # noqa: E402
from faramir.suggestions import TAB as SUGGESTIONS_TAB, SuggestionsReadError, plan_migration, read_table  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("faramir.migrate_sheets")

OVERRIDES_TAB = "identity_overrides"
OVERRIDES_HEADERS = ["tn_slug", "tmdb_id", "note"]
NEW_TABS = [(USAGE_TAB_NAME, list(USAGE_HEADERS)), (OVERRIDES_TAB, OVERRIDES_HEADERS),
            (HISTORY_TAB, list(HISTORY_HEADERS)), (COLLECTIONS_TAB, list(COLLECTION_HEADERS))]

ROLLBACK = [
    "Rollback is manual and separately approved. Nothing is ever deleted automatically.",
    "0. Before --apply: make a copy of the whole workbook (File > Make a copy) and keep it until v2 is verified.",
    "1. Set the repository variable FARAMIR_ENABLED=false (v2 runs stop; an unset variable also means disabled).",
    "2. Redeploy the v1 code (main @ f2a0ce4) only if returning to v1; v2 code refuses to run without the marker.",
    "3. Archive (rename, never delete) faramir_meta -> faramir_meta_archived_<date>. v2 preflight then refuses; v1 ignores it.",
    "4. state: v1 rewrites the whole tab with its 17 headers on its first run, dropping tn_slug / identity_confidence /"
    " identity_method (rows and editor values are unchanged). To restore the exact pre-migration tab, copy it back"
    " from the workbook copy.",
    "5. state_meta / state_lkg: follow init_state_integrity.py --rollback-plan (archive state_meta; keep state_lkg"
    " archived until state is verified).",
    "6. suggestions: the original 17 columns were never written by the migration. BEFORE v1 runs again, export the"
    " v2 columns (suggestion_id, delivery_state, ... evidence_json): v1's feedback update (write_suggestions) clears"
    " the tab and rewrites it with its 17 headers, erasing every v2 column. Picks, Slack ts and feedback survive.",
    "7. weekend_history, weekend_collections, identity_overrides: archive (rename). They hold no v1 data;"
    " history is empty unless collection was separately enabled.",
    "8. Usage: if this migration created it, v1 keeps using it; nothing to undo.",
    "9. Verify v1 with one dry run before re-enabling anything.",
]


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(__file__), f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _optional(gc, sheet_id, tab):
    try:
        return read_values(gc, sheet_id, tab)
    except TabMissing:
        return None


def _header(values):
    h = [str(c) for c in (values[0] if values else [])]
    while h and h[-1] == "":
        h.pop()
    return h


# ── plan (read-only) ─────────────────────────────────────────────────────

def plan(gc, sheet_id: str) -> dict:
    p: dict = {"sheet_id": sheet_id, "expected_schema": EXPECTED_SCHEMA, "refuse": [], "steps": []}
    p["schema_version"] = schema_version(gc, sheet_id)

    state = read_values(gc, sheet_id, STATE_TAB)
    sh, srows = split(state)
    layout = "v2" if sh == list(STATE_HEADERS) else "v1" if sh == list(LEGACY_STATE_HEADERS) else "unknown"
    problems = row_problems(sh, srows, list(STATE_HEADERS)) if state else ["state tab is empty"]
    if layout == "unknown":
        problems.insert(0, f"state header is neither the v1 nor the v2 layout: {sh}")
    if problems:
        p["refuse"] += [f"state: {x}" for x in problems[:10]]
    col = {h: i for i, h in enumerate(sh)}
    p["state"] = {"layout": layout, "rows": len(srows),
                  "editor_overrides": sum(1 for r in srows if col.get("tmdb_id_override") is not None and r[col["tmdb_id_override"]]),
                  "notes": sum(1 for r in srows if col.get("notes") is not None and r[col["notes"]])}

    try:
        table = read_table(gc, sheet_id)
    except SuggestionsReadError as exc:
        table = None
        p["refuse"].append(f"suggestions header layout is unexpected: {exc}")
    if table is not None and table.header and (
            table.header[:len(SUGGESTIONS_HEADERS)] != list(SUGGESTIONS_HEADERS)
            or (table.schema == 1 and len(table.header) != len(SUGGESTIONS_HEADERS))):
        p["refuse"].append(f"suggestions header layout is unexpected: {table.header}")
    if table is None:
        p["suggestions"] = {"schema": "unreadable"}
        p["steps"] = []
        p["already_migrated"] = False
        p["rollback"] = ROLLBACK
        return p
    sugg_plan = plan_migration(table) if table.header else {"already_migrated": True, "assignments": []}
    p["suggestions"] = {"schema": table.schema if table.header else "empty", "rows": len(table.rows),
                        "with_slack_ts": sum(1 for r in table.rows if (r.raw.get("slack_ts") or "").strip()),
                        "with_feedback": sum(1 for r in table.rows if any((r.raw.get(c) or "").strip() for c in
                                                                        ("thumbs_up", "thumbs_down", "thread_replies", "thread_feedback"))),
                        **{k: v for k, v in sugg_plan.items() if k != "assignments"}}

    meta = _optional(gc, sheet_id, STATE_META_TAB)
    lkg = _optional(gc, sheet_id, LKG_TAB)
    existing = {}
    for name, headers in NEW_TABS + [(META_TAB, META_HEADERS)]:
        values = _optional(gc, sheet_id, name)
        # A tab left empty by a crash between creating it and writing its header is not done.
        existing[name] = "complete" if values is not None and _header(values) else "empty" if values is not None else None
        if values is not None and _header(values) and _header(values) != headers and not (
                name == META_TAB and _header(values)[:2] == META_HEADERS[:2]):
            p["refuse"].append(f"tab {name!r} exists with an unexpected header: {_header(values)}")
        if values is not None and len(values) > 1 and not _header(values):
            p["refuse"].append(f"tab {name!r} has rows but no header")

    def step(n, name, done, action):
        p["steps"].append({"step": n, "name": name, "status": "done" if done else "pending", "action": action})

    step(2, "suggestions_v2", bool(sugg_plan.get("already_migrated")),
         f"append delivery columns + ids to {len(table.rows)} rows; existing cells untouched")
    step(3, "state_integrity_init", meta is not None,
         "copy state -> state_lkg (verified), then create state_meta" + (" (state_lkg exists from a partial run)" if lkg is not None and meta is None else ""))
    step(4, "state_v2_columns", layout == "v2", "rewrite state on the 20-column layout via the state protocol (rows unchanged)")
    p["empty_tabs"] = [name for name, _ in NEW_TABS if existing[name] == "empty"]
    for name, _ in NEW_TABS:
        step(5, f"create_{name}", existing[name] == "complete",
             f"{'write the header of the empty' if existing[name] == 'empty' else 'create'} {name!r} (header only)")
    step(6, "verify_workbook", False, "run the preflight checks (all but the schema marker)")
    step(7, "schema_marker", p["schema_version"] == str(EXPECTED_SCHEMA), f"write {META_TAB}.schema_version = {EXPECTED_SCHEMA} LAST")
    p["already_migrated"] = p["schema_version"] == str(EXPECTED_SCHEMA) and all(
        s["status"] == "done" for s in p["steps"] if s["step"] != 6)
    p["rollback"] = ROLLBACK
    return p


# ── apply ────────────────────────────────────────────────────────────────

class MigrationStopped(RuntimeError):
    pass


def apply(gc, sheet_id: str, run_id: str) -> dict:
    """Run the pending steps in order; raise MigrationStopped (marker NOT written) on any failure."""
    from faramir.state_store import StateConflict, StateStore

    p = plan(gc, sheet_id)
    if p["refuse"]:
        raise MigrationStopped("refused before any write: " + "; ".join(p["refuse"][:5]))
    if p["already_migrated"]:
        return {"applied": [], "already_migrated": True}
    dry = runtime.current().dry_run
    done: list[str] = []
    pending = {s["name"] for s in p["steps"] if s["status"] == "pending"}

    if "suggestions_v2" in pending:
        ms = _load_script("migrate_suggestions")
        table = read_table(gc, sheet_id)
        sp = plan_migration(table)
        update_cells(gc, sheet_id, SUGGESTIONS_TAB, ms.build_updates(table, sp), "migrate_suggestions_v2")
        if not dry:
            problems = ms.verify(gc, sheet_id, sp)
            if problems:
                raise MigrationStopped(f"suggestions verification failed (safe to re-run): {problems[:5]}")
        done.append("suggestions_v2")

    if "state_integrity_init" in pending:
        init = _load_script("init_state_integrity")
        try:
            init.apply(gc, sheet_id, run_id)
        except SystemExit as exc:
            raise MigrationStopped(f"state integrity init stopped: {exc}") from exc
        done.append("state_integrity_init")

    if "state_v2_columns" in pending:
        if dry and "state_integrity_init" in done:
            done.append("state_v2_columns (would run after state_meta exists; not simulated in dry-run)")
        else:
            store = StateStore(gc, sheet_id, run_id)
            decision = store.open()
            if decision.action != PROCEED:
                raise MigrationStopped(f"state is not in a clean state ({decision.action}: {decision.reason})")
            try:
                store.acquire_lock()
                # The exact cell text (not numericised records), laid out on the 20-column header.
                store.write([dict(zip(decision.state.header, r)) for r in decision.state.rows])
            except StateConflict as exc:
                raise MigrationStopped(f"state write refused: {exc}") from exc
            finally:
                store.release_lock()
            if not dry:
                h, _ = split(read_values(gc, sheet_id, STATE_TAB))
                if h != list(STATE_HEADERS) or StateStore(gc, sheet_id, run_id + "-verify").open().action != PROCEED:
                    raise MigrationStopped("state did not verify on the 20-column layout")
            done.append("state_v2_columns")

    for name, headers in NEW_TABS:
        if f"create_{name}" in pending:
            if name in p["empty_tabs"]:
                write_values(gc, sheet_id, name, [headers], f"write_header_{name}")
            else:
                create_tab(gc, sheet_id, name, [headers])
            if not dry and _header(read_values(gc, sheet_id, name)) != headers:
                raise MigrationStopped(f"{name} header did not verify")
            done.append(f"create_{name}")

    if dry:
        return {"applied": done, "dry_run": True, "marker": "not written in dry-run"}

    check = check_sheets(gc, sheet_id, collect_history=True)
    remaining = [x for x in check.problems if not x.startswith("Sheets schema version")]
    if remaining:
        raise MigrationStopped(f"workbook did not verify; schema marker NOT written: {remaining[:5]}")
    done.append("verify_workbook")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    values = [META_HEADERS, ["schema_version", str(EXPECTED_SCHEMA), now, run_id]]
    if _optional(gc, sheet_id, META_TAB) is None:
        create_tab(gc, sheet_id, META_TAB, values)
    else:
        write_values(gc, sheet_id, META_TAB, values, "write_schema_marker")
    if schema_version(gc, sheet_id) != str(EXPECTED_SCHEMA):
        raise MigrationStopped("schema marker did not verify")
    done.append("schema_marker")
    return {"applied": done, "already_migrated": False}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="migrate_sheets.py")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm-sheet", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback-plan", action="store_true")
    ap.add_argument("--out-root", default=None)
    args = ap.parse_args(argv)
    if args.rollback_plan:
        print("\n".join(ROLLBACK))
        return 0
    sheet_id = os.environ.get("FARAMIR_SHEET_ID", "")
    if not sheet_id:
        logger.error("FARAMIR_SHEET_ID is not set")
        return 2
    gc = sheet_module.get_sheet_client()
    if not args.apply:
        p = plan(gc, sheet_id)
        print(json.dumps(p, indent=2))
        return 1 if p["refuse"] else 0
    if args.confirm_sheet != sheet_id:
        logger.error("--apply requires --confirm-sheet matching FARAMIR_SHEET_ID")
        return 2
    ctx = runtime.new_context(dry_run=args.dry_run, out_root=args.out_root)
    ok, why = ctx.may_run()
    if not ok:
        logger.error("Not applied: %s", why)
        return 4
    with runtime.use(ctx):
        try:
            result = apply(gc, sheet_id, ctx.run_id)
            ctx.report["migration"] = result
        except MigrationStopped as exc:
            ctx.report["migration"] = {"stopped": str(exc)}
            logger.error("Migration stopped (safe to re-run after review): %s", exc)
            return 5
        finally:
            ctx.write_artifacts()
    print(json.dumps({**result, "artifacts": ctx.out_dir}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
