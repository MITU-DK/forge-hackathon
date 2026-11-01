"""Fetch the official documents the agent may quote from backend/data/knowledge/ (nothing is typed by hand).

    cd backend && python -m scripts.fetch_knowledge     # then: make seed (or just start the API: it seeds itself)

Each document is a Press Information Bureau (Government of India) press release, saved as received with its title, posted date, release
id and canonical URL in a front-matter header, and the day it was fetched. PIB's terms allow reproduction free of charge when the source
is acknowledged and the text is accurate and not used misleadingly: the files are therefore unedited copies, and the agent names the
document and its date when it uses one. Tables keep one line per row ("Paddy (Common) | 2,441 | ...") so a price can never drift
away from its crop. Re-run the script to refresh; the release list below is the only editorial choice.
"""
from __future__ import annotations

import datetime as dt
import html
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent.parent / "data" / "knowledge"
URL = "https://www.pib.gov.in/PressReleasePage.aspx?PRID={prid}"
UA = "Mozilla/5.0 (compatible; KisaanMitra FORGE 2026 hackathon; farm advisory)"

# (PRID, file name, why it is here). Chosen on 31 Oct 2026 for what a farmer asks about prices, support and schemes.
DOCS = [
    (2316941, "msp_rabi_2027_28", "minimum support prices for rabi crops, marketing season 2027-28 (approved 30 Sep 2026)"),
    (2260617, "msp_kharif_2026_27", "minimum support prices for kharif crops, marketing season 2026-27 (approved 13 May 2026)"),
    (2191651, "pm_kisan_21st_instalment_backgrounder", "PM-KISAN: what the scheme pays, who receives it, e-KYC and land-record conditions"),
    (2274616, "pm_kisan_23rd_instalment", "PM-KISAN 23rd instalment announcement (June 2026)"),
    (2237736, "pmfby_premium_rates", "PM Fasal Bima Yojana: farmer premium caps (2 % kharif, 1.5 % rabi, 5 % commercial) and cost sharing"),
]
CHROME = re.compile(r"^(Press Release Page.*|Ministry of .*|Farmers Welfare|Cabinet Committee on Economic Affairs.*|PIB Backgrounder|Press Information Bureau.*)$", re.I)


class _Blocks(HTMLParser):
    """Visible text as blocks: a paragraph per block element, and one line per table row (cells joined by ' | ')."""

    def __init__(self):
        super().__init__()
        self.skip, self.blocks, self.cur = 0, [], []
        self.kinds: list[str] = []  # 'p' paragraph or 't' table, parallel to blocks
        self.table, self.row, self.cell = None, None, None

    def _flush(self):
        t = " ".join("".join(self.cur).split())
        if t:
            self.blocks.append(t)
            self.kinds.append("p")
        self.cur = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        elif tag == "table":
            self._flush()
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag in ("p", "div", "li", "br", "h1", "h2", "h3", "h4") and self.table is None:
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip -= 1
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            if any(self.row):
                self.table.append(" | ".join(self.row))
            self.row = None
        elif tag == "table" and self.table is not None:
            if self.table:
                self.blocks.append("\n".join(self.table))
                self.kinds.append("t")
            self.table = None

    def handle_data(self, data):
        if self.skip:
            return
        (self.cell if self.cell is not None else self.cur).append(data)


MIN_BODY_CHARS = 300
CHROME_LINE = re.compile(r"[\*\s]+|[A-Z/\-]{2,20}|\(Release ID:\s*\d+\)|-- words \| -- min read")


def _merged_blocks(parser: _Blocks) -> tuple[list[str], list[str]]:
    """Cleaned blocks and their kinds; consecutive table fragments are joined into one table."""
    blocks, kinds = [], []
    for text, kind in zip(parser.blocks, parser.kinds):
        text = html.unescape(text).replace("\xa0", " ").strip()
        if not text:
            continue
        if kind == "t" and kinds and kinds[-1] == "t":  # the page splits one price table into several <table> elements
            if text not in blocks[-1]:  # a hidden second copy of the same table is not a continuation
                blocks[-1] += "\n" + text
        else:
            blocks.append(text)
            kinds.append(kind)
    return blocks, kinds


