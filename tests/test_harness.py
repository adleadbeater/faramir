"""Increment 0: the test harness itself, plus baseline tests that run the
existing (pre-v2) Faramir code against the fakes.

Tests marked ``baseline`` pin current behavior, including known defects, so
later increments change it knowingly. Each such test names the increment that
is expected to replace it.
"""

import importlib.util
import json
import os
import socket
import warnings
from datetime import date

import pytest

from tests.fakes.fake_anthropic import FakeAnthropic
from tests.fakes.fake_gspread import FakeClient, make_api_error
from tests.fakes.fake_http import FakeHTTP, FakeResponse, UnexpectedRequest
from tests.fakes.fake_slack import FakeSlack

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
ROOT = os.path.join(os.path.dirname(__file__), "..")


# ── harness ──────────────────────────────────────────────────────────────

def test_network_is_blocked_by_default():
    with pytest.raises(Exception, match="network access is disabled"):
        socket.create_connection(("example.com", 80))


def test_unrouted_http_request_fails_loudly(monkeypatch):
    import requests

    FakeHTTP().install(monkeypatch)
    with pytest.raises(UnexpectedRequest):
        requests.get("https://www.boxofficemojo.com/title/tt0000001/")


def test_fake_gspread_failure_injection_and_write_log():
    gc = FakeClient()
    ws = gc.book("S").add_tab("state", [["a", "b"], ["1", "2"]])
    ws.fail_on("update", make_api_error(), after=1)
    ws.update(values=[["x"]], range_name="A1")
    with pytest.raises(Exception, match="APIError"):
        ws.update(values=[["y"]], range_name="A2")
    assert [c[1] for c in gc.writes()] == ["update", "update"]
    assert ws.data[0][0] == "x"


def test_fake_gspread_accepts_legacy_update_order_like_gspread6():
    gc = FakeClient()
    ws = gc.book("S").add_tab("t")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ws.update("A1", [["h1", "h2"], ["1", "2"]])
    assert ws.data == [["h1", "h2"], ["1", "2"]]
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_pinned_anthropic_major_version():
    import anthropic

    assert anthropic.__version__ == "2.1.0"


