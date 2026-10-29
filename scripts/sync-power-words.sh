#!/bin/bash
# Refresh Faramir's Power Words from a CSV download of the Power Words Sheet. Run from Terminal:
#   bash ~/faramir/scripts/sync-power-words.sh            (newest "...Power Words...Data Sheet....csv" in ~/Downloads)
#   bash ~/faramir/scripts/sync-power-words.sh path.csv
# The sheet is a native Google Sheet, so Drive for desktop can't hand Faramir its contents; a CSV download can.
cd "$(dirname "$0")/.." || exit 1
exec .venv/bin/python scripts/sync_power_words.py "$@"
