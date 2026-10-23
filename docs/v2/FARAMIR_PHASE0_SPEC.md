# Faramir 2.0 Phase 0 Reliability Specification (Revision 3)

**Repository:** `adleadbeater/faramir`. Base commit `f2a0ce4`, which has the same code as `5fd3157`.

**Prepared:** 2026-10-22. **Status:** proposal only. Nothing has been implemented, and no production system has been changed.

**Companion files:**
- `FARAMIR_V2_PLAN.md`: architecture and all phases. Unchanged except where this spec supersedes it.
- `corpus_identity_audit_2026-10-22.csv`: the 322 misidentified corpus rows.
- `PHASE0_SPEC_CHANGES_R3.md`: how each review issue was resolved.

### Revision history

| Rev | Change |
|---|---|
| 2 | Initial detailed specification for independent review |
| 3 | Records the decisions from your review and resolves its 8 required changes plus the production concern. Each change is marked **[R3-n]**. |

---

## 1. Scope, recorded decisions and principles

### 1.1 Decisions recorded from review

| Decision | Effect on this spec |
|---|---|
| Use the **TMDB-only** corpus identity repair; no full BOM rebuild | P0-1 has one mode: repair. Domestic gaps stay gaps and are handled as missing data **[R3-5]**. |
| Store weekend history in **Google Sheets** for the MVP | §5 schema |
| Update the giant-killer test to the genre-agnostic behavior and keep regression coverage | Increment 0 |
| Use **Python 3.11** in an isolated local environment | Increment 0, using a project-local virtual environment |
| Work on a **local branch only**. No push, merge or deployment. | §9 |
| **Expanded BOM/TN collection waits for a Valnet terms/licensing decision.** Use fixtures for development. | Live weekend collection is built but **off by default**. Parser tests use synthetic fixtures. Pages already captured during the audit are used only locally and are not committed. |

### 1.2 Principles

- **Minimal changes to working code [R3-8].** `run_daily.py` keeps its current step structure. New behavior is added as small functions called from the existing steps, and there is no pipeline-wide refactor. The module split in the plan (`sources/`, stage modules) is deferred to Phase 1, where it is actually needed.
- **Fail closed.** Missing or corrupt inputs stop the affected step loudly. They never pass as normal output.
- **Missing is not zero [R3-5].** No headline is ever supported by a value that is absent from the source.

### 1.3 Intended behavior changes (each needs your agreement)

1. **Corrected identities.** Comparisons use correctly identified films, so some picks differ from what the current code would produce.
2. **Untraceable figures.** Picks with figures or comparative claims that can't be traced to candidate evidence are dropped **[R3-7]**.
3. **85-character headline limit,** enforced in code.
4. **Re-releases.** Re-release chart rows are flagged and excluded from picks.
5. **Unreadable or corrupt state** stops the run, or triggers a controlled restore that produces no picks. It never reseeds silently **[R3-3]**.
6. **Same-day re-runs post only picks that haven't already been posted**, within the day's pick cap. A re-run no longer blocks new eligible picks, and never reposts **[R3-4]**.
7. **Missing domestic figures.** Comparisons on the domestic axis are only made against comparison films whose domestic gross was actually reported **[R3-5]**.
8. **Fewer comparison claims.** Rank, record and superlative claims are disallowed in Phase 0 because no candidate evidence supports them. The two prompt examples that encourage them are removed **[R3-7]**.
9. **Stale comparison grosses.** Comparison films still in release when the corpus was built are excluded from "passed" claims once the corpus is more than 7 days old **[R3-6]**.
10. **Degraded-corpus warning.** When the corpus is degraded, the daily Slack header carries a one-line warning. Normal days are unchanged **[R3-6]**.
11. **Scheduled runs are fail-closed.** They do nothing until `FARAMIR_ENABLED=true` is set explicitly **[R3-1]**.

**Out of scope:** new strategies, ranking changes, the event ledger, Cerebro changes, a model change, DST handling, Monday's minimum of 6 picks (moved to Phase 1), and live weekend collection or a corpus refresh (both waiting on the licensing decision).

---

## 2. Development increments [R3-8]

Each increment is a separate, reviewable change on the local branch `v2/phase-0`.

- Each one is independently testable with fixtures and fakes, needs no network or credentials beyond a TMDB read for Increment 1, and leaves the full test suite green.
- I'll stop after each increment for your review. You authorize them one at a time.

| # | Increment | Files touched | Independent acceptance | Depends on |
|---|---|---|---|---|
| 0 | **Test harness and environment**: Python 3.11 virtual environment, exact pins matching what CI resolves today, `requirements-dev.txt`, fakes (gspread, Slack, Anthropic, HTTP), giant-killer test fix, `conftest.py` that blocks network access by default | `requirements*.txt`, `tests/conftest.py`, `tests/fakes/*`, `tests/test_matcher.py` | Existing 30 tests pass; the updated giant-killer tests pass; no code behavior change | none |
| 1 | **Corpus repair tool**: `find_by_imdb`, `repair_corpus` script, schema v2 (missing-metric status), validator, build report. Produces a repaired `corpus.json` **on the branch**. | `tmdb.py`, `scripts/build_corpus.py` (new `--repair-identities` path only), `corpus_validate.py` | Offline repair from recorded `/find` responses reproduces the audit exactly; the validator's gates are tested | 0 |
| 2 | **Daily corpus loading and fail-safe**: health classes, row exclusion, missing-metric handling, stale-comparison exclusion, coverage diagnostics | `corpus.py`, `matcher.py` (null-safe comparisons only), `run_daily.py` step 7 | Corpus-health tests (§3, P0-1b); matcher tests unchanged | 1 |
| 3 | **Runtime context**: dry-run, recording sinks around the existing write calls, kill-switch check in code, fixtures mode, local run report | `runtime.py`, small call-site edits in `run_daily.py`, `sheet.py`, `slack_post.py` | Offline end-to-end dry run makes zero writes with network blocked | 0 |
| 4 | **State integrity and recovery** | `sheet.py`, `state_integrity.py`, `scripts/restore_state.py`, `run_daily.py` steps 2 and 13 | Every row of the recovery decision table is tested (§3, P0-4) | 3 |
| 5 | **Suggestions writes**: one `append_rows`, targeted feedback-cell updates | `sheet.py`, `run_daily.py` step 1b | Call-log tests | 3 |
| 6 | **Slack delivery state machine**, reconciliation, replay script | `delivery.py`, `slack_post.py`, `scripts/replay_picks.py`, `run_daily.py` steps 12–13 | Each crash point tested (§3, P0-5) | 3, 5 |
| 7 | **Claude structured output**, client configuration, stop-reason handling, binding picks to candidates | `claude_select.py`, `schemas.py` | Mocked-client tests | 0 |
| 8 | **Figure and claim validation, 85-character limit, prompt edits** | `validate.py`, `claude_select.py` (prompt) | Claim test matrix (§3, P0-6) | 7, 2 |
| 9 | **Active-film identity resolution** | `identity.py`, `tmdb.py`, `run_daily.py` step 4 | Identity tests | 0 |
| 10 | **TN chart parser and Friday weekend URL** (parser only, no collection) | `bom_fetch.py` (added functions; existing functions unchanged) | Synthetic-fixture parser tests | 0 |
| 11 | **Weekend-history transaction protocol** (collection off by default) | `history.py` | Every crash and recovery case in §5 | 3, 10 |
| 12 | **Workflows, preflight and migration**: fail-closed gates, concurrency, pytest step, dry-run input, `migrate_sheets.py` (plan/apply), preflight checks | `.github/workflows/*.yml`, `preflight.py`, `scripts/migrate_sheets.py` | Preflight tests; workflow logic reviewed by reading it, since it isn't run until deployment | all |

