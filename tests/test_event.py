"""Tests for the Tel2 call event platform."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import MagicMock, create_autospec

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.tel2.const import (
    CALL_EVENT_TYPES,
    CONF_CALL_EVENTS,
    DOMAIN,
    CallEventType,
)
from custom_components.tel2.models import CallEvent
from custom_components.tel2.runtime import Tel2RuntimeData
from custom_components.tel2.webhook import Tel2CallEventManager, signal_call_event
from tests.fake_tel2 import FakeTel2Api
from tests.helpers import ACCOUNT_CODE, LINE_1, LINE_2, async_setup_integration
from tests.test_webhook_lifecycle import WEBHOOK_ENTRY_DATA
from tests.test_webhook_manager import EXTERNAL_URL, REGISTERED_URL, ours

CALL_1 = f"{ACCOUNT_CODE}_{LINE_1}_call"
CALL_2 = f"{ACCOUNT_CODE}_{LINE_2}_call"
CALLER = "447700900123"
STARTED = datetime(2026, 10, 17, 9, 30, 5, tzinfo=UTC)


@pytest.fixture(autouse=True)
def event_platform_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.tel2.PLATFORMS", [Platform.EVENT])


def runtime(entry: MockConfigEntry) -> Tel2RuntimeData:
    data = entry.runtime_data
    assert isinstance(data, Tel2RuntimeData)
    return data


def state_of(hass: HomeAssistant, unique_id: str) -> State:
    entity_id = er.async_get(hass).async_get_entity_id(
        Platform.EVENT, DOMAIN, unique_id
    )
    assert entity_id is not None, unique_id
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def setup_without_manager(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> MockConfigEntry:
    return await async_setup_integration(
        hass, fake_tel2, options={CONF_CALL_EVENTS: False}
    )


async def inject_manager(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    available: bool = True,
    registered: bool = True,
) -> MagicMock:
    manager = create_autospec(Tel2CallEventManager, instance=True)
    manager.available = available
    manager.registered = registered
    data = runtime(entry)
    data.call_events = manager
    data.account.async_update_listeners()
    await hass.async_block_till_done()
    return manager


def call_event(
    event_type: CallEventType = CallEventType.ANSWERED,
    *,
    number: str = LINE_1,
    other: str = CALLER,
    start_time: datetime | None = STARTED,
    duration: int | None = 42,
) -> CallEvent:
    return CallEvent(
        number=number,
        event_type=event_type,
        direction="inbound",
        from_number=other,
        to_number=number,
        call_id="call-1",
        origin_id="call-1",
        start_time=start_time,
        duration=duration,
    )


async def publish(
    hass: HomeAssistant, entry: MockConfigEntry, event: CallEvent
) -> None:
    async_dispatcher_send(hass, signal_call_event(entry.entry_id), event)
    await hass.async_block_till_done()


async def test_event_snapshot(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    entry = await setup_without_manager(hass, fake_tel2)
    await inject_manager(hass, entry)

    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)


async def test_one_call_entity_per_number_listing_every_type(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await setup_without_manager(hass, fake_tel2)
    await inject_manager(hass, entry)

    for unique_id in (CALL_1, CALL_2):
        state = state_of(hass, unique_id)
        assert state.state == STATE_UNKNOWN
        assert state.attributes["event_types"] == [str(t) for t in CALL_EVENT_TYPES]


async def test_published_call_event_fires_matching_entity(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await setup_without_manager(hass, fake_tel2)
    await inject_manager(hass, entry)

    await publish(hass, entry, call_event())

    state = state_of(hass, CALL_1)
    assert dt_util.parse_datetime(state.state) is not None
    assert state.attributes["event_type"] == "answered"
    assert state.attributes["from"] == f"+{CALLER}"
    assert state.attributes["to"] == f"+{LINE_1}"
    assert state.attributes["direction"] == "inbound"
    assert state.attributes["call_id"] == "call-1"
    assert state.attributes["start_time"] == STARTED.isoformat()
    assert state.attributes["duration"] == 42
    assert state_of(hass, CALL_2).state == STATE_UNKNOWN


async def test_outbound_call_event_fires_calling_number(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await setup_without_manager(hass, fake_tel2)
    await inject_manager(hass, entry)
    event = CallEvent(
        number=LINE_2,
        event_type=CallEventType.BUSY,
        direction="outbound",
        from_number=LINE_2,
        to_number=CALLER,
        call_id="call-2",
        origin_id="call-2",
        start_time=None,
        duration=None,
    )

    await publish(hass, entry, event)

    state = state_of(hass, CALL_2)
    assert state.attributes["event_type"] == "busy"
    assert state.attributes["from"] == f"+{LINE_2}"
    assert state.attributes["to"] == f"+{CALLER}"
    assert state.attributes["direction"] == "outbound"
    assert state_of(hass, CALL_1).state == STATE_UNKNOWN


async def test_withheld_caller_kept_raw_without_time_or_duration(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await setup_without_manager(hass, fake_tel2)
    await inject_manager(hass, entry)

    await publish(
        hass,
        entry,
        call_event(
            CallEventType.MISSED, other="anonymous", start_time=None, duration=None
        ),
    )

    state = state_of(hass, CALL_1)
    assert state.attributes["event_type"] == "missed"
    assert state.attributes["from"] == "anonymous"
    assert state.attributes["start_time"] is None
    assert state.attributes["duration"] is None


@pytest.mark.parametrize("event_type", list(CallEventType))
async def test_every_call_event_type_is_accepted(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, event_type: CallEventType
) -> None:
    entry = await setup_without_manager(hass, fake_tel2)
    await inject_manager(hass, entry)

    await publish(hass, entry, call_event(event_type))

    assert state_of(hass, CALL_1).attributes["event_type"] == str(event_type)


async def test_unavailable_without_a_manager(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await setup_without_manager(hass, fake_tel2)

    assert runtime(entry).call_events is None
    assert state_of(hass, CALL_1).state == STATE_UNAVAILABLE
    assert state_of(hass, CALL_2).state == STATE_UNAVAILABLE


async def test_availability_follows_the_manager(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await setup_without_manager(hass, fake_tel2)
    manager = await inject_manager(hass, entry, available=False)

    assert state_of(hass, CALL_1).state == STATE_UNAVAILABLE

    manager.available = True
    runtime(entry).account.async_update_listeners()
    await hass.async_block_till_done()

    assert state_of(hass, CALL_1).state == STATE_UNKNOWN


async def test_unavailable_until_the_registration_is_on_tel2(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await setup_without_manager(hass, fake_tel2)
    manager = await inject_manager(hass, entry, registered=False)

    assert state_of(hass, CALL_1).state == STATE_UNAVAILABLE

    manager.registered = True
    runtime(entry).account.async_update_listeners()
    await hass.async_block_till_done()

    assert state_of(hass, CALL_1).state == STATE_UNKNOWN


async def test_unavailable_once_the_number_leaves_the_account(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await setup_without_manager(hass, fake_tel2)
    await inject_manager(hass, entry)
    account = runtime(entry).account

    numbers = {LINE_1: account.data.numbers[LINE_1]}
    account.async_set_updated_data(replace(account.data, numbers=numbers))
    await hass.async_block_till_done()

    assert state_of(hass, CALL_1).state == STATE_UNKNOWN
    assert state_of(hass, CALL_2).state == STATE_UNAVAILABLE


@pytest.mark.parametrize("other", ["000", "\u0661\u0662\u0663"])
async def test_withheld_digits_are_not_shown_as_a_number(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, other: str
) -> None:
    entry = await setup_without_manager(hass, fake_tel2)
    await inject_manager(hass, entry)

    await publish(hass, entry, call_event(CallEventType.MISSED, other=other))

    assert state_of(hass, CALL_1).attributes["from"] == other


async def test_manager_availability_change_updates_the_entity(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Webhooks", ours(REGISTERED_URL))
    entry = await async_setup_integration(
        hass, fake_tel2, entry_data=WEBHOOK_ENTRY_DATA
    )
    manager = runtime(entry).call_events
    assert isinstance(manager, Tel2CallEventManager)
    assert state_of(hass, CALL_1).state == STATE_UNAVAILABLE
    refreshes = fake_tel2.call_count("Get-Customer-Details")

    hass.config.external_url = EXTERNAL_URL
    await manager.async_reconcile()
    await hass.async_block_till_done()

    assert state_of(hass, CALL_1).state == STATE_UNKNOWN
    assert fake_tel2.call_count("Get-Customer-Details") == refreshes

    hass.config.external_url = None
    await manager.async_reconcile()
    await hass.async_block_till_done()

    assert state_of(hass, CALL_1).state == STATE_UNAVAILABLE
    assert fake_tel2.call_count("Get-Customer-Details") == refreshes
