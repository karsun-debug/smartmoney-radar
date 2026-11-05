"""House of Representatives Periodic Transaction Reports, read at the source.

1. The Clerk publishes a yearly index: ``{YEAR}FD.zip`` -> ``{YEAR}FD.xml`` with
   one ``<Member>`` per filing (Last, First, FilingType, FilingDate, DocID).
   FilingType ``P`` = Periodic Transaction Report (a stock-trade disclosure).
2. Electronic PTRs are text PDFs at ``ptr-pdfs/{YEAR}/{DocID}.pdf`` with a fixed
   table: ID | Owner | Asset | Transaction Type | Date | Notification Date | Amount.
   We read each text fragment with its (x, y) position, find the header row, and
   slice following lines into columns by the header's x positions.
3. Paper filings (scanned, no text layer) can't be parsed; they still produce a
   "filed a new report" event with the PDF link so nothing is silently missed.

The approach mirrors LuxAlgo market-trackers' documented House source.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime

from .models import Trade

INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
PTR_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc}.pdf"

OWNER = {"SP": "配偶", "JT": "共同", "DC": "受扶養人", "": "本人"}
TYPE = {"P": "buy", "S": "sell", "S (PARTIAL)": "sell", "E": "exchange"}
KIND = {"ST": "", "OP": "選擇權", "EF": "ETF", "MF": "基金", "CT": "加密貨幣", "GS": "政府債",
        "CS": "公司債", "AB": "資產擔保", "PS": "特別股", "OT": "其他"}
ANNOTATIONS = ("FILING STATUS", "SUBHOLDING OF", "DESCRIPTION", "COMMENTS", "LOCATION")
HEADER_WORDS = {"ID", "OWNER", "ASSET", "TRANSACTION", "TYPE", "DATE", "NOTIFICATION",
                "AMOUNT", "CAP.", "GAINS >", "$200?"}
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
TICKER_RE = re.compile(r"\(([A-Z0-9.]{1,6})\)")


@dataclass(frozen=True)
class IndexRow:
    last: str
    first: str
    filing_type: str
    filed: date | None
    doc_id: str
    year: int

    @property
    def pdf_url(self) -> str:
        return PTR_URL.format(year=self.year, doc=self.doc_id)


def parse_index(zip_or_xml: bytes, year: int) -> list[IndexRow]:
    data = zip_or_xml
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
            data = zf.read(name)
    rows = []
    for m in ET.fromstring(data).iter("Member"):
        get = lambda tag: (m.findtext(tag) or "").strip()  # noqa: E731
        doc, filed = get("DocID"), get("FilingDate")
        if not doc:
            continue
        try:
            when = datetime.strptime(filed, "%m/%d/%Y").date() if filed else None
        except ValueError:
            when = None
        rows.append(IndexRow(get("Last"), get("First"), get("FilingType").upper(), when, doc, year))
    return rows


# ------------------------------------------------------------------ PDF text

@dataclass(frozen=True)
class Item:
    text: str
    x: float
    y: float
    page: int


def pdf_items(pdf: bytes) -> list[Item]:
    """Positioned text fragments of a PDF (empty list for scanned filings)."""
    from pypdf import PdfReader
    items: list[Item] = []
    reader = PdfReader(io.BytesIO(pdf))
    for page_no, page in enumerate(reader.pages, 1):
        def visit(text, cm, tm, font, size, _p=page_no):
            t = " ".join((text or "").split())
            if not t:
                return
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            items.append(Item(t, x, y, _p))
        page.extract_text(visitor_text=visit)
    return items


def _lines(items: Iterable[Item], tol: float = 3.0) -> list[list[Item]]:
    """Group fragments into visual lines (same page, y within ``tol``), top to bottom."""
    out: list[list[Item]] = []
    for it in sorted(items, key=lambda i: (i.page, -i.y, i.x)):
        if out and out[-1][0].page == it.page and abs(out[-1][0].y - it.y) <= tol:
            out[-1].append(it)
        else:
            out.append([it])
    return [sorted(line, key=lambda i: i.x) for line in out]


@dataclass
class PtrRow:
    owner: str = ""
    asset: str = ""
    tx_type: str = ""
    date: str = ""
    amount: str = ""


def _columns(header: list[Item]) -> list[tuple[str, float]]:
    names = {"OWNER": "owner", "ASSET": "asset", "TRANSACTION": "tx_type", "AMOUNT": "amount",
             "NOTIFICATION": "notified", "CAP.": "capgains", "ID": "id"}
    cols, seen_date = [], False
    for it in header:
        word = it.text.upper()
        if word == "DATE" and not seen_date:
            cols.append(("date", it.x))
            seen_date = True
        elif word in names:
            cols.append((names[word], it.x))
    return sorted(cols, key=lambda c: c[1])


def _assign(line: list[Item], cols: list[tuple[str, float]], slack: float = 4.0) -> dict[str, str]:
    cells: dict[str, list[str]] = {}
    for it in line:
        name = cols[0][0]
        for col, x in cols:
            if it.x + slack >= x:
                name = col
        cells.setdefault(name, []).append(it.text)
    return {k: " ".join(v) for k, v in cells.items()}


def parse_ptr(items: list[Item]) -> list[PtrRow]:
    """Transactions from a House PTR's positioned text."""
    rows: list[PtrRow] = []
    cols: list[tuple[str, float]] | None = None
    for line in _lines(items):
        words = [i.text.upper() for i in line]
        joined = " ".join(words)
        if {"OWNER", "ASSET", "AMOUNT"} <= set(words):
            cols = _columns(line)              # header (repeated on every page)
            continue
        if cols is None:
            continue
        if joined.startswith("* FOR THE COMPLETE LIST") or joined.startswith("CERTIFICATION"):
            cols = None                        # end of the table
            continue
        if set(words) <= HEADER_WORDS:
            continue                           # second/third row of a wrapped header
        cells = _assign(line, cols)
        if any(cells.get("asset", "").upper().startswith(a) for a in ANNOTATIONS):
            continue
        tx = cells.get("tx_type", "").strip().upper()
        if tx in TYPE and DATE_RE.match(cells.get("date", "").strip()):
            rows.append(PtrRow(cells.get("owner", "").strip().upper(), cells.get("asset", ""),
                               tx, cells["date"].strip(), cells.get("amount", "")))
        elif rows:                             # wrapped asset name / amount
            last = rows[-1]
            if cells.get("asset"):
                last.asset = f"{last.asset} {cells['asset']}".strip()
            if cells.get("amount"):
                last.amount = f"{last.amount} {cells['amount']}".strip()
    return rows


