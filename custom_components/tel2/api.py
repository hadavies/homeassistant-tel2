"""Client for the Tel2 portal API."""

import asyncio
from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
import json
import logging
from typing import Any, Final

import aiohttp
from yarl import URL

from .const import (
    API_URL,
    AUTH_ERROR_PATTERNS,
    PERSONAL_PREF_FIELDS,
    PREF_FORWARD_ALWAYS_NUMBER,
    SECRET_PREFS,
)
from .models import (
    AccessContext,
    AccountBalance,
    BundleUsage,
    CustomerDetails,
    PlanSummary,
    Tel2Number,
    WebhookRegistration,
    WebhookSpec,
)
from .numbering import canonical_number, is_valid_destination

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT: Final = aiohttp.ClientTimeout(total=30)
_HEADERS: Final = {"Content-Type": "application/json", "Accept": "application/json"}
_GET_TOKEN: Final = "Get-Token"  # noqa: S105
_SUCCESS: Final = "Success"
_REGISTERED: Final = "1"
_CALLING_PLAN: Final = "CallingPlan"
_PLAN_TOTAL: Final = "Total"
_NO_EXTENSION: Final = "0"
_GET_NUMBERS_PARAMS: Final = {
    "orderby": "",
    "orderdirection": "",
    "searchby": "",
    "searchterm": "",
}
_PROBES: Final[tuple[tuple[AccessContext, str], ...]] = (
    ("account", "Get-Account-Balance"),
    ("voice", "Get-Subscriber-Status"),
    ("api", "Get-Webhooks"),
)
# Prefs Tel2 adds later may carry credentials the constants do not list yet.
_SECRET_KEY_PATTERNS: Final = (
    "password",
    "passwd",
    "pin",
    "secret",
    "token",
    "access_code",
)
# External parties' numbers and addresses are personal data.
_PERSONAL_KEY_PATTERNS: Final = ("number", "email")
_DROPPED_PREFS: Final = (
    SECRET_PREFS
    | PERSONAL_PREF_FIELDS
    | {"pref_uid", "blacklist", "do_not_disturb_forward"}
)
_OWN_NUMBER_PREFS: Final = frozenset({"mailbox", "voicemailmailbox"})
_SET_PREF: Final = "Set-Pref-Value"
_FLAG_ON: Final = "1"
_FLAG_OFF: Final = "0"
_WEBHOOK_STATES: Final = (
    "inboundringing",
    "inboundanswered",
    "inboundended",
    "inboundmissed",
    "outboundringing",
    "outboundanswered",
    "outboundended",
    "outboundunattended",
)


class Tel2Error(Exception):
    """Base class for Tel2 client errors."""


class Tel2ConnectionError(Tel2Error):
    """Tel2 could not be reached or sent an unreadable response."""


class Tel2AuthError(Tel2Error):
    """Tel2 rejected the login, password or token."""


class Tel2ApiError(Tel2Error):
    """Tel2 refused a request for a reason other than authentication."""

    def __init__(
        self, message: str, context: AccessContext | None, action: str
    ) -> None:
        """Store Tel2's message with the request it answered."""
        super().__init__(message)
        self.message = message
        self.context = context
        self.action = action


class Tel2NoData(Tel2Error):  # noqa: N818
    """Tel2 answered with no data."""


def is_auth_message(message: str) -> bool:
    """Return whether a Tel2 error message means the login or token was refused."""
    lowered = message.lower()
    return any(pattern in lowered for pattern in AUTH_ERROR_PATTERNS)


