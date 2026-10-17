"""Tests for the Tel2 config flow."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import TextSelector, TextSelectorType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tel2.const import CONF_LOGIN, CONF_PASSWORD, CONF_TOKEN, DOMAIN
from tests.fake_tel2 import FakeTel2Api

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
    for action in ("Get-Account-Balance", "Get-Subscriber-Status", "Get-Webhooks"):
        assert fake_tel2.call_count(action) == 1


@pytest.mark.parametrize(
    ("action", "response", "error"),
    [
        ("Get-Token", "error_wrong_password.json", "invalid_auth"),
        ("Get-Token", aiohttp.ClientError(), "cannot_connect"),
        ("Get-Customer-Details", TimeoutError(), "cannot_connect"),
        ("Get-Token", "error_generic.json", "unknown"),
        ("Get-Customer-Details", "error_generic.json", "unknown"),
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
                "account": "Get-Account-Balance",
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
