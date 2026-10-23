"""Manual review and replay of undelivered picks. Read-only unless told otherwise.

    python scripts/replay_picks.py                                # list (read-only)
    python scripts/replay_picks.py --plan --id <suggestion_id>    # show what would be posted (read-only)
    FARAMIR_ENABLED=true python scripts/replay_picks.py --apply --id <sid> --confirm-channel <C…>
    FARAMIR_ENABLED=true python scripts/replay_picks.py --dismiss --id <sid> --reason "covered elsewhere"

--apply refuses unless: the pick is expired / not_delivered / failed; a COMPLETE
Slack history read succeeds and shows no message tagged with this id; the state
lock is free. The replayed message says "Delayed suggestion from <date>".
Every write goes through the central write guard (FARAMIR_ENABLED=true, or
--dry-run to record without sending).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from faramir import runtime  # noqa: E402
from faramir import sheet as sheet_module  # noqa: E402
from faramir.corpus import load_corpus_v2  # noqa: E402
from faramir.slack_history import fetch_tagged  # noqa: E402
from faramir.slack_post import post_pick  # noqa: E402
from faramir.state_integrity import STATE_TAB, records, split  # noqa: E402
from faramir.state_store import StateConflict, StateStore  # noqa: E402
from faramir.suggestions import read_table, update_by_id  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("faramir.replay_picks")

REVIEW_STATES = {"expired", "not_delivered", "failed", "unknown", "sending", "planned", "legacy_unknown"}
REPLAYABLE = {"expired", "not_delivered", "failed"}
ROOT = os.path.join(os.path.dirname(__file__), "..")


def _row_view(r) -> dict:
    keys = ("suggestion_id", "run_date", "active_title", "comp_title", "axis", "kind", "headline",
            "delivery_state", "delivery_attempts", "last_attempt_at", "data_date", "delivery_note", "slack_ts")
    return {k: r.get(k) for k in keys}


def _pick_context(gc, sheet_id, row):
    rec = row.record
    st_header, st_rows = split(sheet_module.read_values(gc, sheet_id, STATE_TAB))
    active = next((s for s in records(st_header, st_rows) if str(s.get("tmdb_id")) == str(rec.get("active_tmdb_id"))), {})
    comp = None
    comp_id = str(rec.get("comp_tmdb_id") or "")
    if comp_id and comp_id not in ("None", "0"):
        path = os.environ.get("FARAMIR_CORPUS_PATH") or os.path.join(ROOT, "data", "corpus.v2.json")
        load = load_corpus_v2(path, date.today())
        comp = next((f for f in load.films if str(f.get("tmdb_id")) == comp_id), None)
    return dict(rec), active, comp


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dismiss", action="store_true")
    ap.add_argument("--id", dest="sid", default=None)
    ap.add_argument("--reason", default="")
    ap.add_argument("--confirm-channel", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-root", default=None)
    args = ap.parse_args(argv)

    sheet_id = os.environ.get("FARAMIR_SHEET_ID", "")
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = os.environ.get("SLACK_CHANNEL_ID", "")
    if not sheet_id:
        logger.error("FARAMIR_SHEET_ID is not set")
        return 2
    gc = sheet_module.get_sheet_client()
    table = read_table(gc, sheet_id)

    if not (args.plan or args.apply or args.dismiss):
        rows = [_row_view(r) for r in table.rows if r.get("delivery_state") in REVIEW_STATES]
        print(json.dumps({"needs_review": rows, "count": len(rows)}, indent=2))
        return 0

    matches = table.by_id().get(args.sid or "", [])
    if len(matches) != 1:
        logger.error("suggestion_id %r is on %d rows", args.sid, len(matches))
        return 2
    row = matches[0]
    state = row.get("delivery_state")

    if args.plan:
        pick, active, comp = _pick_context(gc, sheet_id, row)
        preview = runtime.RunContext(mode=runtime.DRY_RUN, enabled=False, run_id="replay-plan", out_dir=os.devnull)
        with runtime.use(preview):
            post_pick(token, channel, pick, active, comp, suggestion_id=row.sid, delayed_from=str(row.get("run_date")))
        print(json.dumps({"row": _row_view(row), "replayable_state": state in REPLAYABLE,
                          "blocks": preview.slack_preview[-1]["blocks"]}, indent=2, ensure_ascii=False))
        return 0

    ctx = runtime.new_context(dry_run=args.dry_run, out_root=args.out_root)
    ok, why = ctx.may_run()
    if not ok:
        logger.error("Not applied: %s", why)
        return 4
    with runtime.use(ctx):
        store = StateStore(gc, sheet_id, ctx.run_id)
        try:
            store.acquire_lock()
        except StateConflict as exc:
            logger.error("Not applied: %s", exc)
            return 5
        try:
            if args.dismiss:
                if state == "posted":
                    logger.error("Refusing to dismiss a posted pick")
                    return 6
                update_by_id(gc, sheet_id, {row.sid: {"delivery_state": "dismissed",
                                                      "delivery_note": f"dismissed by operator: {args.reason}"}},
                             "dismiss_delivery")
                print(json.dumps({"dismissed": row.sid, "applied": not ctx.dry_run}, indent=2))
                return 0
            if state not in REPLAYABLE:
                logger.error("Refusing: state %r is not replayable (%s)", state, sorted(REPLAYABLE))
                return 6
            if args.confirm_channel != channel or not token:
                logger.error("--apply requires --confirm-channel matching SLACK_CHANNEL_ID and a bot token")
                return 2
            oldest = datetime.combine(date.fromisoformat(str(row.get("run_date"))) - timedelta(days=1),
                                      datetime.min.time(), tzinfo=timezone.utc).timestamp()
            hist = fetch_tagged(token, channel, oldest)
            if not hist.ok:
                logger.error("Refusing: Slack history unavailable (%s) — cannot rule out an earlier post", hist.error)
                return 7
            if hist.picks.get(row.sid):
                update_by_id(gc, sheet_id, {row.sid: {"delivery_state": "posted", "slack_ts": sorted(hist.picks[row.sid])[0],
                                                      "delivery_note": "found in Slack history during manual replay"}},
                             "reconcile_delivery")
                logger.error("Refusing: already in Slack (%s); marked posted", hist.picks[row.sid])
                return 8
            pick, active, comp = _pick_context(gc, sheet_id, row)
            attempts = int(row.get("delivery_attempts") or 0) + 1
            update_by_id(gc, sheet_id, {row.sid: {"delivery_state": "sending", "delivery_attempts": str(attempts),
                                                  "last_attempt_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                  "delivery_run_id": ctx.run_id}}, "mark_sending")
            res = post_pick(token, channel, pick, active, comp, suggestion_id=row.sid, run_id=ctx.run_id,
                            delayed_from=str(row.get("run_date")))
            new_state = "posted" if res.status in ("posted", "dry_run") else res.status
            update_by_id(gc, sheet_id, {row.sid: {"delivery_state": new_state, "slack_ts": res.ts or "",
                                                  "delivery_note": f"manual replay: {res.error or 'ok'}"}},
                         "confirm_delivery")
            print(json.dumps({"replayed": row.sid, "state": new_state, "ts": res.ts, "applied": not ctx.dry_run}, indent=2))
            return 0 if new_state == "posted" else 9
        finally:
            store.release_lock()
            ctx.write_artifacts()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
