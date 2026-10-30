# Faramir 2.0: Architecture Assessment and Phased Plan

**Repository:** `adleadbeater/faramir`, local checkout `~/Downloads/faramir`.

**Prepared:** 2026-10-22. Revision 2, which replaces the earlier scratch draft and incorporates the verification work described in §0.

**Companion documents:**
- `FARAMIR_PHASE0_SPEC.md`: the detailed Phase 0 specification.
- `corpus_identity_audit_2026-10-22.csv`: all 322 misidentified corpus rows.

**Status:** planning only. No code, corpus, Sheets, Slack, workflow, or GitHub changes have been made. These documents are untracked files in the local checkout and are not committed.

---

## 0. Repository state and how the findings were verified

### 0.1 Version

| Item | Value |
|---|---|
| Snapshot named in the brief | `5fd3157` (corpus refresh 2026-10-01) |
| Current `HEAD` and `origin/main` (after `git fetch`) | `f2a0ce4`. Its only change is adding `SUGGESTED_IMPROVEMENTS.md`. |
| Code under review | Identical to `5fd3157`, so none of the SUGGESTED_IMPROVEMENTS items have been fixed |
| Existing tests | 31 tests: 30 pass, 1 fails (`test_unexpected_giant_killer_requires_no_overlap`). The test predates commit `828b2fe`, which deliberately changed the rule, so the test is stale. |
| Runtime | CI uses Python 3.11. The local Mac has 3.9.6, and `scripts/run_daily.py:41` (`dict \| None` without `from __future__ import annotations`) cannot be imported on 3.9. |
| Anthropic SDK | `requirements.txt` says `anthropic>=0.40.0`. On Python 3.11 that resolves to **2.1.0**, the current PyPI release. Production therefore installs whatever the latest major version is on each run. I could not see which version the last Actions run actually installed. |

### 0.2 Verification performed (all read-only)

| Check | Method |
|---|---|
| Code paths | Read every module, script, test and workflow |
| Corpus identities | Called TMDB `/find/{imdb_id}?external_source=imdb_id` for all 2,011 corpus rows, using the existing TMDB key from `~/.claude/.env.json`. All 2,011 resolved. |
| Corpus grosses | Downloaded the 10 BOM worldwide and 5 BOM domestic lifetime-chart pages once (15 requests), plus 1 BOM title page for `tt0110478` |
| Chart layout | Downloaded 3 The Numbers (TN) chart pages, plus 2 extra requests to confirm the weekend URL format |
| Source terms | Read the `robots.txt` files for BOM and TN |
| Tests | Ran pytest in a throwaway virtual environment in the session scratchpad, not in the repo |

**Disclosure:** BOM's `robots.txt` says automated scraping is prohibited without IMDb's written permission (see §7). My verification requests came to about 20 BOM page requests, including retries. Faramir already makes requests like these in production. I am flagging this rather than treating it as settled.

### 0.3 SUGGESTED_IMPROVEMENTS, item by item

| # | Claim | Status at HEAD | Notes |
|---|---|---|---|
| 1 | Corpus has duplicate tmdb_ids (180 ids, 388 rows) | **Symptom confirmed. Diagnosis and fix are wrong.** | See §0.4 |
| 2 | `write_state` calls clear() and then update() | Confirmed | The real risk is worse; see §0.5 item 1 |
| 3 | `append_row` is called once per pick | Confirmed | `sheet.py:108` |
| 4 | Feedback rewrites the whole suggestions tab | Confirmed | `run_daily.py:119`, `sheet.py:113` |
| 5 | Claude's output is parsed by walking braces | Confirmed | `claude_select.py:270` |
| 6 | Anthropic client has no timeout or retry config | Confirmed | SDK defaults apply: 10-minute timeout, 2 retries |
| 7 | Scrapers fail quietly | Confirmed | A day-2 fallback exists, but there are no row-count canaries |
| 8 | Corpus refresh commits unchecked to main | Confirmed | |
| 9 | Test coverage is thin | Confirmed | Plus one failing test |
| 10 | `sys.path` hack, loose pin, Python 3.9/3.11 split | Confirmed | The unpinned SDK is now a major-version risk (2.x) |
| 11 | Cron drifts with DST | Confirmed | Already noted in a code comment |
| 12 | No dry-run mode or kill switch | Confirmed | |
| 13 | No per-run summary | Confirmed | |
| 14–20 | Editorial ideas | Not started | Feedback is collected but never read |

### 0.4 Corpus: the verified root cause

