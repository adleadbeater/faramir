# Faramir 2.0 Phase 0: decisions needed before production

Each entry gives the decision, my recommendation, the consequences, and whether it **blocks deployment**. "Blocks" means S7 (activation) must wait for it unless noted. References: PR-n in `PRODUCTION_REQUIREMENTS.md`, V-n in `PRODUCTION_VALIDATION_PLAN.md`.

## Priority decisions

### D1. Data-source permissions (The Numbers, Box Office Mojo): blocks deployment

- **Decision:** may v2 continue the requests production already makes every day? Those are one TN daily chart, BOM title pages for active films' worldwide grosses, and a Sunday TN chart on Mondays. v2 adds no new collection, and weekend history, backfill and corpus rebuilds stay off.
- **Options:**
  - (a) continue the status quo under an explicit, documented sign-off from Valnet (legal or partnerships);
  - (b) obtain a data licence (TN offers commercial data) and switch later;
  - (c) deploy v2 with the BOM worldwide scrape turned off.
- **Recommendation:** **(a) now, pursue (b) in parallel.** v2 doesn't change Faramir's footprint, and its safeguards make the data it uses more accurate. The question is the same one production already carries today, and it should be answered by the company, not by default. Do not choose (c) in Phase 0.
- **Consequences:**
  - (a) Deployment can proceed; the existing risk is unchanged but now acknowledged.
  - (b) Weeks of lead time; it unlocks history collection and Discovery later.
  - (c) Needs a code change (out of Phase 0 scope). Worldwide comparisons and milestones would stop updating, so a large share of picks disappears. BOM's robots.txt disallows scraping, which is why this matters.
- **Blocks:** yes. Without (a) or (b), v2 should not be activated, because activation continues the scraping.

### D2. Late theatrical releases (PR-14): does not block

- **Decision:** should an older film in its *first* US run (e.g. a 2024 French film released in the US in August 2026) be eligible for picks? Today the slug-year rule excludes it together with genuine re-releases.
- **Options:**
  - (a) keep excluding;
  - (b) include films labelled `late_release`;
  - (c) include them, but only with a manual identity override.
- **Recommendation:** **(a) for Phase 0 activation, then (b) in Phase 1** once V1/V2 show how identity resolution behaves on them.
- **Consequences:**
  - (a) No change from the tested behavior. A rare $20M+ late release is skipped and noted in the run report.
  - (b) Small code change, plus tests, plus a review. It relies on TMDB's US release date to pass the identity window; unmatched films are still skipped, never guessed.
  - (c) Adds editor workload.
- **Blocks:** no. This is an editorial-coverage gap, not a correctness risk.

### D3. Protecting the suggestions tab (PR-6): blocks deployment (low effort)

- **Decision:** should the `suggestions` tab (and `state`, `state_meta`, `state_lkg`, `faramir_meta` and the history tabs) be restricted to the service account and the owners?
- **Options:**
  - (a) protected ranges with editing limited to the service account and named owners. Editors keep read access, and keep editing `state` columns N–O (`tmdb_id_override`, `notes`) and the `identity_overrides` tab.
  - (b) a warning-only protection;
  - (c) no protection.
- **Recommendation:** **(a).**
- **Consequences:**
  - (a) Removes the risk of manual sorting or row edits mid-run. v2 already tolerates this through id-based matching and refuses on ambiguity, but a refusal stops that day's feedback or delivery work. Editors lose the ability to annotate suggestion rows directly; feedback stays in Slack reactions and threads, as designed.
  - (b) Prevents accidents only.
  - (c) Leaves an avoidable failure mode.
- **Blocks:** yes, but it is a five-minute Sheets UI step at S5, after the migration.

### D4. Artifact retention (PR-18): blocks S6

- **Decision:** run artifacts (report, recorded writes, Slack preview, state snapshot) contain pick text, box-office figures and sheet cell values, but no credentials. How long should they be kept, and is the repository's visibility acceptable?
- **Options:**
  - (a) 90 days (current setting) in a private repository;
  - (b) 30 days;
  - (c) no artifacts.
- **Recommendation:** **(a) if the repo is private; (b) if it is public or shared widely.** First confirm the visibility of `adleadbeater/faramir`.
- **Consequences:**
  - (a) Full audit trail for recovery: `restore_state.py --from-file` uses the snapshot.
  - (b) A shorter recovery window.
  - (c) Loses the post-run audit and the state snapshot recovery path. Not advised.
- **Blocks:** S6 (the first live dry run creates the first artifact).

### D5. Compatibility with the old production version: blocks deployment (process, not code)

