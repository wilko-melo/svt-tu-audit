#!/usr/bin/env python3
"""SVT.se article ingest for the TU 2026 audit (Sep 1-7).

Green fields per article: publication date, tab/topic, body text,
video yes/no, video length (s), video link.

Usage:
  python3 svt_ingest.py --dates 2026-09-01            # fetch+extract
  python3 svt_ingest.py --dates 2026-09-01 --push     # + write to Google Sheet
  python3 svt_ingest.py --dates 2026-09-01 --refetch-updated
"""
import argparse, glob, html as htmllib, json, os, re, subprocess, sys, time
from collections import OrderedDict
from datetime import datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup

BASE = os.environ.get("SVT_BASE", "/Users/wilkokramer/svt-tu-2026")
SNAPDIR = f"{BASE}/snapshots"
HTMLDIR = f"{BASE}/html"
STATE_PATH = f"{BASE}/state.json"
VIDCACHE_PATH = f"{BASE}/video_cache.json"
SHEET_ID = "1ARh4gYWLeKaXEF7ztwIJstVhhay2F5iO6DEBKmLUN9A"
SA_KEY = os.environ.get(
    "SVT_SA_KEY", "/Users/wilkokramer/.config/gcp/svt-tu-audit-2026-71700c5bf3f5.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
WORDS_PER_SEC = 2.1616  # template constant, column P

REGION = {
    "blekinge": "Blekinge", "dalarna": "Dalarna", "gavleborg": "Gävleborg",
    "halland": "Halland", "helsingborg": "Helsingborg", "jamtland": "Jämtland",
    "jonkoping": "Jönköping", "norrbotten": "Norrbotten", "skane": "Skåne",
    "smaland": "Småland", "stockholm": "Stockholm", "sodertalje": "Södertälje",
    "sormland": "Sörmland", "uppsala": "Uppsala", "varmland": "Värmland",
    "vast": "Väst", "vasterbotten": "Västerbotten",
    "vasternorrland": "Västernorrland", "vastmanland": "Västmanland",
    "orebro": "Örebro", "ost": "Öst",
}
NYHETER_SUB = {
    "inrikes": "Nyheter/Inrikes", "utrikes": "Nyheter/Utrikes",
    "ekonomi": "Nyheter/Ekonomi", "vetenskap": "Nyheter/Vetenskap",
    "granskning": "Nyheter/Granskning", "sapmi": "Nyheter/Sápmi",
    "uutiset": "Nyheter/Uutiset", "nyhetstecken": "Nyheter/Nyhetstecken",
    "svtforum": "Nyheter/SVT Forum", "video": "Nyheter/Video",
}
TOP = {"sport": "Sport", "kultur": "Kultur", "vader": "Väder",
       "nyhetskoll": "Nyhetskoll"}


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def parse_sitemaps():
    """Union of all article-sitemap snapshots -> {url: {pubdate, title, lastmod}}."""
    reg = {}
    for path in sorted(glob.glob(f"{SNAPDIR}/articles_sitemap_*.xml")):
        xml = open(path).read()
        for m in re.finditer(
            r"<url>\s*<loc>(.*?)</loc>\s*<lastmod>(.*?)</lastmod>(.*?)</url>",
            xml, re.S,
        ):
            url, lastmod, rest = m.group(1), m.group(2), m.group(3)
            pd = re.search(r"<news:publication_date>(.*?)</news:publication_date>", rest)
            ti = re.search(r"<news:title>(.*?)</news:title>", rest)
            e = reg.setdefault(url, {"pubdate": None, "title": None, "lastmod": None})
            if pd:
                e["pubdate"] = min(filter(None, [e["pubdate"], pd.group(1)]))
            e["lastmod"] = max(filter(None, [e["lastmod"], lastmod]))
            if ti:
                e["title"] = htmllib.unescape(ti.group(1))
    # RSS feeds as a second enumeration source: catches anything the sitemap
    # hasn't listed yet (it regenerates with a small lag)
    from email.utils import parsedate_to_datetime
    for path in sorted(glob.glob(f"{SNAPDIR}/rss_*.xml")):
        try:
            xml = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in re.finditer(r"<item>.*?<link>(.*?)</link>.*?<pubDate>(.*?)</pubDate>.*?</item>",
                             xml, re.S):
            url = htmllib.unescape(m.group(1).strip())
            if not url.startswith("https://www.svt.se/") or "/nyheter/video/" in url:
                continue
            try:
                pub = parsedate_to_datetime(m.group(2).strip())
            except (TypeError, ValueError):
                continue
            iso = pub.astimezone().isoformat()
            e = reg.setdefault(url, {"pubdate": None, "title": None, "lastmod": None})
            e["pubdate"] = min(filter(None, [e["pubdate"], iso]))
            e["lastmod"] = max(filter(None, [e["lastmod"], iso]))
    return reg


def tab_for_url(url):
    parts = urlparse(url).path.strip("/").split("/")
    if parts[0] == "nyheter" and len(parts) > 1:
        if parts[1] == "lokalt" and len(parts) > 2:
            return "Lokalt/" + REGION.get(parts[2], parts[2].capitalize())
        return NYHETER_SUB.get(parts[1], "Nyheter/" + parts[1].capitalize())
    return TOP.get(parts[0], parts[0].capitalize())


def curl(url, timeout=45):
    r = subprocess.run(
        ["curl", "-sS", "--max-time", str(timeout), "--retry", "2",
         "--retry-delay", "5", "-A", UA, url],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"curl {url}: {r.stderr.strip()[:200]}")
    return r.stdout


def html_path_for(url):
    slug = urlparse(url).path.strip("/").replace("/", "__")[:180]
    return f"{HTMLDIR}/{slug}.html"


# ---------------- extraction ----------------

BOILERPLATE_CLASSES = re.compile(
    r"(TagPills|ArticleFooter|TransparencyBox|ArticleTopTimestamp|"
    r"RelatedArticles|Ellipsis|LatestBroadcast|AsideList|Comments|"
    r"VideoPlaylist|Carousel|InlineTagBox|GuideLinks|RelatedLink)")


def extract_body(soup):
    """Lede + body paragraphs + sub-headlines, per the 2025 reference-row style
    (no headline, no captions, no fact boxes, no footer/policy text)."""
    art = soup.find("article")
    if art is None:
        return "", {"no_article_tag": True}
    chunks, notes = [], {}
    lead = art.find("div", class_=re.compile(r"^(Lead__root|TopContainer__lead)"))
    if lead is None:
        # live pages carry the standfirst in a TopContainer lead outside <article>
        lead = soup.find("div", class_=re.compile(r"TopContainer__lead"))
    if lead:
        for p in lead.find_all("p") or [lead]:
            t = p.get_text(" ", strip=True)
            if t:
                chunks.append(t)
    body = art.find("div", class_=re.compile(r"TextArticle__body"))
    if body is None:
        body = art.find("div", class_=re.compile(r"TextArticle__main"))
    if body is not None:
        for el in body.descendants:
            name = getattr(el, "name", None)
            if name not in ("p", "h2", "h3", "li"):
                continue
            # li that wraps a p is covered by the p itself
            if name == "li" and el.find("p"):
                continue
            if name in ("p", "h2", "h3") and el.find_parent("li"):
                pass  # keep: text of a structured list item
            if el.find_parent(class_=BOILERPLATE_CLASSES):
                continue
            if el.find_parent("figure") or el.find_parent("figcaption"):
                continue
            if el.find_parent(class_=re.compile(r"FactBox|Factbox|InfoBox")):
                continue
            t = el.get_text(" ", strip=True)
            if not t:
                continue
            chunks.append(t)
    fact = art.find_all(class_=re.compile(r"FactBox__root|Factbox__root"))
    if fact:
        notes["factbox_chars"] = sum(len(f.get_text(" ", strip=True)) for f in fact)
    if not chunks:
        notes["empty_body"] = True
    return "\n".join(chunks), notes


def build_stream_table(raw_html):
    """Flatten all turbo-stream chunks (React Router streamController.enqueue)
    into one reference table."""
    table = []
    for m in re.finditer(r'streamController\.enqueue\("(.*?)"\);?\s*</script>',
                         raw_html, re.S):
        try:
            chunk = json.loads('"' + m.group(1) + '"').strip()
            if chunk.startswith("["):
                table.extend(json.loads(chunk))
        except (json.JSONDecodeError, ValueError):
            continue
    return table


def article_videos(raw_html):
    """Exact article-level video objects from the turbo-stream table.

    Included: the topMedia video (referenced from an interned 'topMedia' key)
    and inline body videos (signature: aspectRatio + highlights keys).
    Excluded: 'Fler videos' carousel items ('page' key), Direktcenter live-feed
    posts ('type'/'caption' keys), and teasers (those use 'statisticsId').
    Returns (videos, extra_modules_count, live_feed_seen).
    """
    table = build_stream_table(raw_html)
    svtid_idxs = {i for i, v in enumerate(table) if v == "svtId"}
    if not svtid_idxs:
        return [], 0, False
    tm_idxs = {i for i, v in enumerate(table) if v == "topMedia"}
    topmedia_refs = set()
    for v in table:
        if isinstance(v, dict):
            for k, ref in v.items():
                if (k.startswith("_") and int(k[1:]) in tm_idxs
                        and isinstance(ref, int)):
                    topmedia_refs.add(ref)
    videos, seen = [], set()
    extra = 0
    live_feed = False
    for idx, v in enumerate(table):
        if not isinstance(v, dict):
            continue
        obj, sid = {}, None
        for k, ref in v.items():
            if not k.startswith("_"):
                continue
            ki = int(k[1:])
            key = table[ki] if 0 <= ki < len(table) else k
            val = (table[ref] if isinstance(ref, int) and 0 <= ref < len(table)
                   and not isinstance(table[ref], (dict, list)) else None)
            obj[str(key)] = val
            if ki in svtid_idxs and isinstance(val, str):
                sid = val
        if not sid or sid in seen:
            continue
        if "type" in obj or "caption" in obj or "origin" in obj:
            live_feed = True
            continue
        is_top = idx in topmedia_refs
        is_inline = "aspectRatio" in obj and "highlights" in obj and "page" not in obj
        if is_top or is_inline:
            seen.add(sid)
            videos.append({"svtId": sid, "duration": obj.get("duration")})
        else:
            extra += 1
    return videos, extra, live_feed


def count_players(soup):
    """Real players inside <article> (top mainMedia video + inline videos),
    excluding related/latest-broadcast teaser modules."""
    art = soup.find("article")
    if art is None:
        return 0
    n = 0
    for w in art.find_all("div", class_=re.compile(r"VideoPlayerWrapper__root")):
        if w.find_parent(class_=BOILERPLATE_CLASSES):
            continue
        n += 1
    return n


def resolve_video(svtid, cache):
    if svtid in cache:
        return cache[svtid]
    try:
        raw = curl(f"https://api.svt.se/video/{svtid}", timeout=20)
        d = json.loads(raw)
        if not d.get("svtId") or d.get("contentDuration") is None:
            raise ValueError("not a video")
        info = {"ok": True, "duration": d.get("contentDuration"),
                "title": d.get("episodeTitle") or d.get("programTitle"),
                "svtplay": bool(d.get("hasVodReferences"))}
    except Exception:
        info = {"ok": False}
    cache[svtid] = info
    return info


def extract(url, raw_html, vidcache):
    soup = BeautifulSoup(raw_html, "html.parser")
    body, notes = extract_body(soup)
    n_players = count_players(soup)
    decoded, extra, live_feed = article_videos(raw_html)
    inline = []
    for v in decoded:
        info = resolve_video(v["svtId"], vidcache)
        inline.append({
            "svtId": v["svtId"],
            "duration": (v["duration"] if v["duration"] is not None
                         else info.get("duration")),
            "title": info.get("title"),
            "svtplay": bool(info.get("svtplay")),
        })
    if extra:
        notes["extra_video_modules"] = extra
    if live_feed:
        notes["live_feed"] = True
    if len(inline) != n_players and not live_feed:
        notes["video_mismatch"] = {"players_in_dom": n_players,
                                   "decoded_ids": len(inline)}
    # ld+json publication date
    pub = None
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(s.string)
            if d.get("@type") == "NewsArticle":
                pub = d.get("datePublished")
                break
        except Exception:
            pass
    if any(v["duration"] is None for v in inline):
        notes["missing_duration"] = [v["svtId"] for v in inline
                                     if v["duration"] is None]
    return {"body": body, "videos": inline,
            "players_in_dom": n_players, "ld_pubdate": pub, "notes": notes}


# ---------------- sheet ----------------

def sheet_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        SA_KEY, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds)


