"""Tests for the Tel2 binary sensor platform."""

import aiohttp
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.tel2.const import DOMAIN
from custom_components.tel2.runtime import Tel2RuntimeData
from tests.fake_tel2 import FakeTel2Api
from tests.helpers import ACCOUNT_CODE, LINE_1, LINE_2, async_setup_integration

API_CONNECTED = f"{ACCOUNT_CODE}_api_connected"
REGISTERED_1 = f"{ACCOUNT_CODE}_{LINE_1}_registered"
REGISTERED_2 = f"{ACCOUNT_CODE}_{LINE_2}_registered"


@pytest.fixture(autouse=True)
def binary_sensor_platform_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.tel2.PLATFORMS", [Platform.BINARY_SENSOR])


def runtime(entry: MockConfigEntry) -> Tel2RuntimeData:
    data = entry.runtime_data
    assert isinstance(data, Tel2RuntimeData)
    return data


def state_of(hass: HomeAssistant, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        Platform.BINARY_SENSOR, DOMAIN, unique_id
    )
    assert entity_id is not None, unique_id
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


async def test_binary_sensors_snapshot(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    fake_tel2.respond("Get-Subscriber-Status", "get_subscriber_status_registered.json")

    entry = await async_setup_integration(hass, fake_tel2)

    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)
    assert state_of(hass, REGISTERED_1) == STATE_ON
    assert state_of(hass, REGISTERED_2) == STATE_OFF


async def test_registered_all_off_without_registrations(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)

    assert runtime(entry).lines.last_update_success
    assert state_of(hass, REGISTERED_1) == STATE_OFF
    assert state_of(hass, REGISTERED_2) == STATE_OFF
    assert state_of(hass, API_CONNECTED) == STATE_ON


async def test_registered_follows_lines_refresh(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    fake_tel2.respond("Get-Subscriber-Status", "get_subscriber_status_registered.json")

    await runtime(entry).lines.async_refresh()
    await hass.async_block_till_done()

    assert state_of(hass, REGISTERED_1) == STATE_ON
    assert state_of(hass, REGISTERED_2) == STATE_OFF


async def test_connectivity_off_while_account_refresh_fails(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    account = runtime(entry).account
    assert state_of(hass, API_CONNECTED) == STATE_ON

    fake_tel2.respond("Get-Account-Balance", aiohttp.ClientError())
    await account.async_refresh()
    await hass.async_block_till_done()

    assert not account.last_update_success
    assert state_of(hass, API_CONNECTED) == STATE_OFF

    fake_tel2.respond("Get-Account-Balance", "get_account_balance.json")
    await account.async_refresh()
    await hass.async_block_till_done()

    assert state_of(hass, API_CONNECTED) == STATE_ON


async def test_connectivity_off_while_only_lines_refresh_fails(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    data = runtime(entry)

    fake_tel2.respond("Get-Subscriber-Status", aiohttp.ClientError())
    await data.lines.async_refresh()
    await hass.async_block_till_done()

    assert data.account.last_update_success
    assert not data.lines.last_update_success
    assert state_of(hass, API_CONNECTED) == STATE_OFF

    fake_tel2.respond("Get-Subscriber-Status", "get_subscriber_status.json")
    await data.lines.async_refresh()
    await hass.async_block_till_done()

    assert state_of(hass, API_CONNECTED) == STATE_ON


async def test_connectivity_stays_available_while_coordinators_fail(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    data = runtime(entry)
    fake_tel2.respond("Get-Account-Balance", aiohttp.ClientError())
    fake_tel2.respond("Get-Subscriber-Status", aiohttp.ClientError())

    await data.account.async_refresh()
    await data.lines.async_refresh()
    await hass.async_block_till_done()

    assert state_of(hass, API_CONNECTED) == STATE_OFF
    assert state_of(hass, REGISTERED_1) == STATE_UNAVAILABLE
