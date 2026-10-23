# Phase 3 completion report: Discovery and editorial feedback

Branch `v2/phase-3`, from `v2/phase-2`. Local only.

## Delivered

| Module | Behavior |
|---|---|
| `discovery/items.py` | A Discovery item is a **proposed pattern**, labelled "needs editorial review; not an established fact". Every number in it is a structured claim `{film_key, metric, value, period, source_row}`, checked against committed weekend rows; the re-release share is recomputed from the rows. Derived percentages can't be claimed directly; their two underlying rows are claimed instead. **One unverifiable claim drops the item** (logged to `discovery_dropped`, never shown). |
| `discovery/generators.py` | Six generators: comeback (back in the top 10 after 2 weekends outside it), re-release share (≥ 10% of chart gross), per-theater outlier (≥ 3× the chart median, outside the top 3), weekend-change z-score (\|z\| ≥ 2 against the same release week, cohort ≥ 8), expansion surge (theaters +50% and gross up), format contrast (concert, documentary or anime in the top 5, from TMDB metadata). **Territory breakouts are not built:** there is no data source. |
| `discovery/open_pass.py` | The model open-pass interface plus claim verification. `NullOpenPass` is the default. A Claude proposer is **not wired in**, because running it would mean new paid API calls, which are not authorized. Tested with a fake proposer: a true claim is kept; an invented figure, a proposal with no claims and malformed claims are all dropped. |
| `discovery/novelty.py` | `similar_prior` links (pattern-tag Jaccard plus a same-metric bonus) and a novelty score. |
| `discovery/runner.py` | Reads up to 6 consecutive committed weekends; a gap restarts the series. Anomalies a strategy event already explains (same film and period, a related metric) are excluded. Results go to the `discovery` ledger. With collection off it reports `history_collection_disabled`. Wired into `run_daily`. |
| `editorial.py` | Adds assignee capture ("assigned to …") and **published-article matching**. A MovieWeb URL in the thread wins. Otherwise a News-pool article published within 7 days whose title names the film, scored on headline overlap and the comparison film. **Near-ties are reported as ambiguous and never linked.** |
| `reports/editorial_report.py` | Per strategy: delivered, accepted, assigned, published, rejected (with reason codes), expired, awaiting, and the acceptance rate. **Reproduced exactly from the ledger** (tested). |
| `reports/emerging.py` | The Emerging Strategies report, per editor-confirmed tag. A candidate needs: ≥ 5 accepted items across ≥ 3 films; ≥ 3 published; 14-day sessions per article ≥ the cohort median (`pending_outcomes` until Phase 4 outcomes exist); and a drafted rule. **Promotion is never automatic.** |
| Registry | `discovery` is enabled with `data_status: history_required`, and lists its generators and what was not built. |

## Tests

**864 passed, 2 skipped.** `test_discovery_p3.py` has 18 tests:

- every generator, positive and negative;
- claim verification (value, film, weekend, source row, derived metric);
- an item with a bad claim is dropped;
- the open pass keeps only verified proposals;
- explained anomalies are excluded;
- novelty;
- the runner on real history (ledger rows, a gap, disabled);
- assignee and published matching (thread URL, title match, window, other team, ambiguous);
- both reports reproduced from the ledger;
- the emerging criteria;
- the daily-run report.

## Status and limits

- **Validated only on synthetic history.** Nothing runs on live data until weekend collection is approved.
- **Discovery items are ledgered and reported, not delivered to Slack.** Delivery and ranking come with the Phase 4 ranker.
- **Editor confirmation of pattern tags** is a ledger record (`discovery_editorial`) with no interactive surface yet; Slack interactivity needs hosting.
- **The 14-day outcome criterion** stays `pending_outcomes` until age-comparable outcomes exist (Phase 4).
