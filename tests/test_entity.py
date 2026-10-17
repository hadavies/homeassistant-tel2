"""Tests for the Tel2 entity base classes and dynamic number devices."""

import copy
from dataclasses import replace
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tel2 import sensor
from custom_components.tel2.const import DOMAIN, MANUFACTURER
from custom_components.tel2.coordinator import (
    Tel2AccountCoordinator,
    Tel2LinesCoordinator,
)
from custom_components.tel2.entity import (
    Tel2AccountEntity,
    Tel2LineEntity,
    async_add_line_entities,
)
from custom_components.tel2.models import Tel2Number
from custom_components.tel2.runtime import Tel2ConfigEntry, Tel2RuntimeData
from tests.fake_tel2 import NO_DATA, FakeTel2Api
from tests.fixture_loader import load_fixture
from tests.helpers import ACCOUNT_CODE, LINE_1, LINE_2, async_setup_integration

LINE_3 = "441632960003"
ACCOUNT_PROBE = SensorEntityDescription(key="account_probe", name="Account probe")
LINE_PROBE = SensorEntityDescription(key="line_probe", name="Line probe")
BUNDLE_PROBE = SensorEntityDescription(key="bundle_probe", name="Bundle probe")


class AccountProbe(Tel2AccountEntity, SensorEntity):
    """Account entity reporting the account status."""

    @property
    def native_value(self) -> str:
        """Return the account status."""
        return self.coordinator.data.details.status


class LineProbe(Tel2LineEntity[Tel2LinesCoordinator], SensorEntity):
    """Line entity on the lines coordinator reporting registration."""

    @property
    def native_value(self) -> str:
        """Return the registration status."""
        line = self.coordinator.data.lines[self.canonical]
        return "registered" if line.registered else "unregistered"


class BundleProbe(Tel2LineEntity[Tel2AccountCoordinator], SensorEntity):
    """Line entity on the account coordinator reporting bundle use."""

    @property
    def native_value(self) -> int | None:
        """Return the bundle minutes used."""
        bundle = self.coordinator.data.bundles.get(self.canonical)
        return bundle.used_minutes if bundle else None


async def probe_setup_entry(
    hass: HomeAssistant,
    entry: Tel2ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities([AccountProbe(data.account, ACCOUNT_PROBE)])

    def factory(number: Tel2Number) -> list[SensorEntity]:
        return [
            LineProbe(data.lines, number, LINE_PROBE),
            BundleProbe(data.account, number, BUNDLE_PROBE),
        ]

    async_add_line_entities(entry, async_add_entities, factory)


@pytest.fixture(autouse=True)
def probe_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.tel2.PLATFORMS", [Platform.SENSOR])
    monkeypatch.setattr(sensor, "async_setup_entry", probe_setup_entry)


def runtime(entry: MockConfigEntry) -> Tel2RuntimeData:
    data = entry.runtime_data
    assert isinstance(data, Tel2RuntimeData)
    return data


def number_device(
    hass: HomeAssistant, entry: MockConfigEntry, canonical: str
) -> dr.DeviceEntry | None:
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, canonical), entry.entry_id
    )


