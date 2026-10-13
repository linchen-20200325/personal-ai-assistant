# 安全與隱私模型

## 威脅模型

**不受信任**：LLM / planner 的輸出、外部內容（Email、文件、網頁，可能含 prompt injection）、provider 回應。
**受信任**：本 repository 的程式碼與本機設定。

因此安全保證放在**程式結構**上，而非依賴 LLM 行為。

## 風險分級

| 等級 | 範例 | 預設處理 |
|---|---|---|
| READ | 搜尋、讀取、摘要、比較 | 自主執行 |
| DRAFT | 草稿、提議行程、待確認計畫 | 自主執行 |
| WRITE_LOW | 一般筆記、使用者明確要求的 reminder | **需確認**；可逐項加入 `auto_write_low` 白名單 |
| WRITE_HIGH | 寄信、對外訊息、修改重要行程、取消、刪除、訂票、訂房、購買、付款、金融操作 | **永遠需要人工確認** |

`writes_enabled` 預設 `false`：在明確開啟前，所有寫入一律 DENY。

## 不可繞過的保證（皆有測試）

| # | 保證 | 機制 | 測試 |
|---|---|---|---|
| 1 | WRITE_HIGH 無法被設定降級 | `decide()` 對 WRITE_HIGH 沒有 ALLOW 分支；`Policy` 無任何 WRITE_HIGH 開關（`extra="forbid"`）；executor 啟動時拒絕把非 WRITE_LOW 或未知 tool 放進白名單 | `test_gate.py`、`test_executor.py` |
| 2 | 動態風險只升不降 | `max(base, assessed)`；評估失敗即 DENY | 16 種組合窮舉、降級嘗試 |
| 3 | Approval 綁定精確參數 | 批准必須提交 pending 的 digest；執行時只使用存放的 `params_json` 並重算 digest 比對 | 竄改存放參數、拿 A 的 digest 批准 B |
| 4 | 單次使用 + TTL | `consume()` 原子轉為 CONSUMED；`now ≥ expires_at` 即過期 | 重放、過期、未批准、已拒絕 |
| 5 | 未註冊 tool → DENY | registry 查無即拒絕 | |
| 6 | 多餘欄位 → DENY | `ToolSpec` 在定義時即拒絕任何（含巢狀）未設 `extra="forbid"` 的 model。刻意宣告的 `dict[str, X]` 欄位不在此限，新增 tool 時應避免 | |
| 7 | provider 失敗不偽裝成空資料 | `ToolResult` validator；executor 將非 `ToolResult` 回傳視為 FAILED | contract test、executor test |
| 8 | naive datetime → DENY | `AwareDatetime`；另拒絕數字 / 數字字串 timestamp（避免被靜默解讀為 UTC）與非整分鐘 offset（避免 digest 不穩定） | |
| 9 | Audit 遮蔽 secrets | `AuditEvent` 建構時遮蔽；驗證錯誤只記位置/類型，planner 送來的多餘 key 名稱以 `<extra>` 取代；tool 例外只記類型名稱；provider 錯誤字串在寫入 audit 與回傳 planner 前都經遮蔽 | 寫入磁碟後檢查 |
| 10 | Planner 無法繞過 executor | `PlannedAction` 只有 `tool`、`params`；executor 公開介面僅 `specs/submit/execute_approved`；tool 實例為私有；AST 架構測試確保只有 executor 呼叫 `.run()` | `test_executor.py` |
| 11 | LLM 輸出一律本地驗證、不猜測 | 嚴格 JSON（拒 NaN/Infinity、重複 key）、封閉 schema（`extra="forbid"`）、缺欄位不補預設、型別不轉換、大小上限；任一動作無效 → 整份計畫拒絕 | `test_llm_planner.py`、`test_orchestrator.py` |
| 12 | LLM 無法提升權限 | `Plan` 無 risk/approval 欄位；風險由 tool 決定；WRITE_HIGH（例如含 attendees 的行程）即使在 `auto_write_low` 白名單仍需確認 | `test_orchestrator.py` |
| 13 | 只有人類能核准 | `ConfirmationStore.approve` 只在 `cli.py` 呼叫（AST 測試）；需互動式 TTY 且輸入 `yes`；確認畫面由 `params_json` 產生並 escape bidi / 零寬 / 控制字元 | `test_cli.py`、`test_architecture.py`、`test_display.py` |
| 14 | Vendor SDK 隔離 | `anthropic` 只能在 `adapters/anthropic_model.py` import（ruff + AST）；SDK 例外轉為固定訊息的 `ModelUnavailableError`，且 `from None` 切斷例外鏈 | `test_anthropic_adapter.py`、`test_architecture.py` |
| 15 | API key 不外洩 | 只從 `ANTHROPIC_API_KEY` 環境變數讀入 `SecretStr`；設定錯誤訊息只列欄位名稱；audit 不記使用者文字與模型輸出 | `test_anthropic_adapter.py`、`test_cli.py`（寫檔後檢查） |

