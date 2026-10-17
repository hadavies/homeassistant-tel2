"""Tel2 binary sensors."""

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import Tel2AccountCoordinator, Tel2LinesCoordinator
from .entity import Tel2AccountEntity, Tel2LineEntity, async_add_line_entities
from .models import Tel2Number
from .runtime import Tel2ConfigEntry

PARALLEL_UPDATES = 0

API_CONNECTED = BinarySensorEntityDescription(
    key="api_connected",
    translation_key="api_connected",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)

REGISTERED = BinarySensorEntityDescription(
    key="registered",
    translation_key="registered",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Tel2ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tel2 binary sensors."""
    runtime = entry.runtime_data
    async_add_entities([Tel2ApiConnectedSensor(runtime.account, runtime.lines)])

    def registered_sensors(number: Tel2Number) -> list[Tel2RegisteredSensor]:
        return [Tel2RegisteredSensor(runtime.lines, number, REGISTERED)]

    async_add_line_entities(entry, async_add_entities, registered_sensors)


class Tel2ApiConnectedSensor(Tel2AccountEntity, BinarySensorEntity):
    """Whether the last refresh of both coordinators reached Tel2."""

    def __init__(
        self, account: Tel2AccountCoordinator, lines: Tel2LinesCoordinator
    ) -> None:
        """Initialise the sensor."""
        super().__init__(account, API_CONNECTED)
        self._lines = lines

    @property
    def available(self) -> bool:
        """Stay available so a failure shows as off rather than unavailable."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether both coordinators last refreshed successfully."""
        return self.coordinator.last_update_success and self._lines.last_update_success

    async def async_added_to_hass(self) -> None:
        """Also follow the lines coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(self._lines.async_add_listener(self.async_write_ha_state))


class Tel2RegisteredSensor(Tel2LineEntity[Tel2LinesCoordinator], BinarySensorEntity):
    """Whether a phone or app is registered to the number."""

    @property
    def is_on(self) -> bool | None:
        """Return whether the number is registered."""
        line = self.coordinator.data.lines.get(self.canonical)
        return line.registered if line else None
