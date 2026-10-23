# Phase 2 completion report: headline intelligence

Branch `v2/phase-2`, from `v2/phase-1b`. Local only. No restricted data was accessed and no row-level Cerebro data is committed.

## Delivered

| Module | Behavior |
|---|---|
| `analytics/cerebro_snapshot.py` | Loads a **sanitized** snapshot (aragorn/arwen format; UI and BigQuery header aliases). **Refuses any file with a sensitive column** (writer, editor, author, cost, pay, rate, fee, salary …). Applies the established contract: Regular pages, `Publish` only, brand MW, team pools **never mixed** (News = News, Flash News, Niche News; Features/Lists = Feature, Mini Feature, List), and articles matured ≥ 14 days. Every metric is labelled "cumulative sessions per article to the snapshot date (not calendar-period traffic)" and carries its age range. |
| `analytics/power_words.py` | Power Words snapshot. **MovieWeb figures only** (Screen Rant figures are never pooled). A phrase counts as proven at ≥ 3 MW uses. |
| `analytics/audience.py` | Audience affinity from the snapshot (News pool, 180-day window, median vs pool, tier). It replaces the live-sheet signals when `FARAMIR_CEREBRO_SNAPSHOT` is set; the legacy path is kept otherwise. An unknown subject is neutral, never invented. |
| `headlines/taxonomy.py` | Ten box-office constructions, multi-label, with a primary label by precedence. `FITS` maps strategies to the constructions that are truthful for them (a Chase can't "pass"). |
| `headlines/analysis.py`, `scripts/headline_analysis.py` | Historical MW News box-office headlines by construction: n, median, lift vs baseline, **median age**, failure and heavy-hitter rates, and an `experimental` flag below n = 8. A deterministic 10% audit sample for editors. Everything is labelled `historical_not_faramir`. |
| `headlines/recommend.py` | For each event: the fitting constructions, measured ones first, ranked by lift, plus proven, event-safe MW Power Words. Each comes with rationale codes that cite n and as-of. Without a snapshot the result says so (`no_evidence`). |
| `validate.py` | **SR-1:** rank language ("No. 1", "tops the weekend box office", "box office crown") and `chart_position` claims are accepted **only** with weekend-chart rank evidence, and must match it. Superlatives stay blocked. |
| Pipeline | `headline_guidance` per candidate in the Claude payload, plus a short prompt section (Phase 2 prompt hash pinned). Each pick's `evidence_json` records the constructions used, whether the guidance was followed, and its calibration. |

## Real-data demonstration (sanitized snapshot, run locally; output in git-ignored `out/`)

Arwen's sanitized `cerebro_MW.csv`, treated as of 2026-09-28: **157** matured MW News box-office articles, baseline median **2,383** cumulative sessions per article (ages 14–211 days).

| Construction | n | Median lift | Median age |
|---|---|---|---|
| chart_position | 12 | **+184%** | 97 d |
| passes_comparison | 14 | **+96%** | 112 d |
| anticipation | 13 | +16% | 48 d |
| record_claim | 55 | **−30%** | 84 d |
| opening_weekend | 9 | −26% | 124 d |
| contrast_upset, ongoing_run, lead_with_number, milestone_crossing | ≤ 6 | experimental | — |

**Reading this carefully:**

- Lifts are not age-matched. "Passes" articles are older than the baseline, so part of their lift may be age. Opening-weekend articles are older and still weaker.
- The samples are small. This is a historical prior, not Faramir's learned performance (Phase 4).
- **Overtake recommendation:** passes_comparison first (n = 14, measured), then the experimental ones. Power Words: "Officially" (MW 21,871 sessions per article, n = 514), "Box Office", "Surges".

## Tests

**846 passed, 2 skipped.** `test_headlines_p2.py` has 27 tests:

- the snapshot contract: sensitive refusal, alias mapping, filters, team separation, cumulative labelling, as-of basis;
- MW-only Power Words;
- tagger agreement with **36/40** hand-labelled headlines (≥ 90% required);
- analysis, the audit sample and recommendations;
- audience signals;
- evidence-backed rank claims (7 cases);
- pipeline: snapshot used, guidance passed and recorded; a sensitive snapshot refused while the run continues;
- the script writes only to its output folder.

## Status and limits

- **Implemented and validated on synthetic data, demonstrated on the real sanitized snapshot.**
- **Pending:**
  - the editor audit of the 10% sample (the plan's ≥ 90% agreement criterion);
  - a production snapshot location and refresh owner (no Google credential exists for agents; the refresh is manual);
  - Discover-impressions availability in the export.
- **Claude decides how to use the guidance.** Headline generation stays with Claude under the existing validator; guidance never overrides the evidence rules.
