# Faramir 2.0 Phase 0: production validation plan

Status: **plan only. None of these checks has been run.** Each check marked 🔐 needs your separate authorization before it is executed. The plan is written against branch `v2/phase-0` @ `adf2287` (local only).

How access is classified:

- **Offline:** local machine, fixtures or in-memory fakes; no credentials, no network. Can run any time.
- **External, read-only:** reads a production system (Sheets, Slack, TMDB, Anthropic, GitHub) and writes nothing there.
- **External, writes:** writes to a copy or to production. **Always** 🔐.

Every command runs from the repository root with the project environment (`.venv/bin/python`, Python 3.11). Credentials come from the shell environment and are never written to files. Every run artifact lands under `out/<run_id>/`, which is git-ignored.

**Ordering.** V1 is offline. V2 and V3 need a read-only export. V4 and V5 are read-only live checks. V6 is a sheet copy. V7 runs after push/PR approval. V8 is the rollback rehearsal on the copy. V9 follows activation.

| # | Check | Access | 🔐 | Blocks |
|---|---|---|---|---|
| V0 | Offline baseline (suite, dry run, migration, rollback text) | Offline | — | S2 |
| V1 | Historical validator replay | External read-only (export) + offline analysis | 🔐 read access to past suggestions | S6 |
| V2 | Shadow comparison, at least 5 reporting days | External read-only | 🔐 | S7 |
| V3 | Migration rehearsal on a copy of the real Sheet | External, writes to a **copy** | 🔐 | S5 |
| V4 | Slack permissions, channel identity, timestamp preservation | External read-only (+ one write to the copy) | 🔐 | S5 |
| V5 | Controlled Anthropic structured-output test | External (one paid API call) | 🔐 | S6 |
| V6 | GitHub Actions dry run and overlapping-run test | External (push, dispatch) | 🔐 | S7 |
| V7 | Rollback procedure verification | External, writes to the **copy** | 🔐 | S7 |
| V8 | First three production runs | Production | 🔐 activation | S8 |

---

## V0. Offline baseline (no authorization needed)

```bash
.venv/bin/python -m pytest -q
```
**Expected:** `694 passed, 2 skipped`.

```bash
FARAMIR_TN_CAPTURE_DIR=<dir with the two audit captures> .venv/bin/python -m pytest -q --run-local-capture tests/test_tn_chart.py -k captured
```
**Expected:** `2 passed`.

```bash
env -i HOME=$HOME PATH=/usr/bin:/bin .venv/bin/python scripts/run_daily.py --dry-run --fixtures fixtures/offline/2026-10-22 --out-root out/v0
```
**Expected:**
- exit 0;
- `report.json` shows `preflight.ok = true`, `state.decision = PROCEED`, `candidates = 17`, `picks.after_validation = 1`;
- `write_outcomes = {"recorded": 14}`;
- no line in the log contains `ERROR`.

```bash
.venv/bin/python scripts/migrate_sheets.py --rollback-plan
```
**Expected:** the 10-line rollback procedure (§7 of the readiness report), and nothing else.

---

## V1. Historical validator replay (PR-12, acceptance item E4, part 1)

**Purpose:** count how many real past suggestions the Increment 8–9 validator would accept or reject, categorize the reasons, and find legitimate headlines it filters wrongly.

**Access:** 🔐 a read-only export of the production `suggestions` tab (File → Download → CSV, done by you or with your approval), plus the committed corpus. The analysis itself is offline.

**Tool:** a read-only review tool, `scripts/replay_validator.py`, which **does not exist yet**.

- You asked for no new code in this step, so it is specified here and built only when you authorize V1.
- It writes only to `out/`.
- It never imports the Sheets or Slack writers. Its inputs are the CSV and `data/corpus.v2.json`.

**Two tiers of the replay.** Past rows carry no evidence snapshot, because rows before Increment 7 never stored one:

| Tier | Checks | Fidelity |
|---|---|---|
| A: wording | lexical superlatives/rankings, tense against the row's `kind`, direction, genre attribution (genres from the corpus for the comparison and TMDB for the active film), third-film names (corpus lexicon), 85-character length | Exact, the same functions as production |
| B: figures and claims | dollar figures against the comparison film's *reported* corpus grosses and the threshold. The active film's gross on that date is unknown, so a figure that only matches the active film is labelled `unverifiable`, not rejected. | Partial; labelled |

**Command, once built:**
```bash
.venv/bin/python scripts/replay_validator.py --suggestions suggestions_export.csv --corpus data/corpus.v2.json --out out/v1
```

