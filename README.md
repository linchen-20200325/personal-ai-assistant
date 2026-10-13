# Personal AI Assistant

私人「個人全能 AI 助理」的核心函式庫。設計原則：**一個 Orchestrator + 明確的 tool / provider 邊界 + 每個 side effect 前都有風險閘門**。

> 目前狀態：Batch 3（LLM Planner / Orchestrator、Anthropic adapter、CLI 人工確認）。
> 尚未串接任何真實資料服務或 OAuth（provider 皆回 `UNAVAILABLE`）。實際進度以 `main`、PR 與 CI 為準。

## 架構一覽

```
pai CLI ─► Orchestrator ─► Planner（LLMPlanner ─► StructuredModel ─► Anthropic adapter）
                                   │ Plan（不受信任；本地 schema 驗證，整份通過才繼續）
                                   │
                                   ▼
                          ActionExecutor  ◄── 唯一執行入口
          resolve ► validate ► assess risk ► gate ► (human confirm) ► audit ► run ► audit
                                   │
                                   ▼
                     Tool（schema + 宣告風險）──► Provider Protocol ──► Fake / Google / MCP
```

- 資料與 AI 推理分離：provider 只取資料並附 provenance；缺值是 `None`（unknown），失敗是 `ERROR`/`UNAVAILABLE`，絕不偽裝成空資料。
- 風險分級 `READ < DRAFT < WRITE_LOW < WRITE_HIGH`；`WRITE_HIGH` 一律需要人工確認，任何設定皆無法繞過。

詳見 [docs/architecture.md](docs/architecture.md) 與 [docs/security.md](docs/security.md)。

## 開發

需求：Python 3.12+、[uv](https://docs.astral.sh/uv/)。

```bash
uv sync                      # 安裝依賴（含 dev group）
uv run ruff format --check . # 格式
uv run ruff check .          # lint（含禁止在核心 import LLM/MCP SDK）
uv run mypy                  # strict 型別檢查
uv run pytest --cov          # 測試
```

設定透過環境變數（`ASSISTANT_*`），範本見 [.env.example](.env.example)；
可用 `uv run --env-file .env ...` 載入。`.env` 已列入 `.gitignore`。

## 使用 CLI

```bash
uv sync --extra anthropic                   # 或 pip install 'personal-ai-assistant[anthropic]'
export ANTHROPIC_API_KEY=...                # 只放在環境變數 / secret manager
uv run pai "明天下午有什麼行程？"
```

READ 類動作自動執行；需要確認的動作會顯示由程式產生的確認畫面，必須輸入 `yes` 才會執行。
寫入預設關閉（`ASSISTANT_WRITES_ENABLED=false`）。

## 工作流程

`main` 只透過 PR 變更：feature branch → PR → CI 全綠 → review → merge。不 force push `main`。