def rows_to_trades(rows: list[PtrRow], investor_key: str, filed: date, url: str) -> list[Trade]:
    out = []
    for r in rows:
        asset = " ".join(r.asset.split())
        kind_m = re.search(r"\[([A-Z]{2})\]", asset)
        kind = KIND.get(kind_m.group(1), kind_m.group(1)) if kind_m else ""
        name = asset[:kind_m.start()].strip() if kind_m else asset
        tickers = TICKER_RE.findall(name)
        ticker = next((t for t in reversed(tickers) if re.search(r"[A-Z]", t)), None)
        amount = " ".join(r.amount.split())
        text = amount + f"（{OWNER.get(r.owner, r.owner)}）" + (f" · {kind}" if kind else "")
        out.append(Trade(investor_key, datetime.strptime(r.date, "%m/%d/%Y").date(), filed,
                         TYPE[r.tx_type], "PTR", ticker, name, None, None, text, url))
    return out


Getter = Callable[[str, dict], bytes]


class HouseCollector:
    """New PTRs for watched members, parsed straight from the House Clerk."""

    def __init__(self, http_get: Getter):
        self.get = http_get
        self.errors: list[str] = []

    def collect(self, members: dict[str, str], since: date) -> list[Trade]:
        """``members`` maps a last name (case-insensitive) to our investor key."""
        want = {k.lower(): v for k, v in members.items()}
        trades: list[Trade] = []
        for year in sorted({since.year, date.today().year}):
            index = parse_index(self.get(INDEX_URL.format(year=year), {}), year)
            for row in index:
                key = want.get(row.last.lower())
                if not key or row.filing_type != "P" or not row.filed or row.filed < since:
                    continue
                try:
                    items = pdf_items(self.get(row.pdf_url, {}))
                    parsed = rows_to_trades(parse_ptr(items), key, row.filed, row.pdf_url)
                except Exception as exc:
                    self.errors.append(f"house {row.doc_id}: {exc}")
                    parsed = []
                if not parsed:      # scanned paper filing or unexpected layout
                    parsed = [Trade(key, row.filed, row.filed, "report", "PTR", None,
                                    "新的交易申報（需開 PDF 查看）", url=row.pdf_url)]
                trades += parsed
        return trades
