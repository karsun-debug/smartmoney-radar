import io
import zipfile
from datetime import date, timedelta

import pytest

from spiderhead.house import HouseCollector, Item, parse_index, parse_ptr, pdf_items, rows_to_trades

TODAY = date.today()

HEADER = [("ID", 46), ("Owner", 90), ("Asset", 130), ("Transaction", 342), ("Date", 410),
          ("Notification", 462), ("Amount", 524), ("Cap.", 578)]
HEADER2 = [("Type", 342), ("Date", 462), ("Gains >", 578)]

# (page, y, [(text, x), ...]) — two-page PTR with wrapped names/amounts and annotations.
LAYOUT = [
    (1, 760, [("PERIODIC TRANSACTION REPORT", 200)]),
    (1, 684, [("Name:", 46), ("Hon. Nancy Pelosi", 90)]),
    (1, 600, HEADER), (1, 588, HEADER2),
    (1, 560, [("SP", 90), ("NVIDIA Corporation - Common", 130), ("P", 342), ("01/14/2026", 410),
              ("01/20/2026", 462), ("$1,000,001 -", 524)]),
    (1, 548, [("Stock (NVDA) [ST]", 130), ("$5,000,000", 524)]),
    (1, 536, [("FILING STATUS: New", 130)]),
    (1, 512, [("SP", 90), ("Tesla, Inc. - Common Stock (TSLA)", 130), ("S (partial)", 342),
              ("01/15/2026", 410), ("01/20/2026", 462), ("$250,001 -", 524)]),
    (2, 700, HEADER), (2, 688, HEADER2),
    (2, 660, [("[ST]", 130), ("$500,000", 524)]),
    (2, 636, [("JT", 90), ("Alphabet Inc. - Class A (GOOGL)", 130), ("P", 342), ("01/16/2026", 410),
              ("01/20/2026", 462), ("$1,001 - $15,000", 524)]),
    (2, 624, [("[OP]", 130)]),
    (2, 612, [("DESCRIPTION: Purchased 20 call options, strike $150.", 130)]),
    (2, 576, [("* For the complete list of asset type abbreviations, please visit https://fd.house.gov/", 46)]),
    (2, 540, [("CERTIFICATION AND SIGNATURE", 46)]),
    (2, 524, [("Digitally Signed: Hon. Nancy Pelosi , 01/20/2026", 46)]),
]


def _items():
    return [Item(t, x, y, p) for p, y, parts in LAYOUT for t, x in parts]


def _pdf() -> bytes:
    reportlab = pytest.importorskip("reportlab")  # noqa: F841
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    page = 1
    for p, y, parts in LAYOUT:
        while p > page:
            c.showPage()
            page += 1
        c.setFont("Helvetica", 7)
        for t, x in parts:
            c.drawString(x, y, t)
    c.save()
    return buf.getvalue()


def _check(rows):
    trades = rows_to_trades(rows, "pelosi", date(2026, 1, 20), "https://pdf")
    assert [(t.ticker, t.side, t.traded) for t in trades] == [
        ("NVDA", "buy", date(2026, 1, 14)), ("TSLA", "sell", date(2026, 1, 15)),
        ("GOOGL", "buy", date(2026, 1, 16))]
    nvda, tsla, googl = trades
    assert nvda.amount_text == "$1,000,001 - $5,000,000（配偶）"
    assert nvda.issuer == "NVIDIA Corporation - Common Stock (NVDA)"
    assert tsla.amount_text == "$250,001 - $500,000（配偶）"          # wrapped across pages
    assert googl.amount_text == "$1,001 - $15,000（共同） · 選擇權"
    assert "DESCRIPTION" not in googl.issuer


def test_parse_ptr_positioned_items():
    _check(parse_ptr(_items()))


def test_parse_ptr_real_pdf_bytes():
    _check(parse_ptr(pdf_items(_pdf())))


INDEX = b"""<?xml version="1.0"?><FinancialDisclosure>
<Member><Prefix>Hon.</Prefix><Last>Pelosi</Last><First>Nancy</First><FilingType>P</FilingType>
<FilingDate>%s</FilingDate><DocID>20030001</DocID></Member>
<Member><Prefix>Hon.</Prefix><Last>Pelosi</Last><First>Nancy</First><FilingType>P</FilingType>
<FilingDate>%s</FilingDate><DocID>8220002</DocID></Member>
<Member><Last>Pelosi</Last><First>Nancy</First><FilingType>O</FilingType>
<FilingDate>%s</FilingDate><DocID>10030003</DocID></Member>
<Member><Last>Smith</Last><First>Al</First><FilingType>P</FilingType>
<FilingDate>%s</FilingDate><DocID>20030004</DocID></Member>
<Member><Last>Broken</Last><FilingType>P</FilingType><FilingDate></FilingDate><DocID/></Member>
</FinancialDisclosure>"""


def _zip(xml: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{TODAY.year}FD.xml", xml)
        zf.writestr(f"{TODAY.year}FD.txt", "ignored")
    return buf.getvalue()


def test_parse_index_zip():
    d = f"{TODAY:%m/%d/%Y}".encode()
    rows = parse_index(_zip(INDEX % (d, d, d, d)), TODAY.year)
    assert len(rows) == 4 and rows[0].filing_type == "P" and rows[0].filed == TODAY
    assert rows[0].pdf_url.endswith(f"/ptr-pdfs/{TODAY.year}/20030001.pdf")


def test_house_collector_parses_and_flags_paper_filings():
    d = f"{TODAY:%m/%d/%Y}".encode()
    old = f"{TODAY - timedelta(days=400):%m/%d/%Y}".encode()
    pdf = _pdf()

    def get(url, headers):
        if url.endswith("FD.zip"):
            return _zip(INDEX % (d, d, d, old))
        if url.endswith("20030001.pdf"):
            return pdf
        if url.endswith("8220002.pdf"):
            return b"%PDF-1.4 scanned image without text"
        raise AssertionError(f"unexpected fetch {url}")

    col = HouseCollector(get)
    trades = col.collect({"Pelosi": "pelosi"}, TODAY - timedelta(days=30))
    assert {t.ticker for t in trades if t.side != "report"} == {"NVDA", "TSLA", "GOOGL"}
    [paper] = [t for t in trades if t.side == "report"]
    assert paper.url.endswith("8220002.pdf") and paper.investor_key == "pelosi"