def entity_id(hass: HomeAssistant, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(Platform.SENSOR, DOMAIN, unique_id)


def state_of(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


def line_probe_id(hass: HomeAssistant, canonical: str) -> str | None:
    return entity_id(hass, f"{ACCOUNT_CODE}_{canonical}_line_probe")


def numbers_added_without_line_3_name() -> dict[str, Any]:
    body = copy.deepcopy(load_fixture("get_numbers_number_added.json"))
    for row in body["Data"]:
        if row["ServiceName"] == "01632960003":
            del row["Alias"], row["Parameter_4"]
    return body


async def refresh_account(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await runtime(entry).account.async_refresh()
    await hass.async_block_till_done()


async def test_account_entity_belongs_to_account_device(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    data = runtime(entry)

    registry_entry = er.async_get(hass).async_get(
        entity_id(hass, f"{ACCOUNT_CODE}_account_probe") or ""
    )
    assert registry_entry is not None
    assert registry_entry.device_id == data.account_device_id
    device = dr.async_get(hass).async_get(data.account_device_id)
    assert device is not None
    assert device.identifiers == {(DOMAIN, ACCOUNT_CODE)}
    assert device.manufacturer == MANUFACTURER
    assert device.entry_type is dr.DeviceEntryType.SERVICE
    assert data.account.data.plan is not None
    assert device.model == data.account.data.plan.description
    state = hass.states.get(registry_entry.entity_id)
    assert state is not None
    assert state.state == data.account.data.details.status


async def test_known_numbers_get_line_devices_linked_to_account(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    data = runtime(entry)

    for canonical, name in ((LINE_1, "Line 1"), (LINE_2, "Line 2")):
        device = number_device(hass, entry, canonical)
        assert device is not None
        assert device.name == name
        assert device.manufacturer == MANUFACTURER
        assert device.via_device_id == data.account_device_id
        registry = er.async_get(hass)
        for key in ("line_probe", "bundle_probe"):
            entity = registry.async_get(
                entity_id(hass, f"{ACCOUNT_CODE}_{canonical}_{key}") or ""
            )
            assert entity is not None
            assert entity.device_id == device.id
        probe = hass.states.get(line_probe_id(hass, canonical) or "")
        assert probe is not None
        assert probe.state == "unregistered"


async def test_number_added_on_tel2_gets_device_without_reload(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    data = runtime(entry)
    assert number_device(hass, entry, LINE_3) is None
    fake_tel2.respond("Get-Numbers", "get_numbers_number_added.json")

    await refresh_account(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    device = number_device(hass, entry, LINE_3)
    assert device is not None
    assert device.name == "Line 3"
    assert device.via_device_id == data.account_device_id
    probe_id = line_probe_id(hass, LINE_3)
    assert probe_id is not None
    probe_entry = er.async_get(hass).async_get(probe_id)
    assert probe_entry is not None
    assert probe_entry.device_id == device.id
    assert entity_id(hass, f"{ACCOUNT_CODE}_{LINE_3}_bundle_probe") is not None
    assert state_of(hass, probe_id) == "unregistered"


async def test_line_device_name_falls_back_to_number(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    fake_tel2.respond("Get-Numbers", numbers_added_without_line_3_name())

    await refresh_account(hass, entry)

    device = number_device(hass, entry, LINE_3)
    assert device is not None
    assert device.name == "+441632960003"


async def test_existing_numbers_are_not_added_twice(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, caplog: pytest.LogCaptureFixture
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    before = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)

    await refresh_account(hass, entry)
    fake_tel2.respond("Get-Numbers", "get_numbers_number_added.json")
    await refresh_account(hass, entry)
    await refresh_account(hass, entry)

    after = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert len(after) == len(before) + 2
    assert "already exists" not in caplog.text


async def test_line_unavailable_once_number_leaves_account(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    account = runtime(entry).account
    line_2_probe = line_probe_id(hass, LINE_2)
    line_2_bundle = entity_id(hass, f"{ACCOUNT_CODE}_{LINE_2}_bundle_probe")
    assert line_2_probe is not None
    assert line_2_bundle is not None

    numbers = {LINE_1: account.data.numbers[LINE_1]}
    account.async_set_updated_data(replace(account.data, numbers=numbers))
    await hass.async_block_till_done()

    assert state_of(hass, line_2_probe) == STATE_UNAVAILABLE
    assert state_of(hass, line_2_bundle) == STATE_UNAVAILABLE
    line_1_probe = hass.states.get(line_probe_id(hass, LINE_1) or "")
    assert line_1_probe is not None
    assert line_1_probe.state == "unregistered"


async def test_removed_number_loses_device_and_entities_then_can_return(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    line_2_probe = line_probe_id(hass, LINE_2)
    assert line_2_probe is not None

    fake_tel2.respond("Get-Numbers", "get_numbers_number_removed.json")
    await refresh_account(hass, entry)

    assert number_device(hass, entry, LINE_2) is None
    assert line_probe_id(hass, LINE_2) is None
    assert hass.states.get(line_2_probe) is None
    assert number_device(hass, entry, LINE_1) is not None

    fake_tel2.respond("Get-Numbers", "get_numbers.json")
    await refresh_account(hass, entry)

    assert number_device(hass, entry, LINE_2) is not None
    assert line_probe_id(hass, LINE_2) is not None


async def test_empty_numbers_keep_entities_without_re_adding(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, caplog: pytest.LogCaptureFixture
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    line_1_probe = line_probe_id(hass, LINE_1)
    assert line_1_probe is not None

    fake_tel2.respond("Get-Numbers", NO_DATA)
    await refresh_account(hass, entry)

    assert number_device(hass, entry, LINE_1) is not None
    assert state_of(hass, line_1_probe) == STATE_UNAVAILABLE

    fake_tel2.respond("Get-Numbers", "get_numbers.json")
    await refresh_account(hass, entry)

    assert state_of(hass, line_1_probe) == "unregistered"
    assert "already exists" not in caplog.text


async def test_unique_ids_are_stable_across_reload(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    registry = er.async_get(hass)

    def unique_ids() -> dict[str, str]:
        return {
            item.unique_id: item.entity_id
            for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        }

    before = unique_ids()
    assert set(before) == {
        f"{ACCOUNT_CODE}_account_probe",
        *(
            f"{ACCOUNT_CODE}_{canonical}_{key}"
            for canonical in (LINE_1, LINE_2)
            for key in ("line_probe", "bundle_probe")
        ),
    }

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert unique_ids() == before


async def test_unload_stops_listening_for_new_numbers(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    account = runtime(entry).account
    assert account._listeners

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not account._listeners