SUGGESTED_IMPROVEMENTS #1 says BOM chart rows "parse to the wrong IMDb ID" and recommends merging duplicates and keeping the maximum gross. **Both statements are wrong.**

- **BOM IDs are correct.** All 2,000 worldwide-chart links are `/title/tt…/` links. For every audited row, BOM's chart title and year match the TMDB identity resolved from that IMDb ID. 321 of 322 rows agree on year within ±1. The 11 title differences are alternate spellings such as "Seven" vs. "Se7en". One row, *I Am Sam*, has no current BOM chart title or year to compare against.
- **The defect is in `scripts/build_corpus.py: enrich_with_tmdb()`.** The comment there says "Try to find by imdb_id", but the code calls `resolve_tmdb_id(title)`. That function (`tmdb.py:18-34`) does a TMDB title search **with no year** and returns **the most popular** result. It then overwrites the row's title, release date, genres, director, cast, collection, origin country and distributor with that wrong film's metadata. It keeps BOM's IMDb ID and grosses.
- **Scale:** **322 of 2,011 rows (16.0%)** have the wrong identity.
  - 214 of them are in duplicate groups.
  - 108 are not, so a duplicate check cannot see them.
  - As a result, 322 real films exist in the corpus only under another film's name, and 110 film names in the corpus point to grosses that belong to a different film.
- **Failure modes:**

| Class | Count | Example (IMDb ID → labelled as → actually is) |
|---|---|---|
| The wrong label contains the true title (a more popular film whose name includes the searched title) | 178 | tt0369610 → "Jurassic World Rebirth (2025)" → *Jurassic World* (2015), $1.67B WW; tt0110478 → "Top Gun: Maverick" → *Maverick* (1994); tt2543472 → "Wonder Woman" → *Wonder* (2017) |
| Same title, different film (remake) | 45 | tt6105098 → "The Lion King (1994)" → *The Lion King* (2019), $1.66B WW |
| Other title collisions | 99 | tt4154756 → "Avengers: Endgame" → *Avengers: Infinity War*; tt0114369 → "Scream 7" → *Se7en* |

These classes are assigned mechanically by comparing titles (column `class` in the CSV).

- **Why the max-merge fix is unsafe:**
  - Max-merge keeps the largest row in each group, but often that row is itself mislabelled. For example, "Jurassic World Rebirth" would keep *Jurassic World*'s $1.67B.
  - It deletes real films such as *Infinity War*, *Top Gun* (1986) and *Spider-Man* (2002).
  - It does nothing for the 108 mislabels that are not in duplicate groups.
- **"The Dark Knight" duplicate:** The second row, tt0113071, is *First Knight* (1995) and was confirmed on BOM. It is not a Dark Knight reissue.
- **The grosses themselves are BOM's values for the correct IMDb ID.** The fix is to repair identities, not grosses.
- **Domestic gap:** 1,011 rows have `domestic_lifetime = 0` because the builder ignores the Domestic column on BOM's worldwide chart. 931 of them could be filled from that column.
- **Active films have the same bug:** `run_daily.py:211` resolves new chart titles by title alone. The `tmdb_id_override` column that the README documents is **never read** by any code.

### 0.5 Further defects not listed in SUGGESTED_IMPROVEMENTS

1. **A Sheets read error looks like a cold start.** `read_state` swallows exceptions and returns `[]`. That sets `cold_start=True`, so the run overwrites the state tab with only the films seen today and posts "Faramir initialized" to Slack.
2. **The weekend fetch has never worked.**
   - `fetch_weekend_list` builds its URL from the Sunday date, for example `/weekend/2026/10/18`. TN uses the Friday date: `/weekend/2026/10/16` returns 200, while `/10/18` and `/10/11` return 404.
   - `raise_for_status()` raises on the 404, the code catches it, and the function falls back to Sunday's daily chart.
   - The code that would have parsed the weekend table never runs, even though it also ignores its result.
   - No rank, weekend gross, percentage change or theater count is stored anywhere.
3. **The TN layout limits what the parser can capture.**
   - TN's daily and weekend tables have **no distributor column**, so `distributor` from TN is always empty.
   - There is **no theater-change column**; it has to be derived from consecutive weekends.
   - Only the top ranks carry a rank number (1–15 on the weekend chart, 1–8 on the sampled daily chart). Lower rows show `-`.
   - The page has **no marker separating estimates from actuals**, so revisions can only be detected by collecting the same weekend again.
