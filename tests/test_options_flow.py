"""Tests for the Tel2 options flow."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorMode,
)
import probatio
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tel2.const import (
    CONF_CALL_EVENTS,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    DEFAULT_CALL_EVENTS,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
)


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    mock_setup_entry: AsyncMock,
    request: pytest.FixtureRequest,
) -> MockConfigEntry:
    options: dict[str, Any] = getattr(request, "param", {})
    entry = mock_config_entry_factory(data={CONF_TOKEN: "old"}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert mock_setup_entry.call_count == 1
    return entry


async def _init(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    return dict(result)


def _field(schema: probatio.Schema, key: str) -> tuple[probatio.Marker, Any]:
    return next((marker, val) for marker, val in schema.schema.items() if marker == key)


def _suggested(result: dict[str, Any]) -> dict[str, Any]:
    schema: probatio.Schema = result["data_schema"]
    return {
        str(marker): (marker.description or {}).get("suggested_value")
        for marker in schema.schema
    }


async def test_form_prefills_defaults_without_options(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await _init(hass, loaded_entry)

    assert _suggested(result) == {
        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_MINUTES,
        CONF_CALL_EVENTS: DEFAULT_CALL_EVENTS,
    }


@pytest.mark.parametrize(
    "loaded_entry",
    [{CONF_SCAN_INTERVAL: 17, CONF_CALL_EVENTS: False}],
    indirect=True,
)
async def test_form_prefills_current_options(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await _init(hass, loaded_entry)

    assert _suggested(result) == {CONF_SCAN_INTERVAL: 17, CONF_CALL_EVENTS: False}


async def test_form_uses_box_number_and_boolean_selectors(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await _init(hass, loaded_entry)
    schema: probatio.Schema = result["data_schema"]

    _, interval = _field(schema, CONF_SCAN_INTERVAL)
    number = next(v for v in interval.validators if isinstance(v, NumberSelector))
    assert number.config["min"] == MIN_SCAN_INTERVAL_MINUTES
    assert number.config["max"] == MAX_SCAN_INTERVAL_MINUTES
    assert number.config["step"] == 1
    assert number.config["mode"] == NumberSelectorMode.BOX
    assert number.config["unit_of_measurement"] == "min"
    _, call_events = _field(schema, CONF_CALL_EVENTS)
    assert isinstance(call_events, BooleanSelector)


@pytest.mark.parametrize(
    ("submitted", "stored"),
    [
        (12, 12),
        (12.0, 12),
        (MIN_SCAN_INTERVAL_MINUTES, MIN_SCAN_INTERVAL_MINUTES),
        (MAX_SCAN_INTERVAL_MINUTES, MAX_SCAN_INTERVAL_MINUTES),
    ],
)
async def test_saving_stores_options_as_int(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    submitted: float,
    stored: int,
) -> None:
    result = await _init(hass, loaded_entry)

    result = dict(
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_SCAN_INTERVAL: submitted, CONF_CALL_EVENTS: False},
        )
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options == {CONF_SCAN_INTERVAL: stored, CONF_CALL_EVENTS: False}
    assert type(loaded_entry.options[CONF_SCAN_INTERVAL]) is int


@pytest.mark.parametrize(
    "interval", [MIN_SCAN_INTERVAL_MINUTES - 1, MAX_SCAN_INTERVAL_MINUTES + 1]
)
async def test_out_of_range_interval_is_rejected(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, interval: int
) -> None:
    result = await _init(hass, loaded_entry)

    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_SCAN_INTERVAL: interval, CONF_CALL_EVENTS: True},
        )

    assert loaded_entry.options == {}


async def test_saving_options_reloads_entry(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, mock_setup_entry: AsyncMock
) -> None:
    result = await _init(hass, loaded_entry)

    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30, CONF_CALL_EVENTS: True}
    )
    await hass.async_block_till_done()

    assert mock_setup_entry.call_count == 2
    assert loaded_entry.state is ConfigEntryState.LOADED


async def test_updating_entry_data_does_not_reload(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, mock_setup_entry: AsyncMock
) -> None:
    hass.config_entries.async_update_entry(loaded_entry, data={CONF_TOKEN: "new"})
    await hass.async_block_till_done()

    assert loaded_entry.data == {CONF_TOKEN: "new"}
    assert not loaded_entry.update_listeners
    assert mock_setup_entry.call_count == 1
