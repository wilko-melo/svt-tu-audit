# SVT TU audit 2026

Automated capture for the SVT.se content inventory, 1–7 September 2026
(commissioned by Tidningsutgivarna; same method as the 2025 measurement).

Every article published on svt.se during the window is registered with its
green-field data (publication date, tab/topic, body text, video presence,
video length, video link) in the Google Sheet **SVT TU 2026 Raw data**,
which also carries the live Analytics / Dashboard / README tabs.

## How it runs

`.github/workflows/capture.yml` runs every 4 hours:

1. **Snapshot** `latest-articles-sitemap.xml` + `latest-videos-sitemap.xml`
   (rolling ~48 h window) and 32 section RSS feeds into `snapshots/`.
2. **Ingest** (`bin/svt_ingest.py`): fetch every new article published
   yesterday/today (Europe/Stockholm), extract the green fields, resolve
   video lengths via SVT's video API, refetch articles whose `lastmod`
   advanced, and push rows to the sheet.
3. **Commit** `state.json`, `video_cache.json`, `snapshots/` and the raw
   page cache `html/` back to this repo as the audit trail.

Secrets: `GCP_SA_KEY` — service-account key for
`svt-sheets@svt-tu-audit-2026.iam.gserviceaccount.com` (Sheets scope only).

`bin/snapshot_svt.sh` and `bin/daily_ingest.sh` are the earlier local
(macOS launchd) variants of the same pipeline, kept for reference/backup.

The capture window closes 2026-09-08; after that every run exits early and
the schedule can be removed.
