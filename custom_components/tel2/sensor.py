"""Tel2 sensors."""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import TEL2_CURRENCY
from .coordinator import Tel2AccountCoordinator
from .entity import Tel2AccountEntity, Tel2LineEntity, async_add_line_entities
from .models import AccountState, BundleUsage, Tel2Number
from .runtime import Tel2ConfigEntry

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class Tel2AccountSensorDescription(SensorEntityDescription):
    """Describes a sensor on the account device."""

    value_fn: Callable[[AccountState], StateType | Decimal]


@dataclass(frozen=True, kw_only=True)
class Tel2BundleSensorDescription(SensorEntityDescription):
    """Describes a bundle sensor on a number device."""

    value_fn: Callable[[BundleUsage], int]


ACCOUNT_SENSORS: tuple[Tel2AccountSensorDescription, ...] = (
    Tel2AccountSensorDescription(
        key="balance",
        translation_key="balance",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=TEL2_CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda state: state.balance.balance,
    ),
    Tel2AccountSensorDescription(
        key="available_balance",
        translation_key="available_balance",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=TEL2_CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda state: state.balance.available_balance,
    ),
    Tel2AccountSensorDescription(
        key="plan",
        translation_key="plan",
        value_fn=lambda state: (
            (state.plan.description or state.plan.alias or None) if state.plan else None
        ),
    ),
    Tel2AccountSensorDescription(
        key="account_status",
        translation_key="account_status",
        value_fn=lambda state: state.details.status,
    ),
)

BUNDLE_SENSORS: tuple[Tel2BundleSensorDescription, ...] = (
    Tel2BundleSensorDescription(
        key="bundle_used",
        translation_key="bundle_used",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        # Climbs through the bill cycle and drops to 0 at the next, which is a reset.
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda bundle: bundle.used_minutes,
    ),
    Tel2BundleSensorDescription(
        key="bundle_total",
        translation_key="bundle_total",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda bundle: bundle.total_minutes,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Tel2ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tel2 sensors."""
    account = entry.runtime_data.account
    async_add_entities(
        Tel2AccountSensor(account, description) for description in ACCOUNT_SENSORS
    )

    def bundle_sensors(number: Tel2Number) -> list[Tel2BundleSensor]:
        if number.canonical not in account.data.bundles:
            return []
        return [
            Tel2BundleSensor(account, number, description)
            for description in BUNDLE_SENSORS
        ]

    async_add_line_entities(entry, async_add_entities, bundle_sensors)


class Tel2AccountSensor(Tel2AccountEntity, SensorEntity):
    """A sensor on the account device."""

    entity_description: Tel2AccountSensorDescription

    @property
    def native_value(self) -> StateType | Decimal:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)


class Tel2BundleSensor(Tel2LineEntity[Tel2AccountCoordinator], SensorEntity):
    """Bundle minutes for one number."""

    entity_description: Tel2BundleSensorDescription

    @property
    def available(self) -> bool:
        """Return whether the number still has a bundle."""
        return super().available and self.canonical in self.coordinator.data.bundles

    @property
    def native_value(self) -> int | None:
        """Return the sensor value."""
        bundle = self.coordinator.data.bundles.get(self.canonical)
        return self.entity_description.value_fn(bundle) if bundle else None
