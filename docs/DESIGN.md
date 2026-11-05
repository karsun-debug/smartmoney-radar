# Smartmoney Radar 設計文件：投資大神「言」與「行」追蹤系統

## 1. 目標

1. **看他們做了什麼**：從 SEC 法定申報（13F、13D/G、Form 4）抓出實際的買賣動作。
2. **看他們說了什麼**：收集公開言論（X/Twitter、部落格、致股東信、訪談逐字稿），用 AI 抽出對個股/總經的看多看空觀點。
3. **比對言行**：自動標記「說到做到」、「嘴上看多卻在賣」等情況，並追蹤多位大神的共識與分歧。

> 這只是資訊整理工具，不構成投資建議。13F 有落後與盲點（見第 6 節），結論需人工判讀。

### 1.1 追蹤模式（主要用法）

軟體每天自動去抓「誰發了什麼」以及產業新聞，整理成一份摘要：

1. **大神最新動態**：時間軸列出每則發文/報導、來源連結、抽出的觀點（🟢 看多 / 🔴 看空）。
2. **產業熱度**：每個產業被幾位大神提到、多少產業新聞、整體偏多或偏空、與上一期相比熱度變化。
3. **共識標的**：兩位以上大神同時提到的個股與各自立場（例：GOOGL — Ackman 🟢、Wood 🟢；NVDA — Burry 🔴、Wood 🟢）。

## 2. 資料來源

| 類別 | 來源 | 時效 | 能看到什麼 | 成本 |
|---|---|---|---|---|
| 實際動作 | **13F-HR**（EDGAR） | 季末後 45 天內 | 美股多頭、選擇權（Put/Call）持倉 | 免費 |
| 實際動作 | **13D / 13G** | 跨越 5% 持股後 10 天內 | 大額/維權持股 | 免費 |
| 實際動作 | **Form 4** | 交易後 2 個營業日 | 內部人/董事交易（例：Buffett 對 OXY 加碼） | 免費 |
| 實際動作 | N-PORT（共同基金） | 每季，延遲 60 天 | 公募基金持倉 | 免費 |
| 言論 | X/Twitter（Ackman、Burry、Dalio…） | 即時 | 短評、觀點 | X API 需付費；或匯入 JSON |
| 言論 | 部落格 / Substack / LinkedIn（RSS） | 即時 | 長文觀點 | 免費 |
| 言論 | 致股東信、季報信（Berkshire、Pershing、Baupost） | 年/季 | 最完整的投資論述 | 免費 |
| 言論 | CNBC / Bloomberg 訪談、YouTube 逐字稿 | 即時 | 口頭觀點 | 需轉逐字稿 |

EDGAR 規範：User-Agent 必須帶聯絡資訊、每秒 ≤ 10 次請求。

## 3. 系統架構

```
 ┌──────────────┐   ┌───────────────┐
 │ 申報抓取器    │   │ 言論抓取器     │   排程（每日；13F 申報季每小時）
 │ edgar.py     │   │ posts.py      │
 └──────┬───────┘   └──────┬────────┘
        ▼                  ▼
 ┌──────────────┐   ┌───────────────┐
 │ 持倉解析/比較 │   │ 觀點抽取        │   Claude 結構化輸出：
 │ holdings.py  │   │ Keyword/Claude │   {ticker, stance, confidence, rationale}
 └──────┬───────┘   └──────┬────────┘
        └────────┬─────────┘
                 ▼
        ┌─────────────────┐
        │ 言行比對          │  consistency.py
        └────────┬────────┘
                 ▼
   報告（Markdown） → 之後：儀表板 / Telegram/LINE/Email 通知
```

### 模組（`spiderhead/`）

