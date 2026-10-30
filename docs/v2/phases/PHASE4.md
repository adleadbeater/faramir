# Phase 4 completion report: performance learning, prioritization and Aragorn

Branch `v2/phase-4`, from `v2/phase-3`. Local only. **No outcome data exists for Faramir picks, so calibration is `pending` everywhere, and no recommendation claims to have learned anything.**

## Delivered

| Module | Behavior |
|---|---|
| `learning/outcomes.py` | Age-comparable outcomes. Each linked article's cumulative figures are recorded at every snapshot, with the article's age (`outcome_observations`). The 14-day outcome is the observation closest to day 14 **inside 14–21 days**, never extrapolated. Outcome flags: failure (< 500), heavy hitter (> 25k), Discover visible (> 1,000 impressions). |
| `learning/estimates.py` | Empirical-Bayes shrinkage, from strategy × cohort to strategy to global, with weight n/(n+k), k = 10. Every estimate carries n and an 80% interval. When n_eff < 8 it is `experimental` and the broader level is used. Strategy and headline construction are separate factors. With no outcomes the status is `calibration_pending`. |
| `learning/rank.py` | The score is Σ weight × component − penalties. Components: event strength, audience affinity (snapshot tier), strategy performance (capped ×0.7–×1.3, neutral while pending, never adjusted down before 8 deliveries), headline evidence (measured construction lift), novelty and confidence (identity × status). Penalties: provisional, legacy identity. An event with strength ≥ 0.9 is kept as `deferred` rather than dropped. **Exploration** takes 15% of slots (at least one a week); it prefers a strategy not already in the main slots, then the least-delivered, and marks the pick `exploration=true` with the reason. All choices are deterministic. |
| Decision records | One per scored event per run, append-only (`decisions`), in the plan §6 shape: trigger, audience, performance, score components, config version, rank, `competing_above`, exploration, disposition, headline. |
| `learning/priority.py`, `config/priority_config.json` | A versioned config (plan §6 weights). `propose()` produces a diff with reasons and **never applies itself**. |
| `reports/explainer.py` | The funnel per strategy (detected → … → outcome) with drop reasons, config versions and exploration allocation, **reproduced exactly from the ledger**. `why_more(a, b)` names the first diverging stage; a difference at "detected" is reported as supply, never as performance. |
| `reports/performance.py` | The Strategy Performance & Prioritization report: supply and funnel, acceptance by reason, 14-day outcomes by strategy and by construction, exploration share, priority proposals, calibration status. |
| `adapters/aragorn.py` | The `TitleRefiner` interface. `NullRefiner` is the default (Aragorn has no callable API). **Any refined headline is re-validated** for facts, meaning and 85 characters, and discarded if it fails. A failing refiner never blocks a pick. |
| Generic event delivery | Weekend events (Takeover, Hold) and verified Discovery items now flow through the same path: ranking, then Claude, then binding (`kind: "event"`, bound by film and strategy), then validation (an `event` claim; figures only from the event's stored values; rank wording only from chart rank evidence), then the Slack event block and the deliveries ledger. Event picks get stable suggestion IDs. |
| Pipeline (`run_daily` step 10c) | Links published articles (thread URL or Cerebro title match) and observes their outcomes in the snapshot, estimates, scores every event, ranks into picks_max + 2 slots, sends only those to Claude, and records decisions. The refiner runs after validation. |

## Example (offline fixture day)

20 events scored; calibration `calibration_pending`; 6 ranked into slots:

- 1: Overtake (0.625)
- 2 to 5: Chase (0.576 each)
- one exploration slot, chosen by the rule above

Tested end to end:
- **Weekend Takeover:** collected weekends give a Takeover event; it ranks first, Claude writes "Beta Story Takes No. 1 at the Weekend Box Office", validation accepts it, the Slack event block shows "No. 1 on the weekend chart" with "Why: Takeover", and it lands in the deliveries ledger.

## Tests

**890 passed, 2 skipped.** `test_learning_p4.py` has 26 tests:

- outcomes: window, no extrapolation, observation;
- estimates: pending, shrinkage, experimental fallback, separate factors;
- ranker: components, penalties, caps, the small-delivery guard, slots, exploration and determinism, deferred strong events;
- priority proposals;
- the explainer reproducing exactly, and the supply-vs-performance wording;
- the performance report, pending and then estimated;
- the refiner: null; good, invented-figure, reversed and over-length rewrites; a failing refiner;
- end-to-end weekend event delivery;
- event figure validation.

**Defects found and fixed:**
- an interval collapsed to zero width when every value was identical;
- event picks had no suggestion ID and were silently skipped;
- exploration repeated a strategy already in the main slots.

## Status and limits

- **Infrastructure complete; calibration pending.** It needs roughly three months of delivered, published and age-comparable outcomes, which require production and a refreshed Cerebro snapshot at least every few days.
- **Weights are the plan's initial values** and await approval. Proposals are never applied automatically.
- **No real Aragorn adapter:** option B (HTTP) or C (vendored) needs the Aragorn owner's contract and the 95-vs-85 character decision.
- **Discovery items reach Claude only when tied to a tracked film.** Chart-level items stay in reports and the ledger.
