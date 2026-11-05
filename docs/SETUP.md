# Smartmoney Radar 設定教學（一步一步）

整個流程約 20–30 分鐘。每完成一段，就跑一次 `spiderhead doctor`，它會告訴你哪裡還沒好。

```
✅ SEC User-Agent  SpiderHead you@example.com
✅ SEC EDGAR       BERKSHIRE HATHAWAY INC 連線正常
✅ LINE token      官方帳號「大神通知」(@123abcde) · 推給 Uxxxxxxxx
...
全部就緒 🎉
```

---

## 步驟 0：把程式合併到 main

GitHub Actions 的排程（`schedule`）**只會在預設分支（main）上執行**。
請把 `claude/relaxed-lamport-a1lto5` 分支開 Pull Request 合併到 main。

## 步驟 1：在自己電腦上試跑

需要 Python 3.10 以上。

```bash
git clone https://github.com/karsun-debug/smartmoney-radar.git
```

```bash
cd smartmoney-radar && pip install -e ".[llm,dev]"
```

SEC 規定要附聯絡 email（換成你的）：

```bash
export SPIDERHEAD_USER_AGENT="Smartmoney Radar you@example.com"
```

```bash
spiderhead doctor
```

確認 9 位大神的 SEC 編號都對（右邊的 SEC 名稱要和左邊的基金對得上）：

```bash
spiderhead verify-ciks
```

抓一次資料、產生儀表板：

```bash
spiderhead fetch --days 7
```

```bash
spiderhead dashboard --out site
```

用瀏覽器打開 `site/index.html`。

> ARK 每日交易要「連續兩天」的持股檔才算得出來，所以第一天不會有 Cathie Wood 的每日交易，第二天起才會出現。

## 步驟 2：建立 LINE 推播

LINE Notify 已在 2025 年 3 月停止服務，現在要用「LINE 官方帳號 + Messaging API」。

1. 到 [LINE Official Account Manager](https://manager.line.biz/) 用你的 LINE 登入，**建立 LINE 官方帳號**（名稱例如「大神通知」）。
2. 進入該帳號 → 右上角 **設定** → **Messaging API** → **啟用 Messaging API**，選擇或建立一個 Provider。
3. 到 [LINE Developers Console](https://developers.line.biz/console/) → 點剛剛的 Provider → 點該 channel：
   - **Messaging API** 分頁：用手機掃 QR code，**加這個官方帳號為好友**。
   - 同一頁最下方 **Channel access token (long-lived)** → **Issue**，複製下來 → 這就是 `LINE_CHANNEL_ACCESS_TOKEN`。
   - **Basic settings** 分頁最下方 **Your user ID**（U 開頭）→ 這就是 `LINE_TO`。
4. （建議）回到 Official Account Manager → **回應設定**，關掉「自動回應訊息」，避免你傳訊息給它時它亂回。

在電腦上測試：

```bash
export LINE_CHANNEL_ACCESS_TOKEN="貼上 token"
```

```bash
export LINE_TO="貼上 U 開頭的 user ID"
```

```bash
spiderhead doctor
```

先看訊息長怎樣（不會送出）：

```bash
spiderhead push-line --days 7 --dry-run
```

真的送一則到手機：

```bash
spiderhead push-line --days 7
```

> **訊息額度**：LINE 官方帳號免費方案每月有訊息則數上限（台灣「輕用量」方案目前為每月 200 則，請以 Official Account Manager 顯示為準）。
> 每日摘要約 30 則/月；即時通知只在有新喊單或新申報時才送，通常每天 0–5 則。若接近上限，可以把 `alerts.yml` 的頻率調低。

## 步驟 3：讓 GitHub 每天自動跑

1. GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**，加入：

   | 名稱 | 內容 | 必填 |
   |---|---|---|
   | `SPIDERHEAD_USER_AGENT` | `Smartmoney Radar you@example.com` | ✅ |
   | `LINE_CHANNEL_ACCESS_TOKEN` | 步驟 2 的 token | ✅ |
   | `LINE_TO` | 步驟 2 的 user ID | 建議 |
   | `ANTHROPIC_API_KEY` | Claude API key（分析準確很多） | 建議 |
   | `X_BEARER_TOKEN` | X API（需付費方案） | 選填 |

2. **Settings** → **Pages** → **Source** 選 **GitHub Actions**。
   注意：免費方案的 GitHub Pages 網站是**公開**的，知道網址的人都看得到。
3. **Actions** 分頁 → 若看到提示，按 **I understand my workflows, go ahead and enable them**。
4. 手動跑一次確認：Actions → **daily-digest** → **Run workflow**。
   成功後手機會收到摘要，儀表板網址會出現在該次執行的 **deploy** 步驟裡。
5. 之後自動執行：
   - **daily-digest**：每天台灣時間 07:17 —— 抓資料 → 更新儀表板 → 推每日摘要。
   - **instant-alerts**：每 2 小時 —— 有新喊單或新申報交易就立刻推。
   - **tests**：每次推程式碼時跑測試。

## 步驟 4（選填）：Claude 分析

到 [Claude Console](https://console.anthropic.com/) 建立 API key，設成 `ANTHROPIC_API_KEY`。
排程會自動加上 `--llm`，改用 Claude 判斷看多/看空、產業、反串與引述，比關鍵字準確很多。

**費用控制**：只有「本人發言」、關鍵字已找到個股/產業、或引述大神的報導才會送給 Claude；產業新聞一律用關鍵字。
每次執行最多送 200 則（`--llm-budget` 可調），超過的留到下一次。第一次執行資料最多，之後每次通常只有幾十則。

## 常見問題

| 狀況 | 原因與處理 |
|---|---|
| doctor 顯示 SEC ❌ | 沒設 `SPIDERHEAD_USER_AGENT`，或沒有 email；SEC 會擋。 |
| LINE token ❌ 401 | token 複製不完整或已重新發行；回 Console 重新 Issue。 |
| LINE 收不到 | 手機沒加官方帳號好友；或 `LINE_TO` 填成別人的 ID。 |
| 眾議院申報 ⚠️ | 書記處網站暫時連不上時，會自動改用 LuxAlgo 資料集（較慢、可能缺漏）。 |
| 國會交易 (LuxAlgo) ⚠️ 久未更新 | 只影響參議員與備援；眾議員（如 Pelosi）以書記處為主。 |
| ARK ⚠️ | ARK 偶爾更改檔名；到 ark-funds.com 找新的 CSV 連結，更新 `spiderhead/ark.py` 的 `ARK_FUNDS`。 |
| 股價 ⚠️ | Stooq 暫時無法連線時，喊單勝率和個股走勢圖會留空，其餘功能不受影響。 |
| 排程沒有跑 | 程式還沒合併到 main；或 repo 60 天沒有活動，GitHub 會自動停用排程（到 Actions 重新啟用）。 |
