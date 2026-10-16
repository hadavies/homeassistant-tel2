"""Tests for the Tel2 API client transport and session lifecycle."""

import asyncio
from collections.abc import AsyncGenerator
import json
import logging
from typing import Any

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from yarl import URL

from custom_components.tel2.api import (
    Tel2ApiError,
    Tel2AuthError,
    Tel2Client,
    Tel2ConnectionError,
    Tel2NoData,
)
from custom_components.tel2.const import API_URL
from tests.fake_tel2 import FakeResponse, FakeTel2Api, RawResponse
from tests.fixture_loader import load_fixture

LOGIN = "portal-login-for-tests"
PASSWORD = "portal-password-for-tests"
FIXTURE_TOKEN = load_fixture("get_token.json")["Data"]["token"]


def envelope(
    result: str, message: str, data: Any = None, status: str = ""
) -> dict[str, Any]:
    return {
        "Result": result,
        "Status": status or {"1": "Success", "0": "Success"}.get(result, "Error"),
        "Message": message,
        "Data": [] if data is None else data,
    }


def token_response(token: str) -> dict[str, Any]:
    return envelope("1", "Method Executed Successfully.", {"token": token})


@pytest.fixture
async def session(fake_tel2: FakeTel2Api) -> AsyncGenerator[aiohttp.ClientSession]:
    session = fake_tel2.create_session()
    yield session
    await session.close()


@pytest.fixture
def refreshed_tokens() -> list[str]:
    return []


@pytest.fixture
def client(session: aiohttp.ClientSession, refreshed_tokens: list[str]) -> Tel2Client:
    return Tel2Client(
        session, LOGIN, PASSWORD, on_token_refreshed=refreshed_tokens.append
    )


async def test_success_returns_data(client: Tel2Client) -> None:
    data = await client._call("account", "Get-Account-Balance")

    assert data == load_fixture("get_account_balance.json")["Data"]


async def test_no_data_raises_internally_and_optional_call_returns_none(
    client: Tel2Client,
) -> None:
    with pytest.raises(Tel2NoData):
        await client._call("voice", "Get-Subscriber-Status")

    assert await client._call_optional("voice", "Get-Subscriber-Status") is None


async def test_optional_call_returns_data_on_success(client: Tel2Client) -> None:
    data = await client._call_optional("account", "Get-Account-Balance")

    assert data == load_fixture("get_account_balance.json")["Data"]


@pytest.mark.parametrize(
    "message",
    [
        "Invalid token.",
        "Invalid login details.",
        "Login not specified.",
        "Token not specified",
        "Invalid password",
        "Token expired",
        "NOT AUTHENTICATED",
        "Unauthorised request",
        "Unauthorized",
    ],
)
async def test_auth_messages_raise_auth_error(
    client: Tel2Client, fake_tel2: FakeTel2Api, message: str
) -> None:
    fake_tel2.respond("Get-Account-Balance", envelope("-1", message))

    with pytest.raises(Tel2AuthError):
        await client._call("account", "Get-Account-Balance")


@pytest.mark.parametrize(
    ("fixture", "context", "action"),
    [
        ("error_access_refused_account.json", "account", "Get-Account-Balance"),
        ("error_access_refused_voice.json", "voice", "Get-Subscriber-Status"),
        ("error_access_refused_api.json", "api", "Get-Webhooks"),
        ("error_generic.json", "account", "Get-Account-Balance"),
    ],
)
async def test_non_auth_errors_raise_api_error_without_retry(
    client: Tel2Client,
    fake_tel2: FakeTel2Api,
    fixture: str,
    context: Any,
    action: str,
) -> None:
    fake_tel2.respond(action, fixture)

    with pytest.raises(Tel2ApiError) as err:
        await client._call(context, action)

    assert err.value.message == load_fixture(fixture)["Message"]
    assert err.value.context == context
    assert err.value.action == action
    assert not isinstance(err.value, Tel2AuthError)
    assert fake_tel2.call_count("Get-Token") == 1
    assert fake_tel2.call_count(action) == 1


