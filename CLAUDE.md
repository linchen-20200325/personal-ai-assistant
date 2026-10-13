# 工作規則（永久）

語言：繁體中文（台灣）。

## 靜默自主巡航
在已核准的 Batch 範圍內，自主完成：實作 → 測試 → 修正 → push → PR → CI → review → 安全修正 → merge → 驗證。
可安全自行修復的 review / CI / 測試問題，直接修，不先回報。

只在以下情況回覆使用者，其餘一律靜默（進度、commit/push、PR/CI 狀態、通知、「仍在等待」、重複狀態都不回報）：
1. 需要客戶裁示（架構分岐、spec 缺口、產品規則）
2. 需要付費服務、OAuth、外部 API 或重大 dependency
3. 涉及安全／隱私／不可逆操作
4. GitHub／CI／測試錯誤，合理自行修復後仍無法解決
5. 工作即將超出已核准的 Batch
6. 整個 Batch 完成（只報一次）

Batch 完成回報格式（精簡，不敘述過程）：
🟢 Batch X 完成 / main: SHA / PR: #n / CI: green / tests: 結果 / 安全稽核: 一句 / 需要裁示: 無或事項 / 下一步: 一句

## 專案不變量
- 未經核准不進入下一個 Batch；不做 browser automation、自動付款或訂購、真實 OAuth／LLM 串接。
- 流程：feature branch → PR → CI 全綠 → review 無必修 → merge；不 force push main。
- 核心保持 provider-neutral；所有 side effect 只經 `ActionExecutor`；WRITE_HIGH 永遠需人工確認。
- 不 commit secrets；測試只用合成資料。
- 以 main、PR、CI、程式碼為真實狀態；詳見 docs/architecture.md、docs/security.md。
