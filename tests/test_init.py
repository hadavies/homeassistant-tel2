"""Tests for Tel2 integration setup, unload and device management."""

from datetime import timedelta
import importlib

import aiohttp
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import async_get_platforms
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.tel2 import PLATFORMS, async_remove_config_entry_device
from custom_components.tel2.const import (
    CONF_CALL_EVENTS,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from custom_components.tel2.runtime import Tel2RuntimeData
from custom_components.tel2.webhook import Tel2CallEventManager
from tests.fake_tel2 import FakeTel2Api
from tests.helpers import (
    ACCOUNT_CODE,
    DEFAULT_ENTRY_DATA,
    FIXTURE_TOKEN,
    LINE_1,
    LINE_2,
    STORED_TOKEN,
    async_setup_integration,
)

REMOVED_NUMBER = "441632960003"


def runtime(entry: MockConfigEntry) -> Tel2RuntimeData:
    data = entry.runtime_data
    assert isinstance(data, Tel2RuntimeData)
    return data


def add_device(
    hass: HomeAssistant, entry: MockConfigEntry, identifier: str
) -> dr.DeviceEntry:
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, identifier)}
    )


async def test_integration_loads(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert DOMAIN in hass.config.components


async def test_setup_loads_both_coordinators(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)

    assert entry.state is ConfigEntryState.LOADED
    data = runtime(entry)
    assert set(data.account.data.numbers) == {LINE_1, LINE_2}
    assert set(data.lines.data.lines) == {LINE_1, LINE_2}
    assert isinstance(data.call_events, Tel2CallEventManager)
    assert data.account.client is data.client
    assert data.lines.client is data.client
    assert data.lines.account is data.account
    account_device = dr.async_get(hass).async_get(data.account_device_id)
    assert account_device is not None
    assert account_device.identifiers == {(DOMAIN, ACCOUNT_CODE)}
    assert not entry.update_listeners


async def test_setup_uses_the_stored_token(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)

    assert entry.state is ConfigEntryState.LOADED
    assert fake_tel2.call_count("Get-Token") == 0
    assert {request.body["token"] for request in fake_tel2.requests} == {STORED_TOKEN}


async def test_account_refreshes_before_lines(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    await async_setup_integration(hass, fake_tel2)

    actions = [request.action for request in fake_tel2.requests]
    last_account = max(i for i, a in enumerate(actions) if a == "Get-Numbers")
    first_lines = min(i for i, a in enumerate(actions) if a == "Get-All-Pref-Summary")
    assert last_account < first_lines


async def test_all_platforms_load(hass: HomeAssistant, fake_tel2: FakeTel2Api) -> None:
    entry = await async_setup_integration(hass, fake_tel2)

    assert entry.state is ConfigEntryState.LOADED
    assert {platform.domain for platform in async_get_platforms(hass, DOMAIN)} == {
        str(platform) for platform in PLATFORMS
    }
    assert len(PLATFORMS) == 6


@pytest.mark.parametrize(
    ("module", "parallel_updates"),
    [
        ("sensor", 0),
        ("binary_sensor", 0),
        ("event", 0),
        ("switch", 1),
        ("text", 1),
        ("button", 1),
    ],
)
def test_platform_parallel_updates(module: str, parallel_updates: int) -> None:
    platform = importlib.import_module(f"custom_components.tel2.{module}")

    assert parallel_updates == platform.PARALLEL_UPDATES


async def test_outage_at_startup_retries_then_loads(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Customer-Details", aiohttp.ClientError("down"))

    entry = await async_setup_integration(hass, fake_tel2)

    assert entry.state is ConfigEntryState.SETUP_RETRY

    fake_tel2.respond("Get-Customer-Details", "get_customer_details.json")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.LOADED
    assert set(runtime(entry).lines.data.lines) == {LINE_1, LINE_2}


async def test_lines_outage_at_startup_retries(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", aiohttp.ClientError("down"))

    entry = await async_setup_integration(hass, fake_tel2)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_auth_failure_at_startup_starts_reauth(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Token", "error_wrong_password.json")

    entry = await async_setup_integration(
        hass,
        fake_tel2,
        entry_data={k: v for k, v in DEFAULT_ENTRY_DATA.items() if k != CONF_TOKEN},
    )

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_first_token_is_persisted(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(
        hass,
        fake_tel2,
        entry_data={k: v for k, v in DEFAULT_ENTRY_DATA.items() if k != CONF_TOKEN},
    )

    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_TOKEN] == FIXTURE_TOKEN
    assert fake_tel2.call_count("Get-Token") == 1


async def test_refreshed_token_is_persisted_without_reload(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    data = runtime(entry)
    details_calls = fake_tel2.call_count("Get-Customer-Details")
    fake_tel2.respond(
        "Get-Account-Balance", "error_invalid_token.json", "get_account_balance.json"
    )

    await data.account.async_refresh()
    await hass.async_block_till_done()

    assert data.account.last_update_success
    assert fake_tel2.call_count("Get-Token") == 1
    assert {key: entry.data[key] for key in DEFAULT_ENTRY_DATA} == {
        **DEFAULT_ENTRY_DATA,
        CONF_TOKEN: FIXTURE_TOKEN,
    }
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is data
    assert fake_tel2.call_count("Get-Customer-Details") == details_calls + 1


async def test_saving_options_reloads_with_new_interval(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    before = runtime(entry)
    assert before.lines.update_interval == timedelta(
        minutes=DEFAULT_SCAN_INTERVAL_MINUTES
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30, CONF_CALL_EVENTS: True}
    )
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    after = runtime(entry)
    assert after is not before
    assert after.lines.update_interval == timedelta(minutes=30)


async def test_unload(hass: HomeAssistant, fake_tel2: FakeTel2Api) -> None:
    entry = await async_setup_integration(hass, fake_tel2)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not async_get_platforms(hass, DOMAIN)


async def test_device_removal_only_for_numbers_that_left(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    account_device = dr.async_get(hass).async_get(runtime(entry).account_device_id)
    assert account_device is not None

    removed = add_device(hass, entry, REMOVED_NUMBER)
    current = add_device(hass, entry, LINE_1)

    assert await async_remove_config_entry_device(hass, entry, removed)
    assert not await async_remove_config_entry_device(hass, entry, current)
    assert not await async_remove_config_entry_device(hass, entry, account_device)


async def test_device_removal_refused_while_not_loaded(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    removed = add_device(hass, entry, REMOVED_NUMBER)
    assert await hass.config_entries.async_unload(entry.entry_id)

    assert not await async_remove_config_entry_device(hass, entry, removed)
