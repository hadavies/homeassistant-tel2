"""Anonymise captured Tel2 responses before they are written as fixtures."""

from collections.abc import Callable, Iterable, Mapping
import ipaddress
import re
from typing import Any, Final, NamedTuple
from urllib.parse import urlsplit

from custom_components.tel2.const import SECRET_PREFS
from custom_components.tel2.numbering import canonical_number

ACCOUNT_CODE_PLACEHOLDER: Final = "00000000"
REDACTED: Final = "[REDACTED]"
POSTCODE_PLACEHOLDER: Final = "ZZ99 9ZZ"
DRAMA_RANGE_SIZE: Final = 1000
_DRAMA_CANONICAL_PREFIX: Final = "441632960"
_MIN_PHONE_DIGITS: Final = 9
_MIN_LEARNED_LENGTH: Final = 3
_MIN_ID_LENGTH: Final = 4
_MIN_SECRET_LENGTH: Final = 4
_MIN_SUBSTRING_SECRET_LENGTH: Final = 8
_MIN_SUBSTRING_ACCOUNT_LENGTH: Final = 6
_DROP: Final = object()


def _norm(key: str) -> str:
    """Return a key lowercased with separators removed."""
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _words(name: str) -> list[str]:
    """Split an identifier into lowercase words on separators and camel case."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [word for word in re.split(r"[^a-z0-9]+", spaced.lower()) if word]


_SECRET_PREF_KEYS: Final = frozenset(_norm(pref) for pref in SECRET_PREFS)
_SECRET_SUBSTRINGS: Final = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "securityanswer",
)
_TRIVIAL_SECRET_VALUES: Final = frozenset(
    {"true", "false", "none", "null", "enabled", "disabled", "active", "inactive"}
)
_ENVELOPE_TEXT_KEYS: Final = frozenset({"message", "status", "action"})
SECRET_HEADER_PLACEHOLDER: Final = "X-Example-Header"  # noqa: S105


def is_secret_name(name: str) -> bool:
    """Return whether a key or identifier names a secret."""
    compact = _norm(name)
    if compact.endswith("secretheader"):
        return False
    if compact in _SECRET_PREF_KEYS or compact == "parameter1":
        return True
    if any(fragment in compact for fragment in _SECRET_SUBSTRINGS):
        return True
    if re.search(r"pin(?:number|code)", compact):
        return True
    return any(word in {"pin", "pwd"} or word.endswith("pin") for word in _words(name))


def _names_secret(value: Any, exempt: bool) -> bool:
    """Return whether a string value is an identifier that names a secret."""
    return (
        not exempt
        and isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9_.-]+", value) is not None
        and is_secret_name(value)
    )


_ACCOUNT_KEYS: Final = frozenset(
    {"accountcode", "accountnumber", "accountno", "account", "customercode"}
)
_PERSON_KEYS: Final[Mapping[str, str]] = {
    "customername": "Example Customer",
    "contactname": "Example Customer",
    "fullname": "Example Customer",
    "companyname": "Example Ltd",
    "firstname": "Example",
    "lastname": "Customer",
}
_LOGIN_KEYS: Final = frozenset({"login", "username"})
_NAME_STOPWORDS: Final = frozenset(
    {"the", "and", "ltd", "limited", "plc", "llp", "inc", "company", "mr", "mrs", "ms"}
)
_USER_AGENT_KEYS: Final = frozenset({"useragent", "agent", "ua"})
_DEVICE_KEYS: Final = frozenset(
    {
        "mac",
        "macaddress",
        "deviceid",
        "device",
        "serial",
        "serialnumber",
        "imei",
        "contact",
        "sipcontact",
    }
)
_ID_KEYS: Final = frozenset(
    {
        "id",
        "uniqueid",
        "userid",
        "editid",
        "originid",
        "webhookid",
        "recordid",
        "smsid",
        "cdrid",
        "customerid",
        "accountid",
        "subscriberid",
        "phoneid",
        "lineid",
        "groupid",
        "callid",
        "sessionid",
        "profileid",
        "uuid",
        "guid",
    }
)
_NUMBER_KEYS: Final = frozenset(
    {
        "servicename",
        "aparty",
        "bparty",
        "from",
        "to",
        "line",
        "cli",
        "callerid",
        "destination",
    }
)
_NUMBER_KEY_HINTS: Final = ("number", "phone", "mobile", "fax", "landline")
_LINE_ROW_KEYS: Final = frozenset({"servicename", "linenumber", "line"})
_LINE_NAME_KEYS: Final = frozenset({"name", "alias", "displayname"})
_ALWAYS_LINE_NAME_KEYS: Final = frozenset({"parameter4", "linename"})
_TECHNICAL_NAME_KEYS: Final = frozenset(
    {
        "servicename",
        "prefname",
        "preferencename",
        "filename",
        "hostname",
        "typename",
        "fieldname",
        "parametername",
        "settingname",
        "headername",
        "voicename",
        "planname",
        "productname",
        "bundlename",
        "packagename",
    }
)
_NOT_ADDRESS_HINTS: Final = ("email", "ipaddress", "ipv", "macaddress", "web", "url")
_ADDRESS_EXACT: Final[Mapping[str, str]] = {
    **dict.fromkeys(("building", "buildingname"), "Example House"),
    **dict.fromkeys(("buildingnumber", "streetnumber", "housenumber"), "1"),
    **dict.fromkeys(("city", "town"), "Exampletown"),
}
_ADDRESS_SUBSTRINGS: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (("postcode", "postalcode", "zipcode"), "ZZ99 9ZZ"),
    (("address", "street"), "Example Street"),
    (("suburb",), "Example Suburb"),
)
_ADDRESS_ANCHORED: Final[Mapping[str, str]] = {
    **dict.fromkeys(
        ("flat", "flatnumber", "unit", "unitnumber", "apartment"), "Flat 1"
    ),
    **dict.fromkeys(("district", "locality"), "Example Suburb"),
    **dict.fromkeys(("state", "county", "region", "province"), "Example County"),
    "zip": "ZZ99 9ZZ",
}

_URL_RE: Final = re.compile(
    r"(?<![\w.-])(?:[a-zA-Z][a-zA-Z0-9+.-]*://|sips?:)[^\s\"'<>]+"
)
_EMAIL_RE: Final = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_MAC_RE: Final = re.compile(
    r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])"
)
_IPV4_RE: Final = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6_RE: Final = re.compile(
    r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])"
)
_UK_PHONE_RE: Final = re.compile(
    r"(?<![\d+])(?:"
    r"(?:\+|00)44[ -]?(?:\(0\)[ -]?)?\d(?:[ -]?\d){8,9}"
    r"|44\d{9,10}"
    r"|\(0\d{2,4}\)[ -]?\d(?:[ -]?\d){4,7}"
    r"|0\d(?:[ -]?\d){8,9}"
    r")(?!\d)"
)
_INTL_PHONE_RE: Final = re.compile(r"(?<![\d+])(?:\+|00)\d(?:[ -]?\d){7,14}(?!\d)")
_HOSTNAME_RE: Final = re.compile(
    r"(?<![\w.@/-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?![\w.-])",
    re.IGNORECASE,
)
_SAFE_HOST_SUFFIXES: Final = ("example.com", "example.invalid")
_DIGIT_RUN_RE: Final = re.compile(r"\d{9,}")
_PLACEHOLDER_DIGITS_RE: Final = re.compile(r"(?:0044|44|0)?1632960\d{3}")
_GENERIC_LINE_NAME_RE: Final = re.compile(r"line \d+", re.IGNORECASE)
_POSTCODE_RE: Final = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}(?![A-Za-z0-9])", re.IGNORECASE
)
_KEY_SCRUB_KINDS: Final = frozenset({"secret", "phone", "id"})
_KEY_CHECK_KINDS: Final = frozenset({"secret", "phone", "id", "contact"})
_BOUNDED: Final = r"(?<![A-Za-z0-9])(?<![0-9]\.){}(?![A-Za-z0-9])(?!\.[0-9])"


class _Block(NamedTuple):
    """What kind of record the current dict is."""

    address: bool = False
    line: bool = False


class LeakError(ValueError):
    """Raised when anonymised output still contains personal data."""

    def __init__(self, paths: list[str]) -> None:
        """Hold the offending JSON paths; never the values."""
        super().__init__(
            "Anonymised output failed the leak check at: " + ", ".join(paths)
        )
        self.paths = paths


class Anonymiser:
    """Replace personal data with deterministic placeholders held for one run."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        """Start a run, scrubbing the given extra secrets wherever they appear."""
        self._phones: dict[str, int] = {}
        self._line_names: dict[str, str] = {}
        self._mapped: dict[str, dict[str, str]] = {}
        self._ids: dict[str, int] = {}
        self._learned: dict[str, tuple[str, bool, str]] = {}
        self._patterns: dict[frozenset[str] | None, re.Pattern[str] | None] = {}
        for secret in secrets:
            if secret and len(secret) >= _MIN_LEARNED_LENGTH:
                self._learn(
                    secret,
                    REDACTED,
                    bounded=len(secret) < _MIN_SUBSTRING_SECRET_LENGTH,
                    kind="secret",
                )

    def anonymise(self, value: Any) -> Any:
        """Return an anonymised copy of a decoded JSON value."""
        result = self._value("", "", value, _Block(), 0)
        return None if result is _DROP else result

    def find_leaks(self, fixtures: Mapping[str, Any]) -> list[str]:
        """Return the JSON paths in the output that still look personal or secret."""
        paths: list[str] = []
        for name, value in fixtures.items():
            self._check(value, name, False, 0, paths)
        return paths

    def _learn(self, raw: str, placeholder: str, *, bounded: bool, kind: str) -> None:
        raw = raw.strip()
        if len(raw) < _MIN_LEARNED_LENGTH or raw.casefold() == placeholder.casefold():
            return
        if raw.casefold() not in self._learned:
            self._learned[raw.casefold()] = (placeholder, bounded, kind)
            self._patterns.clear()

    def _learned_pattern(
        self, kinds: frozenset[str] | None = None
    ) -> re.Pattern[str] | None:
        if kinds not in self._patterns:
            alternatives = [
                _BOUNDED.format(re.escape(raw)) if bounded else re.escape(raw)
                for raw, (_, bounded, kind) in sorted(
                    self._learned.items(), key=lambda item: -len(item[0])
                )
                if kinds is None or kind in kinds
            ]
            self._patterns[kinds] = (
                re.compile("|".join(alternatives), re.IGNORECASE)
                if alternatives
                else None
            )
        return self._patterns[kinds]

    def _replace_learned(self, text: str, kinds: frozenset[str] | None = None) -> str:
        pattern = self._learned_pattern(kinds)
        if pattern is None:
            return text
        return pattern.sub(lambda match: self._learned[match[0].casefold()][0], text)

    def _remember_secret(self, value: Any) -> None:
        if isinstance(value, (dict, list)):
            for item in value.values() if isinstance(value, dict) else value:
                self._remember_secret(item)
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            text = str(value).strip()
            if (
                len(text) >= _MIN_SECRET_LENGTH
                and text.casefold() not in _TRIVIAL_SECRET_VALUES
            ):
                self._learn(
                    text,
                    REDACTED,
                    bounded=len(text) < _MIN_SUBSTRING_SECRET_LENGTH,
                    kind="secret",
                )

    def _value(
        self, key: str, norm_key: str, value: Any, block: _Block, depth: int
    ) -> Any:
        if isinstance(value, dict):
            return self._dict(value, depth)
        if isinstance(value, list):
            return self._list(key, norm_key, value, depth)
        if isinstance(value, bool) or value is None or value == "":
            return value
        if isinstance(value, (int, float)):
            return self._number(key, norm_key, value)
        if not isinstance(value, str):
            return value
        return self._str(key, norm_key, value, block)

    def _list(self, key: str, norm_key: str, items: list[Any], depth: int) -> list[Any]:
        result: list[Any] = []
        skip_next = False
        for item in items:
            if skip_next:
                skip_next = False
                if not isinstance(item, (dict, list)):
                    self._remember_secret(item)
                    continue
            if _names_secret(item, exempt=False):
                self._remember_secret(item)
                skip_next = True
                continue
            new_item = self._value(key, norm_key, item, _Block(), depth + 1)
            if new_item is not _DROP:
                result.append(new_item)
        return result

    def _dict(self, data: dict[Any, Any], depth: int) -> Any:
        envelope = _is_envelope(data, depth)
        if any(
            _names_secret(
                item,
                exempt=norm == "secretheader"
                or (envelope and norm in _ENVELOPE_TEXT_KEYS),
            )
            for norm, item in ((_norm(str(key)), item) for key, item in data.items())
        ):
            self._remember_secret(data)
            return _DROP
        norm_keys = {_norm(str(key)) for key in data}
        block = _Block(
            address=any(_address_placeholder(norm, False) for norm in norm_keys),
            line=bool(norm_keys & _LINE_ROW_KEYS),
        )
        result: dict[Any, Any] = {}
        for key, item in data.items():
            str_key = str(key)
            if is_secret_name(str_key):
                self._remember_secret(item)
                continue
            new_item = self._value(str_key, _norm(str_key), item, block, depth + 1)
            if new_item is not _DROP:
                result[self._scrub_key(key) if isinstance(key, str) else key] = new_item
        return result

    def _number(self, key: str, norm_key: str, value: float) -> float:
        if _is_id_key(key, norm_key) and value != 0:
            return type(value)(self._id_number(str(value)))
        if isinstance(value, float) and not value.is_integer():
            return value
        text = str(int(value))
        if _is_number_key(norm_key) or _UK_PHONE_RE.fullmatch(text):
            if not text.startswith("44") and len(text) in (9, 10):
                national = self._phone("0" + text)
                placeholder = national[1:] if national else None
            else:
                placeholder = self._phone(text)
            if placeholder is not None:
                return type(value)(int(placeholder))
        return value

    def _str(self, key: str, norm_key: str, value: str, block: _Block) -> str:
        if norm_key == "secretheader":
            return SECRET_HEADER_PLACEHOLDER
        if norm_key in _ACCOUNT_KEYS:
            bounded = len(value.strip()) < _MIN_SUBSTRING_ACCOUNT_LENGTH
            self._learn(
                value, ACCOUNT_CODE_PLACEHOLDER, bounded=bounded, kind="contact"
            )
            return ACCOUNT_CODE_PLACEHOLDER
        if norm_key in _USER_AGENT_KEYS:
            return self._device(value, "example-user-agent")
        if norm_key in _DEVICE_KEYS:
            return self._device(value, "device")
        if _is_id_key(key, norm_key):
            return value if value == "0" else self._id(value)
        if _is_number_key(norm_key):
            phone = self._phone(value)
            if phone is not None:
                return phone
        if norm_key in _PERSON_KEYS:
            return self._person(value, _PERSON_KEYS[norm_key])
        if norm_key in _LOGIN_KEYS:
            self._learn(value, "example-login", bounded=True, kind="contact")
            return "example-login"
        address = _address_placeholder(norm_key, block.address)
        if address is not None:
            if _address_placeholder(norm_key, False) is None:
                return address
            return self._address(value, address)
        is_line_name = norm_key in _ALWAYS_LINE_NAME_KEYS or (
            block.line and norm_key in _LINE_NAME_KEYS
        )
        if is_line_name and not _UK_PHONE_RE.fullmatch(value.strip()):
            return self._line_name(value)
        if (
            norm_key.endswith("name")
            and norm_key != "name"
            and norm_key not in _TECHNICAL_NAME_KEYS
        ):
            return self._person(value, "Example Customer")
        return self._scrub(value)

    def _address(self, value: str, placeholder: str) -> str:
        self._learn(value, placeholder, bounded=True, kind="name")
        for part in value.split(","):
            self._learn(part, placeholder, bounded=True, kind="name")
            self._learn(
                re.sub(r"^\s*\d+[a-z]?\s+", "", part, flags=re.IGNORECASE),
                placeholder,
                bounded=True,
                kind="name",
            )
        return placeholder

    def _person(self, value: str, placeholder: str) -> str:
        self._learn(value, placeholder, bounded=False, kind="name")
        for part in re.split(r"[^\w'-]+", value):
            if len(part) >= _MIN_LEARNED_LENGTH and part.lower() not in _NAME_STOPWORDS:
                self._learn(part, "Example", bounded=True, kind="name")
        return placeholder

    def _line_name(self, value: str) -> str:
        if value not in self._line_names:
            self._line_names[value] = f"Line {len(self._line_names) + 1}"
        if not _GENERIC_LINE_NAME_RE.fullmatch(value.strip()):
            self._learn(value, self._line_names[value], bounded=True, kind="name")
        return self._line_names[value]

    def _map(self, kind: str, raw: str, template: str) -> str:
        mapping = self._mapped.setdefault(kind, {})
        found = raw.casefold()
        if found not in mapping:
            mapping[found] = template.format(len(mapping) + 1)
        return mapping[found]

    def _device(self, value: str, prefix: str) -> str:
        placeholder = self._map("device", value, prefix + "-{}")
        self._learn(value, placeholder, bounded=False, kind="contact")
        return placeholder

    def _id_number(self, value: str) -> int:
        if value not in self._ids:
            self._ids[value] = len(self._ids) + 1
            if len(value) >= _MIN_ID_LENGTH:
                self._learn(value, self._id(value), bounded=True, kind="id")
        return self._ids[value]

    def _id(self, value: str) -> str:
        number = self._id_number(value)
        return str(number) if value.isdecimal() else f"id-{number}"

    def _phone(self, raw: str) -> str | None:
        stripped = raw.strip()
        try:
            canonical = canonical_number(stripped)
        except ValueError:
            return None
        if len(canonical) < _MIN_PHONE_DIGITS:
            return None
        if canonical.startswith(_DRAMA_CANONICAL_PREFIX):
            return stripped
        index = self._phones.setdefault(canonical, len(self._phones))
        if index >= DRAMA_RANGE_SIZE:
            raise ValueError("More distinct numbers than the drama range holds")
        international = f"{_DRAMA_CANONICAL_PREFIX}{index:03d}"
        self._learn(canonical, international, bounded=False, kind="phone")
        if canonical.startswith("44"):
            national = "0" + international[2:]
            self._learn("0" + canonical[2:], national, bounded=False, kind="phone")
            self._learn(canonical[2:], international[2:], bounded=False, kind="phone")
        if stripped.startswith("+"):
            placeholder = f"+{international}"
        elif stripped.startswith("00"):
            placeholder = f"00{international}"
        elif stripped.startswith(("0", "(0")):
            placeholder = f"0{international[2:]}"
        else:
            placeholder = international
        self._learn(stripped, placeholder, bounded=False, kind="phone")
        return placeholder

    def _scrub(self, text: str) -> str:
        if len(text) >= _MIN_ID_LENGTH and text in self._ids:
            return self._id(text)
        text = _URL_RE.sub(self._replace_url, text)
        text = _EMAIL_RE.sub(self._replace_email, text)
        text = _MAC_RE.sub(lambda match: self._device(match[0], "device"), text)
        text = _IPV4_RE.sub(self._ip_replacer(ipaddress.IPv4Address), text)
        text = _IPV6_RE.sub(self._ip_replacer(ipaddress.IPv6Address), text)
        text = _UK_PHONE_RE.sub(self._replace_phone, text)
        text = _INTL_PHONE_RE.sub(self._replace_phone, text)
        text = self._replace_learned(text)
        return _HOSTNAME_RE.sub(self._replace_host, text)

    def _scrub_key(self, key: str) -> str:
        if len(key) >= _MIN_ID_LENGTH and key in self._ids:
            return self._id(key)
        key = _UK_PHONE_RE.sub(self._replace_phone, key)
        key = _INTL_PHONE_RE.sub(self._replace_phone, key)
        return self._replace_learned(key, _KEY_SCRUB_KINDS)

    def _replace_phone(self, match: re.Match[str]) -> str:
        return self._phone(match[0]) or match[0]

    def _replace_url(self, match: re.Match[str]) -> str:
        placeholder = self._map("url", match[0], "https://example.invalid/{}")
        self._learn(match[0], placeholder, bounded=False, kind="contact")
        host = urlsplit(match[0]).hostname if "//" in match[0] else None
        if host:
            self._learn_host(host)
        return placeholder

    def _replace_email(self, match: re.Match[str]) -> str:
        placeholder = self._map("email", match[0], "user{}@example.com")
        self._learn(match[0], placeholder, bounded=False, kind="contact")
        self._learn_host(match[0].rsplit("@", 1)[1])
        return placeholder

    def _replace_host(self, match: re.Match[str]) -> str:
        if match[0].lower().endswith(_SAFE_HOST_SUFFIXES):
            return match[0]
        return self._learn_host(match[0])

    def _learn_host(self, host: str) -> str:
        placeholder = self._map("host", host, "host{}.example.invalid")
        if not host.lower().endswith(_SAFE_HOST_SUFFIXES):
            self._learn(host, placeholder, bounded=True, kind="contact")
        return placeholder

    def _ip_replacer(
        self, parse: Callable[[str], object]
    ) -> Callable[[re.Match[str]], str]:
        def replace(match: re.Match[str]) -> str:
            try:
                parse(match[0])
            except ValueError:
                return match[0]
            placeholder = self._map("ip", match[0], "192.0.2.{}")
            self._learn(match[0], placeholder, bounded=True, kind="contact")
            return placeholder

        return replace

    def _check(
        self, value: Any, path: str, exempt: bool, depth: int, paths: list[str]
    ) -> None:
        if isinstance(value, dict):
            envelope = _is_envelope(value, depth)
            for index, (key, item) in enumerate(value.items()):
                str_key = str(key)
                if is_secret_name(str_key) or self._leaks(
                    str_key, False, _KEY_CHECK_KINDS
                ):
                    child = f"{path}.<key {index}>"
                    paths.append(child)
                else:
                    child = f"{path}.{str_key}"
                child_exempt = envelope and _norm(str_key) in _ENVELOPE_TEXT_KEYS
                self._check(item, child, child_exempt, depth + 1, paths)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self._check(item, f"{path}[{index}]", False, depth + 1, paths)
        elif (
            value is not None
            and not isinstance(value, bool)
            and self._leaks(str(value), exempt)
        ):
            paths.append(path)

    def _leaks(
        self, text: str, exempt: bool, kinds: frozenset[str] | None = None
    ) -> bool:
        pattern = self._learned_pattern(kinds)
        if pattern is not None and pattern.search(text):
            return True
        if any(
            not _PLACEHOLDER_DIGITS_RE.fullmatch(run)
            for run in _DIGIT_RUN_RE.findall(text)
        ):
            return True
        if any(
            _norm(postcode) != _norm(POSTCODE_PLACEHOLDER)
            for postcode in _POSTCODE_RE.findall(text)
        ):
            return True
        return _names_secret(text, exempt=exempt)


def _address_placeholder(norm_key: str, anchored: bool) -> str | None:
    """Return the example value for an address field, or None."""
    if any(hint in norm_key for hint in _NOT_ADDRESS_HINTS):
        return None
    if norm_key in _ADDRESS_EXACT:
        return _ADDRESS_EXACT[norm_key]
    for fragments, placeholder in _ADDRESS_SUBSTRINGS:
        if any(fragment in norm_key for fragment in fragments):
            return placeholder
    return _ADDRESS_ANCHORED.get(norm_key) if anchored else None


def _is_envelope(data: dict[Any, Any], depth: int) -> bool:
    """Return whether a dict is a Tel2 response envelope."""
    return depth <= 1 and "Result" in data


def _is_id_key(key: str, norm_key: str) -> bool:
    """Return whether a key names a record identifier."""
    return norm_key in _ID_KEYS or key.endswith(("_id", "ID", "Id"))


def _is_number_key(norm_key: str) -> bool:
    """Return whether a key names a phone number."""
    return norm_key in _NUMBER_KEYS or any(
        hint in norm_key for hint in _NUMBER_KEY_HINTS
    )


def anonymise_responses(
    responses: Mapping[str, Any], *, secrets: Iterable[str] = ()
) -> dict[str, Any]:
    """Return anonymised copies of named responses, failing closed on any leak."""
    anonymiser = Anonymiser(secrets)
    # The first pass learns every detected value so free text in earlier files is scrubbed too.
    for value in responses.values():
        anonymiser.anonymise(value)
    result = {name: anonymiser.anonymise(value) for name, value in responses.items()}
    leaks = anonymiser.find_leaks(result)
    if leaks:
        raise LeakError(leaks)
    return result
