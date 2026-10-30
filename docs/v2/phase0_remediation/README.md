# Phase 0 acceptance remediation

This commit fixes the defects found in the independent review of the Phase 0 package. It is local to `v2/phase-0` and was not pushed.

- **Not done in this commit:** no merge, no Sheets migration, no live API check, no production-workflow edit (one comment changed on the branch only), no deploy, and no Phase 1 work.
- **Production remains NO-GO.**

## Results

| | Before (`c1ce617`) | After (this commit) |
|---|---|---|
| New regression tests (`tests/test_remediation.py`, 63) | **40 failed**, 23 passed (`before_fix_test_results.txt`) | **63 passed** |
| Full suite | 694 passed, 2 skipped | **757 passed, 2 skipped** (the 2 opt-in real-capture tests also pass with `--run-local-capture`) |
| Offline dry run | ok | ok: preflight passes, 17 candidates, 1 pick validated, 14 writes recorded, 0 performed, 0 errors |

The "before" column comes from running the new test file against `c1ce617` in a throwaway worktree, with the missing `parse_overrides` stubbed so the tests could be collected.

### The reproduced defects, before → after

| Defect | Before | After |
|---|---|---|
| "Alpha Film Passes Beta Story With $400M Domestic" (domestic $120M/$100M, worldwide $400M/$300M) | `ok=True` | **rejected:** `figure_misattributed: $400M labelled domestic matches no domestic evidence ($400M is Alpha Film worldwide)` |
| "Christopher Nolan's Alpha Film Passes Beta Story" (no credit in the evidence) | `ok=True` | **rejected:** `person_unsupported: 'Christopher Nolan' is not in the evidence for the active film's director or cast` |
| `identity_overrides` with one slug → 7001 and 7002 | the last row won silently (7002) | **refused:** the film is unresolved (`override_conflict … conflicting tmdb_ids ['7001', '7002'] (rows 2, 3)`), reported in the run report and the Slack header, and nothing is guessed |
| A delivery left `unconfirmed`, later found in a complete history read | stayed `unconfirmed` forever | becomes **`posted`** with its Slack timestamp. A failed or incomplete read changes nothing, and nothing is reposted. |
| A committed history row's gross edited afterwards | readers served the edited figure (count-only check) | the weekend reads **`integrity_error`** (unknown). A re-collection **appends** a valid copy, and readers serve the original figure. The edited row is kept and ignored. |

## 1. Axis-specific factual validation (`src/faramir/validate.py`)

Each dollar figure is now checked against the value **of the metric and the film it is attributed to**. Previously it was checked against any number anywhere in the evidence.

- **Metric.**
  - An axis word next to the figure decides the metric. It is looked for first after the figure (within 4 words, e.g. "$400M worldwide"), then before it (within 5 words, e.g. "domestic total of $120M", "worldwide, it has $400M"), always within the figure's own clause.
  - The axis words are: worldwide/global, domestic/stateside/North America/U.S., and international/overseas/abroad.
  - **An unlabelled figure is read as the bound event's axis.**
- **Film.** "Title's [≤3 words] $X" or "Title ($X" ties the figure to that film. Otherwise the figure may belong to either bound film.
- **Allowed values:**
  - each film's *reported* gross on that axis;
  - the international split (worldwide − domestic);
  - the gap between the two films on that axis;
  - for milestones, the threshold and the gap to it.
- **Diagnostics:**
  - `figure_misattributed`: the metric or film is wrong, and the message says what the number actually is;
  - `figure_axis_unlabelled`: an off-axis figure without a label;
  - `unverified_figure`: the number matches nothing.

Tests:

- **9 legitimate cases** pass: parenthetical figures, labelled worldwide figures, the domestic gap, the worldwide gap, international and overseas figures, possessive figures.
- **8 misattributions** are rejected: worldwide labelled domestic, domestic labelled worldwide, an unlabelled off-axis figure, another film's figure, a gap on the wrong axis, an invented figure.
- **Milestones:** the threshold is tied to its axis.
- **Worldwide events:** an unlabelled figure is read as worldwide.

## 2. Named talent and filmmakers (`validate.py`, `binding.py`)

- **The bound evidence now carries credits:** `director` and `lead_cast` for the active film (from its TMDB details) and the comparison film (from the corpus). Claude already received these in its payload, but the validator could not check them.
- **`person_checks` detects named people in three forms:**
  - a possessive name ("Christopher Nolan's", or a one-word "Nolan's" directly before a bound title);
  - a role cue ("directed by", "starring", "director", "led by", …);
  - an "X-led", "X-directed" or "X-starring" phrase.
