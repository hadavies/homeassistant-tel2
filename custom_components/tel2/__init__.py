"""The Tel2 integration."""

import logging
from typing import Final

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import Tel2Client
from .const import (
    CONF_CALL_EVENTS,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_TEL2_WEBHOOK_ID,
    CONF_TOKEN,
    DEFAULT_CALL_EVENTS,
    DOMAIN,
)
from .coordinator import Tel2AccountCoordinator, Tel2LinesCoordinator
from .runtime import Tel2ConfigEntry, Tel2RuntimeData
from .services import async_setup_services
from .webhook import (
    Tel2CallEventManager,
    async_delete_stale_registration,
    async_remove_registration,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final[list[Platform]] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the Tel2 actions once, whatever the number of entries."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: Tel2ConfigEntry) -> bool:
    """Set up Tel2 from a config entry."""

    @callback
    def _persist_token(token: str) -> None:
        # No update listener is registered, so this never reloads the entry.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TOKEN: token}
        )

    client = Tel2Client(
        async_get_clientsession(hass),
        entry.data[CONF_LOGIN],
        entry.data[CONF_PASSWORD],
        token=entry.data.get(CONF_TOKEN),
        on_token_refreshed=_persist_token,
    )
    account = Tel2AccountCoordinator(hass, entry, client)
    await account.async_config_entry_first_refresh()
    lines = Tel2LinesCoordinator(hass, entry, client, account)
    await lines.async_config_entry_first_refresh()

    call_events: Tel2CallEventManager | None = None
    if entry.options.get(CONF_CALL_EVENTS, DEFAULT_CALL_EVENTS):
        call_events = await _async_start_call_events(hass, entry, client, account)
    elif CONF_TEL2_WEBHOOK_ID in entry.data:
        await async_delete_stale_registration(hass, entry, client)

    entry.runtime_data = Tel2RuntimeData(
        client,
        account,
        lines,
        call_events=call_events,
        account_device_id=account.account_device_id,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


# Call events are optional, so a failure to start them never fails setup: the
# event entities stay unavailable until the next reload.
async def _async_start_call_events(
    hass: HomeAssistant,
    entry: Tel2ConfigEntry,
    client: Tel2Client,
    account: Tel2AccountCoordinator,
) -> Tel2CallEventManager | None:
    manager = Tel2CallEventManager(hass, entry, client, account)
    try:
        await manager.async_start()
    except Exception:
        _LOGGER.exception("Could not start Tel2 call events")
        return None

    @callback
    def _reconcile() -> None:
        entry.async_create_background_task(
            hass, manager.async_reconcile(), "tel2 call event reconcile"
        )

    entry.async_on_unload(account.async_add_refresh_listener(_reconcile))
    return manager


async def async_unload_entry(hass: HomeAssistant, entry: Tel2ConfigEntry) -> bool:
    """Unload a Tel2 config entry; the Tel2 call event registration is kept."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and (call_events := entry.runtime_data.call_events) is not None:
        call_events.async_unload()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: Tel2ConfigEntry) -> None:
    """Delete the Tel2 call event registration of a removed entry."""
    client = Tel2Client(
        async_get_clientsession(hass),
        entry.data[CONF_LOGIN],
        entry.data[CONF_PASSWORD],
        token=entry.data.get(CONF_TOKEN),
    )
    await async_remove_registration(hass, entry, client)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: Tel2ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting a number device only once the number has left the account."""
    if entry.state is not ConfigEntryState.LOADED:
        return False
    account = entry.runtime_data.account.data
    identifiers = {
        value for domain, value in device_entry.identifiers if domain == DOMAIN
    }
    return (
        bool(identifiers)
        and account.details.account_code not in identifiers
        and identifiers.isdisjoint(account.numbers)
    )
