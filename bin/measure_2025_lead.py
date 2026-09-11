#!/usr/bin/env python3
"""Measure how much of the 2025 body text is the standfirst/ingress, and emit
the lead-stripped 2025 reference figures used by bin/build_nolead_analytics.py.

The 2025 measurement pasted the body text by hand and kept no article URLs, so
the ingress cannot be read off markup. Two steps get around that:

1. 196 rows carry an svt.se article link in the video-link column. Those pages
   are re-fetched live; where the page's Lead__root paragraphs still match the
   start of the 2025 text verbatim, the true ingress of that row is known.
2. A text-only rule is calibrated on those rows and applied to all 965:
   first paragraph + any following paragraph opening with a quote dash.

Output: per-day and total characters/words with the ingress removed.
No 2025 text is written to disk — this repo is public.

usage: python3 bin/measure_2025_lead.py [path to 2025 xlsx]
"""
import collections, datetime, re, subprocess, sys, unicodedata
from concurrent.futures import ThreadPoolExecutor

import openpyxl
from bs4 import BeautifulSoup

XLSX = (sys.argv[1] if len(sys.argv) > 1
        else "/Users/wilkokramer/Downloads/SVT-granskning TU-2-2.xlsx")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
LEAD = re.compile(r"^(Lead__root|TopContainer__lead)")
COL = {"date": 0, "tab": 1, "text": 6, "chars": 7, "words": 8, "link": 11}


def norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip().lower()


def rule_lead_paragraphs(lines):
    """Calibrated text-only rule: 77% exact, 94.7% of ingress words removed."""
    n = 1
    for line in lines[1:3]:
        if line.lstrip().startswith(("–", "—")) and len(line.split()) <= 45:
            n += 1
        else:
            break
    return n


def load_rows():
    wb = openpyxl.load_workbook(XLSX, data_only=True, read_only=True)
    rows = []
    for r in wb["Rådata"].iter_rows(min_row=2, values_only=True):
        if not r[COL["text"]]:
            continue
        link = r[COL["link"]]
        link = str(link).split("+[@[")[0].strip() if link else ""
        if not (link.startswith("http") and "svt.se" in link and "/video/" not in link):
            link = ""
        rows.append({"date": r[COL["date"]], "text": r[COL["text"]],
                     "chars": float(r[COL["chars"]] or 0),
                     "words": float(r[COL["words"]] or 0), "link": link})
    return rows


def fetch(urls):
    def get(u):
        out = subprocess.run(["curl", "-sL", "--max-time", "40", "-A", UA, u],
                             capture_output=True).stdout
        return u, out.decode("utf-8", "ignore")
    with ThreadPoolExecutor(max_workers=8) as ex:
        return dict(ex.map(get, urls))


def true_lead(html, text):
    soup = BeautifulSoup(html, "html.parser")
    art = soup.find("article")
    if art is None:
        return 0
    lead = art.find("div", class_=LEAD) or soup.find(
        "div", class_=re.compile("TopContainer__lead"))
    if not lead:
        return 0
    paras = [p.get_text(" ", strip=True) for p in (lead.find_all("p") or [lead])]
    lines = text.split("\n")
    n = 0
    for p in [x for x in paras if x]:
        if n < len(lines) and norm(lines[n]) == norm(p):
            n += 1
        else:
            break
    return n


def main():
    rows = load_rows()
    linked = [r for r in rows if r["link"]]
    print(f"2025 rows: {len(rows)} | with a re-fetchable article link: {len(linked)}")
    pages = fetch([r["link"] for r in linked])
    known = 0
    for r in linked:
        n = true_lead(pages.get(r["link"], ""), r["text"])
        if n:
            r["lead_n"] = n
            known += 1
    print(f"ingress verified verbatim on {known} of {len(linked)} re-fetched articles")

    per = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0])
    for r in rows:
        lines = r["text"].split("\n")
        n = r.get("lead_n") or rule_lead_paragraphs(lines)
        stripped = "\n".join(lines[n:])
        rc = len(stripped) / len(r["text"]) if r["text"] else 1.0
        rw = len(stripped.split()) / max(len(r["text"].split()), 1)
        d = r["date"]
        day = (d.date().isoformat() if isinstance(d, datetime.datetime)
               else str(d)[:10])
        a = per[day]
        a[0] += r["chars"]; a[1] += r["words"]
        a[2] += r["chars"] * rc; a[3] += r["words"] * rw; a[4] += 1

    print(f"\n{'day':12}{'art':>5}{'chars':>10}{'no lead':>10}{'words':>8}{'no lead':>9}")
    tc = tw = nc = nw = na = 0
    for day in sorted(per):
        a = per[day]
        tc += a[0]; tw += a[1]; nc += a[2]; nw += a[3]; na += a[4]
        print(f"{day:12}{a[4]:5d}{a[0]:10,.0f}{a[2]:10,.0f}{a[1]:8,.0f}{a[3]:9,.0f}")
    print(f"{'TOTAL':12}{na:5d}{tc:10,.0f}{nc:10,.0f}{tw:8,.0f}{nw:9,.0f}")
    print(f"ingress share 2025: {1 - nc/tc:.1%} of characters, {1 - nw/tw:.1%} of words")


if __name__ == "__main__":
    main()
