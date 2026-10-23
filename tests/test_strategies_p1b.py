"""Phase 1b: Takeover and Hold over committed weekend history — a hand-checked set of four
synthetic weekends, negative cases, the provisional tie rule and the 'no history' paths."""

from datetime import date, datetime, timedelta, timezone

import pytest

from faramir import history as H
from faramir.strategies import hold, takeover, weekend_runner
from faramir.strategies.weekend import from_snapshot
from faramir.tn_chart import parse_chart, weekend_url
from tests.fakes.fake_gspread import FakeClient
from tests.fixtures.tn_synth import page

FRIDAYS = [date(2026, 9, 25) + timedelta(weeks=i) for i in range(5)]


def row(rank, prev, title, gross, theaters, days, total=None):
    slug = title.replace(" ", "-") + "-(2026)"
    return (rank, prev, title, slug, gross, "", theaters, total or gross * 2, days)


# Five synthetic weekends; expectations for the last four were worked out by hand (see table below).
WEEKENDS = {
    FRIDAYS[0]: [row("1", "(new)", "Alpha", 40_000_000, 4000, 3), row("2", "(1)", "Bravo", 20_000_000, 3500, 10),
                 row("3", "(2)", "Old One", 5_000_000, 2000, 24)],
    FRIDAYS[1]: [row("1", "(1)", "Alpha", 25_000_000, 4000, 10), row("2", "(new)", "Charlie", 18_000_000, 3600, 3),   # Hold
                 row("3", "(2)", "Bravo", 9_000_000, 3200, 17)],
    FRIDAYS[2]: [row("1", "(new)", "Delta", 30_000_000, 3900, 3), row("2", "(1)", "Alpha", 15_000_000, 3800, 17),     # Takeover debut
                 row("3", "(2)", "Charlie", 8_000_000, 3400, 10)],
    FRIDAYS[3]: [row("1", "(2)", "Alpha", 12_000_000, 3500, 24), row("2", "(1)", "Delta", 11_000_000, 3900, 10),      # Takeover climb
                 row("3", "(new)", "Echo", 2_000_000, 300, 3), row("4", "(3)", "Charlie", 1_500_000, 3000, 17)],
    FRIDAYS[4]: [row("1", "(1)", "Alpha", 10_100_000, 3300, 31), row("2", "(new)", "Foxtrot", 10_000_000, 2800, 3),   # provisional Hold
                 row("3", "(2)", "Delta", 6_000_000, 3600, 17)],
}
EXPECTED = {   # weekend -> sorted (strategy, film, subtype, status)
    FRIDAYS[1]: [("hold", "Alpha", None, "confirmed")],
    FRIDAYS[2]: [("takeover", "Delta", "debut", "confirmed")],
    FRIDAYS[3]: [("takeover", "Alpha", "climb", "confirmed")],
    FRIDAYS[4]: [("hold", "Alpha", None, "provisional")],
}


@pytest.fixture
def book(monkeypatch):
    monkeypatch.setenv("FARAMIR_COLLECT_HISTORY", "true")
    gc = FakeClient()
    gc.book("S").add_tab(H.HISTORY_TAB, [H.HISTORY_HEADERS])
    gc.book("S").add_tab(H.COLLECTIONS_TAB, [H.COLLECTION_HEADERS])
    for fri, films in WEEKENDS.items():
        chart = parse_chart(page("weekend", fri, films=films), weekend_url(fri), "weekend", fri)
        assert chart.verified, chart.issues
        at = datetime.combine(fri + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=14)
        assert H.collect(gc, "S", chart, run_id=f"r{fri}", retrieved_at=at, now=at).status == "committed"
    return gc


def weekends(gc, fri):
    w = from_snapshot(H.read_weekend(gc, "S", fri.isoformat()))
    prior = from_snapshot(H.read_weekend(gc, "S", (fri - timedelta(days=7)).isoformat()))
    return w, prior


@pytest.mark.parametrize("fri", list(EXPECTED))
def test_four_hand_checked_weekends(book, fri):
    result = weekend_runner.run(book, "S", fri.isoformat(), collection_enabled=True)
    got = sorted((e.strategy, e.film["title"], e.subtype, e.status) for e in result.events)
    assert result.status == "ok" and got == EXPECTED[fri]
    for e in result.events:
        assert [s.name for s in e.sources] == ["tn_weekend_history", "tn_weekend_history"] and e.reason


def test_takeover_evidence(book):
    w, prior = weekends(book, FRIDAYS[2])
    (e,) = takeover.detect(w, prior).events
    assert e.evidence["previous_leader"] == "Alpha" and e.evidence["rank"] == 1 and e.evidence["weekend_gross"] == 30_000_000
    assert e.target == {"type": "rank", "value": 1} and "opening weekend" in e.reason


def test_takeover_negative_same_leader_and_missing_chart(book):
    w, prior = weekends(book, FRIDAYS[1])
    assert takeover.detect(w, prior).rejected[0]["reason_code"] == "same_leader"
    w, _ = weekends(book, FRIDAYS[0])  # no weekend before the first
    assert takeover.detect(w, None).rejected[0]["reason_code"] == "chart_unavailable"
    assert takeover.detect(None, w).rejected[0]["reason_code"] == "chart_unavailable"


