# Faramir 2.0: deployment plan (2026-10-30)

Goal: Faramir 2.0 replaces the current Faramir in place. Same repository (`adleadbeater/faramir`), same
GitHub Actions runner, same service account, same Google Sheet, same Slack bot and channel. Editors see
the same kind of Slack post each day, with the new titles, earlier in the day.

## What went wrong

The work since 2026-10-26 (description kits, verb ranking, descriptors, the Aragorn title stage, the
learned chart timing, the Power Words and Cerebro syncs) was built into `scripts/faramir_run.py`, the
**local test harness**, instead of into `scripts/run_daily.py`, the **production pipeline** that GitHub
Actions runs. The harness was meant only to drive production code on the Mac. It grew its own card
builder, title wiring, chart timing and data loading, and its data came from Mac-only sources (the Drive
mount, a CSV download). None of that reaches the runner. Deploying the branch as it stood would have
posted the old Claude-selected picks without the new titles.

Fix: production owns all of it; the harness becomes a thin wrapper that calls the same production code.

## Facts that shape the plan

| Fact | Consequence |
|---|---|
| `adleadbeater/faramir` is **public** | No Cerebro or Power Words data may be committed to it. Data is read at run time with the service account, and never written to the repo. |
| Only GitHub Actions holds Faramir's credentials (service account, Slack bot, Sheet IDs) | GitHub Actions stays the runner. Nothing needs to be copied anywhere. |
| Production already reads Cerebro from a Google Sheet (`CEREBRO_MW_SHEET_ID`, tab "MovieWeb Article Analysis") | The title stage reads the same sheet, **allow-listed columns only** (the old reader pulled every column, writer and pay included). |
| The Power Words loader already reads the live sheet with the service account | The sheet must be shared (view) with the service account's email. If it isn't, titles still work from Cerebro alone, with a warning. |
| GitHub started the old 15:00 UTC job 3–5 hours late every day | Schedule many off-the-hour triggers (every 15 min, 10am–3pm ET) instead of one. Each trigger costs about a minute when the chart isn't ready. |
| The chart timing and evidence ledger must remember previous days; Actions is stateless | Kept on a `faramir-state` branch that the workflow commits (the plan's own §1 decision; the same pattern as Strider's `strider-state`). |
| The Shell Agent VM has Python 3.11 and systemd timers, but no Faramir credentials | The VM is an option later, not needed to launch. |
| Hishco (the suite bot) can read but not push `adleadbeater/faramir`; `robert-sperduto` has no token on the Mac | **Pushing needs access from Alex**, or Rob's own GitHub sign-in. |

## Build steps (local, no approvals needed)

| # | Step | Status |
|---|---|---|
| 1 | Cards, titles and TMDB credits in production modules (`faramir/cards.py`, `faramir/titles/credits.py`) | Done (f2115e3) |
| 2 | Production step 11: ranked cards → Aragorn titles → bound picks; `FARAMIR_PICK_ENGINE=claude_select` for rollback | Done |
| 3 | Cerebro for titles: allow-listed reader for the production Cerebro sheet (only allowed columns fetched) | Done; first real read happens in the dry run |
| 4 | `run_daily.py --require-chart`: learned readiness, exit 75 until ready, once per day | Done |
| 5 | Workflow: 15-minute off-the-hour triggers, chart timings on the `faramir-state` branch | Done; first real run in the dry run |
| 6 | Slack post: title, alternative, numbers, label, one context line, source links | Done (0cbf663) |
| 7 | The harness calls the production modules | Done |
| 8 | Real-data check of production step 11 (titles stubbed, Slack and Sheet faked) | Passed: 4 slots filled, 4 picks bound, posted and logged |

## Decisions this surfaced

- **Weekend history is off in production** (`FARAMIR_COLLECT_HISTORY: 'false'`, PR-16, pending D1). Takeover,
  Hold and Discovery cards need it; without it, production posts Chase, Countdown, Overtake and Dark Horse only.
  Turning it on is a one-line workflow change once D1 is signed off.
- **The evidence ledger is not persisted in production** (it stays in each run's artifact, as today). The plan's
  §1 put it on a committed branch, but the repository is public and decision records can carry Cerebro-derived
  scores. Only the chart timings (public data) are committed. Learning from outcomes needs a private home later.
- **Run artifacts on a public repository** (D4): any signed-in GitHub user can download them. They carry card
  text and box-office figures, not credentials. Recommend 30-day retention, or a private repository.

## Rob's steps (blocking)

| # | What | When |
|---|---|---|
| R1 | Disable **Faramir Corpus Refresh** (Actions → ⋯ → Disable workflow) | **Before Sun Nov 1, 4am ET** |
| R2 | Push access to `adleadbeater/faramir` (Alex adds `robert-sperduto` or `Hishco` as a collaborator) | Before the first dry run |
| R3 | Share the Power Words sheet (view) with Faramir's service-account email (shown in the first dry-run report) | Before the first dry run |
| R4 | Approve: push the branch, one GitHub dry run (writes recorded, none performed, no Slack) | After the build steps |
| R5 | Approve the Sheet migration (one-way: the old build must not run against the migrated Sheet) | Cutover day |
| R6 | Approve the switch: merge to `main`, set `FARAMIR_ENABLED=true` | Cutover day |
| D1 | Company sign-off on The Numbers / Box Office Mojo use (unchanged from the old build) | Before R6 |

## Cutover order (from PRODUCTION_DECISIONS D5)

1. A normal old-build run posts.
2. Merge to `main` (workflows stay off until `FARAMIR_ENABLED=true`).
3. Migrate the Sheet (rehearsed on a copy first).
4. One dry run on `main`, reviewed.
5. Set `FARAMIR_ENABLED=true`. The next 15-minute trigger posts.
6. Watch the first three days (V8).

Rollback: set `FARAMIR_PICK_ENGINE=claude_select` (old selection, same posting path), or `FARAMIR_ENABLED=false`.
