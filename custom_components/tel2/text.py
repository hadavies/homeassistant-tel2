"""Tel2 text entities."""

import re
from typing import Final

from homeassistant.components.text import TextEntity, TextEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Tel2Error
from .const import DOMAIN, PREF_FORWARD_ALWAYS_NUMBER, TEL2_COUNTRY_CODE
from .coordinator import Tel2LinesCoordinator
from .entity import Tel2LineEntity, async_add_line_entities
from .models import Tel2Number
from .numbering import canonical_number, is_valid_destination
from .runtime import Tel2ConfigEntry

PARALLEL_UPDATES = 1

DESTINATION_PATTERN: Final = r"^\+?[0-9 ()-]{7,20}$"
DESTINATION_MAX_LENGTH: Final = 20

FORWARD_DESTINATION = TextEntityDescription(
    key="forward_destination",
    translation_key="forward_destination",
    entity_category=EntityCategory.CONFIG,
    native_min=0,
    native_max=DESTINATION_MAX_LENGTH,
    pattern=DESTINATION_PATTERN,
)

_DESTINATION_RE: Final = re.compile(DESTINATION_PATTERN)


def _has_dialling_prefix(raw: str) -> bool:
    # A UK number typed without its 0 would otherwise dial abroad, e.g. 7700... as +7.
    return raw.strip().startswith(("0", "+"))


def tel2_destination(raw: str) -> str:
    """Return a valid destination as Tel2 stores it: UK national, else 00 + digits."""
    digits = canonical_number(raw)
    if digits.startswith(TEL2_COUNTRY_CODE):
        return "0" + digits.removeprefix(TEL2_COUNTRY_CODE)
    return "00" + digits


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Tel2ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tel2 text entities."""
    lines = entry.runtime_data.lines

    def forward_destination(number: Tel2Number) -> list[Tel2ForwardDestination]:
        return [Tel2ForwardDestination(lines, number, FORWARD_DESTINATION)]

    async_add_line_entities(entry, async_add_entities, forward_destination)


class Tel2ForwardDestination(Tel2LineEntity[Tel2LinesCoordinator], TextEntity):
    """The call forward always destination of one number."""

    entity_description: TextEntityDescription

    @property
    def native_value(self) -> str | None:
        """Return the stored destination, or None when unset or unusable."""
        line = self.coordinator.data.lines.get(self.canonical)
        value = line.prefs.get(PREF_FORWARD_ALWAYS_NUMBER) if line else None
        # HA raises on a state outside the pattern or length, breaking every state write.
        if (
            not value
            or len(value) > DESTINATION_MAX_LENGTH
            or not _DESTINATION_RE.match(value)
        ):
            return None
        return value

    async def async_set_value(self, value: str) -> None:
        """Save a new destination on Tel2."""
        if not _has_dialling_prefix(value) or not is_valid_destination(value):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_destination"
            )
        destination = tel2_destination(value)
        number = self._account.data.numbers[self.canonical]
        try:
            await self.coordinator.client.async_set_pref(
                number, PREF_FORWARD_ALWAYS_NUMBER, destination
            )
        except Tel2Error as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="forward_destination_failed"
            ) from err
        await self.coordinator.async_apply_optimistic(
            self.canonical, PREF_FORWARD_ALWAYS_NUMBER, destination
        )
