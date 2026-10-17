"""Shared helpers that set up the Tel2 integration against the fake Tel2 API."""

from collections.abc import Mapping
from typing import Any, Final

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tel2.const import CONF_LOGIN, CONF_PASSWORD, CONF_TOKEN, DOMAIN
from tests.fake_tel2 import FakeTel2Api
from tests.fixture_loader import TEST_ENTRY_ID

ACCOUNT_CODE: Final = "00000000"
LINE_1: Final = "441632960001"
LINE_2: Final = "441632960002"
FIXTURE_TOKEN: Final = "fake-token-for-tests"
STORED_TOKEN: Final = "stored-token"

DEFAULT_ENTRY_DATA: Final[Mapping[str, Any]] = {
    CONF_LOGIN: "ha-login",
    CONF_PASSWORD: "hunter2",
    CONF_TOKEN: STORED_TOKEN,
}


async def async_setup_integration(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    *,
    options: Mapping[str, Any] | None = None,
    entry_data: Mapping[str, Any] | None = None,
) -> MockConfigEntry:
    """Add a Tel2 entry for the two-line fixture account and run the real setup.

    Taking the fake makes sure Tel2 is mocked; the entry is returned whatever
    state setup leaves it in, so callers assert it.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=TEST_ENTRY_ID,
        unique_id=ACCOUNT_CODE,
        title="Example Ltd",
        data=dict(DEFAULT_ENTRY_DATA if entry_data is None else entry_data),
        options=dict(options or {}),
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