def ensure_grid(svc, title, need_rows, need_cols, pad=500):
    """Grow the `title` tab so an explicit-range write up to need_rows/need_cols
    fits inside the grid.

    values.update only writes within the existing grid and 400s with "exceeds
    grid limits" past the edge; only values.append grows a sheet. We cannot use
    append, because make_row bakes the row number into its formulas, so we
    widen the grid ourselves instead.
    """
    meta = svc.spreadsheets().get(
        spreadsheetId=SHEET_ID,
        fields="sheets(properties(sheetId,title,gridProperties))",
    ).execute(num_retries=5)
    for sheet in meta.get("sheets", []):
        props = sheet["properties"]
        if props["title"] == title:
            break
    else:
        raise RuntimeError(f"tab {title!r} not found in spreadsheet {SHEET_ID}")
    grid = props.get("gridProperties", {})
    have_rows = grid.get("rowCount", 0)
    have_cols = grid.get("columnCount", 0)
    reqs = []
    if need_rows > have_rows:
        reqs.append({"appendDimension": {
            "sheetId": props["sheetId"], "dimension": "ROWS",
            "length": need_rows - have_rows + pad}})
    if need_cols > have_cols:
        reqs.append({"appendDimension": {
            "sheetId": props["sheetId"], "dimension": "COLUMNS",
            "length": need_cols - have_cols}})
    if not reqs:
        return
    svc.spreadsheets().batchUpdate(
        spreadsheetId=SHEET_ID, body={"requests": reqs}).execute(num_retries=5)
    print(f"push: grew {title!r} from {have_rows}x{have_cols} "
          f"to fit {need_rows}x{need_cols}")


