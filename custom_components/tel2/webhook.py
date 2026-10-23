"""Tel2 call event manager: public URL, Tel2 registration and the HA webhook."""

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hmac
import logging
import secrets
from typing import TYPE_CHECKING, Any, Final, Literal

from aiohttp import ClientError, web
from homeassistant.components import webhook
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.util import dt as dt_util
from yarl import URL

from .api import Tel2ApiError, Tel2Client, Tel2Error
from .const import (
    CONF_CLOUDHOOK_URL,
    CONF_TEL2_WEBHOOK_ID,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DOMAIN,
    TEL2_IGNORED_STATES,
    TEL2_STATE_MAP,
)
from .models import AccountState, CallEvent, WebhookRegistration, WebhookSpec
from .numbering import canonical_number

if TYPE_CHECKING:
    from homeassistant.components.cloud import CloudConnectionState

    from .coordinator import Tel2AccountCoordinator
    from .runtime import Tel2ConfigEntry

_LOGGER = logging.getLogger(__name__)

SECRET_HEADER: Final = "X-Tel2-Signature"  # noqa: S105
ISSUE_NO_PUBLIC_URL: Final = "call_events_no_public_url"
TEL2_TIME_FORMAT: Final = "%Y-%m-%d %H:%M:%S"
UNKNOWN_PARTY: Final = "unknown"
OTHER_PARTY_MAX_LENGTH: Final = 32
CALL_ID_MAX_LENGTH: Final = 64
MAX_DURATION_DIGITS: Final = 9
ADD_RETRY_WINDOW: Final = timedelta(hours=6)
_DIRECTIONS: Final[Mapping[str, Literal["inbound", "outbound"]]] = {
    "received": "inbound",
    "dialed": "outbound",
}

type UrlSource = Literal["cloud", "external", "none"]


@dataclass(frozen=True, slots=True)
class CallEventStatus:
    """Read-only call event status for diagnostics."""

    url_source: UrlSource
    registered_on_tel2: bool
    last_event_at: datetime | None


def signal_call_event(entry_id: str) -> str:
    """Return the dispatcher signal carrying an entry's call events."""
    return f"{DOMAIN}_{entry_id}_call_event"


def signal_call_event_availability(entry_id: str) -> str:
    """Return the dispatcher signal sent when call event availability changes."""
    return f"{DOMAIN}_{entry_id}_call_event_availability"


def registration_name(entry: Tel2ConfigEntry) -> str:
    """Return the name of this entry's webhook registration on Tel2."""
    return f"homeassistant-tel2-{entry.entry_id}"


# Importing the cloud integration pulls in dependencies a Core install may lack,
# so it is imported lazily and only once the integration is loaded.
def _cloud_loaded(hass: HomeAssistant) -> bool:
    return "cloud" in hass.config.components


def _update_data(
    hass: HomeAssistant, entry: Tel2ConfigEntry, data: Mapping[str, Any]
) -> None:
    hass.config_entries.async_update_entry(entry, data=dict(data))