4. **Claude's picks are not checked against real candidates.** `_validate_picks` only checks `active_tmdb_id`, so an invented comparison film, axis or kind passes through.
5. **Picks are posted before they are logged** (`run_daily.py:495` before `:521`). A crash in between causes the picks to repeat the next day, and a manual re-run on the same day reposts everything.
6. **Sources are mixed.** Domestic figures come from TN and worldwide figures from BOM, and active films get no `WW ≥ Dom` check.
7. **Milestone suppression is too broad.** The key `(film, 0, axis)` suppresses every milestone on that axis for 5 days, whatever the threshold.
8. **Title length rules conflict.** The prompt says both 75 characters (`claude_select.py:22`) and 90 (`:100`), and nothing enforces either. The target is 85.
9. **Cerebro (`cerebro.py`) has several problems:**
   - Tags are matched by substring.
   - Nothing filters by team, page type or publish status.
   - It sums cumulative sessions for a 180-day publication cohort.
   - The column names (`ActSess`, tab "MovieWeb Article Analysis") don't match the README, which says "DB tab".
   - `get_all_records()` reads every column. The MW export contains writer and pay columns, which aragorn's Cerebro module deliberately excludes with an allow-list.
10. **Monday has `PICKS_MIN_BY_DAY = 6`,** which conflicts with the principle that zero recommendations is a valid outcome.

---

## 1. Architecture assessment

| Component | Verdict | Reason |
|---|---|---|
| `run_daily.py` pipeline | **Retain, then decompose** | The order of steps is sound. Move the logic into testable stage modules. |
| `tmdb.py` | **Repair** | Add `find_by_imdb` and year-constrained search |
| `bom_fetch.py` | **Repair and split** | One header-mapped TN chart parser for daily and weekend charts, with the correct weekend URL. Keep the BOM worldwide scraper separate and subject to the licensing decision (§7). |
| `build_corpus.py` | **Repair** | Resolve identity by IMDb ID, add integrity gates, and refuse to write a corpus that fails them |
| `corpus.py` | **Extend** | Validate on load and key by `imdb_id` |
| `matcher.py` | **Retain** as the Chase/Overtake comparison engine | Its angle categories become evidence features rather than strategies |
| `milestone.py` | **Retain** as the Countdown/Overtake threshold engine | |
| `cerebro.py` | **Replace** in Phase 2 | Use a sanitized, allow-listed snapshot that follows the canonical contract, as aragorn and arwen already do |
| `claude_select.py` | **Repair, then narrow** | Structured outputs now. Later, Claude only presents events that code has already ranked. |
| `sheet.py` | **Repair** | Non-destructive, idempotent writes. Sheets remains the surface editors see, not the analytical ledger. |
| `slack_post.py` / `feedback.py` | **Extend** | Add rationale lines, message metadata, and status reason codes |
| GitHub Actions | **Retain** | Add a pytest gate, a kill switch, a dry-run input, artifacts and canaries |

**Target data flow:**

```
sources (TN daily/weekend, BOM WW*, TMDB) → source snapshots (+freshness, completeness, revision)
 → identity resolution (imdb_id canonical, confidence) → chart history
 → strategy detectors (deterministic) ┐
 → discovery generators (deterministic) ┘→ events (stable IDs, evidence, validation status)
 → classifier (primary/secondary, overlap rules) → event-level dedup
 → ranker (transparent components + exploration policy) → decision records
 → headline generator (Claude, structured) → [Aragorn adapter] → fact + 85-char validator
 → Slack (zero allowed) → editorial statuses → published URL → outcomes (Cerebro, age-normalized)
 → learning (shrinkage estimates) → versioned priority config (human-reviewed)
 * BOM use subject to licensing decision (§7)
```

**Storage decision (needed by Phase 1).**

- **Recommended:** append-only JSONL ledger files, partitioned by month, committed by Actions to a dedicated `ledger` branch.
  - It never touches `main`.
  - The history is diffable and auditable.
  - Sheets keeps the tabs editors use, as views.
- **Alternatives:**
  - Sheets-only append tabs: simplest, but they reach the cell limit in about 2–3 years and are clumsy to analyse.
  - BigQuery: I have no verified access to it.
- **Phase 0 itself only needs the `weekend_history` tab** (see the Phase 0 spec, §4).

---

## 2. File-by-file plan (all phases)

**Modified files**

