# Increment 11 review: weekend history (collection off)

Branch `v2/phase-0`, local only. Nothing was pushed, deployed or written to live Sheets or Slack, and no workflow changed.

- **No fetching and no backfill.** Nothing in the codebase fetches weekend charts.
- **History is not wired into the daily run.** `faramir.history` is only called from its own tests and the simulation tool.
- **Collection is off by default.** It requires `FARAMIR_COLLECT_HISTORY=true`.
- **No code creates the history tabs.**

## Files

| File | |
|---|---|
| `src/faramir/history.py` | **New.** The protocol, readers, the provisional/confirmed policy, and the missing-data helpers. |
| `tests/test_history.py` | **New.** 40 tests: H1–H12 plus the cases you required. |
| `tests/tools/simulate_history.py` | **New.** Runs the crash and revision scenarios on in-memory Sheets and writes `simulation/`. |
| `docs/v2/STRATEGY_REQUIREMENTS.md` | Adds SR-7 (revisit the 90-day restriction), SR-8 (missing data is never an event) and SR-9 (no invented estimate/actual distinction). |
| `docs/v2/PRODUCTION_REQUIREMENTS.md` | Adds PR-16: what must happen, with approval, before collection can ever be enabled. |

## Schemas

**`weekend_history`.** Append-only; one row per film per collection.

| Group | Columns |
|---|---|
| Identity of the record | `record_id` = sha1(collection_id \| TN slug)[:20], `collection_id` |
| Source and period | `source`, `source_url`, `chart_type`, `weekend_start` (Fri), `weekend_end` (Sun), `position` |
| Original film identity | `source_film_key` (TN slug), `title_reported`, `slug_year` |
| Tracked identity | `tmdb_id`, `imdb_id`, `identity_confidence`, `identity_method`. Matched **by TN slug only**, never by title. The class is preserved: `legacy` stays `legacy`, and an unmatched film is `unmatched`. |
| Engagement (SR-5) | `engagement` (original / late_release / rerelease / unknown), `first_us_release`, `engagement_start`, `total_gross_scope` |
| Ranks | `source_rank` (as printed by TN, blank for `-`), `source_rank_text`, `derived_rank` with `derived_rank_basis = faramir_gross_desc`, `prev_rank_reported`, `prev_status` |
| Figures | `weekend_gross`, `pct_change_reported`, `theaters`, `theater_avg`, `total_gross`, `days_in_release`. **Unavailable means blank, never 0.** |
| Retrieval | `retrieved_at_utc`, `run_id`, `parser_version` |

**`weekend_collections`.** Append-only event log.

Columns: `event_id`, `collection_id`, `source`, `weekend_start`, `event`, `revision`, `expected_rows`, `rows_hash`, `verified_rows`, `reported_count`, `reported_total`, `parsed_total`, `content_hash`, `chart_status`, `status_detail`, `source_url`, `http_status`, `retrieved_at_utc`, `at_utc`, `run_id`, `parser_version`, `error`.

| Event | When | `event_id` |
|---|---|---|
| `begin` | A new collection starts (it carries the revision, the expected row count and the hash of the sorted record ids) | sha1(collection\|begin): idempotent |
| `commit` | After every expected record has been read back and verified | sha1(collection\|commit): idempotent |
| `observe` | The same content is retrieved again, or an interrupted collection is resumed. This is retrieval evidence. | per run |
| `reject` | The chart was not complete or verified. It carries the parser's status and issues, and **no rows are written**. | per run |
| `abort` | An uncommitted collection older than 48 hours whose content is no longer on the chart | per run |

