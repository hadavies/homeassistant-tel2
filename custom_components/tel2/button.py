"""Tel2 line buttons."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.components.button.const import ButtonDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Tel2Client, Tel2Error
from .const import DOMAIN
from .coordinator import Tel2AccountCoordinator
from .entity import Tel2LineEntity, async_add_line_entities
from .models import Tel2Number
from .runtime import Tel2ConfigEntry

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class Tel2ButtonDescription(ButtonEntityDescription):
    """Describes a command button on a number device."""

    press_fn: Callable[[Tel2Client, Tel2Number], Awaitable[None]]
    error_key: str


BUTTONS: tuple[Tel2ButtonDescription, ...] = (
    Tel2ButtonDescription(
        key="resync_line",
        translation_key="resync_line",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda client, number: client.async_resync_line(number),
        error_key="resync_failed",
    ),
    Tel2ButtonDescription(
        key="reboot_phone",
        translation_key="reboot_phone",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda client, number: client.async_reboot_phone(number),
        error_key="reboot_failed",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Tel2ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tel2 line buttons."""
    account = entry.runtime_data.account
    async_add_line_entities(
        entry,
        async_add_entities,
        lambda number: (
            Tel2Button(account, number, description) for description in BUTTONS
        ),
    )


class Tel2Button(Tel2LineEntity[Tel2AccountCoordinator], ButtonEntity):
    """A Tel2 command for one number."""

    entity_description: Tel2ButtonDescription

    async def async_press(self) -> None:
        """Send the command to Tel2."""
        number = self.coordinator.data.numbers[self.canonical]
        try:
            await self.entity_description.press_fn(self.coordinator.client, number)
        except Tel2Error as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=self.entity_description.error_key,
            ) from err
