"""Google OAuth boundary: calendar.readonly only, tokens in the OS keyring.

* The only scope ever requested or accepted is ``calendar.readonly``. A stored
  token carrying any other scope set is refused, never used.
* The OAuth client file is read only during ``login`` and must live outside
  any git repository; its contents are never printed or stored by us.
* The authorized-user token (refresh token, client id/secret) is kept only in
  the OS keyring. Insecure backends (none available, plaintext ``keyrings.alt``)
  are refused rather than silently falling back to a file.
* All failures surface as ``GoogleAuthError`` / ``GoogleAuthUnavailable`` with
  fixed messages: no token material, file contents or library exception text.
"""

import contextlib
import json
import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

import google_auth_httplib2
import httplib2
import keyring
import keyring.errors
import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from keyring.backend import KeyringBackend

CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
SCOPES = (CALENDAR_READONLY_SCOPE,)
KEYRING_SERVICE = "personal-ai-assistant"
KEYRING_USERNAME = "google-calendar-readonly"
DEFAULT_TIMEOUT_SECONDS = 30
LOGIN_HINT = "run `pai-google-auth login --client-secrets <path outside the repo>`"
TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105 - endpoint, not a secret
REVOKE_URI = "https://oauth2.googleapis.com/revoke"

# Allow-list (not deny-list) of OS-backed keyrings, by "module.Class". Anything
# else -- no backend, the null backend, plaintext ``keyrings.alt`` files, unknown
# third-party backends -- is refused rather than trusted with a refresh token.
SECURE_BACKENDS = frozenset(
    {
        "keyring.backends.SecretService.Keyring",
        "keyring.backends.libsecret.Keyring",
        "keyring.backends.kwallet.DBusKeyring",
        "keyring.backends.kwallet.DBusKeyringKWallet4",
        "keyring.backends.macOS.Keyring",
        "keyring.backends.Windows.WinVaultKeyring",
    }
)
_CHAINER = "keyring.backends.chainer.ChainerBackend"


class GoogleAuthError(Exception):
    """Login / token-store failure. ``str(exc)`` is safe to show."""


class GoogleAuthUnavailable(GoogleAuthError):
    """No usable authorization right now (not logged in, revoked, keyring locked)."""


class TokenStore(Protocol):
    def load(self) -> str | None: ...

    def save(self, token_json: str) -> None: ...

    def delete(self) -> None: ...


def _qualified(backend: object) -> str:
    return f"{type(backend).__module__}.{type(backend).__qualname__}"


class KeyringTokenStore:
    """Authorized-user JSON in a secure OS keyring under a fixed service / username."""

    def __init__(
        self,
        backend: KeyringBackend | None = None,
        *,
        secure_backends: frozenset[str] = SECURE_BACKENDS,
    ) -> None:
        self._explicit = backend
        self._secure = secure_backends

    def _backend(self) -> KeyringBackend:
        backend = self._explicit or keyring.get_keyring()
        candidates = [backend]
        if _qualified(backend) == _CHAINER:
            # A chainer reads from every backend it wraps (plaintext ones too);
            # use exactly one secure backend instead.
            candidates = list(getattr(backend, "backends", ()))
        for candidate in candidates:
            if _qualified(candidate) in self._secure:
                return candidate
        raise GoogleAuthUnavailable("no secure OS keyring is available")

    def load(self) -> str | None:
        backend = self._backend()
        failed = False
        try:
            value = backend.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception:  # KeyringError, D-Bus errors, ...
            failed = True
        if failed:
            raise GoogleAuthUnavailable("OS keyring could not be read (locked?)")
        return value

    def save(self, token_json: str) -> None:
        backend = self._backend()
        failed = False
        try:
            backend.set_password(KEYRING_SERVICE, KEYRING_USERNAME, token_json)
            failed = backend.get_password(KEYRING_SERVICE, KEYRING_USERNAME) != token_json
        except Exception:
            failed = True
        if failed:
            raise GoogleAuthError("OS keyring could not store the token")

    def delete(self) -> None:
        backend = self._backend()
        failed = False
        try:
            backend.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except keyring.errors.PasswordDeleteError:
            pass  # nothing stored
        except Exception:
            failed = True
        if failed or backend.get_password(KEYRING_SERVICE, KEYRING_USERNAME) is not None:
            raise GoogleAuthError("OS keyring could not delete the token")


def _scope_set(value: object) -> frozenset[str] | None:
    if isinstance(value, str):
        return frozenset(value.split())
    if isinstance(value, list | tuple) and all(isinstance(s, str) for s in value):
        return frozenset(value)
    return None


def _parse_token(raw: str) -> Credentials | str:
    """Credentials, or a safe refusal reason. Never raises (no exception context)."""
    try:
        info = json.loads(raw)
    except ValueError:
        return "stored Google token is unreadable"
    if not isinstance(info, dict):
        return "stored Google token is unreadable"
    if _scope_set(info.get("scopes")) != frozenset(SCOPES):
        return "stored Google token has unexpected scopes; refused"
    if info.get("token_uri", TOKEN_URI) != TOKEN_URI:
        return "stored Google token has an unexpected token endpoint; refused"
    try:
        credentials: Credentials = Credentials.from_authorized_user_info(info)  # type: ignore[no-untyped-call]
    except Exception:
        return "stored Google token is unreadable"
    return credentials