def test_takeover_near_tie_is_provisional_until_recollected(book):
    films = [row("1", "(new)", "Golf", 9_050_000, 3000, 3), row("2", "(1)", "Alpha", 9_000_000, 3200, 38)]
    fri = FRIDAYS[4] + timedelta(weeks=1)
    chart = parse_chart(page("weekend", fri, films=films), weekend_url(fri), "weekend", fri)
    sunday = datetime.combine(fri + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc)
    H.collect(book, "S", chart, run_id="sun", retrieved_at=sunday, now=sunday)
    w, prior = weekends(book, fri)
    (e,) = takeover.detect(w, prior).events
    assert e.status == "provisional" and "near_tie_provisional" in e.reason_codes
    tuesday = sunday + timedelta(days=2)
    H.collect(book, "S", chart, run_id="tue", retrieved_at=tuesday, now=tuesday)   # same figures re-collected
    w, prior = weekends(book, fri)
    assert takeover.detect(w, prior).events[0].status == "confirmed"


def test_hold_negative_cases(book):
    w, prior = weekends(book, FRIDAYS[2])
    codes = {r["reason_code"] for r in hold.detect(w, prior).rejected}
    assert "not_held" in codes                                   # Delta rose to No. 1: a Takeover
    w, prior = weekends(book, FRIDAYS[3])
    codes = [r["reason_code"] for r in hold.detect(w, prior).rejected]
    assert codes == ["not_held"]                                 # Alpha climbed back: not a hold


def test_hold_requires_a_qualifying_challenger(book):
    fri = FRIDAYS[4] + timedelta(weeks=1)
    no_opener = [row("1", "(1)", "Alpha", 9_000_000, 3200, 38), row("2", "(2)", "Foxtrot", 7_000_000, 2800, 10)]
    limited = no_opener + [row("3", "(new)", "Tiny", 1_000_000, 400, 3)]
    outranked = [row("1", "(new)", "Huge", 30_000_000, 4200, 3), row("2", "(1)", "Alpha", 9_000_000, 3200, 38)]
    for films, code in ((no_opener, "no_challenger"), (limited, "limited_opener")):
        w = from_snapshot(_snap(book, fri, films))
        prior = from_snapshot(H.read_weekend(book, "S", FRIDAYS[4].isoformat()))
        assert [r["reason_code"] for r in hold.detect(w, prior).rejected] == [code]
        fri += timedelta(weeks=1)
    # The opener out-ranks Alpha: Alpha is no longer No. 1, so it is not even a Hold candidate.
    w = from_snapshot(_snap(book, fri, outranked))
    prior = from_snapshot(H.read_weekend(book, "S", (fri - timedelta(days=7)).isoformat()))
    assert hold.detect(w, prior).events == []


def _snap(book, fri, films):
    chart = parse_chart(page("weekend", fri, films=films), weekend_url(fri), "weekend", fri)
    at = datetime.combine(fri + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc)
    H.collect(book, "S", chart, run_id=f"x{fri}", retrieved_at=at, now=at)
    return H.read_weekend(book, "S", fri.isoformat())


def test_damaged_or_missing_history_is_unavailable_not_assumed(book):
    ws = book.book("S").worksheet(H.HISTORY_TAB)
    ws.rows[1][H.HISTORY_HEADERS.index("weekend_gross")] = "1"       # damage the first weekend
    assert weekend_runner.run(book, "S", FRIDAYS[1].isoformat(), collection_enabled=True).status == "history_unavailable"
    later = (FRIDAYS[4] + timedelta(weeks=5)).isoformat()
    assert weekend_runner.run(book, "S", later, collection_enabled=True).status == "history_unavailable"


def test_collection_disabled_reports_no_data(book, monkeypatch):
    monkeypatch.delenv("FARAMIR_COLLECT_HISTORY")
    r = weekend_runner.run(book, "S", FRIDAYS[2].isoformat())
    assert r.status == "history_collection_disabled" and r.events == []


from tests.fakes.fake_anthropic import FakeAnthropic  # noqa: E402
from tests.test_run_daily_corpus import V2_PATH, env, pick_first_candidate  # noqa: E402,F401
from tests.test_run_daily_runtime import artifacts, main  # noqa: E402


def test_daily_run_reports_weekend_strategies_disabled(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    report, _, _ = artifacts(env)
    ws = report["weekend_strategies"]
    assert ws["status"] == "history_collection_disabled" and ws["weekend_start"] == "2026-10-16" and ws["events"] == []


def test_weekend_events_are_ledgered(book, tmp_path):
    from faramir.events.ledger import Ledger

    led = Ledger(str(tmp_path))
    weekend_runner.run(book, "S", FRIDAYS[2].isoformat(), ledger=led, collection_enabled=True)
    (rec,) = led.read("events")
    assert rec["strategy"] == "takeover" and rec["disposition"] == "weekend_detected" and rec["sources"]
