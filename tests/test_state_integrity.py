"""Increment 4: state integrity — checksums, decisions, the write protocol,
every write interruption, editor columns and concurrency (StateStore level)."""

from datetime import datetime, timedelta, timezone

import pytest

from faramir import runtime
from faramir.sheet import STATE_HEADERS
from faramir.state_integrity import (
    ABORT, LKG_TAB, META_TAB, PROCEED, RESTORE, ROLL_FORWARD, STATE_TAB,
    checksum, decide, initial_meta, merge_editor_values, parse_meta, row_values, split,
)
from faramir.state_store import StateConflict, StateRecoveryRefused, StateStore, StateWriteError
from tests.fakes.fake_gspread import FakeClient, make_api_error
from tests.fakes.state_book import init_book, state_row

H = list(STATE_HEADERS)
NOW = datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)


class SimulatedCrash(Exception):
    pass


def rows3():
    return [state_row(1, "One"), state_row(2, "Two"), state_row(3, "Three")]


def book_with(rows=None):
    gc = FakeClient()
    init_book(gc.book("S"), rows or rows3())
    return gc


def tab(gc, name):
    return gc.book("S").worksheet(name)


def meta(gc):
    return parse_meta(tab(gc, META_TAB).data)


def store(gc, run_id="run-A", now=NOW):
    return StateStore(gc, "S", run_id, now_fn=lambda: now)


def run_write(gc, new_rows, run_id="run-A", now=NOW):
    """One normal run: open, lock, write, release (as run_daily does)."""
    s = store(gc, run_id, now)
    d = s.open()
    assert d.action == PROCEED, d.reason
    s.acquire_lock()
    try:
        return s.write(new_rows)
    finally:
        s.release_lock()


def crash_run(gc, new_rows, run_id="run-A", release=True):
    """A run that crashes inside write(); its lock is released as run_daily's
    exit cleanup would (release=False models a killed process)."""
    s = store(gc, run_id)
    assert s.open().action == PROCEED
    s.acquire_lock()
    with pytest.raises(SimulatedCrash):
        s.write(new_rows)
    if release:
        s.release_lock()
    return s


def recover_run(gc, run_id="run-R", now=NOW):
    s = store(gc, run_id, now)
    d = s.open()
    if d.action in (RESTORE, ROLL_FORWARD):
        s.acquire_lock()
        try:
            msg = s.recover()
        finally:
            s.release_lock()
        return d, msg
    return d, None


def system_view(values):
    header, rows = split(values)
    return checksum(header, rows), len(rows)


def bumped(rows, dom_delta=1_000_000):
    out = []
    for r in rows:
        r = dict(r)
        r["domestic_yesterday"] = r["domestic_today"]
        r["domestic_today"] = r["domestic_today"] + dom_delta
        out.append(r)
    return out


# ── checksum and validation ─────────────────────────────────────────────

def test_checksum_ignores_editor_columns_and_row_order():
    v = row_values(H, rows3())
    edited = [list(r) for r in v]
    edited[0][H.index("tmdb_id_override")] = "12345"
    edited[1][H.index("notes")] = "check title"
    assert checksum(H, v) == checksum(H, edited) == checksum(H, list(reversed(v)))
    changed = [list(r) for r in v]
    changed[0][H.index("domestic_today")] = "1"
    assert checksum(H, changed) != checksum(H, v)


@pytest.mark.parametrize("mutate, problem", [
    (lambda r: r.__setitem__(H.index("tmdb_id"), "abc"), "not an integer"),
    (lambda r: r.__setitem__(H.index("status"), "paused"), "status"),
    (lambda r: r.__setitem__(H.index("domestic_today"), "-5"), "not a non-negative integer"),
    (lambda r: r.__setitem__(H.index("domestic_today"), "1"), "domestic_today < domestic_yesterday"),
])
def test_row_validation(mutate, problem):
    from faramir.state_integrity import row_problems

    v = row_values(H, rows3())
    mutate(v[0])
    assert any(problem in p for p in row_problems(H, v, H))


def test_duplicate_tmdb_id_is_invalid():
    from faramir.state_integrity import row_problems

    v = row_values(H, rows3())
    v[1][H.index("tmdb_id")] = v[0][H.index("tmdb_id")]
    assert any("duplicate" in p for p in row_problems(H, v, H))


# ── decision table ──────────────────────────────────────────────────────

def _vals(rows):
    return [H] + row_values(H, rows)


