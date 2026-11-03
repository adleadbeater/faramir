#!/usr/bin/env bash
# Starts the "Faramir Daily" workflow from outside GitHub's scheduler (which drops most scheduled triggers).
# Run from cron on an always-on machine. Needs GH_TOKEN (Actions: read+write on the repo) in the environment or
# in ~/.faramir-dispatch.env. Safe to run often: it does nothing if a run already started today (ET) and did not
# fail, and the workflow itself refuses to post twice (delivered marker + one-run-at-a-time group).
#   10:15 ET first try; later tries (12:15, 14:15 ET) only act if every earlier run failed or never started.
# Cron (the VM is UTC; ET shifts with DST, so list both and let the hour check below pick the right one):
#   15 14,15,16,18,19 * * * /home/rob/faramir-dispatch/dispatch_daily.sh >> /home/rob/faramir-dispatch/dispatch.log 2>&1
set -euo pipefail
REPO="${FARAMIR_REPO:-adleadbeater/faramir}"
[ -f "$HOME/.faramir-dispatch.env" ] && . "$HOME/.faramir-dispatch.env"
: "${GH_TOKEN:?GH_TOKEN not set}"
api() { curl -sS -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" "$@"; }

hour_et=$(TZ=America/New_York date +%H)
case "$hour_et" in 10|12|14) ;; *) exit 0 ;; esac       # only the intended ET slots
day_et=$(TZ=America/New_York date +%F)

runs=$(api "https://api.github.com/repos/$REPO/actions/workflows/daily.yml/runs?per_page=30")
started=$(echo "$runs" | DAY="$day_et" python3 -c '
import sys, json, os
from datetime import datetime
from zoneinfo import ZoneInfo
et = ZoneInfo("America/New_York"); day = os.environ["DAY"]; ok = 0
for r in json.load(sys.stdin).get("workflow_runs", []):
    t = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).astimezone(et)
    if t.strftime("%Y-%m-%d") == day and r["conclusion"] in (None, "success"):
        ok += 1
print(ok)')
if [ "$started" -gt 0 ]; then echo "$(date -u +%FT%TZ) a run already exists for $day_et; nothing to do"; exit 0; fi
code=$(api -o /dev/null -w '%{http_code}' -X POST "https://api.github.com/repos/$REPO/actions/workflows/daily.yml/dispatches" \
       -d '{"ref":"main","inputs":{"dry_run":"false"}}')
echo "$(date -u +%FT%TZ) dispatch -> HTTP $code (204 = started)"
[ "$code" = "204" ]
