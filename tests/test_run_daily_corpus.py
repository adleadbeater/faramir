"""Increment 2: the daily pipeline end to end on fakes, focused on corpus
health. No real Sheets, Slack, TMDB, BOM, The Numbers or Anthropic calls."""

import importlib.util
import json
import os
from datetime import date

import pytest

from tests.fakes.fake_anthropic import FakeAnthropic
from tests.fakes.fake_gspread import FakeClient
from tests.fakes.fake_http import FakeHTTP, FakeResponse
from tests.fakes.fake_slack import FakeSlack
from tests.fakes.state_book import init_book, state_row
from faramir.offline import event_claim

ROOT = os.path.join(os.path.dirname(__file__), "..")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
V2_PATH = os.path.join(ROOT, "data", "corpus.v2.json")
WEBHOOK = "https://hooks.slack.com/services/T/B/X"
TODAY = date(2026, 10, 22)

STATE_HEADERS = None  # filled from faramir.sheet at runtime


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(TODAY.year, TODAY.month, TODAY.day)


def load_run_daily():
    spec = importlib.util.spec_from_file_location("run_daily", os.path.join(ROOT, "scripts", "run_daily.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.date = FixedDate
    return mod


BOM_TITLE_HTML = "<html><body><div>Domestic</div><div>$152,000,000</div><div>International</div><div>$170,000,000</div><div>Worldwide</div><div>$322,000,000</div></body></html>"
TMDB_DETAILS = {
    "imdb_id": "tt5001001", "title": "Beta Story", "release_date": "2026-09-18", "genres": [{"name": "Action"}],
    "credits": {"crew": [{"job": "Director", "name": "Dana Director"}], "cast": [{"name": "Lead One"}]},
    "belongs_to_collection": None, "production_companies": [{"name": "Studio"}], "origin_country": ["US"],
}


@pytest.fixture
def env(monkeypatch, tmp_path):
    from faramir import sheet

    gc = FakeClient()
    book = gc.book("S")
    init_book(book, [state_row(5001, "Beta Story", imdb_id="tt5001001")])
    book.add_tab("suggestions")
    monkeypatch.setattr(sheet, "get_sheet_client", lambda: gc)

    http = FakeHTTP().install(monkeypatch)
    slack = FakeSlack().attach(http)
    with open(os.path.join(FIXTURES, "tn_daily_synthetic.html"), encoding="utf-8") as f:
        tn_html = f.read()
    http.route("GET", "https://www.the-numbers.com/box-office-chart/daily/", FakeResponse(200, text=tn_html))
    http.route("GET", "https://www.boxofficemojo.com/title/", FakeResponse(200, text=BOM_TITLE_HTML))
    http.route("GET", "https://api.themoviedb.org/3/search/movie", FakeResponse(200, {"results": []}))
    http.route("GET", "https://api.themoviedb.org/3/movie/5001", FakeResponse(200, TMDB_DETAILS))

    for k, v in {
        "FARAMIR_SHEET_ID": "S", "SLACK_WEBHOOK_URL": WEBHOOK, "SLACK_BOT_TOKEN": "xoxb-test",
        "SLACK_CHANNEL_ID": "C1", "TMDB_API_KEY": "test-key", "FARAMIR_ENABLED": "true",
        "ANTHROPIC_API_KEY": "test-placeholder",  # never used: FakeAnthropic replaces the client
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CEREBRO_MW_SHEET_ID", raising=False)
    return {"gc": gc, "http": http, "slack": slack, "tmp": tmp_path, "monkeypatch": monkeypatch}


def run(env, *args):
    return load_run_daily().main(["--out-root", str(env["tmp"] / "out"), *args])


def pick_msgs(slack):
    """Pick messages (tagged faramir_pick). Increment 6: the daily header is a
    bot message too, so counting all messages no longer counts picks."""
    return [m for m in slack.messages if (m.get("metadata") or {}).get("event_type") == "faramir_pick"]


def header_msgs(slack):
    return [m for m in slack.messages if (m.get("metadata") or {}).get("event_type") == "faramir_header"]


def payload_of(call):
    return json.loads(call["messages"][0]["content"])


def pick_first_candidate(kwargs):
    film = next(f for f in payload_of(kwargs)["active_films"] if f["candidates"])
    c = film["candidates"][0]
    comp = c["comp"] or {}
    return json.dumps({"picks": [{
        "active_tmdb_id": film["tmdb_id"], "active_title": film["title"],
        "comp_tmdb_id": comp.get("tmdb_id"), "comp_title": comp.get("title"),
        "axis": c["axis"], "kind": c["kind"], "threshold_value": c["threshold_value"],
        "angle_category": (c["angle_categories"] or ["studio_sibling"])[0],
        "headline": f"Beta Story Moves Past {comp.get('title')}", "angle": "A steady run continues.",
        "claims": [event_claim(c)],
    }]})


def candidate_comp_ids(fake):
    return {c["comp"]["tmdb_id"] for f in payload_of(fake.calls[0])["active_films"] for c in f["candidates"] if c["comp"]}


def state_after(gc):
    return gc.book("S").worksheet("state").get_all_records()


# ── OK corpus ───────────────────────────────────────────────────────────

def test_ok_corpus_runs_normally(env):
    fake = FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    run(env)

    assert len(fake.calls) == 1 and candidate_comp_ids(fake)
    slack = env["slack"]
    assert len(pick_msgs(slack)) == 1  # one pick posted
    (hdr,) = header_msgs(slack)
    header = json.dumps(hdr["blocks"])
    assert "1 editorial pick" in header and "degraded" not in header
    assert state_after(env["gc"])[0]["domestic_today"] == 152_000_000


# ── ABORT: legacy corpus never used ─────────────────────────────────────

def test_legacy_corpus_aborts_with_no_writes_at_all(env):
    # Revised in Increment 3: an ABORT halts ALL operational writes, state included.
    fake = FakeAnthropic([]).install(env["monkeypatch"])
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", os.path.join(ROOT, "data", "corpus.json"))
    assert run(env) == 3

    assert fake.calls == []                       # Claude never called
    assert env["slack"].messages == []            # no picks posted
    (alert,) = env["slack"].webhook_posts         # only the integrity alert
    text = json.dumps(alert["payload"])
    assert "comparison data failed integrity" in text and "legacy_format" in text
    assert env["gc"].writes() == []               # no Sheets write of any kind
    assert state_after(env["gc"])[0]["domestic_today"] == 140_000_000  # unchanged


def test_missing_corpus_file_aborts(env):
    FakeAnthropic([]).install(env["monkeypatch"])
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", str(env["tmp"] / "nope.json"))
    run(env)
    assert "corpus_missing" in json.dumps(env["slack"].webhook_posts[0]["payload"])
    assert env["slack"].messages == []


# ── DEGRADED: bad record excluded, editors warned ───────────────────────

def _degraded_copy(tmp):
    with open(V2_PATH) as f:
        data = json.load(f)
    # Corrupt a record that the OK corpus actually offers as a Beta Story
    # domestic candidate (so its absence below is meaningful).
    from faramir.corpus import build_index
    from faramir.matcher import find_candidates

    active = {"tmdb_id": 5001, "domestic_today": 152_000_000, "domestic_yesterday": 140_000_000}
    cands = find_candidates(active, build_index(data["films"]), data["films"])
    target_id = next(c["comp_tmdb_id"] for c in cands if c["axis"] == "domestic"
                     and c["comp"]["worldwide_status"] == "reported")
    target = next(f for f in data["films"] if f["tmdb_id"] == target_id)
    target["worldwide_lifetime"] = target["domestic_lifetime"] - 1
    p = tmp / "corpus.v2.json"
    p.write_text(json.dumps(data))
    return str(p), target["tmdb_id"]


def ledger_candidate_targets(env):
    """Every candidate the run considered (Phase 4 ranks before Claude, so the Claude payload is
    only the top slots; the candidates ledger still lists all of them)."""
    from faramir.events.ledger import Ledger

    (run_dir,) = (env["tmp"] / "out").iterdir()
    return {r["target"] for r in Ledger(str(run_dir / "ledger")).read("candidates")}


def _target_key(tmdb):
    with open(V2_PATH) as f:
        film = next(x for x in json.load(f)["films"] if x["tmdb_id"] == tmdb)
    return f"comp:{film.get('imdb_id') or film['tmdb_id']}"


def test_degraded_target_is_a_candidate_on_the_ok_corpus(env):
    _, bad_tmdb = _degraded_copy(env["tmp"])
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    run(env)
    assert _target_key(bad_tmdb) in ledger_candidate_targets(env)


def test_degraded_corpus_excludes_bad_record_and_warns(env):
    path, bad_tmdb = _degraded_copy(env["tmp"])
    fake = FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", path)
    run(env)

    assert bad_tmdb not in candidate_comp_ids(fake)
    assert _target_key(bad_tmdb) not in ledger_candidate_targets(env)
    header = json.dumps(header_msgs(env["slack"])[0]["blocks"], ensure_ascii=False)
    assert "Comparison data degraded" in header and "invalid: 1" in header


# ── picks naming a non-usable comp never reach Slack ────────────────────

def test_pick_with_comp_outside_usable_corpus_is_dropped(env):
    def invented(kwargs):
        film = payload_of(kwargs)["active_films"][0]
        return json.dumps({"picks": [{
            "active_tmdb_id": film["tmdb_id"], "active_title": film["title"], "comp_tmdb_id": 987654321,
            "comp_title": "Not In Corpus", "axis": "domestic", "kind": "just_passed", "threshold_value": None,
            "angle_category": "unexpected_giant_killer", "headline": "Beta Story Passes Not In Corpus", "angle": "x",
            "claims": [{"type": "passed_comp", "comp_tmdb_id": 987654321, "threshold": None, "axis": "domestic",
                        "relation": None, "genre": None, "text": "passes Not In Corpus"}],
        }]})

    FakeAnthropic([invented]).install(env["monkeypatch"])
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    run(env)
    assert pick_msgs(env["slack"]) == []
    assert "0 editorial picks" in json.dumps(header_msgs(env["slack"])[0]["blocks"])
