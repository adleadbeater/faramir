# Phase 5 completion report: the remaining strategies

Branch `v2/phase-5`, from `v2/phase-4`. Local only. Registry version `2026-10-23.p5`. **All 13 strategies and Discovery are accounted for.** No strategy is reported as operational without its source. All weekend strategies stay silent in production while history collection is off (PR-16).

## Delivered

| Strategy | Module | Rule (thresholds marked [CAL] are initial values) | Registry |
|---|---|---|---|
| 3 Streak | `strategies/streak.py` | Within tier T for N complete, consecutive weekends: No. 1 × 3, top 3 × 4, top 5 × 6 [CAL]. The highest qualifying tier is used. It repeats only at the minimum and then every 2 more weekends. **A missing weekend makes the event `unverifiable`:** a streak is never assumed to continue. | enabled, `history_required` |
| 6 Exit | `strategies/exit.py` | Theater drop ≥ 40% from a base ≥ 1,000 [CAL]. End of run only when the film is absent from 2 consecutive *complete* charts and its total had stalled, with a total ≥ $10M. A missing weekend is never read as an absence. PVOD is never claimed. | enabled, `history_required` |
| 7 Legs | `strategies/legs.py` | A hold in the cohort's top 10%, or a multiplier above P90. Cohort: genre × opening-theater band × week number, from committed history, needing ≥ 20 films [CAL]. Guards: theater growth > 10% (an expansion), and holiday weekends compared only with holidays. | enabled, `history_required`; silent until history accrues |
| 13 Dark Horse | `strategies/dark_horse.py` | Top 5. Out-grosses a film at the same stage (±1 week) with a verified scale advantage: ≥ 1.5× theaters [CAL], or franchise vs original (TMDB collection). Budget comparison only if verified budgets exist (none do). Marketing is never claimed. | enabled, `history_required` |
| 12 Star Record | `strategies/star_record.py` | Career best for a director or top-3-billed actor, counting corpus plus tracked films. **Provable only above the corpus floor and only when the corpus passed integrity.** Otherwise `unverifiable`. Cameos (billed 4th or lower) are excluded. A previous best where the person wasn't top 3 is labelled `ensemble`. Career totals are never claimed. | **disabled** (`live` data): career-record wording awaits editor approval (SR-1) |
| 11 Global Split | `strategies/global_split.py` | International share deviates > 25% from the genre-cohort median (n ≥ 15), ≥ 14 days after international release. Above 80% adds a revision-risk flag. Without `rollout_complete` the event is `unverifiable`. | **disabled**, `synthetic_only`: no rollout-date source |
| 10 Studio Battle | `strategies/studio_battle.py` | Distributor year-to-date rank change in the top 3, or a threshold crossing, with a canonical name map. An incomplete table makes the event `unverifiable`. | **disabled**, `blocked_no_source`: no distributor YTD source |
| 9 Premium Run | `strategies/premium_run.py` | The interface only. It refuses to run without a verified premium-format source. Discovery may surface reports only as `needs_verification`. | **disabled**, `blocked_no_source` |

### Integration

- **`weekend_runner`:**
  - detectors are `(series, ctx)` callables over an 8-week lookback;
  - gaps are kept as `None` (never filled);
  - the context carries history for cohort and absence checks.
- **`classify`:**
  - rule 3: Hold xor Streak, and only Legs may be secondary;
  - rule 4: Takeover beats Dark Horse, which becomes secondary;
  - rule 5: Star Record absorbs an Overtake of the talent's own previous best;
  - every merge records a reason code.
- **`strategy_engine`:** Star Record and Global Split run only when enabled. Tracked films are passed in for the career comparison.
- **`validate`:**
  - "third straight weekend" is ordinary ordinal language and is allowed;
  - "career-best" or "biggest of his career" wording requires Star Record evidence for that pick (SR-1).

## Tests

**918 passed, 2 skipped.** `tests/test_strategies_p5.py` has 28 tests. Each strategy has positive and negative fixtures. They also cover:
- overlap rules 3, 4 and 5;
- career-wording validation;
- the registry audit: 13 strategies, every disabled one with a reason, and every enabled one with its sources;
- disabled daily strategies not running.

The Phase 1b hand-checked weekend set was updated: its fourth weekend now also yields a top-3 Alpha Streak, re-checked by hand.

**Offline dry run:**
- 20 events (16 Overtake, 4 Chase);
- weekend strategies `history_collection_disabled`;
- calibration `pending`;
- 1 validated pick;
- **14 writes recorded, 0 performed**.

**Fixture defects found and fixed in this phase (the detectors were correct):**
- the Legs fixtures used TN chart text `"(new)"` instead of the parsed status `"new"`;
- a Star Record negative fixture lowered the corpus floor along with the earlier film;
- the Bravo totals in the synthetic weekends were not cumulative.

## Status and limits

- **Synthetic-only validation:** Streak, Exit, Legs and Dark Horse are correct on fixtures, but nothing has run on real weekend history (collection is off, PR-16). Legs will stay silent for months after collection starts: cohorts need 20 films.
- **Star Record:** the code works on the live corpus, but it is disabled pending the editorial decision on career-record wording.
- **Global Split, Studio Battle and Premium Run:** each awaits a source (rollout dates, distributor YTD, premium-format screens). Adding any of them is a new-data-source decision.
- **All [CAL] thresholds are initial values.**
