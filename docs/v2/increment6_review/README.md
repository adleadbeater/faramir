# Increment 6: Slack delivery reliability

**Code:**

| File | Change |
|---|---|
| `src/faramir/delivery.py` | New: the delivery state machine |
| `src/faramir/slack_history.py` | New: paginated, tagged history reader |
| `src/faramir/slack_post.py` | Adds `PostResult`, metadata plus visible ref, header as a bot message |
| `src/faramir/suggestions.py` | Schema v2, `update_by_id`, migration planner, exact cell text |
| `src/faramir/sheet.py` | Schema v2 columns |
| `scripts/run_daily.py` | Steps 1b, 1c, 9, 12, 13 |
| `scripts/migrate_suggestions.py`, `scripts/replay_picks.py`, `scripts/verify_slack_access.py` | New |

## 1. What Slack's documentation confirms, and what it doesn't

Checked 2026-10-22 against docs.slack.dev.

| Question | Finding |
|---|---|
| Attach metadata to a message | `chat.postMessage` accepts `metadata: {event_type, event_payload}` and needs `chat:write`, which Faramir already uses. `event_type` allows letters, digits, `_` and `-`. |
| Read history | `conversations.history` needs `channels:history` (public) or `groups:history` (private), which Faramir already uses for feedback. Pagination is `cursor` / `response_metadata.next_cursor`, with `limit` up to 999. It's a Tier 3 rate limit. |
| Read metadata back | `include_all_metadata=true` returns `metadata` on messages. Slack's metadata guide says reading metadata needs the **`metadata.message:read`** scope, and **doesn't say** whether an app can read its *own* metadata without it. Faramir's documented scopes don't include it. |
| Incoming webhooks | No documentation that webhooks carry metadata. |

**Design consequences:**
- Every pick message carries metadata **and** a visible `ref <suggestion_id>` in its existing context line. Reconciliation matches either one, so it works with Faramir's current scopes. Metadata is used whenever Slack returns it.
- The daily header moves from the webhook to `chat.postMessage` (tagged `faramir_header` with `ref daily-header-YYYY-MM-DD`) when a bot token is configured, so a rerun can find it. Without a bot token, the old webhook header is kept and can't be de-duplicated.

**Not verified against the live workspace.** I don't hold the production token. `scripts/verify_slack_access.py` is a **read-only** check (`auth.test` scopes, `conversations.history`). Running it with the production token needs your approval. See PR-3.

## 2. Delivery states

```
                     (append, logged first)
                           planned
                              │  mark 'sending' in Sheets (fails → nothing posted)
                              ▼
                           sending ──────────────────────────────┐
                              │ chat.postMessage                  │ crash / confirm write fails
         ok:true ┌────────────┼──────────────┐ timeout / unreadable│
                 ▼            ▼ ok:false     ▼                     ▼
              posted        failed        unknown             (stays sending)
                 ▲            │              │                     │
                 │            │      ┌───────┴─────────────────────┘
                 │            │      ▼   next run: COMPLETE history read
                 │            │   found tagged message ──────────────► posted
                 │            │   not found and last attempt > 5 min ─► not_delivered
                 │            │   history read failed ───────────────► unchanged (stays open, suppresses)
                 │            ▼                 ▼
                 │       triage: same run_date AND same data_date AND comp still usable
                 │            AND < 3 attempts AND this run's history read was complete
                 └──────────── yes: automatic re-delivery (counts toward the daily cap)
                               no:  expired ──► scripts/replay_picks.py (operator) ──► posted
                                                                      └──► dismissed
 legacy_unknown: migrated pre-v2 row without a Slack ts (still suppresses, like before)
```

**Suppression:** `not_delivered`, `expired`, `failed` and `dismissed` rows no longer suppress a pair. Every other state still does, including legacy rows. This is a deliberate change that affects only the new states, and legacy behavior is unchanged.

## 3. Reconciliation procedure (start of every run, under the state lock)

