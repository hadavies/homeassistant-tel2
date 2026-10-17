"""Config flow for the Tel2 integration."""

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any, Final

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import probatio

from .api import Tel2ApiError, Tel2AuthError, Tel2Client, Tel2ConnectionError
from .const import CONF_LOGIN, CONF_PASSWORD, CONF_TOKEN, DOMAIN
from .models import AccessContext, CustomerDetails

_LOGGER = logging.getLogger(__name__)

REQUIRED_ACCESS_LEVEL: Final = "Full"
CONTEXT_NAMES: Final[Mapping[AccessContext, str]] = {
    "account": "account",
    "voice": "voice",
    "api": "API",
}

USER_SCHEMA: Final = probatio.Schema(
    {
        probatio.Required(CONF_LOGIN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username")
        ),
        probatio.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)


class InsufficientAccess(Exception):  # noqa: N818
    """The login works but Tel2 refused some contexts the integration needs."""

    def __init__(self, refused: set[AccessContext]) -> None:
        """Store the refused contexts."""
        super().__init__(", ".join(sorted(refused)))
        self.refused = refused


@dataclass(frozen=True, slots=True)
class ValidatedLogin:
    """A login Tel2 accepted with every context the integration needs."""

    details: CustomerDetails
    token: str


async def validate_access(
    hass: HomeAssistant, login: str, password: str
) -> ValidatedLogin:
    """Sign in to Tel2, read the account and check the login can use every context."""
    client = Tel2Client(async_get_clientsession(hass), login, password)
    token = await client.async_authenticate()
    # Probe first so a context refusal is reported as access, not a generic error.
    if refused := await client.async_probe_access():
        raise InsufficientAccess(refused)
    details = await client.async_get_customer_details()
    return ValidatedLogin(details, token)


class Tel2ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tel2."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a portal login and create the entry once Tel2 accepts it."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            login, password = _clean_credentials(user_input)
            validated = await self._async_validate(
                login, password, errors, placeholders
            )
            if validated is not None:
                await self.async_set_unique_id(validated.details.account_code)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=_entry_title(validated.details),
                    data=_entry_data(login, password, validated),
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, _suggested_values(user_input)
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _async_validate(
        self,
        login: str,
        password: str,
        errors: dict[str, str],
        placeholders: dict[str, str],
    ) -> ValidatedLogin | None:
        """Validate a submitted login, filling errors and placeholders on failure."""
        if not login or not password.strip():
            errors["base"] = "invalid_auth"
            return None
        try:
            return await validate_access(self.hass, login, password)
        except Tel2AuthError:
            errors["base"] = "invalid_auth"
        except Tel2ConnectionError:
            errors["base"] = "cannot_connect"
        except InsufficientAccess as err:
            errors["base"] = "insufficient_access"
            placeholders["contexts"] = ", ".join(
                sorted(
                    (CONTEXT_NAMES[context] for context in err.refused),
                    key=str.casefold,
                )
            )
            placeholders["level"] = REQUIRED_ACCESS_LEVEL
        except Tel2ApiError as err:
            _LOGGER.warning("Tel2 refused %s during setup: %s", err.action, err.message)
            errors["base"] = "unknown"
        except Exception:
            _LOGGER.exception("Unexpected error validating the Tel2 login")
            errors["base"] = "unknown"
        return None


def _clean_credentials(user_input: Mapping[str, Any]) -> tuple[str, str]:
    return str(user_input[CONF_LOGIN]).strip(), str(user_input[CONF_PASSWORD])


def _suggested_values(user_input: Mapping[str, Any] | None) -> dict[str, Any]:
    if user_input is None:
        return {}
    return {CONF_LOGIN: str(user_input[CONF_LOGIN]).strip()}


def _entry_title(details: CustomerDetails) -> str:
    return f"{details.customer_name} ({details.account_code})"


def _entry_data(login: str, password: str, validated: ValidatedLogin) -> dict[str, str]:
    return {CONF_LOGIN: login, CONF_PASSWORD: password, CONF_TOKEN: validated.token}
