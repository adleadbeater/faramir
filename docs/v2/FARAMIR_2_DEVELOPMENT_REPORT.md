# Faramir 2.0: consolidated development report (Phases 1–5)

**Date:** 2026-10-23. **Branch:** `v2/phase-5` @ `d18df4f`. All work is local.

**This is not a production-ready product.** Every strategy, ranking and learning path in it is code-complete and tested. Nothing has run against production. Several capabilities have never seen real data. Activation depends on the decisions in §9.

## What was not done (per the authorization)

- No push, merge or deploy.
- No live Google Sheets writes and no Slack messages.
- No workflow enabled.
- No scraping or historical backfill.
- No restricted Cerebro access. The only Cerebro data used is arwen's existing *sanitized* snapshot, read locally, with output only in git-ignored `out/`.
- No paid API calls: every Claude call in tests and dry runs uses the offline responder.
- No credential, secret or repository-setting changes.

The Phase 0 dry-run, fail-closed and runtime-write safeguards are unchanged. The Phase 0 source-permission and production-readiness decisions are **still open and were not bypassed**: all weekend-chart strategies stay behind `FARAMIR_COLLECT_HISTORY` (off, PR-16).

---

## 1. Phase status

| Phase | Branch | Commit | Status | Phase report |
|---|---|---|---|---|
| 0 Baseline (accepted) | `v2/phase-0` | `920a9d2` | Accepted baseline | `docs/v2/phase0_remediation/README.md` |
| 1 Strategy MVP | `v2/phase-1` | `ef00245` | Complete | `docs/v2/phases/PHASE1.md` |
| 1b Takeover, Hold | `v2/phase-1b` | `5f52553` | Complete (synthetic history only) | `PHASE1B.md` |
| 2 Headline intelligence | `v2/phase-2` | `da16cf1` | Complete; editor audit of the tagger pending | `PHASE2.md` |
| 3 Discovery and feedback | `v2/phase-3` | `f84866e` | Complete (synthetic history only) | `PHASE3.md` |
| 4 Learning, prioritization, Aragorn | `v2/phase-4` | `f733b71` | Infrastructure complete; **calibration pending** | `PHASE4.md` |
| 5 Remaining strategies | `v2/phase-5` | `dfbc808`, then fixes `c5fc23d`, `9a49ffc`, `d18df4f` | Complete; 4 strategies disabled with reasons | `PHASE5.md` |

Each branch was cut from the previous phase's tip. In total: 84 files changed and +6,633 lines since Phase 0.

---

## 2. All 13 strategies plus Discovery

The three validation levels:
- **A — real data:** runs today on live sources (BOM/TN daily, `corpus_v2`) and has produced output in an offline dry run built from real-format fixtures.
- **B — synthetic only:** correct on hand-built fixtures; its source exists but collection is off until approved.
- **C — awaiting data or permission:** the interface and detector exist, but it is disabled for lack of a source or an editorial decision.

| # | Strategy | Enabled | Data status | Level | What it needs to go live |
|---|---|---|---|---|---|
| 1 | Overtake | ✅ | live | **A** | The Phase 0 activation path only |
| 2 | Chase | ✅ | live | **A** | Same |
| 4 | Countdown | ✅ | live | **A** | Same |
| 5 | Takeover | ✅ | history_required | **B** | Weekend history collection (D1, PR-16) |
| 6 | Hold | ✅ | history_required | **B** | Same |
| 3 | Streak | ✅ | history_required | **B** | Same, plus ≥ 3–6 consecutive collected weekends |
| 6 | Exit | ✅ | history_required | **B** | Same, plus 2+ collected weekends (end-of-run needs 2 complete absences) |
| 7 | Legs | ✅ | history_required | **B** | Same, plus ≥ 20 films per genre × band × week cohort: **months** of history |
| 13 | Dark Horse | ✅ | history_required | **B** | Same. Budgets are unused (no verified source) |
| 12 | Star Record | ❌ | live | **C (decision)** | Editors must approve career-record wording (SR-1). The detector runs on the live corpus and TMDB credits |
| 11 | Global Split | ❌ | synthetic_only | **C (data)** | A source for international rollout dates/status (new data source) |
| 10 | Studio Battle | ❌ | blocked_no_source | **C (data)** | A distributor year-to-date source plus a canonical name map (new data source) |
| 9 | Premium Run | ❌ | blocked_no_source | **C (data)** | A verified IMAX/premium-format source (new data source). The detector refuses to run without one |
| — | **Discovery** | ✅ | history_required | **B** | Weekend history. The model open pass is `NullOpenPass` (wiring Claude = new paid calls, not authorized). Territory breakouts are not built (no source) |

