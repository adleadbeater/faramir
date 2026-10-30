# Faramir 2.0 Phase 0: readiness assessment

Date: 2026-10-23. Branch `v2/phase-0` (local only; never pushed). Baseline: `main` @ `f2a0ce4`.

**Verdict: Phase 0 is code-complete and passes every offline check. It is not ready for production deployment.** Passing tests prove behavior on fakes and fixtures only. Nothing has run against live Sheets, Slack, Anthropic or GitHub Actions. Section 10 lists 11 items that must be closed first, all of them approvals or live verifications rather than coding. One item is time-sensitive and affects production *today*, independent of this branch: **the legacy corpus refresh scheduled on `main` for 2026-11-01** (PR-19).

---

## 1. Test results

```
python -m pytest -q        (Python 3.11.17, project venv, pinned dependencies, network blocked)
694 passed, 2 skipped
```

- **The 2 skipped tests are opt-in** (`--run-local-capture`). They run the parser on the real TN pages captured during the audit, which are not committed. Run locally with `FARAMIR_TN_CAPTURE_DIR`, both pass.
- **The original v1 tests** (`test_matcher.py`, `test_milestone.py`) pass. The only change to them is the approved giant-killer update (Increment 0).
- **What the tests do not prove:**
  - live API behavior: the Sheets formatting of values, Slack scopes, and the Anthropic structured-output acceptance of `claude-sonnet-4-6`;
  - GitHub Actions execution. The workflows are only statically checked.
  - editorial quality of the picks;
  - the validator's rejection rate on real past headlines.

## 2. Increments and local commits

| # | Increment | Commit |
|---|---|---|
| — | Plan, Phase 0 spec (rev 3), change summary, corpus audit | `5d442de` |
| 0 | Test harness, fakes, pinned SDK, giant-killer test fix | `3eb49dd` |
| 1 | TMDB-only corpus identity repair, schema v2, validator | `42508b4` |
| 2 | v2 corpus loading, health classes, missing-data and stale safeguards | `d53ff6e` |
| 3 | Run context, central write guard, dry-run, kill switch, offline mode | `9af58d0` |
| 4 | State integrity, last-known-good backup, recovery, run lock | `ca25ce4` |
| 5 | Idempotent suggestion appends, safe feedback updates | `7d8f957` |
| 6 | Slack delivery reliability (log → post → confirm) | `53fba32` |
| 7 | Structured Claude output, strict candidate binding, API limits | `edcb0c0` |
| 8 | Figure and claim validation, 85-character limit, prompt edits | `c680ab8` |
| 9 | Active-film identity resolution, plus the Increment 8 follow-ups | `5a9606c` |
| 10 | The Numbers chart parser (parse only) | `33e431a` |
| 11 | Weekend-history transaction protocol (collection off) | `2416f57` |
| 12 | Preflight, migration, workflows, own-lease expiry | the commit containing this file (`git log -1 -- docs/v2/phase0_acceptance/README.md`) |

Each increment has a review folder in `docs/v2/incrementN_review/`.

## 3. Spec §8 acceptance checklist

| Item | Status | Evidence |
|---|---|---|
| A1 Existing tests unmodified | ✔ | Only the approved giant-killer change |
| A2 Giant-killer genre-agnostic | ✔ | `test_matcher.py` |
| A3 Offline candidate set matches, differences listed | ✔ | §8 below; `candidate_comparison/` |
| A4 Milestone, suppression, stale and decrease parity | ✔ | Increment 2 parity tests |
| A5 Slack blocks identical except metadata and "n/a" | ✔ | Increments 2 and 6 |
| B1–B2, B4–B9 Corpus gates | ✔ | Increments 1 and 2 |
| B3 322 identities corrected, CSV reproduced | ✔ (offline) | **Your 20-row hand check: pending (approval item 7)** |
| C1 Friday URL · C2 parser exact (+ opt-in capture) · C3 H1–H12 | ✔ | Increments 10 and 11 |
| D1–D6 Failure scenarios | ✔ | Increments 4, 6, 7, 9, 10 and 11 |
| E1–E3 Binding, claim matrix, figures, 85 characters | ✔ | Increments 7 and 8 |
| **E4 Shadow comparison over at least 5 fixture days** | ✘ | **Only one fixture day exists.** Needs PR-12's replay and/or approved read-only live dry runs (blocker 3). |
| F1 Offline dry run makes zero writes | ✔ | §4 |
| F2 Unset `FARAMIR_ENABLED` means disabled; dry run while disabled | ✔ | `test_preflight.py`, `test_workflows.py` |
| F3 Preflight rejects every mismatch | ✔ | `test_preflight.py` (35 tests) |
| F4 Migration idempotent, marker last | ✔ | `test_migrate_sheets.py` |
| F5 No write outside the sinks | ✔ | `test_no_direct_writes.py` |
| **G1 Suite green on 3.11 in CI** | ½ | Green locally on 3.11.17. **Never run in GitHub Actions** (blocker 9). |