def make_row(n, rec):
    """Row n (1-based) in template shape A..W."""
    x = rec["extract"]
    vids = x["videos"]
    total = sum(v["duration"] or 0 for v in vids)
    has_video = 1 if (vids or x["players_in_dom"]) else 0
    links = ", ".join(
        (f"https://www.svtplay.se/video/{v['svtId']}" if v["svtplay"]
         else f"{rec['url']} (video {v['svtId']})") for v in vids)
    if not links and has_video:
        links = rec["url"]
    parts = []
    if len(vids) > 1:
        parts.append(f"{len(vids)} videos: " + ", ".join(
            f"{v['svtId']} ({v['duration']}s)" for v in vids))
    if x["notes"].get("live_feed"):
        parts.append("Live blog (Direktcenter feed)")
    if "video_mismatch" in x["notes"]:
        parts.append("CHECK video count")
    comment = "; ".join(parts)
    pub = (x["ld_pubdate"] or rec["pubdate"] or "")[:10]
    return [
        pub,
        rec["tab"],
        f'=IF(ISERROR(SEARCH("lokalt",B{n})),IF(ISERROR(SEARCH("inrikes",B{n})),"","Inrikes"),"Lokalt")',
        f'=IF(ISNUMBER(SEARCH("Nyheter/Utrikes",B{n})),"",IF(ISNUMBER(SEARCH("lokalt",B{n})),"Lokalt",IF(OR(ISNUMBER(SEARCH("Kultur",B{n})),ISNUMBER(SEARCH("Nyhet",B{n})),ISNUMBER(SEARCH("Sport",B{n}))),"Rikstäckande inkl sport","")))',
        f'=IF(ISNUMBER(SEARCH("Nyheter/Utrikes",B{n})),"",IF(ISNUMBER(SEARCH("lokalt",B{n})),"Lokalt",IF(OR(ISNUMBER(SEARCH("Kultur",B{n})),ISNUMBER(SEARCH("Nyhet",B{n}))),"Rikstäckande exkl sport","")))',
        f'=IF(REGEXMATCH(LOWER($B{n}),"lokalt"),"Lokalt","Riks/utrikes")',
        x["body"],
        f"=LEN(G{n})",
        f'=COUNTA(SPLIT(G{n}," "))',
        has_video,
        total if has_video else "",
        links,
        "", "", "",
        f"=K{n}*{WORDS_PER_SEC}",
        f"=P{n}+I{n}",
        "", "", "", "", "",
        comment,
        rec["url"],  # column X: Article URL
    ]