The plan numbers Exit and Hold both as 6; the registry keys are distinct. "Enabled" with `history_required` means: **silent in production today**. Every run reports `weekend_strategies: history_collection_disabled` and `discovery: history_collection_disabled`, never an empty success.

---

## 3. Test results

**Full suite: 920 passed, 2 skipped** (the 2 skips are the opt-in live tests, unchanged from Phase 0). Time ≈ 14 s.

| Phase | Test file | Tests |
|---|---|---|
| 0 baseline | (existing) | 757 |
| 1 | `test_strategies_p1.py` | 49 |
| 1b | `test_strategies_p1b.py` | 14 (incl. 4 hand-checked weekends) |
| 2 | `test_headlines_p2.py` | 27 |
| 3 | `test_discovery_p3.py` | 18 |
| 4 | `test_learning_p4.py` | 27 |
| 5 | `test_strategies_p5.py` | 28 |

**Offline dry run** (`run_daily.py --dry-run --fixtures fixtures/offline/2026-10-22`):
- 20 events (16 Overtake, 4 Chase) into 20 clusters;
- 126 Chase candidates rejected as `not_meaningful`;
- weekend strategies `history_collection_disabled`;
- calibration `calibration_pending`;
- 6 ranked slots and 1 validated pick;
- **14 writes recorded, 0 performed; Slack preview `sent: false`.**

### Defects found and fixed during Phases 1–5

| Phase | Defect | Severity |
|---|---|---|
| 1 | **Latent Phase 0 bug:** a same-day rerun rolled "yesterday" forward to the previous run's values, so day-over-day figures came out wrong | Real (editorial figures). Fixed |
| 1 | The slim Claude payload dropped event fields | Fixed |
| 2 | "Passes $X" headlines were tagged as the wrong construction | Fixed |
| 2 | Construction lifts hid an article-age bias; `age_days_median` is now surfaced | Fixed (disclosed) |
| 3 | The re-release share value was not verified against rows | Fixed |
| 3 | Derived percentages were directly claimable | Fixed (disallowed) |
| 4 | Event picks had no suggestion ID and were silently skipped | Fixed |
| 4 | The interval collapsed when all values were equal | Fixed |
| 4 | Exploration repeated a strategy already in the main slots | Fixed |
| 5 | Assignee parsing read "@dana for" as a two-word name | Fixed |
| 5 | A single outcome produced a zero-width interval | Fixed (now `None`) |
| 5 | The Streak rationale cited only 3 of its weekends and read "has been the top 3" | Fixed |
| 5 | A provisional event's rationale didn't say so | Fixed |
| 5 | Discovery text said "1 titles" | Fixed |

---

## 4. Examples: recommendations, headlines, rationales

Every rationale is built deterministically from event evidence. No model writes it.

**Live-data path (level A; offline fixture day 2026-10-22):**
- Slack pick: *Beta Story vs Superman*.
- Rationale: `Why: Overtake · worldwide $322.0M passed Superman (1978) at $300.5M; $300.0M the day before · sources: bom_title 2026-10-21; corpus_v2 2026-10-01`
- Context: `Same Origin · Just Passed · Worldwide`.

**Weekend strategies (level B; generated from the hand-checked synthetic weekends):**
- `Hold · Alpha held No. 1 on the 2026-10-02 weekend ($25.0M) against Charlie, which opened in 3,600 theaters with $18.0M at No. 2. · sources: tn_weekend_history 2026-10-02; tn_weekend_history 2026-09-25`
- `Takeover · Delta is No. 1 on the 2026-10-09 weekend chart ($30.0M), taking over from Alpha (No. 1 on 2026-10-02), in its opening weekend.`
- `Streak · Alpha has been in the top 3 for 4 consecutive complete weekends (through 2026-10-16). · sources: [all four weekends]`
- `Hold (provisional) · Alpha held No. 1 … ($10.1M) against Foxtrot … $10.0M`. This is a 1% margin on a provisional chart, so it is **not delivered** until re-collected.
- End-to-end test: Claude (offline) writes "**Beta Story Takes No. 1 at the Weekend Box Office**". Validation accepts the rank language only because chart-rank evidence exists (SR-1). The Slack block shows "Why: Takeover".

**Discovery item (level B; synthetic weekend):**
- Status `proposed`, labelled "needs editorial review; not an established fact".
- Text: "Re-releases took 23.1% of the weekend's chart gross (1 title; largest Classic, $3.0M)."
- Suggested headline: "Re-Releases Took 23% of the Weekend Box Office".
- It carries 2 structured claims, each checked against the stored chart row. One failing claim would drop the item.

