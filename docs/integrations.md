# 整合需求（Integration Requirements）

本 repository **未包含任何真實憑證或 OAuth 設定**。下表列出每個 provider 介面要接上真實服務時需要的前置作業與待裁示項目；在客戶裁示前不實作。

| 介面 | 候選服務 | 最小權限（建議起點） | 前置作業 | 待裁示 |
|---|---|---|---|---|
| `CalendarProvider` | Google Calendar API | `calendar.readonly`；寫入時再加 `calendar.events` | GCP project、啟用 API、OAuth consent screen、Desktop OAuth client | GCP / OAuth、token 存放 |
| `EmailProvider` | Gmail API | `gmail.readonly`（restricted scope）；草稿需 `gmail.compose`（**此 scope 同時允許寄信**，「不寄信」僅由程式碼保證） | 同上；restricted scope 的驗證限制需實測 | 同上；是否允許建立草稿 |
| `DocumentProvider` | Google Drive API | 優先 `drive.file`（非敏感）；全域搜尋需 `drive.readonly`（restricted） | 同上 | 搜尋範圍與 scope 取捨 |
| `NoteProvider` | 本機 SQLite / Notion / Google Docs 等 | 視後端而定 | 依選定後端 | **筆記存放位置**（涉及個資保存） |
| `ReminderProvider` | 本機儲存 + 通知通道，或 Google Tasks / Calendar | 視後端而定 | 依選定後端；提醒送達需要排程與通知通道 | **存放位置與通知通道** |

## 共通注意事項

- OAuth consent screen 維持 Testing 模式時，refresh token 7 天後失效；正式使用前需實測並決定發布狀態。
- Token 不得寫入 repository 或原始碼；存放方式（例如 `~/.config` 下 0600 權限檔案或 OS keyring）於 B4 前裁示。
- Adapter 必須：把 provider 錯誤轉為 `ToolResult.failed(...)`；錯誤字串不得包含憑證；不得以空清單或空字串代替未取得的資料；清單須正確設定 `has_more`。
- `Identifier` 是不透明字串，可能含 `/`、`?`、`%` 等字元；adapter 組 URL 時必須對 path segment 做 percent-encode，不得直接拼接。
- Email 地址只接受純 addr-spec（無顯示名稱、引號、分隔符）；國際化網域需先轉 punycode。provider 回傳不符合此格式的地址（引號 local part、EAI、無網域點）時，adapter 應將該欄位設為 `None` 並以 `PARTIAL` + `missing` 回報，不得猜測或改寫地址。
- 每個新 adapter 都要加入對應 `tests/contracts/` 的 `PROVIDERS` 清單，以錄製的合成回應通過同一套 contract test。
- 本機已存在的 ChatGPT / Claude 端 Google connector 與本 repository 無關，不代表本專案已取得任何授權。
