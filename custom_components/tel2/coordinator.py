"""Data update coordinators for the Tel2 integration."""

import asyncio
from collections.abc import Iterable
from dataclasses import replace
from datetime import timedelta
import logging
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Tel2ApiError, Tel2AuthError, Tel2Client, Tel2ConnectionError, Tel2Error
from .const import (
    ACCOUNT_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MANUFACTURER,
)
from .models import (
    AccountState,
    BundleUsage,
    CustomerDetails,
    LinesState,
    LineState,
    Tel2Number,
)

_LOGGER = logging.getLogger(__name__)

MAX_BUNDLE_CALLS: Final = 4
AUTH_FAILED_FALLBACK: Final = "Tel2 rejected the stored login"


class Tel2AccountCoordinator(DataUpdateCoordinator[AccountState]):
    """Refresh account details, balance, plan, numbers and bundle usage."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: Tel2Client
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="tel2 account",
            update_interval=ACCOUNT_SCAN_INTERVAL,
            always_update=False,
        )
        self.client = client
        self._account_device_id: str | None = None
        self._known_numbers: set[str] | None = None
        self._warned_no_numbers = False

    @property
    def account_device_id(self) -> str:
        """Return the registry ID of the account device."""
        if self._account_device_id is None:
            raise RuntimeError("The account device is created by the first refresh")
        return self._account_device_id

    async def _async_setup(self) -> None:
        """Create or update the account device before the first refresh."""
        try:
            async with asyncio.TaskGroup() as group:
                details = group.create_task(self.client.async_get_customer_details())
                plan = group.create_task(self.client.async_get_current_plan())
        except ExceptionGroup as err:
            raise _update_error(err) from err
        current_plan = plan.result()
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.config_entry.entry_id,
            identifiers={(DOMAIN, details.result().account_code)},
            manufacturer=MANUFACTURER,
            entry_type=dr.DeviceEntryType.SERVICE,
            name=_account_name(details.result()),
            model=(current_plan.description or None) if current_plan else None,
        )
        self._account_device_id = device.id

    async def _async_update_data(self) -> AccountState:
        """Fetch the account state from Tel2."""
        try:
            state = await self._async_fetch()
        except (Tel2Error, ExceptionGroup) as err:
            raise _update_error(err) from err
        self._async_remove_stale_devices(state)
        return state

    async def _async_fetch(self) -> AccountState:
        async with asyncio.TaskGroup() as group:
            details = group.create_task(self.client.async_get_customer_details())
            balance = group.create_task(self.client.async_get_account_balance())
            plan = group.create_task(self.client.async_get_current_plan())
            numbers = group.create_task(self.client.async_get_numbers())
        return AccountState(
            details=details.result(),
            balance=balance.result(),
            plan=plan.result(),
            numbers={number.canonical: number for number in numbers.result()},
            bundles=await self._async_fetch_bundles(numbers.result()),
        )

    async def _async_fetch_bundles(
        self, numbers: Iterable[Tel2Number]
    ) -> dict[str, BundleUsage]:
        semaphore = asyncio.Semaphore(MAX_BUNDLE_CALLS)

        async def fetch(number: Tel2Number) -> BundleUsage | None:
            async with semaphore:
                return await self.client.async_get_line_bundle_usage(number)

        async with asyncio.TaskGroup() as group:
            tasks = {
                number.canonical: group.create_task(fetch(number)) for number in numbers
            }
        return {
            canonical: usage
            for canonical, task in tasks.items()
            if (usage := task.result()) is not None
        }

    def _async_remove_stale_devices(self, state: AccountState) -> None:
        registry = dr.async_get(self.hass)
        entry_id = self.config_entry.entry_id
        previous = self._known_numbers
        if previous is None:
            # Numbers removed while HA was stopped only show up in the registry.
            previous = {
                value
                for device in dr.async_entries_for_config_entry(registry, entry_id)
                for domain, value in device.identifiers
                if domain == DOMAIN
            }
        previous = previous - {state.details.account_code}
        current = set(state.numbers)
        if previous and not current:
            # Likely a Tel2 glitch: keep the devices; an account really left with
            # no numbers keeps unavailable devices the user can delete.
            if not self._warned_no_numbers:
                _LOGGER.warning("Tel2 returned no numbers; keeping the number devices")
                self._warned_no_numbers = True
            self._known_numbers = previous
            return
        self._warned_no_numbers = False
        for canonical in previous - current:
            device = registry.async_get_device_by_identifier(
                (DOMAIN, canonical), entry_id
            )
            if device is not None:
                registry.async_remove_device(device.id)
        self._known_numbers = current


class Tel2LinesCoordinator(DataUpdateCoordinator[LinesState]):
    """Refresh registration and prefs for every number on the account."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: Tel2Client,
        account: Tel2AccountCoordinator,
    ) -> None:
        """Initialise the coordinator."""
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="tel2 lines",
            update_interval=timedelta(minutes=minutes),
            always_update=False,
        )
        self.client = client
        self.account = account

    async def _async_update_data(self) -> LinesState:
        """Fetch registration and prefs, keeping only the account's numbers."""
        # HA types data as never None, but it is until the first refresh.
        account: AccountState | None = self.account.data
        if account is None:
            return LinesState(lines={})
        try:
            async with asyncio.TaskGroup() as group:
                status = group.create_task(self.client.async_get_subscriber_status())
                prefs = group.create_task(self.client.async_get_all_prefs())
        except (Tel2Error, ExceptionGroup) as err:
            raise _update_error(err) from err
        registered = status.result()
        line_prefs = prefs.result()
        return LinesState(
            lines={
                canonical: LineState(
                    registered=canonical in registered,
                    prefs=dict(line_prefs.get(canonical, {})),
                )
                for canonical in account.numbers
            }
        )

    async def async_apply_optimistic(
        self, canonical: str, pref: str, value: str
    ) -> None:
        """Show a pref Tel2 has accepted straight away, then refresh to confirm."""
        current: LinesState | None = self.data
        if current is not None and (line := current.lines.get(canonical)):
            updated = replace(line, prefs={**line.prefs, pref: value})
            self.async_set_updated_data(
                LinesState(lines={**current.lines, canonical: updated})
            )
        await self.async_request_refresh()


def _account_name(details: CustomerDetails) -> str:
    return details.company_name or details.customer_name or details.account_code


def _update_error(err: BaseException) -> BaseException:
    """Map a Tel2 error to the exception the coordinator expects."""
    if isinstance(err, BaseExceptionGroup):
        for kind in (Tel2AuthError, Tel2ConnectionError):
            if (matching := err.subgroup(kind)) is not None:
                return _update_error(matching.exceptions[0])
        return _update_error(err.exceptions[0])
    if isinstance(err, Tel2AuthError):
        return ConfigEntryAuthFailed(str(err) or AUTH_FAILED_FALLBACK)
    if isinstance(err, Tel2ConnectionError):
        return UpdateFailed(
            translation_domain=DOMAIN, translation_key="update_cannot_connect"
        )
    if isinstance(err, Tel2Error):
        message = err.message if isinstance(err, Tel2ApiError) else str(err)
        return UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="update_tel2_error",
            translation_placeholders={"message": message},
        )
    return err
