"""Default watchlist: investors with a loud public voice AND trades you can
check against official filings ("對帳單").

Selection rule: the person posts or is quoted often (X, CNBC, letters) and the
fund files with the SEC, so what they say can be compared with what they did.

CIKs are for the filing entity. Run ``spiderhead verify-ciks`` once to confirm
each CIK still resolves to the expected fund name on EDGAR — funds sometimes
re-register. Users can override or extend the list with a JSON file (see
``load_watchlist`` and ``watchlist.example.json``).
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import Investor

DEFAULT_WATCHLIST: list[Investor] = [
    Investor("ackman", "Bill Ackman", "Pershing Square Capital Management", "0001336528",
             {"x": "BillAckman"}, "13F",
             "X 上最活躍的避險基金經理之一，常公開長文說明持倉；集中持股，13F 很好對照。"),
    Investor("wood", "Cathie Wood", "ARK Investment Management", "0001697748",
             {"x": "CathieDWood", "ark": "ARKK,ARKQ,ARKW,ARKG,ARKF,ARKX"}, "13F+daily",
             "除 13F 外，ARK ETF 每天公布交易明細，是最即時的對帳單。"),
    Investor("burry", "Michael Burry", "Scion Asset Management", "0001649339",
             {"x": "michaeljburry"}, "13F",
             "《大賣空》原型，發文常刪文。Scion 於 2025 年底撤銷投顧登記，之後可能不再申報 13F。"),
    Investor("buffett", "Warren Buffett", "Berkshire Hathaway Inc", "0001067983",
             {}, "13F",
             "不發社群，但致股東信與股東會是全球關注焦點；2026 年起由 Greg Abel 接任執行長。"),
    Investor("druckenmiller", "Stanley Druckenmiller", "Duquesne Family Office", "0001536411",
             {}, "13F", "CNBC / Bloomberg 訪談常引發話題，13F 換股幅度大。"),
    Investor("tepper", "David Tepper", "Appaloosa Management", "0001656456",
             {}, "13F", "CNBC 訪談對市場影響大，常大舉押注中國與科技股。"),
    Investor("dalio", "Ray Dalio", "Bridgewater Associates", "0001350694",
             {"x": "RayDalio"}, "13F",
             "X / LinkedIn 大量總經評論；已退出橋水日常管理，13F 反映的是橋水而非他個人。"),
    Investor("loeb", "Dan Loeb", "Third Point LLC", "0001040273",
             {}, "13F", "以公開信施壓公司聞名的維權投資人，季信觀點與 13F 可互相對照。"),
    Investor("saylor", "Michael Saylor", "Strategy Inc (MicroStrategy)", "0001050446",
             {"x": "saylor"}, "8-K",
             "幾乎每週在 X 預告買比特幣，隔天以 8-K 申報實際買入量與均價，系統會自動解析。"),
    Investor("pelosi", "Nancy Pelosi", "美國眾議員（前議長）", "",
             {"congress": "Pelosi"}, "STOCK Act",
             "網路上被追蹤最多的「國會交易」，多為配偶 Paul Pelosi 的交易；依 STOCK Act 須在 45 天內申報。"
             "已宣布不再競選連任，任期至 2027 年 1 月。"),
]


def load_watchlist(path: str | Path | None = None) -> dict[str, Investor]:
    """Return investors keyed by ``Investor.key``.

    A JSON file, if given, is a list of objects with the Investor fields; its
    entries are added to (and override) the defaults. ``{"key": "x", "remove": true}``
    drops a default entry.
    """
    investors = {inv.key: inv for inv in DEFAULT_WATCHLIST}
    if path:
        for raw in json.loads(Path(path).read_text(encoding="utf-8")):
            if raw.get("remove"):
                investors.pop(raw["key"], None)
                continue
            inv = Investor(
                key=raw["key"], name=raw["name"], fund=raw.get("fund", raw["name"]),
                cik=str(raw["cik"]).zfill(10) if raw.get("cik") else "",
                handles=raw.get("handles", {}), proof=raw.get("proof", "13F"),
                note=raw.get("note", ""),
            )
            investors[inv.key] = inv
    return investors