**Rejected by validation (from tests):**
- invented figures from a refiner;
- reversed comparisons;
- headlines over 85 characters;
- "career-best" wording without Star Record evidence;
- "No. 1" without chart-rank evidence.

---

## 5. Demonstrations: Cerebro-informed prioritization and feedback processing

### 5a. Prioritization with the real sanitized snapshot (local, `out/p5_cerebro`)

- **Snapshot:** arwen's sanitized `cerebro_MW.csv`, as of 2026-09-26 (latest publish date), 9,083 rows loaded under the allow-list.
- **Pool:** MW News only. Features/Lists are never mixed in. 155 matured box-office articles.
- **Baseline:** median **2,383** cumulative sessions per article (ages 14–209 days; *cumulative sessions per article to the snapshot date, not calendar-period traffic*).

The decision record for the rank-1 event (Overtake, Beta Story):

| Component (weight) | Value | Source |
|---|---|---|
| event_strength (.35) | 0.80 | Overtake evidence |
| audience_affinity (.20) | 0.50 | Neutral: the film has no MW coverage in the snapshot (`tier: unknown`, pool median 2,651) |
| strategy_perf_adj (.15) | 0.50 | Neutral: **calibration_pending** (n = 0) |
| headline_evidence (.10) | **0.739** (vs 0.50 without the snapshot) | passes_comparison **+96%** (n = 14, measured); lead_with_number +42% (n = 2, *experimental*); contrast_upset +39% (n = 6, *experimental*) |
| novelty (.10) / confidence (.10) | 1.0 / 0.70 | — |
| penalty | −0.05 | Legacy identity |
| **Score** | **0.649** (0.625 without the snapshot) | |

- **Power Words** passed to Claude: "Officially" (MW 21,871 sessions per article, n = 514), "Box Office", "Surges".
- **Historical construction table:** chart_position **+184%** (n = 12), passes_comparison **+96%** (n = 14), record_claim **−30%** (n = 55).
- **Caveats:** these lifts are not age-matched, and the samples are small. They are a historical prior, not Faramir's learned performance.

### 5b. Feedback processing (synthetic ledger, reproduced exactly from the ledger)

- **Editorial report:**
  - Overtake: 2 delivered, 1 published, 1 awaiting, acceptance 50%.
  - Chase: 1 delivered, 1 rejected (`timing`), acceptance 0%.
- **Explainer funnel (Overtake):** detected 6 → deduped 5 → ranked 5 → suggested 3 → delivered 2 → accepted 1 → published 1 → outcome 0.
- **Performance, before outcomes:**
  - `pending — no age-comparable outcomes yet`;
  - 2 deliveries have no published URL;
  - 1 has no observation.
- **After one day-14 observation (7,000 sessions):**
  - `estimated`, n = 1, **experimental**, interval `None` (no spread information);
  - the strategy falls back to global;
  - the ranker still makes no downward adjustment (< 8 deliveries).
- **Assignee from thread:** "assigned to @dana for tomorrow" → `dana`.
- **Published-article matching:** a thread URL wins. Otherwise News-pool title matching within 7 days. Near-ties are reported as ambiguous and never linked.

---

## 6. Known limitations and unavailable integrations

- **No real outcomes.** All performance estimates are `calibration_pending`. They need ~3 months of delivered, published and age-comparable outcomes, and a Cerebro snapshot refreshed every few days. Neither exists.
- **Weights and every [CAL] threshold are initial values:** plan §6 weights, Streak tiers, the Exit 40% drop, the Legs cohort of 20, the Dark Horse 1.5×. Priority proposals are never applied automatically.
- **Weekend strategies and Discovery have never run on real weekend charts.** Legs will stay silent for months after collection starts.
- **Aragorn:** only `NullRefiner`. No callable Aragorn API exists. The 95-vs-85 character cap is unresolved: Aragorn uses 95; Faramir validates at 85.
- **Discovery open pass:** a Claude proposer is an interface only (new paid calls not authorized). Territory breakouts are not built.
- **Cerebro:** a static sanitized snapshot. There is no automated refresh path, and audience affinity is neutral for films MW hasn't covered.
- **Tagger:** 36/40 (90%) on a hand-labelled fixture. The editor audit (10% sample in `out/headline_analysis_real/audit_sample.csv`) has not been done.
- **Editor interaction:** status, pattern tags and assignment are read from Slack threads and reactions. No interactive Slack surface exists (it would need hosting).
- **Validator limits carried from Phase 0 (PR-12):**
  - third-film detection has no alias table;
  - milestone freshness is not re-checked;
  - genre claims are unattributed;
  - tense and direction rules come from word lists.

