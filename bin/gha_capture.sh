#!/bin/bash
# GitHub Actions capture run (Linux): snapshot SVT sitemaps + RSS, then ingest
# yesterday + today (Europe/Stockholm) and push to the Google Sheet.
set -euo pipefail

BASE="${SVT_BASE:?SVT_BASE not set}"
SNAPDIR="$BASE/snapshots"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

export TZ=Europe/Stockholm
today=$(date +%Y-%m-%d)
yday=$(date -d yesterday +%Y-%m-%d)

# useful runs: Sep 1-7 capture + Sep 8 final top-up of Sep 7
if [ "$(date +%Y%m%d)" -gt 20260908 ]; then
  echo "period over (>2026-09-08), nothing to do"
  exit 0
fi

# ---------- snapshots ----------
stamp=$(date +%y%m%d_%H%M)
for kind in articles videos; do
  out="$SNAPDIR/${kind}_sitemap_${stamp}.xml"
  curl -sS --max-time 60 --retry 3 --retry-delay 10 -A "$UA" \
    "https://www.svt.se/latest-${kind}-sitemap.xml" -o "$out"
  if [ -s "$out" ] && grep -q "<loc>" "$out"; then
    echo "ok: ${kind} $(grep -c '<loc>' "$out") entries"
  else
    echo "ERROR: ${kind} snapshot failed"; rm -f "$out"
    [ "$kind" = "articles" ] && exit 1
  fi
done

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
  slug=${p//\//-}
  out="$SNAPDIR/rss_${slug}_${stamp}.xml"
  if curl -sS --max-time 30 --retry 2 --retry-delay 5 -A "$UA" \
      "https://www.svt.se/${p}/rss.xml" -o "$out" && grep -q "<item>" "$out"; then
    rss_ok=$((rss_ok+1))
  else
    rm -f "$out"
  fi
done
echo "ok: rss ${rss_ok}/${#RSS_PATHS[@]} feeds"

# ---------- ingest ----------
dates=""
for d in "$yday" "$today"; do
  case "$d" in
    2026-09-0[1-7]) dates="$dates $d" ;;
  esac
done
if [ -z "$dates" ]; then
  echo "no in-window dates"; exit 0
fi
echo "ingest:$dates"
# shellcheck disable=SC2086
python3 "$BASE/bin/svt_ingest.py" --dates $dates --refetch-updated --push