def load_credentials(store: TokenStore) -> Credentials:
    """Stored calendar.readonly credentials, or ``GoogleAuthUnavailable``.

    Raised errors carry no exception context: the parse failure of a token
    (whose text holds the refresh token) is never attached to them.
    """
    raw = store.load()
    if raw is None:
        raise GoogleAuthUnavailable(f"Google Calendar is not authorized; {LOGIN_HINT}")
    parsed = _parse_token(raw)
    if isinstance(parsed, str):
        raise GoogleAuthUnavailable(f"{parsed}; {LOGIN_HINT}")
    return parsed


def build_calendar_service(
    credentials: Credentials, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> Any:
    """Calendar v3 client using the bundled discovery document (no discovery fetch)."""
    http = google_auth_httplib2.AuthorizedHttp(
        credentials, http=httplib2.Http(timeout=timeout_seconds)
    )
    return build("calendar", "v3", http=http, cache_discovery=False, static_discovery=True)


def calendar_service_factory(
    store: TokenStore, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> Callable[[], Any]:
    def factory() -> Any:
        return build_calendar_service(load_credentials(store), timeout_seconds=timeout_seconds)

    return factory


def inside_git_repository(path: Path) -> bool:
    """A ``.git`` in any parent. Bare "dotfiles" repos (``--git-dir``) are not detectable."""
    return any((parent / ".git").exists() for parent in path.parents)


def _readable_by_others(path: Path) -> bool:
    return os.name == "posix" and bool(path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO))


class Flow(Protocol):
    def run_local_server(self, **kwargs: Any) -> Any: ...


type FlowFactory = Callable[[str, list[str]], Flow]


def _installed_app_flow(client_secrets: str, scopes: list[str]) -> Flow:
    flow: Flow = InstalledAppFlow.from_client_secrets_file(client_secrets, scopes=scopes)
    return flow


def _revoke(token: object) -> None:
    """Best effort: a token we will not keep should not stay valid at Google."""
    if not isinstance(token, Mapping):
        return
    for key in ("refresh_token", "access_token"):
        value = token.get(key)
        if isinstance(value, str) and value:
            with contextlib.suppress(Exception):  # offline: nothing more we can do
                requests.post(REVOKE_URI, data={"token": value}, timeout=10)
            return


def _run_flow(flow_factory: FlowFactory, path: Path, open_browser: bool) -> Any:
    """Returns credentials, or a safe failure reason (str). Never raises."""
    try:
        flow = flow_factory(str(path), list(SCOPES))
    except Exception:
        return "OAuth client file is not a valid Desktop app client"
    try:
        return flow.run_local_server(
            host="localhost",
            port=0,
            open_browser=open_browser,
            authorization_prompt_message="請在瀏覽器開啟以下網址並授權（唯讀行事曆）：\n{url}",
            success_message="授權完成，可以關閉此分頁。",
        )
    except Warning as warning:
        # oauthlib raises "Scope has changed" with the issued token attached.
        _revoke(getattr(warning, "token", None))
        return "Google granted a different scope set; token revoked and discarded"
    except Exception:
        return "Google sign-in did not complete"


def login(
    client_secrets: Path,
    store: TokenStore,
    *,
    open_browser: bool = True,
    flow_factory: FlowFactory = _installed_app_flow,
) -> None:
    """Run the installed-app OAuth flow on a loopback port and keep the token in the keyring."""
    try:
        path: Path | None = client_secrets.expanduser().resolve(strict=True)
    except OSError:
        path = None
    if path is None:
        raise GoogleAuthError("OAuth client file not found")
    if not path.is_file():
        raise GoogleAuthError("OAuth client path is not a file")
    if inside_git_repository(path):
        raise GoogleAuthError("OAuth client file must be stored outside any git repository")
    if _readable_by_others(path):
        raise GoogleAuthError("OAuth client file is readable by other users; run chmod 600 on it")
    credentials = _run_flow(flow_factory, path, open_browser)
    if isinstance(credentials, str):
        raise GoogleAuthError(credentials)
    granted = _scope_set(getattr(credentials, "granted_scopes", None) or credentials.scopes)
    if granted != frozenset(SCOPES):
        _revoke({"refresh_token": getattr(credentials, "refresh_token", None)})
        raise GoogleAuthError("Google granted a different scope set; token revoked and discarded")
    if not getattr(credentials, "refresh_token", None):
        raise GoogleAuthError("Google returned no refresh token; token discarded")
    token = json.loads(credentials.to_json())
    token["scopes"] = list(SCOPES)
    store.save(json.dumps(token))


def status(store: TokenStore) -> str:
    """Local authorization state (not checked with Google); never includes token material."""
    try:
        load_credentials(store)
    except GoogleAuthError as exc:
        return f"未授權：{exc}"
    return "已授權（scope：calendar.readonly，token 存於 OS keyring；撤銷與否需實際查詢才知道）"
