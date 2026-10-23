"""Tel2 call event entities."""

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CALL_EVENT_TYPES
from .coordinator import Tel2AccountCoordinator
from .entity import Tel2LineEntity, async_add_line_entities
from .models import CallEvent, Tel2Number
from .numbering import display_number
from .runtime import Tel2ConfigEntry
from .webhook import signal_call_event, signal_call_event_availability

PARALLEL_UPDATES = 0

CALL = EventEntityDescription(key="call", translation_key="call")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Tel2ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tel2 call event entities."""

    def call_entities(number: Tel2Number) -> list[Tel2CallEventEntity]:
        return [Tel2CallEventEntity(entry, number)]

    async_add_line_entities(entry, async_add_entities, call_entities)


def _party(value: str) -> str:
    # The other party may be raw text such as "anonymous" or "000", not a number.
    is_number = value.isascii() and value.isdecimal() and not value.startswith("0")
    return display_number(value) if is_number else value


class Tel2CallEventEntity(Tel2LineEntity[Tel2AccountCoordinator], EventEntity):
    """Fires the call events of one number."""

    def __init__(self, entry: Tel2ConfigEntry, number: Tel2Number) -> None:
        """Initialise the entity."""
        super().__init__(entry.runtime_data.account, number, CALL)
        self._entry = entry
        self._attr_event_types = list(CALL_EVENT_TYPES)

    @property
    def available(self) -> bool:
        """Return whether call events are enabled, reachable and registered on Tel2."""
        # Webhooks arrive whatever the polling does, so a failed refresh is ignored.
        manager = self._entry.runtime_data.call_events
        return (
            manager is not None
            and manager.available
            and manager.registered
            and self.canonical in self._account.data.numbers
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to this entry's call events and their availability."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_call_event(self._entry.entry_id),
                self._async_handle_call_event,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_call_event_availability(self._entry.entry_id),
                self.async_write_ha_state,
            )
        )

    @callback
    def _async_handle_call_event(self, event: CallEvent) -> None:
        if event.number != self.canonical:
            return
        self._trigger_event(
            event.event_type,
            {
                "from": _party(event.from_number),
                "to": _party(event.to_number),
                "direction": event.direction,
                "call_id": event.call_id,
                "start_time": (
                    event.start_time.isoformat() if event.start_time else None
                ),
                "duration": event.duration,
            },
        )
        self.async_write_ha_state()
