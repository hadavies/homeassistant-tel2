"""Tests for the Tel2 diagnostics."""

import copy
from datetime import UTC, datetime
import json
from typing import Any, Final
from unittest.mock import create_autospec

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion

from custom_components.tel2.const import (
    CONF_CALL_EVENTS,
    CONF_CLOUDHOOK_URL,
    CONF_TEL2_WEBHOOK_ID,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
)
from custom_components.tel2.diagnostics import _alias_pattern, _sanitise_tel2
from custom_components.tel2.webhook import CallEventStatus, Tel2CallEventManager
from tests.fake_tel2 import FakeTel2Api
from tests.fixture_loader import load_fixture
from tests.helpers import (
    DEFAULT_ENTRY_DATA,
    FIXTURE_TOKEN,
    LINE_1,
    STORED_TOKEN,
    async_setup_integration,
)

ALL_ON: Final = "get_all_pref_summary_all_on.json"
WEBHOOK_ID: Final = "ha-webhook-id-abc123"
WEBHOOK_SECRET: Final = "deadbeefcafe0123"
TEL2_WEBHOOK_ID: Final = "tel2-hook-987"
CLOUDHOOK_URL: Final = "https://hooks.nabu.casa/cloudhook-secret-path"
CALL_EVENT_ENTRY_DATA: Final = {
    **DEFAULT_ENTRY_DATA,
    CONF_WEBHOOK_ID: WEBHOOK_ID,
    CONF_WEBHOOK_SECRET: WEBHOOK_SECRET,
    CONF_TEL2_WEBHOOK_ID: TEL2_WEBHOOK_ID,
    CONF_CLOUDHOOK_URL: CLOUDHOOK_URL,
}
LEAKS: Final = (
    DEFAULT_ENTRY_DATA["login"],
    DEFAULT_ENTRY_DATA["password"],
    STORED_TOKEN,
    FIXTURE_TOKEN,
    WEBHOOK_ID,
    WEBHOOK_SECRET,
    TEL2_WEBHOOK_ID,
    CLOUDHOOK_URL,
    "Example Customer",
    "Example Ltd",
    "01632",
    "07700",
    "441632",
    "@",
    "http",
)


@pytest.fixture(autouse=True)
def no_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("custom_components.tel2.PLATFORMS", [Platform.SENSOR])


