"""Faramir 2.0 in production: step 11 turns Faramir's ranked stories into cards, titles them with the
Aragorn method, binds each to its candidate and posts it through the existing log → post → confirm path."""

import json

import pytest

from faramir.titles import aragorn
from tests.fakes.fake_anthropic import FakeAnthropic
from tests.fakes.fake_gspread import FakeClient
from tests.test_run_daily_corpus import V2_PATH, env, load_run_daily, pick_msgs  # noqa: F401

pytestmark = pytest.mark.cards_engine

CEREBRO = "\n".join(["Title,Pub. Date,Pub. Type,Brand,Content Type,Page Type,Actual Sessions,Tags"] + [
    f"Box Office Story {i},2026-08-{i + 1:02d} 10:00:00,Publish,MW,News,Regular,{8000 + i * 100},Box Office"
    for i in range(20)])


def titled(title="Beta Story's Box Office Run Keeps Climbing", verdict="ok"):
    def write(brief, client, other_titles):
        return aragorn.Titled(title if verdict == "ok" else None, "Beta Story Box Office Alternative", verdict=verdict,
                              usage={"input_tokens": 1000, "output_tokens": 100})
    return write


def run(env, tmp_path, write, *args):
    p = tmp_path / "cerebro.csv"
    p.write_text(CEREBRO)
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    env["monkeypatch"].setenv("FARAMIR_CEREBRO_SNAPSHOT", str(p))
    env["monkeypatch"].delenv("FARAMIR_PICK_ENGINE", raising=False)
    env["monkeypatch"].setattr(aragorn, "write", write)
    fake = FakeAnthropic([]).install(env["monkeypatch"])
    code = load_run_daily().main(["--out-root", str(env["tmp"] / "out"), *args])
    (run_dir,) = (env["tmp"] / "out").iterdir()
    return code, json.loads((run_dir / "report.json").read_text()), fake


def test_ranked_card_is_titled_bound_and_posted(env, tmp_path):
    code, report, fake = run(env, tmp_path, titled())
    assert code == 0 and report["pick_engine"] == "cards"
    assert fake.calls == []                                   # no Claude selection call in the cards engine
    msgs = pick_msgs(env["slack"])
    assert msgs
    for msg in msgs:
        text = json.dumps(msg["blocks"])
        assert "Beta Story's Box Office Run Keeps Climbing" in text and "Alt: Beta Story Box Office Alternative" in text
        assert "Sources: <https://www.the-numbers.com/box-office-chart/" in text and "Why:" not in text
    rows = env["gc"].book("S").worksheet("suggestions").get_all_records()
    assert len(rows) == len(msgs) and len({r["candidate_id"] for r in rows}) == len(rows)
    assert all(r["headline"] == "Beta Story's Box Office Run Keeps Climbing" for r in rows)
    assert report["cards"]["cards"][0]["title_verdict"] == "ok"
    usage = [r for r in report["claude_usage"] if r["call_label"] == "aragorn_title"]
    assert usage and usage[0]["input_tokens"] == 1000


def test_killed_or_untitled_card_is_never_posted(env, tmp_path):
    code, report, _ = run(env, tmp_path, titled(verdict="kill"))
    assert code == 0 and pick_msgs(env["slack"]) == []
    assert env["gc"].book("S").worksheet("suggestions").get_all_records() == []


def test_no_cerebro_means_no_titles_and_a_warning(env, tmp_path):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    env["monkeypatch"].delenv("FARAMIR_PICK_ENGINE", raising=False)
    FakeAnthropic([]).install(env["monkeypatch"])
    assert load_run_daily().main(["--out-root", str(env["tmp"] / "out")]) == 0
    assert pick_msgs(env["slack"]) == []
    assert any("Cerebro data unavailable" in json.dumps(m) for m in env["slack"].messages)


