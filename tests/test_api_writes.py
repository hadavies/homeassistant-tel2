"""Tests for the Tel2 client write operations."""

from collections.abc import AsyncGenerator, Awaitable, Callable
import logging
from typing import Any, NamedTuple

import aiohttp
import pytest

from custom_components.tel2.api import Tel2ApiError, Tel2AuthError, Tel2Client
from custom_components.tel2.const import SECRET_PREFS
from custom_components.tel2.models import Tel2Number, WebhookSpec
from tests.fake_tel2 import NO_DATA, FakeTel2Api
from tests.fixture_loader import load_fixture

LOGIN = "portal-login-for-tests"
PASSWORD = "portal-password-for-tests"
TEL2_ID = "01632960001"
NUMBER = Tel2Number(
    canonical="441632960001", tel2_id=TEL2_ID, name=None, extension=None
)
WEBHOOK_URL = "https://hooks.example.invalid/api/webhook/webhook-id-for-tests"
SECRET_TOKEN = "abc123DEF456secrettokenfortests"
SPEC = WebhookSpec(
    name="homeassistant-tel2-entry",
    url=WEBHOOK_URL,
    secret_header="X-Tel2-Signature",
    secret_token=SECRET_TOKEN,
)
WEBHOOK_STATES = (
    "inboundringing",
    "inboundanswered",
    "inboundended",
    "inboundmissed",
    "outboundringing",
    "outboundanswered",
    "outboundended",
    "outboundunattended",
)

type Write = Callable[[Tel2Client], Awaitable[None]]


class WriteCase(NamedTuple):
    """One write call and the request it must send."""

    write: Write
    action: str
    context: str
    params: dict[str, Any]


WRITES = {
    "set_pref": WriteCase(
        lambda c: c.async_set_pref(NUMBER, "do_not_disturb_status", "1"),
        "Set-Pref-Value",
        "voice",
        {"line": TEL2_ID, "pref": "do_not_disturb_status", "value": "1"},
    ),
    "enable_recording": WriteCase(
        lambda c: c.async_set_recording(NUMBER, True),
        "Enable-Recording",
        "voice",
        {"number": TEL2_ID},
    ),
    "disable_recording": WriteCase(
        lambda c: c.async_set_recording(NUMBER, False),
        "Disable-Recording",
        "voice",
        {"number": TEL2_ID},
    ),
    "resync_line": WriteCase(
        lambda c: c.async_resync_line(NUMBER),
        "Resync-Line",
        "voice",
        {"line": TEL2_ID},
    ),
    "reboot_phone": WriteCase(
        lambda c: c.async_reboot_phone(NUMBER),
        "Reboot-Phone",
        "voice",
        {"line": TEL2_ID},
    ),
    "delete_webhook": WriteCase(
        lambda c: c.async_delete_webhook("42"),
        "Delete-Webhook",
        "api",
        {"id": "42"},
    ),
}
each_write = pytest.mark.parametrize("case", WRITES.values(), ids=WRITES.keys())


def expected_webhook_params(**extra: str) -> dict[str, str]:
    return {
        "name": SPEC.name,
        "url": WEBHOOK_URL,
        "secrettoken": SECRET_TOKEN,
        "secretheader": "X-Tel2-Signature",
        **dict.fromkeys(WEBHOOK_STATES, "1"),
        "e164format": "1",
        "airecording": "0",
        **extra,
    }


@pytest.fixture
async def session(fake_tel2: FakeTel2Api) -> AsyncGenerator[aiohttp.ClientSession]:
    session = fake_tel2.create_session()
    yield session
    await session.close()


@pytest.fixture
def client(session: aiohttp.ClientSession) -> Tel2Client:
    return Tel2Client(session, LOGIN, PASSWORD, token="stored-token-for-tests")


@each_write
async def test_write_sends_action_context_and_params(
    client: Tel2Client, fake_tel2: FakeTel2Api, case: WriteCase
) -> None:
    assert await case.write(client) is None

    [request] = fake_tel2.requests_for(case.action)
    assert request.context == case.context
    assert request.params == case.params


@each_write
async def test_write_rejection_raises_api_error_with_tel2_message(
    client: Tel2Client, fake_tel2: FakeTel2Api, case: WriteCase
) -> None:
    fixture = case.action.lower().replace("-", "_") + "_rejected.json"
    fake_tel2.respond(case.action, fixture)

    with pytest.raises(Tel2ApiError) as err:
        await case.write(client)

    assert err.value.message == load_fixture(fixture)["Message"]
    assert err.value.context == case.context
    assert err.value.action == case.action
    assert fake_tel2.call_count(case.action) == 1


@each_write
async def test_write_with_no_data_is_not_applied(
    client: Tel2Client, fake_tel2: FakeTel2Api, case: WriteCase
) -> None:
    fake_tel2.respond(case.action, NO_DATA)

    with pytest.raises(Tel2ApiError, match="not applied") as err:
        await case.write(client)

    assert err.value.context == case.context
    assert err.value.action == case.action