| File | Change |
|---|---|
| `src/faramir/bom_fetch.py` | Split into `sources/tn_charts.py` (TN daily and weekend parser) and `sources/bom_worldwide.py` |
| `src/faramir/tmdb.py` | Add `find_by_imdb`, `search_movie(title, year)`, retry with backoff, and a confidence result |
| `src/faramir/corpus.py` | Add `validate_corpus()` and lookup by `imdb_id` |
| `src/faramir/sheet.py` | Non-destructive writes, `append_rows`, targeted range updates, and the `runs`, `state_backup` and `weekend_history` tabs |
| `src/faramir/claude_select.py` | Structured output, timeouts and retries, a cost ceiling, and candidate binding |
| `src/faramir/config.py` | Thresholds move to `config/*.yaml` from Phase 1 onward |
| `src/faramir/cerebro.py` | Replaced in Phase 2 |
| `src/faramir/slack_post.py`, `feedback.py` | Phase 0 adds message metadata. Phases 1 and 3 add rationale and statuses. |
| `scripts/run_daily.py` | Becomes a thin entry point with `--dry-run`, `--no-claude` and `--fixtures` |
| `scripts/build_corpus.py` | Identity fix, integrity gates, and an in-place repair mode |
| `.github/workflows/*.yml` | pytest gate, kill switch, dry-run input, artifact upload, no commit on a failed gate |

**New modules, by phase**

| Module | Phase | Responsibility |
|---|---|---|
| `faramir/runtime.py` | 0 | Run context (run_id, dry_run, enabled) and a write guard that every external write must go through |
| `faramir/diagnostics.py` | 0 | Per-run summary |
| `faramir/validate.py` | 0 | Bind each pick to a candidate, check its figures against evidence, enforce 85 characters |
| `faramir/history.py` | 0 | Idempotent, append-only weekend history with revision tracking |
| `faramir/identity.py` | 0 | Identity resolution for active films with a confidence score |
| `faramir/events/schema.py` | 1 | Event data structure with stable `event_id` and fingerprint |
| `faramir/strategies/*.py` | 1, 5 | One pure detector per strategy |
| `faramir/classify.py`, `dedup.py`, `ledger.py` | 1 | Classification, overlap resolution, dedup and the append-only ledger |
| `faramir/analytics/cerebro_snapshot.py`, `power_words.py` | 2 | Canonical article data and Power Words |
| `faramir/headlines/*` | 2 | Headline construction taxonomy, tagger and evidence |
| `faramir/discovery/*` | 3 | Anomaly generators, claim verification and novelty |
| `faramir/editorial.py` | 3 | Status machine, reason codes, URL linking |
| `faramir/learning/estimates.py`, `rank.py` | 4 | Shrinkage estimates, the ranker and exploration |
| `faramir/reports/*` | 3, 4 | Strategy Performance report, Emerging Strategies report, distribution explainer |
| `faramir/adapters/aragorn.py` | 4 | `TitleRefiner` interface: `NullRefiner` first, a real adapter later |

---

## 3. Strategy definitions

**Notation:**
- **[CAL]** marks a starting threshold that needs calibration.
- TN = The Numbers. BOM = Box Office Mojo.
- A "weekend" is TN's weekend chart, keyed by its Friday date.
- Weekend data counts as *provisional* until it has been re-collected (see the Phase 0 spec, §4).
- Rank conditions apply only to rows TN actually ranks (currently the top 15 on the weekend chart).

### Data requirements and current availability

| # | Strategy | Data required | Available? |
|---|---|---|---|
| 1 | Takeover | Weekend rank for 2 consecutive weekends | Yes, once Phase 0 history accrues |
| 2 | Hold | Weekend rank for 2 weekends, plus the opener's theaters and gross | Same as above |
| 3 | Streak | N consecutive weekends with no gaps | Accrues over time. A historical backfill depends on licensing. |
| 4 | Chase | Current cumulative gross, corpus comparison gross, 7-day pace | **Yes, once the corpus is repaired** |
| 5 | Overtake | Same-source cumulative before and after the crossing | **Yes, once the corpus is repaired** |
| 6 | Exit | Weekend theater counts, compared week over week | Derived from history (TN has no theater-change column) |
| 7 | Legs | Weekend % change, opening weekend, cohort history | Needs a historical cohort, which needs backfill or a licence |
| 8 | Countdown | Verified, fresh current total, threshold set, pace | **Yes** |
| 9 | Premium Run | IMAX/premium-format screen and extension data | **No source. Blocked.** |
| 10 | Studio Battle | Distributor year-to-date totals | **TN charts have no distributor column.** Needs another source or a mapping. |
| 11 | Global Split | Domestic/international split, cohort ratios, rollout status | Partial (BOM, subject to licensing) |
| 12 | Star Record | Filmography and grosses, billing order | Career-best is provable with the corpus-floor rule. Career totals are not available. |
| 13 | Dark Horse | Opening theaters, franchise flag, verified budget | Partial. Budgets are unreliable. No marketing spend data. |

