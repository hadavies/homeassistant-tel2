"""Tests for the Tel2 switch platform."""

import copy
from typing import Any
from unittest.mock import AsyncMock

from aiohttp import ClientError
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.tel2.const import DOMAIN, PREF_FORWARD_ALWAYS_NUMBER
from custom_components.tel2.runtime import Tel2RuntimeData
from tests.fake_tel2 import FakeTel2Api
from tests.fixture_loader import load_fixture
from tests.helpers import ACCOUNT_CODE, LINE_1, LINE_2, async_setup_integration

ALL_ON = "get_all_pref_summary_all_on.json"
SWITCH_KEYS = (
    "do_not_disturb",
    "call_forward_always",
    "anonymous_reject",
    "call_recording",
)
PREF_SWITCHES = {
    "do_not_disturb": "do_not_disturb_status",
    "call_forward_always": "callforward_always_status",
    "anonymous_reject": "anoncall_reject",
}


@pytest.fixture(autouse=True)
def switch_platform_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.tel2.PLATFORMS", [Platform.SWITCH])


def runtime(entry: MockConfigEntry) -> Tel2RuntimeData:
    data = entry.runtime_data
    assert isinstance(data, Tel2RuntimeData)
    return data


def switch_id(hass: HomeAssistant, key: str, line: str = LINE_1) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        Platform.SWITCH, DOMAIN, f"{ACCOUNT_CODE}_{line}_{key}"
    )
    assert entity_id is not None, key
    return entity_id


def state_of(hass: HomeAssistant, key: str, line: str = LINE_1) -> str:
    state = hass.states.get(switch_id(hass, key, line))
    assert state is not None
    return state.state


def prefs_with_forward_number() -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(load_fixture("get_all_pref_summary.json"))
    for row in body["Data"]:
        row[PREF_FORWARD_ALWAYS_NUMBER] = "07700900123"
    return body


async def switch(hass: HomeAssistant, service: str, key: str) -> None:
    await hass.services.async_call(
        Platform.SWITCH,
        service,
        {ATTR_ENTITY_ID: switch_id(hass, key)},
        blocking=True,
    )


def line_1_tel2_id(entry: MockConfigEntry) -> str:
    return runtime(entry).account.data.numbers[LINE_1].tel2_id


async def test_switches_snapshot_all_off(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)

    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)


async def test_switches_snapshot_all_on(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", ALL_ON)

    entry = await async_setup_integration(hass, fake_tel2)

    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)


async def test_four_switches_per_number_named_from_translations(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, entity_registry: er.EntityRegistry
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)

    unique_ids = {
        item.unique_id
        for item in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    }
    assert unique_ids == {
        f"{ACCOUNT_CODE}_{line}_{key}"
        for line in (LINE_1, LINE_2)
        for key in SWITCH_KEYS
    }
    names = {
        "do_not_disturb": "Line 1 Do not disturb",
        "call_forward_always": "Line 1 Forward all calls",
        "anonymous_reject": "Line 1 Reject anonymous calls",
        "call_recording": "Line 1 Call recording",
    }
    for key, name in names.items():
        state = hass.states.get(switch_id(hass, key))
        assert state is not None
        assert state.attributes[ATTR_FRIENDLY_NAME] == name


@pytest.mark.parametrize(
    ("fixture", "expected"), [(None, STATE_OFF), (ALL_ON, STATE_ON)]
)
async def test_switches_read_state_from_prefs(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, fixture: str | None, expected: str
) -> None:
    if fixture:
        fake_tel2.respond("Get-All-Pref-Summary", fixture)

    await async_setup_integration(hass, fake_tel2)

    for line in (LINE_1, LINE_2):
        for key in SWITCH_KEYS:
            assert state_of(hass, key, line) == expected, (line, key)


@pytest.mark.parametrize(
    ("pref", "value", "expected"),
    [
        ("do_not_disturb_status", "1", STATE_ON),
        ("profile_recordingenabled", "1", STATE_ON),
        ("profile_recordingenabled", "0", STATE_OFF),
        ("callrecording_disabled", "0", STATE_OFF),
        ("callrecording_disabled", "1", STATE_OFF),
    ],
)
async def test_each_switch_follows_only_its_own_pref(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    pref: str,
    value: str,
    expected: str,
) -> None:
    body: dict[str, Any] = copy.deepcopy(load_fixture("get_all_pref_summary.json"))
    body["Data"][0][pref] = value
    fake_tel2.respond("Get-All-Pref-Summary", body)

    await async_setup_integration(hass, fake_tel2)

    key = "do_not_disturb" if pref == "do_not_disturb_status" else "call_recording"
    assert state_of(hass, key) == expected
    others = [other for other in SWITCH_KEYS if other != key]
    assert all(state_of(hass, other) == STATE_OFF for other in others)
    assert state_of(hass, key, LINE_2) == STATE_OFF


