"""OAuth / keyring boundary: readonly scope only, tokens never leave the keyring."""

import json
from pathlib import Path
from typing import Any

import keyring
import keyring.errors
import pytest
import requests
from keyring.backend import KeyringBackend
from keyring.backends import fail

from personal_ai_assistant import google_auth_cli
from personal_ai_assistant.adapters import google_auth
from personal_ai_assistant.adapters.google_auth import (
    CALENDAR_READONLY_SCOPE,
    KEYRING_SERVICE,
    KEYRING_USERNAME,
    GoogleAuthError,
    GoogleAuthUnavailable,
    KeyringTokenStore,
    build_calendar_service,
    load_credentials,
    login,
    status,
)

REFRESH = "1//synthetic-refresh-token-000"  # gitleaks:allow
CLIENT_SECRET = "GOCSPX-synthetic-client-secret"  # noqa: S105 - synthetic  gitleaks:allow
TOKEN = {
    "token": None,
    "refresh_token": REFRESH,
    "client_id": "synthetic.apps.googleusercontent.com",
    "client_secret": CLIENT_SECRET,
    "token_uri": "https://oauth2.googleapis.com/token",
    "scopes": [CALENDAR_READONLY_SCOPE],
}


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self, *, broken: bool = False) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self.data: dict[tuple[str, str], str] = {}
        self.broken = broken

    def get_password(self, service: str, username: str) -> str | None:
        if self.broken:
            raise keyring.errors.KeyringLocked("locked")
        return self.data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.broken:
            raise keyring.errors.PasswordSetError("nope")
        self.data[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.data:
            raise keyring.errors.PasswordDeleteError("missing")
        del self.data[(service, username)]


class PlaintextKeyring(MemoryKeyring):
    pass


PlaintextKeyring.__module__ = "keyrings.alt.file"


SECURE_FOR_TESTS = frozenset({f"{MemoryKeyring.__module__}.MemoryKeyring"})


def make_store(backend: KeyringBackend | None = None) -> KeyringTokenStore:
    return KeyringTokenStore(backend, secure_backends=SECURE_FOR_TESTS)


def stored(backend: MemoryKeyring) -> str | None:
    return backend.data.get((KEYRING_SERVICE, KEYRING_USERNAME))


# --- token store --------------------------------------------------------------


def test_store_roundtrip_and_delete() -> None:
    backend = MemoryKeyring()
    store = make_store(backend)
    assert store.load() is None
    store.save("x")
    assert store.load() == "x"
    store.delete()
    store.delete()  # idempotent
    assert store.load() is None


@pytest.mark.parametrize("backend", [fail.Keyring(), PlaintextKeyring()])  # type: ignore[no-untyped-call]
def test_insecure_or_missing_keyring_is_refused(backend: KeyringBackend) -> None:
    store = make_store(backend)
    with pytest.raises(GoogleAuthUnavailable, match="no secure OS keyring"):
        store.load()
    with pytest.raises(GoogleAuthUnavailable):
        store.save("x")


def test_default_backend_without_keyring_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())  # type: ignore[no-untyped-call]
    with pytest.raises(GoogleAuthUnavailable):
        make_store().load()


def test_locked_keyring_is_unavailable() -> None:
    store = make_store(MemoryKeyring(broken=True))
    with pytest.raises(GoogleAuthUnavailable, match="locked"):
        store.load()
    with pytest.raises(GoogleAuthError, match="could not store"):
        store.save("x")


# --- loading credentials ------------------------------------------------------


def store_with(token: Any) -> KeyringTokenStore:
    backend = MemoryKeyring()
    if token is not None:
        backend.data[(KEYRING_SERVICE, KEYRING_USERNAME)] = (
            token if isinstance(token, str) else json.dumps(token)
        )
    return make_store(backend)


def test_valid_readonly_token_loads() -> None:
    credentials = load_credentials(store_with(TOKEN))
    assert credentials.refresh_token == REFRESH
    assert list(credentials.scopes or ()) == [CALENDAR_READONLY_SCOPE]


