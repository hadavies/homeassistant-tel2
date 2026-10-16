"""Tests for Tel2 constants."""

import pytest

from custom_components.tel2.const import (
    CALL_EVENT_TYPES,
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
    } == SECRET_PREFS