@each_write
async def test_write_failure_raises_api_error_with_tel2_message(
    client: Tel2Client, fake_tel2: FakeTel2Api, case: WriteCase
) -> None:
    fake_tel2.respond(
        case.action, {**NO_DATA, "Status": "Failure", "Message": "Failed."}
    )

    with pytest.raises(Tel2ApiError) as err:
        await case.write(client)

    assert err.value.message == "Failed."
    assert not isinstance(err.value, Tel2AuthError)
    assert (err.value.context, err.value.action) == (case.context, case.action)


@each_write
async def test_write_with_invalid_token_renews_once_and_retries(
    client: Tel2Client, fake_tel2: FakeTel2Api, case: WriteCase
) -> None:
    success = case.action.lower().replace("-", "_") + ".json"
    fake_tel2.respond(case.action, "error_invalid_token.json", success)

    await case.write(client)

    assert fake_tel2.call_count("Get-Token") == 1
    assert [r.body["token"] for r in fake_tel2.requests_for(case.action)] == [
        "stored-token-for-tests",
        load_fixture("get_token.json")["Data"]["token"],
    ]


async def test_unauthorised_destination_is_a_rejection_not_a_token_retry(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond(
        "Set-Pref-Value",
        {
            "Result": "-1",
            "Status": "Error",
            "Message": "Unauthorised destination number.",
            "Data": [],
        },
    )

    with pytest.raises(Tel2ApiError) as err:
        await WRITES["set_pref"].write(client)

    assert not isinstance(err.value, Tel2AuthError)
    assert err.value.message == "Unauthorised destination number."
    assert fake_tel2.call_count("Set-Pref-Value") == 1
    assert fake_tel2.call_count("Get-Token") == 0


async def test_auth_wording_on_a_success_status_is_not_a_token_retry(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond(
        "Set-Pref-Value",
        {"Result": "-1", "Status": "Success", "Message": "Invalid Token Specified"},
    )

    with pytest.raises(Tel2ApiError) as err:
        await WRITES["set_pref"].write(client)

    assert not isinstance(err.value, Tel2AuthError)
    assert fake_tel2.call_count("Set-Pref-Value") == 1
    assert fake_tel2.call_count("Get-Token") == 0


@pytest.mark.parametrize(
    "pref",
    [
        *sorted(SECRET_PREFS),
        "VOICEMAIL_PIN",
        "new_feature_pin",
        "profile_secret",
        "remote_token",
        "sip_passwd",
    ],
)
async def test_secret_pref_is_refused_without_a_request(
    client: Tel2Client, fake_tel2: FakeTel2Api, pref: str
) -> None:
    with pytest.raises(Tel2ApiError) as err:
        await client.async_set_pref(NUMBER, pref, "1234")

    assert err.value.action == "Set-Pref-Value"
    assert "1234" not in str(err.value)
    assert fake_tel2.requests == []


async def test_add_webhook_sends_every_state_and_omits_phonenumber(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    assert await client.async_add_webhook(SPEC) is None

    [request] = fake_tel2.requests_for("Add-Webhook")
    assert request.context == "api"
    assert request.params == expected_webhook_params()
    assert "editid" not in request.params
    assert "phonenumber" not in request.params


async def test_add_webhook_with_edit_id_updates_the_registration(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    await client.async_add_webhook(SPEC, edit_id="17")

    [request] = fake_tel2.requests_for("Add-Webhook")
    assert request.params == expected_webhook_params(editid="17")


async def test_add_webhook_rejection_raises_api_error(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Add-Webhook", "add_webhook_rejected.json")

    with pytest.raises(Tel2ApiError) as err:
        await client.async_add_webhook(SPEC)

    assert err.value.message == "Invalid webhook URL."
    assert err.value.context == "api"


async def test_add_webhook_without_result_1_is_not_applied(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Add-Webhook", NO_DATA)

    with pytest.raises(Tel2ApiError, match="not applied"):
        await client.async_add_webhook(SPEC)


@pytest.mark.parametrize(
    "token", ["", "abc-123", "abc_123", "abc123==", "abc 123", "abc/123", "abcé123"]
)
async def test_add_webhook_rejects_non_alphanumeric_token_without_a_request(
    client: Tel2Client, fake_tel2: FakeTel2Api, token: str
) -> None:
    spec = WebhookSpec(
        name=SPEC.name,
        url=WEBHOOK_URL,
        secret_header="X-Tel2-Signature",
        secret_token=token,
    )

    with pytest.raises(ValueError, match="alphanumeric"):
        await client.async_add_webhook(spec)

    assert fake_tel2.requests == []


async def test_write_debug_logs_omit_url_secret_and_value(
    client: Tel2Client, fake_tel2: FakeTel2Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    pref_value = "pref-value-for-tests"

    await client.async_add_webhook(SPEC, edit_id="17")
    await client.async_set_pref(NUMBER, "callforward_always_number", pref_value)
    fake_tel2.respond("Add-Webhook", "add_webhook_rejected.json")
    with pytest.raises(Tel2ApiError):
        await client.async_add_webhook(SPEC)
    fake_tel2.respond("Set-Pref-Value", NO_DATA)
    with pytest.raises(Tel2ApiError):
        await client.async_set_pref(NUMBER, "callforward_always_number", pref_value)

    assert any("Add-Webhook" in r.getMessage() for r in caplog.records)
    for secret in (WEBHOOK_URL, "hooks.example.invalid", SECRET_TOKEN, pref_value):
        assert secret not in caplog.text
