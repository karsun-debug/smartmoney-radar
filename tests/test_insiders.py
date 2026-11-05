from datetime import date

from spiderhead.insiders import InsiderScanner, find_clusters, parse_form_index, parse_submission
from spiderhead.store import Store

INDEX = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    November 4, 2026

Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
3           SOMEBODY NEW                                                  1111111     20261104    edgar/data/1111111/0001111111-26-000001.txt
4           ACME ROBOTICS INC                                             2222222     20261104    edgar/data/2222222/0002222222-26-000010.txt
4           DOE JANE                                                      3333333     20261104    edgar/data/2222222/0002222222-26-000010.txt
4           ACME ROBOTICS INC                                             2222222     20261104    edgar/data/2222222/0002222222-26-000011.txt
4           ACME ROBOTICS INC                                             2222222     20261104    edgar/data/2222222/0002222222-26-000012.txt
4/A         OTHER CO                                                      4444444     20261104    edgar/data/4444444/0004444444-26-000002.txt
SC 13D      BIG FUND LP                                                   5555555     20261104    edgar/data/5555555/0005555555-26-000003.txt
"""


def submission(owner, title_xml, code="P", shares="10000", price="25.50", ticker="ACME"):
    return f"""<SEC-DOCUMENT>
<DOCUMENT><TYPE>4<TEXT>
<XML>
<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>2222222</issuerCik><issuerName>Acme Robotics Inc</issuerName>
    <issuerTradingSymbol>{ticker}</issuerTradingSymbol></issuer>
  <reportingOwner><reportingOwnerId><rptOwnerName>{owner}</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>{title_xml}</reportingOwnerRelationship></reportingOwner>
  <nonDerivativeTable><nonDerivativeTransaction>
    <transactionDate><value>2026-11-03</value></transactionDate>
    <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
    <transactionAmounts><transactionShares><value>{shares}</value></transactionShares>
      <transactionPricePerShare><value>{price}</value></transactionPricePerShare></transactionAmounts>
  </nonDerivativeTransaction></nonDerivativeTable>
</ownershipDocument>
</XML>
</TEXT></DOCUMENT></SEC-DOCUMENT>"""


DOCS = {
    "0002222222-26-000010": submission("Doe Jane", "<isOfficer>1</isOfficer><officerTitle>CEO</officerTitle>"),
    "0002222222-26-000011": submission("Roe Rich", "<isDirector>true</isDirector>"),
    "0002222222-26-000012": submission("Poe Pat", "<isDirector>1</isDirector><isTenPercentOwner>1</isTenPercentOwner>"),
}


def test_parse_form_index_dedupes_and_filters():
    rows = parse_form_index(INDEX)
    assert [p.split("/")[-1] for _, p in rows] == ["0002222222-26-000010.txt", "0002222222-26-000011.txt",
                                                    "0002222222-26-000012.txt"]
    assert len(parse_form_index(INDEX, ("4", "4/A"))) == 4


def test_parse_submission_titles_and_codes():
    [b] = parse_submission(DOCS["0002222222-26-000010"], "p", date(2026, 11, 4))
    assert (b.ticker, b.owner, b.title, b.shares, b.price) == ("ACME", "Doe Jane", "CEO", 10000, 25.5)
    assert parse_submission(submission("X", "", code="S"), "p", date(2026, 11, 4)) == []
    assert parse_submission(submission("X", "", ticker="NONE"), "p", date(2026, 11, 4)) == []
    assert parse_submission("no xml here", "p", date(2026, 11, 4)) == []


def test_scanner_and_clusters():
    store = Store(":memory:")
    calls = []

    def get(url):
        calls.append(url)
        if url.endswith("form.20261104.idx"):
            return INDEX
        if "daily-index" in url:
            raise FileNotFoundError("404")
        acc = url.rsplit("/", 1)[-1].removesuffix(".txt")
        return DOCS[acc]
    scanner = InsiderScanner(store, get)
    assert scanner.scan(days=2, today=date(2026, 11, 4)) == 3        # Mon 11/2 + Tue 11/3 404, Wed 11/4 ok
    assert store.insider_day_done(date(2026, 11, 4))
    n = len(calls)
    scanner.scan(days=0, today=date(2026, 11, 4))
    assert len(calls) == n                                            # day already processed
    [c] = find_clusters(store.insider_buys_between(date(2026, 10, 20), date(2026, 11, 4)))
    assert c.ticker == "ACME" and len(c.insiders) == 3 and round(c.value) == 765_000
    assert find_clusters(store.insider_buys_between(date(2026, 10, 20), date(2026, 11, 4)), min_insiders=4) == []


def test_clusters_in_alerts_digest_and_dashboard(tmp_path):
    from datetime import timedelta
    from spiderhead.alerts import run_alerts
    from spiderhead.dashboard import build_site
    from spiderhead.digest import build_digest
    from spiderhead.insiders import InsiderBuy
    from spiderhead.notify import format_line
    from spiderhead.watchlist import load_watchlist
    today = date.today()
    store = Store(":memory:")
    store.add_insider_buys([InsiderBuy(f"p{i}", "ACME", "Acme Robotics Inc", f"Owner {i}", "Director",
                                       today - timedelta(days=i), today, 10_000, 20.0) for i in range(3)])
    inv, sent = load_watchlist(), []
    store.mark_alerted([("__alerts_initialized__", "meta")])
    n, text = run_alerts(store, inv, sent.append)
    assert n == 1 and "🏛 內部人群買 ACME：3 位內部人買進 $0.60M" in text
    assert run_alerts(store, inv, sent.append) == (0, "")
    assert "🏛 內部人群買" in format_line(build_digest(store, inv, 1))
    build_site(store, inv, tmp_path, days=7)
    assert "Owner 2" in (tmp_path / "insiders.html").read_text(encoding="utf-8")
    assert (tmp_path / "ticker" / "ACME.html").exists()