## 4. End-to-end offline dry run

Command: `python scripts/run_daily.py --dry-run --fixtures fixtures/offline/2026-10-22`, run with an emptied environment, no credentials and the network refused. Artifacts are in [`offline_dry_run/`](offline_dry_run/).

| Stage | Result |
|---|---|
| Preflight | ok: settings, corpus OK, Sheets schema v2 with every tab and header |
| State integrity | PROCEED |
| Corpus | OK: 2,011 usable, 0 exclusions on this day |
| Identity | Beta Story resolved (legacy); Alpha Film unresolved (`no_match`, header warning); Old Classic excluded as a re-release (labelled `late_release`) |
| Candidates / selection | 17 candidates; deterministic offline responder; 1 pick |
| Binding / validation | 1 bound (`candidate_id 5533665f…`); validation passed |
| Delivery | Logged → posted → confirmed (recorded): 1 pick and 1 header |
| Writes | **14 recorded, 0 performed**: 11 Sheets (meta, feedback, suggestions append, delivery states, state backup and write, usage) and 2 Slack messages (not sent) |
| Log | No errors or exceptions |

## 5. Migration from v1 to v2

The tool is `scripts/migrate_sheets.py`, also run through the manual `migrate.yml`. **Every apply is a separately approved production action.**

1. **Before:**
   - scheduled runs are already no-ops (`FARAMIR_ENABLED` unset or false after the merge);
   - make a full copy of the workbook;
   - confirm that the `state` lock is free (no run in progress).
2. **Rehearsal on the copy:**
   - plan (read-only), then review it;
   - dry-run apply;
   - apply on the copy;
   - check preflight against the copy;
   - check the lossless Slack timestamp (PR-8);
   - check Slack access with `verify_slack_access.py` (PR-3 / PR-10).
3. **Production:**
   - plan, then your review;
   - set `FARAMIR_MIGRATION_ENABLED=true`;
   - dispatch `migrate.yml` in `apply` mode with `confirm_sheet`;
   - set `FARAMIR_MIGRATION_ENABLED=false`.
4. **The seven idempotent steps:**
   1. validate;
   2. suggestions ids and delivery columns, with the original 17 columns untouched;
   3. `state_lkg`, then `state_meta`;
   4. state on 20 columns through the state protocol;
   5. header-only tabs: Usage (if missing), `identity_overrides`, `weekend_history`, `weekend_collections`;
   6. whole-workbook verification;
   7. **`faramir_meta.schema_version=2` last.**

   A crash at any step leaves preflight failing, and a re-run completes the migration (tested).
5. **After:**
   - run preflight in a dry run on `main` (stage S6);
   - keep the copy until v2 has run cleanly for a week.

## 6. Production activation sequence

Each stage needs your separate approval. The stages follow spec §9.2, updated for Increment 12.

| Stage | Action | Effect | Gate |
|---|---|---|---|
| **Now** | **Disable `Faramir Corpus Refresh` on `main`** (Actions → Disable workflow) | Prevents the 2026-11-01 legacy rebuild | Before 2026-11-01 09:00 UTC (PR-19) |
| S1 | Optional interim measures R1 (editor notice) and R3 (v1-compatible corpus hotfix) | — | Your decision |
| S2 | Push the branch, open a PR, **human code review** | None; PR tests run without secrets | Review approved; CI green (G1) |
| S3 | Create the variables `FARAMIR_ENABLED=false`, `FARAMIR_CORPUS_REFRESH_ENABLED=false`, `FARAMIR_MIGRATION_ENABLED=false` | None | PR-17 |
| S4 | Merge right after a 10:00 ET daily run | Scheduled runs become no-ops; picks pause | Next scheduled run is skipped |
| S5 | Migration rehearsal on a copy, then production (§5) | Workbook on schema v2 | Preflight passes |
| S6 | `daily.yml` dispatch with `dry_run=true`, using live reads | No writes; run artifact only | You review the artifact. PR-11 (structured output live) and PR-12 (validator replay) are closed. |
| S7 | `FARAMIR_ENABLED=true`, then optionally a supervised manual run | Production on v2 | First artifact and Slack output confirmed |
| S8 | Observe 3 scheduled runs | — | No alerts or aborts |