def _meta(state_rows, lkg_rows=None, **state_changes):
    m = initial_meta(_vals(state_rows), NOW.isoformat(), "init")
    if lkg_rows is not None:
        lm = initial_meta(_vals(lkg_rows), NOW.isoformat(), "init")
        m["lkg"] = lm["lkg"]
    m["state"].update({k: str(v) for k, v in state_changes.items()})
    return m


def test_decide_not_initialized():
    d = decide(_vals(rows3()), None, None, H, "r", NOW)
    assert d.action == ABORT and d.reason.startswith("not_initialized")


def test_decide_valid_state_proceeds():
    assert decide(_vals(rows3()), _vals(rows3()), _meta(rows3()), H, "r", NOW).action == PROCEED


def test_decide_unexplained_mismatch_aborts():
    m = _meta(rows3())
    tampered = rows3()
    tampered[0]["domestic_today"] = 999_999_999
    d = decide(_vals(tampered), _vals(rows3()), m, H, "r", NOW)
    assert d.action == ABORT and d.reason.startswith("unexplained_mismatch")


def test_decide_interrupted_write_with_backup_restores():
    new = bumped(rows3())
    m = _meta(rows3(), generation=2, status="writing", checksum=checksum(H, row_values(H, new)))
    mixed = new[:1] + rows3()[1:]  # first row new, rest old
    d = decide(_vals(mixed), _vals(rows3()), m, H, "r", NOW)
    assert d.action == RESTORE


def test_decide_completed_but_unconfirmed_write_rolls_forward():
    new = bumped(rows3())
    m = _meta(rows3(), generation=2, status="writing", checksum=checksum(H, row_values(H, new)), row_count=3)
    assert decide(_vals(new), _vals(rows3()), m, H, "r", NOW).action == ROLL_FORWARD


def test_decide_interrupted_write_without_valid_backup_aborts():
    m = _meta(rows3(), generation=2, status="writing", checksum="x")
    m["lkg"]["checksum"] = "corrupt"
    d = decide(_vals(rows3()[:1]), _vals(rows3()), m, H, "r", NOW)
    assert d.action == ABORT and "without_valid_backup" in d.reason


def test_decide_backup_generation_mismatch_aborts():
    m = _meta(rows3(), generation=5, status="writing", checksum="x")  # backup is gen 1, needs 4
    d = decide(_vals(rows3()[:1]), _vals(rows3()), m, H, "r", NOW)
    assert d.action == ABORT and "backup_generation_mismatch" in d.reason


def test_decide_lock_held_by_other_run_aborts_until_expiry():
    m = _meta(rows3())
    m["lock"].update(status="held", run_id="other", expires_at=(NOW + timedelta(minutes=10)).isoformat())
    assert decide(_vals(rows3()), _vals(rows3()), m, H, "r", NOW).reason.startswith("concurrent_run")
    later = NOW + timedelta(minutes=11)
    assert decide(_vals(rows3()), _vals(rows3()), m, H, "r", later).action == PROCEED


# ── normal write protocol ───────────────────────────────────────────────

def test_first_write_skips_current_backup_and_never_clears():
    gc = book_with()
    assert run_write(gc, bumped(rows3())) == 2
    assert "clear" not in [c[1] for c in gc.writes()]
    m = meta(gc)
    assert (m["state"]["generation"], m["state"]["status"]) == ("2", "ok")
    assert m["lkg"]["generation"] == "1"  # backup of gen 1 was already current
    assert m["lock"]["status"] == "released"
    assert system_view(tab(gc, STATE_TAB).data)[0] == m["state"]["checksum"]


def test_second_write_refreshes_backup_from_validated_state():
    gc = book_with()
    run_write(gc, bumped(rows3()))
    gen2 = [list(r) for r in tab(gc, STATE_TAB).data]
    run_write(gc, bumped(bumped(rows3())))
    m = meta(gc)
    assert (m["state"]["generation"], m["lkg"]["generation"]) == ("3", "2")
    assert tab(gc, LKG_TAB).data == gen2  # backup == the gen-2 state this run validated


def test_shrinking_state_trims_leftover_rows():
    gc = book_with()
    run_write(gc, rows3()[:2])
    _, n = system_view(tab(gc, STATE_TAB).data)
    assert n == 2 and "batch_clear" in [c[1] for c in gc.writes()]


