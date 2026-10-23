# Increment 7: Claude output and pick binding

**Code:**

| File | Change |
|---|---|
| `src/faramir/schemas.py` | New |
| `src/faramir/binding.py` | New |
| `src/faramir/claude_select.py` | `select()` replaces free-text extraction; the prompt is **unchanged** |
| `src/faramir/config.py` | New limits |
| `src/faramir/delivery.py` | New `unconfirmed` state |
| `src/faramir/suggestions.py` | Detects precision-damaged ts; traceability columns |
| `scripts/run_daily.py` | Wiring |
| `scripts/replay_picks.py` | `--checked-channel` |
| `scripts/verify_slack_access.py` | Pagination and channel-identity probes |

## 1. Structured output, verified against the installed SDK

- `anthropic==2.1.0` is pinned. Checked locally:
  - `messages.create` accepts `output_config`;
  - `anthropic.transform_schema` turns a Pydantic model into the JSON schema;
  - `messages.parse` exists, but validates inside its response parser.
- Faramir calls `messages.create(..., output_config={"format": {"type": "json_schema", "schema": transform_schema(PickSet)}})`. It checks `stop_reason` **first** and only then validates the text with `PickSet.model_validate_json`.
- **Schema** (`PickSet.picks[]`, `extra="forbid"`, every field required):

  | Field | Type |
  |---|---|
  | `active_tmdb_id` | int |
  | `active_title` | str |
  | `comp_tmdb_id` | int \| null |
  | `comp_title` | str \| null |
  | `axis` | `domestic` \| `worldwide` |
  | `kind` | `just_passed` \| `about_to_pass` \| `milestone` |
  | `threshold_value` | int \| null |
  | `angle_category` | str |
  | `headline` | str |
  | `angle` | str |

  This is exactly the format the existing prompt already asks for.
- **Unchanged:** the model (`claude-sonnet-4-6`) and the prompt. A test pins the prompt's SHA-256 to the `f2a0ce4` original. Prompt caching is unchanged.
- **Not verified live** (PR-11): whether `claude-sonnet-4-6` accepts `output_config` in production. If it doesn't, the API returns 400 and the run fails safe.

## 2. Candidate binding

A pick is accepted only if it identifies **exactly one** real, unsuppressed candidate:

| Pick | Rule |
|---|---|
| Active film | Must be in today's payload |
| Comparison pick (`comp_tmdb_id` set) | A candidate must exist for (active, comp, axis). `kind` must equal that candidate's kind. A stated `threshold_value` must equal the comparison film's gross on that axis. `kind=milestone` is rejected. |
| Milestone pick (`comp_tmdb_id` null) | `kind` must be `milestone`, the threshold present, and a milestone candidate must exist for (active, threshold, axis) |
| `angle_category` | Must be one of the candidate's computed categories, or the prompt's family name (`creative_head_to_head` → `_director` / `_cast`) |
| Suppressed candidate, a second pick of the same candidate, more than one matching candidate | Rejected |

**Traceability:** every accepted pick carries:
- `candidate_id` = `sha1(data_date | active | C<comp> or M<threshold> | axis | kind)[:16]`, stable for the same event on the same data day;
- an `evidence` snapshot: the data date, the corpus gross date, the event, the active film's figures, and the comparison film's identity, gross and status;
- `evidence_ref` = a hash of that snapshot.

Both are written to two new **immutable** columns in the not-yet-deployed v2 tab (`candidate_id`, `evidence_json`). Every accepted and rejected pick, with its reason, is recorded in the run report (`claude_binding`).

### Examples (from `tests/test_selection.py`)

| Claude pick | Result |
|---|---|
| Alpha Film / comp 9 / domestic / just_passed / franchise_sibling | **Accepted**, candidate `…`, evidence: comp domestic $150.0M reported, active $152.0M |
| Milestone $150M domestic, round_number_milestone | **Accepted** |
| comp 10 / about_to_pass / `creative_head_to_head` (candidate has `_director`) | **Accepted** (family name) |
| comp 12345 | Rejected: *invented comparison: not a candidate for this film* |
| comp 10 on **worldwide** (candidate is domestic only) | Rejected: *wrong axis* |
| comp 9 as `about_to_pass` (candidate is `just_passed`) | Rejected: *event kind does not match* |
| comp 9 with threshold 999 | Rejected: *threshold does not match the comparison's domestic gross* |
| Milestone $200M (not a candidate) | Rejected: *unsupported milestone* |
| Milestone $300M worldwide (suppressed) | Rejected: *candidate is suppressed* |
| comp 9 labelled `unexpected_giant_killer` or `asymmetry` (not computed for this candidate) | Rejected: *angle_category not supported* |
| The same candidate twice | Second rejected: *duplicate* |
| A candidate listed twice in the payload | Rejected: *ambiguous: 2 candidates match* |

## 3. API failures and retries

| Situation | Behaviour | Faramir-level retry? |
|---|---|---|
| Timeout or connection error | The SDK retries (`max_retries=2`, `timeout=120s`), then **no picks**, reason `api_timeout` / `api_connection_error` | No |
| HTTP 4xx/5xx (`APIStatusError`) | 429/5xx retried by the SDK, then **no picks**, `api_status_<code>` | No |
| `stop_reason == "refusal"` | **No picks**, `refusal` (category recorded) | No |
| `stop_reason == "max_tokens"` | **No picks**, `truncated` | No (raise `SELECTION_MAX_TOKENS` instead) |
| Output fails the schema (not JSON, wrong enum, extra or missing field) | One more attempt; then **no picks**, `malformed_output` | Yes, once (`SELECTION_ATTEMPTS=2`) |

