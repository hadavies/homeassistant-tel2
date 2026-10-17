"""Runtime data shared by the platforms of a loaded Tel2 config entry."""

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .api import Tel2Client
from .coordinator import Tel2AccountCoordinator, Tel2LinesCoordinator


@dataclass(slots=True)
class Tel2RuntimeData:
    """Client, coordinators and call event manager of one Tel2 entry."""

    client: Tel2Client
    account: Tel2AccountCoordinator
    lines: Tel2LinesCoordinator
    call_events: object | None
    account_device_id: str


type Tel2ConfigEntry = ConfigEntry[Tel2RuntimeData]