def test_dry_run_write_records_and_touches_nothing(tmp_path):
    gc = book_with()
    ctx = runtime.RunContext(mode=runtime.DRY_RUN, enabled=False, run_id="dry", out_dir=str(tmp_path))
    with runtime.use(ctx):
        s = store(gc, "dry")
        assert s.open().action == PROCEED
        s.acquire_lock()
        s.write(bumped(rows3()))
    assert gc.writes() == []
    ops = [o["op"] for o in ctx.operations]
    assert ops == ["write_state_meta", "write_state_meta", "write_state", "write_state_meta"]
    assert all(o["outcome"] == "recorded" for o in ctx.operations)


def test_halted_run_cannot_write_state():
    gc = book_with()
    s = store(gc)
    assert s.open().action == PROCEED
    runtime.current().halt("corpus ABORT")
    with pytest.raises(runtime.WriteBlocked):
        s.acquire_lock()
    assert gc.writes() == []


# ── every write interruption ────────────────────────────────────────────

def at_gen2():
    gc = book_with()
    run_write(gc, bumped(rows3()))  # gen 2; backup still gen 1
    return gc, [list(r) for r in tab(gc, STATE_TAB).data]


def test_crash_before_backup_is_created():
    gc, gen2 = at_gen2()
    lkg_before = [list(r) for r in tab(gc, LKG_TAB).data]
    tab(gc, META_TAB).fail_on("update", SimulatedCrash(), after=1)  # lock ok, then "lkg writing" crashes
    crash_run(gc, bumped(bumped(rows3())))
    assert tab(gc, STATE_TAB).data == gen2 and tab(gc, LKG_TAB).data == lkg_before
    d, _ = recover_run(gc)
    assert d.action == PROCEED  # nothing was changed; next run is normal
    assert run_write(gc, bumped(bumped(rows3())), run_id="run-B") == 3


def test_crash_during_backup_creation_never_corrupts_the_backup_source():
    gc, gen2 = at_gen2()
    tab(gc, LKG_TAB).fail_on("update", SimulatedCrash(), partial=2)  # half-written backup
    crash_run(gc, bumped(bumped(rows3())))
    assert meta(gc)["lkg"]["status"] == "writing" and tab(gc, STATE_TAB).data == gen2
    d, _ = recover_run(gc)
    assert d.action == PROCEED  # state untouched and valid
    run_write(gc, bumped(bumped(rows3())), run_id="run-B")
    assert tab(gc, LKG_TAB).data == gen2  # rebuilt only from validated state
    assert meta(gc)["lkg"]["status"] == "ok"


def test_crash_during_state_update_restores_backup_and_next_run_is_normal():
    gc, gen2 = at_gen2()
    tab(gc, STATE_TAB).fail_on("update", SimulatedCrash(), partial=2)  # header + 1 new row written
    crash_run(gc, bumped(bumped(rows3())))
    assert meta(gc)["state"]["status"] == "writing"
    d, msg = recover_run(gc)
    assert d.action == RESTORE and "generation 2" in msg
    assert tab(gc, STATE_TAB).data == gen2
    m = meta(gc)
    assert (m["state"]["status"], m["state"]["generation"]) == ("restored", "2")
    # Subsequent normal run after recovery.
    assert run_write(gc, bumped(bumped(rows3())), run_id="run-C") == 3
    assert meta(gc)["state"]["status"] == "ok"


def test_crash_during_cleanup_of_leftover_rows_restores():
    gc, gen2 = at_gen2()
    tab(gc, STATE_TAB).fail_on("batch_clear", SimulatedCrash())
    crash_run(gc, bumped(rows3())[:2])  # shrink: rows written, leftover row not cleared
    d, _ = recover_run(gc)
    assert d.action == RESTORE
    assert tab(gc, STATE_TAB).data == gen2


def test_crash_before_generation_confirmation_rolls_forward_without_rewriting():
    gc, _ = at_gen2()
    # meta updates in this run: lock, lkg writing, lkg ok, state writing, state ok (crash)
    tab(gc, META_TAB).fail_on("update", SimulatedCrash(), after=4)
    target = bumped(bumped(rows3()))
    crash_run(gc, target)
    written = [list(r) for r in tab(gc, STATE_TAB).data]
    d, msg = recover_run(gc)
    assert d.action == ROLL_FORWARD and "generation 3" in msg
    assert tab(gc, STATE_TAB).data == written
    assert (meta(gc)["state"]["status"], meta(gc)["state"]["generation"]) == ("ok", "3")


