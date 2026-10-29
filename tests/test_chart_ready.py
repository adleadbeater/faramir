"""scripts/faramir_run.py: the daily job waits for The Numbers' chart to be posted in full, not just started."""

import importlib.util
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
spec = importlib.util.spec_from_file_location("faramir_run", os.path.join(os.path.dirname(__file__), "..", "scripts", "faramir_run.py"))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

DAY = date(2026, 10, 28)


def rows(today, before):
    return lambda d: today if d == DAY else before


def test_partial_chart_is_not_ready_until_the_last_try(monkeypatch):
    monkeypatch.setattr(run, "chart_rows", rows(12, 25))          # 2026-10-28 at 1:52pm
    assert run.chart_ready(DAY) == (False, "partial: 12 films so far (the day before had 25)")
    assert run.chart_ready(DAY, partial_ok=True)[0]


def test_full_or_missing_chart(monkeypatch):
    monkeypatch.setattr(run, "chart_rows", rows(25, 25))
    assert run.chart_ready(DAY) == (True, "25 films")
    monkeypatch.setattr(run, "chart_rows", rows(0, 25))
    assert run.chart_ready(DAY, partial_ok=True) == (False, "not posted yet")
    monkeypatch.setattr(run, "chart_rows", rows(11, 0))           # day before unreachable: need 10
    assert run.chart_ready(DAY)[0]
