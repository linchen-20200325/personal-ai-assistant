# 架構（V1）

## 原則

1. **一個 Orchestrator，多個薄 Tool**：不建立多套重複的 Agent。
2. **DATA ≠ AI ANALYSIS**：provider 只負責取得、標準化、驗證、標註 provenance；AI 層只讀取已取得的資料，不得補造缺值。
3. **Provider-neutral**：核心（domain / safety / tools / providers / planning）不依賴任何 LLM、MCP 或特定服務 SDK。`ruff` 的 `banned-api` 規則在 CI 強制禁止 import `anthropic`、`openai`、`mcp`，唯一例外是 `adapters/anthropic_model.py`（per-file 豁免）；`tests/test_architecture.py` 另以 AST 確認核心不 import `adapters` / `cli`。
4. **YAGNI**：每個 module / dependency 都要有當下用途。核心 runtime 依賴只有 `pydantic`；Anthropic SDK 是 optional extra（`personal-ai-assistant[anthropic]`），只有 CLI 在執行時延遲載入。

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
| `config.py` | 從 `ASSISTANT_*` 環境變數載入 `Settings`（含 `ASSISTANT_TIMEZONE`） |
| `validation.py` | `validation_summary()`：驗證錯誤只輸出位置與類型（executor 與 planner 共用） |
| `planning/contracts.py` | Provider-neutral 契約：`Plan` / `ProposedAction`、`Planner` Protocol、`StructuredModel` port、`PlanningError` |
| `planning/llm_planner.py` | `LLMPlanner`：組 prompt + 封閉 JSON schema，呼叫 `StructuredModel`，在本地嚴格驗證輸出 |
| `planning/orchestrator.py` | `Orchestrator`：request → planner → 整份計畫驗證 → audit → 逐一交給 `ActionExecutor` |
| `adapters/anthropic_model.py` | Anthropic Messages API 的 `StructuredModel` 實作（唯一可 import `anthropic` 的模組） |
| `providers/unconfigured.py` | 尚未接上真實服務前的 provider：一律回 `UNAVAILABLE` |
| `adapters/google_auth.py` | Google OAuth 邊界：只要求 / 只接受 `calendar.readonly`；token 只存 OS keyring；OAuth client 檔必須在 repo 外 |
| `adapters/google_calendar.py` | `GoogleCalendarProvider`：Calendar v3 `events.list` 唯讀；分頁、對應、驗證、錯誤語意；`create_event` 一律拒絕 |
| `google_auth_cli.py` | `pai-google-auth login / status / logout` |
| `display.py` | 終端安全顯示：escape 控制 / bidi / 零寬字元 |
| `cli.py` | `pai` 指令：composition root + 人工確認通道（唯一呼叫 `ConfirmationStore.approve` 的地方） |

## 執行流程

### B3：自然語言 → 計畫 → 閘門 → 確認 → 執行

```
pai "明天下午幫我加一個牙醫行程"
  │
  ▼
Orchestrator.propose()          空字串 / 超過 2000 字 → plan.rejected（不呼叫 LLM）
  │ PlanningRequest(text, executor.specs(), 本地時間含 offset)
  ▼
Planner（LLMPlanner → StructuredModel port → Anthropic adapter）
  │ 原始 JSON 文字（不受信任）
  ▼
parse_plan()                    嚴格 JSON（拒 NaN/Infinity、重複 key）→ 封閉 schema → 大小上限
  │                             任何錯誤 → plan.rejected；模型/網路錯誤 → plan.failed
  ▼
整份計畫驗證                     任一動作 tool 未註冊或 params 不合 input model → 整份拒絕（不部分執行）
  │ plan.created（只記 tool 名稱與問題數，不記使用者文字或模型文字）；audit 失敗 → 丟棄計畫
  ▼
CLI 顯示計畫                     planner 說明標示「未經驗證」；所有文字經 escape_display
  ▼
ActionExecutor.submit(…, plan_id)  既有流程：validate → risk → gate
  ├─ READ / DRAFT / 白名單 WRITE_LOW → 執行 + audit
  ├─ DENY（未知、寫入關閉…）→ 顯示原因
  └─ CONFIRM → PendingAction
        ▼
     CLI 確認畫面：由 pending.params_json 產生（digest 涵蓋的內容），WRITE_HIGH 另加警示
     非互動式終端 → 直接 reject；必須輸入 `yes`，其他任何輸入 / EOF → reject
        ▼
     ConfirmationStore.approve(digest) → ActionExecutor.execute_approved()（既有重驗證）
```

