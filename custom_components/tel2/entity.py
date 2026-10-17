"""Base entities and the per-number entity helper for Tel2."""

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import Tel2AccountCoordinator, Tel2LinesCoordinator
from .models import Tel2Number
from .numbering import display_number
from .runtime import Tel2ConfigEntry


class Tel2AccountEntity(CoordinatorEntity[Tel2AccountCoordinator]):
    """An entity on the account device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: Tel2AccountCoordinator, description: EntityDescription
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        account_code = coordinator.data.details.account_code
        plan = coordinator.data.plan
        self._attr_unique_id = f"{account_code}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account_code)},
            manufacturer=MANUFACTURER,
            model=(plan.description or None) if plan else None,
            entry_type=DeviceEntryType.SERVICE,
        )


class Tel2LineEntity[CoordinatorT: Tel2AccountCoordinator | Tel2LinesCoordinator](
    CoordinatorEntity[CoordinatorT]
):
    """An entity on the device of one number."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CoordinatorT,
        number: Tel2Number,
        description: EntityDescription,
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self.canonical = number.canonical
        self._account = (
            coordinator
            if isinstance(coordinator, Tel2AccountCoordinator)
            else coordinator.account
        )
        account_code = self._account.data.details.account_code
        self._attr_unique_id = f"{account_code}_{number.canonical}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, number.canonical)},
            manufacturer=MANUFACTURER,
            name=number.name or display_number(number.canonical),
            via_device_id=self._account.account_device_id,
        )

    @property
    def available(self) -> bool:
        """Return whether the number is still on the account and has data."""
        if not super().available or self.canonical not in self._account.data.numbers:
            return False
        if isinstance(self.coordinator, Tel2LinesCoordinator):
            return self.canonical in self.coordinator.data.lines
        return True

    async def async_added_to_hass(self) -> None:
        """Also follow the account coordinator, which decides availability."""
        await super().async_added_to_hass()
        if self.coordinator is not self._account:
            self.async_on_remove(
                self._account.async_add_listener(self.async_write_ha_state)
            )


@callback
def async_add_line_entities(
    entry: Tel2ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[Tel2Number], Iterable[Entity]],
) -> None:
    """Add entities for every number now and for numbers that join the account."""
    account = entry.runtime_data.account
    known: set[str] = set()

    @callback
    def _add_new_numbers() -> None:
        numbers = account.data.numbers
        if numbers:
            # Forget numbers that left so they are re-added if they come back. An
            # empty list keeps its devices (see the coordinator), so keep them known.
            known.intersection_update(numbers)
        new = [
            number for canonical, number in numbers.items() if canonical not in known
        ]
        known.update(number.canonical for number in new)
        if entities := [entity for number in new for entity in factory(number)]:
            async_add_entities(entities)

    _add_new_numbers()
    entry.async_on_unload(account.async_add_listener(_add_new_numbers))