async def test_error_status_with_non_negative_result_is_api_error(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond(
        "Get-Account-Balance",
        envelope("0", "An error occurred processing your request.", status="Error"),
    )

    with pytest.raises(Tel2ApiError):
        await client._call("account", "Get-Account-Balance")


async def test_unknown_result_is_api_error(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond(
        "Get-Account-Balance", envelope("2", "Something new", status="Odd")
    )

    with pytest.raises(Tel2ApiError):
        await client._call("account", "Get-Account-Balance")


@pytest.mark.parametrize(
    "response",
    [
        aiohttp.ClientConnectionError("connection refused"),
        aiohttp.ServerDisconnectedError(),
        TimeoutError(),
        RawResponse("this is not json"),
        RawResponse("<html>Bad gateway</html>", status=502),
        RawResponse(b"\xff\xfe<html>bad gateway</html>", status=502),
        RawResponse(""),
        ["not", "an", "object"],
        RawResponse(json.dumps("a string body")),
        {"Status": "Success", "Message": "No result field", "Data": []},
    ],
    ids=[
        "client-error",
        "disconnected",
        "timeout",
        "invalid-json",
        "http-502-html",
        "http-502-non-utf8",
        "empty-body",
        "list-body",
        "string-body",
        "missing-result",
    ],
)
async def test_transport_failures_raise_connection_error(
    client: Tel2Client, fake_tel2: FakeTel2Api, response: FakeResponse
) -> None:
    fake_tel2.respond("Get-Account-Balance", response)

    with pytest.raises(Tel2ConnectionError):
        await client._call("account", "Get-Account-Balance")


async def test_get_token_transport_failure_raises_connection_error(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Token", TimeoutError())

    with pytest.raises(Tel2ConnectionError):
        await client._call("account", "Get-Account-Balance")
    assert fake_tel2.call_count("Get-Account-Balance") == 0


async def test_get_token_success_without_token_raises_connection_error(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Token", envelope("1", "Method Executed Successfully.", {}))

    with pytest.raises(Tel2ConnectionError):
        await client.async_authenticate()


async def test_token_is_fetched_lazily_and_reused(
    client: Tel2Client, fake_tel2: FakeTel2Api, refreshed_tokens: list[str]
) -> None:
    assert fake_tel2.requests == []

    await client._call("account", "Get-Account-Balance")
    await client._call("account", "Get-Customer-Details")

    assert [request.action for request in fake_tel2.requests] == [
        "Get-Token",
        "Get-Account-Balance",
        "Get-Customer-Details",
    ]
    assert fake_tel2.requests[1].body["token"] == FIXTURE_TOKEN
    assert fake_tel2.requests[2].body["token"] == FIXTURE_TOKEN
    assert refreshed_tokens == [FIXTURE_TOKEN]


async def test_stored_token_is_used_without_get_token(
    session: aiohttp.ClientSession, fake_tel2: FakeTel2Api
) -> None:
    refreshed: list[str] = []
    client = Tel2Client(
        session,
        LOGIN,
        PASSWORD,
        token="stored-token",
        on_token_refreshed=refreshed.append,
    )

    await client._call("account", "Get-Account-Balance")

    assert fake_tel2.call_count("Get-Token") == 0
    assert fake_tel2.requests[0].body["token"] == "stored-token"
    assert refreshed == []


async def test_expired_token_is_renewed_once_and_call_retried(
    client: Tel2Client, fake_tel2: FakeTel2Api, refreshed_tokens: list[str]
) -> None:
    fake_tel2.respond("Get-Token", token_response("token-1"), token_response("token-2"))
    fake_tel2.respond(
        "Get-Account-Balance", "error_invalid_token.json", "get_account_balance.json"
    )

    data = await client._call("account", "Get-Account-Balance")

    assert data == load_fixture("get_account_balance.json")["Data"]
    assert fake_tel2.call_count("Get-Token") == 2
    assert refreshed_tokens == ["token-1", "token-2"]
    assert [r.body["token"] for r in fake_tel2.requests_for("Get-Account-Balance")] == [
        "token-1",
        "token-2",
    ]


async def test_expired_stored_token_is_renewed(
    session: aiohttp.ClientSession, fake_tel2: FakeTel2Api
) -> None:
    refreshed: list[str] = []
    client = Tel2Client(
        session,
        LOGIN,
        PASSWORD,
        token="expired-token",
        on_token_refreshed=refreshed.append,
    )
    fake_tel2.respond(
        "Get-Account-Balance", "error_invalid_token.json", "get_account_balance.json"
    )

    await client._call("account", "Get-Account-Balance")

    assert fake_tel2.call_count("Get-Token") == 1
    assert refreshed == [FIXTURE_TOKEN]


async def test_auth_failure_after_renewal_raises_auth_error(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Account-Balance", "error_invalid_token.json")

    with pytest.raises(Tel2AuthError):
        await client._call("account", "Get-Account-Balance")

    assert fake_tel2.call_count("Get-Token") == 2
    assert fake_tel2.call_count("Get-Account-Balance") == 2


async def test_wrong_password_raises_auth_error_without_retry(
    client: Tel2Client, fake_tel2: FakeTel2Api, refreshed_tokens: list[str]
) -> None:
    fake_tel2.respond("Get-Token", "error_wrong_password.json")

    with pytest.raises(Tel2AuthError):
        await client._call("account", "Get-Account-Balance")
    with pytest.raises(Tel2AuthError):
        await client.async_authenticate()

    assert [request.action for request in fake_tel2.requests] == [
        "Get-Token",
        "Get-Token",
    ]
    assert refreshed_tokens == []


async def test_wrong_password_during_renewal_raises_auth_error(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Token", "get_token.json", "error_wrong_password.json")
    fake_tel2.respond("Get-Account-Balance", "error_invalid_token.json")

    with pytest.raises(Tel2AuthError):
        await client._call("account", "Get-Account-Balance")

    assert fake_tel2.call_count("Get-Token") == 2
    assert fake_tel2.call_count("Get-Account-Balance") == 1


async def test_get_token_api_error_is_not_auth(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Token", "error_generic.json")

    with pytest.raises(Tel2ApiError) as err:
        await client.async_authenticate()

    assert err.value.action == "Get-Token"
    assert err.value.context is None


async def test_authenticate_fetches_and_reports_a_new_token(
    session: aiohttp.ClientSession, fake_tel2: FakeTel2Api
) -> None:
    refreshed: list[str] = []
    client = Tel2Client(
        session, LOGIN, PASSWORD, token="old-token", on_token_refreshed=refreshed.append
    )

    assert await client.async_authenticate() == FIXTURE_TOKEN
    await client._call("account", "Get-Account-Balance")

    assert refreshed == [FIXTURE_TOKEN]
    assert fake_tel2.requests[-1].body["token"] == FIXTURE_TOKEN


async def test_client_works_without_token_callback(
    session: aiohttp.ClientSession, fake_tel2: FakeTel2Api
) -> None:
    client = Tel2Client(session, LOGIN, PASSWORD)

    assert await client.async_authenticate() == FIXTURE_TOKEN


async def test_concurrent_first_calls_fetch_the_token_once(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    await asyncio.gather(
        *(client._call("account", "Get-Account-Balance") for _ in range(5))
    )

    assert fake_tel2.call_count("Get-Token") == 1
    assert fake_tel2.call_count("Get-Account-Balance") == 5


async def test_concurrent_expiries_renew_the_token_once(
    session: aiohttp.ClientSession, fake_tel2: FakeTel2Api
) -> None:
    client = Tel2Client(session, LOGIN, PASSWORD, token="expired-token")
    fake_tel2.respond(
        "Get-Account-Balance",
        "error_invalid_token.json",
        "error_invalid_token.json",
        "get_account_balance.json",
    )

    await asyncio.gather(
        client._call("account", "Get-Account-Balance"),
        client._call("account", "Get-Account-Balance"),
    )

    assert fake_tel2.call_count("Get-Token") == 1
    assert [r.body["token"] for r in fake_tel2.requests_for("Get-Account-Balance")] == [
        "expired-token",
        "expired-token",
        FIXTURE_TOKEN,
        FIXTURE_TOKEN,
    ]


async def test_request_body_shape(client: Tel2Client, fake_tel2: FakeTel2Api) -> None:
    await client._call("voice", "Resync-Line", {"line": "01632960001"})
    await client._call("account", "Get-Account-Balance")

    token_request, resync, balance = fake_tel2.requests
    assert token_request.body == {
        "login": LOGIN,
        "password": PASSWORD,
        "action": "Get-Token",
    }
    assert resync.body == {
        "token": FIXTURE_TOKEN,
        "action": "Resync-Line",
        "context": "voice",
        "params": {"line": "01632960001"},
    }
    assert balance.body["params"] == {}


async def _exercise_every_request_kind(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond(
        "Get-Account-Balance", "error_invalid_token.json", "get_account_balance.json"
    )
    fake_tel2.respond("Get-Webhooks", "error_access_refused_api.json")
    await client._call("account", "Get-Account-Balance")
    await client._call("voice", "Set-Pref-Value", {"line": "01632960001", "value": "1"})
    await client._call_optional("voice", "Get-Subscriber-Status")
    with pytest.raises(Tel2ApiError):
        await client._call("api", "Get-Webhooks")
    await client.async_authenticate()


async def test_no_request_sends_reset_or_credentials_in_the_url(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    await _exercise_every_request_kind(client, fake_tel2)

    assert fake_tel2.call_count("Get-Token") == 3
    for request in fake_tel2.requests:
        assert "reset" not in json.dumps(request.body).lower()
        assert request.url.scheme == "https"
        assert request.url.query_string == ""
        for secret in (LOGIN, PASSWORD, FIXTURE_TOKEN, "token", "login", "password"):
            assert secret not in str(request.url)


async def test_every_request_is_a_json_post_to_the_api_url(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    await _exercise_every_request_kind(client, fake_tel2)

    assert fake_tel2.requests
    for request in fake_tel2.requests:
        assert request.method.upper() == "POST"
        assert request.url == URL(API_URL)
        assert request.headers["Content-Type"] == "application/json"


async def test_api_url_can_be_overridden(aioclient_mock: AiohttpClientMocker) -> None:
    other_url = "https://tel2.example.invalid/api/"
    fake = FakeTel2Api(aioclient_mock, api_url=other_url)
    session = fake.create_session()
    try:
        client = Tel2Client(session, LOGIN, PASSWORD, api_url=other_url)
        await client._call("account", "Get-Account-Balance")
    finally:
        await session.close()

    assert [request.url for request in fake.requests] == [URL(other_url)] * 2


async def test_requests_use_a_30_second_timeout(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker
) -> None:
    timeouts: list[Any] = []

    async def recording_request(method: str, url: str, **kwargs: Any) -> Any:
        timeouts.append(kwargs.get("timeout"))
        return await aioclient_mock.match_request(method, url, **kwargs)

    object.__setattr__(session, "_request", recording_request)
    client = Tel2Client(session, LOGIN, PASSWORD)

    await client._call("account", "Get-Account-Balance")

    assert timeouts == [aiohttp.ClientTimeout(total=30)] * 2


async def test_debug_logs_record_only_context_and_action(
    client: Tel2Client, fake_tel2: FakeTel2Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    param_value = "param-value-for-tests"

    await _exercise_every_request_kind(client, fake_tel2)
    await client._call("voice", "Resync-Line", {"line": param_value})

    tel2_logs = [
        r for r in caplog.records if r.name.startswith("custom_components.tel2")
    ]
    assert tel2_logs
    assert any(
        "account" in r.getMessage() and "Get-Account-Balance" in r.getMessage()
        for r in tel2_logs
    )
    for secret in (
        LOGIN,
        PASSWORD,
        FIXTURE_TOKEN,
        param_value,
        "01632960001",
        "12.34",
        "You do not have access",
    ):
        assert secret not in caplog.text


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"login": ""}, "login"),
        ({"password": ""}, "password"),
        ({"api_url": "http://portal.tel2.co.uk/api/"}, "https"),
    ],
)
async def test_constructor_rejects_unsafe_arguments(
    session: aiohttp.ClientSession, kwargs: dict[str, str], match: str
) -> None:
    arguments: dict[str, Any] = {"login": LOGIN, "password": PASSWORD} | kwargs

    with pytest.raises(ValueError, match=match):
        Tel2Client(session, **arguments)
