"""Config flow for the Tel2 integration."""

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class Tel2ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tel2."""

    VERSION = 1
