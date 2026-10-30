"""Save the last N completed weekend charts from The Numbers into weekend history (approved 2026-10-28).

    .venv/bin/python scripts/backfill_weekends.py --weeks 8

Writes to the local store (scripts/local_store.py) until deployment. Each weekend is committed
through faramir.history (append-only, hash-verified); a weekend already committed is skipped.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import local_store  # noqa: E402

from faramir import history, runtime  # noqa: E402
from faramir.tn_chart import weekend_friday  # noqa: E402
from faramir.weekend_collect import IdentityCache, collect_weekend  # noqa: E402


def last_completed_friday(today: date) -> date:
    fri = weekend_friday(today)
    return fri if today >= fri + timedelta(days=3) else fri - timedelta(weeks=1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=8)
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (default: today)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
    local_store.load_env()
    os.environ["FARAMIR_COLLECT_HISTORY"] = "true"   # collection approved by Rob 2026-10-28 (local store)
    today = date.fromisoformat(args.today) if args.today else date.today()
    latest = last_completed_friday(today)
    fridays = [latest - timedelta(weeks=i) for i in range(args.weeks)][::-1]

    gc = local_store.load()
    book = gc.book(local_store.SHEET_ID)
    for tab, headers in ((history.HISTORY_TAB, history.HISTORY_HEADERS), (history.COLLECTIONS_TAB, history.COLLECTION_HEADERS)):
        if tab not in book._sheets:
            book.add_tab(tab, [headers])
    cache = IdentityCache(os.path.join(local_store.STORE_DIR, "identity_cache.json"))
    raw = os.path.join(local_store.STORE_DIR, "raw")
    # Writes go only to the local store (a FakeClient); this context authorizes them.
    ctx = runtime.RunContext(mode=runtime.LIVE, enabled=True, run_id=f"backfill-{today}",
                             out_dir=os.path.join(local_store.STORE_DIR, "runs"))
    with runtime.use(ctx):
        for fri in fridays:
            result = collect_weekend(gc, local_store.SHEET_ID, fri, cache, ctx.run_id, raw)
            print(f"{fri}  {result.status:<10} rows written {result.rows_written}  {result.detail or ''}")
            local_store.save(gc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
