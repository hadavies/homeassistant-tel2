"""Tel2 line setting switches."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Tel2Client, Tel2Error
from .const import (
    DOMAIN,
    PREF_ANONYMOUS_REJECT,
    PREF_DO_NOT_DISTURB,
    PREF_FORWARD_ALWAYS,
    PREF_FORWARD_ALWAYS_NUMBER,
    PREF_RECORDING_ENABLED,
    PREF_VALUE_OFF,
    PREF_VALUE_ON,
)
from .coordinator import Tel2LinesCoordinator
from .entity import Tel2LineEntity, async_add_line_entities
from .models import Tel2Number
from .runtime import Tel2ConfigEntry

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class Tel2SwitchDescription(SwitchEntityDescription):
    """Describes a line setting switch backed by one Tel2 pref."""

    pref: str
    on_value: str
    off_value: str
    is_on_fn: Callable[[dict[str, str]], bool]
    # Checked before turning on; returns the translation key of the refusal.
    precondition_fn: Callable[[dict[str, str]], str | None] = lambda prefs: None
    write_fn: Callable[[Tel2Client, Tel2Number, bool], Awaitable[None]] | None = None


def _pref_is(pref: str, value: str) -> Callable[[dict[str, str]], bool]:
    return lambda prefs: prefs.get(pref) == value


def _forward_destination_set(prefs: dict[str, str]) -> str | None:
    if prefs.get(PREF_FORWARD_ALWAYS_NUMBER, "").strip():
        return None
    return "forward_destination_required"


SWITCHES: tuple[Tel2SwitchDescription, ...] = (
    Tel2SwitchDescription(
        key="do_not_disturb",
        translation_key="do_not_disturb",
        pref=PREF_DO_NOT_DISTURB,
        on_value=PREF_VALUE_ON,
        off_value=PREF_VALUE_OFF,
        is_on_fn=_pref_is(PREF_DO_NOT_DISTURB, PREF_VALUE_ON),
    ),
    Tel2SwitchDescription(
        key="call_forward_always",
        translation_key="call_forward_always",
        pref=PREF_FORWARD_ALWAYS,
        on_value=PREF_VALUE_ON,
        off_value=PREF_VALUE_OFF,
        is_on_fn=_pref_is(PREF_FORWARD_ALWAYS, PREF_VALUE_ON),
        precondition_fn=_forward_destination_set,
    ),
    Tel2SwitchDescription(
        key="anonymous_reject",
        translation_key="anonymous_reject",
        pref=PREF_ANONYMOUS_REJECT,
        on_value=PREF_VALUE_ON,
        off_value=PREF_VALUE_OFF,
        is_on_fn=_pref_is(PREF_ANONYMOUS_REJECT, PREF_VALUE_ON),
    ),
    Tel2SwitchDescription(
        key="call_recording",
        translation_key="call_recording",
        pref=PREF_RECORDING_ENABLED,
        on_value=PREF_VALUE_ON,
        off_value=PREF_VALUE_OFF,
        is_on_fn=_pref_is(PREF_RECORDING_ENABLED, PREF_VALUE_ON),
        write_fn=lambda client, number, on: client.async_set_recording(number, on),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Tel2ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tel2 line setting switches."""
    lines = entry.runtime_data.lines
    async_add_line_entities(
        entry,
        async_add_entities,
        lambda number: [
            Tel2Switch(lines, number, description) for description in SWITCHES
        ],
    )


class Tel2Switch(Tel2LineEntity[Tel2LinesCoordinator], SwitchEntity):
    """A line setting of one number."""

    entity_description: Tel2SwitchDescription

    @property
    def is_on(self) -> bool | None:
        """Return whether the setting is on."""
        line = self.coordinator.data.lines.get(self.canonical)
        return self.entity_description.is_on_fn(line.prefs) if line else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the setting on."""
        await self._async_write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the setting off."""
        await self._async_write(False)

    async def _async_write(self, on: bool) -> None:
        description = self.entity_description
        line = self.coordinator.data.lines.get(self.canonical)
        prefs = line.prefs if line else {}
        if on and (refusal := description.precondition_fn(prefs)):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key=refusal
            )
        number = self._account.data.numbers[self.canonical]
        value = description.on_value if on else description.off_value
        client = self.coordinator.client
        try:
            if description.write_fn is not None:
                await description.write_fn(client, number, on)
            else:
                await client.async_set_pref(number, description.pref, value)
        except Tel2Error as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="set_failed"
            ) from err
        await self.coordinator.async_apply_optimistic(
            self.canonical, description.pref, value
        )