### Trigger rules and negative tests

**1. Takeover**
- **Fires when:** F ranks 1 on weekend W, a different film G ranked 1 on W-1, and both charts are complete.
- **Subtypes:** `debut` and `climb`.
- **Tie rule:** if F and the No. 2 film are within 2% [CAL] while the data is still provisional, the event is `provisional` and is not delivered.
- **Negative tests:** F was No. 1 on both weekends; a chart is incomplete; F is No. 1 only on a daily chart; a provisional near-tie.

**2. Hold**
- **Fires when:** F's rank is ≤ R (default 1) on both W-1 and W, **and** a challenger C opened on W.
- **Challenger qualifies if:** it opened in ≥ 2,500 theaters [CAL] or grossed ≥ 50% of F's W-1 gross [CAL], and it ranks below F.
- **Negative tests:** no qualifying opener (that is a Streak); the opener out-ranks F; a limited-release opener; F rose only because the previous No. 1 left the chart (that is a Takeover).

**3. Streak**
- **Fires when:** F stays within tier T for N complete, consecutive weekends. Starting values: No. 1 for N ≥ 3, top 3 for N ≥ 4, top 5 for N ≥ 6 [CAL].
- **Repeats:** only at noteworthy extensions.
- **Negative tests:**
  - a missing weekend gives status `unverifiable` (the streak is never assumed to continue);
  - a gap week breaks the streak;
  - N below the minimum.

**4. Chase**
- **Fires when:** F is within 10% [CAL] below a *meaningful* comparison film on the same axis, and the 7-day pace projects passing it within 21 days [CAL].
- **Meaningful means one of:** franchise sibling; same director or top-3 billed actor; top 5 in genre; an all-time rank threshold; Best Picture winner, classic, or a curated iconic list.
- **Negative tests:** already passed (that is an Overtake); an unrelated mid-tier comparison; pace too slow to get there (logged as `pace_insufficient`); the comparison row fails integrity.

**5. Overtake**
- **Fires when:** `prev_cum < comp_value ≤ cur_cum`, both figures from the same source series, and the crossing happened within 3 days [CAL].
- **Includes:** round-number crossings and list-rank crossings.
- **Must be confirmed, never projected.**
- **Negative tests:** the crossing appears only in provisional data; mixed sources disagree; the cumulative figure has been stale for 2 or more days.

**6. Exit**
- **Fires when:** the theater count drops ≥ 40% [CAL] from a base of at least 1,000 [CAL].
- **End-of-run** is inferred only when the film is absent from 2 consecutive complete charts and its total has stopped changing.
- **Negative tests:** a small base; a single gap in the source data; a PVOD announcement (not in the data).

**7. Legs**
- **Fires when:** the drop is better than the cohort's 10th percentile [CAL], or the cumulative-to-opening multiplier exceeds the cohort's 90th percentile for that release week.
- **Cohort:** genre × opening-theater band × week number.
- **Guards:** theater change must be ≤ +10% [CAL]; holiday weekends are adjusted.
- **Can fire while the film is falling in rank.**
- **Negative tests:** gains driven by an expansion; a holiday-inflated weekend; a cohort of fewer than 20 films.

**8. Countdown**
- **Fires when:** the gap G = M − T is ≤ min($10M, 5% of M) [CAL], T is verified and fresh, and the current pace closes the gap within 7 days [CAL].
- **M** is a round milestone or an approved record. If M is a film's lifetime gross, the event is a Chase instead.
- **Negative tests:** a stale total; an unchanged gap (suppressed until G shrinks by ≥ 50% [CAL]); a threshold already crossed.

**9. Premium Run**
- Not built until a verified source exists.
- Discovery may surface reported items marked `needs_verification`.

**10. Studio Battle**
- **Fires when:** a distributor's year-to-date rank changes within the top 3 [CAL], or its year-to-date total crosses a threshold [CAL].
- Requires a distributor source and a canonical name map.
- **Negative tests:** tail rank changes; artifacts from name variants; an incomplete year-to-date figure (status `partial`).

