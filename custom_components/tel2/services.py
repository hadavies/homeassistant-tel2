"""The tel2.set_line_setting action."""

from typing import Final

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import (
    DOMAIN as HOMEASSISTANT_DOMAIN,
    HomeAssistant,
    ServiceCall,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
import probatio

from .api import Tel2ApiError, Tel2Error, is_secret_pref
from .const import DOMAIN
from .runtime import Tel2ConfigEntry

SERVICE_SET_LINE_SETTING: Final = "set_line_setting"
ATTR_PREF: Final = "pref"
ATTR_VALUE: Final = "value"

SET_LINE_SETTING_SCHEMA: Final = probatio.Schema(
    {
        probatio.Required(ATTR_DEVICE_ID): cv.string,
        probatio.Required(ATTR_PREF): probatio.All(
            cv.string, probatio.Strip, probatio.Length(min=1)
        ),
        probatio.Required(ATTR_VALUE): cv.string,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Tel2 actions."""

    async def async_set_line_setting(call: ServiceCall) -> None:
        pref: str = call.data[ATTR_PREF]
        value: str = call.data[ATTR_VALUE]
        entry, canonical = _resolve_line(hass, call.data[ATTR_DEVICE_ID])
        if is_secret_pref(pref):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="secret_pref"
            )
        runtime = entry.runtime_data
        number = runtime.account.data.numbers[canonical]
        try:
            await runtime.client.async_set_pref(number, pref, value)
        except Tel2ApiError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="tel2_rejected",
                translation_placeholders={"message": err.message},
            ) from err
        except Tel2Error as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="set_failed"
            ) from err
        await runtime.lines.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_LINE_SETTING,
        async_set_line_setting,
        schema=SET_LINE_SETTING_SCHEMA,
    )


@callback
def _resolve_line(hass: HomeAssistant, device_id: str) -> tuple[Tel2ConfigEntry, str]:
    """Return the loaded entry and canonical number of a Tel2 number device."""
    device, entry = dr.async_get_device_and_config_entry_for_domain(
        hass, device_id, domain=DOMAIN, include_child_devices=False
    )
    if device is None or entry is None:
        raise _not_a_line()
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=HOMEASSISTANT_DOMAIN,
            translation_key="service_config_entry_not_loaded",
            translation_placeholders={"domain": DOMAIN, "entry_title": entry.title},
        )
    numbers = entry.runtime_data.account.data.numbers
    for domain, identifier in device.identifiers:
        if domain == DOMAIN and identifier in numbers:
            return entry, identifier
    raise _not_a_line()


def _not_a_line() -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN, translation_key="not_a_line"
    )