| 模組 | 職責 |
|---|---|
| `watchlist.py` | 追蹤名單（聲量高＋可對帳：Ackman、Wood、Burry、Buffett、Druckenmiller、Tepper、Dalio、Loeb、Saylor、Pelosi），可用 JSON 擴充或移除 |
| `sources.py` | 自動抓取貼文：X API v2、RSS/Atom、Google News（每位大神 + 每個產業）；單一來源失敗不影響其他 |
| `posts.py` | 貼文匯入與分析：`KeywordAnalyzer`（離線，含「引述本人」判斷）、`ClaudeAnalyzer`（結構化輸出）、`HybridAnalyzer`（先關鍵字、必要時才用 Claude，有預算上限） |
| `sectors.py` | 產業分類（AI/半導體、金融、能源、總經…），關鍵字與代號對照 |
| `edgar.py` | EDGAR API：列出申報、下載 13F 資訊表 / Form 4 / 8-K / 申報表頭、公司名→代號對照表 |
| `holdings.py` | 解析 13F XML（合併拆分列）、季對季比較、各季摘要、CUSIP→代號（SEC 名稱對照 + OpenFIGI 備援） |
| `trades.py` | 即時對帳單：Form 4、13D/G、Strategy 8-K 比特幣購買、國會交易（LuxAlgo 備援與參議員） |
| `house.py` | 眾議院書記處：年度索引 ZIP → PTR PDF → 依文字座標切欄解析表格 |
| `ark.py` | ARK 每日持股檔比對，扣除 ETF 申購贖回後得出每日買賣 |
| `store.py` | SQLite：貼文、觀點、交易、13F 歷史、ARK 持股快照、股價、通知紀錄、資料來源紀錄；舊資料庫自動升級欄位 |
| `trends.py` | 產業熱度（含與上一期比較）、多位大神共識標的 |
| `consistency.py` | 言行比對：觀點 vs 13F 季度變化、觀點 vs 申報交易（前 30 天到後 90 天） |
| `performance.py` | 喊單勝率：Stooq 日線股價、30/90 天報酬、7 天內重複報導只算一次 |
| `digest.py` | 每日摘要資料（動態、產業熱度、共識、對帳單、來源狀態）與大神個人資料 |
| `notify.py` / `alerts.py` | LINE Messaging API 推播：每日摘要、即時通知（每則只送一次，首次執行只記錄） |
| `dashboard.py` | 靜態網頁儀表板（總覽、每位大神、每檔個股；內嵌 SVG 圖表、深色模式） |
| `health.py` / `doctor.py` | 資料來源成功/失敗紀錄與 `spiderhead doctor` 設定檢查 |
| `consensus13f.py` | 大神共同持股（類 Dataroma）：最多人持有、本季最多人買／賣 |
| `clone.py` | 13F 跟單回測：公布日買進前 10 大持股、持有到下一季公布，與 SPY 比較 |
| `insiders.py` | 全市場 Form 4（SEC 每日索引）→ 公開市場買進 → 14 天內 3 位以上內部人的群買 |
| `retail.py` | Reddit 散戶熱度與情緒，對照大神看法（散戶資料不混入大神趨勢） |
| `reportcard.py` | 大神評分卡：說到做到、喊單準確、跟單價值 |
| `channels.py` | 通知管道：LINE、Telegram、Discord、Slack、Email |
| `weekly.py` | 每週週報（Claude 撰寫，或範本） |
| `export.py` | 開放資料：JSON / CSV / RSS |
| `mcp_server.py` | MCP 伺服器，讓 AI 助理唯讀查詢資料庫 |
| `report.py` | Markdown 報告 |
| `cli.py` | `spiderhead fetch / alert / push-line / dashboard / digest / feed / trends / doctor / verify-ciks / holdings / report …` |

## 4. 分析邏輯

### 4.1 持倉變化
- 依 `(CUSIP, Put/Call)` 彙總；以股數判斷動作（不受股價漲跌影響）。
- 依「估計成交金額」排序，最重要的動作排最前。
- 占組合比重 = 本季市值 / 本季總市值。

### 4.2 觀點抽取（Claude）
- 系統提示要求：解析暱稱與公司名、區分「作者本人觀點」與「引用/嘲諷」、給出可信度。
- 使用 JSON Schema 結構化輸出，保證欄位正確；開啟 `fallbacks: "default"`，若遭安全分類器拒絕會自動改由後備模型處理。
- 關鍵字分析器只作離線備援：例如「we never sell KO」會被誤判為看空，這正是需要 LLM 的原因。

### 4.3 言行比對
| 公開看法 | 該季動作 | 判定 |
|---|---|---|
| 看多 | 新建倉 / 加碼 | ✅ 說到做到 |
| 看多 | 減碼 / 清倉 | ⚠️ 言行不一 |
| 看空 | 買入 Put | ✅ 說到做到 |
| 任一 | 持股不變 | ➖ 持有未動 |
| 任一 | 13F 中無此標的 | ❔ 查無（可能未持有、空單、非美股） |

## 5. 路線圖

