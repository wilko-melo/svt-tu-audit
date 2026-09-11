#!/usr/bin/env python3
"""Duplicate the Analytics tab into "Analytics (no lead)" so that BOTH sides of
the year comparison exclude the bold summary/ingress block:

  * every 2026 formula is repointed to the "Raw data (no lead)" tab;
  * the 2025 reference characters/words are replaced by lead-stripped values.

2025 carries no markup (the body text was copy-pasted by hand and the file has
no article URLs), so its ingress is removed with a text rule calibrated against
195 of the 2025 articles that could be re-fetched live via the video-link
column: strip the first paragraph plus any following paragraph that opens with
a quote dash.  Rule accuracy on those articles: 77% exact, 94.7% of the true
ingress words removed -- the 2025 no-lead figures are therefore ~1% high.
"""
import json, re, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svt_ingest import SHEET_ID, sheet_service

SRC, DST = "Analytics", "Analytics (no lead)"
NOLEAD_TAB = "Raw data (no lead)"

# lead-stripped 2025 values, from bin/../tools measurement (see docstring)
Y25 = {
    "2025-09-01": (137317, 20972), "2025-09-02": (146796, 22642),
    "2025-09-03": (134364, 20401), "2025-09-04": (151066, 23470),
    "2025-09-05": (166977, 25622), "2025-09-06": (84371, 13093),
    "2025-09-07": (82762, 12821),
}
Y25_CHARS = sum(v[0] for v in Y25.values())
Y25_WORDS = sum(v[1] for v in Y25.values())
Y25_ARTICLES = 965
Y25_VIDEO_WORDS = 239577          # unchanged: video estimate, not body text


def ensure_tab(svc):
    meta = svc.spreadsheets().get(
        spreadsheetId=SHEET_ID,
        fields="sheets(properties(sheetId,title,index))").execute(num_retries=5)
    props = {s["properties"]["title"]: s["properties"] for s in meta["sheets"]}
    if DST in props:
        print(f"tab {DST!r} exists — rewriting it")
        return props[DST]["sheetId"]
    src = props[SRC]
    res = svc.spreadsheets().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"requests": [{"duplicateSheet": {
            "sourceSheetId": src["sheetId"],
            "insertSheetIndex": src["index"] + 1,
            "newSheetName": DST}}]}).execute(num_retries=5)
    print(f"tab {DST!r} created as a copy of {SRC!r}")
    return res["replies"][0]["duplicateSheet"]["properties"]["sheetId"]


def main():
    svc = sheet_service()
    ensure_tab(svc)
    vals = svc.spreadsheets().values()

    grid = vals.get(spreadsheetId=SHEET_ID, range=f"'{DST}'!A1:N130",
                    valueRenderOption="FORMULA").execute(num_retries=5).get("values", [])
    width = 14
    out, repointed = [], 0
    for row in grid:
        row = list(row) + [""] * (width - len(row))
        new = []
        for c in row[:width]:
            if isinstance(c, str) and "'Raw data'!" in c:
                c = c.replace("'Raw data'!", f"'{NOLEAD_TAB}'!")
                repointed += 1
            new.append(c)
        out.append(new)

    def put(r, c, v):
        out[r - 1][c] = v

    # headline + method notes
    put(1, 0, "SVT.se content audit — analytics (body text WITHOUT the ingress) "
              "· measurement week 1–7 September 2026")
    put(2, 0, "Both years exclude the bold summary/ingress block at the top of "
              "the article. 2026 is live from the 'Raw data (no lead)' tab. "
              "2025 has no markup (hand-pasted text, no URLs), so its ingress "
              "was removed with a text rule calibrated on 195 re-fetched 2025 "
              "articles — 77% exact, 94.7% of ingress words removed, so the "
              "2025 figures here are roughly 1% high.")
    # week totals: 2025 reference column C
    put(10, 2, Y25_CHARS)
    put(11, 2, Y25_WORDS)
    put(14, 2, Y25_WORDS + Y25_VIDEO_WORDS)
    put(15, 2, round(Y25_CHARS / Y25_ARTICLES, 1))
    # per-day 2025 reference columns J (chars) and K (words)
    for i, day in enumerate(sorted(Y25)):
        put(20 + i, 9, Y25[day][0])
        put(20 + i, 10, Y25[day][1])
    put(27, 9, Y25_CHARS)
    put(27, 10, Y25_WORDS)
    # method note
    put(80, 0, "Body text = body + sub-headlines. The standfirst/summary block "
               "('Lead__root') is EXCLUDED here — unlike the canonical 'Raw "
               "data' tab and unlike the 2025 measurement, whose figures were "
               "corrected for this tab. Headline, photo captions, fact boxes, "
               "teasers and footers are excluded in both.")

    vals.update(spreadsheetId=SHEET_ID, range=f"'{DST}'!A1",
                valueInputOption="USER_ENTERED",
                body={"values": out}).execute(num_retries=5)
    print(f"rewrote {len(out)} rows on {DST!r}; repointed {repointed} formulas "
          f"to {NOLEAD_TAB!r}")
    print(f"2025 reference now: {Y25_CHARS:,} chars / {Y25_WORDS:,} words "
          f"(was 1,077,822 / 165,409)")


if __name__ == "__main__":
    main()