Corpus refresh, weekend-history collection and any backfill stay off after S7. Each needs the source-permission decision and its own approval.

## 7. Rollback and recovery

**Immediate (preferred).** Set `FARAMIR_ENABLED=false`. The next scheduled run is skipped by the workflow `if:`, and the code refuses as well. Nothing is lost.

**Run-level failures** need no action, because the code handles them:

- A preflight failure, state ABORT, lock conflict or expired lease stops the run with zero writes and one alert.
- An interrupted state write is recovered automatically on the next run, from the verified backup or by rolling forward. That run makes no picks.
- An ambiguous Slack delivery is never auto-retried. Use `replay_picks.py`, which needs approval.

**State restore.** `restore_state.py` restores from `state_lkg` or from a run artifact's snapshot. It is plan-first, keeps editor values, and needs approval.

**Full revert to v1** (`migrate_sheets.py --rollback-plan` prints these steps):

1. Set `FARAMIR_ENABLED=false`.
2. **Export the v2 suggestion columns first.** v1's feedback update clears the suggestions tab and rewrites it with 17 headers, erasing the ids and delivery states. Picks, Slack timestamps and feedback survive.
3. Revert the merge. That restores the v1 code and v1 corpus.
4. Archive (rename, never delete) `faramir_meta`, `state_meta` and the history tabs.
5. v1's first state write drops the three identity columns; rows and editor values are unchanged.
6. Moving forward again means re-running the migration.

Nothing is deleted automatically, at any step.

## 8. Original vs repaired recommendations

**Candidate level.** Offline, the same 250 probes (10 real chart films from 2026-10-21 plus a sweep) were run on the original code and corpus versus the repaired code and corpus, giving 2,038 candidates before and 2,025 after. The full output is in [`candidate_comparison/`](candidate_comparison/).

| Outcome | Count |
|---|---|
| Unchanged | 1,667 |
| **Same story, but the original named the wrong comparison film** (identity corrected) | **355**, involving 157 distinct comparison records |
| Removed: comparison gross stale (the film was still in release when grossed) | 14 |
| Removed: no longer surfaces once its metadata is corrected | 2 |
| Added: surfaces once its metadata is corrected | 3 |

Code parity: the new code on the old corpus produces exactly the old candidates (2,038 = 2,038). **Every difference comes from corrected data, not from changed editorial scoring.**

Examples from real chart films:

- *Resident Evil* was compared with "Paul Blart: Mall Cop 2 (2015)"; the correct film is *Paul Blart: Mall Cop* (2009).
- *Spider-Man: Brand New Day* was compared with its own still-running gross (stale), so that candidate is removed.

**Pick and headline level** (structural differences, by construction):

| Original v1 | Repaired v2 |
|---|---|
| 16% of comparison films mislabelled (322 of 2,011) | Identity by IMDb ID; 0 known mislabels |
| Active films matched by title alone (most popular result) | Year-, slug- and release-window-aware; ambiguous films are skipped and flagged |
| Picks could name films or comparisons that weren't candidates | Every pick bound to a real candidate and its evidence |
| Prompt invited rankings and "only/first" claims; no check of figures, tense or genre | Claims, figures, tense, direction, genre attribution and third-film mentions are validated; 85 characters, never truncated |
| Posted before logged (crash → duplicates); same-day re-run reposted | Log → post → confirm; idempotent |
| A Sheets read error looked like a cold start and wiped state | Integrity, backup, recovery and lock; read errors stop the run |
| Missing grosses read as $0 | Missing is never zero |

**Not yet measured:** how many real past headlines the new validator would reject, including any legitimate ones (PR-12). This is the main unknown about pick *volume*.

## 9. Outstanding issues, classified

### Deployment blockers

