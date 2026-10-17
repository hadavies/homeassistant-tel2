"""Tests for the Tel2 account coordinator."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from decimal import Decimal
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tel2.api import Tel2AuthError, Tel2Client, Tel2ConnectionError
import custom_components.tel2.config_flow  # noqa: F401  registers the reauth handler
from custom_components.tel2.const import (
    ACCOUNT_SCAN_INTERVAL,
    CONF_LOGIN,
    CONF_PASSWORD,
    DOMAIN,
)
from custom_components.tel2.coordinator import Tel2AccountCoordinator
from custom_components.tel2.models import (
    AccountBalance,
    AccountState,
    BundleUsage,
    CustomerDetails,
    PlanSummary,
    Tel2Number,
)
from tests.fake_tel2 import NO_DATA, FakeTel2Api

ACCOUNT_CODE = "00000000"
LINE_1 = "441632960001"
LINE_2 = "441632960002"
ERROR_LOG = "Error fetching tel2 account data"
RECOVERED_LOG = "Fetching tel2 account data recovered"


def success(data: Any) -> dict[str, Any]:
    return {
        "Result": "1",
        "Status": "Success",
        "Message": "Method Executed Successfully.",
        "Data": data,
    }


@pytest.fixture
def entry(
    hass: HomeAssistant, mock_config_entry_factory: Callable[..., MockConfigEntry]
) -> MockConfigEntry:
    entry = mock_config_entry_factory(
        unique_id=ACCOUNT_CODE,
        data={CONF_LOGIN: "ha-login", CONF_PASSWORD: "hunter2"},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def client(fake_tel2: FakeTel2Api) -> AsyncGenerator[Tel2Client]:
    session = fake_tel2.create_session()
    yield Tel2Client(session, "ha-login", "hunter2")
    await session.close()


@pytest.fixture
def coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, client: Tel2Client
) -> Tel2AccountCoordinator:
    return Tel2AccountCoordinator(hass, entry, client)


async def first_refresh(
    hass: HomeAssistant, entry: MockConfigEntry, coordinator: Tel2AccountCoordinator
) -> None:
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    await coordinator.async_config_entry_first_refresh()
    entry.mock_state(hass, ConfigEntryState.LOADED)


def add_number_device(
    hass: HomeAssistant, entry: MockConfigEntry, canonical: str
) -> dr.DeviceEntry:
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, canonical)}
    )


def number_device(
    hass: HomeAssistant, entry: MockConfigEntry, identifier: str
) -> dr.DeviceEntry | None:
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, identifier), entry.entry_id
    )


async def test_coordinator_settings(
    entry: MockConfigEntry, coordinator: Tel2AccountCoordinator
) -> None:
    assert coordinator.update_interval == ACCOUNT_SCAN_INTERVAL
    assert coordinator.config_entry is entry
    assert coordinator.always_update is False


async def test_first_refresh_builds_account_state(
    hass: HomeAssistant, entry: MockConfigEntry, coordinator: Tel2AccountCoordinator
) -> None:
    await first_refresh(hass, entry, coordinator)

    assert coordinator.data == AccountState(
        details=CustomerDetails(
            account_code=ACCOUNT_CODE,
            customer_name="Example Customer",
            company_name="Example Ltd",
            status="Active",
        ),
        balance=AccountBalance(
            balance=Decimal("12.34"), available_balance=Decimal("10.00")
        ),
        plan=PlanSummary(
            alias="Example Plan", description="Example business voice plan"
        ),
        numbers={
            LINE_1: Tel2Number(LINE_1, "01632960001", "Line 1", "201"),
            LINE_2: Tel2Number(LINE_2, "01632960002", "Line 2", "202"),
        },
        bundles={LINE_1: BundleUsage(used_minutes=37, total_minutes=1000)},
    )


async def test_numbers_without_bundle_are_absent_from_bundles(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    fake_tel2.respond("Get-Line-Bundle-Usage", dict(NO_DATA))

    await first_refresh(hass, entry, coordinator)

    assert set(coordinator.data.numbers) == {LINE_1, LINE_2}
    assert coordinator.data.bundles == {}


async def test_first_refresh_creates_account_device(
    hass: HomeAssistant, entry: MockConfigEntry, coordinator: Tel2AccountCoordinator
) -> None:
    await first_refresh(hass, entry, coordinator)

    device = number_device(hass, entry, ACCOUNT_CODE)
    assert device is not None
    assert coordinator.account_device_id == device.id
    assert device.manufacturer == "Tel2"
    assert device.name == "Example Ltd"
    assert device.model == "Example business voice plan"
    assert device.entry_type is dr.DeviceEntryType.SERVICE


async def test_account_device_without_plan_has_no_model(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    fake_tel2.respond("Get-Current-Plan-Summary", dict(NO_DATA))

    await first_refresh(hass, entry, coordinator)

    device = number_device(hass, entry, ACCOUNT_CODE)
    assert device is not None
    assert device.model is None
    assert coordinator.data.plan is None


async def test_removed_number_device_is_removed_on_refresh(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    await first_refresh(hass, entry, coordinator)
    add_number_device(hass, entry, LINE_1)
    add_number_device(hass, entry, LINE_2)
    fake_tel2.respond("Get-Numbers", "get_numbers_number_removed.json")

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert set(coordinator.data.numbers) == {LINE_1}
    assert number_device(hass, entry, LINE_2) is None
    assert number_device(hass, entry, LINE_1) is not None
    assert number_device(hass, entry, ACCOUNT_CODE) is not None


async def test_number_removed_while_stopped_is_removed_on_first_refresh(
    hass: HomeAssistant, entry: MockConfigEntry, coordinator: Tel2AccountCoordinator
) -> None:
    add_number_device(hass, entry, LINE_1)
    add_number_device(hass, entry, "441632960009")

    await first_refresh(hass, entry, coordinator)

    assert number_device(hass, entry, "441632960009") is None
    assert number_device(hass, entry, LINE_1) is not None
    assert number_device(hass, entry, ACCOUNT_CODE) is not None


async def test_failed_refresh_keeps_devices(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    await first_refresh(hass, entry, coordinator)
    add_number_device(hass, entry, LINE_2)
    fake_tel2.respond("Get-Numbers", "error_generic.json")

    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert number_device(hass, entry, LINE_2) is not None


async def test_empty_numbers_keep_existing_number_devices(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await first_refresh(hass, entry, coordinator)
    add_number_device(hass, entry, LINE_1)
    add_number_device(hass, entry, LINE_2)
    fake_tel2.respond("Get-Numbers", dict(NO_DATA))

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data.numbers == {}
    assert number_device(hass, entry, LINE_1) is not None
    assert number_device(hass, entry, LINE_2) is not None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "1632" not in warnings[0].getMessage()


async def test_empty_numbers_on_first_refresh_keep_number_devices(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    add_number_device(hass, entry, LINE_1)
    fake_tel2.respond("Get-Numbers", dict(NO_DATA))

    await first_refresh(hass, entry, coordinator)

    assert number_device(hass, entry, LINE_1) is not None


async def test_numbers_returning_after_empty_refresh_remove_only_gone_ones(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    await first_refresh(hass, entry, coordinator)
    add_number_device(hass, entry, LINE_1)
    add_number_device(hass, entry, LINE_2)
    fake_tel2.respond("Get-Numbers", dict(NO_DATA), "get_numbers_number_removed.json")

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    assert number_device(hass, entry, LINE_1) is not None
    assert number_device(hass, entry, LINE_2) is None


async def test_auth_failure_on_first_refresh_raises_auth_failed(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    fake_tel2.respond("Get-Token", "error_wrong_password.json")

    with pytest.raises(ConfigEntryAuthFailed):
        await first_refresh(hass, entry, coordinator)


async def test_auth_failure_during_refresh_starts_reauth(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    await first_refresh(hass, entry, coordinator)
    fake_tel2.respond("Get-Customer-Details", "error_wrong_password.json")

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, ConfigEntryAuthFailed)
    flows = list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
    assert len(flows) == 1
    assert flows[0]["step_id"] == "reauth_confirm"


async def test_auth_error_wins_over_simultaneous_connection_error(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await first_refresh(hass, entry, coordinator)

    async def unreachable() -> None:
        raise Tel2ConnectionError("down")

    async def refused() -> None:
        raise Tel2AuthError("Invalid login details.")

    monkeypatch.setattr(coordinator.client, "async_get_customer_details", unreachable)
    monkeypatch.setattr(coordinator.client, "async_get_account_balance", refused)

    await coordinator.async_refresh()

    err = coordinator.last_exception
    assert isinstance(err, ConfigEntryAuthFailed)
    assert isinstance(err.__cause__, ExceptionGroup)
    assert len(err.__cause__.exceptions) == 2


async def test_auth_failure_without_message_has_fallback_text(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await first_refresh(hass, entry, coordinator)

    async def refused() -> None:
        raise Tel2AuthError("")

    monkeypatch.setattr(coordinator.client, "async_get_account_balance", refused)

    await coordinator.async_refresh()

    err = coordinator.last_exception
    assert isinstance(err, ConfigEntryAuthFailed)
    assert str(err) == "Tel2 rejected the stored login"


@pytest.mark.parametrize(
    "response", [aiohttp.ClientError(), TimeoutError()], ids=["client", "timeout"]
)
async def test_connection_error_is_update_failed(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
    response: BaseException,
) -> None:
    await first_refresh(hass, entry, coordinator)
    fake_tel2.respond("Get-Account-Balance", response)

    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    err = coordinator.last_exception
    assert isinstance(err, UpdateFailed)
    assert err.translation_domain == DOMAIN
    assert err.translation_key == "update_cannot_connect"


async def test_tel2_error_is_update_failed_with_message(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    await first_refresh(hass, entry, coordinator)
    fake_tel2.respond("Get-Numbers", "error_generic.json")

    await coordinator.async_refresh()

    err = coordinator.last_exception
    assert isinstance(err, UpdateFailed)
    assert err.translation_domain == DOMAIN
    assert err.translation_key == "update_tel2_error"
    assert err.translation_placeholders == {
        "message": "An error occurred processing your request."
    }


async def test_bundle_error_fails_the_refresh(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    await first_refresh(hass, entry, coordinator)
    fake_tel2.respond("Get-Line-Bundle-Usage", aiohttp.ClientError())

    await coordinator.async_refresh()

    err = coordinator.last_exception
    assert isinstance(err, UpdateFailed)
    assert err.translation_key == "update_cannot_connect"


async def test_outage_logs_once_when_it_starts_and_once_when_it_ends(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await first_refresh(hass, entry, coordinator)
    fake_tel2.respond(
        "Get-Account-Balance",
        aiohttp.ClientError(),
        aiohttp.ClientError(),
        "get_account_balance.json",
    )
    caplog.set_level(logging.INFO)

    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert caplog.text.count(ERROR_LOG) == 1
    assert caplog.text.count(RECOVERED_LOG) == 1


async def test_bundle_calls_are_capped_at_four_in_flight(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = [f"016329600{index:02d}" for index in range(1, 11)]
    fake_tel2.respond("Get-Numbers", success([{"ServiceName": line} for line in lines]))
    in_flight = 0
    peak = 0
    client = coordinator.client
    original = client.async_get_line_bundle_usage

    async def counting(number: Tel2Number) -> BundleUsage | None:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0)
            return await original(number)
        finally:
            in_flight -= 1

    monkeypatch.setattr(client, "async_get_line_bundle_usage", counting)

    await first_refresh(hass, entry, coordinator)

    assert fake_tel2.call_count("Get-Line-Bundle-Usage") == len(lines)
    assert peak == 4
    assert set(coordinator.data.bundles) == {LINE_1}


async def test_unchanged_refresh_does_not_notify_listeners(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    coordinator: Tel2AccountCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    await first_refresh(hass, entry, coordinator)
    calls: list[None] = []
    unsubscribe = coordinator.async_add_listener(lambda: calls.append(None))

    await coordinator.async_refresh()
    assert calls == []

    fake_tel2.respond("Get-Numbers", "get_numbers_number_removed.json")
    await coordinator.async_refresh()
    assert len(calls) == 1
    unsubscribe()
