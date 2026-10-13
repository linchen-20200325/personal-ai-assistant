"""``pai-google-auth``: authorize, inspect or remove read-only Google Calendar access.

    pai-google-auth login --client-secrets ~/secrets/pai-oauth-client.json
    pai-google-auth check --client-secrets ~/secrets/pai-oauth-client.json
    pai-google-auth status
    pai-google-auth logout

Only the path of the OAuth client file is taken; its contents and the
resulting tokens are never printed. Tokens go to the OS keyring only.
"""

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from personal_ai_assistant.display import escape_display

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2


def main(
    argv: Sequence[str] | None = None,
    *,
    store: Any = None,
    flow_factory: Any = None,
    show: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(
        prog="pai-google-auth", description="Manage read-only Google Calendar authorization."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    login_cmd = commands.add_parser("login", help="authorize calendar.readonly in the browser")
    login_cmd.add_argument(
        "--client-secrets",
        required=True,
        type=Path,
        help="path to the Desktop-app OAuth client JSON (must be outside any git repository)",
    )
    login_cmd.add_argument(
        "--no-browser", action="store_true", help="print the URL instead of opening a browser"
    )
    check_cmd = commands.add_parser(
        "check", help="read-only check of an existing OAuth client file and the keyring"
    )
    check_cmd.add_argument("--client-secrets", required=True, type=Path)
    commands.add_parser("status", help="show whether a valid read-only token is stored")
    commands.add_parser("logout", help="delete the stored token from the OS keyring")
    args = parser.parse_args(argv)

    try:
        from personal_ai_assistant.adapters import google_auth
    except ImportError:
        show("缺少 Google 套件：請安裝 extra `personal-ai-assistant[google]`。")
        return EXIT_CONFIG

    token_store = store if store is not None else google_auth.KeyringTokenStore()
    try:
        if args.command == "login":
            kwargs: dict[str, Any] = {"open_browser": not args.no_browser}
            if flow_factory is not None:
                kwargs["flow_factory"] = flow_factory
            google_auth.login(args.client_secrets, token_store, **kwargs)
            show("授權完成：已將唯讀行事曆 token 存入 OS keyring。")
        elif args.command == "check":
            checks = [
                *google_auth.check_client_file(args.client_secrets),
                *google_auth.check_token_store(token_store),
            ]
            for ok, label in checks:
                show(f"{'✔' if ok else '✘'} {escape_display(label)}")
            show("（Calendar API 是否已啟用，需登入後第一次讀取時才能確認。）")
            return EXIT_OK if all(ok for ok, _ in checks) else EXIT_FAILED
        elif args.command == "status":
            show(google_auth.status(token_store))
        else:
            token_store.delete()
            show(
                "已刪除 OS keyring 中的 Google token"
                "（若需完全撤銷，請到 Google 帳戶的第三方存取設定移除）。"
            )
    except google_auth.GoogleAuthError as exc:
        show(f"失敗：{escape_display(str(exc))}")
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