| # | Issue | Ref |
|---|---|---|
| 1 | **The legacy corpus refresh on `main` runs 2026-11-01 09:00 UTC.** It would rebuild `data/corpus.json` with title-only identities. This is independent of the branch and affects production now. Disable the workflow. | PR-19 / PR-5 / R2 |
| 2 | **Source-permission decision.** v2 does not expand collection, but it keeps production's existing daily TN chart fetch and BOM title-page fetch (worldwide grosses). Deploying v2 means continuing them; that needs your explicit decision. | §6 spec; licensing |
| 3 | **No historical validator replay or shadow comparison.** The rejection rate and the false positives on real headlines are unknown (E4). | PR-12 |
| 4 | **Structured output not verified live** on `claude-sonnet-4-6`. It fails safe, but if it fails every run produces no picks. | PR-11 |
| 5 | **Slack token scopes and channel identity not verified**, and lossless Slack timestamps not verified on a sheet copy | PR-3, PR-8, PR-10 |
| 6 | **Migration not rehearsed** on a copy of the real workbook | PR-7, PR-10a, PR-13 |
| 7 | **Workflow concurrency not verified** with two overlapping dispatches | PR-1 |
| 8 | **The `anthropic==2.1.0` pin not confirmed** against production's last install | PR-4 |
| 9 | **Workflows never executed** and CI never run (G1). The workflows are only statically tested; the first dispatch should be a dry run. | — |
| 10 | **No human code review** of the branch. It is about 13 increments of changes, each reviewed through summaries, not line by line. | — |
| 11 | **The suggestions tab is unprotected** (manual sorting or editing races) | PR-6 |

### Acceptable limitations

Each is safe: it fails closed, or it is flagged.

- **Validator:**
  - third-film detection has no alias table, and one-word titles under 7 letters are unchecked;
  - tense and direction rules use word lists;
  - a genre claim in `claims[]` doesn't name its film (the lexical rule does attribute it);
  - strict words like "first", "only" and "best" can drop a legitimate pick.
- **Shortening can change emphasis.** It is fully re-validated, and films, direction, status and figures are checked.
- **Identity:**
  - the 60-day window can leave platform or festival releases unresolved (flagged; an override fixes it);
  - a re-release of last year's film shows as unresolved;
  - TMDB search reads the first page only;
  - legacy identities are trusted when title and year agree (labelled `legacy`).
- **Late US releases of older films are excluded** pending your decision (PR-14).
- **No milestone freshness re-check** on the active film's figures.
- **Parser:**
  - exact footer reconciliation, and any markup change marks the chart `malformed` (fails safe);
  - the 365-day re-release threshold;
  - theaters are required.
- **History** (collection off):
  - reads both tabs in full;
  - the 48-hour orphan and Tuesday-confirmation rules are calibration values;
  - a source reverting to stale figures is followed until the next retrieval.
- **The legacy Monday "weekend" merge** is Sunday's daily chart, now labelled as such (PR-15).
- **Run artifacts** contain pick text and figures, kept 90 days (PR-18).
- **No `runs` tab.** Run records are artifacts.

### Future enhancements (Phase 1+)

- **SR-1** verified rankings, records and superlatives
- **SR-2** a legitimate-phrase regression suite (exists; extend it from PR-12)
- **SR-3** explicit genre-claim attribution (prompt change)
- **SR-4** meaning-preserving rewrites
- **SR-5** re-release data for Discovery
- **SR-6** ongoing legacy-identity verification
- **SR-7** revisit the 90-day eligibility rule per strategy
- **SR-8** missing data is never an event
- **SR-9** no invented estimate/actual distinction
- Weekend-history collection (PR-16), corpus refresh, Discovery, the 13 strategies, learning, headline intelligence, Aragorn integration

## 10. Items requiring your explicit approval

1. **Disable `corpus_refresh.yml` on `main` before 2026-11-01** (PR-19). Optionally also send the R1 editor notice and approve the R3 hotfix.
2. **The source-permission decision** for continuing the existing TN daily and BOM title-page fetches under v2 (blocker 2). History collection and refresh stay off regardless.
3. **Read-only access to past suggestions** (a sheet copy or an export) for the PR-12 replay and shadow comparison.
4. A **live dry run with production credentials** (PR-11, stage S6).
5. **Slack access verification**, read-only (PR-3/PR-10).
6. **Workbook copy and migration rehearsal**, then the production migration (S5; PR-7/8/13).
7. Your **hand check of 20 corpus identities** (B3).
8. **Push, PR and human review** (S2), then **creating the repository variables** (S3, PR-17), then **merge** (S4).
9. **Decisions:** late US releases (PR-14), artifact retention (PR-18), protection of the suggestions tab (PR-6).
10. **Activation** (S7) and the observation period (S8).

## 11. What prevents a safe production deployment today

1. **Unverified live interfaces:** Anthropic structured output, Slack scopes and timestamps, Sheets value formatting, and GitHub Actions.
2. **The unknown editorial impact of the validator** on real headlines.
3. **The unrehearsed migration** of the real workbook.
4. **The undecided source-permission question.**
5. **No human code review.**

None of these needs more Phase 0 code. Each is a verification or a decision that requires your approval. Nothing has been pushed, deployed, migrated or enabled. Phase 1 has not begun.
