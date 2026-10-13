# 架構（V1）

## 原則

1. **一個 Orchestrator，多個薄 Tool**：不建立多套重複的 Agent。
2. **DATA ≠ AI ANALYSIS**：provider 只負責取得、標準化、驗證、標註 provenance；AI 層只讀取已取得的資料，不得補造缺值。
3. **Provider-neutral**：核心（domain / safety / tools / providers 介面）不依賴任何 LLM、MCP 或特定服務 SDK。`ruff` 的 `banned-api` 規則在 CI 強制禁止核心 import `anthropic`、`openai`、`mcp`。
4. **YAGNI**：每個 module / dependency 都要有當下用途。Runtime 依賴只有 `pydantic`。

## 模組

| 模組 | 職責 |
|---|---|
| `domain/risk.py` | `RiskLevel` 全序：READ < DRAFT < WRITE_LOW < WRITE_HIGH |
| `domain/result.py` | `ToolResult` / `DataStatus` / `Provenance`：OK、PARTIAL、UNAVAILABLE、ERROR 的一致性由 validator 強制；`Page[T]` 以必填的 `has_more` 標示清單是否完整 |
| `domain/types.py` | 共用值型別：`Instant`（帶時區 ISO-8601）、`TimeRange`、`EmailAddress`、`Identifier`、`SearchText`、`SearchLimit` |
| `domain/calendar.py` | Calendar 資料模型 |
| `domain/email.py`、`document.py`、`note.py`、`reminder.py` | Email / 文件 / 筆記 / 提醒資料模型 |
| `tools/base.py` | `ToolSpec`（名稱、描述、宣告風險、輸入 schema）、`Tool`、`PlannedAction` |
| `tools/calendar.py` | `calendar.list_events`（READ）、`calendar.create_event`（WRITE_LOW，有 attendees 時升為 WRITE_HIGH） |
| `tools/email.py` | `email.search`、`email.get`（READ）、`email.create_draft`（DRAFT，不寄送；目前沒有寄信 tool） |
| `tools/document.py` | `documents.search`、`documents.read`（READ） |
| `tools/note.py` | `notes.search`（READ）、`notes.create`（WRITE_LOW） |
| `tools/reminder.py` | `reminders.list`（READ）、`reminders.create`（WRITE_LOW；執行時再檢查到期時間仍在未來） |
| `providers/*.py` | `CalendarProvider`、`EmailProvider`、`DocumentProvider`、`NoteProvider`、`ReminderProvider` Protocol：可替換的資料存取邊界 |
| `safety/gate.py` | `Policy` 與純函式 `decide()` |
| `safety/confirmation.py` | `PendingAction`、digest、`ConfirmationStore` |
| `safety/executor.py` | `ActionExecutor`：唯一執行入口 |
| `audit.py` | `AuditEvent`（建構時即遮蔽）、`JsonlAuditSink` |
| `config.py` | 從 `ASSISTANT_*` 環境變數載入 `Settings` |

## 執行流程

### `ActionExecutor.submit(PlannedAction)`

1. 依名稱解析 tool；未註冊 → **DENY**。
2. 以 tool 的輸入 model 驗證參數；多餘欄位、naive datetime、數字 timestamp、型別錯誤 → **DENY**（audit 只記錯誤位置與類型，不記輸入值或 planner 提供的 key 名稱）。
3. 有效風險 `r = max(spec.base_risk, tool.assess_risk(params))`；評估拋例外或回傳非 `RiskLevel` → **DENY**。
4. `decide(r, tool, policy)`：
   - 寫入類且 `writes_enabled=false` → DENY
   - `r ≤ DRAFT` → ALLOW
   - `r = WRITE_LOW` 且在 `auto_write_low` 白名單 → ALLOW
   - 其他（含所有 WRITE_HIGH）→ CONFIRM
5. CONFIRM：建立 `PendingAction`（綁定 digest、TTL），回傳 `PENDING_CONFIRMATION`。
6. ALLOW：先寫 `action.started` audit（失敗則不執行），執行 tool，再寫 `action.executed` / `action.failed`。

### 人工確認 → `ActionExecutor.execute_approved(pending_id)`

1. 人類通道（未來的 CLI/UI）依 `params_json` 顯示內容，並以 `ConfirmationStore.approve(pending_id, digest=..., approver=...)` 批准；
   `ActionExecutor` 本身沒有任何 approve 能力。格式不符的 `pending_id` 直接 DENY。
2. `consume()` 原子地將狀態 APPROVED → CONSUMED（single-use），並檢查 TTL。
3. 重新驗證：tool 仍註冊、存放的參數仍合法、digest 重算相符、風險未上升、policy 仍允許寫入。任何一項失敗 → DENY。
4. 執行並 audit。

### 資料完整性（DATA ≠ AI ANALYSIS）

- 單筆資料：provider 未提供的欄位為 `None`（unknown），所有 provider 回報欄位皆無預設值。
- 清單：有上限的搜尋回傳 `Page[T]`，`has_more` 必填（`None` = 無法判斷），避免「前 20 封」被當成「全部」。
- 文件內容：`DocumentContent.truncated` 必填，避免把截斷的文字當成完整文件摘要。
- 查無或無法解析：回傳 `UNAVAILABLE`，不回傳空字串或空清單。

### 執行結果

`Outcome.EXECUTED` 只在 `ToolResult` 為 OK / PARTIAL 時出現；provider 失敗、tool 拋例外、或 tool 回傳非 `ToolResult`（例如裸 `[]`）都是 `FAILED`，`data` 為 `None`。

## 數學與正確性備註

- **風險單調性**：有效風險 `r_eff = max(r_base, r_dyn)`，因此 `r_eff ≥ r_base` 恆成立。（B3 引入多步驟計畫後，計畫風險定義為各動作 `r_eff` 的最大值。）
- **Digest**：`SHA256(tool + "\n" + canonical_json(params))`，其中 `canonical_json` 為排序鍵、無空白、`allow_nan=False`。
  tool 名稱格式禁止換行，故編碼無歧義。碰撞機率依生日界 `P ≈ n² / 2^257`，`n = 10^6` 時約 `4.3 × 10^-66`。
- **TTL**：`expires_at = created_at + ttl`；`now ≥ expires_at` 視為過期（邊界含等號）。

## 延後項目

| Batch | 內容 |
|---|---|
| B2 ✅ | Email / Document / Notes / Reminder 介面 + fake；`docs/integrations.md` |
| B3 | Orchestrator：Planner Protocol、LLM adapter（供應商待裁示）、CLI 確認通道 |
| B4 | Google 唯讀 adapters（GCP / OAuth / token 存放待裁示） |
| V2+ | 寫入 adapters、持久化 pending store、audit hash chain、taint escalation、受控 booking（見 security.md） |

`ConfirmationStore` 目前為 in-memory：重啟後所有 pending 失效（fail closed）；過期記錄於建立新 pending 時清除。
`config.load_settings()` 目前只有測試在使用，B3 的 CLI 會是第一個實際使用它的地方。
