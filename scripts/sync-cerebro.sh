#!/bin/bash
# Refresh Faramir's Cerebro data from Alex's daily export. Run it from Terminal on Rob's Mac:
#   bash ~/faramir/scripts/sync-cerebro.sh
#
# It must be run by hand from Terminal: macOS blocks scheduled (launchd) jobs from reading the Google
# Drive folder, and only Rob's Google account can see the export. Aragorn and arwen work the same way.
# The export is rebuilt around 3:45am ET, so any time after that picks up the new day.
cd "$(dirname "$0")/.." || exit 1
exec .venv/bin/python scripts/sync_cerebro.py
