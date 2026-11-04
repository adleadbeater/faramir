"""Chart readiness is learned film by film (faramir/chart_watch.py): the daily job runs once the films that
reported same-day yesterday are in again, or once the time they usually report by has passed."""

import importlib.util
import os
import sys
from datetime import date, datetime, timedelta

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
        cw.record(store, d, 10 * 60 + 15, [])   # watched from the start of the polling window, as the waiting job does
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


# ---- 2026-11-02: waiting inside one job instead of on GitHub's dropped cron triggers -------------------------

def at(h, m=0, day=30):
    return datetime(2026, 10, day, h, m, tzinfo=cw.ET)


class Clock:
    def __init__(self, start):
        self.now, self.slept = start, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


def test_a_chart_with_no_history_is_ready_once_it_has_been_quiet_for_the_settle_time():
    store = {"days": {}}
    films = [(f"Film {i}", (20 - i) * BIG) for i in range(14)]
    cw.record(store, DAY, 11 * 60, films)
    assert cw.unchanged_for(store, DAY, 11 * 60 + 30) == 30
    assert not cw.decide(store, DAY, 11 * 60 + 30, [t for t, _ in films], day_before_rows=40)[0]
    ready, why = cw.decide(store, DAY, 11 * 60 + cw.SETTLE, [t for t, _ in films], day_before_rows=40)
    assert ready and why == "14 films; chart unchanged for 45 minutes"


def test_any_change_to_the_chart_restarts_the_quiet_clock():
    store = {"days": {}}
    films = [(f"Film {i}", (20 - i) * BIG) for i in range(14)]
    cw.record(store, DAY, 11 * 60, films)
    cw.record(store, DAY, 11 * 60 + 30, films)                       # same chart: no change
    assert cw.unchanged_for(store, DAY, 12 * 60) == 60
    cw.record(store, DAY, 12 * 60, [("Film 0", 21 * BIG)] + films[1:])   # a top-film gross moved
    assert cw.unchanged_for(store, DAY, 12 * 60) == 0
    cw.record(store, DAY, 12 * 60 + 5, films + [("New", BIG)])       # a new film appeared
    assert cw.unchanged_for(store, DAY, 12 * 60 + 5) == 0


def test_the_quiet_rule_never_applies_to_a_thin_chart():
    store = {"days": {}}
    cw.record(store, DAY, 10 * 60, [("A", BIG), ("B", BIG)])
    assert not cw.decide(store, DAY, 13 * 60, ["A", "B"], day_before_rows=40)[0]


def test_days_recorded_by_the_old_sparse_checks_are_never_learned_from():
    legacy = {"days": {"2026-10-28": {"checks": [["17:50", 21]], "first_seen": {"Clayface": "17:50"},
                                      "gross": {"Clayface": 2 * BIG}}}}
    assert cw.expectations(legacy, DAY) == {}
    fresh = history(YESTERDAY)
    assert "Clayface" in cw.expectations(fresh, DAY)


def test_a_day_first_looked_at_late_in_the_afternoon_is_never_learned_from():
    late = {"days": {"2026-10-28": {"checks": [["16:48", 27]], "changed": [["16:48", "x"]],
                                    "first_seen": {"Clayface": "16:48"}, "gross": {"Clayface": 2 * BIG}}}}
    assert cw.expectations(late, DAY) == {}
    early = {"days": {"2026-10-28": {"checks": [["10:15", 9]], "changed": [["10:15", "x"]],
                                     "first_seen": {"Clayface": "10:15"}, "gross": {"Clayface": 2 * BIG}}}}
    assert cw.expectations(early, DAY) == {"Clayface": 10 * 60 + 15}


def test_wait_polls_every_five_minutes_and_stops_as_soon_as_the_chart_is_ready(tmp_path):
    path, clock = str(tmp_path / "w.json"), Clock(at(11, 0))
    chart = {"n": 0}

    def fetch(d):
        if d != DAY:
            return [(f"Y{i}", BIG) for i in range(14)]
        chart["n"] += 1
        return [(f"Film {i}", (20 - i) * BIG) for i in range(14)] if chart["n"] >= 3 else []

    # no history: the chart is posted on the third poll; 14 of the day before's 14 films are in
    ready, why = cw.wait(DAY, path, now_fn=clock, sleep=clock.sleep, fetch=fetch)
    assert ready and clock.slept == [300, 300]
    assert cw.load(path)["days"]["2026-10-29"]["checks"][0][0] == "11:10"


def test_wait_does_not_poll_before_the_earliest_time(tmp_path):
    clock, calls = Clock(at(9, 7)), []

    def fetch(d):
        calls.append(clock.now)
        return [(f"Film {i}", BIG) for i in range(14)]

    ready, _ = cw.wait(DAY, str(tmp_path / "w.json"), now_fn=clock, sleep=clock.sleep, fetch=fetch)
    assert ready and min(calls) >= at(10, 0) and clock.slept[0] == 53 * 60


def test_wait_gives_up_at_five_pm_when_the_chart_never_appears(tmp_path):
    clock = Clock(at(14, 0))
    ready, why = cw.wait(DAY, str(tmp_path / "w.json"), now_fn=clock, sleep=clock.sleep, fetch=lambda d: [])
    assert not ready and why == "not posted yet" and clock.now >= at(17, 0)


def test_wait_hands_over_to_a_later_trigger_after_the_max_wait(tmp_path):
    clock = Clock(at(10, 0))
    ready, why = cw.wait(DAY, str(tmp_path / "w.json"), now_fn=clock, sleep=clock.sleep, fetch=lambda d: [])
    assert not ready and clock.now - at(10, 0) >= timedelta(minutes=cw.MAX_WAIT)
    assert clock.now < at(17, 0)


def test_waiting_for_a_past_day_checks_once(tmp_path):
    clock = Clock(at(12, 0, day=31))
    ready, _ = cw.wait(DAY, str(tmp_path / "w.json"), now_fn=clock, sleep=clock.sleep, fetch=lambda d: [("A", BIG)] * 20)
    assert ready and clock.slept == []
