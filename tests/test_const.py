"""Tests for Tel2 constants."""

import pytest

from custom_components.tel2.api import is_auth_message
from custom_components.tel2.const import (
    AUTH_ERROR_PATTERNS,
    CALL_EVENT_TYPES,
    PERSONAL_PREF_FIELDS,
    PREF_RECORDING_DISABLED,
    PREF_VALUE_OFF,
    PREF_VALUE_ON,
    SECRET_PREFS,
    TEL2_IGNORED_STATES,
    TEL2_STATE_MAP,
    CallEventType,
)


@pytest.mark.parametrize(
    ("state", "event_type"),
    [
        ("ringing", CallEventType.RINGING),
        ("answered", CallEventType.ANSWERED),
        ("ended", CallEventType.ENDED),
        ("missed", CallEventType.MISSED),
        ("busy", CallEventType.BUSY),
        ("noanswer", CallEventType.NO_ANSWER),
        ("rejected", CallEventType.REJECTED),
        ("invalid", CallEventType.INVALID),
        ("notavailable", CallEventType.NOT_AVAILABLE),
        ("blocked", CallEventType.BLOCKED),
    ],
)
def test_tel2_states_map_to_event_types(state: str, event_type: CallEventType) -> None:
    assert TEL2_STATE_MAP[state] is event_type


def test_aianalysis_is_ignored() -> None:
    assert "aianalysis" in TEL2_IGNORED_STATES
    assert "aianalysis" not in TEL2_STATE_MAP


def test_call_event_types_cover_every_mapped_state() -> None:
    assert set(CALL_EVENT_TYPES) == set(TEL2_STATE_MAP.values()) == set(CallEventType)
    assert "no_answer" in CALL_EVENT_TYPES
    assert "not_available" in CALL_EVENT_TYPES


def test_secret_prefs() -> None:
    assert {
        "voicemail_pin",
        "access_code_number",
        "remotecallback_pin",
        "remotedialtone_pin",
        "conference_pin",
        "security_answer",
        "password",
        "imappassword",
        "conference_adminpin",
    } == SECRET_PREFS


def test_personal_pref_fields() -> None:
    assert {
        "fullname",
        "email",
        "imapuser",
        "pager",
        "customer_id",
        "id",
        "lastcld",
        "lastclr",
    } == PERSONAL_PREF_FIELDS
    assert not PERSONAL_PREF_FIELDS & SECRET_PREFS


def test_pref_values_are_documented_strings() -> None:
    assert (PREF_VALUE_ON, PREF_VALUE_OFF) == ("1", "0")


def test_recording_state_pref_is_the_captured_profile_flag() -> None:
    assert PREF_RECORDING_DISABLED == "callrecording_disabled"


def test_auth_error_patterns_are_lower_case() -> None:
    assert all(pattern == pattern.lower() for pattern in AUTH_ERROR_PATTERNS)


@pytest.mark.parametrize(
    "message",
    ["Invalid Token Specified", "Invalid Access Level or Password Specified"],
)
def test_captured_auth_messages_match(message: str) -> None:
    assert is_auth_message(message)


@pytest.mark.parametrize(
    "message",
    [
        "You do not have access to this function.",
        "Unauthorised: function disabled for this account.",
        "Unauthorised destination number.",
        "Unauthorized request.",
        "Not authenticated for this function.",
        "No Data Found.",
        "An error occurred processing your request.",
    ],
)
def test_access_refused_and_other_messages_are_not_auth(message: str) -> None:
    assert not is_auth_message(message)
