#!/bin/zsh
# Daily snapshot of SVT news/video sitemaps for the TU 2026 audit (Sep 1-7).
# The sitemaps are a rolling ~48h window, so one snapshot per day is enough;
# this runs twice daily + at login as a safety net, and skips if a snapshot
# is less than 5h old (bypass with FORCE=1).

SNAPDIR="/Users/wilkokramer/svt-tu-2026/snapshots"
LOG="/Users/wilkokramer/svt-tu-2026/logs/snapshot.log"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

ts() { date "+%Y-%m-%d %H:%M:%S"; }
log() { echo "$(ts) $1" >> "$LOG"; }

# Collection period ends Sep 7; the 48h window means Sep 9 is the last useful run.
if [ "$(date +%Y%m%d)" -gt 20260909 ]; then
  log "period over (>2026-09-09), nothing to do — this LaunchAgent can be removed"
  exit 0
fi

# Skip if the newest snapshot is younger than 5 hours (unless forced)
if [ -z "$FORCE" ]; then
  recent=$(find "$SNAPDIR" -name 'articles_sitemap_*.xml' -mmin -300 2>/dev/null | head -1)
  if [ -n "$recent" ]; then
    log "skip: recent snapshot exists ($(basename "$recent"))"
    exit 0
  fi
fi

stamp=$(date +%y%m%d_%H%M)
ok=1
for kind in articles videos; do
  out="$SNAPDIR/${kind}_sitemap_${stamp}.xml"
  curl -sS --max-time 60 --retry 3 --retry-delay 10 -A "$UA" \
    "https://www.svt.se/latest-${kind}-sitemap.xml" -o "$out" 2>> "$LOG"
  if [ -s "$out" ] && grep -q "<loc>" "$out"; then
    log "ok: ${kind} $(grep -c '<loc>' "$out") entries -> $(basename "$out")"
  else
    log "ERROR: ${kind} snapshot failed or empty ($out)"
    rm -f "$out"
    ok=0
  fi
done

# RSS feeds: independent second source for the completeness cross-check.
# Small rolling windows (20-100 items), so grab them on every run.
RSS_PATHS=(
  nyheter/inrikes nyheter/utrikes kultur sport vader
  nyheter/nyhetstecken nyheter/sapmi nyheter/uutiset
  nyheter/ekonomi nyheter/vetenskap nyheter/granskning
  nyheter/lokalt/blekinge nyheter/lokalt/dalarna nyheter/lokalt/gavleborg
  nyheter/lokalt/halland nyheter/lokalt/helsingborg nyheter/lokalt/jamtland
  nyheter/lokalt/jonkoping nyheter/lokalt/norrbotten nyheter/lokalt/skane
  nyheter/lokalt/smaland nyheter/lokalt/stockholm nyheter/lokalt/sodertalje
  nyheter/lokalt/sormland nyheter/lokalt/uppsala nyheter/lokalt/varmland
  nyheter/lokalt/vast nyheter/lokalt/vasterbotten nyheter/lokalt/vasternorrland
  nyheter/lokalt/vastmanland nyheter/lokalt/orebro nyheter/lokalt/ost
)
rss_ok=0
for p in "${RSS_PATHS[@]}"; do
  slug=$(echo "$p" | tr '/' '-')
  out="$SNAPDIR/rss_${slug}_${stamp}.xml"
  curl -sS --max-time 30 --retry 2 --retry-delay 5 -A "$UA" \
    "https://www.svt.se/${p}/rss.xml" -o "$out" 2>> "$LOG"
  if [ -s "$out" ] && grep -q "<item>" "$out"; then
    rss_ok=$((rss_ok+1))
  else
    log "warn: rss ${p} empty/failed"
    rm -f "$out"
  fi
done
log "ok: rss ${rss_ok}/${#RSS_PATHS[@]} feeds"

[ $ok -eq 1 ] || exit 1
