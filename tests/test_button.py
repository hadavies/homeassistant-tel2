"""Tests for the Tel2 button platform."""

from dataclasses import FrozenInstanceError
from typing import NamedTuple

import aiohttp
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import snapshot_platform
from syrupy.assertion import SnapshotAssertion

from custom_components.tel2.button import BUTTONS as BUTTON_DESCRIPTIONS
from custom_components.tel2.const import DOMAIN
from tests.fake_tel2 import FakeTel2Api
from tests.helpers import ACCOUNT_CODE, LINE_1, LINE_2, async_setup_integration


class ButtonCase(NamedTuple):
    """A button, the Tel2 action it sends and how a rejection is reported."""

    key: str
    action: str
    rejected: str
    translation_key: str


BUTTONS = (
    ButtonCase(
        "resync_line", "Resync-Line", "resync_line_rejected.json", "resync_failed"
    ),
    ButtonCase(
        "reboot_phone", "Reboot-Phone", "reboot_phone_rejected.json", "reboot_failed"
    ),
)
each_button = pytest.mark.parametrize("case", BUTTONS, ids=lambda case: case.key)


@pytest.fixture(autouse=True)
def button_platform_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.tel2.PLATFORMS", [Platform.BUTTON])


def button_id(hass: HomeAssistant, canonical: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        Platform.BUTTON, DOMAIN, f"{ACCOUNT_CODE}_{canonical}_{key}"
    )
    assert entity_id is not None, key
    return entity_id


async def press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def test_buttons_snapshot(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)

    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)


@each_button
async def test_press_sends_the_command_for_that_number(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    case: ButtonCase,
) -> None:
    await async_setup_integration(hass, fake_tel2)

    await press(hass, button_id(hass, LINE_2, case.key))

    requests = fake_tel2.requests_for(case.action)
    assert len(requests) == 1
    assert requests[0].context == "voice"
    assert requests[0].params == {"line": "01632960002"}
    for other in BUTTONS:
        if other != case:
            assert fake_tel2.call_count(other.action) == 0


@each_button
async def test_tel2_rejection_raises_a_translated_error(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    case: ButtonCase,
) -> None:
    await async_setup_integration(hass, fake_tel2)
    fake_tel2.respond(case.action, case.rejected)

    with pytest.raises(HomeAssistantError) as err:
        await press(hass, button_id(hass, LINE_1, case.key))

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == case.translation_key
    assert fake_tel2.call_count(case.action) == 1


@each_button
async def test_connection_error_raises_a_translated_error(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    case: ButtonCase,
) -> None:
    await async_setup_integration(hass, fake_tel2)
    fake_tel2.respond(case.action, aiohttp.ClientConnectionError("network down"))

    with pytest.raises(HomeAssistantError) as err:
        await press(hass, button_id(hass, LINE_1, case.key))

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == case.translation_key


@pytest.mark.parametrize(
    "description", BUTTON_DESCRIPTIONS, ids=lambda description: description.key
)
def test_button_descriptions_are_immutable(description: object) -> None:
    with pytest.raises(FrozenInstanceError):
        description.key = "changed"  # type: ignore[attr-defined]
