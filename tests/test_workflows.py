"""Increment 12: static checks on the workflow files. They are reviewed by
reading and never run before deployment; these tests pin the safeguards."""

import os
import re

import pytest

from faramir.state_store import LOCK_TTL

WF = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")
FILES = {"daily": "daily.yml", "corpus": "corpus_refresh.yml", "migrate": "migrate.yml"}


def text(name):
    with open(os.path.join(WF, FILES[name]), encoding="utf-8") as f:
        return f.read()


def code(name):
    """The workflow without comment lines."""
    return "\n".join(l for l in text(name).splitlines() if not l.strip().startswith("#"))


@pytest.mark.parametrize("name", FILES)
def test_shared_production_concurrency_group(name):
    assert re.search(r"concurrency:\s*\n\s*group: faramir-production\s*\n\s*cancel-in-progress: false", code(name))


@pytest.mark.parametrize("name", FILES)
def test_timeouts_fit_the_state_lease(name):
    minutes = [int(m) for m in re.findall(r"timeout-minutes:\s*(\d+)", code(name))]
    assert minutes and all(m <= 30 for m in minutes)
    if name in ("daily", "migrate"):  # the jobs that take the state lock
        assert all(m * 60 < LOCK_TTL.total_seconds() / 2 for m in minutes)


@pytest.mark.parametrize("name", FILES)
def test_tests_run_before_any_production_step(name):
    lines = code(name).splitlines()
    test_at = next(i for i, l in enumerate(lines) if "python -m pytest" in l)
    for marker in ("run_daily.py", "build_corpus.py", "migrate_sheets.py", "git push"):
        hits = [i for i, l in enumerate(lines) if marker in l]
        assert all(i > test_at for i in hits), marker


def test_daily_is_fail_closed_and_supports_dry_run():
    c = code("daily")
    assert "if: ${{ vars.FARAMIR_ENABLED == 'true' || (github.event_name == 'workflow_dispatch' && inputs.dry_run) }}" in c
    assert re.search(r"dry_run:\s*\n\s*description:.*\n\s*type: boolean\s*\n\s*default: true", c)
    assert "'--dry-run'" in c and "FARAMIR_ENABLED: ${{ vars.FARAMIR_ENABLED }}" in c
    assert "!= 'false'" not in c and "vars.FARAMIR_ENABLED != " not in c  # never "enabled unless disabled"


def test_corpus_workflow_is_manual_repair_only():
    c = code("corpus")
    assert "schedule:" not in c and "cron:" not in c
    assert "--repair-identities" in c and "--legacy-full-build" not in c
    assert "vars.FARAMIR_CORPUS_REFRESH_ENABLED == 'true'" in c
    commit = c[c.index("Commit the repaired corpus"):]
    assert "vars.FARAMIR_CORPUS_REFRESH_ENABLED == 'true'" in commit.split("run:")[0] and "!inputs.dry_run" in commit.split("run:")[0]
    assert "verify_corpus_v2.py" in c and c.index("verify_corpus_v2.py") < c.index("git push")


def test_migration_workflow_plan_by_default_apply_gated():
    c = code("migrate")
    assert "schedule:" not in c
    assert re.search(r"default: plan", c)
    assert "vars.FARAMIR_MIGRATION_ENABLED == 'true' && vars.FARAMIR_ENABLED != 'true'" in c
    assert '--confirm-sheet "${{ inputs.confirm_sheet }}"' in c


@pytest.mark.parametrize("name", FILES)
def test_no_history_collection_or_new_scraping(name):
    c = code(name)
    assert "FARAMIR_COLLECT_HISTORY: 'true'" not in c
    assert "the-numbers.com" not in c and "boxofficemojo" not in c and "--legacy-full-build" not in c
    if name == "daily":
        assert "FARAMIR_COLLECT_HISTORY: 'false'" in c


def test_lock_lease_exceeds_the_daily_timeout():
    minutes = int(re.search(r"timeout-minutes:\s*(\d+)", code("daily")).group(1))
    assert LOCK_TTL.total_seconds() >= 3 * minutes * 60


def test_daily_polls_for_the_chart_and_keeps_only_public_state():
    c = code("daily")
    assert "cron: '7,22,37,52 14-20 * * *'" in c                     # off-the-hour, every 15 minutes
    assert "--require-chart" in c and '"$code" -eq 75' in c          # not ready = success, try next trigger
    assert "FARAMIR_PICK_ENGINE: ${{ vars.FARAMIR_PICK_ENGINE || 'cards' }}" in c
    assert "FARAMIR_CHART_WATCH: state/chart_watch.json" in c
    # the public state branch carries chart timings only: never the ledger, Cerebro or Power Words
    assert "git add chart_watch.json" in c and "FARAMIR_LEDGER_DIR" not in c and "cerebro_MW" not in c


def test_a_copy_of_the_sheet_is_only_ever_used_for_rehearsals():
    daily, migrate = code("daily"), code("migrate")
    # daily: the copy is read only on a manual dry run; a real run always uses the production secret
    assert ("FARAMIR_SHEET_ID: ${{ (github.event_name == 'workflow_dispatch' && inputs.dry_run && "
            "inputs.copy_sheet_id != '') && inputs.copy_sheet_id || secrets.FARAMIR_SHEET_ID }}") in daily
    # migrate: apply still needs confirm_sheet to equal the sheet actually targeted (checked by the script)
    assert migrate.count("FARAMIR_SHEET_ID: ${{ inputs.copy_sheet_id || secrets.FARAMIR_SHEET_ID }}") == 2