1. Read `conversations.history` for the last **7 days**, which covers the 5-day suppression window. Read **every page**. Anything other than a complete read is `history_ok=false`: a failed request, an HTTP error, `missing_scope`, `not_in_channel`, `ratelimited`, `has_more` without a cursor, or more than 25 pages.
2. If the history read isn't complete, **change nothing.** Open rows stay open and keep suppressing. They aren't reposted, the header is skipped, and no automatic replay happens. New picks are still delivered, because they have new IDs that can't collide with anything.
3. If the read is complete, each open row is resolved:
   - a tagged message is found → `posted`, recording the earliest ts. Two or more messages are reported as `DUPLICATE POSTS`, never merged.
   - `planned` with nothing found → `not_delivered`. It was never attempted, because the `sending` mark is written before any post.
   - `sending` or `unknown`, nothing found, last attempt more than 5 minutes ago → `not_delivered`.
   - last attempt within 5 minutes → left open.
4. Re-read the tab, then triage any `not_delivered` and `failed` rows (diagram above).

### Delivery guarantee (stated plainly)

- **Not exactly-once.** Slack's `chat.postMessage` has no idempotency key.
- **No automatic repost** while an outcome is open or posted, and none on a failed history read.
- **A duplicate is possible in one case only:** Slack accepted a message but left it out of a complete history read made at least 5 minutes later. It would then show up as two ts values for one ID on the next reconciliation, and be reported.
- **Nothing silently lost.** Every logged pick ends `posted`, `expired` (listed for review) or `dismissed`.

## 4. Simulated crash points (`tests/test_delivery.py`)

| Crash point | Next state | Next run | Result |
|---|---|---|---|
| Before posting: the `sending` mark fails | `planned` | `not_delivered`, then same-day replay | Posted **once** |
| During posting: timeout, Slack didn't accept | `unknown` | within 5 min left open; after 5 min `not_delivered` | No blind repost |
| Slack accepted, response lost (timeout) | `unknown` | found in history → `posted` with ts | **Not reposted** |
| Confirmation write to Sheets fails | `sending` | found in history → `posted` | Not reposted |
| Partial confirmation (2 picks, 2nd confirm fails) | posted / sending | 2nd → `posted` | Both posted once |
| Slack `ok:false` | `failed` | same-day replay if eligible | Posted once |
| History `missing_scope` | open | unchanged; header skipped | Still suppresses |
| Duplicate Slack posts for one ID | — | reported `DUPLICATE POSTS` | Not merged |
| Duplicated sheet row for an ID | — | `sending` mark refused | **Not posted** |
| Pick columns edited by hand | — | update refused | Not written |
| Tab sorted during a by-ID write | — | `SuggestionsWriteIncident` | Alert; manual review |

History reader tests cover: pagination (7 messages, page size 2, 4 pages); no metadata scope (all matched by ref); `missing_scope`, `not_in_channel`, `ratelimited`, HTTP 500, timeout and dropped cursor (all `ok=false`); the page cap; and a human quoting a ref (ignored).

**Pipeline tests:**
- an identical-pick rerun reposts nothing and posts no second header;
- a rerun with a genuinely new pick delivers it;
- the daily cap counts earlier runs;
- an unavailable history read skips the header but new picks still go out;
- a concurrent run holding the lock posts nothing;
- an unmigrated tab stops the run;
- a dry run makes zero writes, and its preview carries both the ref and the metadata.

## 5. Suggestions-tab schema v2 and migration

**Seven columns are appended** to the right of the 17 existing ones:

| Column | Meaning |
|---|---|
| `suggestion_id` | **Immutable.** Written once and never updated: `update_by_id` refuses to write it. Derived from the immutable pick columns, so it doesn't change when the headline, feedback, row position or delivery state changes. |
| `delivery_state` | The state in §2 |
| `delivery_attempts` | Number of posting attempts |
| `last_attempt_at` | When the last attempt started |
| `delivery_run_id` | The run that made the last attempt |
| `data_date` | Date of the box office data the pick was built from |
| `delivery_note` | Human-readable reason for the current state |

