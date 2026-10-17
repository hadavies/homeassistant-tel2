"""Tests for call events across the Tel2 entry lifecycle."""

import logging
from typing import Any
from unittest.mock import patch

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.tel2.const import (
    CONF_CALL_EVENTS,
    CONF_SCAN_INTERVAL,
    CONF_TEL2_WEBHOOK_ID,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from custom_components.tel2.runtime import Tel2RuntimeData
from custom_components.tel2.webhook import SECRET_HEADER, Tel2CallEventManager
from tests.fake_tel2 import NO_DATA, FakeTel2Api
from tests.fixture_loader import load_fixture
from tests.helpers import (
    ACCOUNT_CODE,
    DEFAULT_ENTRY_DATA,
    LINE_1,
    async_setup_integration,
)
from tests.test_webhook_manager import (
    EXTERNAL_URL,
    REGISTERED_URL,
    WEBHOOK_ID,
    WEBHOOK_SECRET,
    ours,
)

CALL_1 = f"{ACCOUNT_CODE}_{LINE_1}_call"
WEBHOOK_PATH = f"/api/webhook/{WEBHOOK_ID}"
WEBHOOK_ENTRY_DATA = {
    **DEFAULT_ENTRY_DATA,
    CONF_WEBHOOK_ID: WEBHOOK_ID,
    CONF_WEBHOOK_SECRET: WEBHOOK_SECRET,
}


@pytest.fixture(autouse=True)
def event_platform_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.tel2.PLATFORMS", [Platform.EVENT])


def runtime(entry: MockConfigEntry) -> Tel2RuntimeData:
    data = entry.runtime_data
    assert isinstance(data, Tel2RuntimeData)
    return data


def call_state(hass: HomeAssistant) -> State:
    entity_id = er.async_get(hass).async_get_entity_id(Platform.EVENT, DOMAIN, CALL_1)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def options(*, call_events: bool) -> dict[str, Any]:
    return {
        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_MINUTES,
        CONF_CALL_EVENTS: call_events,
    }


async def set_call_events(
    hass: HomeAssistant, entry: MockConfigEntry, *, enabled: bool
) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], options(call_events=enabled)
    )
    await hass.async_block_till_done()


async def setup_with_external_url(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, **kwargs: Any
) -> MockConfigEntry:
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond("Get-Webhooks", NO_DATA, ours(REGISTERED_URL))
    kwargs.setdefault("entry_data", WEBHOOK_ENTRY_DATA)
    return await async_setup_integration(hass, fake_tel2, **kwargs)


def ha_webhook_registered(hass: HomeAssistant) -> bool:
    return WEBHOOK_ID in hass.data.get("webhook", {})