@pytest.mark.parametrize(
    ("token", "match"),
    [
        (None, "not authorized"),
        ("{not json", "unreadable"),
        ("[]", "unreadable"),
        (TOKEN | {"scopes": ["https://www.googleapis.com/auth/calendar"]}, "unexpected scopes"),
        (
            TOKEN | {"scopes": [CALENDAR_READONLY_SCOPE, "https://mail.google.com/"]},
            "unexpected scopes",
        ),
        ({k: v for k, v in TOKEN.items() if k != "scopes"}, "unexpected scopes"),
        ({k: v for k, v in TOKEN.items() if k != "refresh_token"}, "unreadable"),
    ],
)
def test_unusable_tokens_are_refused_without_leaking(token: Any, match: str) -> None:
    with pytest.raises(GoogleAuthUnavailable, match=match) as info:
        load_credentials(store_with(token))
    assert REFRESH not in str(info.value)
    assert CLIENT_SECRET not in str(info.value)
    assert "pai-google-auth login" in str(info.value)


def test_service_builds_offline_from_credentials() -> None:
    service = build_calendar_service(load_credentials(store_with(TOKEN)))
    request = service.events().list(calendarId="primary")
    assert request.uri.startswith("https://www.googleapis.com/calendar/v3/calendars/primary/events")


def test_status_never_shows_token() -> None:
    assert "已授權" in status(store_with(TOKEN))
    text = status(store_with(None))
    assert text.startswith("未授權")
    assert REFRESH not in status(store_with(TOKEN))


# --- login --------------------------------------------------------------------


class FakeCredentials:
    def __init__(self, *, scopes: list[str], refresh_token: str | None = REFRESH) -> None:
        self.granted_scopes = scopes
        self.scopes = scopes
        self.refresh_token = refresh_token

    def to_json(self) -> str:
        return json.dumps(TOKEN | {"scopes": self.scopes, "refresh_token": self.refresh_token})


