"""Static checks on the English translations and icons."""

import json
from pathlib import Path
import re
from typing import Any

import pytest

from custom_components.tel2.const import (
    CALL_EVENT_TYPES,
    CONF_CALL_EVENTS,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
)

PACKAGE_DIR = Path(__file__).parent.parent / "custom_components" / "tel2"
TRANSLATIONS = PACKAGE_DIR / "translations" / "en.json"
ICONS = PACKAGE_DIR / "icons.json"

EXCEPTIONS = (
    "set_failed",
    "forward_destination_required",
    "invalid_destination",
    "forward_destination_failed",
    "secret_pref",
    "not_a_line",
    "tel2_rejected",
    "resync_failed",
    "reboot_failed",
    "update_cannot_connect",
    "update_tel2_error",
)
CONFIG_ERRORS = ("invalid_auth", "cannot_connect", "insufficient_access", "unknown")
CONFIG_ABORTS = ("already_configured", "wrong_account", "reauth_successful")
ENTITIES = {
    "sensor": (
        "balance",
        "available_balance",
        "plan",
        "account_status",
        "bundle_used",
        "bundle_total",
    ),
    "binary_sensor": ("api_connected", "registered"),
    "switch": (
        "do_not_disturb",
        "call_forward_always",
        "anonymous_reject",
        "call_recording",
    ),
    "text": ("forward_destination",),
    "button": ("resync_line", "reboot_phone"),
    "event": ("call",),
}
ENTITY_PARAMS = [(p, k) for p, keys in ENTITIES.items() for k in keys]
EVENT_ATTRIBUTES = ("from", "to", "direction", "call_id", "start_time", "duration")
SERVICE_FIELDS = ("device_id", "pref", "value")
DASHES = re.compile(r"[\u2013\u2014]")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def strings() -> Any:
    return _load(TRANSLATIONS)


@pytest.fixture(scope="module")
def icons() -> Any:
    return _load(ICONS)


def _all_strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for value in node.values() for s in _all_strings(value)]
    return []


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", text))


def test_files_contain_no_long_dashes() -> None:
    for path in (TRANSLATIONS, ICONS):
        assert not DASHES.search(path.read_text(encoding="utf-8")), path.name


def test_all_strings_are_non_empty(strings: Any) -> None:
    assert all(text.strip() for text in _all_strings(strings))


def test_user_step(strings: Any) -> None:
    step = strings["config"]["step"]["user"]
    assert step["title"]
    assert step["description"]
    for field in (CONF_LOGIN, CONF_PASSWORD):
        assert step["data"][field]
        assert step["data_description"][field]
    assert "Full" in step["data_description"][CONF_LOGIN]


def test_reauth_step(strings: Any) -> None:
    step = strings["config"]["step"]["reauth_confirm"]
    assert step["title"]
    assert step["description"]
    for field in (CONF_LOGIN, CONF_PASSWORD):
        assert step["data"][field]


@pytest.mark.parametrize("key", CONFIG_ERRORS)
def test_config_errors(strings: Any, key: str) -> None:
    assert strings["config"]["error"][key]


def test_insufficient_access_placeholders(strings: Any) -> None:
    text = strings["config"]["error"]["insufficient_access"]
    assert _placeholders(text) == {"contexts", "level"}


@pytest.mark.parametrize("key", CONFIG_ABORTS)
def test_config_aborts(strings: Any, key: str) -> None:
    assert strings["config"]["abort"][key]


def test_options_step(strings: Any) -> None:
    step = strings["options"]["step"]["init"]
    for field in (CONF_SCAN_INTERVAL, CONF_CALL_EVENTS):
        assert step["data"][field]
        assert step["data_description"][field]


@pytest.mark.parametrize(("platform", "key"), ENTITY_PARAMS)
def test_entity_names(strings: Any, platform: str, key: str) -> None:
    assert strings["entity"][platform][key]["name"]


def test_no_unexpected_entities(strings: Any) -> None:
    assert {p: set(keys) for p, keys in strings["entity"].items()} == {
        p: set(keys) for p, keys in ENTITIES.items()
    }


@pytest.mark.parametrize("event_type", CALL_EVENT_TYPES)
def test_call_event_types(strings: Any, event_type: str) -> None:
    states = strings["entity"]["event"]["call"]["state_attributes"]["event_type"][
        "state"
    ]
    assert states[event_type]


def test_call_event_types_match_const(strings: Any) -> None:
    states = strings["entity"]["event"]["call"]["state_attributes"]["event_type"][
        "state"
    ]
    assert set(states) == set(CALL_EVENT_TYPES)


@pytest.mark.parametrize("attribute", EVENT_ATTRIBUTES)
def test_call_event_attributes(strings: Any, attribute: str) -> None:
    attrs = strings["entity"]["event"]["call"]["state_attributes"]
    assert attrs[attribute]["name"]


@pytest.mark.parametrize("key", EXCEPTIONS)
def test_exceptions(strings: Any, key: str) -> None:
    assert strings["exceptions"][key]["message"]


def test_no_unexpected_exceptions(strings: Any) -> None:
    assert set(strings["exceptions"]) == set(EXCEPTIONS)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("tel2_rejected", {"message"}),
        ("update_tel2_error", {"message"}),
        *[
            (key, set())
            for key in EXCEPTIONS
            if key not in {"tel2_rejected", "update_tel2_error"}
        ],
    ],
)
def test_exception_placeholders(strings: Any, key: str, expected: set[str]) -> None:
    assert _placeholders(strings["exceptions"][key]["message"]) == expected


def test_repair_issue(strings: Any) -> None:
    issue = strings["issues"]["call_events_no_public_url"]
    assert issue["title"]
    assert "Home Assistant Cloud" in issue["description"]
    assert "external URL" in issue["description"]


def test_service(strings: Any) -> None:
    service = strings["services"]["set_line_setting"]
    assert service["name"]
    assert service["description"]
    for field in SERVICE_FIELDS:
        assert service["fields"][field]["name"]
        assert service["fields"][field]["description"]


@pytest.mark.parametrize(("platform", "key"), ENTITY_PARAMS)
def test_entity_icons(icons: Any, platform: str, key: str) -> None:
    assert icons["entity"][platform][key]["default"].startswith("mdi:")


@pytest.mark.parametrize("key", ENTITIES["switch"])
def test_switch_state_icons(icons: Any, key: str) -> None:
    icon = icons["entity"]["switch"][key]
    assert icon["state"]["off"].startswith("mdi:")
    assert icon["state"]["off"] != icon["default"]


def test_state_icons_differ_from_default(icons: Any) -> None:
    for platform in icons["entity"].values():
        for icon in platform.values():
            assert icon["default"] not in icon.get("state", {}).values()


def test_service_icon(icons: Any) -> None:
    assert icons["services"]["set_line_setting"]["service"].startswith("mdi:")


def test_icons_only_reference_translated_entities(strings: Any, icons: Any) -> None:
    for platform, keys in icons["entity"].items():
        assert set(keys) <= set(strings["entity"][platform])