class Tel2Client:
    """Talk to the Tel2 API over an injected aiohttp session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        login: str,
        password: str,
        token: str | None = None,
        on_token_refreshed: Callable[[str], None] | None = None,
        *,
        api_url: str = API_URL,
    ) -> None:
        """Initialise the client."""
        if not login:
            raise ValueError("Tel2 login is required")
        if not password:
            raise ValueError("Tel2 password is required")
        if URL(api_url).scheme != "https":
            raise ValueError("Tel2 API URL must use https")
        self._session = session
        self._login = login
        self._password = password
        self._token = token
        self._on_token_refreshed = on_token_refreshed
        self._api_url = api_url
        self._token_lock = asyncio.Lock()

    async def async_authenticate(self) -> str:
        """Fetch a new session token and return it."""
        async with self._token_lock:
            return await self._fetch_token()

    async def async_get_customer_details(self) -> CustomerDetails:
        """Return the account holder's details."""
        row = _first_row(await self._call_required("account", "Get-Customer-Details"))
        account_code = _text(row, "AccountCode") or _text(row, "Account_Code")
        if account_code is None:
            raise Tel2ConnectionError("Tel2 response has no AccountCode")
        return CustomerDetails(
            account_code=account_code,
            customer_name=_text(row, "CustomerName") or "",
            company_name=_text(row, "CompanyName"),
            status=_text(row, "CustomerStatus") or _text(row, "Status") or "",
        )

    async def async_get_account_balance(self) -> AccountBalance:
        """Return the account balance in GBP."""
        row = _first_row(await self._call_required("account", "Get-Account-Balance"))
        return AccountBalance(
            balance=_decimal(row, "Balance"),
            available_balance=_decimal(row, "AvailableBalance"),
        )

    async def async_get_current_plan(self) -> PlanSummary | None:
        """Return the calling plan, or the first plan when none is a calling plan."""
        rows = [
            row
            for row in _rows(
                await self._call_optional("account", "Get-Current-Plan-Summary")
            )
            if row.get("ServiceType") != _PLAN_TOTAL
        ]
        if not rows:
            return None
        row = next((r for r in rows if r.get("ServiceType") == _CALLING_PLAN), rows[0])
        return PlanSummary(
            alias=_text(row, "Alias") or "",
            description=_text(row, "BillingDescription") or "",
        )

    async def async_get_numbers(self) -> list[Tel2Number]:
        """Return the account's phone numbers, without extension-only entries."""
        rows = _rows(
            await self._call_optional("voice", "Get-Numbers", dict(_GET_NUMBERS_PARAMS))
        )
        line_names = await self._line_names()
        numbers: dict[str, Tel2Number] = {}
        for row in rows:
            tel2_id = _text(row, "ServiceName")
            if tel2_id is None or not is_valid_destination(tel2_id):
                continue
            canonical = canonical_number(tel2_id)
            if canonical in numbers:
                continue
            numbers[canonical] = Tel2Number(
                canonical=canonical,
                tel2_id=str(row["ServiceName"]),
                name=line_names.get(canonical)
                or _text(row, "Parameter_4")
                or _text(row, "Alias"),
                extension=_extension(row),
            )
        return list(numbers.values())

    async def _line_names(self) -> dict[str, str]:
        try:
            data = await self._call_optional("voice", "Get-Line-Names")
        except Tel2ApiError as err:
            if err.context is None:
                raise
            return {}
        return {
            canonical: name
            for row in _rows(data)
            if (canonical := _canonical(_text(row, "ServiceName")))
            and (name := _text(row, "Name"))
        }

    async def async_get_line_bundle_usage(
        self, number: Tel2Number
    ) -> BundleUsage | None:
        """Return the number's bundle minutes, or None when it has no bundle."""
        data = await self._call_optional(
            "account", "Get-Line-Bundle-Usage", {"line": number.tel2_id}
        )
        rows = _rows(data)
        if not rows:
            return None
        return BundleUsage(
            used_minutes=_int(rows[0], "USED"), total_minutes=_int(rows[0], "TOTAL")
        )

    async def async_get_subscriber_status(self) -> frozenset[str]:
        """Return the canonical numbers that have a registered phone."""
        data = await self._call_optional("voice", "Get-Subscriber-Status")
        if not isinstance(data, Mapping):
            return frozenset()
        return frozenset(
            canonical
            for raw, state in data.items()
            if str(state) == _REGISTERED and (canonical := _canonical(str(raw)))
        )

    async def async_get_all_prefs(self) -> dict[str, dict[str, str]]:
        """Return the non-secret prefs of every line, keyed by canonical number.

        Null prefs are omitted, so an unset pref is absent from the line's dict.
        """
        prefs: dict[str, dict[str, str]] = {}
        for row in _rows(await self._call_optional("voice", "Get-All-Pref-Summary")):
            canonical = _canonical(_text(row, "pref_uid"))
            if canonical is not None and canonical not in prefs:
                prefs[canonical] = _public_prefs(row, canonical)
        return prefs

    async def async_get_webhooks(self) -> list[WebhookRegistration]:
        """Return the webhooks registered on the account."""
        webhooks: list[WebhookRegistration] = []
        for row in _rows(await self._call_optional("api", "Get-Webhooks")):
            fields = {str(key).lower(): value for key, value in row.items()}
            tel2_id = _text(fields, "id")
            if tel2_id is None:
                continue
            webhooks.append(
                WebhookRegistration(
                    tel2_id=tel2_id,
                    name=_text(fields, "name") or "",
                    url=_text(fields, "url") or "",
                )
            )
        return webhooks

    async def async_set_pref(self, number: Tel2Number, pref: str, value: str) -> None:
        """Set one pref on a number; secret prefs are refused unsent."""
        if is_secret_pref(pref):
            raise Tel2ApiError(
                f"Refusing to send secret pref {pref} to Tel2", "voice", _SET_PREF
            )
        await self._call_write(
            "voice", _SET_PREF, {"line": number.tel2_id, "pref": pref, "value": value}
        )

    async def async_set_recording(self, number: Tel2Number, enabled: bool) -> None:
        """Turn call recording on or off for a number."""
        action = "Enable-Recording" if enabled else "Disable-Recording"
        await self._call_write("voice", action, {"number": number.tel2_id})

    async def async_resync_line(self, number: Tel2Number) -> None:
        """Ask Tel2 to push the current settings to the number's phone."""
        await self._call_write("voice", "Resync-Line", {"line": number.tel2_id})

    async def async_reboot_phone(self, number: Tel2Number) -> None:
        """Ask Tel2 to reboot the number's phone."""
        await self._call_write("voice", "Reboot-Phone", {"line": number.tel2_id})

    async def async_add_webhook(
        self, spec: WebhookSpec, edit_id: str | None = None
    ) -> None:
        """Register a webhook for every line, or update the one with edit_id."""
        token = spec.secret_token
        if not (token.isascii() and token.isalnum()):
            raise ValueError("Tel2 webhook secret token must be alphanumeric")
        params = {
            "name": spec.name,
            "url": spec.url,
            "secrettoken": token,
            "secretheader": spec.secret_header,
            **dict.fromkeys(_WEBHOOK_STATES, _FLAG_ON),
            "e164format": _FLAG_ON,
            "airecording": _FLAG_OFF,
        }
        if edit_id is not None:
            params["editid"] = edit_id
        await self._call_write("api", "Add-Webhook", params)

    async def async_delete_webhook(self, webhook_id: str) -> None:
        """Delete a webhook registration."""
        await self._call_write("api", "Delete-Webhook", {"id": webhook_id})

    async def async_probe_access(self) -> set[AccessContext]:
        """Return the contexts whose probe function Tel2 refused."""
        refused: set[AccessContext] = set()
        for context, action in _PROBES:
            try:
                await self._call(context, action)
            except Tel2NoData:
                continue
            except Tel2ApiError as err:
                # A Get-Token failure says nothing about any context's access.
                if err.context is None:
                    raise
                refused.add(context)
        return refused

    async def _call_required(self, context: AccessContext, action: str) -> Any:
        """Call a Tel2 action whose result cannot be empty."""
        try:
            return await self._call(context, action)
        except Tel2NoData as err:
            raise Tel2ApiError(str(err), context, action) from err

    async def _call_write(
        self, context: AccessContext, action: str, params: dict[str, Any]
    ) -> None:
        """Call a Tel2 write; anything but Result 1 means it was not executed."""
        try:
            await self._call(context, action, params)
        except Tel2NoData as err:
            raise Tel2ApiError(
                f"Tel2 {action} was not applied: {err}", context, action
            ) from err

    async def _call(
        self,
        context: AccessContext,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Call a Tel2 action, renewing the token once if Tel2 refuses it."""
        token = await self._current_token()
        try:
            return await self._send(context, action, params, token)
        except Tel2AuthError:
            token = await self._renew_token(token)
        return await self._send(context, action, params, token)

    async def _call_optional(
        self,
        context: AccessContext,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Call a Tel2 action, returning None when Tel2 has no data."""
        try:
            return await self._call(context, action, params)
        except Tel2NoData:
            return None

    async def _current_token(self) -> str:
        async with self._token_lock:
            if self._token is None:
                return await self._fetch_token()
            return self._token

    async def _renew_token(self, rejected: str) -> str:
        async with self._token_lock:
            # Another caller may already have replaced the rejected token.
            if self._token is not None and self._token != rejected:
                return self._token
            self._token = None
            return await self._fetch_token()

    async def _fetch_token(self) -> str:
        body = {"login": self._login, "password": self._password, "action": _GET_TOKEN}
        try:
            data = await self._post(None, _GET_TOKEN, body)
        except Tel2NoData as err:
            raise Tel2ApiError(str(err), None, _GET_TOKEN) from err
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise Tel2ConnectionError("Tel2 Get-Token response has no token")
        self._token = token
        if self._on_token_refreshed is not None:
            self._on_token_refreshed(token)
        return token

    async def _send(
        self,
        context: AccessContext,
        action: str,
        params: dict[str, Any] | None,
        token: str,
    ) -> Any:
        body = {
            "token": token,
            "action": action,
            "context": context,
            "params": params or {},
        }
        return await self._post(context, action, body)

    async def _post(
        self, context: AccessContext | None, action: str, body: dict[str, Any]
    ) -> Any:
        try:
            async with self._session.post(
                self._api_url, json=body, headers=_HEADERS, timeout=REQUEST_TIMEOUT
            ) as response:
                status = response.status
                raw = await response.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Tel2 %s %s: %s", context or "-", action, type(err).__name__)
            raise Tel2ConnectionError(
                f"Tel2 {action} failed: {type(err).__name__}"
            ) from err
        try:
            data = _parse_envelope(raw, status, context, action)
        except Tel2Error as err:
            _LOGGER.debug("Tel2 %s %s: %s", context or "-", action, type(err).__name__)
            raise
        _LOGGER.debug("Tel2 %s %s: success", context or "-", action)
        return data


def _parse_envelope(
    raw: bytes, status: int, context: AccessContext | None, action: str
) -> Any:
    try:
        envelope = json.loads(raw)
    except ValueError as err:
        raise Tel2ConnectionError(
            f"Tel2 {action} returned HTTP {status} without JSON"
        ) from err
    if not isinstance(envelope, dict) or "Result" not in envelope:
        raise Tel2ConnectionError(f"Tel2 {action} returned an unexpected response")
    result = str(envelope["Result"])
    message = str(envelope.get("Message") or "")
    if envelope.get("Status") == _SUCCESS:
        if result == "1":
            return envelope.get("Data")
        if result == "0":
            raise Tel2NoData(message)
    if is_auth_message(message):
        raise Tel2AuthError(message)
    raise Tel2ApiError(message, context, action)


def _rows(data: Any) -> list[Mapping[str, Any]]:
    if isinstance(data, Mapping):
        return [data] if data else []
    if isinstance(data, list):
        return [row for row in data if isinstance(row, Mapping)]
    return []


def _first_row(data: Any) -> Mapping[str, Any]:
    rows = _rows(data)
    if not rows:
        raise Tel2ConnectionError("Tel2 returned an unexpected response")
    return rows[0]


def _text(row: Mapping[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None or isinstance(value, Mapping | list):
        return None
    return str(value).strip() or None


def _extension(row: Mapping[str, Any]) -> str | None:
    extension = _text(row, "ExtensionNumber")
    return None if extension == _NO_EXTENSION else extension


def _required_text(row: Mapping[str, Any], key: str) -> str:
    value = _text(row, key)
    if value is None:
        raise Tel2ConnectionError(f"Tel2 response has no {key}")
    return value


def _decimal(row: Mapping[str, Any], key: str) -> Decimal:
    try:
        value = Decimal(_required_text(row, key))
    except InvalidOperation as err:
        raise Tel2ConnectionError(f"Tel2 {key} is not a number") from err
    if not value.is_finite():
        raise Tel2ConnectionError(f"Tel2 {key} is not a number")
    return value


def _int(row: Mapping[str, Any], key: str) -> int:
    return int(_decimal(row, key))


def _canonical(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return canonical_number(raw)
    except ValueError:
        return None


def is_secret_pref(key: str) -> bool:
    """Return whether a pref holds a secret that must never be sent or shown."""
    lowered = key.lower()
    return lowered in SECRET_PREFS or any(
        pattern in lowered for pattern in _SECRET_KEY_PATTERNS
    )


def _is_private_key(key: str) -> bool:
    lowered = key.lower()
    if lowered == PREF_FORWARD_ALWAYS_NUMBER:
        return False
    return (
        lowered in _DROPPED_PREFS
        or is_secret_pref(lowered)
        or any(pattern in lowered for pattern in _PERSONAL_KEY_PATTERNS)
    )


def _is_public_pref(key: str, value: Any, canonical: str) -> bool:
    if value is None or isinstance(value, Mapping | list):
        return False
    # Checked first: "voicemailmailbox" contains "email".
    if key.lower() in _OWN_NUMBER_PREFS:
        return _canonical(str(value)) == canonical
    return not _is_private_key(key)


def _public_prefs(row: Mapping[str, Any], canonical: str) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in row.items()
        if _is_public_pref(str(key), value, canonical)
    }
