"""Adopt the existing production ``state`` tab into the integrity system.

Separately reviewable, one-time procedure (Phase 0 deployment stage S5). The
daily run never creates ``state_meta`` or ``state_lkg``; only this script does,
and only with ``--apply``.

    python scripts/init_state_integrity.py                 # plan: read-only
    python scripts/init_state_integrity.py --dry-run --apply --confirm-sheet ID
    FARAMIR_ENABLED=true python scripts/init_state_integrity.py --apply --confirm-sheet ID

Apply order (a crash at any point is safe to re-run):
  1. validate the live ``state`` tab (refuse if invalid);
  2. create (or, after a partial run, overwrite) ``state_lkg`` with an exact
     copy of ``state``, editor columns included; read back and verify;
  3. create ``state_meta`` LAST — until it exists the daily run refuses to
     start ("not initialized"), so a half-finished init is never used.
The ``state`` tab itself is never written.

Rollback (``--rollback-plan`` prints this): set FARAMIR_ENABLED=false; verify
``state`` independently; archive (rename) ``state_meta``; keep ``state_lkg``
(archived, not deleted) until state is verified. Nothing is deleted
automatically. ``state`` is untouched by initialization.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from faramir import runtime  # noqa: E402
from faramir import sheet as sheet_module  # noqa: E402
from faramir.sheet import STATE_HEADERS, TabMissing, create_tab, read_values, write_values  # noqa: E402
from faramir.state_integrity import (  # noqa: E402
    LKG_TAB, META_TAB, STATE_TAB, checksum, initial_meta, meta_values, row_problems, split,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("faramir.init_state_integrity")

ROLLBACK = [
    "1. Set the repository variable FARAMIR_ENABLED=false (stops v2 runs).",
    "2. Independently verify the 'state' tab (row count and checksum against the plan output) before changing anything else.",
    "3. Remove only 'state_meta' (rename it to 'state_meta_archived_<date>'); the daily run then reports not-initialized.",
    "4. Keep 'state_lkg' (rename it to 'state_lkg_archived_<date>') until state has been verified; never delete recovery records automatically.",
    "5. The 'state' tab was never written by initialization; pre-v2 code ignores the archived tabs.",
]


def _optional(gc, sheet_id, tab):
    try:
        return read_values(gc, sheet_id, tab)
    except TabMissing:
        return None


def plan(gc, sheet_id: str) -> dict:
    state = read_values(gc, sheet_id, STATE_TAB)
    meta = _optional(gc, sheet_id, META_TAB)
    lkg = _optional(gc, sheet_id, LKG_TAB)
    header, rows = split(state)
    problems = row_problems(header, rows, list(STATE_HEADERS)) if state else ["state tab is empty"]
    if meta is not None:
        mode = "already_initialized"
    elif lkg is not None:
        mode = "partial_previous_run"
    else:
        mode = "fresh"
    ops = []
    if mode != "already_initialized" and not problems:
        ops.append(("overwrite" if lkg is not None else "create") + f" tab {LKG_TAB}: exact copy of {STATE_TAB} ({len(rows)} rows)")
        ops.append(f"create tab {META_TAB} LAST: state/lkg generation 1, checksum {checksum(header, rows)[:12]}…, lock released")
    return {"sheet_id": sheet_id, "mode": mode, "state_rows": len(rows), "state_problems": problems,
            "state_checksum": checksum(header, rows) if header else "", "operations": ops,
            "state_tab_written": False, "rollback": ROLLBACK}


def apply(gc, sheet_id: str, run_id: str) -> dict:
    p = plan(gc, sheet_id)
    if p["mode"] == "already_initialized":
        raise SystemExit("state_meta already exists — nothing to do (refusing to re-initialize)")
    if p["state_problems"]:
        raise SystemExit("state tab failed validation; fix it first: " + "; ".join(p["state_problems"][:5]))
    state = read_values(gc, sheet_id, STATE_TAB)
    header, rows = split(state)
    values = [header] + rows
    if p["mode"] == "partial_previous_run":
        write_values(gc, sheet_id, LKG_TAB, values, "init_state_lkg")
    else:
        create_tab(gc, sheet_id, LKG_TAB, values)
    ctx = runtime.current()
    if not ctx.dry_run:
        lh, lrows = split(read_values(gc, sheet_id, LKG_TAB))
        if (lh, lrows) != (header, rows):
            raise SystemExit("state_lkg read-back does not match state — state_meta NOT created; safe to re-run")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    create_tab(gc, sheet_id, META_TAB, meta_values(initial_meta(values, now, run_id)))
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
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
        return 0 if not p["state_problems"] else 1

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
            ctx.report["init_plan"] = result
        finally:
            ctx.write_artifacts()
    print(json.dumps({"applied": not ctx.dry_run, "artifacts": ctx.out_dir, **result}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
