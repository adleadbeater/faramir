# Increment 3: run context, write guard, dry-run and kill switch

## One guard for every external write

Every write to Google Sheets, Slack or a committed data file first calls `faramir.runtime.authorize(category, op, detail)`.

| Situation | Result |
|---|---|
| No run context | `WriteBlocked` (fail closed) |
| Run halted (corpus ABORT, failed preflight) | `WriteBlocked` for `sheets`, `slack` and `repo`. Only the `alert` category, one failure alert, is still allowed. |
| Live run with `FARAMIR_ENABLED` not exactly `true` | `WriteBlocked` |
| Dry-run | The operation is recorded and the caller skips the real write. Alerts are recorded too, not sent. |
| Live and enabled | Recorded as `performed`, then executed |

**Where the guard sits:**

| File | Guarded writes |
|---|---|
| `src/faramir/sheet.py` | `write_state`, `append_suggestions`, `write_suggestions`, `flush_usage_to_sheet` (checked *outside* its fail-open `try`, so a block can't be swallowed), `append_corpus_meta` |
| `src/faramir/slack_post.py` | Both transport functions: the webhook and `chat.postMessage` |
| `scripts/build_corpus.py` | Both corpus file writes. Live corpus writes need `FARAMIR_CORPUS_REFRESH_ENABLED=true`, otherwise the script exits with code 4. `--dry-run` records instead. |

`tests/test_no_direct_writes.py` checks statically that:
- `requests.post` and gspread write methods appear only in those modules;
- every function there that writes also calls `authorize`.

A mutation test (removing one guard call) made 3 tests fail.

## ABORT, revised as you required

The corpus check now runs as a **preflight**, before Sheets is even opened. On ABORT, the run halts:
- no state, suggestions, usage, Slack-pick or header write can happen;
- one integrity alert is sent (in dry-run it's only recorded);
- the run exits with code 3;
- local diagnostics are written.

A failed preflight caused by missing settings halts the same way, with no alert because there is no webhook to send it to.

## Kill switch, fail-closed

- A live run needs `FARAMIR_ENABLED=true` (case-insensitive). Unset, `false`, `0`, `yes` and similar all mean disabled.
- A disabled run exits 0 having made no network call and no Sheets read, and writes only a local report.
- Dry-runs are allowed while disabled.

## Dry-run and offline mode

- `--dry-run` (or `FARAMIR_DRY_RUN=true`) writes three files to `out/<run_id>/`:

  | File | Contents |
  |---|---|
  | `report.json` | Diagnostics |
  | `would_write.json` | Every proposed write with its data |
  | `slack_preview.json` | Rendered blocks |

- `--fixtures DIR` (dry-run only) runs fully offline from `fixtures/offline/2026-10-22/`:
  - real sockets are refused;
  - HTTP is served from the manifest;
  - Claude is replaced by a deterministic responder;
  - Sheets is read-only from JSON.
- It needs **no credentials**. It's tested with an empty environment (`env -i`).
- The fixture day is synthetic, apart from the real repaired corpus.

## Samples

- `sample_dry_run/`: the offline fixture day. It records 6 writes and performs 0: feedback rewrite, header, pick, suggestions append, state write and usage flush.
- `sample_abort_dry_run/`: the same day with the legacy corpus. Exactly 1 write is recorded, the alert, and nothing else.

## Behavior preserved

- Selection rules, scoring, suppression, prompts and Slack block layout are unchanged.
- Existing write *order* is unchanged.
- The `clear()` writes remain; Increment 4 replaces them.
- The only new behavior is the guard itself, the ABORT halt, and the fail-closed switch.
