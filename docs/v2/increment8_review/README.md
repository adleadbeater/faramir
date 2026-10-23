# Increment 8 review: figure and claim validation, 85-character limit

Branch `v2/phase-0`, local only. Nothing pushed, deployed, or sent to Slack or Sheets.

## What changed

Every pick that survives binding (Increment 7) now passes through `faramir.validate.validate_pick` before it is logged or posted. A pick that fails is dropped, and the reason goes into `report.json → validation`. The single exception: when the only failure is length, the pick gets one shortening call and is then re-validated in full.

| Check | Rule | Reason prefix |
|---|---|---|
| Declared claims | Each `claims[]` entry must be supported by the bound candidate (table below). The types `rank`, `record`, `superlative`, `chart_position`, `first_or_only` and `count` are always rejected. At least one event claim must match the candidate. | `claim_mismatch`, `claim_contradicted`, `claim_unavailable_metric`, `claim_unsupported`, `claims_missing_event` |
| Lexical: rank/superlative | Scans the headline and angle, with the active and comp titles masked, for `No. N`, `#N`, `number one`, ordinals, `biggest`, `largest`, `highest(-grossing)`, `top-N`, `top-grossing`, `record(s)`, `record-breaking`, `all-time`, `ever`, `best`, `most`, `first`, `only`, `leading` and `dethron*`. "Best Picture" is allowed only when the comp has `is_best_picture`. | `lexical_superlative` |
| Lexical: tense | Completed verbs (passes, passed, beat(s), tops, crosses, …) on an anticipated event, with no anticipation marker, fail. Anticipation markers (nears, closing in, about to, poised, could, within $, …) on a completed event, with no completed verb, fail. | `tense_mismatch` |
| Lexical: genre | A genre word followed by a film noun ("horror movie", "animated hit") must be a TMDB genre of one of the two films. | `genre_unsupported` |
| Lexical: third film | A title from the corpus or today's payload, other than the two bound films, may not appear. Only titles of 2+ words, or one word of 7+ letters, are checked. | `third_film_named` |
| Figures | Every `$` amount must match an evidence value within the precision it is written at: active current gross, comp *reported* gross, threshold, or the gap. A missing metric is never a source. The comp's metric on the event axis must be reported, and a comp whose worldwide gross is below domestic is a partial run. | `unverified_figure`, `comp_metric_unavailable`, `comp_partial_run` |
| Length | Headline is 85 characters or fewer, counted exactly and never truncated. If length is the **only** failure, there is one `shorten_headline` call and the result is fully re-validated. A failed call, a result still too long, or a result that breaks another rule all drop the pick. | `headline_too_long` |

Claim support (Phase 0):

| Claim | Supported when |
|---|---|
| `passed_comp` | Candidate is `just_passed`, with the same comp and axis. The comp metric is reported and active ≥ comp. |
| `nearing_comp` | Candidate is `about_to_pass`, with the same comp and axis. The comp metric is reported and active < comp. |
| `crossed_milestone` / `nearing_milestone` | Milestone candidate whose `milestone_type`, threshold and axis all match. |
| `relation` | `franchise` → `franchise_sibling`; `same_director` → `creative_head_to_head_director`; `same_star` → `creative_head_to_head_cast`. The comp identity must also be `verified`. |
| `genre_descriptor` | The genre belongs to either film (see deviation 1). |

Stale comps never reach validation. Increment 2 excludes them from the usable corpus, so they produce no candidate, and binding rejects any pick that names one. `test_pick_with_comp_outside_usable_corpus_is_dropped` covers that path.

## Prompt diff (approved edits)

- Original SHA-256 `0cc056f0…b0060b` (@ f2a0ce4). New: `775de5f2716767678cc87b80f891cd8a391b6fe82f6ba8c90c230891b2b2c4d4`. `test_selection.py` pins both hashes.
- Both length lines now say 85. They previously disagreed: "max 75 chars" in one place and "Max 90 characters" in another.
- Removed the rank example "Is Now the 4th-Biggest Horror Movie Ever".
- Removed the rank number guidance ("a rank (4th-Biggest Horror Ever)").
- Removed "Becoming Only the X Movie This Year to Do So".
- Added an **Evidence rules** section covering:
  - `angle_category` must come from the chosen candidate;
  - no null citing;
  - no rankings or superlatives;
  - figures must come from the payload, at the precision written;
  - tense must match the event;
  - every assertion must be listed in `claims`.
- The output example now includes `claims`, and there is a list of claim types.
- New `HEADLINE_REWRITE_PROMPT` for the one-shot shortening call: same facts, same films, same tense, nothing added. It is a structured `Headline` schema with `max_tokens` 256, and its usage is recorded as `shorten_headline`.

`full git diff` for the prompt: `git diff edcb0c0 -- src/faramir/claude_select.py`.

## Examples

