"""Chart readiness is learned film by film (faramir/chart_watch.py): the daily job runs once the films that
reported same-day yesterday are in again, or once the time they usually report by has passed."""

import importlib.util
import os
import sys
from datetime import date, datetime

from faramir import chart_watch as cw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
spec = importlib.util.spec_from_file_location("faramir_run", os.path.join(os.path.dirname(__file__), "..", "scripts", "faramir_run.py"))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

DAY = date(2026, 10, 29)
NOON, ONE, TWO_THIRTY = 12 * 60, 13 * 60, 14 * 60 + 30
BIG = 1_000_000


def history(*days):
    """Watched days: each a list of (title, gross, first-seen minute)."""
    store = {"days": {}}
    for d, films in days:
        for title, gross, minute in films:
            cw.record(store, d, minute, [(title, gross)])
    return store


YESTERDAY = (date(2026, 10, 28), [("Clayface", 2 * BIG, 11 * 60), ("Wicker", BIG, 12 * 60 + 30),
                                  ("Verity", 300_000, NOON), ("Tiny", 500, 15 * 60)])


def test_ready_as_soon_as_the_same_day_reporters_are_in():
    store = history(YESTERDAY)
    assert cw.decide(store, DAY, 11 * 60 + 15, ["Clayface", "Verity", "Wicker"]) == \
        (True, "3 films; all 3 that usually report same-day are in")


def test_waits_for_a_missing_reporter_until_its_usual_time_plus_grace():
    store = history(YESTERDAY)
    ready, why = cw.decide(store, DAY, NOON, ["Clayface", "Verity"])
    assert not ready and "Wicker (usually by 12:30pm)" in why and "until 12:50pm" in why
    ready, why = cw.decide(store, DAY, 12 * 60 + 50, ["Clayface", "Verity"])
    assert ready and why.startswith("partial") and "still missing Wicker" in why


def test_tail_and_weekly_reporters_are_never_waited_for():
    # Tiny made $500; a film that only appeared days later was never first-seen on a watched day
    store = history(YESTERDAY)
    assert "Tiny" not in cw.expectations(store, DAY)
    assert cw.decide(store, DAY, 11 * 60, ["Clayface", "Verity", "Wicker"])[0]


def test_expected_time_is_the_latest_of_the_last_three_days():
    store = history((date(2026, 10, 25), [("Wicker", BIG, 15 * 60)]), (date(2026, 10, 26), [("Wicker", BIG, 11 * 60)]),
                    (date(2026, 10, 27), [("Wicker", BIG, 13 * 60)]), (date(2026, 10, 28), [("Wicker", BIG, NOON)]))
    assert cw.expectations(store, DAY) == {"Wicker": 13 * 60}


def test_never_waits_past_the_hard_cutoff():
    store = history((date(2026, 10, 28), [("Late", BIG, 17 * 60)]))
    assert not cw.decide(store, DAY, 14 * 60 + 59, ["Other"])[0]
    assert cw.decide(store, DAY, cw.HARD_CUTOFF, ["Other"])[0]


def test_cold_start_uses_the_old_share_rule_until_the_cold_cutoff():
    store = {"days": {}}
    assert cw.decide(store, DAY, NOON, ["a"] * 12, day_before_rows=25) == \
        (False, "partial: 12 films so far (the day before had 25); no reporting history yet")
    assert cw.decide(store, DAY, NOON, ["a"] * 19, day_before_rows=25) == (True, "19 films")
    assert cw.decide(store, DAY, TWO_THIRTY, ["a"] * 12, day_before_rows=25)[0]
    assert cw.decide(store, DAY, TWO_THIRTY, [], day_before_rows=25) == (False, "not posted yet")


def test_record_keeps_the_first_time_a_film_was_seen():
    store = {"days": {}}
    cw.record(store, DAY, NOON, [("Clayface", 100)])
    cw.record(store, DAY, ONE, [("Clayface", 200), ("Wicker", 50)])
    d = store["days"]["2026-10-29"]
    assert d["first_seen"] == {"Clayface": "12:00", "Wicker": "13:00"} and d["gross"]["Clayface"] == 200
    assert d["checks"] == [["12:00", 1], ["13:00", 2]]


def test_run_records_live_checks_and_decides(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "WATCH_FILE", str(tmp_path / "watch.json"))
    cw.save(history(YESTERDAY), run.WATCH_FILE)
    monkeypatch.setattr(run, "chart_films", lambda d: [("Clayface", BIG), ("Verity", 1)] if d == DAY else [])
    now = datetime(2026, 10, 30, 12, 0, tzinfo=cw.ET)
    ready, why = run.chart_ready(DAY, now=now)
    assert not ready and "Wicker" in why
    assert run.chart_ready(DAY, partial_ok=True, now=now)[0]
    assert cw.load(run.WATCH_FILE)["days"]["2026-10-29"]["first_seen"] == {"Clayface": "12:00", "Verity": "12:00"}


def test_rerun_of_a_past_day_learns_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "WATCH_FILE", str(tmp_path / "watch.json"))
    monkeypatch.setattr(run, "chart_films", lambda d: [("Clayface", BIG)] * 20)
    assert run.chart_ready(DAY, now=datetime(2026, 11, 5, 9, 0, tzinfo=cw.ET))[0]
    assert not os.path.exists(run.WATCH_FILE)
