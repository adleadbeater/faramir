"""One command: run Faramir on today's real box-office charts and print the editor cards.

    .venv/bin/python scripts/faramir_run.py              # cards without titles (no paid calls)
    .venv/bin/python scripts/faramir_run.py --claude     # also run the live Claude selection + titles

What it does, end to end:
  1. Saves the latest completed weekend chart into weekend history (if not saved yet).
  2. Runs the real daily pipeline (scripts/run_daily.py) on live data from The Numbers, Box Office
     Mojo and TMDB. Faramir's sheet data (film state, suggestions, history) lives in the local store
     (out/local_store/), so each run remembers the previous one. Its evidence ledger is kept there too.
  3. Slack is captured, never sent. Without --claude, the Claude call is captured and answered with
     no picks, so the cards show what Faramir ranked for the writer (no title yet).
  4. Writes out/cards/<date>/cards.md and cards.json and prints the cards.

Only the-numbers.com, boxofficemojo.com and api.themoviedb.org are reachable (and api.anthropic.com
with --claude). Any other request fails.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

import pytest
import requests

sys.path.insert(0, os.path.dirname(__file__))
import local_store  # noqa: E402

from faramir import cards as cards_mod, chart_watch, history  # noqa: E402
from faramir.tn_chart import daily_url  # noqa: E402
from faramir.weekend_collect import IdentityCache, collect_weekend  # noqa: E402
from tests.fakes.fake_anthropic import FakeAnthropic  # noqa: E402
from tests.fakes.fake_http import FakeHTTP  # noqa: E402
from tests.fakes.fake_slack import FakeSlack  # noqa: E402
from tests.fakes.state_book import init_book  # noqa: E402

ROOT = local_store.ROOT
REAL_HOSTS = {"www.the-numbers.com", "www.boxofficemojo.com", "api.themoviedb.org"}
# Cerebro snapshot and Power Words export: Faramir's own copies in the local store (scheduled jobs can't
# read Google Drive folders on macOS). Refresh by copying a newer sanitized cerebro_MW.csv there.
DATA_DIR = local_store.STORE_DIR
_REAL_GET, _REAL_POST = requests.get, requests.post


class Router(FakeHTTP):
    """Real HTTP for the approved data sources; Slack goes to the in-memory fake; everything else fails."""

    def _dispatch(self, method, url, **kwargs):
        if urlparse(url).hostname in REAL_HOSTS:
            kwargs = {k: v for k, v in kwargs.items() if v is not None}
            return (_REAL_GET if method == "GET" else _REAL_POST)(url, **kwargs)
        return super()._dispatch(method, url, **kwargs)


def _ensure_tabs(gc):
    book = gc.book(local_store.SHEET_ID)
    if "state" not in book._sheets:
        init_book(book, [])
        book.add_tab("suggestions")
    for tab, headers in ((history.HISTORY_TAB, history.HISTORY_HEADERS),
                         (history.COLLECTIONS_TAB, history.COLLECTION_HEADERS)):
        if tab not in book._sheets:
            book.add_tab(tab, [headers])
    return book


def _run_daily(gc, run_date: date, out_root: str, claude: bool, cold_start: bool, slots: int | None = None) -> dict:
    mp = pytest.MonkeyPatch()
    captured = {}
    try:
        env = {"FARAMIR_SHEET_ID": local_store.SHEET_ID, "FARAMIR_ENABLED": "true",
               "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/LOCAL/ONLY/X", "SLACK_BOT_TOKEN": "xoxb-local",
               "SLACK_CHANNEL_ID": "CLOCAL", "FARAMIR_COLLECT_HISTORY": "true",
               "FARAMIR_CORPUS_PATH": os.path.join(ROOT, "data", "corpus.v2.json"),
               "FARAMIR_LEDGER_DIR": os.path.join(local_store.STORE_DIR, "ledger"),
               "FARAMIR_COLD_START": "true" if cold_start else "false",
               # capture the ranked payload; cards and titles are then built by faramir.cards, as in production
               "FARAMIR_PICK_ENGINE": "claude_select"}
        if os.path.exists(os.path.join(DATA_DIR, "cerebro_MW.csv")):
            env["FARAMIR_CEREBRO_SNAPSHOT"] = os.path.join(DATA_DIR, "cerebro_MW.csv")
            env["FARAMIR_POWER_WORDS"] = os.path.join(DATA_DIR, "power_words.json")
        if not claude:
            env["ANTHROPIC_API_KEY"] = "local-placeholder"
        for k, v in env.items():
            mp.setenv(k, v)
        mp.delenv("CEREBRO_MW_SHEET_ID", raising=False)

        from faramir import config, sheet
        mp.setattr(sheet, "get_sheet_client", lambda: gc)
        if slots:   # review mode: let more ranked stories through to the writer than a normal day allows
            mp.setitem(config.PICKS_MAX_BY_DAY, run_date.weekday(), slots)
        http = Router().install(mp)
        FakeSlack().attach(http)

        if not claude:
            def capture(kwargs):
                captured["payload"] = json.loads(kwargs["messages"][0]["content"])
                return json.dumps({"picks": []})
            FakeAnthropic([capture]).install(mp)

        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(run_date.year, run_date.month, run_date.day)

        spec = importlib.util.spec_from_file_location("run_daily", os.path.join(ROOT, "scripts", "run_daily.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.date = FixedDate
        code = mod.main(["--out-root", out_root])
    finally:
        mp.undo()
    run_dir = max(glob.glob(os.path.join(out_root, "*/")), key=os.path.getmtime)
    return {"code": code, "run_dir": run_dir, "payload": captured.get("payload")}


def write_titles(cards: list[dict], report: dict, out_dir: str, live: bool, only: set | None = None,
                 previous: dict | None = None) -> str:
    """Titles with the local store's data: the synced Cerebro snapshot and Power Words copy."""
    from faramir.analytics import cerebro_snapshot as CS
    from faramir.titles import data as tdata
    from faramir.titles.credits import Credits

    snap_path = os.path.join(DATA_DIR, "cerebro_MW.csv")
    phrases, pw_source = tdata.load_power_words(None, os.path.join(DATA_DIR, "power_words_sheet.tsv"))
    client = None
    if live:
        import anthropic
        client = anthropic.Anthropic()
    return cards_mod.write_titles(
        cards, snap=CS.load(snap_path) if os.path.exists(snap_path) else None, phrases=phrases, pw_source=pw_source,
        corpus_films=json.load(open(os.path.join(ROOT, "data", "corpus.v2.json")))["films"],
        credits=Credits(os.path.join(DATA_DIR, "tmdb_details.json")),
        data_date=(report.get("source_dates") or {}).get("daily_chart"),
        weekend=(report.get("weekend_strategies") or {}).get("weekend_start"),
        client=client, briefs_dir=os.path.join(out_dir, "title_briefs"), only=only, previous=previous)


