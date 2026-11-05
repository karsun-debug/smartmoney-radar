"""Minimal SEC EDGAR client for 13F-HR, 13D/G and Form 4 filings.

SEC rules: send a descriptive User-Agent with contact info and stay under
10 requests/second. Set ``SPIDERHEAD_USER_AGENT`` (e.g. "SpiderHead you@mail.com").
"""
from __future__ import annotations

import os
import time
from datetime import date

import requests

from .models import Filing

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/index.json"
ARCHIVE_FILE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{name}"
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Forms that reveal real trades, fastest first.
FORM_4 = "4"                     # insider trade, due within 2 business days
FORM_13D = ("SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A")   # >5% activist stake, 10 days
FORM_13G = ("SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A")   # >5% passive stake
FORM_13F = ("13F-HR", "13F-HR/A")  # quarterly holdings, due 45 days after quarter end


class EdgarClient:
    def __init__(self, user_agent: str | None = None, min_interval: float = 0.15,
                 session: requests.Session | None = None):
        ua = user_agent or os.environ.get("SPIDERHEAD_USER_AGENT")
        if not ua:
            raise ValueError("SEC requires a User-Agent with contact info: set "
                             "SPIDERHEAD_USER_AGENT='SpiderHead your@email'")
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
        self.min_interval = min_interval
        self._last = 0.0

    def _get(self, url: str) -> requests.Response:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        resp = self.session.get(url, timeout=30)
        self._last = time.monotonic()
        resp.raise_for_status()
        return resp

    def list_filings(self, cik: str, forms: tuple[str, ...] | str = FORM_13F,
                     limit: int = 8) -> list[Filing]:
        """Most recent filings of the given form types, newest first."""
        forms = (forms,) if isinstance(forms, str) else forms
        cik = str(cik).zfill(10)
        recent = self._get(SUBMISSIONS_URL.format(cik=cik)).json()["filings"]["recent"]
        out: list[Filing] = []
        for i, form in enumerate(recent["form"]):
            if form not in forms:
                continue
            report = recent.get("reportDate", [""] * len(recent["form"]))[i]
            out.append(Filing(
                cik=cik, accession=recent["accessionNumber"][i], form=form,
                filing_date=date.fromisoformat(recent["filingDate"][i]),
                report_date=date.fromisoformat(report) if report else None,
                primary_doc=recent.get("primaryDocument", [""] * len(recent["form"]))[i],
            ))
            if len(out) >= limit:
                break
        return out

    def latest_13f_per_quarter(self, cik: str, quarters: int = 2) -> list[Filing]:
        """One filing per report quarter (amendments replace originals), newest first."""
        by_quarter: dict[date | None, Filing] = {}
        for f in self.list_filings(cik, FORM_13F, limit=quarters * 3):
            by_quarter.setdefault(f.report_date, f)   # list is newest first
        return sorted(by_quarter.values(), key=lambda f: f.report_date or date.min,
                      reverse=True)[:quarters]

    def fetch_infotable_xml(self, filing: Filing) -> bytes:
        """Download the information-table XML attached to a 13F filing."""
        cik_int = int(filing.cik)
        acc = filing.accession.replace("-", "")
        index = self._get(ARCHIVE_INDEX_URL.format(cik_int=cik_int, acc=acc)).json()
        names = [item["name"] for item in index["directory"]["item"]]
        xmls = [n for n in names if n.lower().endswith(".xml") and n != "primary_doc.xml"]
        for name in xmls:
            body = self._get(ARCHIVE_FILE_URL.format(cik_int=cik_int, acc=acc, name=name)).content
            if b"informationTable" in body or b"infoTable" in body:
                return body
        raise LookupError(f"No information table found in {filing.accession}")

    def filing_index_url(self, filing: Filing) -> str:
        acc = filing.accession.replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{int(filing.cik)}/{acc}/{filing.accession}-index.htm"

    def fetch_primary_xml(self, filing: Filing) -> bytes:
        """Raw XML of an XML-based filing (Form 4). The submissions API points at
        the XSL-rendered copy (``xslF345X05/doc.xml``); the raw file sits one level up."""
        name = filing.primary_doc.rsplit("/", 1)[-1]
        acc = filing.accession.replace("-", "")
        return self._get(ARCHIVE_FILE_URL.format(cik_int=int(filing.cik), acc=acc, name=name)).content

    def fetch_header(self, filing: Filing) -> str:
        """SGML header (has SUBJECT COMPANY for 13D/G)."""
        acc = filing.accession.replace("-", "")
        url = ARCHIVE_FILE_URL.format(cik_int=int(filing.cik), acc=acc,
                                      name=f"{filing.accession}-index-headers.html")
        return self._get(url).text

    def entity_name(self, cik: str) -> str:
        """Registered name of the entity behind a CIK (for checking the watchlist)."""
        return self._get(SUBMISSIONS_URL.format(cik=str(cik).zfill(10))).json().get("name", "")

    def company_name_map(self) -> dict[str, str]:
        """SEC's issuer-name -> ticker table, for ``TickerResolver``."""
        data = self._get(COMPANY_TICKERS_URL).json()
        return {row["title"]: row["ticker"] for row in data.values()}