| Headline / angle | Event | Result |
|---|---|---|
| "New Hit Passes Old Classic" | just_passed | ok |
| "New Hit Passes Old Classic" | about_to_pass | `tense_mismatch: completed …` |
| "New Hit Is Closing In on Old Classic" | just_passed | `tense_mismatch: anticipatory …` |
| "…Becomes the Biggest Action Movie" | any | `lexical_superlative: 'Biggest'` |
| "It passes a Best Picture winner." | comp not Best Picture | `lexical_superlative: 'Best'` |
| "Past Old Classic's $145.4M" | comp $145,000,000 | `unverified_figure` (precision implies ±$50K) |
| "Ahead by $5 million" | active $150M vs comp $145M | ok (the gap) |
| "…chasing Star Wars territory" | Star Wars not bound | `third_film_named: 'Star Wars'` |
| claims includes `{"type": "rank"}` | any | `claim_unsupported:rank` |

## Tests

`tests/test_validation.py` (new, 61 tests) covers:

- **Claim matrix:** each allowed claim type, positive and negative. Never-supported types. A missing event claim. A contradicted claim, and one on an unreported metric. Relation claims without the category or without verified identity. Genre claims.
- **Lexical scan:** 13 superlative and rank phrasings, plus title masking. The Best Picture exception. Tense mismatches in both directions, plus milestones and the bare word "beat". Genre descriptors. Third-film titles. Lexicon filtering.
- **Figures:** precision parsing, the gap, a missing metric.
- **Length:** the 85 boundary and the `only_length` flag.
- **`shorten_headline`:** a successful call records usage. A refusal, malformed output, or timeout returns None.
- **End to end:**
  - a rank claim, a superlative, or an unverified figure never reaches Slack;
  - a long headline is shortened once and posted, and the posted text is the shortened headline;
  - three failure paths drop the pick without truncation (still too long, newly invalid, API error);
  - a pick that is too long *and* otherwise invalid gets no shortening call.

Also updated:

- `test_missing_values.py`: the interim figure-guard tests were ported to `validate.figure_checks`, plus a new milestone-figure test.
- `test_selection.py`: prompt hash.
- `test_harness.py`, `test_run_daily_corpus.py`: fake responses now carry claims.
- `offline.py`: the offline responder emits an event claim. Its angle wording no longer says "first", which the new scan correctly flagged.

Full suite: **422 passed** (361 at Increment 7, plus 61).

## Sample dry run

`sample_dry_run/` is the offline fixture day, run with no credentials and a cleared environment:

```
python scripts/run_daily.py --dry-run --fixtures fixtures/offline/2026-10-22 --out-root docs/v2/increment8_review/sample_dry_run
```

Results:

- `mode: dry_run`;
- `write_outcomes: {"recorded": 11}`, meaning 11 writes were recorded and none executed;
- 1 pick returned, 1 accepted (`report.json → validation`, reasons `[]`);
- usage shows only `select_picks#1`, because no shortening was needed.

## Story-selection impact

- **More picks will be dropped.** The spec anticipated this.
  - The old prompt actively asked for rank and "only" headlines; those requests are now removed from the prompt and blocked in code.
  - Any headline over 85 characters costs one extra small call. The old prompt allowed up to 90.
- **Not measured against history.** There are no historical headlines locally, and I did not read the production suggestions tab. The spec's shadow comparison (E4) is where every drop and its reason will appear before activation. A read-only replay of past headlines through `validate_pick` would be a cheap pre-check if you want it.

## Deviations from the spec and false-positive risks

1. **Genre claims accept either film's genres.** The spec says the subject film only. A claim like "passes the fantasy classic" describes the comp and is legitimate. The cost: the lexical scan cannot tell which film a genre word attaches to, so "Horror Film X Passes Y" passes when only Y is horror. Restricting to the active film is a one-line change if you prefer the spec's stricter rule.
2. **Bare "top" is not flagged.** Only `top-N` and `top-grossing` are, because "tops" is a completion verb and "top" appears in ordinary phrasing. "Top" in a ranking sense ("the top film") gets through the lexical scan. If it is declared, the claim check still rejects it.
3. **Strict words can cause false positives:**
   - "first" ("first weekend", "first film in the franchise");
   - "only" ("only two weeks in");
   - "ever" ("whatever" is safe, but "ever since" is caught);
   - "most" ("most of its gross");
   - "best" ("best-reviewed").

   Each of these drops a pick that is actually fine. Title text is masked, so "The Only Living Boy in New York" as a bound comp is safe.
4. **No alias table for third-film detection.** Matching uses exact corpus/payload titles. A short or one-word title under 7 letters ("Up", "Jaws", "Smile") is never checked, and a nickname ("the MCU finale") is not caught.
5. **Active-film freshness for `crossed_milestone`** is not re-checked here. It relies on today's source data. The spec's "fresh (not stale)" condition for the active metric will be enforced when source freshness lands (later increment).
6. **Tense uses word lists, not a parser.** Mixed sentences that contain both a completed verb and an anticipation marker pass, by design, in both directions.

## Remaining risks

- The shortening model could change meaning while staying valid, for example by dropping a qualifier. It is re-validated against the same rules, but the editorial meaning is not checked.
- PR-11 (a live structured-output check) now also covers the `Headline` schema, which has not been exercised against the real API.

## Next: Increment 9

Active-film identity resolution. Resolve active films to TMDB with IMDb-ID-first matching, so that `identity_unresolved` blocks franchise, director and cast angles and naming the film. Today's "Could not resolve TMDB for 'Alpha Film'" warnings in the offline run show the current title-only fallback.
