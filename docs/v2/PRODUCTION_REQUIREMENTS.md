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
| PR-7 | Run `migrate_suggestions.py` (plan → dry-run → apply, after a tab copy) at stage S5, with scheduled runs disabled; keep the copy until verified. | Increment 6 | Deployment stage S5 | Open |

## PR-1 plan (Increment 12, verified at deployment)

- Every production workflow gets `concurrency: { group: faramir-production, cancel-in-progress: false }`. Verify with two overlapping dispatches: the second must queue, not run in parallel.
- `timeout-minutes` for the daily job stays at 15 or lower, well under the 45-minute lease, so GitHub kills a stuck job before its lease can lapse.
- **A run that outlives its lease** cannot write over a newer run:
  - `StateStore.write()` re-reads the lock and refuses if another run now owns it, which is tested.
  - The remaining gap: an expired lease that nobody else has taken. Increment 12 will make `write()` also refuse when **its own** lease has expired (fail closed), with a test.
- **Local and manual runs** go through the same Sheets lock. Running locally against production is itself an approval-gated action.