**Expected output:**
- `out/v1/summary.md`: total rows, accepted and rejected per tier, and the rejection reasons by category;
- `out/v1/rejections.csv`: one row per rejected headline and reason;
- `out/v1/review_sample.csv`: 50 rejected headlines sampled across all reason categories, each with a column for your verdict (`correct_rejection` / `false_positive`).

**Pass criteria (for your review, not automatic):**
- Every false positive you mark becomes a regression test in `LEGITIMATE` (SR-2). The rule is fixed only after your approval.
- The share of headlines rejected by Tier A purely for wording you consider legitimate is ≤ 5% [CAL] of rows.

---

## V2. Shadow comparison, at least 5 reporting days (acceptance item E4, part 2)

**Purpose:** see what v2 would have posted on real days next to what v1 actually posted, with every drop listed and its reason.

**Recommended method: five consecutive live dry runs.**
- **Access:** 🔐 production read credentials (Sheets read, TMDB, Anthropic, Slack history read, the TN daily chart Faramir already fetches) on five consecutive weekdays, including one Monday. No writes.
- **Why this method:** it adds no requests beyond what production already makes daily (one TN daily chart, the same BOM title pages), and it needs no historical backfill.

Run once per day, after v1's 10:00 ET run, for 5 days:
```bash
FARAMIR_DRY_RUN=true .venv/bin/python scripts/run_daily.py --dry-run --out-root out/v2
```
This runs locally with read credentials, or as the V6 GitHub dispatch.

**Expected for each day:**
- exit 0, and `would_write.json` lists only `recorded` outcomes;
- `report.json → validation` lists every pick with its reasons;
- `claude_binding.rejected` lists unbound picks;
- `identity` lists unresolved and re-release films.

**Comparison** (offline, after the 5 days): for each day, line up v1's posted picks (from the suggestions tab for that date) against v2's `slack_preview.json`. Classify every difference as:

- corrected identity;
- dropped by the validator (with its reason);
- dropped as unbound;
- unresolved active film;
- re-release or late release;
- stale comparison;
- a different selection from Claude.

The tool for this is `scripts/shadow_compare.py` (read-only). **It does not exist yet** and is built only on authorization.

**Pass criteria (your review):** every v2 drop is explained by a listed reason; you accept every class of difference; there are no unexplained errors, alerts or preflight failures.

**Alternative (not recommended):** five historical days via `--run-date`. This would need new requests for past TN daily charts and BOM pages, which counts as new collection, so it waits on the source-permission decision.

---

## V3. Migration rehearsal on a copy of the real Sheet (PR-7, PR-10a, PR-13)

**Access:** 🔐 you make a full copy of the production workbook (File → Make a copy) and share it with the service account. Writes go **only** to the copy.

```bash
export FARAMIR_SHEET_ID=<COPY_ID>                  # never the production id in this step
.venv/bin/python scripts/migrate_sheets.py > out/v3_plan.json
```
**Expected:** exit 0; `refuse = []`; `state.layout = "v1"` with the production row count; the number of editor overrides and notes; suggestion rows, Slack timestamps and feedback counts; and 8 pending steps.

**Review that the counts match the real tabs before continuing.**

```bash
.venv/bin/python scripts/migrate_sheets.py --dry-run --apply --confirm-sheet $FARAMIR_SHEET_ID --out-root out/v3_dry
```
**Expected:** exit 0; `migration.dry_run = true`; the copy is unchanged (check its version history).

```bash
FARAMIR_ENABLED=true .venv/bin/python scripts/migrate_sheets.py --apply --confirm-sheet $FARAMIR_SHEET_ID --out-root out/v3_apply
```
**Expected:** exit 0; `applied` lists the pending steps ending in `schema_marker`.

Then check the copy:
- `state` has 20 columns; columns A–Q are identical to the original; R–T are blank; `tmdb_id_override` and `notes` are unchanged.
- `suggestions` columns A–Q are unchanged (spot-check 10 Slack timestamps and the feedback cells); columns R onward hold ids and delivery states.
- `state_lkg` and `state_meta` exist. `faramir_meta` holds `schema_version 2`.
- The four new tabs have a header row only.

Run it again:
```bash
FARAMIR_ENABLED=true .venv/bin/python scripts/migrate_sheets.py --apply --confirm-sheet $FARAMIR_SHEET_ID
```
**Expected:** `{"applied": [], "already_migrated": true}`, with no change to the copy.

