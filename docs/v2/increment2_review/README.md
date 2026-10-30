# Increment 2: daily corpus loading and fail-safe

These notes are for independent review. The authoritative rules are in the docstrings of `src/faramir/corpus.py`.

## Version compatibility

- The daily run loads **only** `data/corpus.v2.json`. A test run can override the path with `FARAMIR_CORPUS_PATH`.
- `load_corpus_v2()` never falls back to the v1 corpus. Each problem below produces **ABORT** with a named reason:

  | Problem | Reason |
  |---|---|
  | v1 list (legacy full-build output) | `legacy_format` (the message names `--repair-identities`) |
  | missing file | `corpus_missing` |
  | unreadable file | `corpus_unreadable` |
  | malformed file | `bad_format` |
  | wrong schema version | `schema_version` |

- **What happens on ABORT:**
  - The daily run sends one Slack alert: "comparison data failed integrity — no picks today", with the reasons.
  - Claude is not called.
  - No pick is posted and no suggestion is logged.
  - State is still updated.

### How a legacy corpus is kept out after deployment

| Layer | Mechanism | When it takes effect |
|---|---|---|
| 1. Loader | Rejects v1 files (above). The v2 path `data/corpus.v2.json` differs from the file the legacy refresh writes (`data/corpus.json`). | This increment |
| 2. Build script | `scripts/build_corpus.py` with no arguments now **refuses** the legacy full build (exit code 3); it runs only with `--legacy-full-build`. The current `corpus_refresh.yml` calls it with no arguments, so once this code is on `main`, a scheduled refresh fails before its commit step and commits nothing. | On merge (not authorized) |
| 3. Workflow gate | `FARAMIR_CORPUS_REFRESH_ENABLED == 'true'` gate. Unset means disabled. | Increment 12 |

**Before deployment, none of these protect production.** The Nov 1 run uses `main`'s current code, so recommendation R2 (disable `corpus_refresh.yml` before 2026-11-01 09:00 UTC) still stands. It needs your approval, and I have not changed the live workflow.

## Missing grosses

- `faramir.corpus.reported(film, metric)` returns a reported positive integer or `None`. A missing gross is never zero.
- The candidate index is built per metric:
  - A film with a missing domestic gross stays eligible for worldwide comparisons, and the reverse holds too.
  - All four combinations of reported and missing are tested on both axes.
- **Genre rank and "notable":** a film with a missing worldwide gross can't hold a genre rank and doesn't count as notable for giant-killer purposes. Asymmetry cohorts skip missing values.
- **Slack** shows `n/a` for an unavailable gross. It never shows `$0` or `—`.
- **Interim figure guard,** in `run_daily._validate_pick_figures`, until Increment 8:
  - It drops a pick whose axis gross is unavailable.
  - When the comparison film has any missing gross, every dollar figure in the prose must match a reported figure (comparison film, active film or threshold) within 2%.
  - It drops any pick naming a comparison film that isn't a usable corpus record.
- **Milestones** read the active film's figures only. An active film without a worldwide figure produces domestic milestones only.

## Health classes

Thresholds marked [CAL] need calibration.

**ABORT**: no picks, plus an alert. Any of:
- the file is missing, unreadable or malformed;
- legacy v1 format;
- schema version ≠ 2;
- `gross_as_of` missing, unparseable or in the future;
- the row count or coverage recorded in the file's header doesn't match its rows (an edited or truncated file);
- duplicate `imdb_id`;
- duplicate verified `tmdb_id`;
- invalid rows > 1% [CAL];
- unresolved identities > 2% [CAL];
- fewer than 1,800 usable rows [CAL];
- worldwide reported share < 95% [CAL].

**DEGRADED**: picks are allowed from **usable records only**, and the Slack header gets one warning line. Any of, with no ABORT condition:
- 0 < invalid rows ≤ 1%;
- 0 < unresolved identities ≤ 2%;
- corpus older than 45 days [CAL].

Excluded records are listed by reason in the run log. They move to the `runs` tab in Increment 3/4.

**OK**: none of the above.

Domestic coverage is **not** a criterion. The 49.7% figure is the known limit of the TMDB-only corpus and is enforced per comparison instead.

**Current result:** the repaired corpus is **OK** today: 2,011 usable rows, no exclusions without chart evidence. It becomes DEGRADED (age) after 2026-11-15.

## Stale-gross rule

A record is excluded as stale **only on verifiable evidence that its gross moved after `gross_as_of`.** Release-date proximity alone is never used.

- **S1, identity evidence:** a Faramir state row with the same `imdb_id` (or the same `tmdb_id`, only if the state row has no `imdb_id`) was seen on a chart dated after `gross_as_of`. The chart date is taken as `last_seen − 2 days` because of the day-2 fallback, so the rule can miss a day of evidence but never invents it.
- **S2, title evidence:** today's chart, dated after `gross_as_of`, lists the same normalized title, and its cumulative domestic gross is consistent with being the same film:
  - at least 90% of the record's reported domestic gross; **and**
  - for a record released before the chart's previous year (a re-release candidate), at most 150% [CAL].
  - A much larger total on an old title is a different film sharing the name, as with a remake.
  - If the record's domestic gross is missing, S2 doesn't apply.

**Tests:**

| Type | Case | Covered by |
|---|---|---|
| False positive | Film released 2 weeks before `gross_as_of` but not on the chart | Not stale |
| False positive | New "Superman" at $20M or $300M vs *Superman* (1978) at $134M | Not stale |
| False positive | Chart dated on `gross_as_of` | Not stale |
| False positive | Missing domestic gross | Not stale |
| False positive | Mismatched `imdb_id` | Not stale |
| False negative | *Back to the Future* (1985) re-release on the chart | Stale |
| False negative | *Resident Evil* (2026) growing from $108M to $150M | Stale |
| False negative | State row seen after the gross date | Stale |

**On the 2026-10-21 chart, the rule excludes exactly four records,** each a genuine case: *Spider-Man: Brand New Day*, *The Odyssey*, *Back to the Future* and *Resident Evil* (2026).

**Known gap:** a film still earning more than 90 days into its run that Faramir doesn't track, under a title that differs between The Numbers and the corpus. This is narrowed in Increment 10, when chart slugs give exact identities.

## Candidate comparison

See `candidate_comparison.md` and `candidate_differences.csv`. To reproduce: `python scripts/compare_candidates.py`.