- **What is checked:** each name must be in the credits of the film it is attached to. That is the title right after it, otherwise the active film; a role cue with no title may match either film. Surname-only matches count.
- **A comparison film's credits count only when its identity is `verified`.**
- **Diagnostics:** `person_unsupported` (in nobody's credits) and `person_misattributed` (credited on the other film).
- **No false positives on headline wording.** Common capitalized headline words (Box, Office, Weekend, Halloween, Passes, …) are never read as names, so Title Case headlines without people are not flagged.

Tests: 10 supported or name-free headlines pass, 7 unsupported or misattributed ones are rejected, unverified comparison credits don't count, binding carries the credits, and the daily run drops a "Christopher Nolan's …" pick.

**Known limits** (SR-10):

- A name with no cue ("…as Tom Hanks fans return") is not detected.
- Studios ("Pixar's Alpha Film") are always unsupported, because no evidence carries distributors.
- A structured `talent` claim type is proposed for Phase 1.

## 3. Ambiguous identity overrides (`src/faramir/identity.py`, `scripts/run_daily.py`)

`parse_overrides` replaces the reader in `run_daily`:

| Tab content | Result |
|---|---|
| The same slug twice, with the same tmdb_id | One mapping, plus a note |
| The same slug with different tmdb_ids | `OverrideConflict`: the film is unresolved (`override_conflict`), and neither the override, the state row nor a search is used |
| A slug with a malformed tmdb_id (alone or next to a valid row) | `OverrideConflict` (malformed) |
| A tmdb_id with no slug, or an unknown header | Reported, ignored |
| A malformed `tmdb_id_override` cell in `state` | The film is unresolved (`override_malformed`). This was silently ignored before. |

Every problem is logged, recorded in `report.json → identity_overrides.problems`, and, for films on today's chart, shown in the Slack header's unresolved-identity line. There are 9 tests, including one daily-run test.

## 4. Slack reconciliation (`src/faramir/delivery.py`)

- **`unconfirmed` is re-checked on every complete history read** (`RECHECK`). A positive identification, by message metadata or the visible `ref <id>`, sets `posted` with its Slack timestamp and a note.
- **Absent, outside the window, or any failed or incomplete read:** no change.
- **The ban on automatic reposting is unchanged:** `unconfirmed` is still not in `REPLAYABLE`, and a manual replay still requires `--checked-channel`.

There are 6 tests: found by metadata, found by visible ref, three failed or incomplete reads, and still-absent-never-reposted.

## 5. Weekend-history integrity (`src/faramir/history.py`)

**Collection is still off.**

- **What the commit records.** `begin` and `commit` events now carry `record_hashes` (`record_id:content_hash` for every record), in a new last column of `weekend_collections`.
- **A physical row is valid only if its content hash equals the committed hash.** The hash covers every column except `retrieved_at_utc` and `run_id`.
- **Readers** (`check_records`, `read_weekend`, `collection_rows`):
  - serve exactly one valid copy per committed record;
  - ignore and count *partial* rows (blank required cells, i.e. cut off mid-write), *altered* rows (complete but with different content, such as an edited gross, even a careful edit) and *unexpected* rows;
  - if any committed record has no valid copy, the weekend reads `integrity_error`, which is treated as unknown. It never falls back to an older revision or to partial data.
- **Repair is append-only.** Collecting the same content again sees the damage and appends fresh copies of the damaged records, verifies them, and adds an `observe` event saying "repaired". Nothing is updated or removed.
- **Resume.** A record counts as present only if it has a valid copy, so a row cut off mid-write is re-appended.

There are 7 tests: a modified gross that is detected and repaired, an altered duplicate ignored, a partial row during collection, a partial row after commit, a careful tamper, a missing record hash, and repair using appends only.

The offline fixture's header-only `weekend_collections` tab gains the new column. That tab doesn't exist in production; the migration creates it.

## 6. Schedule (`.github/workflows/daily.yml`: comment only)

`cron: '0 15 * * *'` is UTC, which means:

- **11:00 EDT** from the second Sunday of March to the first Sunday of November;
- **10:00 EST** the rest of the year.

The comments on `main` and on the branch both said the reverse. **Only the branch comment is corrected. The cron is unchanged** (PR-20).

**Proposal for 10:00 Eastern all year** (not applied; needs approval):

1. Two UTC crons: `'0 14 * * *'` (10:00 EDT) and `'0 15 * * *'` (10:00 EST).
2. The first job step computes the time in `America/New_York` and continues only if the hour is 10, so exactly one of the two runs proceeds each day; the other is a no-op that logs "outside the 10:00 ET window".
3. GitHub cron can start late, often by minutes and occasionally by more than an hour. So the guard should accept from 10:00 to 11:59 ET **and** skip if today's run already completed (`state_meta` shows a generation written today). Same-day re-runs are already safe (idempotent logging, delivery suppression, a single header), so the guard only avoids wasted runs.
4. Daylight-saving transition days resolve correctly, because the check uses real local time.

**Alternatives:**

- accept 10:00/11:00 drift (no change);
- `'0 14 * * *'` only, which gives 10:00 EDT and 09:00 EST, possibly before the morning's data is published.

## Files and lines changed (this commit, new-file line numbers)

| File | Lines | What |
|---|---|---|
| `src/faramir/validate.py` | 20–31 | docstring: figure and person rules |
| | 365–456 | `NOT_A_NAME`, `PERSON_ROLE_RE`, `_people`, `_same_person`, `_trim_name`, `person_checks` (new) |
| | 473 | `lexical_checks` calls `person_checks` |
| | 484–598 | `AXIS_LABELS`, `_axis_of`, `_figure_axis`, `_figure_film`, `_film_values`, `figure_checks` (rewritten) |
| `src/faramir/binding.py` | 103–107 | evidence adds `director` and `lead_cast` (active and comparison) |
| `src/faramir/identity.py` | 106–171 | `Overrides`, `OverrideConflict`, `parse_overrides` (new) |
| | 339, 371–374, 384–388 | `resolve`: refuses an override conflict and a malformed state override |
| `scripts/run_daily.py` | 57–70 | `_identity_overrides` delegates to `parse_overrides` |
| | 356–357 | report `identity_overrides` |
| `src/faramir/delivery.py` | 13–16, 49, 99, 134–141 | `RECHECK`: unconfirmed → posted on positive identification |
| `src/faramir/history.py` | 83, 89–91 | `record_hashes` column, `REQUIRED_CELLS` |
| | 179–225 | `content_hash`, `record_hashes_cell`, `parse_record_hashes`, `RecordCheck`, `check_records` (new) |
| | 267–318 | plan: `repair` action; resume counts only valid copies |
| | 335–353 | events carry `record_hashes`; `verify` uses content hashes |
| | 382–391 | `execute`: append-only repair |
| | 480–517 | `read_weekend` and `collection_rows`: content-verified reads, `integrity_error` |
| `.github/workflows/daily.yml` | 7–10 | schedule comment corrected (cron unchanged) |
| `fixtures/offline/2026-10-22/sheets.json` | 274–275 | `weekend_collections` header plus the `record_hashes` column |
| `tests/test_remediation.py` | new | 63 regression tests |
| docs | — | `PRODUCTION_REQUIREMENTS.md` (PR-20, PR-21), `STRATEGY_REQUIREMENTS.md` (SR-10), this file, `before_fix_test_results.txt` |

## Existing behavior altered

1. **Figures:** an unlabelled figure that matches only the *other* axis is now rejected ("label its metric").
   - **International and overseas figures (worldwide − domestic) and the gap on the non-event axis are now accepted.** Before, they were wrongly rejected as unverified.
2. **Named people** not in the attached film's credits, and studio possessives before a title, are now rejected.
3. **`evidence_json` and `evidence_ref`** now include the credits, so the offline fixture's evidence reference changed (`b362a21…` → `13440d5c…`). Candidate ids are unchanged.
4. **Overrides:** a conflicting or malformed slug used to resolve to the last valid row; it is now unresolved. A malformed `tmdb_id_override` in state used to be ignored; the film is now unresolved.
5. **An `unconfirmed` delivery** can now become `posted`. That was previously terminal until manual action.
6. **History:** a new `weekend_collections` column; readers verify content; a new `repaired` result and `integrity_error` reads. Collection is still disabled, and no production tab exists.
7. **No change** to candidates, selection, the prompt (its hash is unchanged), state, suggestions writes, preflight or migration steps.

## External writes remain guarded

- **No new write path.**
  - History repair appends through `sheet.append_values`, which calls `authorize`.
  - The unconfirmed → posted change goes through `suggestions.update_by_id`, which uses guarded `update_cells`.
- `test_no_direct_writes.py` (static: Sheets writes only in `sheet.py`, each guarded; Slack posts only in `slack_post.py`, each guarded) passes.
- The history append-only tests pass (only `worksheet`, `get_all_values` and `append_rows` touch the history tabs).
- **The offline dry run performed 0 writes.**

## Updated deployment blockers

**Fixed by this commit:**

- validator axis and figure attribution;
- unsupported talent attribution;
- ambiguous overrides;
- the terminal `unconfirmed` state;
- history row integrity.

**Added:**

- **PR-20:** schedule decision.
- **PR-21:** independent re-review of this commit before S2.

**Unchanged and still blocking** (see `docs/v2/PRODUCTION_DECISIONS.md` and `PRODUCTION_VALIDATION_PLAN.md`):

1. Disable the legacy corpus refresh on `main` before 2026-11-01 09:00 UTC (PR-19). Time-sensitive and independent of v2.
2. Data-source permission decision (D1).
3. Historical validator replay and the 5-day shadow comparison (PR-12, E4; V1/V2). These matter more now, because the figure and person rules are stricter.
4. Live structured-output check (PR-11, V5).
5. Slack scopes, channel identity and timestamp preservation (PR-3, PR-8, PR-10; V4).
6. Migration rehearsal on a copy (PR-7, PR-13; V3).
7. GitHub Actions dry run and overlap test (PR-1, G1; V6).
8. Rollback rehearsal (PR-2, D5; V7).
9. `anthropic` pin confirmation (PR-4).
10. Protection of the suggestions tab (PR-6, D3) and artifact retention (PR-18, D4).
11. Human or independent re-review of the remediation (PR-21).
