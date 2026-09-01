#!/bin/zsh
# Daily automatic ingest for the SVT/TU 2026 audit.
# Runs after the 11:45 snapshot: tops up yesterday (late-evening articles)
# and captures today's articles so far, then pushes to the Google Sheet.

PY="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
BASE="/Users/wilkokramer/svt-tu-2026"
LOG="$BASE/logs/ingest.log"

ts() { date "+%Y-%m-%d %H:%M:%S"; }

# useful runs: Sep 1-7 capture + Sep 8 final top-up of Sep 7
if [ "$(date +%Y%m%d)" -gt 20260908 ]; then
  echo "$(ts) period over — this LaunchAgent can be removed" >> "$LOG"
  exit 0
fi

# skip if a successful ingest ran <3h ago (RunAtLoad fires on every login); FORCE=1 overrides
STAMP="$BASE/.last_ingest"
if [ -z "$FORCE" ] && [ -n "$(find "$STAMP" -mmin -180 2>/dev/null)" ]; then
  echo "$(ts) skip: recent ingest" >> "$LOG"
  exit 0
fi

today=$(date +%Y-%m-%d)
yday=$(date -v-1d +%Y-%m-%d)
dates=""
for d in "$yday" "$today"; do
  case "$d" in
    2026-09-0[1-7]) dates="$dates $d" ;;
  esac
done
[ -z "$dates" ] && { echo "$(ts) no in-window dates" >> "$LOG"; exit 0; }

echo "$(ts) ingest start:$dates" >> "$LOG"
"$PY" "$BASE/bin/svt_ingest.py" --dates ${=dates} --refetch-updated --push >> "$LOG" 2>&1
rc=$?
[ $rc -eq 0 ] && touch "$STAMP"
echo "$(ts) ingest done rc=$rc" >> "$LOG"
exit $rc
