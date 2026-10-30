# Increment 9 review: active-film identity resolution (plus Increment 8 follow-ups)

Branch `v2/phase-0`, local only. Nothing pushed, deployed, or sent to Slack or Sheets. No live Slack, Sheets or TMDB access; every test runs on fakes.

## Part A — Increment 8 follow-ups (from the provisional acceptance)

### A1. Ordinary language vs. unsupported claims

The lexical scan now distinguishes the two. Phrases that are always a rank or record claim stay blocked:

- `No. N`, `#N`, number one;
- biggest, largest, highest(-grossing);
- top-N, top-grossing;
- record(s), all-time;
- best (except "Best Picture" for a Best Picture comp);
- dethrone, unprecedented, history-making.

Ambiguous words are blocked **unless** the surrounding words make them ordinary:

| Word | Allowed when | Still blocked |
|---|---|---|
| first, second … tenth, 3rd, 5th … | followed by a time unit: weekend, week, day, frame, month, year, outing, session. Also "first trailer/look/reviews" and "at first" | "First Film Since 2019", "The 4th Film to…", "second-biggest", "first-ever", "for the first time" |
| only | "only $3M", "only two weeks", "only 10 days", "only slightly/just/barely", "not only" | "the only sequel", "only two films have…" (a count claim) |
| ever | "ever since", "ever-growing" | "biggest … ever", "best run ever" |
| most | "most of", "at most", "for the most part" | "most successful" |
| leading | "leading man/lady/role/star" | "leading the box office" |

Unsupported ranking and record claims remain blocked. The requirement to support them with real evidence later is recorded as **SR-1** in [`STRATEGY_REQUIREMENTS.md`](../STRATEGY_REQUIREMENTS.md).

The regression guard is **SR-2**: `tests/test_validation.py::LEGITIMATE` holds 12 ordinary phrasings that must keep passing. Any future change that rejects one has made the title engine too strict.

### A2. Genre attribution (proposed rule, implemented lexically)

Each genre descriptor is attributed to a film, and that film must have the genre. Attribution works in this order:

1. **A bound title right after the phrase** → that film. Example: "the fantasy classic *Old Classic*".
2. **Apposition right before the phrase** → that film. Examples: "*Old Classic*, the fantasy hit" or "*New Hit*, the action film, …".
3. **A comparison verb in the sentence:**
   - a phrase before the verb describes the film doing the passing (the subject);
   - a phrase after the verb describes the comparison (the object);
   - a passive construction ("is passed by", "falls behind") inverts this.
4. **Otherwise** → the active film. Milestones only ever have the active film.

The checks report two different reasons:

- `genre_misattributed`: the genre belongs to the *other* film.
- `genre_unsupported`: the genre belongs to neither film.

Tests cover six correctly attributed and four misattributed cases. Examples:

- "This Fantasy Film Just Passed *Old Classic*", when only the comp is fantasy, is rejected as misattributed.
- "*New Hit* Passes a Beloved Fantasy Film" is accepted, because the phrase is the object and describes the comp.

**Proposed for approval, not applied.** The prompt hash is unchanged in this increment. The proposal: a `genre_descriptor` claim names the film it describes through `comp_tmdb_id`, with null meaning the active film. That way the declared claim is attributed, not only the wording (SR-3). Until then, declared genre claims accept either film's genres and the lexical rule does the attribution.

### A3. Shortening must not change meaning

`validate.meaning_changes(pick, new_headline)` runs after every shortening attempt, in addition to full re-validation. It rejects a shortened headline that:

- no longer names a bound film. A distinctive short form counts as named: "Ghost Protocol" for "Mission: Impossible – Ghost Protocol".
- reverses the direction of the comparison.
- changes completed or anticipated wording. This covers both the comparison status and the milestone status.
- drops, changes or adds a dollar figure. Rounding within the stated precision is allowed: "$1 Billion" ↔ "$1.0B".

A new general rule, `direction_reversed`, also applies to every pick, not only shortened ones. "*Old Classic* Passes *New Hit*" for a pick where *New Hit* did the passing is now rejected everywhere.

Tests:

- three faithful shortenings that pass;
- nine meaning changes that are caught:
  - films swapped;
  - passive reversal;
  - passes → nears;
  - comp dropped;
  - about to pass → passes;
  - $1B → $100M;
  - figure removed;
  - crosses → nears;
  - figure added;
- one end-to-end test where a reversed shortening never reaches Slack.

### A4. Recorded for later

- **PR-12** (in [`PRODUCTION_REQUIREMENTS.md`](../PRODUCTION_REQUIREMENTS.md)): before Phase 0 deployment, replay representative historical suggestions through the validator. Report accepted vs. rejected counts, the rejection reasons by category, and any legitimate headlines that are filtered incorrectly. It carries the known limitations:
  - third-film detection: no alias table, and one-word titles shorter than 7 letters are not checked;
  - no active-film freshness check for milestone claims;
  - declared genre claims are not attributed;
  - tense and direction rules use word lists, not a parser.