def test_run_daily_imports_on_python_311():
    # scripts/run_daily.py uses `dict | None` without a __future__ import, so it
    # only imports on 3.10+. This pins that CI's 3.11 is the supported runtime.
    spec = importlib.util.spec_from_file_location("run_daily", os.path.join(ROOT, "scripts", "run_daily.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert callable(mod.main)


# ── Sheets baselines ─────────────────────────────────────────────────────

def _state_row(tmdb_id, title, dom):
    return {"tmdb_id": tmdb_id, "canonical_title": title, "status": "active", "domestic_today": dom}


def test_write_values_round_trip_never_clears():
    # Replaces the Increment 0 baselines for write_state/read_state: the state
    # tab is overwritten in place and leftover rows trimmed — no clear().
    from faramir.sheet import read_values, write_values

    gc = FakeClient()
    gc.book("S").add_tab("state", [["h"], ["1"], ["2"], ["3"]])
    write_values(gc, "S", "state", [["h"], ["9"]], "write_state")
    assert read_values(gc, "S", "state") == [["h"], ["9"]]
    assert "clear" not in [c[1] for c in gc.writes()]


def test_read_values_raises_on_api_error_never_returns_empty():
    # Replaces test_baseline_read_state_swallows_api_errors (Increment 4).
    from faramir.sheet import SheetReadError, TabMissing, read_values

    gc = FakeClient()
    gc.book("S").add_tab("state", [["tmdb_id"], ["1"]])
    gc.book("S").fail_worksheet("state", make_api_error())
    with pytest.raises(SheetReadError):
        read_values(gc, "S", "state")
    with pytest.raises(TabMissing):
        read_values(gc, "S", "nope")


def test_append_new_end_state_single_call():
    # Replaces the Increment 0 baselines for append_suggestions (one call per
    # row, header via append_row): one append_rows call, header included.
    from faramir.sheet import SUGGESTIONS_HEADERS
    from faramir.suggestions import append_new, read_table

    gc = FakeClient()
    gc.book("S").add_tab("suggestions")
    rows = [dict(run_date="2026-10-22", active_tmdb_id=1, comp_tmdb_id=c, axis="domestic", kind="just_passed",
                 headline=h) for c, h in ((9, "H1"), (8, "H2"))]
    append_new(gc, "S", rows)
    assert gc.book("S").worksheet("suggestions").data[0] == SUGGESTIONS_HEADERS
    assert [r["headline"] for r in read_table(gc, "S").records()] == ["H1", "H2"]
    assert [c[1] for c in gc.writes()] == ["append_rows"]


# ── Slack baselines ──────────────────────────────────────────────────────

def _pick():
    return {
        "active_tmdb_id": 1, "active_title": "Alpha Film", "comp_tmdb_id": 9, "comp_title": "Classic",
        "axis": "domestic", "kind": "just_passed", "threshold_value": None,
        "angle_category": "franchise_sibling", "headline": "Alpha Film Passes Classic", "angle": "Angle text.",
    }


def test_post_pick_and_header_through_fake_slack(monkeypatch):
    from faramir.slack_post import post_header, post_pick

    http = FakeHTTP().install(monkeypatch)
    slack = FakeSlack().attach(http)
    post_header("https://hooks.slack.com/services/T/B/X", 1, 3, date(2026, 10, 22))
    ts = post_pick("xoxb-test", "C1", _pick(), {"domestic_today": 45_000_000}, {"release_year": "2010", "domestic_lifetime": 44_000_000, "worldwide_lifetime": 90_000_000})
    assert ts == slack.messages[0]["ts"]
    assert len(slack.webhook_posts) == 1
    assert "Alpha Film Passes Classic" in json.dumps(slack.messages[0]["blocks"])


def test_post_pick_returns_none_on_slack_error(monkeypatch):
    from faramir.slack_post import post_pick

    http = FakeHTTP().install(monkeypatch)
    slack = FakeSlack().attach(http)
    slack.fail_next_post("ratelimited")
    assert post_pick("xoxb-test", "C1", _pick(), {}, None) is None


def test_collect_feedback_reads_reactions_and_replies(monkeypatch):
    from faramir.feedback import collect_feedback

    http = FakeHTTP().install(monkeypatch)
    slack = FakeSlack().attach(http)
    slack.reactions["111.1"] = [{"name": "+1", "count": 2}, {"name": "x", "count": 1}]
    slack.replies["111.1"] = [{"ts": "111.2", "text": "already covered"}]
    rows = [{"run_date": "2026-10-21", "slack_ts": "111.1", "active_title": "A"}, {"run_date": "2026-10-20", "slack_ts": "99.1"}]
    out = collect_feedback("xoxb-test", "C1", rows, date(2026, 10, 21))
    assert out[0]["thumbs_up"] == 2 and out[0]["thumbs_down"] == 1
    assert out[0]["thread_feedback"] == "already covered"
    assert out[1] == rows[1]


# ── Claude baselines ─────────────────────────────────────────────────────

def _payload():
    return {"run_date": "2026-10-22", "picks_target": 4, "picks_min": 2, "picks_max": 4,
            "active_films": [{"tmdb_id": 1, "title": "Alpha Film", "candidates": []}]}


def test_select_picks_parses_json_with_prose_preamble(monkeypatch):
    from faramir.claude_select import select_picks

    body = "Here are the picks:\n" + json.dumps({"picks": [_pick()]})
    FakeAnthropic([body]).install(monkeypatch)
    picks = select_picks(_payload())
    assert [p["headline"] for p in picks] == ["Alpha Film Passes Classic"]


def test_select_picks_returns_empty_after_two_bad_responses(monkeypatch):
    from faramir.claude_select import select_picks

    fake = FakeAnthropic(["not json", "still not json"]).install(monkeypatch)
    assert select_picks(_payload()) == []
    assert len(fake.calls) == 2


@pytest.mark.baseline
def test_baseline_select_picks_only_checks_active_id(monkeypatch):
    # Defect: an invented comp passes validation. Replaced in Increment 7 (binding).
    from faramir.claude_select import select_picks

    invented = dict(_pick(), comp_tmdb_id=123456789, comp_title="Invented Film")
    FakeAnthropic([json.dumps({"picks": [invented]})]).install(monkeypatch)
    assert select_picks(_payload())[0]["comp_tmdb_id"] == 123456789


@pytest.mark.baseline
def test_baseline_anthropic_client_has_no_timeout_or_retry_config(monkeypatch):
    # Replaced in Increment 7: explicit timeout and max_retries.
    from faramir.claude_select import select_picks

    fake = FakeAnthropic([json.dumps({"picks": []})]).install(monkeypatch)
    select_picks(_payload())
    assert fake.client_kwargs == [{}]


# ── The Numbers baselines ────────────────────────────────────────────────

def _tn_daily_html():
    with open(os.path.join(FIXTURES, "tn_daily_synthetic.html"), encoding="utf-8") as f:
        return f.read()


def test_fetch_daily_list_on_synthetic_fixture(monkeypatch):
    from faramir.bom_fetch import fetch_daily_list

    http = FakeHTTP().install(monkeypatch)
    http.route("GET", "https://www.the-numbers.com/box-office-chart/daily/2026/10/21", FakeResponse(200, text=_tn_daily_html()))
    rows = fetch_daily_list(date(2026, 10, 21))
    assert [r["title"] for r in rows] == ["Alpha Film", "Beta Story", "Gamma Night", "Old Classic", "Small Indie"]
    beta = rows[1]
    assert beta["gross_to_date"] == 152_000_000 and beta["daily_gross"] == 1_200_000 and beta["days_in_release"] == 34
    # TN has no distributor column, so the current parser always returns "".
    assert all(r["distributor"] == "" for r in rows)


@pytest.mark.baseline
def test_baseline_weekend_fetch_uses_sunday_url_and_falls_back_to_daily(monkeypatch):
    # Defect: TN weekend charts are keyed by the Friday date; the Sunday URL
    # 404s and the function silently returns Sunday's *daily* chart.
    # Replaced in Increment 10 (Friday URL, real weekend parser).
    from faramir.bom_fetch import fetch_weekend_list

    http = FakeHTTP().install(monkeypatch)
    http.route("GET", "https://www.the-numbers.com/box-office-chart/weekend/2026/10/18", FakeResponse(404, text=""))
    http.route("GET", "https://www.the-numbers.com/box-office-chart/daily/2026/10/18", FakeResponse(200, text=_tn_daily_html()))
    rows = fetch_weekend_list(2026, 42)  # ISO week 42 → Sunday 2026-10-18
    urls = [c["url"] for c in http.calls]
    assert urls[0].endswith("/weekend/2026/10/18")
    assert urls[-1].endswith("/daily/2026/10/18")
    assert len(rows) == 5