`collection_id` = sha1(source | weekend_start | the chart's content hash)[:20]. It comes from content only, never from the time or URL, so retries and concurrent runs compute the same ids.

## Transaction and recovery

1. **Gate.** If collection is disabled, nothing is read or written. A failed or missing-tab read stops the run with zero writes, and is never treated as "no history". An unexpected header is also a read error.
2. **Reject anything unverified.** Only a `complete` weekend chart is collected. A partial, malformed or wrong chart, or one with a duplicate TN slug, gets a single `reject` event.
3. **Plan from the log:**
   - already committed → one `observe` event and zero rows;
   - begun but not committed → resume: an `observe` event, then append **only the missing** `record_id`s;
   - otherwise → `begin` (with revision = highest revision begun for that weekend + 1), then all rows in one `append_rows`.
4. **Verify before commit.** Read back the rows. Every expected `record_id` must be present with exactly the expected content, ignoring only the attempt columns `retrieved_at_utc` and `run_id`, and there must be no unexpected ids. Only then is `commit` appended. If verification fails, the collection stays uncommitted (invisible) for the next run to resume.
5. **Orphans.** An uncommitted collection for that weekend that is older than 48 hours, and whose content is not the chart currently being collected, gets `abort`. Its rows stay in the sheet and are never deleted.
6. **Readers** see only committed collections, de-duplicate by `record_id`, and check the row count against the commit.
   - **Current version:** the committed collection retrieved most recently (by its latest `begin` or `observe`), with revision as the tiebreak.
   - **A revision supersedes the earlier snapshot without erasing it.** The superseded collection stays readable through `collection_rows`.
   - **A source that reverts** to earlier figures is followed.
7. **Every write** goes through `sheet.append_values`, which applies the runtime write guard. Without a run context the write is blocked; in dry-run it is recorded only.

**Deviation from spec H2.** Re-collecting identical content writes one `observe` event, not nothing. This is needed for your requirement to retain retrieval evidence, and it is what makes "latest valid version" and "confirmed by recollection" decidable. It adds zero history rows.

## Estimates vs actuals

TN does not label estimates, so nothing here pretends to. Each weekend has a `collection_status` that is a **collection-policy** label, not an official status:

- **`confirmed_by_recollection`**: the current content was retrieved by **at least two runs**, at least one of them **on or after the Tuesday after the weekend** [CAL].
- **`provisional`** otherwise. That includes two Sunday/Monday retrievals, and a single retrieval made on Tuesday.
- **A revision restarts at `provisional`.**
- Every retrieval stays in the event log (time, run, HTTP status, URL) and is exposed as `Snapshot.observations`.
- SR-9 records that identical figures retrieved twice are not proof of an official final.

## Missing data is never an event

- `film_series(slug, weekends)` gives one of three states per weekend:
  - `charted`;
  - `not_on_chart`, **only** when a complete committed chart lacks the film;
  - `unknown`, when the weekend is uncollected, uncommitted or crashed.
- `streak()` returns `indeterminate` at an unknown weekend, **never `broken`**. This is recorded as SR-8.

## Simulated crash and revision results

From [`simulation/scenarios.json`](simulation/scenarios.json), generated by `tests/tools/simulate_history.py` on in-memory Sheets. "Reader" is what `read_weekend` returns after each step.

| Scenario | Step | Result | Reader | Physical rows / events |
|---|---|---|---|---|
| H1 | collect | committed, 8 rows | available, rev 1, 8 rows, provisional | 8 / begin, commit |
| H2 | collect again | unchanged, 0 rows | same, 2 observations | 8 / +observe |
| H3 crash after begin | crash | — | **missing** | 0 / begin |
|  | next run | resumed, 8 rows | available, 8 | 8 / begin, observe, commit |
| H4 crash after 3 of 8 rows | crash | — | **missing** (3 rows invisible) | 3 / begin |
|  | next run | resumed, **5** rows | available, 8 | 8 |
| H5 crash before commit | crash | — | **missing** | 8 / begin |
|  | next run | resumed, 0 rows | available, 8 | 8 |
| H6 acknowledgement lost | crash | — | **missing** | 8 / begin |
|  | next run | resumed, 0 rows | available, 8 | 8 |
| H7 revised figures | revised | committed rev 2 | rev 2 current, rev 1 superseded and intact | 16 |
| H8 crashed revision | revised (crash) | — | **rev 1 still current** | 10 / begin, commit, begin |
|  | source reverts | unchanged | rev 1 | 10 |
|  | +70 h | unchanged, **1 orphan aborted** | rev 1, confirmed_by_recollection | 10 / +abort; the 2 orphan rows are kept but invisible |
| H9 malformed after good | malformed | rejected, 0 rows | **rev 1 unaffected** | 8 / +reject |
| Policy | Sunday, then Tuesday | committed, unchanged | provisional → **confirmed_by_recollection** | 8 |

The simulation recorded 48 guarded Sheets operations and **0 non-Sheets operations**. Each one reached only the in-memory fake.

**Tests that cover the same ground and more** (`tests/test_history.py`, 40 tests):

- H1–H12, including a crash at 1, 3 and 7 of 8 rows;
- a commit that landed with its acknowledgement lost;
- duplicate physical rows;
- concurrent identical runs (physical duplicates, one logical commit);
- concurrent runs with different content (both kept; the latest retrieval is current);
- a verification failure (left uncommitted, resumed later);
- a revision after confirmation;
- revised historical totals;
- failed reads, missing tabs (never created), unexpected headers;
- the guard: blocked without a context, recorded in dry-run;
- disabled by default (zero calls);
- append-only enforcement: the only calls are worksheet open, `get_all_values` and `append_rows`, and the module contains no other write helper;
- missing-weekend semantics;
- ids that depend on content only.

## Excluded and older films are retained

`test_every_valid_row_is_retained_whatever_its_eligibility` collects the synthetic weekend and reads it back: **all 8 rows are stored**, with nothing filtered by the 90-day rule or the $20M floor.

| Film | Why Phase 0 ignores it | Stored as |
|---|---|---|
| Galaxy Run (2019) | re-release, 2,733 days | `engagement=rerelease`, `first_us_release=2019-04-26`, `engagement_start` blank (unknown from one chart), `total_gross_scope=all_engagements` |
| Old Reel (1985) | re-release, 15,086 days | `rerelease`, days `15086` |
| Le Voyage (2024, France) | late US release (excluded by the slug-year rule) | `late_release` |
| Small Indie | $20K weekend, far below the floors | full row |
| Quiet Orchard | tracked, legacy identity | `identity_confidence=legacy`, `tmdb_id=9`; a slug match never upgrades it |
| Iron Harbor | not tracked | `identity_confidence=unmatched`, `tmdb_id` blank |

[`simulation/H1_current_snapshot_rows.json`](simulation/H1_current_snapshot_rows.json) shows the stored rows. Phase 0 story selection is unchanged. The 90-day restriction is recorded for the strategy engine to revisit as SR-7.

## No unapproved external writes

- Production code has **no call** to `history.collect`. `grep` finds it only in tests and the simulation tool.
- **No fetch code** exists for weekend charts.
- **Collection is off by default.** A test shows that with the flag unset it makes zero Sheets calls.
- **Every history write goes through the runtime guard.** The existing static test (`test_no_direct_writes.py`) still passes.
- Tests and the simulation use `FakeClient` only, and the conftest blocks real network access.
- **No tabs created.** Missing tabs produce a `read_failed` result with no `add_worksheet` call.
- **The offline daily dry run is unchanged** (this increment doesn't touch `run_daily.py`).

## Tests

**617 passed, 2 skipped.** The 2 skipped are the opt-in real-capture parser tests. That is up from 577 (+40 history tests); no existing test changed.

## Remaining risks

- **Read cost grows with history.** Each collection reads both tabs in full: about 10k rows a year, which is fine for years, but a reader cache or per-year tabs may be needed later.
- **Sheets row-append semantics.** `append_rows` with `INSERT_ROWS` is not atomic across rows. The protocol tolerates any subset landing (resume plus verify), but a partially written *single row* (cells cut off mid-row) has not been observed and is modelled only as a "content differs" verification failure, which leaves the collection uncommitted.
- **Concurrent runs with different content** both commit, and the latest retrieval wins. This is correct, but the revision numbers can tie. PR-1 (the workflow concurrency group) is what prevents it in practice.
- **The 48-hour orphan rule and the Tuesday confirmation rule are calibration values [CAL].** Holiday weekends (Monday holidays) may need a Wednesday rule.
- **A source that reverts to old figures is followed, by design.** If TN briefly served stale content, readers would follow it until the next retrieval. All versions remain in the log.
- **Enabling collection needs a fetch step that doesn't exist yet,** plus the PR-16 approvals.

## Next: Increment 12 — workflows, preflight and migration (code only, not run)

Exact scope, per spec P0-7, adjusted for Increments 9–11:

1. **`src/faramir/preflight.py`.** It runs before any write on every run; a failure aborts with zero writes and an alert. It checks:
   - `faramir_meta.schema_version` equals `EXPECTED_SCHEMA` (2);
   - the required tabs and exact headers: `state` (20 columns, with the Increment 9 legacy 17-column layout still accepted until migrated), `state_meta`, `state_lkg`, `suggestions` (with the delivery columns) and `runs`. When `FARAMIR_COLLECT_HISTORY` is true it also requires `weekend_history` and `weekend_collections`. **This corrects the spec's stale `weekend_snapshots` name.**
   - corpus schema 2 and health not ABORT;
   - the state decision table;
   - `FARAMIR_ENABLED == 'true'` for live runs.
2. **`scripts/migrate_sheets.py`** with `--plan` (default), `--apply` and `--rollback-plan`. It is idempotent. Its steps:
   - validate v1 state;
   - create the new tabs, including the two history tabs **with headers only**;
   - append the header-only columns to `state` and `suggestions`;
   - mark posted suggestions;
   - initialize `state_meta`/`state_lkg`;
   - write `schema_version` **last**.

   Tests use a fake v1 workbook. Running it is a production action that needs separate approval (stage S5, on a sheet copy first).
3. **Workflows** (`.github/workflows/daily.yml`, `corpus_refresh.yml`, and a manual `migrate.yml`): **edited in the repo only, reviewed by reading, never dispatched.**
   - fail-closed `if:` gates on `vars.FARAMIR_ENABLED` / `FARAMIR_CORPUS_REFRESH_ENABLED` (an unset variable means disabled);
   - `concurrency: {group: faramir-production, cancel-in-progress: false}` (PR-1);
   - `timeout-minutes` ≤ 15;
   - a pytest step;
   - a `dry_run` input;
   - no history-collection step.
4. **PR-1 remaining gap:** `StateStore.write()` also refuses once **its own** lease has expired (fail closed), with a test.
5. **Out of scope:** pushing, enabling any variable, dispatching any workflow, running the migration, creating any tab, enabling collection, any new fetching.
