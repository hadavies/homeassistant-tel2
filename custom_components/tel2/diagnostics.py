"""Redacted config entry diagnostics for Tel2."""

from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import re
from typing import Any, Final

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CALL_EVENTS,
    CONF_CLOUDHOOK_URL,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_TEL2_WEBHOOK_ID,
    CONF_TOKEN,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DEFAULT_CALL_EVENTS,
    PREF_ANONYMOUS_REJECT,
    PREF_DO_NOT_DISTURB,
    PREF_FORWARD_ALWAYS,
    PREF_FORWARD_ALWAYS_NUMBER,
    PREF_RECORDING_DISABLED,
    SECRET_PREFS,
    TEL2_COUNTRY_CODE,
)
from .models import LinesState
from .runtime import Tel2ConfigEntry

TO_REDACT: Final = frozenset(
    {
        CONF_LOGIN,
        CONF_PASSWORD,
        CONF_TOKEN,
        CONF_WEBHOOK_ID,
        CONF_WEBHOOK_SECRET,
        CONF_CLOUDHOOK_URL,
        "url",
        CONF_TEL2_WEBHOOK_ID,
        "account_code",
        "customer_name",
        "company_name",
        "name",
        "email",
        "from_number",
        "to_number",
        *SECRET_PREFS,
        PREF_FORWARD_ALWAYS_NUMBER,
        "do_not_disturb_forward",
        "voicemail_sms_alert",
        "voicemail_emailcopy",
        "missed_call_email_address",
    }
)
DIAGNOSTIC_PREFS: Final = (
    PREF_DO_NOT_DISTURB,
    PREF_FORWARD_ALWAYS,
    PREF_ANONYMOUS_REJECT,
    PREF_RECORDING_DISABLED,
    "profile_recordingenabled",
    "callrecording_type",
)
_SECRET_KEY_PATTERNS: Final = (
    "password",
    "passwd",
    "pin",
    "secret",
    "token",
    "access_code",
    "login",
    "url",
    "key",
    "auth",
)
_SEPARATORS: Final = r"[ \-().]*"
_DIGIT_RUN: Final = re.compile(r"\d[\d\s\-().,/_]*\d")
_URI: Final = re.compile(r"://|\b(?:tel|sips?):", re.IGNORECASE)
_MIN_REDACTED_DIGITS: Final = 7

type Aliasing = tuple[re.Pattern[str], dict[str, str]] | None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: Tel2ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Tel2 config entry."""
    runtime = entry.runtime_data
    account = runtime.account.data
    lines = runtime.lines.data
    aliasing = _alias_pattern(
        sorted({*account.numbers, *lines.lines}),
        {number.canonical: number.tel2_id for number in account.numbers.values()},
    )
    tel2 = _sanitise_tel2(
        {"account": _json_safe(asdict(account)), "lines": _lines(lines)}, aliasing
    )
    return {
        "entry": _redact({"data": dict(entry.data), "options": dict(entry.options)}),
        **tel2,
        "coordinators": {
            "account": {"last_update_success": runtime.account.last_update_success},
            "lines": {"last_update_success": runtime.lines.last_update_success},
        },
        "call_events": _call_event_status(entry),
    }


def _lines(state: LinesState) -> dict[str, Any]:
    lines: dict[str, Any] = {}
    for canonical, line in state.lines.items():
        prefs: dict[str, Any] = {
            pref: line.prefs[pref] for pref in DIAGNOSTIC_PREFS if pref in line.prefs
        }
        if line.prefs.get(PREF_FORWARD_ALWAYS_NUMBER):
            prefs[PREF_FORWARD_ALWAYS_NUMBER] = REDACTED
        prefs["other_pref_count"] = len(line.prefs.keys() - prefs.keys())
        lines[canonical] = {"registered": line.registered, "prefs": prefs}
    return {"lines": lines}


def _call_event_status(entry: Tel2ConfigEntry) -> dict[str, Any]:
    enabled = entry.options.get(CONF_CALL_EVENTS, DEFAULT_CALL_EVENTS)
    manager = entry.runtime_data.call_events
    if manager is None:
        return {"enabled": enabled, "running": False}
    status = manager.status
    return {
        "enabled": enabled,
        "running": True,
        "url_source": status.url_source,
        "registered_on_tel2": status.registered_on_tel2,
        "last_event_at": _json_safe(status.last_event_at),
    }


def _number_forms(canonical: str, tel2_id: str | None) -> set[str]:
    forms = {canonical, f"+{canonical}", f"00{canonical}"}
    if tel2_id:
        forms.add(tel2_id)
    if canonical.startswith(TEL2_COUNTRY_CODE):
        national = canonical.removeprefix(TEL2_COUNTRY_CODE)
        forms |= {f"0{national}", f"+{TEL2_COUNTRY_CODE}0{national}"}
    return forms


def _alias_pattern(owned: list[str], tel2_ids: Mapping[str, str]) -> Aliasing:
    aliases = {
        _digits_form(form): f"line_{index}"
        for index, canonical in enumerate(owned, start=1)
        for form in _number_forms(canonical, tel2_ids.get(canonical))
    }
    if not aliases:
        return None
    alternation = "|".join(
        _SEPARATORS.join(map(re.escape, form))
        for form in sorted(aliases, key=len, reverse=True)
    )
    return re.compile(rf"(?<!\d)(?:{alternation})(?!\d)"), aliases


def _alias(text: str, aliasing: Aliasing) -> str:
    if aliasing is None:
        return text
    pattern, aliases = aliasing
    return pattern.sub(lambda match: aliases[_digits_form(match.group())], text)


def _digits_form(match: str) -> str:
    return re.sub(_SEPARATORS, "", match)


def _sanitise_tel2(data: dict[str, Any], aliasing: Aliasing) -> dict[str, Any]:
    """Alias own numbers, then redact contact details and secret-like keys."""
    return _redact(_scrub(data, aliasing))


def _redact(data: dict[str, Any]) -> dict[str, Any]:
    return async_redact_data(data, TO_REDACT | _secret_like_keys(data))


def _scrub(value: Any, aliasing: Aliasing) -> Any:
    if isinstance(value, dict):
        return {
            _scrub_key(key, aliasing): _scrub(item, aliasing)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item, aliasing) for item in value]
    if isinstance(value, str):
        aliased = _alias(value, aliasing)
        return REDACTED if _has_contact_details(aliased) else aliased
    return value


def _scrub_key(key: Any, aliasing: Aliasing) -> Any:
    if not isinstance(key, str):
        return key
    aliased = _alias(key, aliasing)
    return REDACTED if _has_contact_details(aliased) else aliased


def _has_contact_details(text: str) -> bool:
    if "@" in text or _URI.search(text):
        return True
    return any(
        sum(char.isdigit() for char in match.group()) >= _MIN_REDACTED_DIGITS
        for match in _DIGIT_RUN.finditer(text)
    )


def _is_secret_like(key: str) -> bool:
    lowered = key.lower()
    return any(pattern in lowered for pattern in _SECRET_KEY_PATTERNS) or (
        "email" in lowered.replace("voicemail", "")
    )


def _secret_like_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    items: Iterable[Any]
    if isinstance(value, dict):
        keys.update(
            key for key in value if isinstance(key, str) and _is_secret_like(key)
        )
        items = value.values()
    elif isinstance(value, list):
        items = value
    else:
        return keys
    for item in items:
        keys |= _secret_like_keys(item)
    return keys


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value