- **SR-1 to SR-4** in `STRATEGY_REQUIREMENTS.md`.

## Part B — Active-film identity resolution

### Rule: a title match alone never establishes identity

`faramir.identity.resolve()` decides each chart row. The first rule that applies wins:

| # | Rule | Method / confidence | TMDB calls |
|---|---|---|---|
| 0 | **Re-release.** The TN slug year is more than one year before the chart year, e.g. `Old-Classic-(1985)` on a 2026 chart. Excluded from picks and not tracked. | `rerelease` | none |
| 1 | **Editor override by TN slug** (`identity_overrides` tab, read-only to Faramir) | `override` / manual | details only |
| 2a | **State row with the same stored TN slug** | carried from the row | none |
| 2b | **Legacy state row (no slug yet) with the same normalized title *and* a compatible year.** Compatible means slug year ±1; with no slug year, release date within 60 days of the implied opening. The slug is backfilled. | `state_legacy_title_year` / legacy | none |
| 2c | The matched row's **`tmdb_id_override`** editor column differs from its `tmdb_id`. The row is re-keyed: domestic history carried; worldwide and IMDb taken from the new identity. | `state_override` / manual | details only |
| 3 | **TMDB search.** Conditions: exact normalized title (or original title); release year within slug year ±1; release date within 60 days of the implied opening (chart date − days in release + 1), where a US theatrical date may satisfy this when the primary date doesn't; **exactly one** candidate survives; that candidate has an IMDb ID. | `search` / high, or medium when the US date or implied year was needed | 2 searches, then details (plus up to 3 release-date lookups) |
| 4 | Otherwise **unresolved**: skipped, reported with its reason and every candidate considered, and flagged in the Slack header | — | — |

Further safeguards:

- **A row with a different slug is never matched by title.** A different slug means a different film.
- **Duplicates are not guessed.** When two chart rows resolve to one TMDB film, only the row continuing that state row is kept; if there is no such row, none is kept.
- `get_movie_details` now uses the shared retry/backoff helper.
- The legacy popularity search `resolve_tmdb_id` is no longer used for active films. It remains only in the opt-in legacy corpus full build.

### Identity-matching examples (all tested)

| Chart row | Candidates | Result |
|---|---|---|
| `Superman-(2026)` | Superman 1978, Superman 2025, Superman 2026-10-12 | **2026 film**. 1978 is rejected on year; 2025 is rejected because it falls outside the 60-day window. |
| `Saw-X-(2026)` | Saw (2004), Saw X | **Saw X**. The sequel is matched by its exact title. |
| `Saw-(2026)` | only Saw X | **unresolved** `no_match`. The sequel is never taken for the original. |
| `New-Hit-(2026)`, state row "New Hit" released 2018 | search finds New Hit 2026 | **2026 film by search**. The 2018 row is reported as "same title, different film". |
| `Le-Film-(2026)` | FR release in May; US theatrical 2026-10-10 | **resolved, medium** (US date) |
| `The English Title` with original title "Le Film" | — | **resolved** (original title counts as exact) |
| `New-Hit-(2026)` | two "New Hit" films, both inside the window | **unresolved** `ambiguous: 2`. Both are listed and neither is fetched. |
| `Old-Classic-(1985)` on the 2026 chart | — | **rerelease**, with no TMDB call |
| `Alpha-Film-(2026)` in `identity_overrides` → 7001 | — | **override / manual**, with no search |
| State row 5001 with `tmdb_id_override` 5002 | — | **re-keyed to 5002**. Domestic $140M → $152M is carried; 5001 is removed. |

### Unresolved-film scenarios and how they are reported

Every chart row appears in `report.json → identity`, with its status, method, confidence, reason and the candidates considered. Unresolved films also add one header line, for example:

> ⚠ Film identity unresolved — not tracked today: Alpha Film (no_match). Set its TMDB id in identity_overrides to include it.

Re-releases are expected, so they are reported but do not add a header warning.

| Reason | When |
|---|---|
| `no_match` | No candidate survives title, year and release window. Possible re-releases are labelled as such. |
| `ambiguous: N` | More than one candidate survives |
| `ambiguous_state` | Several state rows share the slug, or several legacy rows share the title and year |
| `no_imdb_id` | The single candidate has no IMDb ID |
| `details_failed` | TMDB details failed for an override or for the chosen candidate |
| `no_year_evidence` | No slug year and no days in release |
| `duplicate_identity` | Two chart rows resolve to the same film |

### State tab: three appended columns, with a safe upgrade

The state tab gains three columns: `tn_slug`, `identity_confidence` and `identity_method`. The production tab has 17 columns, so:

- **A 17-column legacy tab is still valid.** Its checksum is computed on its own header, so it matches the existing meta.
- **The first write backs it up on the new header** (blank new columns, identical content), writes the 20-column state, and verifies both.
- **Restoring from a legacy-layout backup works the same way.** `restore_state.py` also accepts a legacy source.
- **Editor values are unaffected.** The new columns are appended, so `tmdb_id_override` and `notes` keep their positions.

Tests cover: run_daily on a legacy tab (PROCEED, then upgraded state and backup, then the next run PROCEEDs); a RESTORE from a legacy backup that keeps the editor override; and init on a legacy tab (the existing tests). The deployment check is recorded as **PR-13**.

### Differences in story candidates (offline fixture day, Increment 8 vs 9)

|  | Increment 8 | Increment 9 |
|---|---|---|
| Candidates sent to Claude | 17 | 17 (same) |
| Pick | Beta Story vs Superman (candidate `5533665f…`) | same |
| Alpha Film | "Could not resolve TMDB", skipped | `unresolved: no_match`, skipped, **header warning** |
| Old Classic (1985) | Searched, "Could not resolve", skipped | **re-release**, no search, skipped |
| Beta Story | Title-matched to state | `state_legacy_title_year`, slug backfilled |
| Recorded writes | 11 | 14 (one-time `state_lkg` backup on the new header + 2 meta) |
| State header | 17 columns | 20 columns |

The fixture day has no film that resolves by search, so the candidate set is unchanged. The search path is covered end to end in `test_identity.py`: Alpha Film resolves to 7001, and the 1994 remake is rejected.

In production, expect these differences:

1. **New films whose title matches an older film now get the right identity.** Before, the most popular search result won, often the wrong film. Their comps, franchise and director angles change accordingly.
2. **Films that can't be pinned down are skipped and flagged,** where they used to be guessed.
3. **Re-releases no longer enter state.**
4. **A legacy row whose stored release year disagrees with the slug no longer matches by title.** The film is resolved afresh and the stale row eventually retires. That film loses one day of `domestic_yesterday`, so it has no "just passed" candidate on that day.

## Tests

**511 passed** (474 after Part A, 474 → 511 with Part B), up from 422 at Increment 8.

- `tests/test_validation.py`: 61 → 113 tests:
  - 12 legitimate phrases;
  - 11 phrases that stay blocked;
  - 6 direction cases;
  - 13 genre-attribution cases;
  - 12 meaning-preservation cases;
  - 1 end-to-end test.
- `tests/test_identity.py`: new, 37 tests (29 unit, 8 through run_daily or StateStore).

## Sample dry run

`sample_dry_run/` holds the offline fixture day, run with no credentials and an emptied environment:

```
python scripts/run_daily.py --dry-run --fixtures fixtures/offline/2026-10-22 --out-root docs/v2/increment9_review/sample_dry_run
```

Results:

- 14 writes recorded, 0 executed;
- `identity` shows Alpha Film unresolved, Beta Story resolved (legacy) and Old Classic as a re-release;
- the header preview carries the unresolved line;
- `state_snapshot_proposed.json` shows the 20-column header with Beta Story's slug.

## Remaining risks

- **The 60-day release window can reject legitimate films.** Wide expansions of platform releases, and festival films whose TMDB primary date and US date are both far from the TN opening, become `no_match`. They are flagged, not guessed, and an override fixes them. TN's days-in-release for an expansion counts from the platform date, which usually keeps them inside the window.
- **Editor-supplied override for a film not in state** needs the new optional `identity_overrides` tab (PR-13). Without it, editors can correct only films already in state, via `tmdb_id_override`.
- **Re-release rule only uses the slug year.** A re-release of a film from last year (e.g., a December 2025 film re-released in fall 2026) isn't caught by rule 0. It fails the release window ("possible re-release") and is reported unresolved rather than tracked.
- **TMDB search returns the first page only** (20 results), twice: once with the year, once without. A very common title whose target film ranks past 20 on both queries would be `no_match`, not wrong.
- **Legacy rows are trusted when title and year agree.** They were originally resolved by popularity search. A wrong identity that happens to share the exact title and year (very unlikely) would persist until an editor sets `tmdb_id_override`. These rows are labelled `confidence: legacy`, so they can be audited.
- **The re-key drops the old (wrong) row** once its replacement is written. Its worldwide history is discarded on purpose, because it belonged to the wrong IMDb ID.
- **Increment 8 limitations are unchanged** and tracked under PR-12: no title alias table, short one-word titles unchecked, no milestone freshness check, and word-list tense and direction rules.

## Next: Increment 10

TN chart parser and Friday weekend URL. This is **parser only, no collection**: added functions in `bom_fetch.py`, existing functions unchanged, and synthetic-fixture tests. It does not expand BOM or TN scraping.
