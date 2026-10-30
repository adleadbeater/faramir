# Increment 5: suggestions-tab reliability

**Code:**

| File | Change |
|---|---|
| `src/faramir/suggestions.py` | New |
| `src/faramir/sheet.py` | Adds guarded `append_values` and `update_cells`. **Removes** `read_suggestions` (which swallowed errors), `append_suggestions` (one call per row) and `write_suggestions` (cleared and rewrote the whole tab). |
| `src/faramir/feedback.py` | `fetch_feedback`, which returns only fetched fields |
| `scripts/run_daily.py` | Steps 1b, 9 and 13 |

## Stable identity without a schema change

A suggestion's id is **derived** from columns every row already has:

```
sha1(run_date | active_tmdb_id | comp_tmdb_id or "M"+threshold | axis | kind)[:16]
```

Sheet quirks such as `"None"`, `"9"`, `"9.0"` and `""` are normalized first. Because the id is derived:
- legacy rows get the same id as new ones;
- **nothing is backfilled, rewritten or reordered**;
- no production header change is needed in this increment.

Rows that lack those fields are reported as *unidentifiable*, and duplicate ids and duplicate Slack ts values are reported. **None of these are ever merged.**

## Idempotent appends (`append_new`)

1. Read the tab and skip any id already present. This covers same-day reruns and a pick repeated within one run.
2. Append the rest in **one** `append_rows` call. On an empty tab, the header goes in the same call.
3. After **any** error, re-read and reconcile by id before deciding anything. Only ids still missing are retried, up to 3 attempts in total.
4. If the reconciliation read itself fails, stop. Faramir never retries blind.
5. After appending, report any id that now appears more than once. Nothing is deleted.
6. A pick without an identity can't be reconciled, so it's appended at most once and never retried. It's counted as `unconfirmed_unidentifiable`.

`append_rows` is treated as **at-least-once on Google's side**. Exactly-once comes from the reconciliation by id, not from the API.

## Feedback updates (`apply_feedback`)

- **Only the four feedback cells** of a row are written (`thumbs_up`, `thumbs_down`, `thread_replies`, `thread_feedback`), and only when their value changes.
- **Target row**, found in a **fresh** read:
  - exactly one row carries the Slack ts; **and**
  - that row's derived id equals the id seen in the original read.
  - A moved row is followed and reported.
- **Refused and reported:**
  - a missing row;
  - a ts on two or more rows (in either read);
  - a ts now sitting on a different suggestion;
  - a row that can't be identified.
- **Partial Slack reads:** `read_reactions` returns only the fields it actually fetched. A failed `reactions.get` no longer writes zeros over real feedback. That's a correction to existing behavior.
- **After writing,** every updated row is read back. If the ts or the values aren't where Faramir wrote them (for example, the tab was sorted *during* the write), the run raises `SuggestionsWriteIncident`, which sends the failure alert and lists the rows to check by hand.

### Remaining limitations of non-transactional Sheets

- **Writes are by position.** A1 ranges address positions, not records. If an editor sorts or inserts rows in the seconds between Faramir's fresh read and its `batch_update`, a value could land on another row.
  - Faramir **detects** this on read-back and alerts with the exact rows.
  - It **can't undo it automatically**, because the original cells now belong to a different story. Restoring them would be another guess.
- **Mitigations:**
  - The window is a few seconds, around 10:00 ET.
  - Only feedback cells are ever written, never pick data.
  - Every write is preceded by an identity check.
  - The README will tell editors not to sort or insert rows in `suggestions` during the run.
- **The proper fix** is to stop addressing cells by position. That needs the tab to have an explicit id column and protected pick columns, which is a production schema change. It will be proposed with Increment 6's delivery columns, for your approval.

## Suppression

The suppression logic is **unchanged.** It was moved verbatim into `_suppressed_pairs()`, and a test compares it against a verbatim copy of the pre-v2 code on legacy rows: the results are identical.

The input now comes from the single Step 1b read (`Table.records()`, the same conversion as `get_all_records`) instead of a second read.

**One explicit correction:** a failed read of the suggestions tab now **stops the run**. Before, `read_suggestions` returned `[]`, which silently disabled suppression and could repeat stories.

## Failure scenarios tested (`tests/test_suggestions.py`)

| Scenario | Result |
|---|---|
| Batch append **landed but timed out** | Reconciled. No second append, each id appears once. |
| Batch append never landed | Retried once, each id appears once |
| Only some rows landed | Only the missing row is retried |
| Same-day rerun, same picks | Nothing appended |
| Persistent API errors | Stops after 3 attempts, nothing duplicated |
| Reconciliation read fails | Stops; no blind retry |
| Empty tab plus timeout | Header written exactly once |
| Unidentifiable pick plus timeout | Not retried, reported |
| Feedback on a moved row | Follows the row by ts and id; reported as moved |
| Row deleted / ts duplicated / ts now on another story | Refused; tab unchanged |
| Reactions fetch failed | Thumbs left as they were; only the replies fields written |
| Feedback batch interrupted part-way | Re-running is safe: unchanged values are skipped, the rest is written |
| Tab sorted during the feedback write | `SuggestionsWriteIncident` raised with the rows to review |
| Fresh read fails | Raises; nothing written |
| Dry-run, halted run | Operations recorded, or `WriteBlocked`; zero Sheets writes |
| Daily run when the suggestions tab can't be read | Stops; only the lock's own meta writes, no picks |

## Historical suggestions preserved

`test_daily_run_preserves_history_and_reruns_add_no_duplicates` runs the full daily pipeline over 8 legacy rows (missing ids, a duplicate id, partial feedback, `"None"` values). It checks that:
- every historical row keeps its **position and every non-feedback cell**;
- exactly one new row is appended;
- a same-day rerun returning the same pick adds **no** duplicate.

The append tests also check that the existing rows form an exact prefix of the tab afterwards.

## Not changed (Increment 6)

Picks are still posted to Slack **before** they're logged, so a same-day rerun can post a Slack duplicate even though the sheet won't get a duplicate row. Delivery states, reconciliation and replay are Increment 6.

## Recorded production requirements

See `../PRODUCTION_REQUIREMENTS.md` (PR-1 concurrency and lease, PR-2 rollback keeps backups).
