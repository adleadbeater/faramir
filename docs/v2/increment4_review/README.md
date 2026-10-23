# Increment 4: state integrity and recovery

**Code:**

| File | Role |
|---|---|
| `src/faramir/state_integrity.py` | Rules only, no Sheets access |
| `src/faramir/state_store.py` | The write protocol |
| `src/faramir/sheet.py` | Guarded read and write building blocks; `read_state` and `write_state` are removed |
| `scripts/init_state_integrity.py` | One-time initialization |
| `scripts/restore_state.py` | Manual restore |

The integration point is `scripts/run_daily.py`, Step 1a.

## Tabs

| Tab | Purpose | Who writes it |
|---|---|---|
| `state` | Working state. Editors may change only `tmdb_id_override` and `notes`. | The daily run (protocol below), restores |
| `state_lkg` | Last-known-good copy of a **validated** generation | The daily run, only from state it validated at open; init |
| `state_meta` | One row each for `state`, `lkg` and `lock`: generation, status, row count, checksum, run ID, lock expiry | Daily run, restores, init (created only by init) |

The checksum is SHA-256 over the system-owned columns, with rows sorted by `tmdb_id`. **Editor columns are excluded**, so an editor's change is never mistaken for corruption.

## Write protocol (Sheets has no multi-tab transactions)

1. **Concurrency check:** this run holds the lock, and the `state` generation and status are unchanged since open.
2. **Editor values** are taken from the **live** tab, read just before writing. This keeps edits made mid-run.
3. **Backup:** if `state_lkg` isn't already the validated generation g, refresh it.
   1. Set `lkg` meta to `writing`.
   2. Write the backup **from the state validated at open**, with live editor values.
   3. Read it back and verify.
   4. Set `lkg` meta to `ok`, generation g.
4. Set `state` meta to `writing`, generation g+1, with the expected checksum.
5. Overwrite `state` in place (never `clear()`), then trim leftover rows. Read it back and verify.
6. Set `state` meta to `ok`, generation g+1. A local `state_snapshot.json` is written for the run artifact.

## Recovery decision table

Evaluated at the start of every run, before any write.

| # | `state_meta` | State tab | Backup (`state_lkg`) | Decision | What the run does |
|---|---|---|---|---|---|
| 1 | Missing | — | — | **ABORT** `not_initialized` | Alert only; creates nothing |
| 2 | Lock held by another run, not expired | — | — | **ABORT** `concurrent_run` | Alert only |
| 3 | `ok` / `restored` | Valid, checksum matches | Any | **PROCEED** | Normal run |
| 4 | `ok` / `restored` | Doesn't match its confirmed generation | Any | **ABORT** `unexplained_mismatch` | Alert, zero writes, manual restore |
| 5 | `writing` | Complete and matches the *expected* checksum | Any | **ROLL_FORWARD** | Confirms the generation (no rewrite); **no picks**; alert |
| 6 | `writing` | Doesn't match | Valid, generation g | **RESTORE** | Restores the backup, keeping live editor values; **no picks**; alert |
| 7 | `restoring` | Any | Valid, target generation | **RESTORE** (retry) | Same as 6; the restore is idempotent |
| 8 | `writing` / `restoring` | Doesn't match | Invalid or missing | **ABORT** `…without_valid_backup` | Alert, zero writes, manual restore |
| 9 | `writing` / `restoring` | Doesn't match | Wrong generation | **ABORT** `backup_generation_mismatch` | Alert, zero writes, manual restore |
| 10 | RESTORE needed | — | — | Editor values ambiguous: conflicting duplicates, or values only on rows the backup lacks | Refused, then ABORT; a human decides |
| 11 | State read fails (API error, missing tab) | — | — | **ABORT** | Never treated as empty; zero writes |
| 12 | PROCEED, but state is empty | — | — | **ABORT** unless `FARAMIR_COLD_START=true` | Lock released |

Every recovery runs under the state lock and through the write guard. A recovery run then **halts**: no Claude call, no picks, no header. Normal operation resumes on the next healthy run.

## Simulated interruptions

All tested in `tests/test_state_integrity.py`. "Next run" means a fresh run with a different run ID.

| Interruption point | State after the crash | Next run |
|---|---|---|
| Before the backup is created (meta update to `lkg writing` fails) | Unchanged | PROCEED, normal write to gen 3 |
| During backup creation (half-written `state_lkg`) | Unchanged; `lkg` shows `writing` | PROCEED; backup rebuilt **from validated state** (asserted equal to gen 2) |
| During the state update (only some rows written) | Mixed rows; `state` shows `writing` | RESTORE to gen 2, no picks; then the next run writes gen 3 normally |
| During cleanup of leftover rows | Extra rows remain | RESTORE to gen 2 |
| Before generation confirmation (final meta update fails) | All rows written; `writing` | ROLL_FORWARD to gen 3 without rewriting; no picks |
| During automatic recovery (restore write fails) | `restoring` | RESTORE retried and succeeds; next run normal |
| Subsequent normal run after recovery | `restored` | PROCEED and write a new generation, status `ok` |
| Read-back verification fails (silent corruption) | `writing` | RESTORE |
| Process killed (lock not released) | Lock held | ABORT `concurrent_run` until the lock expires (45 min), then RESTORE |
| State tampered while marked `ok` | — | ABORT; backup **unchanged**, and `write()` refuses |

