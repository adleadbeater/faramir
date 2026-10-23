"""Increment 4: recovery through the real daily entry point, plus the init and
restore scripts — all on fakes, no credentials or network."""

import importlib.util
import json
import os

import pytest

from faramir.sheet import STATE_HEADERS
from faramir.state_integrity import LKG_TAB, META_TAB, STATE_TAB, parse_meta, row_values
from faramir.state_store import StateStore
from tests.fakes.fake_anthropic import FakeAnthropic
from tests.fakes.fake_gspread import FakeClient, make_api_error
from tests.fakes.state_book import state_row
from tests.test_run_daily_corpus import ROOT, V2_PATH, env, pick_first_candidate  # noqa: F401
from tests.test_run_daily_runtime import artifacts, main, posts
from tests.test_state_integrity import SimulatedCrash

H = list(STATE_HEADERS)


def book(env):
    return env["gc"].book("S")


def interrupt_a_write(env, partial=2):
    """Leave the env workbook as a crashed write would: state 'writing', rows mixed."""
    gc = env["gc"]
    s = StateStore(gc, "S", "crashed-run")
    s.open()
    s.acquire_lock()
    rows = [state_row(5001, "Beta Story", imdb_id="tt5001001", dom_today=150_000_000, dom_yest=140_000_000)]
    book(env).worksheet(STATE_TAB).fail_on("update", SimulatedCrash(), partial=partial)
    with pytest.raises(SimulatedCrash):
        s.write(rows)
    s.release_lock()


# ── recovery run through run_daily ──────────────────────────────────────

def test_recovery_run_restores_and_posts_no_picks_then_next_run_is_normal(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    good = [list(r) for r in book(env).worksheet(STATE_TAB).data]
    book(env).worksheet(STATE_TAB).rows[1][H.index("tmdb_id_override")] = "999"  # editor fix
    interrupt_a_write(env, partial=1)  # only the header landed; row still has the override
    fake = FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])

    assert main(env) == 3
    assert fake.calls == [] and env["slack"].messages == []          # no ordinary picks
    (alert,) = env["slack"].webhook_posts
    assert "state recovered" in json.dumps(alert["payload"])
    restored = book(env).worksheet(STATE_TAB).data
    assert restored[1][H.index("domestic_today")] == good[1][H.index("domestic_today")]
    assert restored[1][H.index("tmdb_id_override")] == "999"           # editor value kept
    assert parse_meta(book(env).worksheet(META_TAB).data)["state"]["status"] == "restored"
    report, _, _ = artifacts(env)
    assert report["halted_reason"].startswith("recovery run")
    assert not [c for c in env["gc"].writes() if c[0] == "suggestions"]  # recovery logs no picks

    # The next healthy run operates normally.
    for p in list((env["tmp"] / "out").iterdir()):
        for f in p.iterdir():
            f.unlink()
        p.rmdir()
    assert main(env) == 0
    from tests.test_run_daily_corpus import pick_msgs

    assert len(pick_msgs(env["slack"])) == 1
    m = parse_meta(book(env).worksheet(META_TAB).data)
    assert m["state"]["status"] == "ok" and m["lock"]["status"] == "released"


