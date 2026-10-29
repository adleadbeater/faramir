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
from datetime import date, timedelta
from urllib.parse import urlparse

import pytest
import requests

sys.path.insert(0, os.path.dirname(__file__))
import local_store  # noqa: E402

from faramir import history  # noqa: E402
from faramir.strategies.common import money  # noqa: E402
from faramir.tn_chart import daily_url, weekend_url  # noqa: E402
from faramir.weekend_collect import IdentityCache, collect_weekend  # noqa: E402
from tests.fakes.fake_anthropic import FakeAnthropic  # noqa: E402
from tests.fakes.fake_http import FakeHTTP  # noqa: E402
from tests.fakes.fake_slack import FakeSlack  # noqa: E402
from tests.fakes.state_book import init_book  # noqa: E402

ROOT = local_store.ROOT
REAL_HOSTS = {"www.the-numbers.com", "www.boxofficemojo.com", "api.themoviedb.org"}
ARWEN = os.path.join(os.path.dirname(os.path.realpath(os.path.expanduser(
    "~/Library/CloudStorage/GoogleDrive-robert.s@valnetinc.com/.shortcut-targets-by-id/"
    "13KvU_4c5Z98LjjEbBqcdzx5xaVcLCly-/claude-space-robert-sperduto/arwen/data/cerebro_MW.csv"))))
LABEL = {"overtake": "Overtake", "chase": "Chase", "countdown": "Countdown", "takeover": "Takeover", "hold": "Hold",
         "streak": "Streak", "legs": "Legs", "exit": "Exit", "dark_horse": "Dark Horse", "discovery": "Discovery",
         "star_record": "Star Record", "global_split": "Global Split", "studio_battle": "Studio Battle",
         "premium_run": "Premium Run"}
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
               "FARAMIR_COLD_START": "true" if cold_start else "false"}
        if os.path.exists(os.path.join(ARWEN, "cerebro_MW.csv")):
            env["FARAMIR_CEREBRO_SNAPSHOT"] = os.path.join(ARWEN, "cerebro_MW.csv")
            env["FARAMIR_POWER_WORDS"] = os.path.join(ARWEN, "power_words.json")
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


# ── cards ───────────────────────────────────────────────────────────────

def _context(rationale: str) -> str:
    parts = [p.strip() for p in (rationale or "").split(" · ")]
    body = [p for p in parts[1:] if not p.startswith("sources:") and not p.startswith("also ")]
    return " · ".join(body)


def _numbers(film: dict, c: dict) -> list[str]:
    lines = []
    if c["kind"] != "event":
        lines.append(f"{film['title']}: {money(film['domestic_today'])} domestic · "
                     f"{money(film['worldwide_today']) if film.get('worldwide_today') else 'worldwide n/a'} worldwide")
        comp = c.get("comp")
        if comp:
            dom_l, ww_l = comp.get("domestic_lifetime"), comp.get("worldwide_lifetime")
            lines.append(f"{comp['title']} ({comp.get('release_year')}): "
                         f"{money(dom_l) if dom_l else 'n/a'} domestic · {money(ww_l) if ww_l else 'n/a'} worldwide (lifetime)")
        elif c.get("threshold_value"):
            lines.append(f"Milestone: {money(c['threshold_value'])} {c['axis']}")
        return lines
    v = c.get("event_values") or {}
    keys = [("weekend_gross", "weekend gross"), ("previous_weekend_gross", "previous weekend"), ("rank", "rank"),
            ("theaters", "theaters"),
            ("previous_leader", "previous No. 1"), ("rival", "rival"), ("rival_weekend_gross", "rival weekend gross"),
            ("rival_theaters", "rival theaters"), ("previous_theaters", "previous theaters"), ("drop_pct", "theater drop %"),
            ("total_gross", "total"), ("weekends", "consecutive weekends"), ("weekend_change_pct", "weekend change %"),
            ("theater_avg", "per-theater average")]
    shown = []
    for k, label in keys:
        if v.get(k) is not None:
            val = v[k]
            if k in ("weekend_gross", "previous_weekend_gross", "rival_weekend_gross", "total_gross"):
                val = money(val)
            elif k == "theater_avg":
                val = f"${val:,.0f}"
            elif isinstance(val, (int, float)) and k in ("theaters", "rival_theaters", "previous_theaters"):
                val = f"{val:,.0f}"
            shown.append(f"{label} {val}")
    lines.append(f"{film['title']}: " + " · ".join(shown))
    return lines


def _sources(film: dict, c: dict, data_date: str, weekend: str, imdb: dict) -> list[str]:
    if c["kind"] == "event":
        return [weekend_url(date.fromisoformat(weekend))]
    out = [daily_url(date.fromisoformat(data_date))]
    if c["axis"] == "worldwide" and imdb.get(film["tmdb_id"]):
        out.append(f"https://www.boxofficemojo.com/title/{imdb[film['tmdb_id']]}/")
    return out


