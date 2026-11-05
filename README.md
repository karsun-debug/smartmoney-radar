# SpiderHead

自動追蹤美國投資大神（Buffett、Ackman、Burry、Dalio、Cathie Wood、Howard Marks…）**最近說了什麼**、**哪些產業正在被熱議**，並可對照 **SEC 申報的實際持倉**，檢查「說的」跟「做的」是否一致。

主要功能：
- **自動抓取**：每位大神的 X 貼文（需 X API）、部落格/Substack/YouTube RSS、Google News 報導，以及各產業新聞。
- **觀點分析**：抽出提到的個股、產業、看多/看空（關鍵字或 Claude）。
- **產業熱度**：哪些產業被最多大神談到、偏多還是偏空、和上一期比熱度變化。
- **共識標的**：兩位以上大神同時在談的個股，以及他們各自的立場。
- **言行比對**：觀點 vs 13F 持倉變化。

設計細節見 [docs/DESIGN.md](docs/DESIGN.md)。

## 安裝

```bash
pip install -e ".[llm,dev]"
```

## 使用

SEC 要求 User-Agent 帶聯絡方式：

```bash
export SPIDERHEAD_USER_AGENT="SpiderHead your@email.com"
```

### 追蹤發文與產業動態

自動抓取最近 7 天的發文與產業新聞，存進 `data/spiderhead.db` 並分析（加 `--llm` 用 Claude；有 `X_BEARER_TOKEN` 才會抓 X）：

```bash
spiderhead fetch --days 7 --sources sources.example.json
```

看摘要（大神最新動態 + 產業熱度 + 共識標的），可輸出成 Markdown 檔：

```bash
spiderhead digest --days 7 --out digest.md
```

只看某位大神，或只看產業熱度：

```bash
spiderhead feed --investor ackman --days 30
```

```bash
spiderhead trends --days 7
```

手動整理的貼文（訪談、截圖抄錄）也可以匯入：

```bash
spiderhead import my_posts.json
```

想每天自動更新，用 cron 排程即可：

```bash
crontab -e   # 加入：0 8 * * * cd /path/to/SpiderHead && spiderhead fetch && spiderhead digest --out digest.md
```

`sources.example.json` 示範如何加 RSS、改搜尋關鍵字、關掉某個來源（設為 `null`）。

### 持倉與言行比對

列出追蹤名單：

```bash
spiderhead list
```

最新一季 13F 持倉變化（從 EDGAR 抓）：

```bash
spiderhead holdings buffett
```

分析貼文（加 `--llm` 改用 Claude，需設定 `ANTHROPIC_API_KEY`）：

```bash
spiderhead posts tests/fixtures/posts.json --llm
```

言行一致報告（離線範例資料）：

```bash
spiderhead report buffett tests/fixtures/posts.json --prev-xml tests/fixtures/prev_q.xml --curr-xml tests/fixtures/curr_q.xml --tickers tests/fixtures/tickers.json
```

貼文 JSON 格式：`{"investor": "ackman", "date": "2026-10-01", "text": "...", "source": "x", "url": "...", "kind": "own"}`，可以是陣列或 JSONL。`kind`：`own` 本人發言、`news` 關於他的報導、`industry` 產業新聞。

## 測試

```bash
pytest -q
```

> 僅供研究參考，不構成投資建議。
