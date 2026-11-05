# Smartmoney Radar 大神雷達

> 指令名稱仍是 `spiderhead`，環境變數仍以 `SPIDERHEAD_` 開頭（沿用舊名，已設定的 Secrets 不用改）。

自動追蹤美國投資大神（Buffett、Ackman、Burry、Dalio、Cathie Wood、Howard Marks…）**最近說了什麼**、**哪些產業正在被熱議**，並可對照 **SEC 申報的實際持倉**，檢查「說的」跟「做的」是否一致。

主要功能：
- **自動抓取**：每位大神的 X 貼文（需 X API）、部落格/Substack/YouTube RSS、Google News 報導，以及各產業新聞。
- **觀點分析**：抽出提到的個股、產業、看多/看空（關鍵字或 Claude）。
- **產業熱度**：哪些產業被最多大神談到、偏多還是偏空、和上一期比熱度變化。
- **共識標的**：兩位以上大神同時在談的個股，以及他們各自的立場。
- **即時對帳單**：Form 4、13D/G、國會議員交易申報（Pelosi 等），每筆附原始文件連結。
- **言行比對**：發言 vs 實際申報交易，以及 vs 13F 季度持倉。
- **喊單勝率**：發言後 30 / 90 天股價表現，排出誰最準。
- **ARK 每日交易**：比對 ARK 每天公布的持股檔，算出 Cathie Wood 當天買賣了什麼（已扣除 ETF 申購贖回造成的整體變動）。
- **LINE 推播**：每天早上收到摘要；有新喊單或新申報交易時**即時通知**（每 2 小時檢查一次）。
- **網頁儀表板**：產業熱度變化圖、每位大神一頁、每檔個股一頁，手機可看、支援深色模式。

與 GitHub 上同類專案的比較見 [docs/DESIGN.md](docs/DESIGN.md#7-與-github-同類專案比較2026-11-調查)。

設計細節見 [docs/DESIGN.md](docs/DESIGN.md)。

> **第一次使用請看 [docs/SETUP.md](docs/SETUP.md)**：一步一步設定 LINE、GitHub 自動執行，並用 `spiderhead doctor` 檢查。

## 安裝

```bash
pip install -e ".[llm,dev]"
```

## 使用

SEC 要求 User-Agent 帶聯絡方式：

```bash
export SPIDERHEAD_USER_AGENT="Smartmoney Radar your@email.com"
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
crontab -e   # 加入：0 8 * * * cd /path/to/smartmoney-radar && spiderhead fetch && spiderhead digest --out digest.md
```

`sources.example.json` 示範如何加 RSS、改搜尋關鍵字、關掉某個來源（設為 `null`）。

### LINE 推播

1. 到 [LINE Developers](https://developers.line.biz/) 建立 Messaging API channel（會同時建立一個 LINE 官方帳號），用手機加它好友。
2. 在 channel 設定頁發行 **Channel access token (long-lived)**。
3. 設定環境變數後推播（`LINE_TO` 可填你的 userId；不填會廣播給所有好友）：

```bash
export LINE_CHANNEL_ACCESS_TOKEN="你的 token"
```

```bash
spiderhead push-line --days 1 --dry-run
```

確認內容後拿掉 `--dry-run` 就會送出。

即時通知（新喊單、新申報交易，每則只送一次；第一次執行只記錄不送，避免一次湧入歷史資料）：

```bash
spiderhead fetch --days 2 && spiderhead alert
```

### 網頁儀表板

```bash
spiderhead dashboard --out site --days 90 --with-13f
```

打開 `site/index.html` 即可。沒有網路時加 `--no-prices`。想先看效果可以用假資料：

```bash
SPIDERHEAD_DB=data/demo.db python examples/demo_data.py && SPIDERHEAD_DB=data/demo.db spiderhead dashboard --out site-demo
```

### 每天自動執行（GitHub Actions）

`.github/workflows/daily.yml` 每天台灣時間 07:17 會：抓資料 → 產生儀表板發佈到 GitHub Pages → 推 LINE。
到 repo 的 Settings → Secrets and variables → Actions 加入 `SPIDERHEAD_USER_AGENT`、`LINE_CHANNEL_ACCESS_TOKEN`（選填：`LINE_TO`、`ANTHROPIC_API_KEY`、`X_BEARER_TOKEN`），
再到 Settings → Pages 把來源設為 GitHub Actions。免費方案的 Pages 網站是公開的。

### 持倉與言行比對

追蹤名單挑選標準：**網路聲量高**，而且**有官方申報可以對帳**。預設 10 位：Ackman、Cathie Wood、Burry、Buffett、Druckenmiller、Tepper、Dalio、Loeb、Saylor、Pelosi。
`watchlist.example.json` 示範如何加上段永平、李錄，或移除某人。第一次使用請確認 CIK：

```bash
spiderhead verify-ciks
```

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
