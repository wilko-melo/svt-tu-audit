#!/usr/bin/env python3
"""Build a second sheet tab that holds the body text WITHOUT the bold
summary/ingress block at the top of each article ("wirklich nur der
eigentliche Text").

The canonical tab "Raw data" is left untouched — this duplicates it into
"Raw data (no lead)" and rewrites only column G; H/I (chars/words) are
formulas that reference G in the same row, so they recompute themselves.

The removed lead of every article is kept in nolead.json so the split is
reproducible and reversible.
"""
import json, os, re, sys

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svt_ingest import (BASE, HTMLDIR, SHEET_ID, STATE_PATH, ensure_grid,
                        load_json, sheet_service)

SRC_TAB = "Raw data"
DST_TAB = "Raw data (no lead)"
NOLEAD_PATH = f"{BASE}/nolead.json"
LEAD_CLASS = re.compile(r"^(Lead__root|TopContainer__lead)")


def split_lead(raw_html):
    """Return (lead_paragraphs, ...) exactly as extract_body() collects them."""
    soup = BeautifulSoup(raw_html, "html.parser")
    art = soup.find("article")
    if art is None:
        return []
    lead = art.find("div", class_=LEAD_CLASS)
    if lead is None:
        lead = soup.find("div", class_=re.compile(r"TopContainer__lead"))
    if not lead:
        return []
    out = []
    for p in lead.find_all("p") or [lead]:
        t = p.get_text(" ", strip=True)
        if t:
            out.append(t)
    return out


def strip_lead(body, lead_paras):
    """Drop the lead paragraphs from the front of the stored body."""
    lines = body.split("\n")
    i = 0
    for lp in lead_paras:
        if i < len(lines) and lines[i].strip() == lp.strip():
            i += 1
        else:
            break
    return "\n".join(lines[i:]).lstrip("\n"), i


def build_split():
    state = load_json(STATE_PATH, {})
    out, unmatched = {}, []
    for url, rec in state.items():
        x = rec.get("extract") or {}
        body = x.get("body", "")
        if not body:
            continue
        hp = os.path.join(HTMLDIR, os.path.basename(rec.get("html_path", "")))
        if not os.path.exists(hp):
            unmatched.append((url, "no cached html"))
            continue
        with open(hp, encoding="utf-8", errors="ignore") as fh:
            lead = split_lead(fh.read())
        nobody, n = strip_lead(body, lead)
        if lead and n != len(lead):
            unmatched.append((url, f"matched {n}/{len(lead)} lead paragraphs"))
        out[url] = {
            "lead": "\n".join(lead[:n]),
            "body_nolead": nobody,
            "lead_paragraphs": n,
            "sheet_row": rec.get("sheet_row"),
        }
    return out, unmatched


def ensure_tab(svc):
    meta = svc.spreadsheets().get(
        spreadsheetId=SHEET_ID,
        fields="sheets(properties(sheetId,title,index,gridProperties))",
    ).execute(num_retries=5)
    sheets = {s["properties"]["title"]: s["properties"] for s in meta["sheets"]}
    if DST_TAB in sheets:
        print(f"tab {DST_TAB!r} exists — refreshing column G only")
        return sheets[DST_TAB]["sheetId"]
    src = sheets[SRC_TAB]
    res = svc.spreadsheets().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"requests": [{"duplicateSheet": {
            "sourceSheetId": src["sheetId"],
            "insertSheetIndex": src["index"] + 1,
            "newSheetName": DST_TAB}}]}).execute(num_retries=5)
    sid = res["replies"][0]["duplicateSheet"]["properties"]["sheetId"]
    print(f"tab {DST_TAB!r} created as a copy of {SRC_TAB!r}")
    return sid


def main():
    split, unmatched = build_split()
    with open(NOLEAD_PATH, "w", encoding="utf-8") as fh:
        json.dump(split, fh, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"split {len(split)} articles -> {NOLEAD_PATH}")
    if unmatched:
        print(f"WARNING: {len(unmatched)} articles need a look:")
        for u, why in unmatched[:20]:
            print("   ", why, u)

    svc = sheet_service()
    ensure_tab(svc)

    by_row = {v["sheet_row"]: v for v in split.values() if v["sheet_row"]}
    if not by_row:
        print("no rows mapped — nothing written")
        return
    first, last = min(by_row), max(by_row)
    cur = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{DST_TAB}'!G{first}:G{last}").execute(num_retries=5)
    have = cur.get("values", [])

    col, kept = [], 0
    for n in range(first, last + 1):
        rec = by_row.get(n)
        if rec is None:                       # row we do not own: leave as is
            old = have[n - first] if n - first < len(have) else []
            col.append([old[0] if old else ""])
            kept += 1
        else:
            col.append([rec["body_nolead"]])

    ensure_grid(svc, DST_TAB, last, 24)
    CHUNK = 200
    for off in range(0, len(col), CHUNK):
        part = col[off:off + CHUNK]
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=f"'{DST_TAB}'!G{first + off}",
            valueInputOption="RAW",
            body={"values": part}).execute(num_retries=5)
    print(f"wrote G{first}:G{last} on {DST_TAB!r} "
          f"({len(col) - kept} rewritten, {kept} left untouched)")


if __name__ == "__main__":
    main()
