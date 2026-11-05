# SpiderHead

追蹤美國投資大神（Buffett、Ackman、Burry、Druckenmiller、Dalio…）的**公開言論**與 **SEC 申報的實際持倉**，自動比對「說的」跟「做的」是否一致。

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

貼文 JSON 格式：`{"investor": "ackman", "date": "2026-10-01", "text": "...", "source": "x", "url": "..."}`，可以是陣列或 JSONL。

## 測試

```bash
pytest -q
```

> 僅供研究參考，不構成投資建議。
