"""Tests for the tel2.set_line_setting action."""

from typing import Any
from unittest.mock import patch

import aiohttp
from homeassistant.auth.models import User
from homeassistant.const import Platform
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)
from homeassistant.helpers import device_registry as dr
import probatio
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import tel2
from custom_components.tel2.const import DOMAIN
from custom_components.tel2.services import (
    SET_LINE_SETTING_SCHEMA,
    async_setup_services,
)
from tests.fake_tel2 import FakeTel2Api
from tests.fixture_loader import TEST_ENTRY_ID
from tests.helpers import (
    ACCOUNT_CODE,
    DEFAULT_ENTRY_DATA,
    LINE_1,
    async_setup_integration,
)

SERVICE = "set_line_setting"
SET_PREF = "Set-Pref-Value"
PREF_SUMMARY = "Get-All-Pref-Summary"


@pytest.fixture(autouse=True)
def button_platform_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.tel2.PLATFORMS", [Platform.BUTTON])


def device_id_for(hass: HomeAssistant, identifier: str) -> str:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, identifier), TEST_ENTRY_ID
    )
    assert device is not None
    return device.id


async def set_line_setting(
    hass: HomeAssistant, *, context: Context | None = None, **data: Any
) -> None:
    await hass.services.async_call(
        DOMAIN, SERVICE, data, blocking=True, context=context
    )