---

## 7. Production blockers

Phase 0 blockers, all still **open**:
- **D1 / PR-16** source permission for TN/BOM weekend charts;
- **PR-3 / PR-10** Slack scopes and channel identity;
- **PR-4** SDK version confirmation;
- **PR-6** protect the suggestions tab;
- **PR-7 / PR-13** migrations on a sheet copy;
- **PR-8** Slack ts round-trip;
- **PR-11** one approved live structured-output call;
- **PR-12** historical validator replay;
- **PR-17** repository variables created as `false`;
- **PR-18** artifact retention;
- **PR-20** the schedule-time decision;
- **PR-21** independent re-review of the remediation;
- **D5** compatibility with v1.

**Time-sensitive and independent of v2 — PR-19 / D6:** `main` still schedules the legacy corpus refresh for **2026-11-01 09:00 UTC**. Unless that workflow is disabled in GitHub, it will rebuild `data/corpus.json` with title-matched (wrong) identities.

New in Phases 1–5:
1. **Ledger location in production** (plan §1): an Actions-committed ledger branch or another store. Today the ledger is written under the run's `out/` directory, so in Actions it would not persist between runs. Dedup across days and learning **depend on persistence**.
2. **Cerebro refresh path:** how a sanitized snapshot reaches the runner, and how often.
3. **Re-review** of Phases 1–5 (this branch) before any merge.
4. **Prompt changes:** the Phase 2 and Phase 4 prompt sections change Claude's input (hashes pinned in tests). PR-11's live check must run on the v2 prompt.

---

## 8. Deployment and rollback requirements

**Deployment** follows the Phase 0 staged plan (`PRODUCTION_VALIDATION_PLAN.md`, V0–V8 and S1–S8). Each step needs separate approval:
1. Re-review, then push the `v2/*` branches and open a PR. **Not merged.**
2. Create the repository variables as `false`.
3. Rehearse the migrations on a sheet copy.
4. Run the Slack/Anthropic read-only checks.
5. Shadow comparison (≥ 5 days).
6. Activate with `FARAMIR_ENABLED`.

**Phase 1–5 additions:**
- Decide where the ledger lives.
- Set `FARAMIR_CEREBRO_SNAPSHOT` / `FARAMIR_POWER_WORDS` only after the refresh path is approved. Without them the ranker runs neutral, not broken.
- `FARAMIR_COLLECT_HISTORY` stays off until D1.
- Strategy enablement is controlled by `config/strategies.json`.
- Ranking weights are in the versioned `config/priority_config.json`.

**Rollback:** the Phase 0 procedure (`migrate_sheets.py --rollback-plan`) is unchanged:
- `FARAMIR_ENABLED=false` stops v2;
- tabs are archived (renamed), never deleted;
- v2 suggestion columns must be exported before v1 runs again;
- verify v1 with a dry run.

The Phase 1–5 features add no Sheets tabs. The ledger is append-only JSONL outside the Sheet, so rolling back needs no ledger action; keep it for audit. Disabling any one strategy is a one-line registry change, with no code rollback.

---

## 9. Exact decisions needed before activation

1. **D6 / PR-19 (by 2026-11-01): disable the legacy *Faramir Corpus Refresh* workflow on `main`.** This is your GitHub action; I'm not authorized to do it.
2. **D1:** permission to use The Numbers and Box Office Mojo weekend charts. This gates Takeover, Hold, Streak, Exit, Legs, Dark Horse and Discovery.
3. **Ledger storage location** in production.
4. **Cerebro snapshot refresh path and cadence** (sanitized, allow-listed).
5. **Star Record:** approve or decline career-record wording (SR-1). Until then it stays disabled.
6. **Global Split, Studio Battle and Premium Run:** keep them disabled, or approve finding a new data source for each. Each one is a new-data-source decision.
7. **Initial weights and [CAL] thresholds:** accept the plan values for launch, or change them.
8. **Aragorn:** integration option (HTTP or vendored), and the 95-vs-85 character cap.
9. **Discovery open pass:** whether to authorize the paid Claude proposer (with a cost cap), or keep deterministic generators only.
10. **Editor audit** of the 10% headline-tagger sample.
11. The remaining Phase 0 items: PR-3/4/6/7/8/10/11/12/13/14/17/18/20/21 and D5.
12. **Independent re-review** of `v2/phase-1` … `v2/phase-5` before push or merge.