class FakeFlow:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.kwargs: dict[str, Any] = {}

    def run_local_server(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def client_file(tmp_path: Path) -> Path:
    path = tmp_path / "client.json"
    path.write_text('{"installed": {"client_id": "x"}}', encoding="utf-8")
    path.chmod(0o600)
    return path


def factory_for(flow: FakeFlow, seen: list[tuple[str, list[str]]]) -> Any:
    def factory(path: str, scopes: list[str]) -> FakeFlow:
        seen.append((path, scopes))
        return flow

    return factory


def test_login_requests_only_readonly_and_stores_in_keyring(tmp_path: Path) -> None:
    backend = MemoryKeyring()
    flow = FakeFlow(FakeCredentials(scopes=[CALENDAR_READONLY_SCOPE]))
    seen: list[tuple[str, list[str]]] = []
    login(client_file(tmp_path), make_store(backend), flow_factory=factory_for(flow, seen))
    assert seen == [(str(client_file(tmp_path).resolve()), [CALENDAR_READONLY_SCOPE])]
    assert flow.kwargs["host"] == "localhost"
    assert flow.kwargs["port"] == 0
    saved = json.loads(stored(backend) or "{}")
    assert saved["refresh_token"] == REFRESH
    assert saved["scopes"] == [CALENDAR_READONLY_SCOPE]
    # Nothing is written next to the client file.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["client.json"]


@pytest.mark.parametrize(
    ("credentials", "match"),
    [
        (FakeCredentials(scopes=["https://www.googleapis.com/auth/calendar"]), "different scope"),
        (
            FakeCredentials(scopes=[CALENDAR_READONLY_SCOPE, "openid"]),
            "different scope",
        ),
        (FakeCredentials(scopes=[CALENDAR_READONLY_SCOPE], refresh_token=None), "no refresh"),
        (RuntimeError(f"oauth failure {CLIENT_SECRET}"), "did not complete"),
    ],
)
def test_login_failures_store_nothing(tmp_path: Path, credentials: Any, match: str) -> None:
    backend = MemoryKeyring()
    flow = FakeFlow(credentials)
    with pytest.raises(GoogleAuthError, match=match) as info:
        login(client_file(tmp_path), make_store(backend), flow_factory=factory_for(flow, []))
    assert stored(backend) is None
    assert CLIENT_SECRET not in str(info.value)


def test_login_refuses_client_file_inside_git_repo(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "config"
    nested.mkdir()
    with pytest.raises(GoogleAuthError, match="outside any git repository"):
        login(
            client_file(nested),
            make_store(MemoryKeyring()),
            flow_factory=factory_for(FakeFlow(None), []),
        )


def test_login_refuses_this_repository() -> None:
    inside = Path(google_auth.__file__)
    with pytest.raises(GoogleAuthError, match="outside any git repository"):
        login(inside, make_store(MemoryKeyring()), flow_factory=factory_for(FakeFlow(None), []))


@pytest.mark.parametrize("make", [lambda p: p / "missing.json", lambda p: p])
def test_login_requires_an_existing_file(tmp_path: Path, make: Any) -> None:
    with pytest.raises(GoogleAuthError, match=r"not found|not a file"):
        login(
            make(tmp_path),
            make_store(MemoryKeyring()),
            flow_factory=factory_for(FakeFlow(None), []),
        )


def test_login_rejects_invalid_client_file(tmp_path: Path) -> None:
    def bad_factory(path: str, scopes: list[str]) -> Any:
        raise ValueError(f"Client secrets must be for a web or installed app {CLIENT_SECRET}")

    with pytest.raises(GoogleAuthError, match="not a valid Desktop app client") as info:
        login(client_file(tmp_path), make_store(MemoryKeyring()), flow_factory=bad_factory)
    assert CLIENT_SECRET not in str(info.value)


def test_real_flow_factory_rejects_non_installed_client(tmp_path: Path) -> None:
    path = tmp_path / "web.json"
    path.write_text(json.dumps({"web": {"client_id": "x"}}), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(GoogleAuthError, match="not a valid Desktop app client"):
        login(path, make_store(MemoryKeyring()))


# --- pai-google-auth ----------------------------------------------------------


def run_cli(argv: list[str], store: Any, flow: FakeFlow | None = None) -> tuple[int, str]:
    lines: list[str] = []
    factory = None if flow is None else factory_for(flow, [])
    code = google_auth_cli.main(argv, store=store, flow_factory=factory, show=lines.append)
    return code, "\n".join(lines)


def test_cli_login_status_logout(tmp_path: Path) -> None:
    backend = MemoryKeyring()
    store = make_store(backend)
    flow = FakeFlow(FakeCredentials(scopes=[CALENDAR_READONLY_SCOPE]))
    code, out = run_cli(
        ["login", "--client-secrets", str(client_file(tmp_path)), "--no-browser"], store, flow
    )
    assert code == google_auth_cli.EXIT_OK
    assert flow.kwargs["open_browser"] is False
    assert REFRESH not in out
    assert CLIENT_SECRET not in out
    code, out = run_cli(["status"], store)
    assert code == 0
    assert "已授權" in out
    code, out = run_cli(["logout"], store)
    assert code == 0
    assert stored(backend) is None
    _, out = run_cli(["status"], store)
    assert "未授權" in out


def test_cli_login_failure_exit_code(tmp_path: Path) -> None:
    code, out = run_cli(
        ["login", "--client-secrets", str(tmp_path / "nope.json")],
        make_store(MemoryKeyring()),
    )
    assert code == google_auth_cli.EXIT_FAILED
    assert "not found" in out


def test_cli_without_keyring_reports_failure() -> None:
    code, out = run_cli(["status"], make_store(fail.Keyring()))  # type: ignore[no-untyped-call]
    assert code == 0
    assert "no secure OS keyring" in out


# --- audit hardening (B4 review) -------------------------------------------------


class ChainerBackend(KeyringBackend):
    priority = 1

    def __init__(self, backends: list[KeyringBackend]) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self.backends = backends

    def get_password(self, service: str, username: str) -> str | None:
        for b in self.backends:
            if (value := b.get_password(service, username)) is not None:
                return value
        return None

    def set_password(self, service: str, username: str, password: str) -> None:
        self.backends[0].set_password(service, username, password)

    def delete_password(self, service: str, username: str) -> None:
        self.backends[0].delete_password(service, username)


ChainerBackend.__module__ = "keyring.backends.chainer"


class DroppingKeyring(MemoryKeyring):
    def set_password(self, service: str, username: str, password: str) -> None:
        return  # silently discards, like the null backend


def test_chainer_uses_only_its_secure_member() -> None:
    plaintext, secure = PlaintextKeyring(), MemoryKeyring()
    plaintext.data[(KEYRING_SERVICE, KEYRING_USERNAME)] = "PLAINTOKEN"
    store = make_store(ChainerBackend([plaintext, secure]))
    assert store.load() is None  # the plaintext copy is never read
    store.save("x")
    assert stored(secure) == "x"
    assert stored(plaintext) == "PLAINTOKEN"


def test_chainer_without_secure_member_is_refused() -> None:
    with pytest.raises(GoogleAuthUnavailable, match="no secure OS keyring"):
        make_store(ChainerBackend([PlaintextKeyring()])).load()


def test_unknown_backends_are_refused_by_default() -> None:
    with pytest.raises(GoogleAuthUnavailable, match="no secure OS keyring"):
        KeyringTokenStore(MemoryKeyring()).load()
    null = __import__("keyring.backends.null", fromlist=["Keyring"]).Keyring()
    with pytest.raises(GoogleAuthUnavailable, match="no secure OS keyring"):
        KeyringTokenStore(null).save("x")


def test_save_is_verified_by_reading_back() -> None:
    store = KeyringTokenStore(
        DroppingKeyring(), secure_backends=frozenset({f"{__name__}.DroppingKeyring"})
    )
    with pytest.raises(GoogleAuthError, match="could not store"):
        store.save("x")


def test_token_parse_errors_carry_no_exception_context() -> None:
    for raw in ("{" + REFRESH, json.dumps(TOKEN | {"expiry": {"not": "a string"}})):
        with pytest.raises(GoogleAuthUnavailable) as info:
            load_credentials(store_with(raw))
        assert info.value.__context__ is None
        assert info.value.__cause__ is None


def test_token_endpoint_is_pinned() -> None:
    with pytest.raises(GoogleAuthUnavailable, match="token endpoint"):
        load_credentials(store_with(TOKEN | {"token_uri": "https://evil.example.com/token"}))


def test_scope_change_warning_revokes_and_stores_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    revoked: list[dict[str, Any]] = []
    monkeypatch.setattr(requests, "post", lambda url, **kw: revoked.append({"url": url, **kw}))
    warning = Warning("Scope has changed")
    warning.token = {"access_token": "at", "refresh_token": "1//BROAD-RT"}  # type: ignore[attr-defined]
    backend = MemoryKeyring()
    with pytest.raises(GoogleAuthError, match="revoked and discarded") as info:
        login(
            client_file(tmp_path),
            make_store(backend),
            flow_factory=factory_for(FakeFlow(warning), []),
        )
    assert stored(backend) is None
    assert revoked == [
        {"url": google_auth.REVOKE_URI, "data": {"token": "1//BROAD-RT"}, "timeout": 10}
    ]
    assert info.value.__context__ is None


def test_world_readable_client_file_is_refused(tmp_path: Path) -> None:
    path = client_file(tmp_path)
    path.chmod(0o644)
    with pytest.raises(GoogleAuthError, match="chmod 600"):
        login(path, make_store(MemoryKeyring()), flow_factory=factory_for(FakeFlow(None), []))