def test_dry_run_titles_but_writes_nothing(env, tmp_path):
    env["monkeypatch"].delenv("FARAMIR_ENABLED")
    code, report, _ = run(env, tmp_path, titled(), "--dry-run")
    assert code == 0 and env["gc"].writes() == [] and pick_msgs(env["slack"]) == []
    assert report["cards"]["cards"][0]["title"] == "Beta Story's Box Office Run Keeps Climbing"


def test_cerebro_sheet_is_read_column_by_column_without_sensitive_columns():
    from faramir.analytics import cerebro_snapshot

    gc = FakeClient()
    gc.book("C").add_tab(cerebro_snapshot.SHEET_TAB, [
        ["ArticleTitle", "Writer", "PubDatetime", "PubType", "ContentType", "PageType", "ActSess", "WriterPay"],
        ["A Story", "Jane Doe", "2026-08-01 10:00:00", "Publish", "News", "Regular", "9000", "120"]])
    snap = cerebro_snapshot.from_sheet(gc, "C")
    assert [(a.title, a.sessions) for a in snap.articles] == [("A Story", 9000.0)]
    fetched = [args[0] for _, method, args in gc.calls if method == "col_values"]
    assert 2 not in fetched and 8 not in fetched            # Writer and WriterPay never downloaded


# ── the scheduled job: --require-chart ──────────────────────────────────

def test_not_ready_chart_stops_before_any_sheet_or_slack_call(env, tmp_path, monkeypatch):
    from faramir import chart_watch

    monkeypatch.setenv("FARAMIR_CHART_WATCH", str(tmp_path / "watch.json"))
    monkeypatch.setattr(chart_watch, "check", lambda day, path: (False, "partial: 3 films; waiting for Wicker"))
    run_daily = load_run_daily()
    assert run_daily.main(["--out-root", str(env["tmp"] / "out"), "--require-chart"]) == run_daily.CHART_NOT_READY
    assert env["gc"].calls == [] and env["slack"].messages == []


def test_ready_chart_runs_once_then_the_day_is_done(env, tmp_path, monkeypatch):
    from faramir import chart_watch

    watch = tmp_path / "watch.json"
    monkeypatch.setenv("FARAMIR_CHART_WATCH", str(watch))
    monkeypatch.setattr(chart_watch, "check", lambda day, path: (True, "25 films"))
    code, report, _ = run(env, tmp_path, titled(), "--require-chart")
    assert code == 0 and report["chart_ready"] == "25 films" and pick_msgs(env["slack"])
    assert chart_watch.load(str(watch))["delivered"]
    calls = len(env["gc"].calls)
    assert load_run_daily().main(["--out-root", str(env["tmp"] / "out2"), "--require-chart"]) == 0
    assert len(env["gc"].calls) == calls                  # second trigger: nothing read, nothing posted


def test_sheets_client_backs_off_on_googles_quota(monkeypatch):
    import gspread
    from gspread.http_client import BackOffHTTPClient

    from faramir import sheet

    seen = {}
    monkeypatch.setenv("GOOGLE_SA_JSON", "{}")
    monkeypatch.setattr(gspread, "service_account_from_dict", lambda info, **kw: seen.update(kw) or "client")
    assert sheet.get_sheet_client() == "client" and seen["http_client"] is BackOffHTTPClient


def test_cerebro_sheet_dates_are_month_first_with_a_time():
    from faramir.analytics import cerebro_snapshot

    gc = FakeClient()
    gc.book("C").add_tab(cerebro_snapshot.SHEET_TAB, [
        ["ArticleTitle", "PubDatetime", "PubType", "ContentType", "PageType", "ActSess"],
        ["A Story", "10/29/2026 9:15:00", "Publish", "News", "Regular", "9000"],
        ["B Story", "8/1/2026 14:05:00", "Publish", "News", "Regular", "7000"]])
    snap = cerebro_snapshot.from_sheet(gc, "C")
    assert snap.dropped_rows == 0 and [a.pub_date.month for a in snap.articles] == [10, 8]