`update_by_id` may write only the delivery columns, `slack_ts` and the feedback columns. It **refuses** to write a row whose pick columns no longer match its ID.

**Migration** (`scripts/migrate_suggestions.py`: plan by default, `--dry-run`, then `--apply --confirm-sheet` plus `FARAMIR_ENABLED=true`, `--repair`):
- **IDs:**
  - a unique legacy row gets its derived ID;
  - a legacy duplicate keeps the ID with a `~2`/`~3` suffix and is reported;
  - a row without pick IDs gets `legacy-<content hash>`.
- **Delivery state:** `posted` if the row has a Slack ts, otherwise `legacy_unknown`.
- **Only the new columns are written.** The test asserts the 17 original columns of every row are identical afterwards. Feedback is untouched, and nothing is deleted or reordered.
- The tab is read back and verified. A sort during the write is detected (exit 5), and `--repair` re-derives the new columns from row content.
- **Gate:** until migrated, the daily run refuses to start (`not on schema v2`) and makes no suggestions writes.
- **Order:** run at stage S5, after merge and with scheduled runs disabled, alongside `init_state_integrity.py`.
- **Rollback:**
  1. Make a copy of the tab first (File → Make a copy).
  2. To roll back, set `FARAMIR_ENABLED=false` and delete columns R–X.
  3. The original 17 columns were never written.

## 6. Row moves, sorting and conflicting writes

**In code:**
- Every update locates rows by the explicit ID in a fresh read.
- Refused: missing rows, duplicated IDs, and pick columns that changed.
- Only allow-listed cells are written.
- Every write is read back. A row that moved *during* the write raises an incident.
- All writes happen under the state lock, so Faramir runs can't interleave.

**Proposed for production (needs your approval, PR-6):** protect the `suggestions` tab ("Restrict who can edit": the service account and owners only). Editors don't need to edit it, since feedback comes from Slack. This removes manual sorting and editing by non-owners, which is the main source of the race.

**Remaining limitation:** Sheets writes still address cells by position. The race can't be eliminated for owners or other automations with edit rights. It's narrowed to a few seconds, always detected on read-back, never silent, and refused when ambiguous.

## 7. Bug found and fixed in this increment

The Increment 5 feedback path read Slack ts values from gspread's number-converted records, so a real ts like `1700000001.000100` became `1700000001.0001` and matched no row. **All feedback with a trailing-zero ts would have been refused.**
- **Fix:** rows now expose their exact cell text (`Row.get` / `Row.raw`), and feedback and delivery use it.
- **Regression test:** `test_realistic_slack_ts_with_trailing_zeros_survives_the_feedback_path`.

## 8. Sample dry run

`sample_dry_run/` (offline fixture, empty environment, no network) recorded 11 operations and performed 0:

```
write_state_meta (lock) → update_feedback_cells → append_suggestions (planned)
→ chat.postMessage (header, metadata faramir_header) → mark_sending
→ chat.postMessage (pick, metadata faramir_pick + "ref 9d8c8898f25da967") → confirm_delivery
→ write_state_meta → write_state → write_state_meta → flush_usage
```

## 9. Remaining risks

- **Metadata read scope and real history behavior are unverified live.** The ref fallback covers a missing scope. `verify_slack_access.py` needs approval to run.
- **The header channel changes** from webhook to bot when a bot token exists. That's visible to editors (the bot's name and icon), and it's the same channel only if `SLACK_CHANNEL_ID` matches the webhook's channel. Confirm at deployment.
- **The visible `ref <id>`** adds about 22 characters to each pick's context line.
- **Positional writes:** see §6. Protecting the tab (PR-6) is strongly recommended.
- **Dry runs** don't simulate reconciliation's own Sheets updates when deciding replays. They show proposed operations only.