@pytest.mark.parametrize("key", list(PREF_SWITCHES))
async def test_turn_on_sets_pref_to_one(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, key: str
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", prefs_with_forward_number())
    entry = await async_setup_integration(hass, fake_tel2)

    await switch(hass, SERVICE_TURN_ON, key)

    [request] = fake_tel2.requests_for("Set-Pref-Value")
    assert request.context == "voice"
    assert request.params == {
        "line": line_1_tel2_id(entry),
        "pref": PREF_SWITCHES[key],
        "value": "1",
    }


@pytest.mark.parametrize("key", list(PREF_SWITCHES))
async def test_turn_off_sets_pref_to_zero(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, key: str
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", ALL_ON)
    entry = await async_setup_integration(hass, fake_tel2)

    await switch(hass, SERVICE_TURN_OFF, key)

    [request] = fake_tel2.requests_for("Set-Pref-Value")
    assert request.params == {
        "line": line_1_tel2_id(entry),
        "pref": PREF_SWITCHES[key],
        "value": "0",
    }


@pytest.mark.parametrize(
    ("service", "action"),
    [(SERVICE_TURN_ON, "Enable-Recording"), (SERVICE_TURN_OFF, "Disable-Recording")],
)
async def test_recording_uses_its_own_actions(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, service: str, action: str
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)

    await switch(hass, service, "call_recording")

    [request] = fake_tel2.requests_for(action)
    assert request.context == "voice"
    assert request.params == {"number": line_1_tel2_id(entry)}
    assert fake_tel2.call_count("Set-Pref-Value") == 0


@pytest.mark.parametrize(
    ("key", "pref", "value"),
    [
        ("do_not_disturb", "do_not_disturb_status", "1"),
        ("call_forward_always", "callforward_always_status", "1"),
        ("anonymous_reject", "anoncall_reject", "1"),
        ("call_recording", "profile_recordingenabled", "1"),
    ],
)
async def test_turn_on_shows_on_before_any_refresh(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, key: str, pref: str, value: str
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", prefs_with_forward_number())
    entry = await async_setup_integration(hass, fake_tel2)
    lines = runtime(entry).lines
    refresh = AsyncMock()
    lines.async_request_refresh = refresh  # type: ignore[method-assign]
    fetches = fake_tel2.call_count("Get-All-Pref-Summary")

    await switch(hass, SERVICE_TURN_ON, key)

    assert state_of(hass, key) == STATE_ON
    assert lines.data.lines[LINE_1].prefs[pref] == value
    assert fake_tel2.call_count("Get-All-Pref-Summary") == fetches
    refresh.assert_awaited_once()


@pytest.mark.parametrize("key", SWITCH_KEYS)
async def test_turn_off_shows_off_before_any_refresh(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, key: str
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", ALL_ON)
    entry = await async_setup_integration(hass, fake_tel2)
    lines = runtime(entry).lines
    lines.async_request_refresh = AsyncMock()  # type: ignore[method-assign]

    await switch(hass, SERVICE_TURN_OFF, key)

    assert state_of(hass, key) == STATE_OFF
    assert state_of(hass, key, LINE_2) == STATE_ON


@pytest.mark.parametrize(
    ("key", "action", "rejection"),
    [
        ("do_not_disturb", "Set-Pref-Value", "set_pref_value_rejected.json"),
        ("call_forward_always", "Set-Pref-Value", "set_pref_value_rejected.json"),
        ("anonymous_reject", "Set-Pref-Value", "set_pref_value_rejected.json"),
        ("call_recording", "Enable-Recording", "enable_recording_rejected.json"),
        ("do_not_disturb", "Set-Pref-Value", ClientError("boom")),
    ],
)
async def test_tel2_failure_raises_and_leaves_state(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    key: str,
    action: str,
    rejection: str | BaseException,
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", prefs_with_forward_number())
    fake_tel2.respond(action, rejection)
    entry = await async_setup_integration(hass, fake_tel2)
    lines = runtime(entry).lines
    before = copy.deepcopy(lines.data)

    with pytest.raises(HomeAssistantError) as raised:
        await switch(hass, SERVICE_TURN_ON, key)

    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "set_failed"
    assert fake_tel2.call_count(action) >= 1
    assert state_of(hass, key) == STATE_OFF
    assert lines.data == before


async def test_recording_disable_failure_leaves_it_on(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", ALL_ON)
    fake_tel2.respond("Disable-Recording", "disable_recording_rejected.json")
    await async_setup_integration(hass, fake_tel2)

    with pytest.raises(HomeAssistantError):
        await switch(hass, SERVICE_TURN_OFF, "call_recording")

    assert state_of(hass, "call_recording") == STATE_ON


async def test_forward_always_needs_a_destination(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    assert (
        PREF_FORWARD_ALWAYS_NUMBER not in runtime(entry).lines.data.lines[LINE_1].prefs
    )

    with pytest.raises(ServiceValidationError) as raised:
        await switch(hass, SERVICE_TURN_ON, "call_forward_always")

    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "forward_destination_required"
    assert fake_tel2.call_count("Set-Pref-Value") == 0
    assert state_of(hass, "call_forward_always") == STATE_OFF


async def test_forward_always_blank_destination_is_refused(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    body = prefs_with_forward_number()
    body["Data"][0][PREF_FORWARD_ALWAYS_NUMBER] = "  "
    fake_tel2.respond("Get-All-Pref-Summary", body)
    await async_setup_integration(hass, fake_tel2)

    with pytest.raises(ServiceValidationError):
        await switch(hass, SERVICE_TURN_ON, "call_forward_always")

    assert fake_tel2.call_count("Set-Pref-Value") == 0


async def test_forward_always_turns_off_without_a_destination(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    body: dict[str, Any] = copy.deepcopy(load_fixture("get_all_pref_summary.json"))
    body["Data"][0]["callforward_always_status"] = "1"
    fake_tel2.respond("Get-All-Pref-Summary", body)
    await async_setup_integration(hass, fake_tel2)

    await switch(hass, SERVICE_TURN_OFF, "call_forward_always")

    [request] = fake_tel2.requests_for("Set-Pref-Value")
    assert request.params["value"] == "0"


async def test_external_change_shows_after_lines_refresh(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    assert all(state_of(hass, key) == STATE_OFF for key in SWITCH_KEYS)
    fake_tel2.respond("Get-All-Pref-Summary", ALL_ON)

    await runtime(entry).lines.async_refresh()
    await hass.async_block_till_done()

    for line in (LINE_1, LINE_2):
        assert all(state_of(hass, key, line) == STATE_ON for key in SWITCH_KEYS)