`Plan` 沒有任何 risk / approval / identity 欄位且 `extra="forbid"`，LLM 無法自行宣告風險或核准；
風險只由 tool 宣告與 `assess_risk` 決定，權限只由 `decide()` 決定。

**Planner 契約（provider-neutral）**：

- `Planner.plan(PlanningRequest) -> Plan`，失敗只拋 `PlanningError`（`InvalidPlanError` / `ModelUnavailableError`），訊息可安全顯示與 audit。
- `StructuredModel.generate(StructuredRequest) -> str`：adapter 只負責傳輸，回傳原始 JSON 文字；驗證在核心。
- Tool 參數以 `arguments_json`（JSON 字串）傳遞，讓輸出 schema 全部 `additionalProperties: false`（各家 structured output 的共同要求）；核心再解碼、驗證。
- 新增 OpenAI 等供應商：在 `adapters/` 實作 `StructuredModel`，並在 `pyproject.toml` 加 per-file 豁免；核心不需修改。

**B3 範圍限制**：單輪規劃，tool 結果不回送 LLM（因此外部內容不會進入 prompt，沒有 prompt injection 面）；CLI 直接顯示 provider 資料（DATA），不做 LLM 摘要。多輪 / 摘要留待後續 Batch。

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
| B3 ✅ | Orchestrator、Planner Protocol、Anthropic adapter、CLI 確認通道 |
| B4 ✅ | Google Calendar 唯讀 adapter（`calendar.readonly`、OS keyring）；Gmail / Drive 未做 |
| V2+ | 寫入 adapters、持久化 pending store、audit hash chain、taint escalation、受控 booking（見 security.md） |

`ConfirmationStore` 目前為 in-memory：重啟後所有 pending 失效（fail closed）；過期記錄於建立新 pending 時清除。
`pai` CLI：`ASSISTANT_CALENDAR_PROVIDER=google` 時行事曆改用 `GoogleCalendarProvider`，且**不註冊** `calendar.create_event`（planner 看不到寫入 tool）；其餘 provider 仍為 `UnconfiguredProvider`（回 `UNAVAILABLE`）。

### Google Calendar 對應規則（DATA ≠ AI）

| Google 欄位 | Domain | 規則 |
|---|---|---|
| `id` | `event_id` | 缺少 / 非字串 → 丟棄該事件並列入 `missing` |
| `start.dateTime` / `start.date` | `start` | `dateTime` 必須帶 offset（保留原 offset）；全天 `date` 以事件或日曆自身時區的 00:00 表示；兩者皆無時區 → 丟棄並列入 `missing`（不猜） |
| `end` | `end` | 缺少 → `None`；無法解析或不晚於 start → `None` 並列入 `missing` |
| `summary` / `location` | `title` / `location` | 缺少 → `None`；型別錯誤 → `None` 並列入 `missing` |
| `attendees` | `attendees` | 欄位缺少或 `attendeesOmitted` → `None`（未知，非「沒有」）；任一地址不是純 addr-spec → 整個欄位 `None` 並列入 `missing`（不裁剪） |
| `status=cancelled` | — | 略過 |

分頁上限 `MAX_PAGES × 250`；超過時回 `PARTIAL` 並標示 `events_beyond_page_limit`。`items` 缺漏或回應形狀不符 → `ERROR`（不當成空清單）。

錯誤語意：401 / token 失效 → `UNAVAILABLE`（提示重新 `pai-google-auth login`）；403 權限 / 404 → `UNAVAILABLE`；429、403 rate limit、5xx、網路 → `ERROR`。訊息為固定字串，不含回應內容或 token。
