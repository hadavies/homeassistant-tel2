"""Tel2 call event manager: public URL, Tel2 registration and the HA webhook."""

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
import logging
import secrets
from typing import TYPE_CHECKING, Any, Final, Literal

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.network import NoURLAvailableError
from yarl import URL

from .api import Tel2ApiError, Tel2Client, Tel2Error
from .const import (
    CONF_CLOUDHOOK_URL,
    CONF_TEL2_WEBHOOK_ID,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DOMAIN,
)
from .models import WebhookRegistration, WebhookSpec

if TYPE_CHECKING:
    from homeassistant.components.cloud import CloudConnectionState

    from .runtime import Tel2ConfigEntry

_LOGGER = logging.getLogger(__name__)

SECRET_HEADER: Final = "X-Tel2-Signature"  # noqa: S105
ISSUE_NO_PUBLIC_URL: Final = "call_events_no_public_url"

type UrlSource = Literal["cloud", "external", "none"]


@dataclass(frozen=True, slots=True)
class CallEventStatus:
    """Read-only call event status for diagnostics."""

    url_source: UrlSource
    registered_on_tel2: bool
    last_event_at: datetime | None


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
        self, hass: HomeAssistant, entry: Tel2ConfigEntry, client: Tel2Client
    ) -> None:
        """Initialise the manager; nothing is registered until async_start."""
        self.hass = hass
        self.entry = entry
        self.client = client
        self.available = False
        self._url_source: UrlSource = "none"
        self._registered = False
        self._last_event_at: datetime | None = None
        self._tel2_failing = False
        self._lock = asyncio.Lock()
        self._unsub_cloud: Callable[[], None] | None = None

    @property
    def webhook_id(self) -> str:
        """Return the HA webhook ID."""
        return str(self.entry.data[CONF_WEBHOOK_ID])

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

    async def async_reconcile(self) -> None:
        """Resolve the public URL and make the Tel2 registration match it."""
        async with self._lock:
            url = await self._async_resolve_url()
            if url is None:
                self.available = False
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    self._issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_NO_PUBLIC_URL,
                )
                return
            self.available = True
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
            await self._async_register_on_tel2(url)

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
            if existing is not None and existing.url == url:
                tel2_id: str | None = existing.tel2_id
            elif existing is not None:
                await self.client.async_add_webhook(spec, edit_id=existing.tel2_id)
                tel2_id = existing.tel2_id
            else:
                await self.client.async_add_webhook(spec)
                added = _find(await self.client.async_get_webhooks(), stored_id, name)
                tel2_id = None if added is None else added.tel2_id
                if added is None:
                    _LOGGER.warning(
                        "Tel2 accepted the call event registration %s but does not list it; retrying on the next refresh",
                        name,
                    )
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
        self._registered = tel2_id is not None
        if tel2_id is not None and self.entry.data.get(CONF_TEL2_WEBHOOK_ID) != tel2_id:
            _update_data(
                self.hass,
                self.entry,
                {**self.entry.data, CONF_TEL2_WEBHOOK_ID: tel2_id},
            )

    async def _async_cloud_connection_changed(
        self, state: CloudConnectionState
    ) -> None:
        _LOGGER.debug("Cloud connection changed to %s", state)
        await self.async_reconcile()

    async def _async_handle_call_event_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        """Handle a Tel2 call event POST; Tel2 always gets a 200."""
        return web.Response(status=200)


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
    if (tel2_id := entry.data.get(CONF_TEL2_WEBHOOK_ID)) is not None:
        try:
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
