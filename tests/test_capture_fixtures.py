"""Tests for the fixture capture script's safety rails."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import aiohttp
import pytest

from custom_components.tel2.const import API_URL
from scripts import capture_fixtures
from scripts.capture_fixtures import (
    READ_ONLY_ACTIONS,
    build_request,
    main,
    write_fixtures,
)
from tests.fake_tel2 import ACTION_CONTEXTS
from tests.fixture_loader import CAPTURED_DIR

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


def test_allowlist_contexts_match_the_ones_the_integration_sends() -> None:
    assert dict(READ_ONLY_ACTIONS) == {
        "Get-Token": "",
        **{
            action: ACTION_CONTEXTS[action]
            for action in READ_ONLY_ACTIONS
            if action != "Get-Token"
        },
    }


def test_build_request_refuses_the_token_reset_parameter() -> None:
    with pytest.raises(ValueError, match="reset"):
        build_request("Get-Numbers", token="t", params={"reset": "1"})


def test_build_request_sends_empty_params_by_default() -> None:
    assert build_request("Get-Webhooks", token="t")["params"] == {}


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


def _run_script(*python_flags: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTHONPATH" and not key.startswith("TEL2_")
    }
    return subprocess.run(  # noqa: S603
        [sys.executable, *python_flags, capture_fixtures.__file__, "--help"],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_script_runs_from_any_directory_without_the_repo_on_the_path(
    tmp_path: Path,
) -> None:
    result = _run_script(cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "read-only Tel2 API actions" in result.stdout


def test_script_explains_how_to_run_it_outside_the_dev_environment(
    tmp_path: Path,
) -> None:
    result = _run_script("-S", cwd=tmp_path)

    assert result.returncode == 1
    assert "Cannot import custom_components.tel2" in result.stderr
    assert "Traceback" not in result.stderr


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


class _HttpStatus:
    def __init__(self, status: int) -> None:
        self.status = status


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if isinstance(self._payload, _HttpStatus):
            raise aiohttp.ClientResponseError(
                None,  # type: ignore[arg-type]
                (),
                status=self._payload.status,
            )

    async def json(self, content_type: str | None = None) -> Any:
        return self._payload


class _FakeSession:
    calls: list[tuple[str, dict[str, Any]]]
    overrides: dict[str, Any]
    timeout: aiohttp.ClientTimeout | None

    def __init__(self, *, timeout: aiohttp.ClientTimeout | None = None) -> None:
        type(self).timeout = timeout

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
    _FakeSession.timeout = None
    monkeypatch.delenv("TEL2_LOGIN", raising=False)
    monkeypatch.delenv("TEL2_PASSWORD", raising=False)
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
    assert stdout.startswith(f"Wrote {len(written) - 1} anonymised fixtures to {out}.")
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
        "Data": {"Reference": "123456789012", "Ledger": "998877665544"},
    }
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        main(["--out", str(out)])
    message = str(exc.value.code)
    assert message.splitlines()[1:] == [
        f"{name}.Data.{key}"
        for name in ("get_account_balance.json", "error_invalid_token.json")
        for key in ("Reference", "Ledger")
    ]
    assert "123456789012" not in message
    assert not out.exists()


def test_credentials_can_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEL2_LOGIN", " env-login ")
    monkeypatch.setenv("TEL2_PASSWORD", "env-password")

    def _no_prompt(*_: object) -> str:
        raise AssertionError("prompted despite environment credentials")

    monkeypatch.setattr(capture_fixtures.getpass, "getpass", _no_prompt)
    seen: list[tuple[str, str]] = []

    async def _capture(login: str, password: str) -> Any:
        seen.append((login, password))
        raise TimeoutError

    monkeypatch.setattr(capture_fixtures, "capture", _capture)
    with pytest.raises(SystemExit):
        capture_fixtures.main(["--out", "unused"])
    assert seen == [("env-login", "env-password")]


def _requests(fake: type[_FakeSession]) -> list[dict[str, Any]]:
    return [body for _, body in fake.calls]


def test_main_sends_the_capture_plan_in_order(
    fake_tel2: type[_FakeSession], tmp_path: Path
) -> None:
    assert main(["--out", str(tmp_path)]) == 0

    requests = _requests(fake_tel2)
    assert [body["action"] for body in requests] == [
        "Get-Token",
        "Get-Customer-Details",
        "Get-Account-Balance",
        "Get-Current-Plan-Summary",
        "Get-Numbers",
        "Get-Line-Names",
        "Get-Subscriber-Status",
        "Get-All-Pref-Summary",
        "Get-Webhooks",
        "Get-Line-Bundle-Usage",
        "Get-Account-Balance",
        "Get-Token",
    ]
    assert requests[0] == {"login": LOGIN, "password": PASSWORD, "action": "Get-Token"}
    assert [body["token"] for body in requests[1:-1]] == [TOKEN] * 9 + ["invalid"]
    assert requests[4]["params"] == {
        "orderby": "",
        "orderdirection": "",
        "searchby": "",
        "searchterm": "",
    }
    assert requests[9]["params"] == {"line": "01134960847"}
    assert all(body["params"] == {} for body in (*requests[1:4], *requests[5:9]))
    assert requests[10]["params"] == {}
    wrong = requests[-1]
    assert wrong["login"] == LOGIN
    assert wrong["password"] != PASSWORD
    assert len(wrong["password"]) >= 32


def test_main_bounds_every_request_with_a_timeout(
    fake_tel2: type[_FakeSession], tmp_path: Path
) -> None:
    assert main(["--out", str(tmp_path)]) == 0

    assert fake_tel2.timeout is not None
    assert fake_tel2.timeout.total == 30


def test_main_writes_the_committed_capture_layout(
    fake_tel2: type[_FakeSession], tmp_path: Path
) -> None:
    assert main(["--out", str(tmp_path)]) == 0

    assert (tmp_path / "index.json").read_text() == (
        CAPTURED_DIR / "index.json"
    ).read_text()


@pytest.mark.parametrize(
    ("data", "lines"),
    [
        (
            [
                {"ServiceName": "01632960001"},
                {"ServiceName": "01632960001"},
                {"ServiceName": ""},
                {"Parameter_4": "Lounge"},
                {"ServiceName": 1632960004},
                "01632960003",
                {"ServiceName": "07700900123"},
            ],
            ["01632960001", "07700900123"],
        ),
        (
            {
                "7": {"ServiceName": "07700900123"},
                "8": {"ServiceName": "01632960001"},
            },
            ["07700900123", "01632960001"],
        ),
        ("", []),
    ],
    ids=["list", "keyed", "no-data"],
)
def test_bundle_usage_is_requested_once_per_listed_line(
    fake_tel2: type[_FakeSession], tmp_path: Path, data: Any, lines: list[str]
) -> None:
    fake_tel2.overrides["Get-Numbers"] = {"Result": "1", "Data": data}

    assert main(["--out", str(tmp_path)]) == 0

    bundle_requests = [
        body["params"]
        for body in _requests(fake_tel2)
        if body["action"] == "Get-Line-Bundle-Usage"
    ]
    assert bundle_requests == [{"line": line} for line in lines]
    bundles = json.loads((tmp_path / "get_line_bundle_usage.json").read_text())
    assert len(bundles) == len(lines)


@pytest.mark.parametrize(
    ("response", "status"),
    [
        ({"Result": "-1", "Status": "Error", "Message": "Bad login"}, "Error"),
        ({"Result": "1", "Status": "Success", "Data": {"token": ""}}, "Success"),
        ({"Result": "1", "Status": "Odd", "Data": "token"}, "Odd"),
        ({"Result": "1", "Status": "Odd", "Data": {"token": 7}}, "Odd"),
        (["token"], "None"),
    ],
    ids=["error", "empty", "data-not-object", "token-not-text", "not-object"],
)
def test_main_stops_after_get_token_without_a_token(
    fake_tel2: type[_FakeSession], tmp_path: Path, response: Any, status: str
) -> None:
    fake_tel2.overrides["Get-Token"] = response
    out = tmp_path / "out"

    with pytest.raises(SystemExit) as exc:
        main(["--out", str(out)])

    assert f"(Status: {status})" in str(exc.value.code)
    assert [body["action"] for body in _requests(fake_tel2)] == ["Get-Token"]
    assert not out.exists()


def test_main_writes_nothing_when_tel2_answers_with_an_http_error(
    fake_tel2: type[_FakeSession], tmp_path: Path
) -> None:
    fake_tel2.overrides["Get-Webhooks"] = _HttpStatus(500)
    out = tmp_path / "out"

    with pytest.raises(SystemExit) as exc:
        main(["--out", str(out)])

    assert "ClientResponseError" in str(exc.value.code)
    assert not out.exists()


def test_main_writes_to_the_captured_fixtures_directory_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEL2_LOGIN", LOGIN)
    monkeypatch.setenv("TEL2_PASSWORD", PASSWORD)

    async def _capture(login: str, password: str) -> Any:
        return {}, TOKEN

    written: list[Path] = []
    monkeypatch.setattr(capture_fixtures, "capture", _capture)
    monkeypatch.setattr(
        capture_fixtures, "write_fixtures", lambda out, _: written.append(out)
    )

    assert main([]) == 0
    assert [path.resolve() for path in written] == [CAPTURED_DIR.resolve()]


def test_write_fixtures_keeps_text_readable_and_marks_keyed_files(
    tmp_path: Path,
) -> None:
    out = tmp_path / "nested" / "captured"

    write_fixtures(
        out,
        {
            "get_webhooks.json": {"Message": "Aucune donnée"},
            "get_line_bundle_usage.json": {},
        },
    )

    assert (out / "get_webhooks.json").read_text(encoding="utf-8") == (
        '{\n  "Message": "Aucune donnée"\n}\n'
    )
    assert json.loads((out / "index.json").read_text())["files"] == [
        {"file": "get_webhooks.json", "action": "Get-Webhooks", "source": "captured"},
        {
            "file": "get_line_bundle_usage.json",
            "action": "Get-Line-Bundle-Usage",
            "source": "captured",
            "keyed_by": "line",
        },
    ]