- **Decision:** accept the strict ordering, and the one-way nature of the data migration.
- **The facts** (verified in v1's code at `f2a0ce4`):
  - v1's `write_state` and `write_suggestions` **clear and rewrite** their tabs with v1 headers. Run against a migrated workbook, v1 drops the three state identity columns on its first run, and drops the suggestion ids and delivery states the first time its feedback path runs.
  - v1 ignores `faramir_meta`, `state_meta`, `state_lkg` and the history tabs.
  - v2 refuses an unmigrated workbook (preflight), and refuses the v1 corpus (ABORT).
- **Recommendation:** follow §6 of the readiness report exactly:
  - merge only after a v1 run;
  - the merged workflows are no-ops until `FARAMIR_ENABLED=true`;
  - migrate only after v1 has stopped running;
  - activate only after the S6 dry run.
  - For rollback: export the v2 suggestion columns **before** v1 runs again (rollback step 2), and rehearse it in V7.
- **Consequences:** a pause in picks from S4 to S7, targeted at under one day by doing S5 and S6 on the merge day. Rolling back to v1 loses the v2 columns unless they were exported first; picks, Slack timestamps and feedback always survive.
- **Blocks:** yes, until V3 and V7 are rehearsed on a copy.

### D6. The 2026-11-01 corpus refresh on `main` (PR-19 / PR-5 / R2): urgent, and not tied to v2

- **Decision:** disable the `Faramir Corpus Refresh` workflow on `main` before 2026-11-01 09:00 UTC?
- **Recommendation:** **Yes.** In GitHub, go to Actions → Faramir Corpus Refresh → ⋯ → Disable workflow. It is reversible and touches no code.
- **Consequences:**
  - Disabled: the corpus stays as it is (with v1's known identity errors) until v2 ships its repaired corpus.
  - Not disabled: the legacy rebuild re-scrapes BOM and re-matches by title only, regenerating the wrong-film errors. It also makes a large set of new BOM requests, which weighs on D1.
- **Blocks:** not v2 deployment, but it is the only item with a hard date.

## Other production requirements

| Ref | Decision needed | Recommendation | Consequences | Blocks? |
|---|---|---|---|---|
| PR-1 | Accept the concurrency design; verify it on GitHub | Accept; run the V6 overlap test | Two runs can never write together; a stuck run's late write is refused | Yes, until V6 |
| PR-2 | Rollback keeps backups | Done in code; confirm in V7 | Nothing is deleted automatically | Yes, until V7 |
| PR-3 / PR-10b | Approve the read-only Slack check | Approve V4 | Confirms delivery and feedback will work, and that it is the right channel | Yes |
| PR-4 | Confirm the `anthropic==2.1.0` pin | Check the last production Actions log; if it differs, run V5 on the pinned version anyway (the pin is what v2 ships) | A version mismatch is harmless for v2, since it pins its own version; this is documentation | No (V5 covers the behavior) |
| PR-7 / PR-10a / PR-13 | Approve the migration rehearsal on a copy, then production | Approve V3 now; production at S5 | Workbook on schema v2; editor values preserved | Yes |
| PR-8 | Approve the timestamp probe on the copy | Approve with V4 | Confirms new Slack timestamps are stored losslessly; counts legacy rows already damaged | Yes |
| PR-9 | — | Done | Ambiguous deliveries are never auto-retried | No |
| PR-11 | Approve one Anthropic call | Approve V5 | Confirms the structured-output schema is accepted | Yes |
| PR-12 | Approve the export plus the read-only replay tool | Approve V1 | Measures the validator's real rejection rate and false positives | Yes |
| E4 | Approve 5 days of live dry runs plus the read-only comparison tool | Approve V2 | Shows the editorial impact before activation | Yes |
| PR-15 | Accept the legacy Monday merge as daily data | Accept for Phase 0; replace it when history collection is approved | No weekend data is ever claimed | No |
| PR-16 | Keep weekend history off | Keep it off until D1 (b) or an explicit permission | No new collection | No |
| PR-17 | Approve creating the three variables as `false` | Approve at S3 | Workflows stay no-ops until you enable them | Yes (S3) |
| B3 | Your hand check of 20 corrected identities | Do it before S4 (the audit CSV lists all 322) | Independent confirmation of the corpus repair | Yes |
| Review | Independent code review of the branch | In progress (this package) | — | Yes |

## Summary

- **Blocking decisions:** D1 data source, D3 tab protection, D4 artifact retention (S6), D5 compatibility process, plus the approvals for V1–V7.
- **Non-blocking:** D2 late releases (keep excluding for now), PR-4, PR-9, PR-15, PR-16.
- **Urgent:** D6, before 2026-11-01.
