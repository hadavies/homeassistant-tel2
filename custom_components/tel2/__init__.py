"""The Tel2 integration."""

from typing import Final

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import Tel2Client
from .const import CONF_LOGIN, CONF_PASSWORD, CONF_TOKEN, DOMAIN
from .coordinator import Tel2AccountCoordinator, Tel2LinesCoordinator
from .runtime import Tel2ConfigEntry, Tel2RuntimeData

PLATFORMS: Final[list[Platform]] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]


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

    entry.runtime_data = Tel2RuntimeData(
        client,
        account,
        lines,
        call_events=None,
        account_device_id=account.account_device_id,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Tel2ConfigEntry) -> bool:
    """Unload a Tel2 config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


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