def build_cards(payload: dict | None, report: dict, picks: list[dict], imdb: dict, limit: int) -> list[dict]:
    data_date = (report.get("source_dates") or {}).get("daily_chart") or report.get("run_date")
    weekend = (report.get("weekend_strategies") or {}).get("weekend_start")
    cards = []
    if picks:   # --claude: the validated picks
        for p in picks:
            cards.append({"label": LABEL.get(p.get("strategy") or "", p.get("strategy") or ""), "title": p["headline"],
                          "numbers": p.get("numbers", []), "context": p.get("angle", ""), "sources": p.get("sources", [])})
        return cards
    if not payload:
        return cards
    ranked = sorted(((f, c) for f in payload["active_films"] for c in f["candidates"] if c.get("rank")),
                    key=lambda fc: fc[1]["rank"])
    per_film: dict = {}
    seen = set()
    for film, c in ranked:
        key = (film["tmdb_id"], c.get("strategy"))
        if key in seen or per_film.get(film["tmdb_id"], 0) >= 2:
            continue   # one card per film × strategy, at most two per film (the writer's cap)
        seen.add(key)
        per_film[film["tmdb_id"]] = per_film.get(film["tmdb_id"], 0) + 1
        label = LABEL.get(c.get("strategy"), c.get("strategy"))
        if c.get("secondary_strategy"):
            label += f" (+{LABEL.get(c['secondary_strategy'], c['secondary_strategy'])})"
        cards.append({"rank": c["rank"], "label": label, "title": None, "numbers": _numbers(film, c),
                      "context": _context(c.get("rationale")), "sources": _sources(film, c, data_date, weekend, imdb),
                      "exploration": bool(c.get("exploration")), "_film": film, "_cand": c})
        if len(cards) >= limit:
            break
    return cards


def _credits(tmdb_id, cache_path: str) -> dict:
    """Genres, director, top cast and franchise from TMDB (cached; free API)."""
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    key = str(tmdb_id)
    if key not in cache and tmdb_id and int(tmdb_id) > 0:
        try:
            from faramir.tmdb import get_movie_details
            d = get_movie_details(int(tmdb_id))
            cache[key] = {"genres": d.get("genres") or [], "director": d.get("director") or "",
                          "cast": (d.get("lead_cast") or [])[:3], "collection": d.get("collection_name") or ""}
        except Exception:  # noqa: BLE001
            cache[key] = {}
        json.dump(cache, open(cache_path, "w"), indent=1)
    return cache.get(key, {})


def _first_reason(logic: str) -> str:
    """The kill reasoning's first substantive sentence, for a one-line card note."""
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", logic or "") if x.strip()]
    sentences = [x for x in sentences if not x.lower().startswith("all three kill")] or sentences
    return sentences[0] if sentences else "too soft to run"


def write_titles(cards: list[dict], gc, report: dict, out_dir: str, live: bool, only: set | None = None,
                 previous: dict | None = None) -> str:
    """Build each card's title brief; with live=True, write titles with the Aragorn method."""
    from faramir.analytics import cerebro_snapshot as CS
    from faramir.titles import aragorn, brief as brief_mod, data as tdata

    snap_path = os.path.join(ARWEN, "cerebro_MW.csv")
    if not cards or not os.path.exists(snap_path):
        return "titles: no Cerebro snapshot available" if cards else ""
    snap = CS.load(snap_path)
    pool = tdata.desk_pool(snap)
    baseline = round(sum(a.sessions or 0 for a in pool) / len(pool)) if pool else 0
    phrases, pw_source = tdata.load_power_words(None, os.path.join(local_store.STORE_DIR, "power_words_sheet.tsv"))
    tdata.measure_phrases(phrases, pool)
    corpus = json.load(open(os.path.join(ROOT, "data", "corpus.v2.json")))["films"]
    others = sorted({f["title"] for f in corpus if " " in f["title"]}
                    | {c["_film"]["title"] for c in cards}, key=len, reverse=True)
    data_date = (report.get("source_dates") or {}).get("daily_chart")
    weekend = (report.get("weekend_strategies") or {}).get("weekend_start")
    briefs_dir = os.path.join(out_dir, "title_briefs")
    import shutil
    shutil.rmtree(briefs_dir, ignore_errors=True)   # briefs are rebuilt every run; never leave stale ones
    os.makedirs(briefs_dir, exist_ok=True)
    client = None
    if live:
        import anthropic
        client = anthropic.Anthropic()
    tokens = {"input_tokens": 0, "output_tokens": 0}
    for i, c in enumerate(cards, 1):
        film, cand = c["_film"], c["_cand"]
        credits = _credits(film.get("tmdb_id"), os.path.join(local_store.STORE_DIR, "tmdb_details.json"))
        b = brief_mod.build(film, cand, credits, data_date, weekend, c["sources"], pool, baseline, phrases)
        with open(os.path.join(briefs_dir, f"{i:02d}_{cand.get('strategy')}.txt"), "w") as f:
            f.write(b.user_message())
        prior = (previous or {}).get((c["label"], tuple(c["numbers"])))
        already = bool(prior and prior.get("title_verdict") and prior.get("title_verdict") != "error")
        if not live or (only and i not in only) or (not only and already):   # never pay twice for the same card
            if prior and prior.get("title_verdict"):
                c.update({k: prior[k] for k in ("title", "alternative", "title_verdict", "title_logic",
                                                "title_options", "title_note") if k in prior})
            else:
                c["title_note"] = "brief ready (run with --titles to write it)"
            continue
        t = aragorn.write(b, client, [o for o in others if o not in b.names])
        c.update({"title": t.title, "alternative": t.alternative, "title_verdict": t.verdict,
                  "title_logic": t.note, "title_options": t.options})
        if t.verdict == "kill":
            c["title_note"] = "Pass: " + _first_reason(t.note)
        elif t.verdict == "over_length":
            c["title_note"] = t.note
        elif not t.title:
            c["title_note"] = f"no title passed the fact check ({t.verdict})"
        for k in tokens:
            tokens[k] += t.usage.get(k, 0)
    cost = tokens["input_tokens"] * 3 / 1e6 + tokens["output_tokens"] * 15 / 1e6
    return (f"titles: {pw_source}; Cerebro News+Flash baseline S/A {baseline:,} ({len(pool):,} articles, "
            f"as of {snap.as_of}); briefs in {briefs_dir}"
            + (f"; {tokens['input_tokens']:,} in / {tokens['output_tokens']:,} out tokens ≈ ${cost:.2f}" if live else ""))


