# Phase 1 completion report: Strategy MVP

Branch `v2/phase-1`, from `920a9d2`. Local only.

## Delivered

| Increment | Module | Notes |
|---|---|---|
| 1.1 Event schema | `events/schema.py` | Stable `event_id` (strategy, film, period, axis, target); `fingerprint` (id plus evidence); `story_key` (the story regardless of day, used for cross-day dedup); `cluster_key`. `validate()` refuses an event with no source, evidence, identity or reason. |
| 1.1 Evidence ledger | `events/ledger.py` | Append-only JSONL, one file per stream per month, idempotent by `id`. A dry run reads the persistent ledger but writes only to its artifact directory. Streams: events, candidates, deliveries, observations, editorial. |
| 1.2 Overtake | `strategies/overtake.py` | Confirmed crossings only: prev < target ≤ cur. Rejects a first observation, a stale figure or an old crossing. A provisional source makes the event `provisional`. Records the all-time rank before and after. |
| 1.2 Chase | `strategies/chase.py` | Searches its own 10% window. "Meaningful" means franchise, director or cast, top-5 genre, a notable film, or all-time top 100. The pace comes from a 7-day observation series, else the 1-day change. Rejects: `pace_insufficient` (logged), `not_meaningful`, `already_passed`, `no_pace`, stale, suppressed. |
| 1.2 Countdown | `strategies/countdown.py` | Gap ≤ min($10M, 5%) and fresh. Needs pace within 7 days. An unchanged gap stays suppressed until it has halved. |
| 1.3 Classifier | `classify.py` | One primary and at most one secondary per cluster. Rule 1: completed beats anticipated. Rule 2: Chase and Countdown within 2% merge. Rule 6: `rejected_alternatives` carry reason codes. Provisional or unverifiable events are deferred. Later phases register more rules through `OVERLAP_RULES`. |
| 1.4 Dedup | `dedup.py` | A confirmed story delivered before is `already_delivered`. An anticipated story is `unchanged_anticipated` within 14 days unless its gap has halved. A story appears at most once per run. |
| 1.5 Rationale | `rationale.py`, `slack_post.py` | One "Why: …" line per pick, built only from the event's evidence and sources. |
| 1.6 Editorial status | `editorial.py` | Statuses are suggested, accepted, assigned, published, rejected and expired. Rejection reason codes (already_covered, timing, questionable_data, weak_comparison, audience_appeal) come from the thread text, and a published URL from the thread. Recorded in the ledger only when a status changes. |
| 1.7 Integration | `strategy_engine.py`, `run_daily.py` step 10b | Claude now chooses only among classified, deduplicated events, each carrying its strategy, event_id and rationale. Delivered events are recorded in `deliveries`. |
| Registry | `config/strategies.json`, `strategies/__init__.py` | All 13 strategies plus Discovery, each with `enabled` and `data_status`. Only Overtake, Chase and Countdown are `live`. |

## A defect found and fixed (Phase 0 state)

A same-day rerun rolled `domestic_yesterday`/`worldwide_yesterday` forward to the previous **run's** values. On a recovery rerun every crossing and the pace disappeared: stories were missed, not wrong ones. `run_daily` steps 4 and 5 now keep the previous **day's** figure when a row was already updated today. The fix is targeted and tested.

## Behavior changes

- **Candidates sent to Claude:** one per delivered cluster, instead of the raw matcher and milestone list.
  - Chase replaces the matcher's 5% "about to pass" set with the plan's rule: within 10%, meaningful, and paced.
  - Countdown replaces the milestone "about to pass" set: gap limits plus pace.
- **Slack picks** gain a "Why:" context line.
- **`evidence_json`** carries a `strategy_event` block (event_id, strategy, rationale, reason codes).

## Tests

**806 passed, 2 skipped** (opt-in). `tests/test_strategies_p1.py` has 49 tests:

- the schema and every validation refusal;
- the ledger, including dry-run isolation;
- 7 Overtake, 8 Chase, 7 Countdown, 4 classifier and 5 dedup cases;
- rationale, editorial and the registry;
- integration: every candidate and event in the ledger, no duplicate cluster sent to Claude, a zero-pick day, cross-run dedup with a persistent ledger, and the same-day rerun fix.

Existing tests adjusted: four clean-up helpers now remove nested artifact directories.

**Offline dry run:**
- 20 events (16 Overtake, 4 Chase) form 20 clusters;
- 126 Chase comparisons rejected as `not_meaningful`;
- 1 pick, carrying "Why: Overtake · worldwide $322.0M passed Superman (1978) at $300.5M; $300.0M the day before · sources: bom_title 2026-10-21; corpus_v2 2026-10-01";
- 14 writes recorded, 0 performed.

## Limits

- **Pace is a 1-day change** until the persistent ledger (`FARAMIR_LEDGER_DIR`) has a week of observations. Where the production ledger is stored is a deployment decision.
- **"All-time top 100"** is ranked against the corpus, which covers films above its floor (≈ $89M domestic).
- **Editorial statuses** come from Slack feedback only. Interactive buttons would need hosting (plan §9 risk).