def _body_blocks(blocks: list[str]) -> list[str]:
    """Release text without page chrome and without the hidden second copy of every table that the page repeats."""
    body, seen = [], set()
    for b in blocks:
        if CHROME_LINE.fullmatch(b) or b in seen:
            continue
        seen.add(b)
        body.append(b)
    return body


def parse_release(page: str) -> dict:
    """PIB release page -> {title, posted, release_id, body}. Raises ValueError when the page does not look like a release."""
    parser = _Blocks()
    parser.feed(page)
    parser._flush()
    blocks, _ = _merged_blocks(parser)
    try:
        i = next(n for n, b in enumerate(blocks) if b.startswith("Posted On"))
    except StopIteration:
        raise ValueError("no 'Posted On' line: not a PIB press release page") from None
    title = next((b for b in blocks[:i] if not CHROME.match(b)), "")
    inline = blocks[i].replace("Posted On:", "").strip()  # the date is either in the same block as "Posted On:" or in the next one
    posted = inline or (blocks[i + 1] if i + 1 < len(blocks) else "")
    first_body = i + 1 if inline else i + 2
    release_id = next((m.group(1) for b in blocks if (m := re.search(r"Release ID:\s*(\d+)", b))), "")
    body = _body_blocks(blocks[first_body:])
    if not title or len(" ".join(body)) < MIN_BODY_CHARS:
        raise ValueError("release text too short or untitled")
    return {"title": title, "posted": posted.split(" by ")[0].strip(), "release_id": release_id, "body": "\n\n".join(body)}


# Where PIB's own table header is garbled, a reading guide is inserted before the table, marked as ours. Each was checked against a row and
# against the release's prose (kharif paddy: 2441 is the MSP, as the text says; 2441 - 2369 = the stated increase of 72).
GUIDES = {
    "msp_kharif_2026_27": "[Reading guide added by KisaanMitra, not part of the PIB text: in each crop row of the table below, the first number after the crop name is the "
                          "MSP for 2026-27 in Rs per quintal, then the cost of production, the margin over cost in percent, the MSP for 2025-26, the MSP for 2013-14, "
                          "the increase over 2025-26 and the increase over 2013-14.]",
}


def render(doc: dict, prid: int, why: str, today: dt.date, guide: str = "") -> str:
    if guide:
        parts = doc["body"].split("\n\n")
        k = next((n for n, b in enumerate(parts) if b.startswith("S. No")), 0)
        parts[k] = guide + "\n" + parts[k]  # the guide is the table's first line, so no chunk can hold the numbers without it
        doc = {**doc, "body": "\n\n".join(parts)}
    return (f"title: {doc['title']}\nsource: Press Information Bureau, Government of India\nurl: {URL.format(prid=prid)}\nposted: {doc['posted']}\n"
            f"release_id: {doc['release_id'] or prid}\nfetched: {today.isoformat()}\nwhy: {why}\n---\n{doc['body']}\n")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    bad = 0
    with httpx.Client(timeout=40, follow_redirects=True, headers={"User-Agent": UA}) as c:
        for prid, name, why in DOCS:
            try:
                doc = parse_release(c.get(URL.format(prid=prid)).raise_for_status().text)
            except (httpx.HTTPError, ValueError) as e:
                print(f"FAIL {name}: {type(e).__name__}: {e}")
                bad += 1
                continue
            (OUT / f"{name}.md").write_text(render(doc, prid, why, dt.date.today(), GUIDES.get(name, '')), encoding="utf-8")
            print(f"ok   {name}: {doc['posted']} | {doc['title'][:70]} | {len(doc['body'])} chars")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
