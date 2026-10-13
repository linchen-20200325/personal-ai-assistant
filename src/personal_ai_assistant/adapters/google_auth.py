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

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import google_auth_httplib2
import httplib2
import keyring
import keyring.errors
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from keyring.backend import KeyringBackend
from keyring.backends import fail

CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
SCOPES = (CALENDAR_READONLY_SCOPE,)
KEYRING_SERVICE = "personal-ai-assistant"
KEYRING_USERNAME = "google-calendar-readonly"
DEFAULT_TIMEOUT_SECONDS = 30
LOGIN_HINT = "run `pai-google-auth login --client-secrets <path outside the repo>`"


class GoogleAuthError(Exception):
    """Login / token-store failure. ``str(exc)`` is safe to show."""


class GoogleAuthUnavailable(GoogleAuthError):
    """No usable authorization right now (not logged in, revoked, keyring locked)."""


class TokenStore(Protocol):
    def load(self) -> str | None: ...

    def save(self, token_json: str) -> None: ...

    def delete(self) -> None: ...


class KeyringTokenStore:
    """Authorized-user JSON in the OS keyring under a fixed service / username."""

    def __init__(self, backend: KeyringBackend | None = None) -> None:
        self._explicit = backend

    def _backend(self) -> KeyringBackend:
        backend = self._explicit or keyring.get_keyring()
        if isinstance(backend, fail.Keyring) or type(backend).__module__.startswith("keyrings.alt"):
            raise GoogleAuthUnavailable("no secure OS keyring is available")
        return backend

    def load(self) -> str | None:
        backend = self._backend()
        try:
            return backend.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except keyring.errors.KeyringError:
            raise GoogleAuthUnavailable("OS keyring could not be read (locked?)") from None

    def save(self, token_json: str) -> None:
        backend = self._backend()
        try:
            backend.set_password(KEYRING_SERVICE, KEYRING_USERNAME, token_json)
        except keyring.errors.KeyringError:
            raise GoogleAuthError("OS keyring could not store the token") from None

    def delete(self) -> None:
        backend = self._backend()
        try:
            backend.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except keyring.errors.PasswordDeleteError:
            return  # nothing stored
        except keyring.errors.KeyringError:
            raise GoogleAuthError("OS keyring could not delete the token") from None


def _scope_set(value: object) -> frozenset[str] | None:
    if isinstance(value, str):
        return frozenset(value.split())
    if isinstance(value, list | tuple) and all(isinstance(s, str) for s in value):
        return frozenset(value)
    return None


def load_credentials(store: TokenStore) -> Credentials:
    """Stored calendar.readonly credentials, or ``GoogleAuthUnavailable``."""
    raw = store.load()
    if raw is None:
        raise GoogleAuthUnavailable(f"Google Calendar is not authorized; {LOGIN_HINT}")
    try:
        info = json.loads(raw)
        if not isinstance(info, dict):
            raise TypeError
        if _scope_set(info.get("scopes")) != frozenset(SCOPES):
            raise GoogleAuthUnavailable(
                f"stored Google token has unexpected scopes; refused. {LOGIN_HINT}"
            )
        credentials: Credentials = Credentials.from_authorized_user_info(info)  # type: ignore[no-untyped-call]
    except GoogleAuthUnavailable:
        raise
    except (ValueError, TypeError, KeyError):
        raise GoogleAuthUnavailable(f"stored Google token is unreadable; {LOGIN_HINT}") from None
    return credentials


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
    return any((parent / ".git").exists() for parent in path.parents)


class Flow(Protocol):
    def run_local_server(self, **kwargs: Any) -> Any: ...


type FlowFactory = Callable[[str, list[str]], Flow]


def _installed_app_flow(client_secrets: str, scopes: list[str]) -> Flow:
    flow: Flow = InstalledAppFlow.from_client_secrets_file(client_secrets, scopes=scopes)
    return flow


def login(
    client_secrets: Path,
    store: TokenStore,
    *,
    open_browser: bool = True,
    flow_factory: FlowFactory = _installed_app_flow,
) -> None:
    """Run the installed-app OAuth flow on a loopback port and keep the token in the keyring."""
    try:
        path = client_secrets.expanduser().resolve(strict=True)
    except OSError:
        raise GoogleAuthError("OAuth client file not found") from None
    if not path.is_file():
        raise GoogleAuthError("OAuth client path is not a file")
    if inside_git_repository(path):
        raise GoogleAuthError("OAuth client file must be stored outside any git repository")
    try:
        flow = flow_factory(str(path), list(SCOPES))
    except (ValueError, KeyError, OSError):
        raise GoogleAuthError("OAuth client file is not a valid Desktop app client") from None
    try:
        credentials = flow.run_local_server(
            host="localhost",
            port=0,
            open_browser=open_browser,
            authorization_prompt_message="請在瀏覽器開啟以下網址並授權（唯讀行事曆）：\n{url}",
            success_message="授權完成，可以關閉此分頁。",
        )
    except Exception:
        raise GoogleAuthError("Google sign-in did not complete") from None
    granted = _scope_set(getattr(credentials, "granted_scopes", None) or credentials.scopes)
    if granted != frozenset(SCOPES):
        raise GoogleAuthError("Google granted a different scope set; token discarded")
    if not getattr(credentials, "refresh_token", None):
        raise GoogleAuthError("Google returned no refresh token; token discarded")
    token = json.loads(credentials.to_json())
    token["scopes"] = list(SCOPES)
    store.save(json.dumps(token))


def status(store: TokenStore) -> str:
    """Human-readable authorization state; never includes token material."""
    try:
        load_credentials(store)
    except GoogleAuthError as exc:
        return f"未授權：{exc}"
    return "已授權（scope：calendar.readonly，token 存於 OS keyring）"
