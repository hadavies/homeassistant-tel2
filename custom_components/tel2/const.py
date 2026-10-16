"""Constants for the Tel2 integration."""

from collections.abc import Mapping
from datetime import timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final

DOMAIN: Final = "tel2"

API_URL: Final = "https://portal.tel2.co.uk/api/"
TEL2_COUNTRY_CODE: Final = "44"
TEL2_CURRENCY: Final = "GBP"

DEFAULT_SCAN_INTERVAL_MINUTES: Final = 5
MIN_SCAN_INTERVAL_MINUTES: Final = 2
MAX_SCAN_INTERVAL_MINUTES: Final = 60
ACCOUNT_SCAN_INTERVAL: Final = timedelta(minutes=15)

CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_CALL_EVENTS: Final = "call_events"
DEFAULT_CALL_EVENTS: Final = True

CONF_LOGIN: Final = "login"
CONF_PASSWORD: Final = "password"  # noqa: S105
CONF_TOKEN: Final = "token"  # noqa: S105
CONF_WEBHOOK_ID: Final = "webhook_id"
CONF_WEBHOOK_SECRET: Final = "webhook_secret"  # noqa: S105
CONF_TEL2_WEBHOOK_ID: Final = "tel2_webhook_id"
CONF_CLOUDHOOK_URL: Final = "cloudhook_url"

SECRET_PREFS: Final = frozenset(
    {
        "voicemail_pin",
        "access_code_number",
        "remotecallback_pin",
        "remotedialtone_pin",
        "conference_pin",
        "security_answer",
        "password",
        "imappassword",
        "conference_adminpin",
    }
)

PERSONAL_PREF_FIELDS: Final = frozenset(
    {
        "fullname",
        "email",
        "imapuser",
        "pager",
        "customer_id",
        "id",
        "lastcld",
        "lastclr",
    }
)

# Lower-case substrings of Tel2 error messages that mean the token or login was refused.
# Provisional until the live capture confirms Tel2's wording.
AUTH_ERROR_PATTERNS: Final[tuple[str, ...]] = (
    "invalid login",
    "invalid token",
    "login not specified",
    "token not specified",
    "invalid password",
    "token expired",
    "not authenticated",
    "unauthorised",
    "unauthorized",
)

# The API docs show pref values as "1"/"0" strings; confirm against the live capture.
PREF_VALUE_ON: Final = "1"
PREF_VALUE_OFF: Final = "0"

PREF_DO_NOT_DISTURB: Final = "do_not_disturb_status"
PREF_FORWARD_ALWAYS: Final = "callforward_always_status"
PREF_FORWARD_ALWAYS_NUMBER: Final = "callforward_always_number"
PREF_ANONYMOUS_REJECT: Final = "anoncall_reject"


class CallEventType(StrEnum):
    """Call event types fired by the per-line event entity."""

    RINGING = "ringing"
    ANSWERED = "answered"
    ENDED = "ended"
    MISSED = "missed"
    BUSY = "busy"
    NO_ANSWER = "no_answer"
    REJECTED = "rejected"
    INVALID = "invalid"
    NOT_AVAILABLE = "not_available"
    BLOCKED = "blocked"


CALL_EVENT_TYPES: Final[tuple[CallEventType, ...]] = tuple(CallEventType)

TEL2_STATE_MAP: Final[Mapping[str, CallEventType]] = MappingProxyType(
    {
        "ringing": CallEventType.RINGING,
        "answered": CallEventType.ANSWERED,
        "ended": CallEventType.ENDED,
        "missed": CallEventType.MISSED,
        "busy": CallEventType.BUSY,
        "noanswer": CallEventType.NO_ANSWER,
        "rejected": CallEventType.REJECTED,
        "invalid": CallEventType.INVALID,
        "notavailable": CallEventType.NOT_AVAILABLE,
        "blocked": CallEventType.BLOCKED,
    }
)
TEL2_IGNORED_STATES: Final = frozenset({"aianalysis"})