def test_unexplained_mismatch_stops_with_zero_writes(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    book(env).worksheet(STATE_TAB).rows[1][H.index("domestic_today")] = "1"
    fake = FakeAnthropic([]).install(env["monkeypatch"])
    assert main(env) == 3
    assert env["gc"].writes() == [] and fake.calls == []
    (alert,) = env["slack"].webhook_posts
    assert "unexplained_mismatch" in json.dumps(alert["payload"])


def test_not_initialized_stops_and_creates_nothing(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    del book(env)._sheets[META_TAB]
    del book(env)._sheets[LKG_TAB]
    FakeAnthropic([]).install(env["monkeypatch"])
    assert main(env) == 3
    assert env["gc"].writes() == []
    assert "not_initialized" in json.dumps(env["slack"].webhook_posts[0]["payload"])


def test_state_read_error_stops_with_zero_writes(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    book(env).fail_worksheet(STATE_TAB, make_api_error())
    FakeAnthropic([]).install(env["monkeypatch"])
    assert main(env) == 3
    assert env["gc"].writes() == []


def test_concurrent_run_holding_the_lock_stops_with_zero_writes(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    other = StateStore(env["gc"], "S", "manual-dispatch")
    other.open()
    other.acquire_lock()
    writes_before = len(env["gc"].writes())
    FakeAnthropic([]).install(env["monkeypatch"])
    assert main(env) == 3
    assert len(env["gc"].writes()) == writes_before
    assert "concurrent_run" in json.dumps(env["slack"].webhook_posts[0]["payload"])


def test_empty_state_needs_explicit_cold_start(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    b = book(env)
    from faramir.state_integrity import initial_meta, meta_values

    b.add_tab(STATE_TAB, [H])
    b.add_tab(LKG_TAB, [H])
    b.add_tab(META_TAB, meta_values(initial_meta([H], "2026-10-21T00:00:00+00:00", "init")))
    FakeAnthropic([]).install(env["monkeypatch"])
    env["monkeypatch"].delenv("FARAMIR_COLD_START", raising=False)
    assert main(env) == 3
    assert parse_meta(b.worksheet(META_TAB).data)["lock"]["status"] == "released"
    assert b.worksheet(STATE_TAB).data == [H]


def test_dry_run_records_state_protocol_without_writing(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    assert main(env, "--dry-run") == 0
    assert env["gc"].writes() == []
    _, writes, _ = artifacts(env)
    ops = [w["op"] for w in writes if w["category"] == "sheets"]
    assert ops.count("write_state") == 1 and "write_state_meta" in ops
    assert all(w["outcome"] == "recorded" for w in writes)


# ── init_state_integrity.py ─────────────────────────────────────────────

def load_script(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def legacy_book(monkeypatch, tmp_path):
    """A production-like workbook before initialization: state tab only."""
    from faramir import sheet

    gc = FakeClient()
    gc.book("S").add_tab(STATE_TAB, [H] + row_values(H, [state_row(1, "One", tmdb_id_override="123"), state_row(2, "Two")]))
    gc.book("S").add_tab("suggestions")
    monkeypatch.setattr(sheet, "get_sheet_client", lambda: gc)
    monkeypatch.setenv("FARAMIR_SHEET_ID", "S")
    return gc, tmp_path


def test_init_plan_is_read_only(legacy_book, capsys):
    gc, _ = legacy_book
    assert load_script("init_state_integrity").main([]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["mode"] == "fresh" and plan["state_rows"] == 2 and plan["state_tab_written"] is False
    assert gc.writes() == []


def test_init_apply_requires_confirmation_and_enabled(legacy_book):
    gc, tmp = legacy_book
    init = load_script("init_state_integrity")
    assert init.main(["--apply", "--confirm-sheet", "WRONG"]) == 2
    assert init.main(["--apply", "--confirm-sheet", "S", "--out-root", str(tmp)]) == 4  # FARAMIR_ENABLED unset
    assert gc.writes() == []


def test_init_dry_run_records_only(legacy_book, monkeypatch):
    gc, tmp = legacy_book
    assert load_script("init_state_integrity").main(["--apply", "--dry-run", "--confirm-sheet", "S", "--out-root", str(tmp)]) == 0
    assert gc.writes() == []


def test_init_apply_creates_backup_then_meta_last_and_never_writes_state(legacy_book, monkeypatch):
    gc, tmp = legacy_book
    monkeypatch.setenv("FARAMIR_ENABLED", "true")
    state_before = [list(r) for r in gc.book("S").worksheet(STATE_TAB).data]
    assert load_script("init_state_integrity").main(["--apply", "--confirm-sheet", "S", "--out-root", str(tmp)]) == 0
    created = [c[0] for c in gc.calls if c[1] == "add_worksheet"]
    assert created == [LKG_TAB, META_TAB]
    assert gc.book("S").worksheet(STATE_TAB).data == state_before
    assert not [c for c in gc.writes() if c[0] == STATE_TAB]
    assert gc.book("S").worksheet(LKG_TAB).data == state_before        # editor value "123" included
    assert StateStore(gc, "S", "first-v2-run").open().action == "PROCEED"


def test_init_interrupted_before_meta_is_safe_and_rerunnable(legacy_book, monkeypatch):
    gc, tmp = legacy_book
    monkeypatch.setenv("FARAMIR_ENABLED", "true")
    init = load_script("init_state_integrity")
    gc.book("S").add_worksheet_failure = None
    real_add = gc.book("S").add_worksheet
    calls = {"n": 0}

    def add_then_fail(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise SimulatedCrash()
        return real_add(*a, **k)

    gc.book("S").add_worksheet = add_then_fail
    with pytest.raises(SimulatedCrash):
        init.main(["--apply", "--confirm-sheet", "S", "--out-root", str(tmp)])
    assert StateStore(gc, "S", "r").open().reason.startswith("not_initialized")  # half-done init is never used
    gc.book("S").add_worksheet = real_add
    assert init.main(["--apply", "--confirm-sheet", "S", "--out-root", str(tmp)]) == 0  # partial mode
    assert StateStore(gc, "S", "r").open().action == "PROCEED"


def test_init_refuses_invalid_state_or_reinitialization(legacy_book, monkeypatch):
    gc, tmp = legacy_book
    monkeypatch.setenv("FARAMIR_ENABLED", "true")
    init = load_script("init_state_integrity")
    gc.book("S").worksheet(STATE_TAB).rows[1][H.index("status")] = "paused"
    with pytest.raises(SystemExit, match="failed validation"):
        init.main(["--apply", "--confirm-sheet", "S", "--out-root", str(tmp)])
    gc.book("S").worksheet(STATE_TAB).rows[1][H.index("status")] = "active"
    assert init.main(["--apply", "--confirm-sheet", "S", "--out-root", str(tmp)]) == 0
    with pytest.raises(SystemExit, match="already exists"):
        init.main(["--apply", "--confirm-sheet", "S", "--out-root", str(tmp)])


def test_rollback_plan_is_printed_and_touches_nothing(legacy_book, capsys):
    gc, _ = legacy_book
    assert load_script("init_state_integrity").main(["--rollback-plan"]) == 0
    out = capsys.readouterr().out
    assert "Keep 'state_lkg'" in out and "never delete recovery records automatically" in out
    assert "delete the tabs" not in out
    assert gc.calls == []


# ── restore_state.py ────────────────────────────────────────────────────

@pytest.fixture
def initialized(legacy_book, monkeypatch):
    gc, tmp = legacy_book
    monkeypatch.setenv("FARAMIR_ENABLED", "true")
    load_script("init_state_integrity").main(["--apply", "--confirm-sheet", "S", "--out-root", str(tmp)])
    monkeypatch.delenv("FARAMIR_ENABLED")
    return gc, tmp


def test_restore_plan_is_read_only(initialized, capsys):
    gc, _ = initialized
    n = len(gc.writes())
    assert load_script("restore_state").main(["--from", "lkg"]) == 0
    assert json.loads(capsys.readouterr().out)["source_rows"] == 2
    assert len(gc.writes()) == n


def test_restore_apply_is_guarded_and_keeps_live_editor_values(initialized, monkeypatch):
    gc, tmp = initialized
    restore = load_script("restore_state")
    st = gc.book("S").worksheet(STATE_TAB)
    st.rows[2][H.index("notes")] = "editor note after backup"
    st.rows[1][H.index("domestic_today")] = "1"  # corruption to repair
    assert restore.main(["--from", "lkg", "--apply", "--confirm-sheet", "S", "--out-root", str(tmp)]) == 4  # disabled
    monkeypatch.setenv("FARAMIR_ENABLED", "true")
    assert restore.main(["--from", "lkg", "--apply", "--confirm-sheet", "S", "--out-root", str(tmp)]) == 0
    assert st.rows[1][H.index("domestic_today")] == gc.book("S").worksheet(LKG_TAB).rows[1][H.index("domestic_today")]
    assert st.rows[2][H.index("notes")] == "editor note after backup"
    assert st.rows[1][H.index("tmdb_id_override")] == "123"
    m = parse_meta(gc.book("S").worksheet(META_TAB).data)
    assert (m["state"]["status"], m["state"]["generation"]) == ("restored", "2")
    assert StateStore(gc, "S", "next").open().action == "PROCEED"


def test_restore_refuses_editor_conflicts_unless_source_chosen(initialized, monkeypatch, tmp_path):
    gc, tmp = initialized
    monkeypatch.setenv("FARAMIR_ENABLED", "true")
    st = gc.book("S").worksheet(STATE_TAB)
    st.rows.append(row_values(H, [state_row(3, "Three", notes="only in live")])[0])
    restore = load_script("restore_state")
    with pytest.raises(SystemExit, match="editor-value conflicts"):
        restore.main(["--from", "lkg", "--apply", "--confirm-sheet", "S", "--out-root", str(tmp)])
    assert restore.main(["--from", "lkg", "--editor-values", "source", "--apply", "--confirm-sheet", "S",
                         "--out-root", str(tmp)]) == 0


def test_restore_refuses_invalid_source(initialized, monkeypatch, tmp_path):
    gc, tmp = initialized
    monkeypatch.setenv("FARAMIR_ENABLED", "true")
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"header": H, "rows": [row_values(H, [state_row(1, "One", status="paused")])[0]]}))
    with pytest.raises(SystemExit, match="source failed validation"):
        load_script("restore_state").main(["--from-file", str(snap), "--apply", "--confirm-sheet", "S", "--out-root", str(tmp)])