**11. Global Split**
- **Fires when:** the international share deviates more than 25% from the cohort median, with a cohort of at least 15 films [CAL] and at least 14 days since international release.
- An international share above 80% triggers a revision-risk flag.
- **Negative tests:** staggered international rollout; a split that is normal for the genre; a cohort that is too small.

**12. Star Record**
- **Fires when:** F becomes the career-best (or top-N) film for a director or a top-3 billed actor.
- **Provability rule:** if F's gross is above the corpus floor (about $97.8M worldwide or $89.1M domestic today), then every film that grossed more is in the corpus, provided the corpus passes integrity checks.
- Career totals are excluded.
- **Negative tests:** a cameo below third billing; F is below the floor (`unverifiable`); the previous best was an ensemble film (labelled "ensemble").

**13. Dark Horse**
- **Fires when:** at a comparable release stage (±1 week), F outgrosses a film G that has a verified scale advantage: at least 1.5× the theaters [CAL], franchise vs. original, or a verified budget at least 3× larger [CAL]. F must rank in the top 5.
- **Never claims** "heavily promoted".
- **Negative tests:** the films are at different release stages; budgets are unknown and theater counts are similar; F is outside the top 5.

### Overlap resolution

Events are clustered by `(film, reporting_period, axis, target)`. Each cluster produces at most one recommendation, with one primary strategy and at most one secondary.

1. **Completed beats anticipated:** Overtake beats Chase and Countdown on the same target. An unchanged anticipated event is never re-sent.
2. **Chase vs. Countdown:** a comparison film is a Chase and a threshold is a Countdown. If targets are within 2% of each other on the same day, rank decides primary and secondary.
3. **Hold, Streak, Legs:** Hold if there was a challenger; otherwise Streak if the tier run qualifies. Legs is secondary when both rank and retention qualify, and primary when no rank condition is met.
4. **Takeover beats Dark Horse;** Dark Horse becomes secondary if the imbalance evidence is met.
5. **Star Record beats Overtake** when the film surpassed is the talent's own earlier film and the result is a career-best.
6. Every classification stores `rejected_alternatives[]` with reason codes.

Each rule gets a positive and a negative test using synthetic history, including all of the overlap examples in the brief.

---

## 4. Discovery design

**Generators.** Deterministic generators run over the same history:
- comeback (re-entry to the top 10 after an absence);
- re-release share;
- outliers in per-theater average;
- z-score outliers within a cohort;
- expansion surges;
- format or type contrasts (concert films, anime, event cinema vs. wide studio releases);
- territory breakouts (once territory data exists).

An anomaly that a strategy event already explains (same film, period and metric) is excluded.

**Open pass.**
- Claude receives the week's chart and history summary.
- Every proposed pattern must come back as structured claims: `{film_id, metric, value, period, source_row}`.
- Each claim is checked against stored data. Unverifiable claims are dropped and logged, never shown.

**Each Discovery item records:**
- evidence and sources;
- why it matters;
- a headline and an alternative;
- `pattern_tags` (proposed by Claude, confirmed by an editor);
- `similar_prior` links (overlap of tags and metrics with past items).

Discovery items are tracked in their own ledger stream.

**Emerging Strategies Report (monthly).** A cluster of accepted items sharing a confirmed tag becomes a *candidate* strategy when all of these hold:
- at least 5 accepted items [CAL], across at least 3 films;
- at least 3 published;
- 14-day sessions per article at or above the box-office cohort median;
- a deterministic rule has been drafted.

**Promotion is never automatic.** `config/strategies.yaml` is versioned, and changes go through a PR you approve.

---

## 5. Learning design

- **Outcome measure:** sessions at a fixed article age (14 days), plus failure rate (< 500), heavy-hitter rate (> 25k), Discover visibility (> 1,000 impressions) and Discover clicks per article. Search and Discover stay separate.
- **Age normalization:** either Cerebro provides age-bucketed sessions (unverified), or Faramir snapshots each linked article's cumulative sessions at every Cerebro refresh and keeps the value closest to day 14, recording its actual age.
- **Film cohort features:**
  - genre bucket;
  - franchise flag;
  - talent/franchise affinity tier (Cerebro);
  - opening theater band;
  - verified budget band (otherwise `unknown`);
  - domestic/international weighting;
  - release-week bucket.
- **Priors:** classify MovieWeb's historical box-office articles into strategies and headline constructions. Claude does the tagging, an editor audits a 10% sample, and the results are labelled "historical, not Faramir-originated".
- **Estimation:** empirical-Bayes shrinkage, with each strategy × cohort estimate pulled toward the strategy estimate, which is pulled toward the global estimate, using weight n/(n+k) with k=10 [CAL].
  - Every estimate is reported with n and an interval.
  - When n_eff < 8 [CAL], the result is labelled `experimental` and the broader level is used instead.