async def test_call_event_lifecycle_end_to_end(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    entry = await setup_with_external_url(hass, fake_tel2)

    assert entry.state is ConfigEntryState.LOADED
    [added] = fake_tel2.requests_for("Add-Webhook")
    assert added.params["url"] == REGISTERED_URL
    assert added.params["secrettoken"] == WEBHOOK_SECRET
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "7"
    assert call_state(hass).state == STATE_UNKNOWN

    client = await hass_client_no_auth()
    response = await client.post(
        WEBHOOK_PATH,
        json=load_fixture("webhook_inbound_answered.json"),
        headers={SECRET_HEADER: WEBHOOK_SECRET},
    )
    assert response.status == 200
    await hass.async_block_till_done()
    fired = call_state(hass)
    assert dt_util.parse_datetime(fired.state) is not None
    assert fired.attributes["event_type"] == "answered"

    await set_call_events(hass, entry, enabled=False)

    assert entry.state is ConfigEntryState.LOADED
    [deleted] = fake_tel2.requests_for("Delete-Webhook")
    assert deleted.params["id"] == "7"
    assert CONF_TEL2_WEBHOOK_ID not in entry.data
    assert runtime(entry).call_events is None
    assert not ha_webhook_registered(hass)
    assert call_state(hass).state == STATE_UNAVAILABLE

    fake_tel2.respond("Get-Webhooks", NO_DATA, ours(REGISTERED_URL, "8"))
    await set_call_events(hass, entry, enabled=True)

    assert entry.state is ConfigEntryState.LOADED
    assert fake_tel2.call_count("Add-Webhook") == 2
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "8"
    assert ha_webhook_registered(hass)
    assert call_state(hass).state != STATE_UNAVAILABLE

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert [r.params["id"] for r in fake_tel2.requests_for("Delete-Webhook")] == [
        "7",
        "8",
    ]
    assert not ha_webhook_registered(hass)


async def test_default_setup_starts_the_manager_without_a_public_url(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)

    assert entry.state is ConfigEntryState.LOADED
    manager = runtime(entry).call_events
    assert isinstance(manager, Tel2CallEventManager)
    assert manager.available is False
    assert fake_tel2.call_count("Add-Webhook") == 0
    assert fake_tel2.call_count("Get-Webhooks") == 0
    assert call_state(hass).state == STATE_UNAVAILABLE


async def test_account_refresh_reconciles_the_registration(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await setup_with_external_url(hass, fake_tel2)
    lookups = fake_tel2.call_count("Get-Webhooks")

    await runtime(entry).account.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert fake_tel2.call_count("Get-Webhooks") == lookups + 1
    assert fake_tel2.call_count("Add-Webhook") == 1


async def test_account_refresh_re_adds_a_missing_registration(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await setup_with_external_url(hass, fake_tel2)
    fake_tel2.respond("Get-Webhooks", NO_DATA, ours(REGISTERED_URL, "9"))

    await runtime(entry).account.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert [r.params["url"] for r in fake_tel2.requests_for("Add-Webhook")] == [
        REGISTERED_URL,
        REGISTERED_URL,
    ]
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "9"


async def test_failed_account_refresh_does_not_reconcile(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await setup_with_external_url(hass, fake_tel2)
    lookups = fake_tel2.call_count("Get-Webhooks")
    fake_tel2.respond("Get-Customer-Details", aiohttp.ClientError("down"))

    await runtime(entry).account.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert not runtime(entry).account.last_update_success
    assert fake_tel2.call_count("Get-Webhooks") == lookups


async def test_call_event_fires_while_the_account_refresh_fails(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    entry = await setup_with_external_url(hass, fake_tel2)
    account = runtime(entry).account
    fake_tel2.respond("Get-Line-Bundle-Usage", load_fixture("error_generic.json"))
    await account.async_refresh()
    await hass.async_block_till_done()
    assert not account.last_update_success
    assert call_state(hass).state == STATE_UNKNOWN
    changes: list[State] = []

    @callback
    def record(event: Event[EventStateChangedData]) -> None:
        if (state := event.data["new_state"]) is not None:
            changes.append(state)

    async_track_state_change_event(hass, call_state(hass).entity_id, record)
    client = await hass_client_no_auth()
    response = await client.post(
        WEBHOOK_PATH,
        json=load_fixture("webhook_inbound_missed.json"),
        headers={SECRET_HEADER: WEBHOOK_SECRET},
    )
    assert response.status == 200
    await hass.async_block_till_done()

    [fired] = changes
    assert dt_util.parse_datetime(fired.state) is not None
    assert fired.attributes["event_type"] == "missed"

    fake_tel2.respond("Get-Line-Bundle-Usage", "get_line_bundle_usage.json")
    await account.async_refresh()
    await hass.async_block_till_done()

    assert account.last_update_success
    assert changes == [fired]


async def test_unload_keeps_the_tel2_registration(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await setup_with_external_url(hass, fake_tel2)
    assert ha_webhook_registered(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not ha_webhook_registered(hass)
    assert fake_tel2.call_count("Delete-Webhook") == 0
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "7"


async def test_disabled_setup_deletes_a_stale_registration(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(
        hass,
        fake_tel2,
        options=options(call_events=False),
        entry_data={**WEBHOOK_ENTRY_DATA, CONF_TEL2_WEBHOOK_ID: "7"},
    )

    assert entry.state is ConfigEntryState.LOADED
    assert [r.params["id"] for r in fake_tel2.requests_for("Delete-Webhook")] == ["7"]
    assert CONF_TEL2_WEBHOOK_ID not in entry.data
    assert runtime(entry).call_events is None
    assert not ha_webhook_registered(hass)
    assert fake_tel2.call_count("Add-Webhook") == 0


async def test_disabled_setup_without_a_registration_calls_nothing(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(
        hass, fake_tel2, options=options(call_events=False)
    )

    assert entry.state is ConfigEntryState.LOADED
    assert runtime(entry).call_events is None
    for action in ("Get-Webhooks", "Add-Webhook", "Delete-Webhook"):
        assert fake_tel2.call_count(action) == 0
    assert call_state(hass).state == STATE_UNAVAILABLE


async def test_disabled_setup_keeps_the_id_when_tel2_is_down(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Delete-Webhook", aiohttp.ClientError("down"))

    entry = await async_setup_integration(
        hass,
        fake_tel2,
        options=options(call_events=False),
        entry_data={**WEBHOOK_ENTRY_DATA, CONF_TEL2_WEBHOOK_ID: "7"},
    )

    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "7"


async def test_unexpected_start_failure_does_not_fail_setup(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch.object(
        Tel2CallEventManager, "async_start", side_effect=RuntimeError("boom")
    ):
        entry = await setup_with_external_url(hass, fake_tel2)

    assert entry.state is ConfigEntryState.LOADED
    assert runtime(entry).call_events is None
    assert call_state(hass).state == STATE_UNAVAILABLE
    assert "Could not start Tel2 call events" in caplog.text

    await runtime(entry).account.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert fake_tel2.call_count("Get-Webhooks") == 0


async def test_removal_with_tel2_down_completes_and_warns(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entry = await setup_with_external_url(hass, fake_tel2)
    fake_tel2.respond("Delete-Webhook", aiohttp.ClientError("down"))

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert fake_tel2.call_count("Delete-Webhook") == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("may remain on the Tel2 account" in r.getMessage() for r in warnings)
    assert "Error calling entry remove callback" not in caplog.text


async def test_removal_without_a_registration_calls_nothing(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert fake_tel2.call_count("Delete-Webhook") == 0