def test_crash_during_automatic_recovery_retries_safely():
    gc, gen2 = at_gen2()
    tab(gc, STATE_TAB).fail_on("update", SimulatedCrash(), partial=2)
    crash_run(gc, bumped(bumped(rows3())))
    tab(gc, STATE_TAB).fail_on("update", SimulatedCrash(), partial=1)  # the restore itself crashes
    s = store(gc, "run-R1")
    assert s.open().action == RESTORE
    s.acquire_lock()
    with pytest.raises(SimulatedCrash):
        s.recover()
    s.release_lock()
    assert meta(gc)["state"]["status"] == "restoring"
    d, _ = recover_run(gc, "run-R2")
    assert d.action == RESTORE and tab(gc, STATE_TAB).data == gen2
    assert run_write(gc, bumped(bumped(rows3())), run_id="run-C") == 3


def test_readback_mismatch_leaves_write_marked_and_is_recovered():
    gc, gen2 = at_gen2()
    ws = tab(gc, STATE_TAB)
    original_update = ws.update

    def corrupting_update(values=None, range_name=None, **kw):
        result = original_update(values=values, range_name=range_name, **kw)
        ws.rows[1][H.index("domestic_today")] = "7"  # silent corruption after the API call
        return result

    ws.update = corrupting_update
    s = store(gc)
    s.open()
    s.acquire_lock()
    with pytest.raises(StateWriteError):
        s.write(bumped(bumped(rows3())))
    s.release_lock()
    ws.update = original_update
    assert meta(gc)["state"]["status"] == "writing"
    d, _ = recover_run(gc)
    assert d.action == RESTORE and tab(gc, STATE_TAB).data == gen2


def test_killed_process_keeps_lock_until_expiry():
    gc, gen2 = at_gen2()
    tab(gc, STATE_TAB).fail_on("update", SimulatedCrash(), partial=2)
    crash_run(gc, bumped(bumped(rows3())), release=False)
    d, _ = recover_run(gc, now=NOW + timedelta(minutes=5))
    assert d.action == ABORT and d.reason.startswith("concurrent_run")
    d, _ = recover_run(gc, now=NOW + timedelta(hours=1))
    assert d.action == RESTORE and tab(gc, STATE_TAB).data == gen2


def test_backup_is_never_replaced_with_corrupted_state():
    gc, gen2 = at_gen2()
    lkg_before = [list(r) for r in tab(gc, LKG_TAB).data]
    tab(gc, STATE_TAB).rows[1][H.index("domestic_today")] = "1"  # tampered, meta still "ok"
    d, _ = recover_run(gc)
    assert d.action == ABORT and d.reason.startswith("unexplained_mismatch")
    assert tab(gc, LKG_TAB).data == lkg_before
    s = store(gc, "run-X")
    s.open()
    with pytest.raises(RuntimeError):
        s.write(bumped(rows3()))  # write() refuses without a PROCEED decision
    assert tab(gc, LKG_TAB).data == lkg_before


def test_read_error_is_not_an_empty_state():
    from faramir.sheet import SheetReadError

    gc = book_with()
    gc.book("S").fail_worksheet(STATE_TAB, make_api_error())
    with pytest.raises(SheetReadError):
        store(gc).open()
    assert gc.writes() == []


def test_uninitialized_workbook_is_never_initialized_by_the_daily_store():
    gc = FakeClient()
    gc.book("S").add_tab(STATE_TAB, _vals(rows3()))
    d = store(gc).open()
    assert d.action == ABORT and d.reason.startswith("not_initialized")
    assert gc.writes() == [] and not any(c[1] == "add_worksheet" for c in gc.calls)


# ── editor-maintained columns ───────────────────────────────────────────

OV, NOTES = H.index("tmdb_id_override"), H.index("notes")


def test_editor_change_between_runs_is_kept_and_not_corruption():
    gc = book_with()
    tab(gc, STATE_TAB).rows[2][OV] = "424242"  # editor fixes row for tmdb 2
    run_write(gc, bumped(rows3()))  # pipeline rows carry the old (blank) value
    assert tab(gc, STATE_TAB).rows[2][OV] == "424242"


def test_editor_change_during_a_run_is_kept():
    gc = book_with()
    s = store(gc)
    assert s.open().action == PROCEED
    s.acquire_lock()
    tab(gc, STATE_TAB).rows[1][NOTES] = "edited mid-run"
    s.write(bumped(rows3()))
    s.release_lock()
    assert tab(gc, STATE_TAB).rows[1][NOTES] == "edited mid-run"