- **Strategy vs. headline:** they are separate factors in the outcome model, so a weak construction doesn't penalize a strategy.
- **Feedback routing:**
  - "Already covered" or "timing" goes to novelty and timing.
  - "Questionable data" goes to source confidence.
  - "Weak comparison" and "audience appeal" go to the strategy × cohort estimate.

---

## 6. Ranking and decision traceability

**Initial score (for your approval):**

```
score = 0.35·event_strength + 0.20·audience_affinity + 0.15·strategy_perf_adj
      + 0.10·headline_evidence + 0.10·novelty + 0.10·confidence − penalties
```

**Policy settings:**
- Performance adjustments are capped at ×0.7–×1.3 in year one.
- An event with event_strength ≥ 0.9 [CAL] stays eligible regardless of adjustments.
- Every strategy gets at least 8 deliveries [CAL] before it can be adjusted down.
- Exploration takes 1 slot a week or 15% of slots [CAL], whichever applies, and those picks are marked `exploration=true`.
- Re-evaluation is monthly. Each run produces a versioned `priority_config` diff with reasons, which editors can override or roll back.
- There is no slot quota, and Monday's minimum of 6 is removed.

**Decision record (one per candidate per run, append-only):**

```
run_id, event_id, fingerprint, film{imdb_id,tmdb_id,title}, strategy_primary, strategy_secondary,
rejected_alternatives[{strategy, reason_code}],
trigger{metric, values[], period, source, retrieved_at, validation_status},
audience{signals[], tier, evidence_ref}, performance{scope, n, estimate, interval, fallback_level, is_experimental},
score_components{...}, priority_config_version, rank, competing_above[event_ids],
exploration{flag, reason}, disposition{suggested|filtered|deferred|rejected, reason_codes[]},
headline{recommended, alternative, construction, rationale_codes[], evidence_refs[], aragorn{used, before, after}},
validation{figures_checked[], length, passed}
```

**Distribution explainer** (for example, "why more Dark Horse than Countdown?").

- A deterministic funnel per strategy, with the drop reasons at each stage:

  ```
  detected → validated → qualified → deduped → ranked → delivered → accepted → published → outcome
  ```

- It also reports the config versions, exploration allocations and evidence n that were in effect.
- Explanations are generated only from these tables. A drop in supply is never reported as underperformance.

**Strategy Performance & Prioritization Report (monthly):**
- supply;
- deliveries;
- acceptance and rejection, by reason;
- 14-day outcomes;
- cohort patterns with n;
- priority changes with reasons;
- exploration share.

---

## 7. Data dependencies and source feasibility

See the Phase 0 spec, §5, for the full source table. In summary:

| Source | Used for | Status |
|---|---|---|
| **TMDB API** | Identity, metadata | Licensed API with a working key. **Safe to use now.** |
| **The Numbers** charts (HTML) | Daily and weekend domestic data | Already scraped in production. `robots.txt` restricts only some named bots. **I could not find TN's terms of use** (`/terms-of-use` returns 404). Licensing has not been verified. TN sells data commercially through OpusData. |
| **Box Office Mojo** (HTML) | Corpus and daily worldwide per film | Already scraped in production. **`robots.txt` says scraping is prohibited without IMDb's written permission** and that the content is for personal, non-commercial use. This is a live compliance question about current production behavior, not only about v2. |
| Premium formats, territory rollout, distributor market share, budgets, marketing | Strategies 9, 10, 11 (partial), 13 | No verified source |
| Cerebro canonical contract | Learning, audience | **Needed:** the column specification, team field, Regular/Publish filters, and a Discover-impressions column. The sanitized export used elsewhere has no impressions column. |
| Power Words | Headlines | A sanitized snapshot format exists in aragorn and arwen. **Needed:** permission to reuse it and the name of the refresh owner. |

**Decision needed.** Do you want legal or business review of the TN and BOM terms? The answer affects Phase 0 (whether the corpus is rebuilt from BOM; the spec offers a path that avoids BOM) and Phases 1b and 5 (historical backfill).

---

## 8. Aragorn integration assessment

**What I found.** These findings come from the Drive copy and suite notes; GitHub `claude-space/aragorn` is the canonical source.