CHART_NOT_POSTED = 75   # EX_TEMPFAIL: the daily job retries


WATCH_FILE = os.path.join(local_store.STORE_DIR, "chart_watch.json")


def chart_films(day: date) -> list[tuple[str, int]]:
    return chart_watch.films_on_chart(day, get=_REAL_GET)


def chart_ready(day: date, partial_ok: bool = False, now: datetime | None = None) -> tuple[bool, str]:
    """Is this day's daily chart ready? Learned film by film (faramir/chart_watch.py), shared with production."""
    ready, why = chart_watch.check(day, WATCH_FILE, now, fetch=chart_films)
    return (ready or (partial_ok and not why.startswith("not posted"))), why


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="run date YYYY-MM-DD (default today; the chart used is the day before)")
    ap.add_argument("--claude", action="store_true", help="run the live Claude selection and titles (paid call)")
    ap.add_argument("--titles", action="store_true",
                    help="write titles with the Aragorn method (one paid Claude call per card)")
    ap.add_argument("--title-cards", default=None, help="with --titles: only these card numbers, e.g. 5 or 2,5")
    ap.add_argument("--require-chart", action="store_true",
                    help="stop (exit 75) if The Numbers hasn't posted yesterday's daily chart in full yet, instead of "
                         "falling back to an older or partial chart; the daily job retries later")
    ap.add_argument("--watch-only", action="store_true",
                    help="record one check of yesterday's chart for chart_watch's learning, and stop")
    ap.add_argument("--partial-ok", action="store_true",
                    help="with --require-chart: accept a partly posted chart (the daily job's last try)")
    ap.add_argument("--cards", type=int, default=8, help="maximum cards to show")
    ap.add_argument("--all", action="store_true", help="review mode: show every ranked story, not just today's slots")
    args = ap.parse_args(argv)
    run_date = date.fromisoformat(args.date) if args.date else date.today()
    if args.watch_only:
        print(f"Chart watch: {chart_ready(run_date - timedelta(days=1))[1]}")
        return 0
    if args.require_chart:
        ready, why = chart_ready(run_date - timedelta(days=1), args.partial_ok)
        if not ready:
            print(f"The Numbers' {run_date - timedelta(days=1)} daily chart isn't ready ({why}); try again later.")
            return CHART_NOT_POSTED
        if why.startswith("partial"):
            print(f"PARTIAL CHART: running ({why}).")
    names = ("TMDB_API_KEY", "ANTHROPIC_API_KEY") if (args.claude or args.titles) else ("TMDB_API_KEY",)
    local_store.load_env(names)
    if args.claude:
        REAL_HOSTS.add("api.anthropic.com")

    gc = local_store.load()
    _ensure_tabs(gc)
    out_root = os.path.join(local_store.STORE_DIR, "runs")

    # 1. latest completed weekend into history
    from faramir import runtime
    os.environ["FARAMIR_COLLECT_HISTORY"] = "true"
    fri = run_date - timedelta(days=(run_date.weekday() - 4) % 7)
    if run_date < fri + timedelta(days=3):
        fri -= timedelta(weeks=1)
    ctx = runtime.RunContext(mode=runtime.LIVE, enabled=True, run_id=f"weekend-{fri}",
                             out_dir=os.path.join(local_store.STORE_DIR, "runs", "weekend"))
    with runtime.use(ctx):
        res = collect_weekend(gc, local_store.SHEET_ID, fri, IdentityCache(
            os.path.join(local_store.STORE_DIR, "identity_cache.json")), ctx.run_id, os.path.join(local_store.STORE_DIR, "raw"))
    print(f"weekend {fri}: {res.status}")
    local_store.save(gc)

    # 2. first run ever: seed film state from the day before, so today has a baseline
    if not gc.book(local_store.SHEET_ID).worksheet("state").get_all_records():
        print("first run: seeding film state from the previous day's chart")
        _run_daily(gc, run_date - timedelta(days=1), out_root, claude=False, cold_start=True)
        local_store.save(gc)

    # 3. today's run. A re-run of the same date starts from the store as it was before that date's first
    #    run, so a second run never counts unchanged figures as another stale day.
    before = os.path.join(local_store.STORE_DIR, f"workbook.before-{run_date}.json")
    if os.path.exists(before):
        gc = local_store.load(before)
        print(f"re-run of {run_date}: starting from the store as it was before today's first run")
    else:
        local_store.save(gc, before)
    result = _run_daily(gc, run_date, out_root, args.claude, cold_start=False, slots=60 if args.all else None)
    local_store.save(gc)
    report = json.load(open(os.path.join(result["run_dir"], "report.json")))
    if report.get("halted_reason"):
        print("Faramir stopped:", report["halted_reason"])

    imdb = {int(r["tmdb_id"]): r.get("imdb_id") for r in gc.book(local_store.SHEET_ID).worksheet("state").get_all_records()
            if str(r.get("tmdb_id")).isdigit()}
    cards = cards_mod.build(result["payload"], report, imdb, args.cards)
    out_dir = os.path.join(ROOT, "out", "cards", run_date.isoformat())
    os.makedirs(out_dir, exist_ok=True)
    only = {int(x) for x in args.title_cards.split(",")} if args.title_cards else None
    previous = {}
    if os.path.exists(os.path.join(out_dir, "cards.json")):   # keep titles already written today
        previous = {(c["label"], tuple(c["numbers"])): c for c in json.load(open(os.path.join(out_dir, "cards.json")))}
    titles_report = write_titles(cards, report, out_dir, live=args.titles, only=only, previous=previous)
    md = cards_mod.render(cards, run_date, report)
    open(os.path.join(out_dir, "cards.md"), "w").write(md)
    json.dump([{k: v for k, v in c.items() if not k.startswith("_")} for c in cards],
              open(os.path.join(out_dir, "cards.json"), "w"), indent=1)
    if titles_report:
        print(titles_report)
    print(md)
    print(f"\nSaved to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
