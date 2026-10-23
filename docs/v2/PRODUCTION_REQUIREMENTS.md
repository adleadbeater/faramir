# Recorded production requirements (must be satisfied before activation)

These were recorded during Phase 0 review. They don't block local increments, but **each must be verified before production activation (stage S7).**

| # | Requirement | Recorded | Planned resolution | Status |
|---|---|---|---|---|
| PR-1 | The Google Sheets state lock isn't atomic. The final deployment must include **verified** workflow concurrency protection, including runs that exceed the 45-minute lock lease. | Increment 5 review | See "PR-1 plan" below | Open |
| PR-2 | Rollback must **preserve the last-known-good backup** until state has been independently verified after restoration. Recovery records are never deleted automatically. | Increment 5 review | Init rollback now archives (renames) tabs instead of deleting, and keeps `state_lkg`. No code path deletes `state_lkg`, `state_meta` or snapshots. | Done in code and docs; to be re-verified at deployment |
| PR-3 | Verify the production Slack token: `chat:write`, `channels:history` (or `groups:history`) and `reactions:read` are present; whether `metadata.message:read` is present (optional — reconciliation falls back to the visible ref); history readable for the channel. Confirm `SLACK_CHANNEL_ID` is the channel the webhook header posts to today. | Spec revision 3; refined in Increment 6 | Run `scripts/verify_slack_access.py` (read-only) with approval | Open |
| PR-4 | `anthropic==2.1.0` must be confirmed against the version the last production run installed. | Increment 0 | Access to the Actions log | Open |
| PR-5 | Decide whether the 2026-11-01 scheduled corpus refresh is disabled (recommendation R2). | Spec revision 3 | Your decision | Open |
| PR-6 | Protect the `suggestions` tab (service account and owners only) to remove manual sorting/editing races. | Increment 6 | Sheets UI, at deployment, with approval | Open |
| PR-8 | **Slack ts stored losslessly.** New writes are `RAW` strings (tested). Before activation, write one test ts into a *copy* of the sheet and read it back unchanged; run `migrate_suggestions.py` (plan) and review `precision_damaged_slack_ts_rows` (legacy rows whose ts lost precision; their feedback can't be matched). | Increment 7 (carried from Increment 6 review) | Pre-activation check on a sheet copy | Open |
| PR-9 | **Ambiguous deliveries are never auto-retried.** Done in code: an attempted pick absent from history becomes `unconfirmed` (suppresses; manual replay needs `--checked-channel`). | Increment 7 (carried from Increment 6 review) | Implemented and tested | Done |
| PR-10 | **Pre-activation validation:** (a) run the suggestions migration and state init against a *copy* of the production sheet first; (b) `verify_slack_access.py` must show required scopes, `history_ok`, a working cursor (`pagination.cursor_returned` / `page2_ok`), and `channel_identity.faramir_daily_headers_seen > 0` for `SLACK_CHANNEL_ID`. Blocks activation. | Increment 7 (carried from Increment 6 review) | Deployment stages S5/S6, with approval | Open |
| PR-11 | **Structured output on `claude-sonnet-4-6`** is verified against the SDK only (offline). Confirm with one approved live dry run before activation; a 400 here fails safe (no picks, header warning). | Increment 7 | Live dry run, with approval | Open |
| PR-12 | **Historical validator replay.** Replay a representative set of past Faramir suggestions (read-only, from a sheet copy or export) through `validate_pick`. Report accepted vs rejected, rejection reasons by category, and every legitimate headline the validator filters incorrectly, so factual improvements can be told apart from unnecessary losses. Known limitations to carry into that review: third-film detection (no alias table; short one-word titles unchecked), active-film freshness for milestone claims (not re-checked), genre claims in `claims[]` not attributed to a film, word-list tense/direction rules. | Increment 8 review | Read-only replay, with approval to read the data | Open |
| PR-13 | **State tab gains three columns** (`tn_slug`, `identity_confidence`, `identity_method`). The first run after deployment reads the 17-column legacy tab, backs it up to `state_lkg` and writes the 20-column header. Verify on a *copy* of the sheet first (with PR-10a). Optional editor-owned `identity_overrides` tab (`tn_slug`, `tmdb_id`, `note`) is read-only to Faramir; create it only if editors want overrides for films not yet in state. | Increment 9 | Deployment stage S5, with approval | Open |
| PR-7 | Run `migrate_suggestions.py` (plan → dry-run → apply, after a tab copy) at stage S5, with scheduled runs disabled; keep the copy until verified. | Increment 6 | Deployment stage S5 | Open |

## PR-1 plan (Increment 12, verified at deployment)

- Every production workflow gets `concurrency: { group: faramir-production, cancel-in-progress: false }`. Verify with two overlapping dispatches: the second must queue, not run in parallel.
- `timeout-minutes` for the daily job stays at 15 or lower, well under the 45-minute lease, so GitHub kills a stuck job before its lease can lapse.
- **A run that outlives its lease** cannot write over a newer run:
  - `StateStore.write()` re-reads the lock and refuses if another run now owns it, which is tested.
  - The remaining gap: an expired lease that nobody else has taken. Increment 12 will make `write()` also refuse when **its own** lease has expired (fail closed), with a test.
- **Local and manual runs** go through the same Sheets lock. Running locally against production is itself an approval-gated action.
