"""Increment 12: scripts/migrate_sheets.py against a realistic v1 workbook copy
(in-memory). Plan is read-only; apply is idempotent, preserves state, editor
values, suggestions and feedback, and writes the schema marker last."""

import importlib.util
import json
import os

import pytest

from faramir import runtime
from faramir.preflight import check_sheets, schema_version
from faramir.sheet import STATE_HEADERS, SUGGESTIONS_HEADERS
from faramir.state_integrity import LKG_TAB, META_TAB, STATE_TAB, parse_meta
from faramir.state_store import StateStore
from tests.fakes.fake_gspread import FakeClient
from tests.fixtures import legacy_workbook as LW

ROOT = os.path.join(os.path.dirname(__file__), "..")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = load("migrate_sheets")


class Crash(Exception):
    pass


@pytest.fixture
def gc():
    c = FakeClient()
    LW.build(c.book("S"))
    return c


def tab(gc, name):
    return gc.book("S").worksheet(name).data


def tabs(gc):
    return set(gc.book("S")._sheets)


# ── plan ────────────────────────────────────────────────────────────────

def test_plan_is_read_only_and_describes_every_step(gc):
    p = M.plan(gc, "S")
    assert gc.writes() == [] and not [c for c in gc.calls if c[1] == "add_worksheet"]
    assert p["refuse"] == [] and p["already_migrated"] is False and p["schema_version"] is None
    assert p["state"] == {"layout": "v1", "rows": 6, "editor_overrides": 1, "notes": 3}
    assert (p["suggestions"]["schema"], p["suggestions"]["rows"], p["suggestions"]["with_slack_ts"],
            p["suggestions"]["with_feedback"]) == (1, 7, 6, 4)
    pending = [s["name"] for s in p["steps"] if s["status"] == "pending"]
    assert pending == ["suggestions_v2", "state_integrity_init", "state_v2_columns", "create_identity_overrides",
                       "create_weekend_history", "create_weekend_collections", "verify_workbook", "schema_marker"]
    assert p["rollback"] == M.ROLLBACK


def test_main_plan_is_default_and_read_only(gc, monkeypatch, capsys):
    from faramir import sheet

    monkeypatch.setattr(sheet, "get_sheet_client", lambda: gc)
    monkeypatch.setenv("FARAMIR_SHEET_ID", "S")
    assert M.main([]) == 0 and gc.writes() == []
    assert json.loads(capsys.readouterr().out)["already_migrated"] is False


def test_rollback_plan_prints_and_touches_nothing(capsys):
    assert M.main(["--rollback-plan"]) == 0
    out = capsys.readouterr().out
    assert "ever deleted" in out and "FARAMIR_ENABLED=false" in out and "Archive (rename, never delete)" in out


@pytest.mark.parametrize("mutate, why", [
    (lambda b: b.worksheet("state").rows.__setitem__(2, ["x"] + b.worksheet("state").rows[2][1:]), "state"),
    (lambda b: b.add_tab("weekend_history", [["something", "else"]]), "weekend_history"),
    (lambda b: b.worksheet("suggestions").rows[0].__setitem__(3, "renamed"), "suggestions header"),
])
def test_unexpected_workbook_is_refused_before_any_write(gc, mutate, why):
    mutate(gc.book("S"))
    p = M.plan(gc, "S")
    assert p["refuse"] and why in " ".join(p["refuse"])
    with pytest.raises(M.MigrationStopped, match="refused before any write"):
        M.apply(gc, "S", "mig")
    assert gc.writes() == []


# ── apply ───────────────────────────────────────────────────────────────

def test_apply_migrates_and_preserves_everything(gc):
    state_before = [list(r) for r in tab(gc, STATE_TAB)]
    sugg_before = [list(r) for r in tab(gc, "suggestions")]
    usage_before = [list(r) for r in tab(gc, "Usage")]
    corpus_meta_before = [list(r) for r in tab(gc, "corpus_meta")]

    result = M.apply(gc, "S", "mig-1")
    assert result["applied"] == ["suggestions_v2", "state_integrity_init", "state_v2_columns",
                                 "create_identity_overrides", "create_weekend_history", "create_weekend_collections",
                                 "verify_workbook", "schema_marker"]

    # state: same rows and editor values; three new blank columns
    state = tab(gc, STATE_TAB)
    assert state[0] == list(STATE_HEADERS)
    assert [r[:17] for r in state[1:]] == state_before[1:] and all(r[17:] == ["", "", ""] for r in state[1:])
    assert state[2][STATE_HEADERS.index("tmdb_id_override")] == "1009"
    assert state[2][STATE_HEADERS.index("notes")] == "wrong film matched — editor fix"
    # backup holds the pre-upgrade content; integrity verifies
    assert [r[:17] for r in tab(gc, LKG_TAB)[1:]] == state_before[1:]
    assert StateStore(gc, "S", "check").open().action == "PROCEED"
    assert parse_meta(tab(gc, META_TAB))["state"]["generation"] == "2"

    # suggestions: original 17 columns byte-for-byte, Slack ts and feedback included
    sugg = tab(gc, "suggestions")
    assert [r[:17] for r in sugg] == [r[:17] for r in sugg_before]
    assert sugg[1][SUGGESTIONS_HEADERS.index("slack_ts")] == "1760540401.000100"
    assert sugg[1][SUGGESTIONS_HEADERS.index("thread_feedback")] == "Good — assign to Dana"
    ids = [r[17] for r in sugg[1:]]
    assert len(set(ids)) == 7 and ids[3].endswith("~2") and ids[4].startswith("legacy-")
    states = [r[18] for r in sugg[1:]]
    assert states[5] == "legacy_unknown" and states[0] == "posted"

    # untouched tabs; new tabs header-only; marker
    assert tab(gc, "Usage") == usage_before and tab(gc, "corpus_meta") == corpus_meta_before
    for name in ("identity_overrides", "weekend_history", "weekend_collections"):
        assert len(tab(gc, name)) == 1
    assert schema_version(gc, "S") == "2"
    assert check_sheets(gc, "S", collect_history=True).ok