async def setup_with_call_events(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> MockConfigEntry:
    fake_tel2.respond("Get-All-Pref-Summary", ALL_ON)
    entry = await async_setup_integration(
        hass,
        fake_tel2,
        options={CONF_CALL_EVENTS: True},
        entry_data=CALL_EVENT_ENTRY_DATA,
    )
    manager = create_autospec(Tel2CallEventManager, instance=True)
    manager.status = CallEventStatus(
        url_source="cloud",
        registered_on_tel2=True,
        last_event_at=datetime(2026, 10, 17, 9, 30, tzinfo=UTC),
    )
    entry.runtime_data.call_events = manager
    return entry


def assert_no_leaks(diagnostics: Any) -> None:
    serialised = json.dumps(diagnostics)
    for leak in LEAKS:
        assert leak not in serialised


async def test_diagnostics_snapshot_with_call_events(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    fake_tel2: FakeTel2Api,
    snapshot: SnapshotAssertion,
) -> None:
    entry = await setup_with_call_events(hass, fake_tel2)

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics == snapshot
    assert_no_leaks(diagnostics)


async def test_diagnostics_aliases_own_numbers_and_redacts_external(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    fake_tel2: FakeTel2Api,
) -> None:
    entry = await setup_with_call_events(hass, fake_tel2)

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    numbers = diagnostics["account"]["numbers"]
    assert list(numbers) == ["line_1", "line_2"]
    assert numbers["line_1"]["canonical"] == "line_1"
    assert numbers["line_2"]["tel2_id"] == "line_2"
    assert numbers["line_1"]["name"] == "**REDACTED**"
    assert numbers["line_1"]["extension"] == "201"
    lines = diagnostics["lines"]["lines"]
    assert list(lines) == ["line_1", "line_2"]
    assert lines["line_1"]["prefs"] == {
        "do_not_disturb_status": "1",
        "callforward_always_status": "1",
        "anoncall_reject": "1",
        "callrecording_disabled": "0",
        "callrecording_type": "1",
        "profile_recordingenabled": "1",
        "callforward_always_number": "**REDACTED**",
        "other_pref_count": len(entry.runtime_data.lines.data.lines[LINE_1].prefs) - 7,
    }
    assert diagnostics["entry"]["data"][CONF_CLOUDHOOK_URL] == "**REDACTED**"
    assert diagnostics["entry"]["data"][CONF_WEBHOOK_SECRET] == "**REDACTED**"
    assert diagnostics["call_events"] == {
        "enabled": True,
        "running": True,
        "url_source": "cloud",
        "registered_on_tel2": True,
        "last_event_at": "2026-10-17T09:30:00+00:00",
    }
    assert diagnostics["coordinators"] == {
        "account": {"last_update_success": True},
        "lines": {"last_update_success": True},
    }


async def test_diagnostics_without_call_event_manager(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    fake_tel2: FakeTel2Api,
) -> None:
    entry = await async_setup_integration(
        hass, fake_tel2, options={CONF_CALL_EVENTS: False}
    )
    entry.runtime_data.call_events = None

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["call_events"] == {"enabled": False, "running": False}
    assert diagnostics["account"]["balance"] == {
        "balance": "12.34",
        "available_balance": "10.00",
    }
    assert_no_leaks(diagnostics)


async def test_diagnostics_redacts_person_named_line_and_free_text(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    fake_tel2: FakeTel2Api,
) -> None:
    numbers = copy.deepcopy(load_fixture("get_numbers.json"))
    numbers["Data"][0]["Alias"] = "Alice Smith"
    fake_tel2.respond("Get-Numbers", numbers)
    plan = copy.deepcopy(load_fixture("get_current_plan_summary.json"))
    plan["Data"][0]["Alias"] = "Ring 07700 900123, 07700-900124 or 01632 960001"
    plan["Data"][0]["BillingDescription"] = "Questions to boss@example.org"
    fake_tel2.respond("Get-Current-Plan-Summary", plan)
    entry = await async_setup_integration(hass, fake_tel2)

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["account"]["numbers"]["line_1"]["name"] == "**REDACTED**"
    assert diagnostics["account"]["plan"] == {
        "alias": "**REDACTED**",
        "description": "**REDACTED**",
    }
    assert "Alice" not in json.dumps(diagnostics)
    assert_no_leaks(diagnostics)


@pytest.mark.parametrize(
    "value",
    [
        "07700900123,07700900124",
        "+44 7700 900123; +44 (0)7700 900124",
        "Divert to 0044 7700-900123 after 20s",
        "Notify alice@example.org",
        "tel:+447700900123",
        "sip:alice@pbx.example.org",
        "SIP:pbx",
        "see https://example.org/x",
        "+0 1234 5678",
        "07700\t900123",
        "07700\u00a0900123",
        "07700/900123",
        "07700_900_123",
        "07700,900,123",
    ],
)
def test_sanitise_redacts_contact_values(value: str) -> None:
    assert _sanitise_tel2({"free_text": value}, None) == {"free_text": "**REDACTED**"}


def test_sanitise_redacts_contact_details_in_keys() -> None:
    assert _sanitise_tel2({"07700900123": "x", "status": "1"}, None) == {
        "**REDACTED**": "x",
        "status": "1",
    }


@pytest.mark.parametrize(
    "key",
    ["status_url", "webhookUrl", "x_apikey_y", "ApiKey", "auth_header", "pager_email"],
)
def test_sanitise_redacts_secret_like_keys(key: str) -> None:
    assert _sanitise_tel2({"nested": {key: "harmless"}}, None) == {
        "nested": {key: "**REDACTED**"}
    }


def test_sanitise_keeps_voicemail_flags_and_plain_values() -> None:
    data = {
        "voicemail_disable": "0",
        "balance": "1234.56",
        "extension": "201",
        "alias": "Example Plan",
    }

    assert _sanitise_tel2(data, None) == data


def test_sanitise_aliases_own_number_formats() -> None:
    aliasing = _alias_pattern(["441632960001"], {"441632960001": "01632960001"})
    forms = [
        "441632960001",
        "+441632960001",
        "00441632960001",
        "01632960001",
        "01632 960001",
        "+44 1632-960-001",
        "+44 (0)1632 960001",
    ]

    assert _sanitise_tel2({form: form for form in forms}, aliasing) == {
        "line_1": "line_1"
    }
