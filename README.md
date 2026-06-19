# Faramir — Box Office Comparison Agent

Faramir is a daily editorial agent for MovieWeb. Every morning it scans films currently in theatrical release, finds editorially meaningful comparisons against historical titles they've just passed (or are about to pass) on domestic or worldwide totals, and posts 2–6 picks to Slack — each with a draft headline and angle.

## How it works

1. Pulls yesterday's BOM domestic chart via `boxoffice-api`
2. Scrapes per-film worldwide totals from BOM title pages
3. Compares each active film's current gross against a cached corpus of ~3,000 all-time top grossers
4. Scores candidate pairs on editorial richness (franchise sibling, same director/cast, genre milestone, unexpected giant-killer, round-number milestone)
5. Sends the top candidates to Claude, which selects the picks and writes headlines + angles
6. Posts results to Slack; logs picks and updates state in Google Sheets

## Setup

### 1. Secrets (GitHub → Settings → Secrets and variables → Actions)

| Secret | Description |
|---|---|
| `TMDB_API_KEY` | TMDB API v3 key (free at themoviedb.org) |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook URL |
| `GOOGLE_SA_JSON` | Full contents of a Google service account JSON key file |
| `FARAMIR_SHEET_ID` | ID of the Google Sheet Faramir owns (state, suggestions, corpus_meta tabs) |
| `CEREBRO_MW_SHEET_ID` | ID of the MovieWeb Cerebro article-analysis sheet (read-only, DB tab) |

### 2. Google Sheet setup

Create a Google Sheet and share it (Editor) with the service account email from your JSON key. Add three tabs named exactly: `state`, `suggestions`, `corpus_meta`. Leave them empty — Faramir will write the headers on first run.

### 3. First run order

**Always run corpus refresh before the first daily run.**

1. Go to Actions → **Faramir Corpus Refresh** → Run workflow. Wait ~20 min. Confirm `data/corpus.json` is committed to the repo with ~3,000 entries.

2. Go to Actions → **Faramir Daily** → Run workflow. This is the cold-start run — it seeds the `state` tab with currently active films and posts an "initialized" message to Slack. No picks are generated yet.

3. The next day (or trigger manually again after 24h), run **Faramir Daily** again. This produces the first real picks.

4. Run it a third time the same day to verify suppression is working — you should see no repeated pairs.

### 4. Local development

```bash
cp .env.example .env
# fill in your keys

pip install -r requirements.txt

# seed corpus locally first
python scripts/build_corpus.py

# then run the daily pipeline
python scripts/run_daily.py
```

### 5. Title mismatch fixes

If BOM and TMDB disagree on a title, find the row in the `state` tab and fill in the correct TMDB id in the `tmdb_id_override` column. The next run will use that id.

## File map

```
src/faramir/
  config.py        — all constants
  tmdb.py          — TMDB API client
  bom_fetch.py     — BOM daily list + worldwide scraper
  corpus.py        — load and search corpus.json
  matcher.py       — candidate pair generation and scoring
  milestone.py     — round-number milestone detection
  cerebro.py       — MovieWeb Cerebro signal lookups
  sheet.py         — Google Sheets helpers
  claude_select.py — Anthropic API call + prompt
  slack_post.py    — Slack Block Kit formatter

scripts/
  run_daily.py     — daily entrypoint (11-step pipeline)
  build_corpus.py  — monthly corpus refresh

data/
  corpus.json              — cached all-time top grossers (refreshed monthly)
  best_picture_winners.json — Oscar BP winners 1990–2024 (hand-maintained)
```

## Picks schedule

| Day | Target picks |
|---|---|
| Monday | 6 (weekend post-mortem) |
| Tuesday–Friday | 2–4 (Claude decides based on slate quality) |
| Saturday–Sunday | 2 |

Max 2 picks per active film per run. Pairs are suppressed for 5 days after being picked.