The test `test_backup_is_never_replaced_with_corrupted_state`, together with the backup-crash test, shows the backup is only ever written from validated state.

## Editor columns (`tmdb_id_override`, `notes`)

These are the only manually editable fields: `tmdb_id_override` is the one the README tells editors to use, and `notes` is free text. Every other column is overwritten by the pipeline each run. Tests:

| Test | Shows |
|---|---|
| `test_editor_change_between_runs_is_kept_and_not_corruption` | Change is kept; the checksum still validates |
| `test_editor_change_during_a_run_is_kept` | An edit made after open and before the write is kept |
| `test_backup_carries_editor_values` | The backup includes editor values |
| `test_restore_keeps_live_editor_values` | Auto-restore keeps the live value (message reports the count) |
| `test_recovery_run_restores_and_posts_no_picks_then_next_run_is_normal` | End to end: override `999` survives a recovery run |
| `test_restore_refused_on_conflicting_editor_values` | Duplicate rows with different values: refused, nothing written |
| `test_restore_refused_when_editor_value_would_be_lost` | A note on a row the backup lacks: refused |
| `restore_state.py` tests | Keeps live values by default; a conflict needs an explicit `--editor-values source` |
| `test_init_apply_creates_backup_then_meta_last_and_never_writes_state` | Init copies `tmdb_id_override=123` into the backup |

## Concurrency

**The scenario:** a scheduled run and a manual dispatch both open generation g.

**Sheets-level safeguard** (implemented and tested): an advisory lock row in `state_meta`.
- **Acquire:** refused if another run holds an unexpired lock. Taking the lock is followed by a confirm re-read, which detects a lost race.
- **Before writing:** the run re-checks both the generation and lock ownership. A stale run gets `StateConflict` and writes nothing.
- **Release:** at exit, through the run's exit cleanup.
- **Expiry:** 45 minutes, which is longer than the 15-minute workflow timeout.

**GitHub Actions safeguard (proposed, not applied).** Add to `daily.yml`, `corpus_refresh.yml`, and any future `migrate`/`restore` workflows:

```yaml
concurrency:
  group: faramir-production
  cancel-in-progress: false
```

This serializes scheduled and manual runs. Note that GitHub keeps only **one** pending run per group, so a newer queued run replaces an older queued one. That's acceptable for a daily job. Local CLI runs bypass Actions concurrency, which is why the Sheets lock exists.

**Remaining limitations:**
- Sheets has no compare-and-swap, so a window of a few seconds remains between the confirm re-read and the meta write. The Actions group closes it for workflow runs.
- A run killed mid-write holds the lock until it expires (fails safe: the next run stops rather than racing).
- Writes to the suggestions tab aren't covered by the lock until Increments 5 and 6.

## Production migration (stage S5, after merge, while `FARAMIR_ENABLED` is unset or false)

Old code's `write_state` clears the state tab and rewrites it without updating `state_meta`. If initialization ran while old code was still scheduled, the next v2 run would see an unexplained mismatch and stop. That's safe, but noisy. So initialization runs only **after** the merge, while scheduled runs are disabled.

1. `python scripts/init_state_integrity.py` (**plan, read-only**). Review the mode (`fresh`), row count, problems (must be none) and checksum.
2. `python scripts/init_state_integrity.py --dry-run --apply --confirm-sheet <ID>` writes the proposed operations to `out/`.
3. `FARAMIR_ENABLED=true python scripts/init_state_integrity.py --apply --confirm-sheet <ID>`:
   1. creates `state_lkg` as an exact copy and verifies it;
   2. creates `state_meta` **last**.

   `state` is never written. A crash before step 2 leaves no `state_meta`, so the daily run refuses to start, and re-running the script completes it in "partial" mode.
4. A daily `--dry-run` must show `State integrity: PROCEED`.

**Rollback** (`--rollback-plan` prints it):
1. Set `FARAMIR_ENABLED=false`.
2. Delete the `state_meta` and `state_lkg` tabs.
3. `state` was never touched by initialization.
4. Pre-v2 code ignores both tabs.

**Approval:** each of steps 2–3 and any rollback is a separate production action needing your approval. Nothing here has been run against production.