def render(cards: list[dict], run_date: date, report: dict) -> str:
    data_date = (report.get("source_dates") or {}).get("daily_chart")
    out = [f"# Faramir — {run_date:%A, %B %-d, %Y}", f"Daily chart {data_date} · weekend "
           f"{(report.get('weekend_strategies') or {}).get('weekend_start')}", ""]
    if not cards:
        out.append("_No story candidates today._")
    for i, c in enumerate(cards, 1):
        out.append(f"**{i}. {c['label'].upper()}**" + ("  _(test slot)_" if c.get("exploration") else ""))
        if c["title"]:
            out.append(f"**{c['title']}**" + (f"  _({c['title_note']})_" if c.get("title_verdict") == "over_length" else ""))
            if c.get("alternative"):
                out.append(f"_Alternative: {c['alternative']}_")
        else:
            out.append(f"_Title: {c.get('title_note') or 'not written (run with --titles)'}_")
        out += [f"- {n}" for n in c["numbers"]]
        if c["context"]:
            out.append(c["context"])
        out.append("Sources: " + " · ".join(c["sources"]))
        out.append("")
    return "\n".join(out)


CHART_NOT_POSTED = 75   # EX_TEMPFAIL: the daily job retries


def chart_posted(day: date) -> bool:
    """Has The Numbers published this day's daily chart (a parsed chart with rows)?"""
    from faramir.tn_chart import parse_chart
    from faramir.weekend_collect import USER_AGENT
    try:
        resp = _REAL_GET(daily_url(day), headers={"User-Agent": USER_AGENT}, timeout=30)
        return resp.ok and bool(parse_chart(resp.text, daily_url(day), "daily", day).rows)
    except Exception:  # noqa: BLE001 — treat as not posted; the job retries
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="run date YYYY-MM-DD (default today; the chart used is the day before)")
    ap.add_argument("--claude", action="store_true", help="run the live Claude selection and titles (paid call)")
    ap.add_argument("--titles", action="store_true",
                    help="write titles with the Aragorn method (one paid Claude call per card)")
    ap.add_argument("--title-cards", default=None, help="with --titles: only these card numbers, e.g. 5 or 2,5")
    ap.add_argument("--require-chart", action="store_true",
                    help="stop (exit 75) if The Numbers hasn't posted yesterday's daily chart yet, instead of "
                         "falling back to an older chart; the daily job retries later")
    ap.add_argument("--cards", type=int, default=8, help="maximum cards to show")
    ap.add_argument("--all", action="store_true", help="review mode: show every ranked story, not just today's slots")
    args = ap.parse_args(argv)
    run_date = date.fromisoformat(args.date) if args.date else date.today()
    if args.require_chart and not chart_posted(run_date - timedelta(days=1)):
        print(f"The Numbers hasn't posted the {run_date - timedelta(days=1)} daily chart yet; try again later.")
        return CHART_NOT_POSTED
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
    picks = []   # --claude: validated picks become cards in step 3 of the build (titles)
    cards = build_cards(result["payload"], report, picks, imdb, args.cards)
    out_dir = os.path.join(ROOT, "out", "cards", run_date.isoformat())
    os.makedirs(out_dir, exist_ok=True)
    only = {int(x) for x in args.title_cards.split(",")} if args.title_cards else None
    previous = {}
    if os.path.exists(os.path.join(out_dir, "cards.json")):   # keep titles already written today
        previous = {(c["label"], tuple(c["numbers"])): c for c in json.load(open(os.path.join(out_dir, "cards.json")))}
    titles_report = write_titles(cards, gc, report, out_dir, live=args.titles, only=only, previous=previous)
    md = render(cards, run_date, report)
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
