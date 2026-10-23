# Faramir: Suggested Improvements for v2

Snapshot of `adleadbeater/faramir` @ `5fd3157` (2026-10-22). These are ordered by priority. Items marked **verified** were checked against the live repo and data at export time.

---

## P0: Live bug that's still open

### 1. Corpus has duplicate rows per film, which is why comp figures are still wrong (verified)

The August fix to `scrape_worldwide()` (take the max value) only touches **active** films. Comparison figures come from `data/corpus.json`, which `scripts/build_corpus.py` builds from the BOM lifetime charts, and that file is still broken:

- **180 tmdb_ids appear more than once (388 rows in total).**
- Examples from the 2026-10-01 corpus:

| Title | Row | Dom | WW |
|---|---|---|---|
| The Dark Knight | correct | $535.0M | $1.008B |
| The Dark Knight | duplicate | $0 | $127.6M ← the figure in the feedback report |
| Top Gun: Maverick | correct | $722.0M | $1.503B |
| Top Gun: Maverick | duplicates ×2 | $183.5M / $101.6M | $360.6M / $183.0M |

**Root cause:** `scrape_bom_chart()` dedupes on `imdb_id`, but some chart rows parse to the wrong IMDb ID (the reissue row for The Dark Knight comes back as `tt0113071`). TMDB enrichment then resolves the title back to the same `tmdb_id` (155), so two rows survive with different grosses. `run_daily.py` builds `corpus_by_tmdb = {f["tmdb_id"]: f ...}`, which means **the last duplicate wins, and that choice is arbitrary**. That's the block-vs-prose mismatch.

**Why the new validation gate doesn't catch it:** the bad row has `domestic_lifetime = 0`, so the `WW < Dom` check never fires. The prose check only catches the case where Claude happens to cite a bigger number.

**Fix (small):**
1. In `build_corpus.py`, after enrichment, merge rows by `tmdb_id` and keep the **max** of `domestic_lifetime` and `worldwide_lifetime`.
2. In `corpus.load_corpus()`, apply the same merge defensively and log a warning if any duplicates are found.
3. Add a test that fails if `corpus.json` has a duplicate `tmdb_id` or a row where `0 < WW < Dom`.
4. Investigate the IMDb ID parse in `_parse_imdb_id()`. BOM's WW chart links can point at `/releasegroup/` or `/title/` pages, and the regex takes the first `tt…` it sees.
5. Trigger `corpus_refresh.yml` manually after the fix. Don't wait for the 1st of the month.

---

## P1: Reliability

| # | Issue | Where | Suggestion |
|---|---|---|---|
| 2 | `write_state()` runs `clear()` then `update()`. A crash between the two wipes all state, and the next run becomes a cold start. | `sheet.py` | Write first and trim extra rows afterwards, or write to a temp tab and swap. |
| 3 | `append_suggestions()` calls `append_row` once per pick. That's N API calls and risks the Sheets quota. | `sheet.py` | Use one `append_rows()` call (same pattern as the usage flush). |
| 4 | `write_suggestions()` rewrites the entire suggestions tab every day just to add feedback. The cost grows forever. | `run_daily.py` Step 1b | Update only yesterday's rows by cell range, or move feedback to its own append-only tab. |
| 5 | Claude output is parsed by brace-walking free text. | `claude_select.py` | Use the API's structured outputs (JSON schema) so the response is guaranteed valid. That removes `_parse()` and most of the retries. |
| 6 | No timeout or explicit retry config on the Anthropic client. | `claude_select.py` | `anthropic.Anthropic(timeout=..., max_retries=...)`. |
| 7 | Scrapers fail quietly when HTML changes. They return `[]` or partial data. | `bom_fetch.py`, `build_corpus.py` | Add canary checks: a daily chart with fewer than ~5 rows, or a corpus with fewer than ~1,500 films or that shrinks by more than 5% from the prior build, should alert Slack and refuse to commit. |
| 8 | The corpus refresh commits straight to `main` with no checks. | `corpus_refresh.yml` | Run the integrity tests from #1 before `git commit`, so a bad build never lands. |
| 9 | Test coverage is only `matcher` and `milestone`. | `tests/` | Add tests for `_validate_pick_figures`, Slack template routing (`kind` → block), stale or decrease guards, and the corpus integrity check. Run `pytest` in CI before the daily job. |
| 10 | `sys.path.insert` hack, loose `anthropic>=0.40.0` pin, and the code targets 3.9 while CI runs 3.11. | repo | Add a `pyproject.toml`, pin every dependency, and standardise on 3.11. |
| 11 | The cron drifts an hour with DST. | `daily.yml` | Either accept it and document it, or run hourly and gate inside the script on the ET hour. |
| 12 | There's no dry-run mode or kill switch. | `run_daily.py` | `FARAMIR_DRY_RUN=1` would log picks without posting to Slack or writing to Sheets. A GitHub repo variable `FARAMIR_ENABLED` checked at the top of the run would act as the kill switch. |
| 13 | There's no per-run summary. | `sheet.py` | Add a `runs` tab with one row per run: date, BOM date used, active films, candidates, picks, dropped picks and the reason, stale films, and the usage total. That makes a bad day diagnosable without digging through Actions logs. |

---

## P2: Editorial quality

| # | Idea | Why |
|---|---|---|
| 14 | **Feed the feedback back in.** Thumbs and thread replies are already collected into the sheet but never used in selection. | Pass the last ~30 days of 👍/👎 by `angle_category` and active-film tier into the prompt, or turn them into scoring weights. The system can then learn which categories land. |
| 15 | **Join to MW published performance** (the data you offered to pull). | This tells you which picks became articles and which converted. It's the real ground truth for the tier and category weighting. |
| 16 | **Make the inflation check deterministic.** Right now it's Claude's "mental check". | Add a small CPI table (`data/cpi.json`), compute the adjusted comp gross, and pass it in the payload. Drop the pick in code if the adjusted comp is more than X× the active film's gross. The rule is then auditable and consistent. |
| 17 | **Enforce headline variety in code**, not only in the prompt. | After selection, compare the first ~4 words of same-day headlines. If two share a construction, run one cheap re-generation call for that pick. |
| 18 | **Graded signal instead of a binary `is_signal`.** | Pass `sessions_180d` and article counts so Claude can tell a Tier 1 film from a Tier 2 one, instead of a yes/no flag. |
| 19 | **International-weighted films** (Ne Zha 2-style). | BOM's WW for China-heavy titles lags and gets revised. Flag `intl_share > 80%` so the writer double-checks the figure. |
| 20 | **Model choice.** | Selection runs on `claude-sonnet-4-6`. Before a v2 build, benchmark the current Sonnet and Haiku generations on a week of saved payloads. The usage tab now gives you cost per run to compare. |

---

## Security notes (for the v2 build)

- **No secrets in the repo.** I scanned the export for Anthropic, Slack and Google key patterns and found none. `.env.example` contains names only.
- **Google service account:** keep it shared on the Faramir and Cerebro sheets only, never Drive-wide.
- **Slack bot token:** it only needs `chat:write`, `reactions:read` and `channels:history` (or `groups:history` for a private channel). Remove any other scopes.
- **Corpus auto-commit:** the refresh workflow has `contents: write`. Branch protection on `main`, with an exception only for the Actions bot, limits the damage if the workflow is ever compromised.
- **Data sent externally:** box-office figures and film metadata go to Anthropic. No PII and no MW internal data, apart from Cerebro-derived signal flags.
