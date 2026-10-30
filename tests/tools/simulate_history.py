"""Run the weekend-history protocol through crash and revision scenarios on
in-memory Sheets and write the results for review. No network, no real Sheets.

    python tests/tools/simulate_history.py <out_dir>
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]

from faramir import history as H  # noqa: E402
from faramir import runtime  # noqa: E402
from faramir.tn_chart import parse_chart, weekend_url  # noqa: E402
from tests.fakes.fake_gspread import FakeClient  # noqa: E402
from tests.fixtures.tn_synth import WEEKEND_FILMS, page  # noqa: E402

FRI = date(2026, 10, 16)
T0 = datetime(2026, 10, 18, 14, 0, tzinfo=timezone.utc)


class Crash(Exception):
    pass


def chart(films=None, **kw):
    return parse_chart(page("weekend", FRI, films=films, **kw), weekend_url(FRI), "weekend", FRI)


def revised():
    films = [list(f) for f in WEEKEND_FILMS]
    films[1][7] = 59_400_000
    return chart([tuple(f) for f in films])


def book():
    gc = FakeClient()
    gc.book("S").add_tab(H.HISTORY_TAB, [H.HISTORY_HEADERS])
    gc.book("S").add_tab(H.COLLECTIONS_TAB, [H.COLLECTION_HEADERS])
    return gc


def run(gc, c, run_id, at):
    try:
        r = H.collect(gc, "S", c, run_id=run_id, retrieved_at=at, now=at)
        return {"run": run_id, "status": r.status, "revision": r.revision, "rows_written": r.rows_written,
                "orphans_aborted": len(r.orphans_aborted), "detail": r.detail}
    except Crash:
        return {"run": run_id, "status": "CRASHED (simulated)"}


def reader(gc):
    s = H.read_weekend(gc, "S", "2026-10-16")
    return {"status": s.status, "revision": s.revision, "rows": len(s.rows), "collection_status": s.collection_status,
            "observations": len(s.observations), "superseded_revisions": [x["revision"] for x in s.superseded]}


def physical(gc):
    ws = gc.book("S").worksheet(H.HISTORY_TAB)
    ev = gc.book("S").worksheet(H.COLLECTIONS_TAB)
    return {"history_rows": len(ws.rows) - 1, "events": [r[4] for r in ev.rows[1:]]}


def scenario(name, steps):
    gc = book()
    log = []
    for label, fn in steps:
        result = fn(gc)
        log.append({"step": label, "result": result, "reader_after": reader(gc), "tabs_after": physical(gc)})
    return {"scenario": name, "steps": log}, gc


def crash_on(tab, partial, after=0):
    def arm(gc):
        gc.book("S").worksheet(tab).fail_on("append_rows", Crash(), after=after, partial=partial)
        return "armed"
    return arm


def main(out_dir: str) -> None:
    os.environ["FARAMIR_COLLECT_HISTORY"] = "true"
    os.makedirs(out_dir, exist_ok=True)
    N = len(WEEKEND_FILMS)
    h = timedelta(hours=1)
    scenarios = [
        ("H1 normal collection", [("collect", lambda gc: run(gc, chart(), "run-1", T0))]),
        ("H2 identical content twice", [("collect", lambda gc: run(gc, chart(), "run-1", T0)),
                                        ("collect again", lambda gc: run(gc, chart(), "run-2", T0 + 6 * h))]),
        ("H3 crash after begin, before rows", [("arm", crash_on(H.HISTORY_TAB, 0)),
                                               ("collect", lambda gc: run(gc, chart(), "run-1", T0)),
                                               ("next run", lambda gc: run(gc, chart(), "run-2", T0 + h))]),
        ("H4 crash after 3 of 8 rows", [("arm", crash_on(H.HISTORY_TAB, 3)),
                                        ("collect", lambda gc: run(gc, chart(), "run-1", T0)),
                                        ("next run", lambda gc: run(gc, chart(), "run-2", T0 + h))]),
        ("H5 crash after rows, before commit", [("arm", crash_on(H.COLLECTIONS_TAB, 0, after=1)),
                                                ("collect", lambda gc: run(gc, chart(), "run-1", T0)),
                                                ("next run", lambda gc: run(gc, chart(), "run-2", T0 + h))]),
        ("H6 append acknowledgement lost", [("arm", crash_on(H.HISTORY_TAB, N)),
                                            ("collect", lambda gc: run(gc, chart(), "run-1", T0)),
                                            ("next run", lambda gc: run(gc, chart(), "run-2", T0 + h))]),
        ("H7 revised figures", [("collect", lambda gc: run(gc, chart(), "run-1", T0)),
                                ("collect revised", lambda gc: run(gc, revised(), "run-2", T0 + 24 * h))]),
        ("H8 crashed revision, source reverts, orphan aborted", [
            ("collect", lambda gc: run(gc, chart(), "run-1", T0)),
            ("arm", crash_on(H.HISTORY_TAB, 2)),
            ("collect revised", lambda gc: run(gc, revised(), "run-2", T0 + 12 * h)),
            ("source back to original", lambda gc: run(gc, chart(), "run-3", T0 + 20 * h)),
            ("70 hours later", lambda gc: run(gc, chart(), "run-4", T0 + 70 * h))]),
        ("H9 malformed chart after a good one", [("collect", lambda gc: run(gc, chart(), "run-1", T0)),
                                                 ("collect malformed", lambda gc: run(gc, chart(footer_total=1), "run-2", T0 + h))]),
        ("Confirmation by recollection", [("Sunday", lambda gc: run(gc, chart(), "run-1", T0)),
                                          ("Tuesday", lambda gc: run(gc, chart(), "run-2", T0 + 49 * h))]),
    ]
    summary = []
    ctx = runtime.RunContext(mode=runtime.LIVE, enabled=True, run_id="simulation", out_dir=out_dir)
    with runtime.use(ctx):  # writes reach only the in-memory FakeClient
        for name, steps in scenarios:
            result, gc = scenario(name, steps)
            summary.append(result)
            if name == "H8 crashed revision, source reverts, orphan aborted":
                for t in (H.HISTORY_TAB, H.COLLECTIONS_TAB):
                    with open(os.path.join(out_dir, f"H8_{t}.json"), "w") as f:
                        json.dump(gc.book("S").worksheet(t).rows, f, indent=0)
            if name == "H1 normal collection":
                snap = H.read_weekend(gc, "S", "2026-10-16")
                with open(os.path.join(out_dir, "H1_current_snapshot_rows.json"), "w") as f:
                    json.dump(snap.rows, f, indent=1)
    with open(os.path.join(out_dir, "scenarios.json"), "w") as f:
        json.dump(summary, f, indent=1)
    external = [o for o in ctx.operations if o["category"] != "sheets"]
    print(json.dumps({"scenarios": len(summary), "guarded_sheet_ops": len(ctx.operations), "non_sheet_ops": external}))


if __name__ == "__main__":
    main(sys.argv[1])
