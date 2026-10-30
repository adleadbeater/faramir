# Phase 1b completion report: Takeover and Hold

Branch `v2/phase-1b`, from `v2/phase-1`. Local only.

## Delivered

| Module | Behavior |
|---|---|
| `strategies/weekend.py` | `Weekend` and `WeekendRow` views are built **only** from committed, content-verified weekend history. A missing, uncommitted, conflicting or damaged weekend is `None`, never assumed. An "opener" is a film TN marks `(new)`, or one in its first 3 days that was absent last weekend. |
| `strategies/takeover.py` | Fires when F is No. 1 on W, a different film was No. 1 on W−1, and both charts are committed. Subtype `debut` or `climb`. **Tie rule:** within 2% of No. 2 on a provisional chart, the event is `provisional` (deferred); it becomes `confirmed` once the same figures are re-collected on or after the Tuesday. Rejects: `same_leader`, `chart_unavailable`, `no_ranked_leader`. |
| `strategies/hold.py` | Fires when F holds rank ≤ 1 on both W−1 and W against a qualifying opener (≥ 2,500 theaters, or ≥ 50% of F's W−1 gross) that ranks below F. The same tie rule applies. Rejects: `no_challenger` (a Streak), `limited_opener`, `challenger_outranks`, `not_held` (a Takeover or climb), `chart_unavailable`. |
| `strategies/weekend_runner.py` | Reads W and W−1 from history, runs the enabled weekend detectors, classifies and writes to the ledger. With collection off it returns `history_collection_disabled`. Missing weekends return `history_unavailable`. |
| `run_daily.py` | Every run reports `weekend_strategies` for the latest completed weekend. In production today this is `history_collection_disabled`. |

**Hand-checked set** (4 weekends; `tests/test_strategies_p1b.py::EXPECTED`):

| Weekend | Expected and produced |
|---|---|
| 2026-10-02 | Hold: Alpha (No. 1) against opener Charlie (3,600 theaters, No. 2) |
| 2026-10-09 | Takeover (debut): Delta replaces Alpha |
| 2026-10-16 | Takeover (climb): Alpha back to No. 1 from No. 2 (Echo is a limited opener) |
| 2026-10-23 | Hold, **provisional**: Alpha $10.1M vs opener Foxtrot $10.0M (1% margin, Sunday collection) |

## Tests

**819 passed, 2 skipped.** `test_strategies_p1b.py` has 13 tests:

- the four weekends;
- Takeover evidence;
- `same_leader` and `chart_unavailable`;
- a near tie that is provisional, then confirmed by recollection;
- the Hold negatives (`not_held`, `no_challenger`, `limited_opener`, an opener that out-ranks the leader);
- damaged and missing history reads as unavailable;
- collection disabled;
- the daily-run report;
- ledger rows.

## Status and limits

- **Validated only on synthetic history.** Not operational with live data until weekend collection is approved (PR-16).
- **Not yet delivered to Slack.** Weekend events go to the ledger and run report. Delivery through ranking and headline generation arrives with the generic event path in Phases 2 and 4. Rank claims ("No. 1") need the evidence-backed rank rule (SR-1), which Phase 2 adds.
