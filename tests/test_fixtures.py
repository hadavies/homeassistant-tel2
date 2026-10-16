"""Tests for the Tel2 test fixtures and their loader."""

from collections.abc import Callable
import json
from pathlib import Path
import re
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tel2.const import (
    PERSONAL_PREF_FIELDS,
    PREF_ANONYMOUS_REJECT,
    PREF_DO_NOT_DISTURB,
    PREF_FORWARD_ALWAYS,
    PREF_FORWARD_ALWAYS_NUMBER,
    PREF_VALUE_OFF,
    PREF_VALUE_ON,
    SECRET_PREFS,
    TEL2_IGNORED_STATES,
    TEL2_STATE_MAP,
)
from tests import fixture_loader
from tests.fixture_loader import (
    FIXTURES_DIR,
    TEST_ENTRY_ID,
    load_captured,
    load_fixture,
)

SUCCESS_MESSAGE = "Method Executed Successfully."
NO_DATA_MESSAGE = "No Data Found."

REQUIRED_API_FIXTURES = {
    "get_token.json",
    "get_customer_details.json",
    "get_account_balance.json",
    "get_current_plan_summary.json",
    "get_numbers.json",
    "get_numbers_extension_only.json",
    "get_numbers_number_added.json",
    "get_numbers_number_removed.json",
    "get_line_names.json",
    "get_line_bundle_usage.json",
    "get_subscriber_status.json",
    "get_subscriber_status_registered.json",
    "get_all_pref_summary.json",
    "get_all_pref_summary_all_on.json",
    "get_all_pref_summary_number_added.json",
    "get_webhooks.json",
    "get_webhooks_populated.json",
    "error_wrong_password.json",
    "error_invalid_token.json",
    "error_access_refused_account.json",
    "error_access_refused_voice.json",
    "error_access_refused_api.json",
    "error_generic.json",
}
INT_WRITES = (
    "set_pref_value",
    "enable_recording",
    "disable_recording",
    "resync_line",
    "reboot_phone",
)
STRING_WRITES = ("add_webhook", "delete_webhook")
WRITE_FIXTURES = {
    f"{write}{suffix}.json"
    for write in INT_WRITES + STRING_WRITES
    for suffix in ("", "_rejected")
}
INBOUND_STATES = ("ringing", "answered", "ended", "missed", "blocked")
OUTBOUND_STATES = (
    "ringing",
    "answered",
    "ended",
    "busy",
    "noanswer",
    "rejected",
    "invalid",
    "notavailable",
    "blocked",
)
WEBHOOK_FIXTURES = {
    *(f"webhook_inbound_{state}.json" for state in INBOUND_STATES),
    *(f"webhook_outbound_{state}.json" for state in OUTBOUND_STATES),
    "webhook_aianalysis.json",
}
LINE_1 = "01632960001"
LINE_2 = "01632960002"

