"""Migrate the suggestions tab to schema v2 (explicit suggestion_id + delivery columns).

    python scripts/migrate_suggestions.py                     # plan: read-only
    python scripts/migrate_suggestions.py --dry-run --apply --confirm-sheet ID
    FARAMIR_ENABLED=true python scripts/migrate_suggestions.py --apply --confirm-sheet ID

Run only while scheduled runs are disabled (deployment stage S5). What it does:
  * appends the 7 new column headers to the right of the existing header;
  * fills ONLY those new columns for every existing row (suggestion_id from the
    row's own pick columns; duplicates "~2", "~3"; rows without pick ids
    "legacy-<hash>"; delivery_state "posted" if a Slack ts exists else
    "legacy_unknown");
  * never changes, deletes or reorders an existing cell or row — feedback included;
  * reads the tab back and verifies every row's new id was computed from that
    same row's content. A mismatch (the tab was sorted mid-write) is reported
    and the run is safe to repeat: the new columns are re-derived from content.
Until every row has an id, the daily run refuses to start ("not migrated").

Rollback: set FARAMIR_ENABLED=false and delete the 7 new columns (R..X); the
original 17 columns were never written. Keep a copy of the tab first (File >
Make a copy) — recommended before --apply.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from gspread.utils import rowcol_to_a1

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from faramir import runtime  # noqa: E402
from faramir import sheet as sheet_module  # noqa: E402
from faramir.sheet import DELIVERY_COLUMNS, SUGGESTIONS_HEADERS, update_cells  # noqa: E402
from faramir.suggestions import TAB, plan_migration, read_table  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("faramir.migrate_suggestions")


def build_updates(table, plan) -> list[dict]:
    first_col = len(SUGGESTIONS_HEADERS) + 1  # the new block sits right after the 17 v1 columns
    updates = [{"range": f"{rowcol_to_a1(1, first_col)}:{rowcol_to_a1(1, first_col + len(DELIVERY_COLUMNS) - 1)}",
                "values": [list(DELIVERY_COLUMNS)]}]
    for a in plan["assignments"]:
        updates.append({
            "range": f"{rowcol_to_a1(a['row'], first_col)}:{rowcol_to_a1(a['row'], first_col + len(DELIVERY_COLUMNS) - 1)}",
            "values": [[a[c] for c in DELIVERY_COLUMNS]],
        })
    return updates


def verify(client, sheet_id, plan) -> list[str]:
    after = read_table(client, sheet_id)
    expected = {a["row"]: a["suggestion_id"] for a in plan["assignments"]}
    problems = []
    if after.schema != 2:
        return ["header not on schema v2 after write"]
    for r in after.rows:
        want = expected.get(r.number)
        if want is None or r.sid != want:
            problems.append(f"row {r.number}: id {r.sid!r} expected {want!r}")
        elif r.derived and not want.startswith(r.derived):
            problems.append(f"row {r.number}: id {want} does not match its pick columns")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm-sheet", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-root", default=None)
    ap.add_argument("--repair", action="store_true",
                    help="re-derive the new columns after a failed verification (tab already has them)")
    args = ap.parse_args(argv)
    sheet_id = os.environ.get("FARAMIR_SHEET_ID", "")
    if not sheet_id:
        logger.error("FARAMIR_SHEET_ID is not set")
        return 2
    gc = sheet_module.get_sheet_client()
    table = read_table(gc, sheet_id)
    if table.header and table.header[: len(SUGGESTIONS_HEADERS)] != list(SUGGESTIONS_HEADERS) or \
            (table.schema == 1 and len(table.header) != len(SUGGESTIONS_HEADERS)):
        logger.error("Unexpected suggestions header layout; refusing: %s", table.header)
        return 1
    plan = plan_migration(table, force=args.repair)
    summary = {k: v for k, v in plan.items() if k != "assignments"}
    summary["sample"] = plan["assignments"][:5]
    if not args.apply:
        print(json.dumps(summary, indent=2))
        return 0
    if plan.get("already_migrated"):
        print(json.dumps({"applied": False, "reason": "already on schema v2"}, indent=2))
        return 0
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
            update_cells(gc, sheet_id, TAB, build_updates(table, plan), "migrate_suggestions_v2")
            problems = [] if ctx.dry_run else verify(gc, sheet_id, plan)
            ctx.report["migration"] = {**summary, "verification_problems": problems}
        finally:
            ctx.write_artifacts()
    if problems:
        logger.error("Verification failed (the tab changed during the write) — safe to re-run: %s", problems[:10])
        return 5
    print(json.dumps({"applied": not ctx.dry_run, **summary, "artifacts": ctx.out_dir}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
