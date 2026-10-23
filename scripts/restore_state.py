"""Manual state restore — the operator path after an ABORT that needs a human.

    python scripts/restore_state.py --from lkg                      # plan: read-only
    python scripts/restore_state.py --from-file out/<run>/state_snapshot.json
    FARAMIR_ENABLED=true python scripts/restore_state.py --from lkg --apply --confirm-sheet ID

The source must pass validation. Editor columns (tmdb_id_override, notes) are
taken from the live ``state`` tab by default; if that is ambiguous (conflicting
duplicates, or editor values on rows the source lacks) the restore is refused
unless ``--editor-values source`` is chosen explicitly. All writes go through
the central write guard and the state lock; the restored generation is marked
``restored`` so the next daily run proceeds normally.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from faramir import runtime  # noqa: E402
from faramir import sheet as sheet_module  # noqa: E402
from faramir.sheet import STATE_HEADERS, read_values, write_values  # noqa: E402
from faramir.state_integrity import (  # noqa: E402
    LKG_TAB, META_TAB, STATE_TAB, checksum, merge_editor_values, meta_values, parse_meta, row_problems, split,
)
from faramir.state_store import StateConflict, StateStore  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("faramir.restore_state")


def load_source(gc, sheet_id, source: str, path: str | None):
    if source == "file":
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
        return snap["header"], [[str(c) for c in r] for r in snap["rows"]]
    return split(read_values(gc, sheet_id, LKG_TAB))


def plan(gc, sheet_id, source, path, editor_values) -> dict:
    header, rows = load_source(gc, sheet_id, source, path)
    expected = list(STATE_HEADERS)
    problems = row_problems(header, rows, expected)
    live = read_values(gc, sheet_id, STATE_TAB)
    _, live_rows = split(live)
    if editor_values == "current" and not problems:
        merged, conflicts, preserved = merge_editor_values(expected, rows, live, strict=True)
    else:
        merged, conflicts, preserved = rows, [], 0
    tcol = expected.index("tmdb_id")
    live_ids = {r[tcol] for r in live_rows if len(r) > tcol}
    src_ids = {r[tcol] for r in rows}
    return {
        "source": source if source != "file" else path, "source_rows": len(rows), "source_problems": problems,
        "editor_values": editor_values, "editor_conflicts": conflicts, "editor_values_kept_from_live": preserved,
        "rows_only_in_live": sorted(live_ids - src_ids)[:20], "rows_only_in_source": sorted(src_ids - live_ids)[:20],
        "restored_checksum": checksum(expected, merged) if not problems else "",
        "_merged": merged,
    }


def apply(gc, sheet_id, run_id, p) -> int:
    if p["source_problems"]:
        raise SystemExit("source failed validation: " + "; ".join(p["source_problems"][:5]))
    if p["editor_conflicts"]:
        raise SystemExit("editor-value conflicts (use --editor-values source to override deliberately): "
                         + "; ".join(p["editor_conflicts"][:5]))
    store = StateStore(gc, sheet_id, run_id)
    store.acquire_lock()
    try:
        meta = parse_meta(read_values(gc, sheet_id, META_TAB))
        new_gen = int(meta["state"].get("generation") or 0) + 1
        merged = p["_merged"]
        digest = checksum(list(STATE_HEADERS), merged)
        meta["state"] = store._stamp(meta["state"], status="restoring", generation=new_gen, checksum=digest,
                                     row_count=len(merged))
        write_values(gc, sheet_id, META_TAB, meta_values(meta), "write_state_meta")
        write_values(gc, sheet_id, STATE_TAB, [list(STATE_HEADERS)] + merged, "manual_restore_state")
        if not runtime.current().dry_run:
            store._verify(STATE_TAB, digest, len(merged))
        meta["state"] = store._stamp(meta["state"], status="restored")
        write_values(gc, sheet_id, META_TAB, meta_values(meta), "write_state_meta")
        return new_gen
    finally:
        store.release_lock()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from", dest="source", choices=["lkg"])
    src.add_argument("--from-file", dest="path")
    ap.add_argument("--editor-values", choices=["current", "source"], default="current")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm-sheet", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-root", default=None)
    args = ap.parse_args(argv)
    source = "file" if args.path else "lkg"

    sheet_id = os.environ.get("FARAMIR_SHEET_ID", "")
    if not sheet_id:
        logger.error("FARAMIR_SHEET_ID is not set")
        return 2
    gc = sheet_module.get_sheet_client()
    p = plan(gc, sheet_id, source, args.path, args.editor_values)
    shown = {k: v for k, v in p.items() if not k.startswith("_")}
    if not args.apply:
        print(json.dumps(shown, indent=2))
        return 0 if not (p["source_problems"] or p["editor_conflicts"]) else 1
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
            gen = apply(gc, sheet_id, ctx.run_id, p)
            ctx.report["restore"] = {**shown, "generation": gen}
        except StateConflict as exc:
            logger.error("Not applied: %s", exc)
            return 5
        finally:
            ctx.write_artifacts()
    print(json.dumps({"applied": not ctx.dry_run, "generation": gen, "artifacts": ctx.out_dir, **shown}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
