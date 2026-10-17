"""Tests for the Tel2 sensor platform."""

import copy
from dataclasses import replace

from homeassistant.const import (
    ATTR_FRIENDLY_NAME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
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
from tests.fake_tel2 import NO_DATA, FakeTel2Api
from tests.fixture_loader import load_fixture
from tests.helpers import ACCOUNT_CODE, LINE_1, LINE_2, async_setup_integration

ACCOUNT_KEYS = ("balance", "available_balance", "plan", "account_status")
BUNDLE_KEYS = ("bundle_used", "bundle_total")


@pytest.fixture(autouse=True)
def sensor_platform_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.tel2.PLATFORMS", [Platform.SENSOR])


def runtime(entry: MockConfigEntry) -> Tel2RuntimeData:
    data = entry.runtime_data
    assert isinstance(data, Tel2RuntimeData)
    return data


def sensor_id(hass: HomeAssistant, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        Platform.SENSOR, DOMAIN, unique_id
    )
    assert entity_id is not None, unique_id
    return entity_id


def state_of(hass: HomeAssistant, unique_id: str) -> str:
    state = hass.states.get(sensor_id(hass, unique_id))
    assert state is not None
    return state.state


async def test_sensors_snapshot(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)

    await snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)


async def test_bundle_sensors_only_for_numbers_with_a_bundle(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, entity_registry: er.EntityRegistry
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)

    unique_ids = {
        item.unique_id
        for item in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    }
    assert unique_ids == {
        *(f"{ACCOUNT_CODE}_{key}" for key in ACCOUNT_KEYS),
        *(f"{ACCOUNT_CODE}_{LINE_1}_{key}" for key in BUNDLE_KEYS),
    }
    assert not any(LINE_2 in unique_id for unique_id in unique_ids)
    assert state_of(hass, f"{ACCOUNT_CODE}_{LINE_1}_bundle_used") == "37"
    assert state_of(hass, f"{ACCOUNT_CODE}_{LINE_1}_bundle_total") == "1000"


async def test_sensor_names_come_from_translations(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    await async_setup_integration(hass, fake_tel2)

    expected = {
        f"{ACCOUNT_CODE}_balance": "Example Ltd Balance",
        f"{ACCOUNT_CODE}_available_balance": "Example Ltd Available balance",
        f"{ACCOUNT_CODE}_plan": "Example Ltd Plan",
        f"{ACCOUNT_CODE}_account_status": "Example Ltd Account status",
        f"{ACCOUNT_CODE}_{LINE_1}_bundle_used": "Line 1 Bundle minutes used",
        f"{ACCOUNT_CODE}_{LINE_1}_bundle_total": "Line 1 Bundle minutes included",
    }
    for unique_id, name in expected.items():
        state = hass.states.get(sensor_id(hass, unique_id))
        assert state is not None
        assert state.attributes[ATTR_FRIENDLY_NAME] == name


async def test_values_follow_account_refresh(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    assert state_of(hass, f"{ACCOUNT_CODE}_balance") == "12.34"
    fake_tel2.respond(
        "Get-Account-Balance",
        {
            "Result": "1",
            "Status": "Success",
            "Message": "Method Executed Successfully.",
            "Data": {"Balance": "-1.5", "AvailableBalance": "3.21"},
        },
    )

    await runtime(entry).account.async_refresh()
    await hass.async_block_till_done()

    assert state_of(hass, f"{ACCOUNT_CODE}_balance") == "-1.5"
    assert state_of(hass, f"{ACCOUNT_CODE}_available_balance") == "3.21"


async def test_bundle_sensors_unavailable_once_bundle_disappears(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    account = runtime(entry).account

    account.async_set_updated_data(replace(account.data, bundles={}))
    await hass.async_block_till_done()

    for key in BUNDLE_KEYS:
        assert state_of(hass, f"{ACCOUNT_CODE}_{LINE_1}_{key}") == STATE_UNAVAILABLE
    assert state_of(hass, f"{ACCOUNT_CODE}_balance") == "12.34"


async def test_bundle_sensors_added_once_a_bundle_appears(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, caplog: pytest.LogCaptureFixture
) -> None:
    fake_tel2.respond("Get-Line-Bundle-Usage", NO_DATA)
    entry = await async_setup_integration(hass, fake_tel2)
    registry = er.async_get(hass)
    bundle_used = f"{ACCOUNT_CODE}_{LINE_1}_bundle_used"
    assert registry.async_get_entity_id(Platform.SENSOR, DOMAIN, bundle_used) is None

    fake_tel2.respond("Get-Line-Bundle-Usage", "get_line_bundle_usage.json")
    for _ in range(2):
        await runtime(entry).account.async_refresh()
        await hass.async_block_till_done()

    assert state_of(hass, bundle_used) == "37"
    assert state_of(hass, f"{ACCOUNT_CODE}_{LINE_1}_bundle_total") == "1000"
    assert "already exists" not in caplog.text


async def test_plan_unknown_without_a_plan(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Current-Plan-Summary", NO_DATA)

    await async_setup_integration(hass, fake_tel2)

    assert state_of(hass, f"{ACCOUNT_CODE}_plan") == STATE_UNKNOWN
    assert state_of(hass, f"{ACCOUNT_CODE}_account_status") == "Active"


async def test_plan_shows_the_billing_description(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    await async_setup_integration(hass, fake_tel2)

    assert state_of(hass, f"{ACCOUNT_CODE}_plan") == "Example business voice plan"


async def test_plan_falls_back_to_the_alias(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    plan = copy.deepcopy(load_fixture("get_current_plan_summary.json"))
    plan["Data"][0]["BillingDescription"] = ""
    fake_tel2.respond("Get-Current-Plan-Summary", plan)

    await async_setup_integration(hass, fake_tel2)

    assert state_of(hass, f"{ACCOUNT_CODE}_plan") == "Example Plan"


async def test_plan_unknown_when_alias_and_description_are_blank(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    plan = copy.deepcopy(load_fixture("get_current_plan_summary.json"))
    plan["Data"][0]["Alias"] = ""
    plan["Data"][0]["BillingDescription"] = ""
    fake_tel2.respond("Get-Current-Plan-Summary", plan)

    await async_setup_integration(hass, fake_tel2)

    assert state_of(hass, f"{ACCOUNT_CODE}_plan") == STATE_UNKNOWN
