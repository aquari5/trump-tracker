"""Fetches statements (RSS feeds) and House stock-trade filings (STOCK Act PTRs)."""
from __future__ import annotations

import html
import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import feedparser
import requests
from pypdf import PdfReader

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
}
session = requests.Session()
session.headers.update(HEADERS)


def http_get(url: str, timeout: int = 30) -> requests.Response:
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", raw or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


# --- Statements ------------------------------------------------------------

@dataclass
class Post:
    id: str
    source: str
    published: datetime
    title: str
    text: str
    url: str


def fetch_feed(src: dict) -> list[Post]:
    feed = feedparser.parse(http_get(src["url"]).content)
    if not feed.entries:
        raise RuntimeError(f"{src['name']}: feed has no entries")

    posts = []
    for e in feed.entries:
        guid = e.get("id") or e.get("link")
        parsed = e.get("published_parsed") or e.get("updated_parsed")
        published = datetime(*parsed[:6], tzinfo=timezone.utc) if parsed else datetime.now(timezone.utc)
        if src.get("type") == "truth_rss":
            text = html_to_text(e.get("description", ""))
            # Drop "View video" / bare attachment links; they carry no statement.
            text = re.sub(r"(?m)^View (video|image)\s*$", "", text).strip()
            url = e.get("truth_originalurl") or e.get("link", "")
            title = ""
        else:
            body = e.content[0].value if e.get("content") else e.get("summary", "")
            title = html_to_text(e.get("title", ""))
            text = html_to_text(body)
            url = e.get("link", "")
        posts.append(Post(
            id=f"{src['key']}:{guid}",
            source=src["name"],
            published=published,
            title=title,
            text=text,
            url=url,
        ))
    return posts


# --- House of Representatives stock trades ----------------------------------

HOUSE_INDEX = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
HOUSE_PTR = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"

TX_TYPES = {"P": "Kupno", "S": "Sprzedaż", "S (partial)": "Sprzedaż częściowa", "E": "Wymiana"}
OWNERS = {"SP": "małżonek", "JT": "wspólne", "DC": "dziecko", "": "sam/a"}

# One transaction row in an electronically filed PTR, e.g.
#   "SP NVIDIA Corporation - Common Stock\n(NVDA) [ST]\nS (partial) 08/31/2026 09/14/2026 $1,001 - $15,000"
# The asset name (with its ticker, if any) precedes the match.
TX_RE = re.compile(
    r"\[(?P<atype>[A-Z]{2,3})\]\s*"
    r"(?P<type>S \(partial\)|P|S|E)\s+"
    r"(?P<date>\d{2}/\d{2}/\d{4})\s+\d{2}/\d{2}/\d{4}\s+"
    r"(?P<amount>Over \$[\d,]+|\$[\d,]+(?:\s*-\s*\$[\d,]+)?)"
)
TICKER_RE = re.compile(r"\((?P<ticker>[A-Z][A-Z0-9.\-/]{0,9})\)\s*$")
# Form labels render as spaced-out capitals ("F      S     : New", "S          O : Trust");
# every transaction is followed by them, so the next asset name starts after the last one.
LABEL_LINE = re.compile(r"^[A-Z]\s{2,}")
DESCRIPTION_LINE = re.compile(r"^D\s{2,}:\s*(.+)")


@dataclass
class Filing:
    doc_id: str
    name: str
    district: str
    filed: date
    url: str
    scanned: bool = False
    transactions: list = field(default_factory=list)


def fetch_house_filings(year: int) -> list[Filing]:
    """Index of periodic transaction reports (FilingType P) filed in `year`."""
    zf = zipfile.ZipFile(io.BytesIO(http_get(HOUSE_INDEX.format(year=year), timeout=60).content))
    root = ET.fromstring(zf.read(f"{year}FD.xml"))
    filings = []
    for m in root:
        if m.findtext("FilingType") != "P":
            continue
        doc_id = m.findtext("DocID", "").strip()
        name = " ".join(filter(None, [m.findtext("First"), m.findtext("Last"), m.findtext("Suffix")]))
        filings.append(Filing(
            doc_id=doc_id,
            name=re.sub(r"\s+", " ", name).strip(),
            district=m.findtext("StateDst", "") or "",
            filed=datetime.strptime(m.findtext("FilingDate"), "%m/%d/%Y").date(),
            url=HOUSE_PTR.format(year=year, doc_id=doc_id),
        ))
    return filings


def amount_floor(amount: str) -> int:
    first = re.search(r"\$([\d,]+)", amount)
    return int(first.group(1).replace(",", "")) if first else 0


def parse_ptr(pdf_bytes: bytes) -> list[dict]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    # The PDF renders form labels with NUL padding ("F\x00\x00 S\x00: New").
    text = text.replace("\x00", " ")
    matches = list(TX_RE.finditer(text))
    transactions = []
    prev_end = 0
    for i, m in enumerate(matches):
        lines = [ln.strip() for ln in text[prev_end:m.start()].splitlines() if ln.strip()]
        prev_end = m.end()
        starts = [j + 1 for j, ln in enumerate(lines) if LABEL_LINE.match(ln) or ln == "$200?"]
        asset = " ".join(lines[starts[-1]:] if starts else lines)
        ticker = TICKER_RE.search(asset)
        if not ticker:
            continue  # bonds, funds and other assets without a ticker
        asset = asset[:ticker.start()].strip()
        owner = ""
        owner_match = re.match(r"^(SP|JT|DC)\s+", asset)
        if owner_match:
            owner = owner_match.group(1)
            asset = asset[owner_match.end():]
        asset = re.sub(r"\s*-?\s*(Common Stock|Ordinary Shares)\b.*$", "", asset, flags=re.I)
        asset = re.sub(r"\s+New$", "", asset).strip(" -,")

        # Labels directly under the row may include a description ("D : Purchased 10 calls...").
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        description = ""
        for ln in filter(None, (ln.strip() for ln in text[m.end():next_start].splitlines())):
            if not LABEL_LINE.match(ln):
                break
            d = DESCRIPTION_LINE.match(ln)
            if d:
                description = d.group(1).strip()

        transactions.append({
            "ticker": ticker.group("ticker"),
            "asset": asset or ticker.group("ticker"),
            "asset_type": m.group("atype"),
            "type": TX_TYPES.get(m.group("type"), m.group("type")),
            "date": datetime.strptime(m.group("date"), "%m/%d/%Y").date().isoformat(),
            "amount": re.sub(r"\s+", " ", m.group("amount")),
            "amount_floor": amount_floor(m.group("amount")),
            "owner": OWNERS.get(owner, owner),
            "description": description,
        })
    return transactions


def load_filing(filing: Filing) -> Filing:
    pdf = http_get(filing.url, timeout=60).content
    # Paper filings are scanned images (DocIDs starting with 8/9) with no text layer.
    try:
        filing.transactions = parse_ptr(pdf)
    except Exception as e:  # malformed PDFs shouldn't stop the whole run
        log.warning("Could not parse %s: %s", filing.url, e)
        filing.transactions = []
    filing.scanned = not filing.transactions and filing.doc_id[:1] in ("8", "9")
    return filing