_DIGIT_RUN = re.compile(r"\d{9,}")
_TIMESTAMP = re.compile(
    r"\d{4}-?\d{2}-?\d{2}[T ]\d{2}:?\d{2}:?\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_DRAMA_NUMBER = re.compile(r"(?:0|44|0044)(?:1632960|7700900)\d{3}")
DOCUMENTED_FAKES = frozenset({"00000000"})


def _index() -> list[dict[str, Any]]:
    return json.loads((FIXTURES_DIR / "index.json").read_text(encoding="utf-8"))[
        "files"
    ]


def _entry(name: str) -> dict[str, Any]:
    return next(entry for entry in _index() if entry["file"] == name)


def _synthetic(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _envelopes(name: str) -> list[dict[str, Any]]:
    data = _synthetic(name)
    if _entry(name).get("keyed_by") == "line":
        return list(data.values())
    return [data]


def unexpected_numbers(text: str) -> list[str]:
    return [
        run
        for run in _DIGIT_RUN.findall(_TIMESTAMP.sub(" ", text))
        if run not in DOCUMENTED_FAKES and not _DRAMA_NUMBER.fullmatch(run)
    ]


def test_index_entries_exist_and_parse() -> None:
    for entry in _index():
        assert (FIXTURES_DIR / entry["file"]).is_file(), entry["file"]
        _synthetic(entry["file"])


def test_every_fixture_is_indexed_once() -> None:
    indexed = [entry["file"] for entry in _index()]
    on_disk = {path.name for path in FIXTURES_DIR.glob("*.json")} - {"index.json"}
    assert len(indexed) == len(set(indexed))
    assert set(indexed) == on_disk


def test_index_marks_every_fixture_synthetic_with_a_description() -> None:
    for entry in _index():
        assert entry["source"] == "synthetic"
        assert entry["description"].strip()
        assert "\n" not in entry["description"]


def test_every_required_fixture_is_indexed() -> None:
    indexed = {entry["file"] for entry in _index()}
    assert indexed >= REQUIRED_API_FIXTURES | WRITE_FIXTURES | WEBHOOK_FIXTURES


def test_api_fixtures_use_the_documented_envelope() -> None:
    for entry in _index():
        if "action" not in entry:
            continue
        for envelope in _envelopes(entry["file"]):
            assert set(envelope) == {"Result", "Status", "Message", "Data"}
            match envelope["Result"]:
                case "1":
                    assert envelope["Status"] == "Success"
                    assert envelope["Message"] == SUCCESS_MESSAGE
                case "0":
                    assert envelope["Status"] == "Success"
                    assert envelope["Message"] == NO_DATA_MESSAGE
                case "-1":
                    assert envelope["Status"] == "Error"
                    assert envelope["Message"]
                case other:
                    pytest.fail(f"{entry['file']}: unexpected Result {other!r}")


@pytest.mark.parametrize(
    ("write", "data"),
    [(write, 1) for write in INT_WRITES] + [(write, "1") for write in STRING_WRITES],
)
def test_write_fixtures_succeed_and_reject(write: str, data: object) -> None:
    success = _synthetic(f"{write}.json")
    assert success["Result"] == "1"
    assert success["Data"] == data
    assert type(success["Data"]) is type(data)
    assert _synthetic(f"{write}_rejected.json")["Result"] == "-1"


def test_access_refused_and_generic_errors_are_not_auth_errors() -> None:
    auth_messages = {
        _synthetic("error_wrong_password.json")["Message"],
        _synthetic("error_invalid_token.json")["Message"],
    }
    for name in (
        "error_access_refused_account.json",
        "error_access_refused_voice.json",
        "error_access_refused_api.json",
        "error_generic.json",
    ):
        envelope = _synthetic(name)
        assert envelope["Result"] == "-1"
        assert envelope["Message"] not in auth_messages


def test_bundle_usage_has_a_bundle_line_and_a_no_data_line() -> None:
    usage = _synthetic("get_line_bundle_usage.json")
    assert usage[LINE_1]["Result"] == "1"
    assert set(usage[LINE_1]["Data"]) == {"USED", "TOTAL"}
    assert usage[LINE_2]["Result"] == "0"


def _numbers(name: str) -> list[str]:
    return [row["ServiceName"] for row in _synthetic(name)["Data"]]


def test_number_variants() -> None:
    assert _numbers("get_numbers.json") == [LINE_1, LINE_2]
    assert _numbers("get_numbers_number_added.json") == [LINE_1, LINE_2, "01632960003"]
    assert _numbers("get_numbers_number_removed.json") == [LINE_1]
    extension_only = _synthetic("get_numbers_extension_only.json")["Data"]
    assert [row for row in extension_only if not row["ServiceName"]] == [
        row for row in extension_only if row["ExtensionNumber"] == "203"
    ]
    assert all(row["Parameter_1"] for row in _synthetic("get_numbers.json")["Data"])


def test_registered_status_has_one_line_online() -> None:
    assert _synthetic("get_subscriber_status_registered.json")["Data"] == {LINE_1: "1"}
    assert _synthetic("get_subscriber_status.json")["Result"] == "0"


@pytest.mark.parametrize(
    "name",
    [
        "get_all_pref_summary.json",
        "get_all_pref_summary_all_on.json",
        "get_all_pref_summary_number_added.json",
    ],
)
def test_pref_rows_carry_every_secret_and_personal_field(name: str) -> None:
    rows = _synthetic(name)["Data"]
    for row in rows:
        assert set(row) >= SECRET_PREFS
        assert set(row) >= PERSONAL_PREF_FIELDS
        assert all(row[key] for key in SECRET_PREFS | PERSONAL_PREF_FIELDS)
        assert _DRAMA_NUMBER.fullmatch(row["lastcld"])
        assert _DRAMA_NUMBER.fullmatch(row["lastclr"])


def test_pref_variants_switch_states() -> None:
    switches = (PREF_DO_NOT_DISTURB, PREF_FORWARD_ALWAYS, PREF_ANONYMOUS_REJECT)
    off_rows = _synthetic("get_all_pref_summary.json")["Data"]
    on_rows = _synthetic("get_all_pref_summary_all_on.json")["Data"]
    assert [row["pref_uid"] for row in off_rows] == [LINE_1, LINE_2]
    assert [row["pref_uid"] for row in on_rows] == [LINE_1, LINE_2]
    for row in off_rows:
        assert {row[pref] for pref in switches} == {PREF_VALUE_OFF}
        assert row["profile_recordingenabled"] == PREF_VALUE_OFF
        assert row[PREF_FORWARD_ALWAYS_NUMBER] is None
    for row in on_rows:
        assert {row[pref] for pref in switches} == {PREF_VALUE_ON}
        assert row["profile_recordingenabled"] == PREF_VALUE_ON
        assert row["callrecording_disabled"] == PREF_VALUE_OFF
        assert row[PREF_FORWARD_ALWAYS_NUMBER].startswith("07700900")


def test_populated_webhooks_include_the_home_assistant_registration() -> None:
    webhooks = _synthetic("get_webhooks_populated.json")["Data"]
    assert len(webhooks) == 2
    assert all(hook["url"].startswith("https://example.invalid/") for hook in webhooks)
    ours = [
        hook
        for hook in webhooks
        if hook["name"] == f"homeassistant-tel2-{TEST_ENTRY_ID}"
    ]
    assert len(ours) == 1
    assert ours[0]["secretheader"] == "X-Tel2-Signature"
    assert TEST_ENTRY_ID in _entry("get_webhooks_populated.json")["description"]
    assert _synthetic("get_webhooks.json")["Result"] == "0"


def test_mock_config_entry_defaults_to_the_fixture_entry_id(
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    assert mock_config_entry_factory().entry_id == TEST_ENTRY_ID


def test_webhook_payloads_cover_every_state() -> None:
    states = {_synthetic(name)["state"] for name in WEBHOOK_FIXTURES}
    assert states == set(TEL2_STATE_MAP) | TEL2_IGNORED_STATES


@pytest.mark.parametrize("name", sorted(WEBHOOK_FIXTURES))
def test_webhook_payload_shape(name: str) -> None:
    payload = _synthetic(name)
    state = payload["state"]
    assert {"state", "id", "originid", "from", "to", "type"} <= set(payload)
    assert payload["type"] == ("dialed" if "outbound" in name else "received")
    line = payload["from"] if payload["type"] == "dialed" else payload["to"]
    assert line == "441632960001"
    assert ("start_time" in payload) == (
        state not in {"ringing", "answered", "aianalysis"}
    )
    assert ("duration" in payload) == (state == "ended")
    assert ("voiceuri" in payload) == (state == "ended")
    assert ("transcript" in payload) == (state == "aianalysis")


def test_no_fixture_contains_a_number_outside_the_drama_ranges() -> None:
    texts = [path.read_text(encoding="utf-8") for path in FIXTURES_DIR.rglob("*.json")]
    assert texts
    assert [run for text in texts for run in unexpected_numbers(text)] == []
    assert any(_DRAMA_NUMBER.search(text) for text in texts)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('"01632960001"', []),
        ('"441632960001"', []),
        ('"07700900123"', []),
        ('"447700900123"', []),
        ('"00000000"', []),
        ('"07911123456"', ["07911123456"]),
        ('"442079460000"', ["442079460000"]),
        ('"01632970001"', ["01632970001"]),
        ('"1632970001"', ["1632970001"]),
        ('"2026-10-16 09:15:02"', []),
        ('"2026-10-16T09:15:02.123456789+01:00"', []),
        ('"2026-10-16T09:15:02Z"', []),
        ('"20261016T091502Z"', []),
        ('"2026-10-16 09:15:02", "07911123456"', ["07911123456"]),
        ('"1760600123"', ["1760600123"]),
    ],
)
def test_unexpected_numbers(text: str, expected: list[str]) -> None:
    assert unexpected_numbers(text) == expected


def test_no_dashes_in_fixtures() -> None:
    for path in FIXTURES_DIR.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert not {chr(0x2013), chr(0x2014)} & set(text)


def test_load_fixture_returns_the_synthetic_file_even_when_captured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fixture_loader, "CAPTURED_DIR", tmp_path)
    (tmp_path / "get_token.json").write_text('{"captured": true}', encoding="utf-8")
    assert load_fixture("get_token.json") == _synthetic("get_token.json")


def test_load_fixture_missing_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_fixture("does_not_exist.json")


def test_load_captured_returns_the_captured_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fixture_loader, "CAPTURED_DIR", tmp_path)
    (tmp_path / "get_token.json").write_text('{"captured": true}', encoding="utf-8")
    assert load_captured("get_token.json") == {"captured": True}


def test_load_captured_returns_none_without_a_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fixture_loader, "CAPTURED_DIR", tmp_path)
    assert load_captured("get_token.json") is None