Preflight against the copy:
```bash
FARAMIR_DRY_RUN=true .venv/bin/python scripts/run_daily.py --dry-run --out-root out/v3_preflight
```
**Expected:** `report.preflight.ok = true`; `state.decision = PROCEED`; zero performed writes.

**Optional crash rehearsal on a second copy:** interrupt the apply (Ctrl-C during the state step). Then:
- **Expected:** preflight fails.
- Re-run the apply. **Expected:** it completes, and the content matches the first copy.

---

## V4. Slack: permissions, channel identity, timestamp preservation (PR-3, PR-8, PR-10b)

**Access:** 🔐 the production bot token, used read-only, plus one cell write to the **copy**.

```bash
SLACK_BOT_TOKEN=<prod token> SLACK_CHANNEL_ID=<prod channel> .venv/bin/python scripts/verify_slack_access.py > out/v4_slack.json
```
**Expected:**
- `chat:write`, `channels:history` (or `groups:history`) and `reactions:read` are present;
- `history_ok: true`;
- `pagination.cursor_returned` or `page2_ok` is true;
- `channel_identity.faramir_daily_headers_seen > 0`, which proves the webhook posts to this channel;
- `metadata.message:read` is optional. If absent, reconciliation uses the visible `ref <id>`.
- Nothing is posted.

**Timestamp preservation** on the V3 copy, using the RAW write path production uses:
```bash
FARAMIR_ENABLED=true FARAMIR_SHEET_ID=<COPY_ID> .venv/bin/python - <<'EOF'
import sys; sys.path.insert(0, "src")
from faramir import runtime, sheet
from faramir.suggestions import read_table
ctx = runtime.new_context(dry_run=False, out_root="out/v4_ts")
with runtime.use(ctx):
    gc = sheet.get_sheet_client(); sid = __import__("os").environ["FARAMIR_SHEET_ID"]
    # AB1 is beyond the 26 v2 columns (Z = evidence_json): an empty cell on the copy.
    sheet.update_cells(gc, sid, "suggestions", [{"range": "AB1", "values": [["1761058801.000100"]]}], "v4_ts_probe")
    print(repr(sheet.read_values(gc, sid, "suggestions")[0][27]))
EOF
```
**Expected:** `'1761058801.000100'`, with the trailing zeros intact. Afterwards, clear `AB1` on the copy by hand.

Then run `migrate_suggestions.py` in plan mode against the **production** id (read-only):
```bash
FARAMIR_SHEET_ID=<PROD_ID> .venv/bin/python scripts/migrate_suggestions.py
```
**Expected:** the `precision_damaged_slack_ts_rows` count is reported. Those legacy rows cannot be matched to feedback, and that is accepted as a known loss.

---

## V5. Controlled Anthropic structured-output test (PR-11)

**Access:** 🔐 one paid call to `claude-sonnet-4-6` with a synthetic payload (no production data).

```bash
ANTHROPIC_API_KEY=<key> .venv/bin/python - <<'EOF'
import sys; sys.path[:0] = ["src", "."]
from tests.test_selection import payload
from faramir.claude_select import select
r = select(payload())
print(r.status, r.reason, len(r.picks), [a.get("stop_reason") for a in r.attempts])
for p in r.picks: print(p["headline"], [c["type"] for c in p["claims"]])
EOF
```
**Expected:**
- `ok`, empty reason, 1–4 picks, stop reason `end_turn`;
- every pick has `claims` with one event claim;
- no API 400 (which would mean the schema is rejected) and no `refusal`.

**On failure:** the same call fails safe in production (no picks, header warning). Record the error and fix it before S6.

**Follow-up:** the first V2 live dry run is the real-payload confirmation. Its `report.claude_selection.status` should be `ok`.

---

## V6. GitHub Actions dry run and overlapping-run test (PR-1, item G1)

**Access:** 🔐 the push and PR (S2), then the variables (S3). Dispatches run **on the PR branch**, before any merge, with `FARAMIR_ENABLED` still `false`.

```bash
gh workflow run daily.yml --ref v2/phase-0 -f dry_run=true
gh run watch
```
**Expected:**
- the test step shows `694 passed, 2 skipped`;
- the run step exits 0, in dry-run mode;
- the artifact `faramir-run-<id>` contains `report.json` with `preflight.ok`.

That last point holds only if production Sheets is already migrated; before S5, the expected result is instead a **clean preflight failure** with zero writes and an alert. With the dry-run input, the alert is recorded but not sent.

