"""Tests for the Tel2 config flow."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import TextSelector, TextSelectorType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tel2.config_flow import InsufficientAccess
from custom_components.tel2.const import CONF_LOGIN, CONF_PASSWORD, CONF_TOKEN, DOMAIN
from tests.fake_tel2 import FakeTel2Api
from tests.fixture_loader import load_fixture

pytestmark = pytest.mark.usefixtures("mock_setup_entry")

USER_INPUT = {CONF_LOGIN: "ha-login", CONF_PASSWORD: "hunter2"}
ACCOUNT_CODE = "00000000"


async def _start(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}
    return dict(result)


async def _submit(
    hass: HomeAssistant, flow_id: str, user_input: dict[str, str] = USER_INPUT
) -> dict[str, Any]:
    return dict(await hass.config_entries.flow.async_configure(flow_id, user_input))


async def test_user_step_shows_login_and_password(hass: HomeAssistant) -> None:
    result = await _start(hass)

    schema = {str(key): value for key, value in result["data_schema"].schema.items()}
    assert set(schema) == {CONF_LOGIN, CONF_PASSWORD}
    password = schema[CONF_PASSWORD]
    assert isinstance(password, TextSelector)
    assert password.config["type"] == TextSelectorType.PASSWORD
    assert password.config["autocomplete"] == "current-password"
    assert schema[CONF_LOGIN].config["autocomplete"] == "username"


async def test_user_creates_entry(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, mock_setup_entry: AsyncMock
) -> None:
    result = await _start(hass)

    result = await _submit(hass, result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Example Customer ({ACCOUNT_CODE})"
    assert result["data"] == {
        CONF_LOGIN: "ha-login",
        CONF_PASSWORD: "hunter2",
        CONF_TOKEN: "fake-token-for-tests",
    }
    assert result["result"].unique_id == ACCOUNT_CODE
    assert len(mock_setup_entry.mock_calls) == 1
    get_token = fake_tel2.requests_for("Get-Token")[0]
    assert get_token.body["login"] == "ha-login"
    assert get_token.body["password"] == "hunter2"
    for action in ("Get-Subscriber-Status", "Get-Webhooks"):
        assert fake_tel2.call_count(action) == 1
    assert fake_tel2.call_count("Get-Customer-Details") >= 1
    assert fake_tel2.call_count("Get-Account-Balance") == 0


@pytest.mark.parametrize(
    ("action", "response", "error"),
    [
        ("Get-Token", "error_wrong_password.json", "invalid_auth"),
        ("Get-Token", aiohttp.ClientError(), "cannot_connect"),
        ("Get-Customer-Details", TimeoutError(), "cannot_connect"),
        ("Get-Token", "error_generic.json", "unknown"),
        ("Get-Customer-Details", "error_generic.json", "insufficient_access"),
    ],
)
async def test_user_errors_then_recovers(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    action: str,
    response: Any,
    error: str,
) -> None:
    fake_tel2.respond(action, response, default_response(action))
    result = await _start(hass)

    result = await _submit(hass, result["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert not hass.config_entries.async_entries(DOMAIN)

    result = await _submit(hass, result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY


def default_response(action: str) -> str:
    return action.lower().replace("-", "_") + ".json"


@pytest.mark.parametrize(
    ("refused", "contexts"),
    [
        ({"voice": "Get-Subscriber-Status"}, "voice"),
        (
            {
                "voice": "Get-Subscriber-Status",
                "api": "Get-Webhooks",
                "account": "Get-Customer-Details",
            },
            "account, API, voice",
        ),
    ],
)
async def test_user_insufficient_access(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    refused: dict[str, str],
    contexts: str,
) -> None:
    for context, action in refused.items():
        fake_tel2.respond(
            action, f"error_access_refused_{context}.json", default_response(action)
        )
    result = await _start(hass)

    result = await _submit(hass, result["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "insufficient_access"}
    assert result["description_placeholders"] == {
        "contexts": contexts,
        "level": "Full",
    }
    assert not hass.config_entries.async_entries(DOMAIN)

    result = await _submit(hass, result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_unauthorised_probe_reports_access(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond(
        "Get-Webhooks",
        {
            "Result": "-1",
            "Status": "Error",
            "Message": "Unauthorised: function disabled for this account.",
            "Data": [],
        },
    )
    result = await _start(hass)

    result = await _submit(hass, result["flow_id"])

    assert result["errors"] == {"base": "insufficient_access"}
    assert result["description_placeholders"]["contexts"] == "API"


async def test_user_customer_details_refused_reports_access(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    refused = "error_access_refused_account.json"
    fake_tel2.respond("Get-Customer-Details", refused)
    fake_tel2.respond("Get-Account-Balance", refused)
    result = await _start(hass)

    result = await _submit(hass, result["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "insufficient_access"}
    assert result["description_placeholders"] == {
        "contexts": "account",
        "level": "Full",
    }

    fake_tel2.respond("Get-Customer-Details", "get_customer_details.json")
    fake_tel2.respond("Get-Account-Balance", "get_account_balance.json")
    result = await _submit(hass, result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    "user_input",
    [
        {CONF_LOGIN: "   ", CONF_PASSWORD: "hunter2"},
        {CONF_LOGIN: "ha-login", CONF_PASSWORD: " \t"},
    ],
)
async def test_user_blank_credentials(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    caplog: pytest.LogCaptureFixture,
    user_input: dict[str, str],
) -> None:
    result = await _start(hass)

    result = await _submit(hass, result["flow_id"], user_input)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert not fake_tel2.requests
    assert "Traceback" not in caplog.text

    result = await _submit(hass, result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_strips_login_keeps_password(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    result = await _start(hass)

    result = await _submit(
        hass,
        result["flow_id"],
        {CONF_LOGIN: "  ha-login ", CONF_PASSWORD: " hunter2 "},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LOGIN] == "ha-login"
    assert result["data"][CONF_PASSWORD] == " hunter2 "
    get_token = fake_tel2.requests_for("Get-Token")[0]
    assert get_token.body["login"] == "ha-login"
    assert get_token.body["password"] == " hunter2 "


async def test_user_unexpected_exception(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, caplog: pytest.LogCaptureFixture
) -> None:
    result = await _start(hass)

    with patch(
        "custom_components.tel2.config_flow.Tel2Client.async_probe_access",
        side_effect=RuntimeError("boom"),
    ):
        result = await _submit(hass, result["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
    assert "Unexpected error validating the Tel2 login" in caplog.text
    assert "hunter2" not in caplog.text
    assert "fake-token-for-tests" not in caplog.text

    result = await _submit(hass, result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_already_configured(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    mock_config_entry_factory(unique_id=ACCOUNT_CODE).add_to_hass(hass)
    result = await _start(hass)

    result = await _submit(hass, result["flow_id"])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_user_never_logs_credentials(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG", logger="custom_components.tel2")
    fake_tel2.respond("Get-Webhooks", "error_access_refused_api.json")
    result = await _start(hass)

    await _submit(hass, result["flow_id"])

    assert "hunter2" not in caplog.text
    assert "fake-token-for-tests" not in caplog.text


OLD_DATA = {CONF_LOGIN: "old-login", CONF_PASSWORD: "old-pass", CONF_TOKEN: "old-token"}
REAUTH_INPUT = {CONF_LOGIN: "  ha-login ", CONF_PASSWORD: " hunter2 "}


@pytest.fixture
def reauth_entry(
    hass: HomeAssistant, mock_config_entry_factory: Callable[..., MockConfigEntry]
) -> MockConfigEntry:
    entry = mock_config_entry_factory(unique_id=ACCOUNT_CODE, data=dict(OLD_DATA))
    entry.add_to_hass(hass)
    return entry


async def _start_reauth(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    result = dict(await entry.start_reauth_flow(hass))
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {}
    return result


async def test_reauth_shows_form_with_login_prefilled(
    hass: HomeAssistant, reauth_entry: MockConfigEntry
) -> None:
    result = await _start_reauth(hass, reauth_entry)

    schema = {str(key): key for key in result["data_schema"].schema}
    assert set(schema) == {CONF_LOGIN, CONF_PASSWORD}
    assert schema[CONF_LOGIN].description == {"suggested_value": "old-login"}
    assert not (schema[CONF_PASSWORD].description or {}).get("suggested_value")
    password = result["data_schema"].schema[schema[CONF_PASSWORD]]
    assert isinstance(password, TextSelector)
    assert password.config["type"] == TextSelectorType.PASSWORD


async def test_reauth_updates_credentials_and_reloads(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_setup_entry: AsyncMock,
    reauth_entry: MockConfigEntry,
) -> None:
    result = await _start_reauth(hass, reauth_entry)
    assert len(mock_setup_entry.mock_calls) == 1

    result = await _submit(hass, result["flow_id"], REAUTH_INPUT)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert reauth_entry.data == {
        CONF_LOGIN: "ha-login",
        CONF_PASSWORD: " hunter2 ",
        CONF_TOKEN: "fake-token-for-tests",
    }
    assert reauth_entry.unique_id == ACCOUNT_CODE
    assert len(mock_setup_entry.mock_calls) == 2
    assert reauth_entry.state is ConfigEntryState.LOADED
    get_token = fake_tel2.requests_for("Get-Token")[0]
    assert get_token.body["login"] == "ha-login"
    assert get_token.body["password"] == " hunter2 "


async def test_reauth_wrong_account(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_setup_entry: AsyncMock,
    reauth_entry: MockConfigEntry,
) -> None:
    other = load_fixture("get_customer_details.json")
    other["Data"]["AccountCode"] = "11111111"
    fake_tel2.respond("Get-Customer-Details", other)
    result = await _start_reauth(hass, reauth_entry)

    result = await _submit(hass, result["flow_id"])
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert reauth_entry.data == OLD_DATA
    assert reauth_entry.unique_id == ACCOUNT_CODE
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.parametrize(
    ("action", "response", "error"),
    [
        ("Get-Token", "error_wrong_password.json", "invalid_auth"),
        ("Get-Token", aiohttp.ClientError(), "cannot_connect"),
        ("Get-Webhooks", "error_access_refused_api.json", "insufficient_access"),
        ("Get-Customer-Details", "error_generic.json", "insufficient_access"),
    ],
)
async def test_reauth_errors_then_recovers(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    reauth_entry: MockConfigEntry,
    *,
    action: str,
    response: Any,
    error: str,
) -> None:
    fake_tel2.respond(action, response, default_response(action))
    result = await _start_reauth(hass, reauth_entry)

    result = await _submit(hass, result["flow_id"])

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": error}
    assert reauth_entry.data == OLD_DATA
    login_key = next(
        key for key in result["data_schema"].schema if str(key) == CONF_LOGIN
    )
    assert login_key.description == {"suggested_value": "ha-login"}

    result = await _submit(hass, result["flow_id"])
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert reauth_entry.data[CONF_PASSWORD] == "hunter2"


async def test_reauth_insufficient_access_placeholders(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    reauth_entry: MockConfigEntry,
) -> None:
    fake_tel2.respond("Get-Subscriber-Status", "error_access_refused_voice.json")
    result = await _start_reauth(hass, reauth_entry)

    result = await _submit(hass, result["flow_id"])

    assert result["errors"] == {"base": "insufficient_access"}
    placeholders = result["description_placeholders"]
    assert placeholders["contexts"] == "voice"
    assert placeholders["level"] == "Full"


async def test_reauth_never_logs_credentials(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    caplog: pytest.LogCaptureFixture,
    reauth_entry: MockConfigEntry,
) -> None:
    caplog.set_level("DEBUG")
    fake_tel2.respond("Get-Token", "error_generic.json", "get_token.json")
    result = await _start_reauth(hass, reauth_entry)

    result = await _submit(hass, result["flow_id"])
    result = await _submit(hass, result["flow_id"])
    await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    for secret in ("hunter2", "fake-token-for-tests", "old-pass", "old-token"):
        assert secret not in caplog.text


def test_insufficient_access_names_the_refused_contexts() -> None:
    assert str(InsufficientAccess({"voice", "account"})) == "account, voice"
