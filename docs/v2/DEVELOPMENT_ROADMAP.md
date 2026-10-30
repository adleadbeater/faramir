# Faramir 2.0 development roadmap (Phases 1–5)

**Baseline:** `v2/phase-0` @ `920a9d2` (Phase 0 code-complete, provisionally accepted).

- **Scope:** local development only. No push, merge, deploy, live Sheets or Slack, scraping, backfill, restricted Cerebro access or paid API calls.
- **Specs:** `FARAMIR_V2_PLAN.md` (§3–§9), `STRATEGY_REQUIREMENTS.md` (SR-1 to SR-10), `FARAMIR_PHASE0_SPEC.md`.
- **Branches:** each phase branches from the previous phase's tip: `v2/phase-1` → `v2/phase-1b` → `v2/phase-2` → `v2/phase-3` → `v2/phase-4` → `v2/phase-5`.

## Engineering decisions (made once, here)

| Decision | Choice | Why |
|---|---|---|
| Ledger storage | Append-only JSONL, partitioned by stream and month, written under the run's artifact directory (`out/<run_id>/ledger/`) and readable from any directory | Plan §1 recommends JSONL. Where it lives in production (a ledger branch committed by Actions) is a deployment decision; until then nothing leaves the local run. |
| Config format | JSON (`config/strategies.json`, `config/priority_config.json`), not YAML | PyYAML is not in the pinned environment. Adding a dependency is not worth it for config files. |
| Strategy registry | Every one of the 13 strategies plus Discovery is listed with `enabled`, `data_status` (`live` / `history_required` / `synthetic_only` / `blocked_no_source` / `blocked_permission`) and its required sources | No strategy can be reported as operational unless its sources exist |
| Weekend strategies | Run only on committed weekend history (`faramir.history`). With collection off (PR-16) they yield "source unavailable" in production and are validated on synthetic history | History collection stays off, per the source-permission question |
| Cerebro data | `analytics/cerebro_snapshot.py` reads a **sanitized** snapshot from a configured path (the arwen/aragorn format). Tests use synthetic snapshots. No raw or row-level Cerebro data is committed to the Faramir repo; real-data runs write to the git-ignored `out/` | Preserves the access restrictions |
| Cerebro contract | The arwen/aragorn contract: Regular pages, `Publish` (not Republish), Video excluded, a minimum article age, team pools never mixed (News = News, Flash News, Niche News; Features/Lists = Feature, Mini Feature, List). Sessions are **cumulative per article** and are never treated as calendar-period traffic. | The established normalization contract |
| Claude calls | The production selection call stays as built. Discovery's open pass and any new generation run through interfaces with a deterministic offline implementation. No new paid calls. | No paid API testing |
| Aragorn | `TitleRefiner` interface with `NullRefiner` (the default) and a contract-tested fake | No callable Aragorn API exists (plan §8) |
| Calibration | Learning infrastructure is complete; estimates without real outcomes are labelled `calibration_pending` | About three months of outcomes are needed; none exist |

## Phases

| Phase | Increments | Depends on | Acceptance criteria (measurable) |
|---|---|---|---|
| **1 Strategy MVP** | 1.1 event schema and evidence ledger · 1.2 Overtake, Chase and Countdown detectors · 1.3 classifier and overlap rules · 1.4 event-level dedup · 1.5 rationale lines · 1.6 light editorial status capture · 1.7 pipeline integration | Phase 0 | A positive and a negative fixture test for every trigger rule in plan §3 (Overtake, Chase, Countdown) and every overlap rule that applies. Every event has a stable `event_id`, a source, evidence and a reason. Every candidate gets a ledger row with a disposition. No duplicate cluster is delivered. A zero-pick day works. Offline dry run green. Full suite green. |
| **1b Takeover, Hold** | 1b.1 Takeover (debut and climb, provisional tie rule) · 1b.2 Hold (challenger rules) · 1b.3 weekend-strategy runner over committed history | 1, Phase 0 history | Positive and negative tests per rule; a hand-checked set of 4 synthetic weekends matches exactly. With collection off, the result is reported as "history unavailable". |
| **2 Headline intelligence** | 2.1 Cerebro snapshot loader with an allow-list and contract filters · 2.2 Power Words · 2.3 construction taxonomy and tagger · 2.4 historical box-office headline analysis plus a 10% audit sample · 2.5 evidence-based headline recommendation (construction, power words, rationale codes) wired into the payload and the decision record | 1 | Sensitive columns are refused. Team pools are never mixed. Immature articles are excluded. Every recommended headline passes `validate_pick`. Rationale codes and evidence references are present. The tagger agrees ≥ 90% with a hand-labelled fixture set (the editor audit itself is pending). |
| **3 Discovery and feedback** | 3.1 deterministic generators (comeback, re-release share, per-theater outlier, cohort z-score, expansion surge, format contrast) · 3.2 claim verification and the open-pass interface · 3.3 novelty and `similar_prior` · 3.4 editorial status machine, reason codes and published-article matching · 3.5 Emerging Strategies report and editorial report | 1, 2 | No unverified claim is delivered (tested). Anomalies already explained by a strategy event are excluded. Reports reproduce exactly from the ledger. Promotion is never automatic. |
| **4 Learning, prioritization, Aragorn** | 4.1 age-comparable outcomes · 4.2 shrinkage estimates with n and intervals · 4.3 transparent ranker, caps and exploration · 4.4 decision records and versioned priority config · 4.5 distribution explainer and performance report · 4.6 `TitleRefiner` (null adapter) | 3 | Every score has its components. The explainer reproduces funnel counts exactly. Every priority change is versioned with reasons. Small samples (n_eff < 8) are flagged `experimental`. With no outcomes, calibration shows `pending`. Refiner output is always re-validated. |
| **5 Remaining strategies** | 5.1 Streak · 5.2 Legs · 5.3 Exit · 5.4 Global Split · 5.5 Star Record · 5.6 Dark Horse · 5.7 Studio Battle (interface, disabled) · 5.8 Premium Run (interface, disabled) · 5.9 final registry audit | 1b, 4 | Fixtures pass for each strategy. Strategies blocked on data stay disabled with a stated reason. All 13 strategies and Discovery are accounted for in the registry. |

## Blocking rule

A finding blocks the program only if it produces materially wrong editorial conclusions, risks data loss, creates a security or compliance problem, prevents safe operation, or makes the phase impossible. Everything else is decided, documented in the phase report, and the work continues.
