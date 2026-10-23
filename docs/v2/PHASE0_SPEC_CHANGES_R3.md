# Phase 0 Spec, Revision 3: Change Summary

This summarizes how `FARAMIR_PHASE0_SPEC.md` revision 3 resolves the review. Section numbers refer to revision 3.

## Decisions recorded (§1.1)

| Decision | Where it now lives in the spec |
|---|---|
| Corpus repair uses **TMDB only**; there is no BOM rebuild | P0-1a. The rebuild path isn't run, and the corpus-refresh workflow is gated off. |
| Weekend history is stored in Google Sheets | §5 |
| The giant-killer test is updated to the genre-agnostic rule, with regression coverage kept | P0-8, Increment 0 |
| Python 3.11 in an isolated environment | Increment 0, using a project-local virtual environment |
| Work stays on a local branch only | §9.4 |
| Expanded BOM/TN collection waits on licensing; development uses fixtures | Live weekend collection is off by default (P0-3). §9.4 prohibits any new TN or BOM request. |

## Required changes

| # | Issue | Resolution | Section |
|---|---|---|---|
| 1 | Deployment sequencing | **Fail-closed gates:** an unset `FARAMIR_ENABLED` means disabled; the corpus refresh has its own gate. **Preflight** checks that the Sheets schema version, the corpus schema and health, and state integrity all match, otherwise the run aborts with zero writes. New code and the v2 corpus ship in **one merge**. Sheets migration runs **after** the merge, while scheduled runs are no-ops, and writes its schema version **last**. Then a dry run, then **explicit activation**. The old-and-new incompatibilities are analyzed, and the rollback path is documented. | §9.1–9.3, P0-7 |
| 2 | Weekend-history crash recovery | IDs are deterministic and content-based (`collection_id`, `record_id`), with no time component. An append-only event log (`begin`/`commit`/`reject`/`abort`) records each collection. **Readers only see committed collections.** Recovery resumes by appending only the missing `record_id`s. The revision number is fixed at `begin` and reused when resuming. Readers de-duplicate. Orphans are aborted after 48 hours and never deleted. Runs are serialized by a concurrency group. 12 tests (H1–H12). | §5.2 |
| 3 | State recovery | State is checksummed and versioned (`state_meta`), and editor-owned columns are excluded from the checksum. The **last-known-good copy is refreshed before each write, only from state validated in the same run**, so it can't be overwritten by corrupt state. The write is followed by a read-back check. There's an off-Sheets JSON backup in every successful run's artifact. A recovery decision table covers each case: auto-restore runs only for an interrupted write and makes **no picks that run**; an unknown mismatch aborts; manual restore uses `restore_state.py` (plan/apply). | P0-4 |
| 4 | Slack delivery | **Eight delivery states per pick.** Reconciliation uses message metadata in Slack history, read twice. Same-day automatic replay happens only if the pick's data is still current and it re-validates; older picks become `expired`, are listed, and can be replayed manually through `replay_picks.py`. **Delivery guarantee:** at most once automatically, no silent loss, same-day retries. Undelivered picks don't suppress future suggestions. **A same-day re-run posts genuinely new eligible picks within the daily cap.** Crash points CP1–CP8 are each tested. | P0-5 |
| 5 | Missing domestic figures | Corpus schema v2 records each gross as a value plus a status (`reported`, `missing` or `confirmed_zero`). All 1,011 zero domestic values (and 12 worldwide) become `missing`; none can be confirmed zero in TMDB-only mode. A comparison film with missing domestic data is excluded from domestic-axis comparisons and claims. The payload shows `null` with an explicit "do not cite" instruction, and the validator rejects any figure or claim built on a non-`reported` metric. Coverage is reported per build and per run: domestic 49.7%, worldwide 99.4%. | P0-1a, P0-1b, P0-6 |
| 6 | Corpus fail-safe | Three corpus health classes. **ABORT** (old-schema corpus, duplicates, under 1,800 valid rows, more than 1% invalid, more than 2% unresolved, worldwide coverage under 95%) produces no picks and sends an alert. **DEGRADED** (old corpus, small invalid share, a coverage drop) allows picks but adds a Slack header warning. **Row-level exclusion** uses reason codes. A new rule excludes comparison films that were still in release when the corpus was built, because their grosses are stale. | P0-1b |
| 7 | Comparative claims | Claude must return structured `claims[]`, and each claim type has evidence rules. **Rank, record, superlative, chart-position and "first/only" claims are disallowed in Phase 0**, because no candidate evidence supports them. An independent lexical check catches superlatives, mismatches between "passes" and "nears", and third films that Claude didn't report as claims. Genre descriptors must match TMDB genres. The two prompt examples that invite unsupported superlatives are removed (pending your approval). | P0-6 |
| 8 | Manageable increments | **13 increments (0–12)**, each with its files, a stand-alone acceptance test and dependencies. There's a stop after each one for your review. The pipeline-wide refactor and module split are deferred to Phase 1, and `run_daily.py` keeps its current step structure. | §2, §1.2 |

## Production concern (§7)

**Measured exposure.** On the 2026-10-21 chart, *Resident Evil* has 19 of its 73 domestic-axis comparisons (26%) carrying the wrong identity. The *Spider-Man* (2002) franchise story is structurally suppressed. I couldn't measure worldwide exposure without new BOM requests.

**November 1 refresh.** This run is certain unless it's disabled. It would:
- re-scrape BOM while the licensing decision is pending;
- re-create mislabels, probably on different rows;
- have no gate against a truncated corpus;
- commit directly to `main`;
- overwrite any interim hotfix.

**Recommendations.** None has been actioned; each needs your approval.

| Item | Action | Recommendation |
|---|---|---|
| R1 | Send editors a verification notice | Do now (you send it) |
| R2 | Disable `corpus_refresh.yml` | Before 2026-11-01 09:00 UTC; time-sensitive |
| R3 | A v1-compatible, TMDB-repaired interim corpus hotfix | Recommended together with R2 |
| R4 | Pause daily runs | Only if R3 is declined |

## Other changes from revision 2

- Behavior change #6 has been reversed: same-day re-runs are no longer blocked. They post only picks that haven't been posted, within the daily cap.
- `weekend_snapshots` is replaced by the `weekend_collections` event log.
- The kill-switch default is inverted: unset now means disabled.
- `migrate_sheets.py`, `restore_state.py`, `replay_picks.py` and `preflight.py` are added.
- The `tests.yml` workflow for pull requests is added.
- The acceptance checklist is expanded: B7–B9, C3, D1–D3, E2 and F2–F4.
