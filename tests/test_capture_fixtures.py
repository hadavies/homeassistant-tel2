"""Tests for the fixture capture script's safety rails."""

import copy
import json
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from custom_components.tel2.const import API_URL
from scripts import capture_fixtures
from scripts.capture_fixtures import READ_ONLY_ACTIONS, build_request, main

WRITE_PREFIXES = (
    "Set-",
    "Add-",
    "Delete-",
    "Update-",
    "Enable-",
    "Disable-",
    "Reset-",
    "Reboot-",
    "Resync-",
    "Send-",
)


def test_allowlist_contains_no_write_actions() -> None:
    assert [a for a in READ_ONLY_ACTIONS if a.startswith(WRITE_PREFIXES)] == []


def test_allowlist_covers_the_capture_actions() -> None:
    assert set(READ_ONLY_ACTIONS) == {
        "Get-Token",
        "Get-Customer-Details",
        "Get-Account-Balance",
        "Get-Current-Plan-Summary",
        "Get-Line-Bundle-Usage",
        "Get-Numbers",
        "Get-Line-Names",
        "Get-Subscriber-Status",
        "Get-All-Pref-Summary",
        "Get-Webhooks",
    }


def test_build_request_refuses_actions_outside_the_allowlist() -> None:
    with pytest.raises(ValueError, match="Set-Pref-Value"):
        build_request("Set-Pref-Value", token="t")


def test_build_request_uses_the_action_context() -> None:
    body = build_request("Get-Numbers", token="t", params={"orderby": ""})
    assert body == {
        "token": "t",
        "action": "Get-Numbers",
        "context": "voice",
        "params": {"orderby": ""},
    }


def test_token_request_carries_credentials_in_the_body_without_reset() -> None:
    body = build_request("Get-Token", login="user", password="pw")
    assert body == {"login": "user", "password": "pw", "action": "Get-Token"}
    assert "reset" not in body


def test_help_exits_without_prompting(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*_: object) -> str:
        raise AssertionError("prompted")

    monkeypatch.setattr("builtins.input", _fail)
    monkeypatch.setattr("getpass.getpass", _fail)
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


LOGIN = "portaluser42"
PASSWORD = "Pa55-w0rd!x"
TOKEN = "tok3nValueQ9"
NO_DATA = {"Result": "0", "Status": "Success", "Message": "No Data Found.", "Data": ""}


def _tel2(body: dict[str, Any], overrides: dict[str, Any]) -> Any:
    action = body["action"]
    if action in overrides:
        override = overrides[action]
        if isinstance(override, BaseException):
            raise override
        return override
    if action == "Get-Token":
        if body["password"] == PASSWORD:
            return {"Result": "1", "Status": "Success", "Data": {"token": TOKEN}}
        return {"Result": "-1", "Status": "Error", "Message": f"Bad login {LOGIN}"}
    if body["token"] == "invalid":
        return {
            "Result": "-1",
            "Status": "Error",
            "Message": "Invalid Token",
            "Data": "",
        }
    if action == "Get-Customer-Details":
        return {
            "Result": "1",
            "Data": {
                "Account_Code": "48213977",
                "CustomerName": "Morwenna Pryce",
                "Note": f"session {TOKEN} for {LOGIN} pw {PASSWORD}",
            },
        }
    if action == "Get-Numbers":
        return {
            "Result": "1",
            "Data": [
                {
                    "ServiceName": "01134960847",
                    "Parameter_1": "SipPass123",
                    "Parameter_4": "Lounge",
                }
            ],
        }
    if action == "Get-Line-Bundle-Usage":
        return {"Result": "1", "Data": {"USED": "10", "TOTAL": "100"}}
    return NO_DATA


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self, content_type: str | None = None) -> Any:
        return self._payload


class _FakeSession:
    calls: list[tuple[str, dict[str, Any]]]
    overrides: dict[str, Any]

    def __init__(self, *_: object, **__: object) -> None:
        pass

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def post(self, url: str, *, json: dict[str, Any]) -> _FakeResponse:
        type(self).calls.append((url, copy.deepcopy(json)))
        return _FakeResponse(_tel2(json, type(self).overrides))


@pytest.fixture
def fake_tel2(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSession]:
    _FakeSession.calls = []
    _FakeSession.overrides = {}
    monkeypatch.setattr(capture_fixtures.aiohttp, "ClientSession", _FakeSession)
    answers = iter((LOGIN, PASSWORD))
    monkeypatch.setattr(capture_fixtures.getpass, "getpass", lambda *_: next(answers))

    def _no_input(*_: object) -> str:
        raise AssertionError("input() echoes")

    monkeypatch.setattr("builtins.input", _no_input)
    return _FakeSession


def test_main_captures_with_only_safe_requests(
    fake_tel2: type[_FakeSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen_secrets: list[tuple[str, ...]] = []
    real = capture_fixtures.anonymise_responses

    def _spy(responses: Any, *, secrets: tuple[str, ...]) -> Any:
        seen_secrets.append(tuple(secrets))
        return real(responses, secrets=secrets)

    monkeypatch.setattr(capture_fixtures, "anonymise_responses", _spy)
    out = tmp_path / "out"
    assert main(["--out", str(out)]) == 0

    assert seen_secrets == [(LOGIN, PASSWORD, TOKEN)]
    for url, body in fake_tel2.calls:
        assert url == API_URL
        assert body["action"] in READ_ONLY_ACTIONS
        assert "reset" not in json.dumps(body)
        if body["action"] != "Get-Token":
            assert LOGIN not in json.dumps(body)
            assert PASSWORD not in json.dumps(body)
    assert sum(PASSWORD in json.dumps(body) for _, body in fake_tel2.calls) == 1

    written = {path.name: path.read_text() for path in out.iterdir()}
    index = json.loads(written["index.json"])
    assert {entry["file"] for entry in index["files"]} == set(written) - {"index.json"}
    assert {entry["source"] for entry in index["files"]} == {"captured"}
    assert {"error_wrong_password.json", "error_invalid_token.json"} <= set(written)
    stdout = capsys.readouterr().out
    for text in (*written.values(), stdout):
        for secret in (TOKEN, PASSWORD, LOGIN, "SipPass123", "48213977", "Morwenna"):
            assert secret not in text


def test_main_writes_nothing_when_a_request_fails(
    fake_tel2: type[_FakeSession], tmp_path: Path
) -> None:
    fake_tel2.overrides["Get-Webhooks"] = aiohttp.ClientConnectionError()
    out = tmp_path / "out"
    with pytest.raises(SystemExit):
        main(["--out", str(out)])
    assert not out.exists()


def test_main_writes_nothing_when_the_leak_check_trips(
    fake_tel2: type[_FakeSession], tmp_path: Path
) -> None:
    fake_tel2.overrides["Get-Account-Balance"] = {
        "Result": "1",
        "Data": {"Reference": "123456789012"},
    }
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        main(["--out", str(out)])
    assert "get_account_balance.json.Data.Reference" in str(exc.value.code)
    assert "123456789012" not in str(exc.value.code)
    assert not out.exists()
