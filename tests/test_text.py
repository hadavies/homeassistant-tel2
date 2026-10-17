"""Tests for the Tel2 text platform."""

from homeassistant.components.text import ATTR_VALUE, DOMAIN as TEXT_DOMAIN
from homeassistant.components.text.const import SERVICE_SET_VALUE
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import snapshot_platform
from syrupy.assertion import SnapshotAssertion

from custom_components.tel2.const import DOMAIN, PREF_FORWARD_ALWAYS_NUMBER
from tests.fake_tel2 import FakeTel2Api
from tests.helpers import ACCOUNT_CODE, LINE_1, LINE_2, async_setup_integration

ALL_ON = "get_all_pref_summary_all_on.json"


@pytest.fixture(autouse=True)
def text_platform_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.tel2.PLATFORMS", [Platform.TEXT])


def text_id(hass: HomeAssistant, line: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        Platform.TEXT, DOMAIN, f"{ACCOUNT_CODE}_{line}_forward_destination"
    )
    assert entity_id is not None, line
    return entity_id


def state_of(hass: HomeAssistant, line: str) -> str:
    state = hass.states.get(text_id(hass, line))
    assert state is not None
    return state.state


async def set_value(hass: HomeAssistant, line: str, value: str) -> None:
    await hass.services.async_call(
        TEXT_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: text_id(hass, line), ATTR_VALUE: value},
        blocking=True,
    )


async def test_text_snapshot(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)

    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)


async def test_unset_destination_is_unknown(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    await async_setup_integration(hass, fake_tel2)

    assert state_of(hass, LINE_1) == STATE_UNKNOWN
    assert state_of(hass, LINE_2) == STATE_UNKNOWN


async def test_destination_shown_from_prefs(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", ALL_ON)

    await async_setup_integration(hass, fake_tel2)

    assert state_of(hass, LINE_1) == "07700900123"
    assert state_of(hass, LINE_2) == "07700900124"


@pytest.mark.parametrize(
    ("typed", "sent"),
    [
        ("+44 7700 900123", "07700900123"),
        ("07700 900123", "07700900123"),
        ("+44 (0)7700 900123", "07700900123"),
        ("+1 202 555 0147", "0012025550147"),
    ],
)
async def test_valid_destination_is_saved_in_tel2_format(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, typed: str, sent: str
) -> None:
    await async_setup_integration(hass, fake_tel2)
    fake_tel2.respond("Get-All-Pref-Summary", ALL_ON)

    await set_value(hass, LINE_1, typed)

    [request] = fake_tel2.requests_for("Set-Pref-Value")
    assert request.context == "voice"
    assert request.params == {
        "line": "01632960001",
        "pref": PREF_FORWARD_ALWAYS_NUMBER,
        "value": sent,
    }


async def test_saved_destination_shows_before_the_refresh(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    await async_setup_integration(hass, fake_tel2)
    entity_id = text_id(hass, LINE_1)
    refreshes = fake_tel2.call_count("Get-All-Pref-Summary")
    seen: list[str] = []
    hass.bus.async_listen(
        "state_changed",
        lambda event: (
            seen.append(event.data["new_state"].state)
            if event.data["entity_id"] == entity_id
            else None
        ),
    )

    await set_value(hass, LINE_1, "+44 7700 900123")
    await hass.async_block_till_done()

    # The fixture refresh still has no destination, so Tel2's value wins after it.
    assert seen == ["07700900123", STATE_UNKNOWN]
    assert fake_tel2.call_count("Get-All-Pref-Summary") == refreshes + 1


async def test_saved_destination_is_reflected_after_the_refresh(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    await async_setup_integration(hass, fake_tel2)
    fake_tel2.respond("Get-All-Pref-Summary", ALL_ON)

    await set_value(hass, LINE_1, "07700 900123")
    await hass.async_block_till_done()

    assert state_of(hass, LINE_1) == "07700900123"
    assert state_of(hass, LINE_2) == "07700900124"


@pytest.mark.parametrize(
    "value", ["+0 7700 900123", "(((((((", "0044 (0)", "+1 23 45", "7700 900123"]
)
async def test_invalid_destination_is_refused_without_a_tel2_call(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, value: str
) -> None:
    await async_setup_integration(hass, fake_tel2)
    calls = len(fake_tel2.requests)

    with pytest.raises(ServiceValidationError) as err:
        await set_value(hass, LINE_1, value)

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "invalid_destination"
    assert len(fake_tel2.requests) == calls
    assert state_of(hass, LINE_1) == STATE_UNKNOWN


async def test_text_outside_the_pattern_is_refused_without_a_tel2_call(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    await async_setup_integration(hass, fake_tel2)
    calls = len(fake_tel2.requests)

    with pytest.raises(ValueError, match="pattern"):
        await set_value(hass, LINE_1, "call me maybe")

    assert len(fake_tel2.requests) == calls


async def test_tel2_rejection_keeps_the_previous_destination(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", ALL_ON)
    await async_setup_integration(hass, fake_tel2)
    refreshes = fake_tel2.call_count("Get-All-Pref-Summary")
    fake_tel2.respond("Set-Pref-Value", "set_pref_value_rejected.json")

    with pytest.raises(HomeAssistantError) as err:
        await set_value(hass, LINE_1, "07700 900999")

    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "forward_destination_failed"
    assert fake_tel2.call_count("Set-Pref-Value") == 1
    assert fake_tel2.call_count("Get-All-Pref-Summary") == refreshes
    assert state_of(hass, LINE_1) == "07700900123"


async def test_unusable_stored_destination_is_unknown(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    lines = entry.runtime_data.lines

    async def no_refresh() -> None:
        return None

    monkeypatch.setattr(lines, "async_request_refresh", no_refresh)

    for stored in ("", "sip:someone@example.com", "+123456789012345678901"):
        await lines.async_apply_optimistic(LINE_1, PREF_FORWARD_ALWAYS_NUMBER, stored)
        await hass.async_block_till_done()
        assert state_of(hass, LINE_1) == STATE_UNKNOWN