- **MVP**：CLI、13F 比較、貼文分析、言行報告、離線測試。
- **v0.2（本次）**：自動抓取發文與產業新聞、SQLite 儲存、產業熱度、共識標的、摘要報告。
- **v0.3（本次）**：LINE 推播、網頁儀表板、Form 4 / 13D/G / 國會交易「即時對帳單」、喊單勝率、個股頁、OpenFIGI。
- **v0.4（本次）**：ARK 每日交易（持股檔比對、扣除 ETF 申購贖回）、即時通知（每 2 小時）、`spiderhead doctor` 設定檢查、設定教學。
- **之後**：13F 持倉歷史存庫（看多季趨勢）、Saylor 的 8-K 比特幣購買解析、直接解析眾議院/參議院申報（不依賴第三方資料集）、X 貼文截圖/圖表辨識。
- **v0.3**：排程（cron / GitHub Actions），新申報或新貼文時推播（LINE / Telegram / Email）。
- **v0.4**：網頁儀表板 —— 大神共識熱力圖（多人同時加碼的標的）、個人言行一致率排行、申報後股價表現回測。
- **v0.5**：訪談影片自動轉逐字稿；用 Claude 摘要致股東信每段觀點並標出處。

## 6. 限制與注意

- 13F **落後最多 135 天**（季初交易、季末後 45 天才申報），看到時可能已出場。
- 13F **看不到**：放空、非美股、債券多數、現金、低於門檻的部位；Put/Call 只列名目，不代表避險或投機。
- 季對季只看到「淨變化」，看不到季中來回操作。
- 貼文觀點可能是說書、反串或替持倉造勢，因此「言行不一」本身就是有價值的訊號。
- 抓取 X 等平台要遵守其服務條款；建議用官方 API 或手動匯出 JSON。


## 7. 與 GitHub 同類專案比較（2026-11 調查）

| 專案 | 做什麼 | 我們借鏡的地方 |
|---|---|---|
| [dokson/hedge-fund-tracker](https://github.com/dokson/hedge-fund-tracker) | 13F + 13D/G + Form 4 合併、React 網頁、GitHub Pages、AI 選股 | 不只看 13F：加入 **Form 4 / 13D/G** 做「即時對帳單」；CUSIP→代號用 **OpenFIGI** 備援；每檔個股一頁 |
| [LuxAlgo/market-trackers](https://github.com/LuxAlgo/market-trackers) | 國會交易、內部人、13F 等 18 種公開資料，每列附原始文件連結，CC0 每日資料包 | **國會議員交易**（直接讀它的 CC0 資料包）；每筆交易都附 **原始申報連結** |
| [StephanAkkerman/fintwit-bot](https://github.com/StephanAkkerman/fintwit-bot) | 追蹤 X 財經推文、情緒分析、熱門代號，推到 Discord | 社群貼文 → 情緒 → 推播的流程（我們用 Claude + LINE） |
| [wescules/insider-trading-analyzer](https://github.com/wescules/insider-trading-analyzer) | Form 4 篩選、回測內部人買進後的報酬 | **喊單後 30/90 天報酬與勝率** |

Smartmoney Radar 的定位差異：其他專案只看「申報」或只看「貼文」，我們把兩者接起來——
**誰在網路上喊了什麼 → 他實際申報買賣了什麼 → 喊完之後股價怎麼走**。

### 即時對帳單的來源與時效

| 來源 | 誰適用 | 時效 |
|---|---|---|
| Form 4 | 持股 >10% 或公司內部人（例：Berkshire 買 OXY） | 交易後 2 個工作日 |
| Schedule 13D/G | 持股跨過 5%（例：Pershing Square、Third Point 的維權持股） | 13D 5 個工作日 |
| 國會交易申報（PTR） | 國會議員與配偶（例：Pelosi） | 45 天內。眾議員直接讀眾議院書記處的年度索引與 PDF（自行解析表格；紙本掃描件會發「提交新申報」通知附 PDF 連結）；參議員與備援用 LuxAlgo 資料集 |
| ARK 每日持股檔 | Cathie Wood（ARK 各檔 ETF） | 每個交易日 |
| 8-K（比特幣購買公告） | Michael Saylor（Strategy） | 通常買入後隔個工作日 |
| 13F | 管理資產 > 1 億美元的機構 | 季末後 45 天 |

### 喊單勝率的算法

- 只算**本人發言**、可信度 ≥ 0.5、明確看多或看空**個股**的發言；同一人同一天同一檔只算一次。
- 進場價 = 發言當日（或下一個交易日）收盤價；30 / 90 天後收盤價比較。
- 看多後上漲、看空後下跌算「對」；平均報酬依方向調整。
- 未計股利、交易成本；發言未滿天數不計入。股價來自 Stooq 免費日線。
