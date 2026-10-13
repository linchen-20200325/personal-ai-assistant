# Personal AI Assistant

私人「個人全能 AI 助理」的核心函式庫。設計原則：**一個 Orchestrator + 明確的 tool / provider 邊界 + 每個 side effect 前都有風險閘門**。

> 目前狀態：Batch 1（安全核心 + Calendar 參考切片，僅 fake provider）。
> 尚未串接任何真實服務、LLM 或 OAuth。實際進度以 `main`、PR 與 CI 為準。

## 架構一覽

```
Planner / LLM（B3，未實作）──► PlannedAction（不受信任的資料）
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

## 工作流程

`main` 只透過 PR 變更：feature branch → PR → CI 全綠 → review → merge。不 force push `main`。