- Aragorn is a Node Slack bot: a Socket Mode process on the ShellAgent VM, plus a 5-minute GitHub Actions poller.
- It is triggered by `reframe: <title> | <context>` messages or `/reframe`.
- Its engine is `src/services/claude-service.js`, which uses a sanitized Cerebro snapshot and Power Words.
- **It has no documented callable API.**
- The suite title rules allow **95 characters**, while the target here is 85.

| Option | Pros | Cons |
|---|---|---|
| A. Use Slack as the message bus | No changes to Aragorn | Asynchronous, noisy and fragile |
| B. Add an authenticated HTTP `/refine` endpoint | Clean contract | A separate project and approval; a dependency on the VM |
| C. Vendor Aragorn's prompt and rules at a pinned commit | Testable, no runtime coupling | Risk of divergence; the Aragorn owner must sanction syncing |
| **D. Null adapter (recommended for the pilot)** | Works today | No Aragorn benefit yet |

**Recommendation.** Define the interface now:

```
TitleRefiner.refine(headline, alt, evidence) -> {headline, alt, changes, refiner_version}
```

- In Phase 4, evaluate option B or C once the Aragorn owner confirms the contract and the length cap.
- Faramir revalidates facts and length on every output that Aragorn returns.

---

## 9. Phased roadmap

| Phase | Scope | Depends on | Example acceptance criteria |
|---|---|---|---|
| **0 Reliability** | `FARAMIR_PHASE0_SPEC.md` | None | The spec's §6 checklist |
| **1 Strategy MVP** | Event schema, ledger, Overtake, Chase, Countdown, classifier, dedup, Slack rationale line, light editorial status capture | 0 | Fixtures pass for every rule; a zero-pick day works; no duplicate cluster is delivered; every candidate gets a ledger row |
| 1b | Takeover, Hold | ≥ 3 complete stored weekends | Matches a hand-checked set of 4 weekends |
| **2 Headline intelligence** | Cerebro snapshot, Power Words, construction tagger, historical classification audit | Cerebro contract and Power Words access | Every figure validated; rationale codes present; ≥ 90% agreement with the audited sample |
| **3 Discovery and feedback** | Generators, claim verification, statuses, URL linking, Emerging Strategies Report | 1, 2 | No unverifiable claim is delivered; reports reproduce from the ledger |
| **4 Prioritization and Aragorn** | Estimates, ranker, exploration, explainer, refiner adapter | 3 plus about 3 months of outcomes | The explainer reproduces counts exactly; every priority change is versioned |
| **5 Remaining strategies** | Streak, Legs, Exit, Studio Battle, Global Split, Star Record, Dark Horse | Data per §7 | Fixtures pass for each strategy; strategies blocked on data stay off |

**Changes from the brief's phase sequence, and why:**
- **Weekend-history capture moves into Phase 0.** History only accumulates from the day collection starts, so every week without it pushes back Takeover, Hold, Streak and Legs.
- **Light editorial status capture moves into Phase 1.** Learning needs months of outcomes, so the clock should start as early as possible.

**Gates for every phase:**
1. A plan, followed by your approval.
2. Work on a branch named `v2/phase-N`.
3. Tests, then a report.
4. A stop at the phase boundary.

Merging, workflow changes, corpus commits, Sheets schema changes and Slack format changes each need separate approval.

**Rollback:** revert the merge and set `FARAMIR_ENABLED=false`. Tabs that v2 adds are new tabs, never in-place rewrites of existing ones.

**Main risks:**
- scraping terms;
- ambiguity in the Cerebro contract;
- small sample sizes for months;
- Slack interactivity needs hosting (GitHub Actions cannot receive Slack callbacks);
- Aragorn's 95 vs. 85 cap;
- the unpinned Anthropic SDK major version.

---

## 10. Phase 0

The full specification is in **`FARAMIR_PHASE0_SPEC.md`**.

**Decisions needed before Phase 0 starts:**
1. **Corpus repair path:**
   - in-place identity repair using TMDB only (recommended: no new BOM scraping); or
   - a full rebuild from BOM.
2. **Weekend-history storage:** the proposed Sheets tab, or JSONL.
3. **The stale test:** confirm that the giant-killer test should follow the code.
4. **Repository process:** push access, branch protection, and who reviews PRs on `adleadbeater/faramir`.
5. **Local development tooling:** OK to install Python 3.11 on your Mac for development, for example with Homebrew or `uv`.
6. **Source terms:** whether TN and BOM terms go to legal review, and whether Phase 0 should wait for it.