---

## 3. Change specifications

Each change covers files, problem and verified root cause, solution, data and interface changes, tests, production risk, and reversal.

### P0-1a Corpus identity repair (TMDB-only) and metric availability [R3-5]

**Files:** `src/faramir/tmdb.py` (`find_by_imdb`), `scripts/build_corpus.py` (adds a `--repair-identities` path; the BOM rebuild path is left as-is and isn't run), `src/faramir/corpus_validate.py` (new), tests, and fixture `tests/fixtures/tmdb_find_corpus_2026-10-22.json`.

**Root cause (verified):** `enrich_with_tmdb()` resolves each film by title search with no year and takes the most popular result, so 322 of 2,011 rows have the wrong identity. Evidence is in §4.

**Solution:**
1. For each row, call `/find/{imdb_id}`. Reload the metadata from the true TMDB ID and keep all gross fields. Recompute `is_classic` from the true year.
2. **No BOM or TN requests are made.**
3. If `/find` returns zero or several movies, mark the row `identity_status=unresolved`. Its creative metadata is blanked and its grosses kept. Zero rows are unresolved today.
4. **Metric availability (schema v2):**
   - Each gross field becomes a value plus a status:

     | Field | Values |
     |---|---|
     | `domestic_lifetime` | int, or `null` |
     | `domestic_status` | `reported` (on BOM's domestic top-1,000 chart at build time), `missing`, or `confirmed_zero` |
     | `worldwide_lifetime` | int, or `null` |
     | `worldwide_status` | same values as `domestic_status` |

   - The repair converts every `0` to `null` with status `missing`. That covers 1,011 domestic and 12 worldwide values.
   - In TMDB-only mode **no row can be `confirmed_zero`**, because no source in use proves a film had zero US gross. The status exists for a later licensed source.
   - The repair also adds a file-level header object:

     ```
     {corpus_schema_version: 2, gross_as_of: "2026-10-01", identity_repaired_at, rows, coverage}
     ```

   - The file becomes `{"meta": {...}, "films": [...]}`. That deliberately breaks old readers, which matters for §9.
5. **Coverage report:** the share of rows with `reported` status for each metric.
   - Today: domestic **1,000 of 2,011 (49.7%)**; worldwide **1,999 of 2,011 (99.4%)**.
   - It is written into the build report and into every daily run's diagnostics.
6. `validate_corpus()` hard gates:
   - duplicate `imdb_id`;
   - duplicate verified `tmdb_id`;
   - `worldwide < domestic` where both are `reported`;
   - a `status`/value mismatch (for example `reported` with a `null` value);
   - fewer than 1,800 rows;
   - a wrong schema version.
   - Any failure exits non-zero, and nothing is written.

**Data and interface changes:** the corpus file format changes (v2). `corpus.load_corpus()` returns `(meta, films)`, and its only caller is updated.

**Tests:**
- remake, sequel-absorbs-original and same-title-same-year cases;
- an unresolved row;
- the full offline repair reproduces the CSV (322 identity changes, 0 duplicates, grosses unchanged);
- zero is converted to `null`/`missing`;
- no `confirmed_zero` appears in repair mode;
- each gate fails on bad input and passes on good input;
- the build doesn't write when a gate fails.

**Production risk:** none until deployment (§9).

**Reversal:** the corpus file reverts with the merge revert.

### P0-1b Corpus health: abort vs. exclude [R3-6]

**Files:** `src/faramir/corpus.py`, `run_daily.py` step 7, and null-safe comparisons in `matcher.py`. The matcher change is one guard in `detect_angle_categories` and one in `detect_asymmetry`; scoring is unchanged.

**Classification at load.** Every daily run classifies the corpus before generating candidates.

| Class | Conditions (any one) | Daily behavior |
|---|---|---|
| **ABORT** | File missing or unparseable; `corpus_schema_version ≠ 2`, which catches the old corrupted corpus; any duplicate `imdb_id` or verified `tmdb_id`; fewer than 1,800 valid rows; invalid rows > 1% [CAL]; unresolved identities > 2% [CAL]; worldwide coverage < 95% [CAL] | **No comparison or milestone picks.** State update and diagnostics still run. An alert goes out: "Faramir: comparison data failed integrity — no picks today". Never produces normal-looking output. |
| **DEGRADED** | Corpus age (`run_date − gross_as_of`) > 45 days [CAL]; invalid rows > 0 and ≤ 1%; domestic coverage drops by more than 2 points from the value recorded in `meta.coverage` | Picks are allowed. The Slack header gains one line, for example "⚠ Comparison data as of 2026-10-01 (58 days old) — verify comp figures". The reason is recorded in `runs`. |
| **OK** | None of the above | Normal |

The baseline domestic coverage of 49.7% is a known, recorded property of the TMDB-only corpus, so it is OK, not DEGRADED. The *constraint* that comes from it is enforced per row (below).

**Row-level exclusion.** Each case affects one comparison only and is logged with a reason code. Run counts go into `runs.corpus_exclusions`.

| Row condition | Excluded from |
|---|---|
| Fails a row check (for example `worldwide < domestic`) | Everything (`comp_invalid`) |
| `domestic_status ≠ reported` | Domestic-axis comparisons, plus any claim that mentions its domestic gross (`metric_missing`) |
| `worldwide_status ≠ reported` | The same, for worldwide |
| `identity_status = unresolved` | Franchise, director and cast angles, and naming the film in a headline (`identity_unresolved`). Numeric use isn't allowed either, because the headline would have to name the film. |
| Comparison released within 120 days [CAL] before `gross_as_of` (so probably still in release when the gross was captured), and the corpus is > 7 days old | "Passed" and "nears" claims (`comp_gross_stale`). Its stored gross is likely understated. |

**Missing data never supports a headline.**
- The Claude payload shows missing metrics as `null`, and the prompt states that `null` means "not available — do not cite".
- The validator (P0-6) rejects any claim or figure that depends on a non-`reported` metric.
- The Slack figures block renders missing values as "n/a". It previously rendered "—" for zero.

**Tests:**
- each ABORT trigger;
- each DEGRADED trigger, including the header line;
- an old v1 corpus is detected and aborts;
- each row exclusion produces its reason code;
- a domestic-axis candidate is never generated against a `missing` comparison;
- the asymmetry cohort skips `null` values;
- regression: existing matcher tests pass with v2 data.

**Production risk:** ABORT stops picks on a bad day. That's intended, and it's visible through the alert.

---

### P0-2 Active-film identity resolution

This is unchanged from revision 2 in substance.

**Files:** `identity.py` (new), `tmdb.py`, `run_daily.py` step 4, and `STATE_HEADERS`.

**Resolution order:**
1. `tmdb_id_override`.
2. A stored TN slug.
3. Search with an exact normalized title, the slug year (±1), a release date within 60 days of the implied opening, a unique top candidate, and an IMDb ID present.
4. Otherwise the film is **unresolved**: skipped and reported, never guessed.

**Re-releases:** if `slug_year < chart_year − 1`, the row is flagged `rerelease` and excluded from picks.

**State changes:** three columns are appended to the state tab: `tn_slug`, `identity_confidence`, `identity_method`.

**Tests:** as in revision 2.

**Change from revision 2:** the TN slug comes from the **daily chart Faramir already fetches**, so it needs no new collection.

---

### P0-3 TN chart parser and weekend history [R3-2]

**Parser (Increment 10).**
- Added functions in `bom_fetch.py` map columns by exact header, using the TN layout verified in the audit.
- The **Friday-dated** weekend URL is fixed.
- Completeness is checked: the footer count and total must reconcile, and ranks must be contiguous.
- An unknown layout raises an error.
- **Existing `fetch_daily_list` behavior is untouched.** The Monday merge keeps using the daily chart.

**Fixtures:**
- Synthetic HTML reproducing the verified structure is committed.
- The real pages captured during the audit (weekend 2026-10-16, daily 2026-10-21) are used only by an opt-in local test, `-m local_capture`, and are not committed.

**Collection (Increment 11):**
- `FARAMIR_COLLECT_HISTORY` defaults to **false**. It is enabled only after the licensing decision and your approval.
- The transaction protocol is in §5.2.

---

### P0-4 Google Sheets state integrity and recovery [R3-3]

**Files:** `sheet.py`, `state_integrity.py` (new), `scripts/restore_state.py` (new), `run_daily.py` steps 2 and 13.

**Problem.**
- `read_state` returns `[]` on any error, which leads to a silent reseed.
- `write_state` calls `clear()` and then `update()`.
- Removing `clear()` alone could leave a mixture of old and new rows after an interrupted update.

**Design.**
- **Tabs:** `state` (primary; editors may edit only `tmdb_id_override` and `notes`), `state_lkg` (last-known-good copy), and `state_meta`.
- **`state_meta` rows:** one row each for `state` and `state_lkg`, with columns:

  ```
  tab, generation, status (ok|writing|restored), row_count, checksum, written_at, run_id
  ```

  Each row is written with a single-range update.
- **Checksum:** SHA-256 over a canonical serialization of every *system-owned* column, with rows sorted by `tmdb_id`. Editor-owned columns are excluded, so an editor setting an override doesn't count as corruption. When state is written, editor columns are merged by `tmdb_id` from the state as read at the start of the run.

**Integrity check, `validate_state(rows, meta)`.** It fails if any of these is true:
- the header isn't the expected list (a superset in the expected order is allowed);
- any `tmdb_id` is duplicated or not an integer;
- `status ∉ {active, retired}`;
- a gross field isn't a non-negative integer, or `domestic_today < domestic_yesterday`;
- `row_count ≠ meta.row_count`;
- `checksum ≠ meta.checksum`;
- `meta.status ≠ ok`.

**Write protocol (end of a successful run).**
1. **Refresh the backup first.**
   - Copy the state *as validated at the start of this run* into `state_lkg`: set `meta[lkg].status=writing`, write the rows, trim, read back, verify, then set `meta[lkg].status=ok` with the checksum.
   - If anything fails, **abort before touching `state`**.
   - The backup is only ever overwritten with state that passed validation in the same run. **A corrupted state can never overwrite the backup.**
2. Set `meta[state].status=writing` and `generation=g+1`.
3. `ws.update(values=..., range_name="A1")`, then `batch_clear` on the leftover rows only.
4. Read the tab back and recompute the checksum. If it matches, set `meta[state]` to `ok` with the new checksum and row count. If it doesn't, leave the status as `writing` and alert.
5. **Off-Sheets backup:** every successful run also uploads the written state as JSON to the run's Actions artifact (90-day retention). Dry-run uploads nothing.

**Recovery decision table (start of run).**

| `state` | `state_lkg` | Situation | Behavior |
|---|---|---|---|
| valid | any | Normal | Proceed |
| invalid, `meta.status=writing` | valid | Interrupted write | **Controlled auto-restore:** copy `state_lkg` into `state`, set `meta.status=restored`, alert "state restored from last-known-good (gen N)". **This run updates state but makes no picks**, because the baseline is a day older. Normal picks resume next run. Can be turned off with `FARAMIR_AUTO_RESTORE=false`. |
| invalid, `meta.status=ok` (checksum or row mismatch) | valid | Unknown external change or corruption | **Abort with zero writes**, alert. A human runs `restore_state.py --from lkg --apply`, or fixes the tab. Nothing is restored automatically, because the cause is unknown. |
| invalid | invalid or missing | Both copies bad | **Abort with zero writes.** Manual restore from an Actions artifact with `restore_state.py --from artifact:<run_id> --apply`. |
| empty, no meta | any | First run | Cold start only with `FARAMIR_COLD_START=1`; otherwise abort |
| read error (API, missing tab) | any | Transient or configuration problem | Abort with zero writes. Never treated as a cold start. |

**`restore_state.py`:**
- `--plan` (default) shows the row count, generation and a diff summary.
- `--apply` writes. It refuses to restore from a source that fails `validate_state`.
- It is a production action and needs approval each time it's used.

**Suggestions-tab writes (Increment 5):**
- One `append_rows` call.
- Feedback goes through `batch_update` of the four feedback cells only, matched by `slack_ts`. A row whose `slack_ts` has moved is skipped.

**Tests:**
- every row of the decision table;
- interrupted at each write step (2, 3, 4) produces the expected next-run behavior;
- an editor changing an override doesn't break the checksum;
- the backup is never written from invalid state;
- a backup-write failure leaves `state` untouched;
- a read error leads to zero writes;
- the restore script refuses an invalid source;
- the artifact backup isn't uploaded in dry-run.

**Production risk:** about 6–8 extra Sheets calls per run, well within quota. Editors must not add or delete rows in `state` by hand; that is documented in the README.

---

### P0-5 Slack delivery: states, reconciliation, replay [R3-4]

**Files:** `delivery.py` (new), `slack_post.py`, `scripts/replay_picks.py` (new), `run_daily.py` steps 12–13, and `SUGGESTIONS_HEADERS`.

**Identifiers:**
- `pick_key = sha1(run_date|active_tmdb_id|comp_tmdb_id or "M"+threshold|axis|kind)[:16]`
- `run_id = <run_date>-<GITHUB_RUN_ID or local-ts>`
- `data_date` is the date of the TN data the pick was built from.

**New suggestion columns** (appended to the right): `run_id`, `pick_key`, `data_date`, `delivery_state`, `attempts`, `last_attempt_at`, `slack_ts`. The last one already exists.

**Per-pick delivery states:**

| State | Meaning | Suppresses the pair from future suggestions? | Next step |
|---|---|---|---|
| `planned` | Row logged; no Slack attempt yet | Yes, today only (prevents a duplicate during recovery) | Send |
| `sending` | Recorded immediately before `chat.postMessage` | Yes, today | Reconcile if found later |
| `posted` | Slack returned a `ts`, or reconciliation found the message | Yes, for 5 days, as today | Done |
| `failed` | Slack returned `ok:false`, a definite non-delivery (after 3 retries with backoff) | No | Automatic retry on a same-day re-run |
| `unknown` | Was `sending` at a crash or timeout and reconciliation couldn't run | Yes, until resolved | Reconcile on the next run |
| `not_delivered` | Reconciliation confirmed it isn't in the channel | No | Automatic replay if still valid (below), otherwise `expired` |
| `expired` | Not delivered, and its `data_date` is older than the current data or re-validation failed | No | **Listed in the run report and the next header**; manual replay only |
| `dismissed` | An operator chose not to replay it | No | Done |

**Run sequence:**
1. **Reconcile** every row in `planned`, `sending` or `unknown` from the last 3 days.
   - Read `conversations.history` for the window with message metadata.
   - A message whose metadata `pick_key` matches means the row becomes `posted`, with `slack_ts` recorded.
   - Otherwise a `planned` row becomes `not_delivered`.
   - A `sending` row becomes `not_delivered` only after a second history read at least 30 seconds later still finds nothing.
   - If history can't be read, rows stay `unknown`, still suppress, an alert is sent, and **nothing is reposted**.
2. **Replay automatically** `not_delivered` and `failed` picks only when **all** of these hold:
   - same `run_date`;
   - same `data_date` as today's data;
   - the pick re-passes binding and figure/claim validation (P0-6) against current data.
   - Otherwise the pick becomes `expired`.
3. **Select new picks** as today. Suppression counts `posted`, plus today's `planned`, `sending` and `unknown` rows.
   - A pick whose `pick_key` is already `posted` today is skipped.
   - **Genuinely new eligible picks are posted on a same-day re-run**, as long as today's posted count plus the new ones stays within `PICKS_MAX_BY_DAY`.
   - This replaces revision 2's blanket "skip Slack on re-run".
4. **Log first:** append the new rows as `planned` in one call.
5. **Post each pick:**
   1. Set `sending`.
   2. Call `chat.postMessage` with metadata `{event_type: "faramir_pick", event_payload: {pick_key, run_id}}`.
   3. On success set `posted` and the `ts`; on `ok:false` set `failed`; on a timeout set `unknown`.
   4. Writes are batched where possible, with at most one state write per pick at each transition.
6. **Header:** posted through the webhook only if no header has been posted today (`runs.header_posted`). A re-run that adds picks posts no second header.
7. **Expired picks:** the next run's header gets one line, "N earlier suggestions could not be delivered — see run report", but only when N > 0.

**Manual replay, `scripts/replay_picks.py`:**
- `--list` shows `not_delivered`, `expired` and `failed` rows.
- `--pick-key K --plan` re-validates against current data and shows the exact blocks.
- `--apply` posts with a context line "Delayed suggestion from <date>" and sets `posted`.
- `--dismiss` sets `dismissed`.
- The script refuses to post a pick that fails re-validation or is already `posted`. Each use is an operator action that needs approval.

**Delivery guarantee:**
- **Automatic posting is at-most-once per `pick_key`.** Before any automatic repost, Slack history is checked for that key's metadata. A duplicate is only possible if Slack accepted a message but didn't show it in history on two reads 30 seconds or more apart, and a later reconciliation would then mark both.
- **No silent loss.** Every logged pick ends in `posted`, `expired` or `dismissed`. Anything not posted is listed in the run report and flagged in the next header.
- **Same-day delivery is retried automatically** while the pick's data is still current. Delivery on a later day is always a deliberate manual action.

**Scopes and verification:** `chat:write` and `channels:history` are already held. **Message metadata on `chat.postMessage` and its return in `conversations.history` must be verified against Slack's documentation and a sandbox channel during Increment 6, before anything relies on them.** If they aren't available, the fallback match is the exact first block text plus bot ID within the window. That is weaker, and I'll report it.

**Crash-point tests**, using a fake Slack with an injectable failure at each point:

| # | Crash point | Expected outcome on the next run |
|---|---|---|
| CP1 | Before the `planned` append | Pick regenerated normally; no duplicate |
| CP2 | After `planned`, before `sending` | Reconciliation finds no message, so `not_delivered`, then same-day replay posts it once |
| CP3 | After `sending`, before the API call | Absent on both reads, so `not_delivered`, then replayed once |
| CP4 | After the API succeeded, before the `ts` write | Found by metadata, so `posted`; **not reposted** |
| CP5 | API `ok:false` | `failed`, retried on a same-day re-run, posted once |
| CP6 | API timeout (message actually delivered) | `unknown`, then found, so `posted`; not reposted |
| CP7 | History unreadable | Stays `unknown`, still suppresses, alert sent, no repost |
| CP8 | Next day, still undelivered | `expired`, listed, not auto-posted; manual replay re-validates |

Also tested:
- a same-day re-run with new eligible picks posts only those, within the cap;
- a re-run doesn't post a second header;
- the replay script refuses a stale or already-posted pick.

---

### P0-6 Claude output, figure and claim validation [R3-7]

**Files:** `claude_select.py`, `schemas.py` (new), `validate.py` (new). The `_validate_pick_figures` checks move into `validate.py`, and the existing tests are kept.

**Structured output:**
- `client.messages.parse(output_format=PickSet)` with Pydantic, per the current Anthropic Python SDK documentation.
- Client settings: `timeout=120`, `max_retries=3`, and `max_tokens` read from config.
- `stop_reason` of `refusal` or `max_tokens` counts as a failure: one retry, then no picks with a reason recorded.
- A `count_tokens` ceiling is applied before the call.
- The model is unchanged: `claude-sonnet-4-6`.

**`Pick` gains a required `claims[]` list.** Every factual assertion in the headline and angle must appear as one claim.

| Claim type | Fields | Allowed in Phase 0 when |
|---|---|---|
| `passed_comp` | comp_tmdb_id, axis | The bound candidate has `kind=just_passed`, the same comparison film and axis, the comparison film's metric for that axis is `reported` and not stale, and active ≥ comparison on current data |
| `nearing_comp` | comp_tmdb_id, axis | The candidate has `kind=about_to_pass`, the same comparison film and axis, a `reported` metric, and active < comparison |
| `crossed_milestone` | threshold, axis | A milestone candidate of type `just_passed` with the same threshold and axis; the active film's metric is fresh (not stale) |
| `nearing_milestone` | threshold, axis | A milestone candidate of type `about_to_pass` |
| `relation` | type ∈ {franchise, same_director, same_star} | The candidate's `angle_categories` contains the matching category, and the comparison film's identity is `verified` |
| `genre_descriptor` | genre | The genre is in the subject film's TMDB genres |
| `rank`, `record`, `superlative`, `chart_position`, `first_or_only`, `count` | (any) | **Never in Phase 0.** No candidate carries the evidence needed. They can be enabled per strategy in Phase 1 or later, once ranked-list evidence exists. |

**Independent lexical cross-check.** This guards against Claude under-reporting its own claims. The validator scans the headline and angle without relying on the `claims[]` list:

- **Rank, record or superlative language** fails the pick unless a claim of an allowed type covers it, and in Phase 0 none can. The patterns are:
  - `No. \d`, `#\d`, ordinals (`\d+(st|nd|rd|th)`);
  - `biggest|largest|highest(-grossing)?|top|record|all-time|ever|best|most|first|only|leading|dethron\w*|No\. ?1|number one`.

  Phrases such as "top 10" or "best picture" are handled by an allow-list. The Best Picture case is permitted when the comparison film has `is_best_picture`.
- **Completion vs. anticipation.**
  - Completion verbs (`passes|passed|surpass\w*|tops|topped|beats?|overtak\w*|crosses|crossed|outgross\w*|eclipses?`) without an anticipation marker require a completed claim.
  - Anticipation markers (`nears?|closing in|about to|on track|poised|set to|could|approaching|within \$`) require an anticipated claim.
  - A mismatch fails the pick. Example: "X Passes Y" on an `about_to_pass` candidate.
- **Every film named** in the headline must be the active film or the bound comparison film. Matching is by title and known aliases.
- **Figures:** as in revision 2. Every dollar amount must match an evidence value within the implied precision and must come from a `reported` metric. Unmatched figures fail the pick.

**Prompt edits** (an editorial change for your approval):
- Remove the rank example "[Film] Is Now the 4th-Biggest Horror Movie Ever".
- Remove "Becoming Only the X Movie This Year to Do So".
- Add: "Only assert relationships present in the candidate evidence; null values are unavailable and must not be cited."
- Set both length lines to 85.
- Add the `claims[]` output instruction.

**Claim test matrix (Increment 8):**
- Positive tests for each allowed claim type.
- Negative tests:
  - "No. 1" or "biggest … ever" with no claim;
  - a `rank` claim supplied;
  - "passes" on an `about_to_pass` candidate;
  - "nears" on a `just_passed` candidate fails, because anticipatory wording misdescribes a completed event;
  - a third film named in the headline;
  - a `relation=franchise` claim without the category;
  - a figure from a `missing` domestic metric;
  - a figure from a stale comparison;
  - a "horror" descriptor when the film isn't tagged horror;
  - `is_best_picture` allows "Best Picture winner";
  - an under-reported claim, where Claude omits a superlative from `claims[]` and the lexical scan still catches it.

**Production risk:** more picks will be dropped than today. The shadow comparison (§8, item E4) lists every drop and its reason for your review before activation.

---

### P0-7 Dry-run, kill switch, preflight, migration [R3-1]

**Files:** `runtime.py`, `preflight.py` (new), `scripts/migrate_sheets.py` (new), and the workflows.

**Runtime and sinks:**
- As in revision 2: `--dry-run`, recording sinks, `--fixtures`, `--no-claude`.
- Dry-run is permitted even when production is disabled, because it writes nothing apart from its artifact.

**Fail-closed gates:**
- Daily job: `if: ${{ vars.FARAMIR_ENABLED == 'true' || inputs.dry_run }}`.
- Corpus refresh job: `if: ${{ vars.FARAMIR_CORPUS_REFRESH_ENABLED == 'true' }}`. It stays off while the licensing decision is pending, and the new corpus job only offers TMDB repair.
- **An unset variable means disabled.** This reverses revision 2, so merging can't start production on its own.
- The code checks the same variables.

**Concurrency:** `concurrency: {group: faramir-production, cancel-in-progress: false}` on both workflows serializes runs, which protects the state, history and delivery protocols.

**Preflight** (every run, before any write). If any check fails, the run aborts with zero writes and sends an alert.

| Check | Requirement |
|---|---|
| Sheets schema | `faramir_meta.schema_version` equals the code's `EXPECTED_SCHEMA` (2) |
| Required tabs and headers | `state` (with the v2 columns), `state_meta`, `state_lkg`, `suggestions` (with the delivery columns), `runs`. Plus `weekend_history` and `weekend_snapshots` if collection is enabled. |
| Corpus | `corpus_schema_version == 2` and health not ABORT |
| State | Integrity decision table (P0-4) |
| Variables | `FARAMIR_ENABLED == 'true'` for live runs |

**`migrate_sheets.py`:**
- `--plan` (default) prints every change. `--apply` performs them.
- Steps:
  1. Validate the current `state` with the v1 rules.
  2. Create the new tabs.
  3. Append the new header columns to `state` and `suggestions`, writing only header cells.
  4. Set existing suggestion rows that have a `slack_ts` to `delivery_state=posted`.
  5. Compute and write `state_meta` and the initial `state_lkg`.
  6. Write `faramir_meta.schema_version=2` **last**, so preflight passes only after a complete migration.
- Idempotent: re-running it is a no-op.
- It has a `--rollback-plan` that lists what would remain if v1 were restored.
- Running it is a production action.

**Tests:**
- preflight rejects each mismatch;
- migrate `--plan` output on a fake v1 workbook;
- `--apply` is idempotent;
- the schema version is written last (a crash before it leaves preflight failing);
- an unset variable disables;
- dry-run works while disabled.

---

### P0-8 Tests, CI, dependencies

These are as in revision 2, now delivered by Increments 0 and 12:
- The giant-killer test is changed to assert genre-agnostic behavior: a shared genre doesn't block the angle; franchise, director or cast overlap does.
- The previous assertion is preserved as a regression test of the new rule, rather than deleted.
- Exact pins are copied from what CI resolves today on 3.11. The Actions log would confirm this; otherwise it's the PyPI resolution on the build date, recorded.
- A pytest step is added before the run and the commit.
- A `tests.yml` workflow runs on `pull_request` only, with no secrets. It is created on the branch and doesn't run until a PR exists.

---

## 4. Corpus correction evidence

Unchanged from revision 2. The full data is in `corpus_identity_audit_2026-10-22.csv`.

**Method:** TMDB `/find` on all 2,011 IMDb IDs, cross-checked against the title and year on BOM's live lifetime chart (2026-10-22). Of the 322 corrected rows:
- 311 have identical titles on BOM and TMDB;
- 10 are alternate titles of the same film (Seven/Se7en, Borat, Dracula, AVP, Leap!/Ballerina and others);
- *I Am Sam* (tt0277027) is no longer on BOM's chart, so it has no BOM title or year to compare; its TMDB identity resolved normally.
- 321 agree on year within ±1.

| Measure | Value |
|---|---|
| Rows / wrong identity | 2,011 / **322 (16.0%)** |
| Wrong rows in duplicate groups / invisible to a duplicate check | 214 / **108** |
| Film names in the corpus that point at another film's grosses | 110 |
| Unresolved after `/find` | **0** |
| Duplicate `tmdb_id` values after repair | **0** |
| Domestic `missing` / worldwide `missing` after repair | **1,011 / 12** (none can be confirmed zero in TMDB-only mode) |

**Examples:**

| IMDb ID | Corpus label | Verified identity | Dom / WW (BOM, for this IMDb ID) |
|---|---|---|---|
| tt0369610 | Jurassic World Rebirth (2025) | Jurassic World (2015) | $653.4M / $1.672B |
| tt6105098 | The Lion King (1994) | The Lion King (2019) | $543.6M / $1.662B |
| tt4154756 | Avengers: Endgame | Avengers: Infinity War | $678.8M / $2.052B |
| tt0114369 | Scream 7 (2026) | Se7en (1995) | $101.9M / $329.8M |
| tt0110478 | Top Gun: Maverick | Maverick (1994) | $101.6M / $183.0M |
| tt0092099 | Top Gun: Maverick | Top Gun (1986) | $183.5M / $360.6M |
| tt0113071 | The Dark Knight | First Knight (1995) | missing / $127.6M |
| tt2543472 | Wonder Woman (2017) | Wonder (2017) | $132.4M / $315.0M |
| tt0407304 | War of the Worlds (2025) | War of the Worlds (2005) | $234.3M / $603.9M (not in a duplicate group) |
| tt32553390 | A Place Called Silence (2024) | A Place Called Silence (2024), the IMDb-linked TMDB record | missing / $174.3M (not in a duplicate group) |

Revision 2's full 20-row table and the explanation of how matching prevents errors for remakes, re-releases and similar titles still apply. That explanation is in P0-2 above.

---

## 5. Weekend history (Google Sheets) [R3-2]

### 5.1 Tabs

**`weekend_history`**: append-only, one row per film in a collection.

| Column | Notes |
|---|---|
| `record_id` | `sha1(collection_id|source_film_key)[:20]`. Deterministic: the same content always produces the same ID. |
| `collection_id` | `sha1(source|weekend_start|canonical_content_hash)[:20]`. **Doesn't depend on collection time**, so a retry of the same content reuses it. |
| `source`, `source_url`, `weekend_start` (Fri), `weekend_end` (Sun) | |
| `source_film_key` (TN slug), `title_reported`, `slug_year` | |
| `tmdb_id`, `imdb_id`, `identity_confidence`, `rerelease` | |
| `source_rank` (blank if TN shows `-`), `derived_rank` (Faramir-derived, labelled), `prev_rank_reported` | |
| `weekend_gross`, `pct_change_reported`, `theaters`, `theater_avg`, `total_gross`, `days_in_release` | |
| `collected_at_utc`, `run_id`, `parser_version` | Informational only; never part of any ID |

**`weekend_collections`**: an append-only event log. This replaces revision 2's `weekend_snapshots`, whose time-based IDs made retries ambiguous.

| Column | Notes |
|---|---|
| `event_id` | `sha1(collection_id|event)[:20]` for `begin` and `commit`, so they are idempotent; `sha1(collection_id|event|run_id)` for `reject` and `abort` |
| `collection_id`, `source`, `weekend_start`, `event` | `event ∈ {begin, commit, reject, abort}` |
| `revision` | Set on `begin` as (highest committed revision for the weekend) + 1; reused when the same `collection_id` is resumed |
| `expected_rows`, `rows_hash` | On `begin`: the row count and a hash of the sorted `record_id`s |
| `verified_rows` | On `commit` |
| `reported_count`, `reported_total`, `parsed_total`, `status_detail`, `http_status`, `error` | |
| `at_utc`, `run_id`, `parser_version` | |

### 5.2 Transaction and recovery protocol

1. **Fetch, parse and validate.** If the chart isn't complete, append a `reject` event and stop. **No history rows are written.**
2. **Compute** the canonical content hash, then `collection_id` and the `record_id`s.
3. **Read** the collection events for this `weekend_start`, and the history rows whose `collection_id` matches. The read is filtered to that weekend's rows.
4. **Decide:**

   | Situation | Action |
   |---|---|
   | `commit` already exists for `collection_id` | No-op (`unchanged`). Idempotent. |
   | The latest committed collection for the weekend has the same content hash | No-op |
   | `begin` exists without `commit` (an earlier crash) | **Resume.** Reuse the stored revision and append **only** the `record_id`s that are missing. |
   | No events for `collection_id` | Append `begin` (with a new revision, `expected_rows`, `rows_hash`), then append all rows in one `append_rows` |

5. **Verify:** read back the rows for `collection_id` and count the **distinct** `record_id`s. If that equals `expected_rows` and the hash matches, append `commit`. Otherwise leave the collection uncommitted for the next run to resume, and alert.
6. **Readers** use only rows whose `collection_id` has a `commit` event, de-duplicate by `record_id` (first occurrence), and take the weekend's **current** revision as the committed collection with the highest revision. Uncommitted rows are invisible.
7. **Orphans:** a `begin` with no `commit` that is more than 48 hours old, and whose content is no longer the current chart content, gets an `abort` event. Its rows stay in the sheet (nothing is ever deleted) but are permanently ignored. They are counted in `runs.history_orphans`.
8. **Concurrency:** the workflow concurrency group serializes runs. If two runs still raced with identical content, they would compute the same `collection_id` and `record_id`s, so readers would de-duplicate, and `commit` is idempotent through its deterministic `event_id`.
9. **Provisional data:** a weekend is `provisional` until it has a committed collection captured on or after the following Tuesday [CAL], because TN doesn't label estimates. Phase 1b detectors use only non-provisional weekends for confirmed events.
10. **Preservation:**
    - Code has only an append path for these tabs, and a test enforces that.
    - A protected range is recommended at deployment.
    - A monthly JSONL export goes to an Actions artifact.

**Tests (Increment 11):**

| # | Case | Expected |
|---|---|---|
| H1 | Normal collection | `begin`, N rows, `commit`; readers see N |
| H2 | Same content collected twice | Second run writes nothing |
| H3 | Crash after `begin`, before rows | Next run resumes with the same `collection_id`, writes N rows, commits once |
| H4 | Crash midway (k of N rows written) | Next run appends only the N−k missing rows, then commits |
| H5 | Crash after rows, before `commit` | Next run verifies and commits; no new rows |
| H6 | Append reported failure but rows were written (duplicate on retry) | Duplicate physical rows; readers de-duplicate; commit verification counts distinct IDs |
| H7 | Content revised between runs | New `collection_id` with `revision=2`; revision 1 stays intact and committed |
| H8 | Crash on a revision, then content reverts to revision 1 | Matches the committed revision 1, so no-op; the uncommitted revision 2 is aborted after 48 hours |
| H9 | Incomplete or invalid chart | `reject` event only; zero history rows |
| H10 | Reader given uncommitted rows | Ignores them |
| H11 | Two runs with identical content | No logical duplicates; a single effective commit |
| H12 | Append-only enforcement | No update, clear or delete call ever made on these tabs |

**Volume:** about 65 rows per weekend × 1–3 revisions is roughly 10k rows a year, plus a few hundred events.

---

## 6. Data-source feasibility (per your decision)

| Source | Phase 0 use | Status |
|---|---|---|
| TMDB API | Corpus repair, active-film identity | Licensed API; **in use** |
| TN daily chart | Already fetched daily by production; now also parsed for slugs | **Existing collection, unchanged in volume** |
| TN weekend chart | Parser built and tested on fixtures; **live collection off** | Waiting on the licensing decision |
| BOM title pages (daily worldwide) | Existing production behavior, unchanged | Existing; terms question remains open (BOM's robots.txt prohibits scraping) |
| BOM lifetime charts | **Not used** (no rebuild); corpus refresh workflow gated off | Waiting on the licensing decision |

Sources not yet available (premium formats, distributor share, territories, budgets) are as listed in the plan's §7.

---

## 7. Interim production risk and the November 1 refresh

### 7.1 Current exposure (measured, read-only)

- On the 2026-10-21 TN daily chart, 10 films meet Faramir's active rule (≥ $20M domestic, ≤ 90 days).
- **On the domestic axis alone, *Resident Evil* ($150.2M) has 73 corpus comparisons within ±5%, and 19 of them (26%) carry the wrong identity.** Examples: rows labelled "X-Men: Days of Future Past", "Lee Cronin's The Mummy" and "Scary Movie".
- *Spider-Man: Brand New Day* has 2 comparisons in its window, both correct.
- The other 8 films are below the corpus domestic floor ($89.1M), so they get no domestic comparisons at all.
- I could not measure worldwide-axis exposure without new BOM requests.
- **Editorial impact:** until identities are fixed, any comparison pick can name the wrong film. One known structural loss: *Spider-Man* (2002) is labelled with *Brand New Day*'s TMDB ID, so the matcher skips it as "self" and the franchise-sibling story is never surfaced.

### 7.2 November 1 scheduled corpus refresh: risk assessment

`corpus_refresh.yml` runs on `0 9 1 * *`, which is **2026-11-01 09:00 UTC** (05:00 ET).

| What happens | Consequence |
|---|---|
| The old `build_corpus.py` scrapes about 15 BOM chart pages | **Automated BOM collection while the terms decision is pending** |
| About 3,000 TMDB title-only resolutions | A similar mislabel rate, probably on **different rows** as TMDB popularity drifts. The audit CSV would then no longer be a valid guide for editors. |
| Partial BOM failures `break` silently; no shrink or row-count gate | A truncated corpus, possibly empty, can be committed |
| Commits straight to `main` with `[skip ci]` and no checks | Bad data goes live at the 10:00 ET daily run with no warning |
| If the interim hotfix (R3 below) has been applied | It would be **overwritten** by corrupted data |

**Assessment:** the refresh is certain to run unless disabled. The impact is medium-to-high: it continues the corruption, makes it unpredictable, risks a truncated corpus, and raises a compliance question.

### 7.3 Recommended interim procedures (none taken; each needs your approval)

| # | Action | Type | Recommendation |
|---|---|---|---|
| R1 | Tell editors that Faramir's comparison film names and figures are unverified until further notice: check BOM before assigning, and use the audit CSV's list of 110 affected names | Editorial notice. **You send it**; I won't post to Slack. | **Do now** |
| R2 | Disable `corpus_refresh.yml` before 2026-11-01 09:00 UTC (GitHub → Actions → Disable workflow, which is reversible) | Production config | **Strongly recommended** |
| R3 | **Interim corpus hotfix:** commit a *v1-compatible* repaired corpus to `main` ahead of Phase 0. Identities corrected via `/find`; the schema is unchanged and zeros are kept, so the current code reads it as-is. | Production data change; needs push and merge approval | **Recommended** together with R2. It removes the 322 mislabels from live output within a day. Validation first: the existing test suite plus an offline run of the current pipeline on fixtures with the hotfix corpus. Reversible by reverting the commit. |
| R4 | Pause `daily.yml` until Phase 0 is activated | Production config | Only if R3 is declined and editors can't verify comparisons |

**R3 caveat:** the hotfix doesn't fix missing domestic figures, stale in-release comparison grosses, or the other Phase 0 defects. It only removes the wrong-identity errors.

---

## 8. Phase 0 acceptance checklist

**A. Regression**
- [ ] A1. The existing 30 passing tests pass unmodified.
- [ ] A2. The giant-killer tests assert genre-agnostic behavior, with regression coverage kept.
- [ ] A3. Offline fixture day: the candidate set matches the current code, except for listed differences caused by identity, missing metrics, stale comparisons or re-releases.
- [ ] A4. Milestones, suppression keys, stale-data guards and decrease guards are unchanged (parity tests).
- [ ] A5. Slack pick blocks are identical apart from metadata and "n/a" for missing values.

**B. Corpus**
- [ ] B1. 0 duplicate `imdb_id` / verified `tmdb_id`.
- [ ] B2. 0 rows with `worldwide < domestic` among `reported` values.
- [ ] B3. All 322 identities corrected; the CSV is reproduced offline; you check 20 by hand.
- [ ] B4. Grosses unchanged; zeros become `missing`; no `confirmed_zero`.
- [ ] B5. Coverage reported (domestic 49.7%, worldwide 99.4%).
- [ ] B6. A failing gate writes nothing.
- [ ] B7. A v1 corpus causes ABORT with an alert and no picks.
- [ ] B8. DEGRADED adds the header warning; row exclusions are logged with reason codes.
- [ ] B9. No domestic-axis candidate or figure ever uses a `missing` metric.

**C. Weekend history** (fixtures only)
- [ ] C1. Friday URL.
- [ ] C2. Parser exact on synthetic fixtures. Opt-in local check on the captured 2026-10-16 page: rank 1 *Street Fighter* $30,775,377 / 3,575 theaters; footer 62 films and $103,334,297.
- [ ] C3. H1–H12 pass.

**D. Failure scenarios**
- [ ] D1. Every state decision-table row passes.
- [ ] D2. Interruption at each write step behaves as specified, and the backup is never written from invalid state.
- [ ] D3. CP1–CP8 pass, plus the same-day re-run tests.
- [ ] D4. Claude refusal, `max_tokens`, timeout or schema failure gives zero picks with a reason.
- [ ] D5. TN 404 or a layout change gives `reject` or `UnknownLayout`, never fallback data presented as weekend data.
- [ ] D6. A TMDB outage leaves films unresolved and skipped.

**E. Claims and figures**
- [ ] E1. Unbound picks are dropped.
- [ ] E2. The claim matrix passes, including the under-reporting case.
- [ ] E3. Figures traceable; 85-character limit enforced; no truncation.
- [ ] E4. **Shadow comparison** over at least 5 fixture days (and, if approved, read-only live dry runs): every drop is listed with its reason and reviewed by you.

**F. Gates**
- [ ] F1. An offline dry run with network blocked makes zero writes.
- [ ] F2. An unset `FARAMIR_ENABLED` means disabled; dry-run works while disabled.
- [ ] F3. Preflight rejects each schema, corpus and state mismatch.
- [ ] F4. Migration plan is idempotent and writes the schema version last.
- [ ] F5. No write outside the sinks.

**G. CI**
- [ ] G1. Suite green on 3.11 with pinned dependencies.

---

## 9. Staged rollout and boundaries [R3-1]

### 9.1 Why ordering matters

| Problem combination | What would break |
|---|---|
| Old code with migrated Sheets | Old `write_state` and `write_suggestions` call `clear()` and rewrite with v1 headers, **wiping the new columns** |
| New code with the v1 corpus or unmigrated Sheets | Preflight aborts (by design) |
| Old code with a v2 corpus | The v2 file format (`{"meta","films"}`) makes old code fail to load it, and old comparisons on `null` raise exceptions. That fails loudly, never quietly. |

So migration must happen **after** old code stops running and **before** new code is activated. New code and the v2 corpus ship in the **same merge**.

### 9.2 Stages (each needs separate approval)

| Stage | Action | Production effect | Exit criteria |
|---|---|---|---|
| S0 | Local increments 0–12 on `v2/phase-0` | None | Checklist §8 complete, apart from items needing live access |
| S1 | Optional interim measures R2 and R3 (§7.3) | Refresh disabled; hotfix corpus live | Your approval |
| S2 | Push the branch and open a PR | None (no push trigger; PR tests run without secrets) | Review approved |
| S3 | Create repository variables `FARAMIR_ENABLED=false` and `FARAMIR_CORPUS_REFRESH_ENABLED=false` | None. Old workflows ignore them. | Variables exist |
| S4 | **Merge** right after a 10:00 ET daily run | Scheduled runs become **no-ops** (fail-closed). Daily picks pause from now until S7. | Merge done; the next scheduled run logs "disabled" |
| S5 | `migrate_sheets.py --plan`, which you review, then `--apply`, run through a manual `migrate.yml` dispatch with a confirmation input | Sheets gain the v2 tabs and columns; v1 data is untouched; schema version written last | Preflight passes |
| S6 | Dry run on `main` (`workflow_dispatch`, `dry_run=true`) with live reads | No writes; artifact only | You review the artifact and shadow comparison |
| S7 | **Activation:** set `FARAMIR_ENABLED=true`, optionally followed by a supervised manual run | Production live on v2 | First `runs` row and Slack output confirmed |
| S8 | Observe 3 scheduled runs | | No alerts or abort conditions |

Weekend-history collection and the corpus refresh stay **off** after S7. Each needs the licensing decision plus its own approval.

**Downtime:** S4 to S7 is the only gap in picks. The target is to keep it within one day by completing S5 and S6 on the same day as the merge.

### 9.3 Rollback

- **Immediate (preferred):** set `FARAMIR_ENABLED=false`. Nothing runs, nothing is lost, and the fix goes forward on a branch.
- **Full revert to v1 code:**
  1. Set `FARAMIR_ENABLED=false`.
  2. Export the v2 tabs and columns. `migrate_sheets.py --rollback-plan` lists them.
  3. Revert the merge, which restores both the v1 code and the v1 corpus. If the R3 hotfix was applied, its commit remains.
  4. The old workflow has no gate, so it **resumes on its next schedule**. Its first state write drops the v2 state columns. The extra tabs are ignored.
  5. Moving forward again requires re-running the migration.
- **State restore:** `restore_state.py` from `state_lkg` or an artifact.

### 9.4 Boundaries

**Allowed during local development without further approval:**
- local commits on `v2/phase-0`;
- a project-local Python 3.11 virtual environment;
- running tests and offline fixture dry runs;
- TMDB API reads (Increments 1 and 9);
- using the TN pages already captured during the audit, locally.

**Not allowed without explicit approval:**
- any push, PR, merge or repository variable change;
- any change to production Sheets or Slack;
- any workflow change on `main`;
- a live dry run using production credentials;
- **any new TN or BOM request** (no new collection; fixtures only);
- R1–R4;
- `restore_state.py`, `replay_picks.py` or `migrate_sheets.py` with `--apply`.

---

## 10. Open items

1. Agreement with behavior changes 1–11 in §1.3, including the prompt edits in P0-6.
2. Decisions on R1–R4. **R2 is time-sensitive (2026-11-01 09:00 UTC).**
3. Slack message metadata, to be verified in Increment 6.
4. The exact `anthropic` version production last installed, which needs access to the Actions logs.
5. The licensing decision, which gates weekend-history collection, corpus refresh and historical backfill.