**Overlap test:**
```bash
gh workflow run daily.yml --ref v2/phase-0 -f dry_run=true
gh workflow run daily.yml --ref v2/phase-0 -f dry_run=true
gh run list --workflow daily.yml --limit 2
```
**Expected:** the second run is `queued` until the first completes, and never runs in parallel. Neither is cancelled.

**Fail-closed check:**
```bash
gh workflow run daily.yml --ref v2/phase-0 -f dry_run=false
```
**Expected:** the job is **skipped**, because `FARAMIR_ENABLED` is not `true`.

**Corpus workflow:** `gh workflow run corpus_refresh.yml --ref v2/phase-0 -f gross_as_of=2026-10-01 -f dry_run=true`. **Expected:** tests pass first; the repair runs as a dry run; no commit and no push.

GitHub dispatches only workflows whose file exists on the default branch (`--ref` then runs the branch's version of it). `daily.yml` and `corpus_refresh.yml` exist on `main`. **`migrate.yml` is new, so it can be dispatched only after the merge (S4).** Rehearse the migration locally (V3) before then.

---

## V7. Rollback procedure verification (PR-2; readiness report §7)

**Access:** 🔐 the V3 copy after migration, plus a local checkout of `main` @ `f2a0ce4` (v1). v1 runs only against the **copy**, with Slack disabled: unset `SLACK_WEBHOOK_URL` and `SLACK_BOT_TOKEN`, or use a test webhook you choose.

1. **Export:** download the copy's `suggestions` tab as CSV, keeping the v2 columns (rollback step 2).
2. **Immediate rollback:** with `FARAMIR_ENABLED=false`, run a v2 live run on the copy. **Expected:** "not started", zero calls.
3. **v1 against the migrated copy:** run `git worktree add ../faramir-v1 f2a0ce4`, then from `../faramir-v1` run `FARAMIR_SHEET_ID=<COPY_ID> SLACK_WEBHOOK_URL=<a test-channel webhook you choose> python scripts/run_daily.py`. v1 has no dry run and no kill switch: it makes its normal live reads (TN, BOM, TMDB, Anthropic) and **posts to whatever webhook it is given**, so it must never see the production webhook in this step. **Expected:**
   - v1 runs;
   - `state` is rewritten with 17 headers (rows and editor values intact);
   - `suggestions` loses the v2 columns *only if* v1's feedback path runs. This confirms the warning in step 2.
4. **Forward again:** re-run V3's apply on the same copy. **Expected:** the migration completes again, and preflight passes.
5. **State restore:**
   ```bash
   FARAMIR_SHEET_ID=<COPY_ID> .venv/bin/python scripts/restore_state.py --from lkg
   ```
   This is the plan. Then run it with `--dry-run --apply --confirm-sheet <COPY_ID>`. **Expected:** the plan shows the backup generation, editor-value conflicts (none), and the checksum; the dry-run records only.

**Pass:** each step behaves as written in §7, and nothing is deleted.

---

## V8. First three production runs after activation (S8)

**Access:** 🔐 activation (`FARAMIR_ENABLED=true`).

For each of the first three scheduled runs, check:

| Check | Where | Expected |
|---|---|---|
| Run completed | Actions run | Exit 0, about 2–5 minutes, under the 15-minute timeout |
| Preflight | artifact `report.json → preflight` | `ok: true` |
| State | `report.state` and `state_meta` | `PROCEED`; generation +1; lock `released` |
| Delivery | `report.delivery.outcomes` and the Slack channel | Every pick `posted` and `confirmed`; exactly one header; no duplicates |
| Validation | `report.validation` | Drops are listed with reasons; spot-check that every dropped headline really was invalid |
| Identity | `report.identity` | Any unresolved films get an override if they matter editorially |
| Alerts | Slack alerts | None |
| Suggestions rows | Sheet | One row per pick, `delivery_state = posted` |
| Feedback (day 2+) | Sheet | Yesterday's reactions written only to their own rows |

**Stop rule:** any alert, preflight failure, duplicate post or unexplained drop means you set `FARAMIR_ENABLED=false`, diagnose, and fix forward on a branch.

---

## Summary of authorizations needed

1. V1: read access to past suggestions (an export), plus building the read-only `replay_validator.py`.
2. V2: five days of read-only live dry runs, plus building the read-only `shadow_compare.py`.
3. V3 and V7: a copy of the production workbook, with writes to the copy, and a v1 run against the copy.
4. V4: the production bot token, read-only, and one cell probe on the copy.
5. V5: one Anthropic call.
6. V6: push, PR and repository variables (S2/S3), plus dispatches on the PR branch.
7. V8: activation (S7).