def test_schema_marker_is_the_last_write(gc):
    M.apply(gc, "S", "mig-1")
    mutating = [c for c in gc.calls if c[1] in ("add_worksheet", "update", "batch_update", "append_rows", "batch_clear")]
    assert mutating[-1][0] == "faramir_meta"
    assert [c[0] for c in mutating].count("faramir_meta") == 2 and mutating[-2][0] == "faramir_meta"  # create + header/row


def test_apply_is_idempotent(gc):
    M.apply(gc, "S", "mig-1")
    snapshot = {name: [list(r) for r in tab(gc, name)] for name in tabs(gc)}
    n = len(gc.writes())
    assert M.apply(gc, "S", "mig-2") == {"applied": [], "already_migrated": True}
    assert len(gc.writes()) == n and {name: tab(gc, name) for name in tabs(gc)} == snapshot


@pytest.mark.parametrize("crash_tab, after", [
    ("suggestions", 0),       # during the suggestions cell update
    ("state", 0),             # during the state upgrade write
    ("weekend_history", 0),   # while creating a new tab's header
    ("faramir_meta", 0),      # writing the marker itself
])
def test_crash_at_any_step_leaves_preflight_failing_and_rerun_completes(gc, crash_tab, after):
    real_add = gc.book("S").add_worksheet
    method = "batch_update" if crash_tab == "suggestions" else "update"

    if crash_tab in ("weekend_history", "faramir_meta"):
        def add(title, *a, **k):
            ws = real_add(title, *a, **k)
            if title == crash_tab:
                ws.fail_on("update", Crash())
            return ws
        gc.book("S").add_worksheet = add
    else:
        gc.book("S").worksheet(crash_tab).fail_on(method, Crash(), after=after)
    with pytest.raises(Crash):
        M.apply(gc, "S", "mig-1")
    assert schema_version(gc, "S") != "2" and not check_sheets(gc, "S").ok   # never a usable half-migration
    gc.book("S").add_worksheet = real_add
    if crash_tab == "state":
        # The interrupted state write is recovered by the normal protocol before re-running.
        s = StateStore(gc, "S", "recover")
        assert s.open().action in ("RESTORE", "ROLL_FORWARD")
        s.acquire_lock()
        s.recover()
        s.release_lock()
    M.apply(gc, "S", "mig-2")
    assert schema_version(gc, "S") == "2" and check_sheets(gc, "S", collect_history=True).ok
    assert tab(gc, STATE_TAB)[2][STATE_HEADERS.index("tmdb_id_override")] == "1009"


def test_dry_run_apply_records_and_writes_nothing(gc, tmp_path):
    ctx = runtime.RunContext(mode=runtime.DRY_RUN, enabled=False, run_id="dry", out_dir=str(tmp_path))
    with runtime.use(ctx):
        result = M.apply(gc, "S", "dry")
    assert gc.writes() == [] and result["dry_run"] and result["marker"] == "not written in dry-run"
    assert {o["op"] for o in ctx.operations} >= {"migrate_suggestions_v2", "create_tab"}


@pytest.mark.no_run_context
def test_apply_requires_confirmation_and_enabled(gc, monkeypatch, tmp_path):
    from faramir import sheet

    monkeypatch.setattr(sheet, "get_sheet_client", lambda: gc)
    monkeypatch.setenv("FARAMIR_SHEET_ID", "S")
    monkeypatch.delenv("FARAMIR_ENABLED", raising=False)
    assert M.main(["--apply", "--confirm-sheet", "WRONG"]) == 2
    assert M.main(["--apply", "--confirm-sheet", "S", "--out-root", str(tmp_path)]) == 4  # unset means disabled
    assert gc.writes() == []


def test_migrated_workbook_runs_the_daily_pipeline(gc, monkeypatch, tmp_path):
    """After migration, a normal (fake) daily run passes preflight and proceeds."""
    M.apply(gc, "S", "mig-1")
    from faramir.preflight import check_settings

    assert check_sheets(gc, "S").ok
    env = {"FARAMIR_ENABLED": "true", **{k: "x" for k in ("FARAMIR_SHEET_ID", "SLACK_WEBHOOK_URL", "SLACK_BOT_TOKEN",
                                                         "SLACK_CHANNEL_ID", "TMDB_API_KEY", "ANTHROPIC_API_KEY")}}
    assert check_settings(live=True, env=env).ok