另：
- 執行前的 audit 寫入失敗時，side effect **不會**執行（fail closed）。
- side effect 完成後的 audit 寫入失敗時，executor **不拋例外**，而是回傳實際結果並在 `reason` 標示 `completion audit failed`，避免呼叫端誤以為未執行而重試造成重複動作。

## 已知限制與後續 Batch 的要求

- **外部內容不受信任（prompt injection）**：B3 為單輪規劃，tool 結果**不**回送 LLM，因此 provider 取得的外部文字不會進入 prompt。日後加入多輪 / 摘要時，外部內容必須標示為資料而非指令；寫入類動作仍一律經 risk gate。V2 規劃：讀過外部內容後提高 WRITE_LOW 的確認要求（taint escalation）。
- **送往 LLM 的資料**：使用者輸入的請求文字、tool 目錄（名稱、描述、schema）與目前本地時間會送到 Anthropic API；不送 audit、設定或憑證。
- **Anthropic adapter**：測試以 mock HTTP transport 進行，CI 不會呼叫真實 API、不消耗額度。SDK 內建重試（408/409/429/5xx，`max_retries=2`）與 timeout（預設 60 秒）。模型拒答（`refusal`）或輸出被截斷（非 `end_turn`）一律視為失敗，不嘗試修補。
- **Email**：目前只有讀取與存草稿（DRAFT），沒有寄送 tool。寄送屬 WRITE_HIGH，加入前需客戶裁示。收件人只接受單一純 addr-spec（防止夾帶額外收件人）；主旨等單行欄位拒絕控制字元（CR/LF）。Audit 中 `body`、`subject`、`text` 欄位遮蔽，收件人地址遮蔽 local part。
- **DRAFT 不受 `writes_enabled` 限制**：依核准的風險模型，DRAFT 可自主執行，因此即使寫入關閉，仍可建立遠端草稿（不會寄出）。若日後希望草稿也需確認或有數量上限，屬產品規則調整。

- **確認畫面（B3 CLI）**：必須由程式從 `PendingAction.params_json` 產生，不得使用 planner 撰寫的摘要；顯示時需 escape 控制字元與 bidi / 零寬字元（例如 U+202E、U+200B），避免標題被視覺偽裝。
- **Pending 寫入 audit 的順序**：pending 記錄先建立、再寫 `action.pending`。若此 audit 失敗，會留下一筆尚未記錄的 pending；但它之後的批准（`confirmation.approved`）與執行（`action.started`）都必須先成功寫入 audit，所以任何實際執行仍有完整紀錄。
- **架構測試範圍**：AST 測試只保證 `.run()` 由 executor 呼叫；provider 方法只應由 tool 呼叫，這一點目前靠 code review 維持。
- **Provider adapter 契約**：錯誤字串不得包含憑證；executor 的遮蔽只是第二道防線。

## Booking / 付款政策

**目前階段**：訂票、訂房、付款類能力只做到 DRAFT、human handoff 與 human confirmation；本 repository 尚未提供任何此類 tool。

這**不是永久限制**。長期目標包含受控的訂票能力，但只有在以下條件全部成立，並經客戶另行裁示後，才可設計受控 booking execution：

- provider / API 足夠可靠；
- side effect 可被明確辨識；
- confirmation gate 完整；
- 金額、品項、日期、對象能在執行前重新驗證（對應現有的 digest 綁定與執行前重驗證機制）；
- audit 完整。

**付款屬最高風險，任何情況下都不得由系統自行執行。**

## Secrets 與隱私

- 不得 commit token、API key、密碼或 OAuth credentials；`.gitignore` 已涵蓋 `.env*`、`credentials*.json`、`token*.json`、`client_secret*.json`、`*.pem`、`*.key`。
- `.env.example` 只含 placeholder。唯一的 secret 是 `ANTHROPIC_API_KEY`（`SecretStr`，只經環境變數提供）；Google 憑證隨 B4 adapter 引入。
- CI 以 gitleaks 掃描完整 git 歷史。測試中的合成假值以 `gitleaks:allow` 標註。
- Audit：參數中 key 名稱符合 `token|secret|passw|authoriz|api_key|cookie|credential|session|body|content` 的值會被遮蔽；自由文字中的憑證指派（`key=value`、`key: value`、JSON `"key": "value"`、`Authorization: Basic …`）、Bearer token 與 Email local part 會被遮蔽；字串先截斷至 256 字元再比對，regex 皆為有界量詞，避免 ReDoS。這是 best effort，**不得**把 secrets 放在一般欄位的自由文字中。
- Audit 檔案以 `0600` 權限建立，預設位於 `var/`（已 gitignore）。
- 測試只使用合成資料（`example.com`），repository 內不得放真實個資。
- 最小權限：未來 OAuth scope 先申請唯讀，寫入 scope 逐項加入。