def push(state, dates):
    svc = sheet_service()
    # adopt rows that already exist on the sheet (self-healing after a crashed
    # run that wrote rows but lost its state) — keyed by column X (article URL)
    xs = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="Raw data!X1:X100000").execute(num_retries=5)
    adopted = 0
    last_used = 2
    for i, row in enumerate(xs.get("values", [])):
        n = i + 1
        url = row[0] if row else ""
        if url:
            last_used = max(last_used, n)
            rec = state.get(url)
            if rec is not None and not rec.get("sheet_row"):
                rec["sheet_row"] = n
                rec["needs_row_update"] = True  # rewrite with our own extract
                adopted += 1
    if adopted:
        print(f"push: adopted {adopted} rows already on the sheet")
    got = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="Raw data!A:A").execute(num_retries=5)
    next_row = max(len(got.get("values", [])), last_used) + 1
    todo = sorted(
        (u for u, r in state.items()
         if r.get("extract") and not r.get("sheet_row")
         and (r["extract"].get("ld_pubdate") or r["pubdate"] or "")[:10] in dates),
        key=lambda u: ((state[u]["extract"].get("ld_pubdate")
                        or state[u]["pubdate"] or ""), state[u]["tab"], u))
    if not todo:
        print("push: nothing new")
        return
    rows, assigned = [], []
    for u in todo:
        rows.append(make_row(next_row + len(rows), state[u]))
        assigned.append((u, next_row + len(rows) - 1))
    ensure_grid(svc, "Raw data", next_row + len(rows) - 1,
                max(len(r) for r in rows))
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"Raw data!A{next_row}",
        valueInputOption="USER_ENTERED",
        body={"values": rows}).execute(num_retries=5)
    for u, n in assigned:
        state[u]["sheet_row"] = n
    print(f"push: wrote {len(rows)} rows at A{next_row}")