class Tel2CallEventManager:
    """Keep the HA webhook, the public URL and the Tel2 registration in step."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: Tel2ConfigEntry,
        client: Tel2Client,
        account: Tel2AccountCoordinator,
    ) -> None:
        """Initialise the manager; nothing is registered until async_start."""
        self.hass = hass
        self.entry = entry
        self.client = client
        self.account = account
        self.available = False
        self._url_source: UrlSource = "none"
        self._registered = CONF_TEL2_WEBHOOK_ID in entry.data
        self._last_event_at: datetime | None = None
        self._add_unlisted_at: datetime | None = None
        self._tel2_failing = False
        self._lock = asyncio.Lock()
        self._unsub_cloud: Callable[[], None] | None = None

    @property
    def webhook_id(self) -> str:
        """Return the HA webhook ID."""
        return str(self.entry.data[CONF_WEBHOOK_ID])

    @property
    def registered(self) -> bool:
        """Return whether Tel2 is known to hold our registration."""
        return self._registered

    @property
    def status(self) -> CallEventStatus:
        """Return the call event status."""
        return CallEventStatus(
            url_source=self._url_source,
            registered_on_tel2=self._registered,
            last_event_at=self._last_event_at,
        )

    async def async_start(self) -> None:
        """Register the HA webhook, then resolve the URL and reconcile Tel2."""
        missing: dict[str, str] = {}
        if CONF_WEBHOOK_ID not in self.entry.data:
            missing[CONF_WEBHOOK_ID] = webhook.async_generate_id()
        if CONF_WEBHOOK_SECRET not in self.entry.data:
            # Tel2 only accepts an alphanumeric secret token.
            missing[CONF_WEBHOOK_SECRET] = secrets.token_hex(32)
        if missing:
            _update_data(self.hass, self.entry, {**self.entry.data, **missing})

        webhook.async_register(
            self.hass,
            DOMAIN,
            f"Tel2 call events ({self.entry.title})",
            self.webhook_id,
            self._async_handle_call_event_webhook,
            local_only=False,
            allowed_methods=["POST"],
        )
        try:
            if _cloud_loaded(self.hass):
                from homeassistant.components import cloud  # noqa: PLC0415

                self._unsub_cloud = cloud.async_listen_connection_change(
                    self.hass, self._async_cloud_connection_changed
                )
            await self.async_reconcile()
        except BaseException:
            self.async_unload()
            raise

    def async_unload(self) -> None:
        """Release the HA webhook and cloud listener; keep the Tel2 registration."""
        webhook.async_unregister(self.hass, self.webhook_id)
        if self._unsub_cloud is not None:
            self._unsub_cloud()
            self._unsub_cloud = None
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        self._set_available(False)

    async def async_reconcile(self) -> None:
        """Resolve the public URL and make the Tel2 registration match it."""
        async with self._lock:
            url = await self._async_resolve_url()
            if url is None:
                self._set_available(False)
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    self._issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_NO_PUBLIC_URL,
                )
                return
            self._set_available(True)
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
            await self._async_register_on_tel2(url)

    def _set_available(self, available: bool) -> None:
        if available == self.available:
            return
        self.available = available
        async_dispatcher_send(
            self.hass, signal_call_event_availability(self.entry.entry_id)
        )

    def _set_registered(self, registered: bool) -> None:
        if registered == self._registered:
            return
        self._registered = registered
        async_dispatcher_send(
            self.hass, signal_call_event_availability(self.entry.entry_id)
        )

    @property
    def _issue_id(self) -> str:
        return f"{ISSUE_NO_PUBLIC_URL}_{self.entry.entry_id}"

    async def _async_resolve_url(self) -> str | None:
        if (url := await self._async_cloud_url()) is not None:
            self._url_source = "cloud"
            return url
        if (url := self._external_url()) is not None:
            self._url_source = "external"
            return url
        self._url_source = "none"
        return None

    async def _async_cloud_url(self) -> str | None:
        if not _cloud_loaded(self.hass):
            return None
        from hass_nabucasa import CloudError  # noqa: PLC0415
        from homeassistant.components import cloud  # noqa: PLC0415

        if not cloud.async_active_subscription(self.hass):
            return None
        if (stored := self.entry.data.get(CONF_CLOUDHOOK_URL)) is not None:
            return str(stored)
        try:
            url = await cloud.async_get_or_create_cloudhook(self.hass, self.webhook_id)
        except (cloud.CloudNotAvailable, CloudError, TimeoutError, ValueError) as err:
            _LOGGER.debug("No cloudhook available: %s", type(err).__name__)
            return None
        _update_data(
            self.hass, self.entry, {**self.entry.data, CONF_CLOUDHOOK_URL: url}
        )
        return url

    def _external_url(self) -> str | None:
        try:
            url = webhook.async_generate_url(
                self.hass, self.webhook_id, allow_internal=False, prefer_external=True
            )
        except NoURLAvailableError:
            return None
        return url if URL(url).scheme == "https" else None

    async def _async_register_on_tel2(self, url: str) -> None:
        name = registration_name(self.entry)
        spec = WebhookSpec(
            name=name,
            url=url,
            secret_header=SECRET_HEADER,
            secret_token=str(self.entry.data[CONF_WEBHOOK_SECRET]),
        )
        stored_id = self.entry.data.get(CONF_TEL2_WEBHOOK_ID)
        try:
            existing = _find(await self.client.async_get_webhooks(), stored_id, name)
            if existing is None:
                tel2_id = await self._async_add_registration(spec, stored_id)
            else:
                self._add_unlisted_at = None
                if existing.url != url:
                    await self.client.async_add_webhook(spec, edit_id=existing.tel2_id)
                tel2_id = existing.tel2_id
        except (Tel2Error, ValueError) as err:
            if not self._tel2_failing:
                _LOGGER.warning(
                    "Could not register Tel2 call events, retrying on the next refresh: %s",
                    err,
                )
            self._tel2_failing = True
            return
        if self._tel2_failing:
            _LOGGER.info("Tel2 call event registration recovered")
        self._tel2_failing = False
        self._set_registered(tel2_id is not None)
        if tel2_id is not None and self.entry.data.get(CONF_TEL2_WEBHOOK_ID) != tel2_id:
            _update_data(
                self.hass,
                self.entry,
                {**self.entry.data, CONF_TEL2_WEBHOOK_ID: tel2_id},
            )

    # Tel2 can accept an add without listing it, so re-adding on every refresh
    # would pile up duplicate registrations.
    async def _async_add_registration(
        self, spec: WebhookSpec, stored_id: str | None
    ) -> str | None:
        now = dt_util.utcnow()
        unlisted_at = self._add_unlisted_at
        if unlisted_at is not None and now - unlisted_at < ADD_RETRY_WINDOW:
            return None
        await self.client.async_add_webhook(spec)
        added = _find(await self.client.async_get_webhooks(), stored_id, spec.name)
        if added is not None:
            self._add_unlisted_at = None
            return added.tel2_id
        if unlisted_at is None:
            _LOGGER.warning(
                "Tel2 accepted the call event registration %s but does not list it; checking again on each refresh",
                spec.name,
            )
        self._add_unlisted_at = now
        return None

    async def _async_cloud_connection_changed(
        self, state: CloudConnectionState
    ) -> None:
        _LOGGER.debug("Cloud connection changed to %s", state)
        await self.async_reconcile()

    async def _async_handle_call_event_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        """Handle a Tel2 call event POST; Tel2 always gets a 200."""
        if not self._signed(request):
            _LOGGER.debug("Dropped a call event without a valid secret")
        elif (payload := await _async_read_json(request)) is None:
            _LOGGER.debug("Dropped a call event that is not valid JSON")
        else:
            self._handle_payload(payload)
        return web.Response(status=200)

    def _signed(self, request: web.Request) -> bool:
        expected = self.entry.data.get(CONF_WEBHOOK_SECRET)
        received = request.headers.get(SECRET_HEADER)
        if not isinstance(expected, str) or not expected or received is None:
            return False
        try:
            encoded = received.encode("utf-8", "surrogateescape")
        except UnicodeError:
            return False
        return hmac.compare_digest(encoded, expected.encode())

    def _handle_payload(self, payload: Any) -> None:
        state = payload.get("state") if isinstance(payload, dict) else None
        if isinstance(state, str) and state in TEL2_IGNORED_STATES:
            _LOGGER.debug("Ignored a call event with an ignored state")
            return
        try:
            event = _parse_call_event(payload)
        except ValueError as err:
            _LOGGER.debug("Dropped a call event: %s", err)
            return
        # Before the event is sent, so the entity is available to fire it.
        self._set_registered(True)
        account: AccountState | None = self.account.data
        if account is None or event.number not in account.numbers:
            _LOGGER.debug("Dropped a call event for a number not on the account")
            return
        self._last_event_at = dt_util.utcnow()
        async_dispatcher_send(self.hass, signal_call_event(self.entry.entry_id), event)


async def _async_read_json(request: web.Request) -> Any:
    try:
        return await request.json()
    except ValueError, ClientError, web.HTTPException:
        return None


# Exception messages are logged, so they name the problem but never a value.
def _parse_call_event(payload: Any) -> CallEvent:
    if not isinstance(payload, dict):
        raise ValueError("payload is not an object")  # noqa: TRY004
    state = _required(payload, "state")
    if (event_type := TEL2_STATE_MAP.get(state)) is None:
        raise ValueError("unknown state")
    direction = _DIRECTIONS.get(_required(payload, "type"))
    if direction is None:
        raise ValueError("unknown call type")
    ours, theirs = ("to", "from") if direction == "inbound" else ("from", "to")
    numbers = {ours: _canonical(payload, ours), theirs: _other_party(payload, theirs)}
    if not (call_id := _printable(payload.get("id"), CALL_ID_MAX_LENGTH)):
        raise ValueError("missing or invalid id")
    origin_id = _printable(payload.get("originid"), CALL_ID_MAX_LENGTH)
    return CallEvent(
        number=numbers[ours],
        event_type=event_type,
        direction=direction,
        from_number=numbers["from"],
        to_number=numbers["to"],
        call_id=call_id,
        origin_id=origin_id or call_id,
        start_time=_start_time(payload.get("start_time")),
        duration=_duration(payload.get("duration")),
    )


def _required(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing or invalid {key}")
    return value


def _canonical(payload: dict[str, Any], key: str) -> str:
    try:
        return canonical_number(_required(payload, key))
    except ValueError:
        raise ValueError(f"missing or invalid {key}") from None


# Withheld and non-numeric callers still matter (a missed call from a withheld
# number), so the other party is kept raw when it is not a phone number.
def _other_party(payload: dict[str, Any], key: str) -> str:
    if not (raw := _printable(payload.get(key))):
        return UNKNOWN_PARTY
    try:
        number = canonical_number(raw)
    except ValueError:
        number = raw
    return number[:OTHER_PARTY_MAX_LENGTH]


def _printable(value: Any, max_length: int | None = None) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value if ch.isprintable()).strip()[:max_length]


def _start_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, TEL2_TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _duration(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.isdecimal() and value.isascii():
        # Checked before int() so a huge string cannot hit the int digit limit.
        return int(value) if len(value) <= MAX_DURATION_DIGITS else None
    if isinstance(value, int) and 0 <= value < 10**MAX_DURATION_DIGITS:
        return value
    return None


def _find(
    registrations: list[WebhookRegistration], tel2_id: str | None, name: str
) -> WebhookRegistration | None:
    by_id = (reg for reg in registrations if reg.tel2_id == tel2_id)
    by_name = (reg for reg in registrations if reg.name == name)
    return next(by_id, None) or next(by_name, None)


async def async_delete_stale_registration(
    hass: HomeAssistant, entry: Tel2ConfigEntry, client: Tel2Client
) -> None:
    """Delete the Tel2 registration left behind after call events were disabled."""
    tel2_id = entry.data.get(CONF_TEL2_WEBHOOK_ID)
    if tel2_id is None:
        return
    try:
        await client.async_delete_webhook(str(tel2_id))
    except Tel2ApiError as err:
        if not await _async_registration_gone(client, str(tel2_id)):
            _LOGGER.warning(
                "Could not delete the Tel2 call event registration, retrying on the next setup: %s",
                err,
            )
            return
    except Tel2Error as err:
        _LOGGER.warning(
            "Could not delete the Tel2 call event registration, retrying on the next setup: %s",
            err,
        )
        return
    data = {
        key: value for key, value in entry.data.items() if key != CONF_TEL2_WEBHOOK_ID
    }
    _update_data(hass, entry, data)


async def _async_registration_gone(client: Tel2Client, tel2_id: str) -> bool:
    try:
        registrations = await client.async_get_webhooks()
    except Tel2Error:
        return False
    return all(reg.tel2_id != tel2_id for reg in registrations)


async def async_remove_registration(
    hass: HomeAssistant, entry: Tel2ConfigEntry, client: Tel2Client
) -> None:
    """Delete the Tel2 registration and the cloudhook of a removed entry."""
    tel2_id = entry.data.get(CONF_TEL2_WEBHOOK_ID)
    try:
        # Add-Webhook can succeed without its id ever being stored.
        if tel2_id is None and (
            found := _find(
                await client.async_get_webhooks(), None, registration_name(entry)
            )
        ):
            tel2_id = found.tel2_id
        if tel2_id is not None:
            await client.async_delete_webhook(str(tel2_id))
    except Tel2Error as err:
        _LOGGER.warning(
            "Could not delete the Tel2 call event registration; it may remain on the Tel2 account: %s",
            err,
        )
    if CONF_CLOUDHOOK_URL in entry.data and _cloud_loaded(hass):
        from homeassistant.components import cloud  # noqa: PLC0415

        try:
            await cloud.async_delete_cloudhook(hass, str(entry.data[CONF_WEBHOOK_ID]))
        except cloud.CloudNotAvailable, ValueError:
            _LOGGER.debug("No cloudhook to delete for %s", entry.entry_id)