async def test_sets_pref_and_refreshes_lines(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    tel2_id = entry.runtime_data.account.data.numbers[LINE_1].tel2_id
    refreshes = fake_tel2.call_count(PREF_SUMMARY)

    await set_line_setting(
        hass,
        device_id=device_id_for(hass, LINE_1),
        pref="do_not_disturb_status",
        value=1,
    )
    await hass.async_block_till_done()

    [request] = fake_tel2.requests_for(SET_PREF)
    assert request.context == "voice"
    assert request.params == {
        "line": tel2_id,
        "pref": "do_not_disturb_status",
        "value": "1",
    }
    assert fake_tel2.call_count(PREF_SUMMARY) > refreshes


async def test_non_admin_is_refused_unsent(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, hass_read_only_user: User
) -> None:
    await async_setup_integration(hass, fake_tel2)

    with pytest.raises(Unauthorized):
        await set_line_setting(
            hass,
            context=Context(user_id=hass_read_only_user.id),
            device_id=device_id_for(hass, LINE_1),
            pref="do_not_disturb_status",
            value="1",
        )

    assert fake_tel2.call_count(SET_PREF) == 0


async def test_admin_can_set_pref(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, hass_admin_user: User
) -> None:
    await async_setup_integration(hass, fake_tel2)

    await set_line_setting(
        hass,
        context=Context(user_id=hass_admin_user.id),
        device_id=device_id_for(hass, LINE_1),
        pref="do_not_disturb_status",
        value="1",
    )

    assert fake_tel2.call_count(SET_PREF) == 1


@pytest.mark.parametrize("pref", ["voicemail_pin", "remote_access_pin", "Password"])
async def test_secret_pref_is_refused_unsent(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, pref: str
) -> None:
    await async_setup_integration(hass, fake_tel2)

    with pytest.raises(ServiceValidationError) as err:
        await set_line_setting(
            hass, device_id=device_id_for(hass, LINE_1), pref=pref, value="1234"
        )

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "secret_pref"
    assert fake_tel2.call_count(SET_PREF) == 0


async def test_tel2_rejection_carries_its_message(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    await async_setup_integration(hass, fake_tel2)
    fake_tel2.respond(SET_PREF, "set_pref_value_rejected.json")
    refreshes = fake_tel2.call_count(PREF_SUMMARY)

    with pytest.raises(HomeAssistantError) as err:
        await set_line_setting(
            hass, device_id=device_id_for(hass, LINE_1), pref="bogus", value="x"
        )

    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "tel2_rejected"
    assert err.value.translation_placeholders == {
        "message": "Invalid preference value."
    }
    assert fake_tel2.call_count(PREF_SUMMARY) == refreshes


async def test_connection_failure_is_set_failed(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    await async_setup_integration(hass, fake_tel2)
    fake_tel2.respond(SET_PREF, aiohttp.ClientConnectionError())
    refreshes = fake_tel2.call_count(PREF_SUMMARY)

    with pytest.raises(HomeAssistantError) as err:
        await set_line_setting(
            hass,
            device_id=device_id_for(hass, LINE_1),
            pref="anoncall_reject",
            value="1",
        )

    assert err.value.translation_key == "set_failed"
    assert fake_tel2.call_count(PREF_SUMMARY) == refreshes


async def test_non_tel2_device_is_refused(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    await async_setup_integration(hass, fake_tel2)
    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("other", LINE_1)}
    )

    with pytest.raises(ServiceValidationError) as err:
        await set_line_setting(
            hass, device_id=device.id, pref="do_not_disturb_status", value="1"
        )

    assert err.value.translation_key == "not_a_line"
    assert fake_tel2.call_count(SET_PREF) == 0


async def test_account_device_is_refused(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    await async_setup_integration(hass, fake_tel2)

    with pytest.raises(ServiceValidationError) as err:
        await set_line_setting(
            hass,
            device_id=device_id_for(hass, ACCOUNT_CODE),
            pref="do_not_disturb_status",
            value="1",
        )

    assert err.value.translation_key == "not_a_line"
    assert fake_tel2.call_count(SET_PREF) == 0


async def test_unknown_device_is_refused(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    await async_setup_integration(hass, fake_tel2)

    with pytest.raises(ServiceValidationError) as err:
        await set_line_setting(
            hass, device_id="not-a-device", pref="do_not_disturb_status", value="1"
        )

    assert err.value.translation_key == "not_a_line"
    assert fake_tel2.call_count(SET_PREF) == 0


async def test_number_that_left_the_account_is_refused(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "441632960999")}
    )

    with pytest.raises(ServiceValidationError) as err:
        await set_line_setting(
            hass, device_id=device.id, pref="do_not_disturb_status", value="1"
        )

    assert err.value.translation_key == "not_a_line"
    assert fake_tel2.call_count(SET_PREF) == 0


async def test_unloaded_entry_is_refused(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    device_id = device_id_for(hass, LINE_1)
    await hass.config_entries.async_unload(entry.entry_id)

    with pytest.raises(ServiceValidationError) as err:
        await set_line_setting(
            hass, device_id=device_id, pref="do_not_disturb_status", value="1"
        )

    assert err.value.translation_key == "service_config_entry_not_loaded"
    assert fake_tel2.call_count(SET_PREF) == 0


@pytest.mark.parametrize("pref", ["", "   "])
async def test_blank_pref_fails_validation(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, pref: str
) -> None:
    await async_setup_integration(hass, fake_tel2)

    with pytest.raises(probatio.Invalid):
        await set_line_setting(
            hass, device_id=device_id_for(hass, LINE_1), pref=pref, value="1"
        )

    assert fake_tel2.call_count(SET_PREF) == 0


async def test_action_is_registered_once_for_two_entries(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    with patch.object(
        tel2, "async_setup_services", wraps=async_setup_services
    ) as setup_services:
        first = await async_setup_integration(hass, fake_tel2)
        second = MockConfigEntry(
            domain=DOMAIN,
            unique_id="99999999",
            data=dict(DEFAULT_ENTRY_DATA),
        )
        second.add_to_hass(hass)
        await hass.config_entries.async_setup(second.entry_id)
        await hass.async_block_till_done()

    assert setup_services.call_count == 1
    assert hass.services.has_service(DOMAIN, SERVICE)

    await hass.config_entries.async_unload(first.entry_id)
    assert hass.services.has_service(DOMAIN, SERVICE)


def test_single_character_pref_is_valid_and_stripped() -> None:
    data = SET_LINE_SETTING_SCHEMA({"device_id": "abc", "pref": " x ", "value": "1"})

    assert data["pref"] == "x"