def test_backup_carries_editor_values():
    gc = book_with()
    run_write(gc, bumped(rows3()))
    tab(gc, STATE_TAB).rows[3][OV] = "777"
    run_write(gc, bumped(bumped(rows3())), run_id="run-B")
    lkg = tab(gc, LKG_TAB).data
    assert lkg[3][OV] == "777"


def test_restore_keeps_live_editor_values():
    gc, _ = at_gen2()
    tab(gc, STATE_TAB).rows[1][OV] = "555"  # editor fix made after gen 2
    tab(gc, STATE_TAB).fail_on("update", SimulatedCrash(), partial=3)  # rows 1-2 rewritten (editor value merged in)
    crash_run(gc, bumped(bumped(rows3())))
    d, msg = recover_run(gc)
    assert d.action == RESTORE
    assert tab(gc, STATE_TAB).rows[1][OV] == "555"
    assert "editor value" in msg


def test_restore_refused_on_conflicting_editor_values():
    gc, _ = at_gen2()
    tab(gc, STATE_TAB).fail_on("update", SimulatedCrash(), partial=2)
    crash_run(gc, bumped(bumped(rows3())))
    ws = tab(gc, STATE_TAB)
    dup = list(ws.rows[1])
    dup[OV] = "111"
    ws.rows[1][OV] = "222"
    ws.rows.append(dup)  # same tmdb_id twice with different editor values
    s = store(gc, "run-R")
    assert s.open().action == RESTORE
    s.acquire_lock()
    before = [list(r) for r in ws.rows]
    with pytest.raises(StateRecoveryRefused, match="conflicting editor values"):
        s.recover()
    assert ws.rows == before  # nothing written


def test_restore_refused_when_editor_value_would_be_lost():
    gc, _ = at_gen2()
    tab(gc, STATE_TAB).fail_on("update", SimulatedCrash(), partial=2)
    crash_run(gc, bumped(bumped(rows3())) + [state_row(4, "Four")])
    # An editor annotates film 4, which exists only in the interrupted write.
    tab(gc, STATE_TAB).rows.append(row_values(H, [state_row(4, "Four", notes="new film note")])[0])
    s = store(gc, "run-R")
    assert s.open().action == RESTORE
    s.acquire_lock()
    with pytest.raises(StateRecoveryRefused, match="would be lost"):
        s.recover()


def test_merge_editor_values_unit():
    base = row_values(H, rows3())
    live = [H] + [list(r) for r in base]
    live[1][OV] = "9"
    merged, conflicts, kept = merge_editor_values(H, base, live, strict=True)
    assert merged[0][OV] == "9" and conflicts == [] and kept == 1


# ── concurrency ─────────────────────────────────────────────────────────

def test_two_runs_on_the_same_generation_only_one_writes():
    gc = book_with()
    a, b = store(gc, "scheduled"), store(gc, "manual")
    assert a.open().action == PROCEED and b.open().action == PROCEED  # both read gen 1
    a.acquire_lock()
    with pytest.raises(StateConflict, match="held by scheduled"):
        b.acquire_lock()
    a.write(bumped(rows3()))
    a.release_lock()
    b.acquire_lock()  # lock is free now, but b's view of the state is stale
    before = [list(r) for r in tab(gc, STATE_TAB).data]
    with pytest.raises(StateConflict, match="state changed since this run opened it"):
        b.write(bumped(rows3(), dom_delta=5))
    assert tab(gc, STATE_TAB).data == before and meta(gc)["state"]["generation"] == "2"


def test_lost_lock_race_is_detected():
    gc = book_with()
    s = store(gc, "mine")
    s.open()
    real = s._read_meta
    calls = {"n": 0}

    def racing_read():
        m = real()
        calls["n"] += 1
        if calls["n"] == 2:  # the confirm re-read sees another run's lock
            m["lock"]["run_id"] = "theirs"
        return m

    s._read_meta = racing_read
    with pytest.raises(StateConflict, match="lost the lock race"):
        s.acquire_lock()


def test_write_without_lock_is_refused():
    gc = book_with()
    s = store(gc)
    s.open()
    with pytest.raises(StateConflict, match="does not hold the state lock"):
        s.write(bumped(rows3()))
    assert [c for c in gc.writes() if c[0] == STATE_TAB] == []
