"""Tests for the Tel2 lines coordinator."""

from collections.abc import AsyncGenerator, Callable
from datetime import timedelta
from typing import Any

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.tel2.api import Tel2Client
from custom_components.tel2.const import (
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    PERSONAL_PREF_FIELDS,
    PREF_ANONYMOUS_REJECT,
    PREF_DO_NOT_DISTURB,
    PREF_FORWARD_ALWAYS,
    PREF_VALUE_OFF,
    PREF_VALUE_ON,
    SECRET_PREFS,
)
from custom_components.tel2.coordinator import (
    Tel2AccountCoordinator,
    Tel2LinesCoordinator,
)
from custom_components.tel2.models import LinesState
from tests.fake_tel2 import NO_DATA, FakeTel2Api
from tests.fixture_loader import load_fixture
from tests.helpers import async_setup_integration

ACCOUNT_CODE = "00000000"
LINE_1 = "441632960001"
LINE_2 = "441632960002"
LINE_3 = "441632960003"


def success(data: Any) -> dict[str, Any]:
    return {
        "Result": "1",
        "Status": "Success",
        "Message": "Method Executed Successfully.",
        "Data": data,
    }


@pytest.fixture
def options() -> dict[str, Any]:
    return {}


@pytest.fixture
def entry(
    hass: HomeAssistant,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    options: dict[str, Any],
) -> MockConfigEntry:
    entry = mock_config_entry_factory(
        unique_id=ACCOUNT_CODE,
        data={CONF_LOGIN: "ha-login", CONF_PASSWORD: "hunter2"},
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def client(fake_tel2: FakeTel2Api) -> AsyncGenerator[Tel2Client]:
    session = fake_tel2.create_session()
    yield Tel2Client(session, "ha-login", "hunter2")
    await session.close()


@pytest.fixture
def account(
    hass: HomeAssistant, entry: MockConfigEntry, client: Tel2Client
) -> Tel2AccountCoordinator:
    return Tel2AccountCoordinator(hass, entry, client)


@pytest.fixture
async def lines(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    client: Tel2Client,
    account: Tel2AccountCoordinator,
) -> AsyncGenerator[Tel2LinesCoordinator]:
    coordinator = Tel2LinesCoordinator(hass, entry, client, account)
    yield coordinator
    await coordinator.async_shutdown()


async def first_refresh(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    account: Tel2AccountCoordinator,
    lines: Tel2LinesCoordinator,
) -> None:
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    await account.async_config_entry_first_refresh()
    await lines.async_config_entry_first_refresh()
    entry.mock_state(hass, ConfigEntryState.LOADED)


@pytest.fixture
async def loaded(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    account: Tel2AccountCoordinator,
    lines: Tel2LinesCoordinator,
) -> Tel2LinesCoordinator:
    await first_refresh(hass, entry, account, lines)
    return lines


def pref_rows(*mailboxes: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = load_fixture("get_all_pref_summary_number_added.json")[
        "Data"
    ]
    return [row for row in rows if row["pref_uid"] in mailboxes]


async def test_coordinator_settings(
    entry: MockConfigEntry,
    account: Tel2AccountCoordinator,
    lines: Tel2LinesCoordinator,
) -> None:
    assert lines.update_interval == timedelta(minutes=5)
    assert lines.config_entry is entry
    assert lines.account is account
    assert lines.always_update is False


@pytest.mark.parametrize("options", [{CONF_SCAN_INTERVAL: 12}])
async def test_interval_follows_option(lines: Tel2LinesCoordinator) -> None:
    assert lines.update_interval == timedelta(minutes=12)


async def test_refresh_makes_two_calls(
    loaded: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    assert fake_tel2.call_count("Get-Subscriber-Status") == 1
    assert fake_tel2.call_count("Get-All-Pref-Summary") == 1


async def test_registered_line_is_marked_registered(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    account: Tel2AccountCoordinator,
    lines: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    fake_tel2.respond("Get-Subscriber-Status", "get_subscriber_status_registered.json")

    await first_refresh(hass, entry, account, lines)

    assert lines.data.lines[LINE_1].registered is True
    assert lines.data.lines[LINE_2].registered is False


async def test_no_data_registration_leaves_every_line_unregistered(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    account: Tel2AccountCoordinator,
    lines: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    fake_tel2.respond("Get-Subscriber-Status", dict(NO_DATA))

    await first_refresh(hass, entry, account, lines)
    await lines.async_refresh()

    assert lines.last_update_success
    assert lines.last_exception is None
    assert set(lines.data.lines) == {LINE_1, LINE_2}
    assert not any(line.registered for line in lines.data.lines.values())


async def test_prefs_are_mapped_per_line_without_secrets(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    account: Tel2AccountCoordinator,
    lines: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", "get_all_pref_summary_all_on.json")

    await first_refresh(hass, entry, account, lines)

    for line in lines.data.lines.values():
        assert line.prefs[PREF_DO_NOT_DISTURB] == PREF_VALUE_ON
        assert line.prefs[PREF_FORWARD_ALWAYS] == PREF_VALUE_ON
        assert line.prefs[PREF_ANONYMOUS_REJECT] == PREF_VALUE_ON
        assert not set(line.prefs) & (SECRET_PREFS | PERSONAL_PREF_FIELDS)
        assert not any("fake-" in value for value in line.prefs.values())


async def test_numbers_not_on_the_account_are_ignored(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    account: Tel2AccountCoordinator,
    lines: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", "get_all_pref_summary_number_added.json")
    fake_tel2.respond(
        "Get-Subscriber-Status", success({"01632960001": "1", "01632960003": "1"})
    )

    await first_refresh(hass, entry, account, lines)

    assert set(lines.data.lines) == {LINE_1, LINE_2}
    assert LINE_3 not in lines.data.lines


async def test_every_account_number_gets_a_line_state(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    account: Tel2AccountCoordinator,
    lines: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", success(pref_rows("01632960001")))

    await first_refresh(hass, entry, account, lines)

    assert set(lines.data.lines) == {LINE_1, LINE_2}
    assert lines.data.lines[LINE_1].prefs[PREF_DO_NOT_DISTURB] == PREF_VALUE_OFF
    assert lines.data.lines[LINE_2].prefs == {}
    assert lines.data.lines[LINE_2].registered is False


async def test_lines_follow_numbers_added_to_the_account(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    account: Tel2AccountCoordinator,
    lines: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", "get_all_pref_summary_number_added.json")
    await first_refresh(hass, entry, account, lines)
    fake_tel2.respond("Get-Numbers", "get_numbers_number_added.json")

    await account.async_refresh()
    await lines.async_refresh()

    assert set(lines.data.lines) == {LINE_1, LINE_2, LINE_3}


async def test_account_update_before_lines_have_loaded_does_not_refresh_lines(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    account: Tel2AccountCoordinator,
    lines: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    await account.async_config_entry_first_refresh()
    fake_tel2.respond("Get-All-Pref-Summary", "error_generic.json")
    with pytest.raises(ConfigEntryNotReady):
        await lines.async_config_entry_first_refresh()
    assert lines.data is None
    pref_calls = fake_tel2.call_count("Get-All-Pref-Summary")
    fake_tel2.respond("Get-Numbers", "get_numbers_number_added.json")

    await account.async_refresh()
    await hass.async_block_till_done()

    assert LINE_3 in account.data.numbers
    assert lines.data is None
    assert fake_tel2.call_count("Get-All-Pref-Summary") == pref_calls


async def test_without_account_data_there_are_no_lines(
    lines: Tel2LinesCoordinator, fake_tel2: FakeTel2Api
) -> None:
    await lines.async_refresh()

    assert lines.last_update_success
    assert lines.data == LinesState(lines={})
    assert fake_tel2.call_count("Get-All-Pref-Summary") == 0


async def test_optimistic_update_reaches_listeners_before_the_refresh(
    loaded: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    before = loaded.data
    seen: list[tuple[str | None, int]] = []

    def listener() -> None:
        seen.append(
            (
                loaded.data.lines[LINE_1].prefs.get(PREF_DO_NOT_DISTURB),
                fake_tel2.call_count("Get-All-Pref-Summary"),
            )
        )

    unsubscribe = loaded.async_add_listener(listener)
    fake_tel2.respond("Get-All-Pref-Summary", "get_all_pref_summary_all_on.json")

    await loaded.async_apply_optimistic(LINE_1, PREF_DO_NOT_DISTURB, PREF_VALUE_ON)

    assert seen[0] == (PREF_VALUE_ON, 1)
    assert loaded.data.lines[LINE_2].prefs[PREF_DO_NOT_DISTURB] == PREF_VALUE_ON
    assert fake_tel2.call_count("Get-All-Pref-Summary") == 2
    assert before.lines[LINE_1].prefs[PREF_DO_NOT_DISTURB] == PREF_VALUE_OFF
    unsubscribe()


async def test_optimistic_update_changes_only_that_pref(
    loaded: Tel2LinesCoordinator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = loaded.data

    async def no_refresh() -> None:
        return None

    monkeypatch.setattr(loaded, "async_request_refresh", no_refresh)

    await loaded.async_apply_optimistic(LINE_1, PREF_ANONYMOUS_REJECT, PREF_VALUE_ON)

    after = loaded.data
    assert after.lines[LINE_1].prefs == {
        **before.lines[LINE_1].prefs,
        PREF_ANONYMOUS_REJECT: PREF_VALUE_ON,
    }
    assert after.lines[LINE_1].registered == before.lines[LINE_1].registered
    assert after.lines[LINE_2] == before.lines[LINE_2]
    assert before.lines[LINE_1].prefs[PREF_ANONYMOUS_REJECT] == PREF_VALUE_OFF


async def test_upstream_pref_change_shows_after_refresh(
    loaded: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    assert loaded.data.lines[LINE_1].prefs[PREF_DO_NOT_DISTURB] == PREF_VALUE_OFF
    fake_tel2.respond("Get-All-Pref-Summary", "get_all_pref_summary_all_on.json")

    await loaded.async_refresh()

    assert loaded.data.lines[LINE_1].prefs[PREF_DO_NOT_DISTURB] == PREF_VALUE_ON
    assert loaded.data.lines[LINE_1].prefs[PREF_FORWARD_ALWAYS] == PREF_VALUE_ON


async def test_auth_failure_on_first_refresh_raises_auth_failed(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    account: Tel2AccountCoordinator,
    lines: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    fake_tel2.respond("Get-Subscriber-Status", "error_wrong_password.json")

    with pytest.raises(ConfigEntryAuthFailed):
        await first_refresh(hass, entry, account, lines)


async def test_auth_failure_during_refresh_is_auth_failed(
    loaded: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", "error_wrong_password.json")

    await loaded.async_refresh()

    assert not loaded.last_update_success
    assert isinstance(loaded.last_exception, ConfigEntryAuthFailed)


async def test_unauthorised_function_is_update_failed_not_reauth(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    loaded: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    fake_tel2.respond(
        "Get-All-Pref-Summary",
        {
            "Result": "-1",
            "Status": "Error",
            "Message": "Unauthorised: function disabled for this account.",
            "Data": [],
        },
    )
    tokens = fake_tel2.call_count("Get-Token")

    await loaded.async_refresh()
    await hass.async_block_till_done()

    err = loaded.last_exception
    assert isinstance(err, UpdateFailed)
    assert err.translation_key == "update_tel2_error"
    assert not list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
    assert fake_tel2.call_count("Get-Token") == tokens


@pytest.mark.parametrize(
    "response", [aiohttp.ClientError(), TimeoutError()], ids=["client", "timeout"]
)
async def test_connection_error_is_update_failed(
    loaded: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
    response: BaseException,
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", response)

    await loaded.async_refresh()

    assert not loaded.last_update_success
    err = loaded.last_exception
    assert isinstance(err, UpdateFailed)
    assert err.translation_domain == DOMAIN
    assert err.translation_key == "update_cannot_connect"


async def test_tel2_error_is_update_failed_with_message(
    loaded: Tel2LinesCoordinator,
    fake_tel2: FakeTel2Api,
) -> None:
    fake_tel2.respond("Get-Subscriber-Status", "error_generic.json")

    await loaded.async_refresh()

    err = loaded.last_exception
    assert isinstance(err, UpdateFailed)
    assert err.translation_key == "update_tel2_error"


async def test_new_account_number_refreshes_lines_once(
    hass: HomeAssistant, fake_tel2: FakeTel2Api, freezer: FrozenDateTimeFactory
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    account = entry.runtime_data.account
    lines = entry.runtime_data.lines
    before = fake_tel2.call_count("Get-All-Pref-Summary")
    fake_tel2.respond("Get-Numbers", "get_numbers_number_added.json")
    fake_tel2.respond("Get-All-Pref-Summary", "get_all_pref_summary_number_added.json")

    await account.async_refresh()
    await hass.async_block_till_done()

    assert fake_tel2.call_count("Get-All-Pref-Summary") == before + 1
    assert LINE_3 in lines.data.lines

    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await account.async_refresh()
    account.async_update_listeners()
    await hass.async_block_till_done()

    assert fake_tel2.call_count("Get-All-Pref-Summary") == before + 1
