#!/bin/bash
# Faramir's daily run: today's cards with titles, once a day.
#
# Scheduled on Rob's Mac by launchd (scripts/launchd/com.movieweb.faramir.daily.plist) at 10am local
# time; the same script is meant to run unchanged on the Shell Agent VM later (cron/systemd timer).
#
# - Waits for The Numbers to post yesterday's daily chart: up to 8 tries, 30 minutes apart (until 1:30pm).
# - Runs once per day: a finished day is marked, so a second trigger (e.g. the Mac waking) does nothing.
# - Logs to out/logs/daily-YYYY-MM-DD.log and posts a macOS notification when the cards are ready.
# - Never posts to Slack or writes Google Sheets (faramir_run.py captures Slack; local store only).

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1
TODAY="$(date +%F)"
LOG_DIR="$REPO/out/logs"
CARDS_DIR="$REPO/out/cards/$TODAY"
DONE="$CARDS_DIR/.done"
LOCK="$REPO/out/.daily_run.lock"
TRIES="${FARAMIR_DAILY_TRIES:-8}"
WAIT="${FARAMIR_DAILY_WAIT_SECONDS:-1800}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/daily-$TODAY.log"

notify() {
    if command -v osascript >/dev/null 2>&1; then
        osascript -e "display notification \"$1\" with title \"Faramir\"" >/dev/null 2>&1 || true
    fi
}

if [ -f "$DONE" ]; then
    echo "$(date '+%F %T') already done today" >>"$LOG"
    exit 0
fi
if ! mkdir "$LOCK" 2>/dev/null; then
    echo "$(date '+%F %T') another run is in progress" >>"$LOG"
    exit 0
fi
trap 'rmdir "$LOCK"' EXIT

for attempt in $(seq 1 "$TRIES"); do
    echo "$(date '+%F %T') attempt $attempt" >>"$LOG"
    "$REPO/.venv/bin/python" scripts/faramir_run.py --titles --require-chart >>"$LOG" 2>&1
    code=$?
    if [ "$code" -eq 0 ]; then
        mkdir -p "$CARDS_DIR" && touch "$DONE"
        cards=$(grep -c '^\*\*[0-9]' "$CARDS_DIR/cards.md" 2>/dev/null || echo 0)
        cost=$(grep -o '≈ \$[0-9.]*' "$LOG" | tail -1)
        msg="$cards story cards ready for $TODAY ${cost:+($cost)} — out/cards/$TODAY/cards.md"
        cerebro="$REPO/out/local_store/cerebro_MW.csv"
        if [ -f "$cerebro" ] && [ -n "$(find "$cerebro" -mtime +3 2>/dev/null)" ]; then
            msg="$msg · Cerebro data is over 3 days old: run sync-cerebro.sh in Terminal"
        fi
        notify "$msg"
        echo "$(date '+%F %T') done: $cards cards" >>"$LOG"
        exit 0
    elif [ "$code" -eq 75 ] && [ "$attempt" -lt "$TRIES" ]; then
        sleep "$WAIT"
    else
        break
    fi
done
notify "No cards today: the run stopped (exit $code). See out/logs/daily-$TODAY.log"
echo "$(date '+%F %T') gave up (exit $code)" >>"$LOG"
exit 1