def sync_updates(state):
    """Rewrite rows whose article was refetched after an update."""
    todo = [r for r in state.values()
            if r.get("needs_row_update") and r.get("sheet_row")]
    if not todo:
        return
    svc = sheet_service()
    data = [{"range": f"Raw data!A{r['sheet_row']}",
             "values": [make_row(r["sheet_row"], r)]} for r in todo]
    ensure_grid(svc, "Raw data",
                max(r["sheet_row"] for r in todo),
                max(len(d["values"][0]) for d in data))
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data}).execute(num_retries=5)
    for r in todo:
        r.pop("needs_row_update", None)
    print(f"push: refreshed {len(data)} updated rows")


# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="+", required=True)
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--refetch-updated", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(HTMLDIR, exist_ok=True)
    state = load_json(STATE_PATH, {})
    vidcache = load_json(VIDCACHE_PATH, {})
    reg = parse_sitemaps()
    targets = {u: e for u, e in reg.items()
               if e["pubdate"] and e["pubdate"][:10] in args.dates}
    print(f"sitemap union: {len(reg)} urls, in-scope for {args.dates}: {len(targets)}")

    n_fetched = n_updated = 0
    for i, (url, e) in enumerate(sorted(targets.items())):
        rec = state.setdefault(url, {})
        rec.update({"url": url, "pubdate": e["pubdate"], "lastmod": e["lastmod"],
                    "sitemap_title": e["title"], "tab": tab_for_url(url)})
        need = not rec.get("fetched_at")
        if args.refetch_updated and rec.get("fetched_at") and \
                (e["lastmod"] or "") > rec["fetched_at"]:
            need = True
        if need:
            if args.limit and n_fetched >= args.limit:
                continue
            try:
                raw = curl(url)
            except RuntimeError as err:
                print(f"  FETCH FAIL {url}: {err}", file=sys.stderr)
                continue
            path = html_path_for(url)
            with open(path, "w") as f:
                f.write(raw)
            was = bool(rec.get("fetched_at"))
            rec["fetched_at"] = datetime.now().astimezone().isoformat()
            rec["html_path"] = path
            rec["extract"] = extract(url, raw, vidcache)
            if was:
                rec.pop("sheet_row_stale", None)
                if rec.get("sheet_row"):
                    rec["needs_row_update"] = True
                n_updated += 1
            else:
                n_fetched += 1
            if (n_fetched + n_updated) % 25 == 0:
                save_json(STATE_PATH, state)
                save_json(VIDCACHE_PATH, vidcache)
                print(f"  ...{n_fetched} fetched")
            time.sleep(0.7)
        elif rec.get("html_path") and not rec.get("extract"):
            rec["extract"] = extract(url, open(rec["html_path"]).read(), vidcache)

    save_json(STATE_PATH, state)
    save_json(VIDCACHE_PATH, vidcache)
    print(f"fetched {n_fetched} new, {n_updated} updated")

    done = [r for r in state.values() if r.get("extract")]
    flags = [r["url"] for r in done if r["extract"]["notes"]]
    print(f"extracted total: {len(done)}, flagged: {len(flags)}")
    for u in flags[:15]:
        print("  flag:", u, json.dumps(state[u]["extract"]["notes"])[:120])

    if args.push:
        failed = []
        try:
            push(state, set(args.dates))
        except Exception as err:
            failed.append(f"push: {err}")
            print(f"push failed, rows stay unassigned for next run: {err}",
                  file=sys.stderr)
        save_json(STATE_PATH, state)
        try:
            sync_updates(state)
        except Exception as err:
            failed.append(f"sync_updates: {err}")
            print(f"sync_updates failed, rows stay flagged for next run: {err}",
                  file=sys.stderr)
        save_json(STATE_PATH, state)
        if failed:
            sys.exit("; ".join(failed))


if __name__ == "__main__":
    main()