- **Every attempt's token usage** is buffered for the Usage tab (`select_picks#1`, `#2`). Failures are recorded in `claude_selection.attempts`.
- **On any failure** the run still posts its header, with *"⚠ Pick selection unavailable today (<reason>) — no picks posted."* It never posts invented, stale or reused picks.
- **Retries can't duplicate Slack posts.** The retry happens before anything is logged, and delivery is keyed by `suggestion_id` (Increment 6). This is tested: malformed output, then a valid response, gives exactly one post.

## 4. Cost controls

| Control | Value |
|---|---|
| `SELECTION_MAX_TOKENS` | 4096 (unchanged; it was hard-coded before, now read from config) |
| `SELECTION_MAX_INPUT_TOKENS` | 60,000 [CAL] |
| `SELECTION_TIMEOUT_S` | 120 |
| `SELECTION_MAX_RETRIES` | 2 |
| `SELECTION_ATTEMPTS` | 2 |

- **Token cap:** before the call, `messages.count_tokens` measures the payload.
  - If it's over the cap, the **lowest-richness candidates are trimmed** to about 90% of the cap and the payload is re-counted.
  - If it's still over (or nothing is left), the call is skipped and the result is `skipped_token_cap` with no picks.
  - If counting itself fails, a conservative local estimate is used (characters ÷ 3).
- **Today's payload size:** the offline fixture day counts about 4.4k tokens, far under the cap.

## 5. Changes to actual story selection

**No change** to the prompt, model, candidate generation, scoring, suppression or ranking. What changes is which of Claude's **outputs are accepted**:

| Previously posted, now rejected | Why |
|---|---|
| A pick naming a comparison film or milestone that wasn't a candidate | It was never verified |
| A pick of a suppressed pair. The matcher drops suppressed comparison candidates, so a repeated pair is "not a candidate". | Before this, only the prompt prevented repeats |
| Wrong axis, wrong kind, a threshold that doesn't match the comparison film's gross | The figures would have been wrong |
| An `angle_category` the candidate doesn't support, e.g. `asymmetry` (never computed: `detect_asymmetry` isn't wired in), or `franchise_sibling` without a shared collection | The label in the Slack meta line would have made an unsupported claim |
| Output that isn't schema-valid. Before, the code accepted JSON with surrounding prose. | — |

**Impact estimate:** I can't measure this against real Claude outputs without production data. The likeliest reduction comes from the category rule. When a candidate's only computed category isn't in the prompt's list (`genre_milestone`, `studio_sibling`, `same_origin`), Claude may label it with a prompt category that isn't supported, and that pick is now rejected.

- **Detection:** the run report lists every rejection, so the first dry runs will show the rate.
- **Fix:** a one-line prompt instruction to "use one of the candidate's angle_categories", proposed for Increment 8 (prompt edits need approval).

## 6. Safeguards carried forward from the Increment 6 review

| Safeguard | Status |
|---|---|
| Slack ts stored losslessly | New writes send the ts as a string with `value_input_option=RAW` (tested). Legacy rows whose stored ts doesn't match `^\d{9,11}\.\d{6}$` are flagged (`precision_damaged_slack_ts_rows` in the run report and the migration plan, plus a note on the row). Final check on a sheet copy: **PR-8 (blocker)**. |
| No auto-retry just because a message is absent from history | **Done.** A pick that was attempted but isn't in history becomes `unconfirmed`: it keeps suppressing, is never retried automatically, and a manual replay needs `--checked-channel`. Only never-attempted (`not_delivered`) and Slack-rejected (`failed`) picks can be retried automatically. |
| Migration, channel identity, permissions and pagination validated before activation | `verify_slack_access.py` now also probes cursor pagination and checks that the channel contains Faramir's current webhook headers. Migration and init run on a sheet copy first. **PR-10 (blocker).** |

## 7. Tests

**361 passed, 0 failed.** That's 325 carried forward plus 36 in `tests/test_selection.py`.

**Replaced** (justified API or behavior changes, each documented in the test):

| Old test | Replaced by |
|---|---|
| `test_select_picks_parses_json_with_prose_preamble` | Schema-valid JSON only |
| `test_baseline_select_picks_only_checks_active_id` | The invented comparison is rejected by binding |
| `test_baseline_anthropic_client_has_no_timeout_or_retry_config` | The client has an explicit timeout and retries |
| The Increment 6 test expecting `not_delivered` after an ambiguous attempt | Expects `unconfirmed` |
| The Increment 6 identical-rerun test | The repeat is rejected earlier, at binding |

**Offline dry run** (`sample_dry_run/`, empty environment, sockets blocked): the stand-in responder counts tokens, a schema-valid pick binds (`candidate_id 5533665f11c17d9f`, `evidence_ref c66dc334bee14053`), and 11 writes are recorded with **0 performed**.

## 8. Remaining risks

- **PR-11:** structured output on the production model hasn't been checked live.
- **Category rejections** may lower pick volume until the Increment 8 prompt line is approved.
- **Evidence is captured at binding** (the data date's figures). If figures are revised later the same day, that isn't reflected until the next run.
- **The token-cap trim keeps the highest-richness candidates.** It could remove a lower-scoring candidate Claude would have preferred. That only happens above 60k tokens.
