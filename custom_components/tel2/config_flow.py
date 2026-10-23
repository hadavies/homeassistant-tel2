"""Config flow for the Tel2 integration."""

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any, Final

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import probatio

from .api import Tel2ApiError, Tel2AuthError, Tel2Client, Tel2ConnectionError
from .const import (
    CONF_CALL_EVENTS,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    DEFAULT_CALL_EVENTS,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
)
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

OPTIONS_SCHEMA: Final = probatio.Schema(
    {
        probatio.Required(CONF_SCAN_INTERVAL): probatio.All(
            NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL_MINUTES,
                    max=MAX_SCAN_INTERVAL_MINUTES,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="min",
                )
            ),
            probatio.Coerce(int),
        ),
        probatio.Required(CONF_CALL_EVENTS): BooleanSelector(),
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> Tel2OptionsFlow:
        """Return the options flow for a Tel2 entry."""
        return Tel2OptionsFlow()

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

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication after Tel2 rejected the stored login."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the login again and update the entry for the same account."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            login, password = _clean_credentials(user_input)
            validated = await self._async_validate(
                login, password, errors, placeholders
            )
            if validated is not None:
                await self.async_set_unique_id(validated.details.account_code)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates=_entry_data(login, password, validated),
                )
        suggested = _suggested_values(user_input) or {
            CONF_LOGIN: reauth_entry.data.get(CONF_LOGIN, "")
        }
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, suggested),
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
            _LOGGER.warning(
                "Tel2 refused %s while validating the login: %s",
                err.action,
                err.message,
            )
            errors["base"] = "unknown"
        except Exception:
            _LOGGER.exception("Unexpected error validating the Tel2 login")
            errors["base"] = "unknown"
        return None


class Tel2OptionsFlow(OptionsFlowWithReload):
    """Handle Tel2 options; saving them reloads the entry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the line refresh interval and whether to use call events."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_CALL_EVENTS: bool(user_input[CONF_CALL_EVENTS]),
                }
            )
        current = {
            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_MINUTES,
            CONF_CALL_EVENTS: DEFAULT_CALL_EVENTS,
            **self.config_entry.options,
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, current),
        )


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
