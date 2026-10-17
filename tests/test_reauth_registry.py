"""Reauth keeps the entity and device registries, including user renames."""

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tel2.const import CONF_LOGIN, CONF_PASSWORD, DOMAIN
from custom_components.tel2.runtime import Tel2RuntimeData
from tests.fake_tel2 import FakeTel2Api
from tests.helpers import ACCOUNT_CODE, LINE_1, async_setup_integration

RENAMED_ENTITY_ID = "switch.front_desk_dnd"
RENAMED_ENTITY_NAME = "Front desk DND"
RENAMED_DEVICE_NAME = "Front desk"


def _entities(hass: HomeAssistant, entry: MockConfigEntry) -> list[er.RegistryEntry]:
    return sorted(
        er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id),
        key=lambda entity: entity.unique_id,
    )


def _devices(hass: HomeAssistant, entry: MockConfigEntry) -> list[dr.DeviceEntry]:
    return sorted(
        dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id),
        key=lambda device: sorted(device.identifiers),
    )


def _rename_dnd_switch(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    dnd = next(
        entity
        for entity in _entities(hass, entry)
        if entity.domain == "switch" and entity.translation_key == "do_not_disturb"
        if (device := devices.async_get(entity.device_id or "")) is not None
        and (DOMAIN, LINE_1) in device.identifiers
    )
    entities.async_update_entity(
        dnd.entity_id, new_entity_id=RENAMED_ENTITY_ID, name=RENAMED_ENTITY_NAME
    )
    assert dnd.device_id is not None
    devices.async_update_device(dnd.device_id, name_by_user=RENAMED_DEVICE_NAME)


async def _fail_auth_on_refresh(
    hass: HomeAssistant, entry: MockConfigEntry, fake_tel2: FakeTel2Api
) -> str:
    runtime = entry.runtime_data
    assert isinstance(runtime, Tel2RuntimeData)
    fake_tel2.respond("Get-Customer-Details", "error_wrong_password.json")
    await runtime.account.async_refresh()
    await hass.async_block_till_done()
    (flow,) = entry.async_get_active_flows(hass, {SOURCE_REAUTH})
    assert flow["step_id"] == "reauth_confirm"
    fake_tel2.respond("Get-Customer-Details", "get_customer_details.json")
    return flow["flow_id"]


async def test_reauth_keeps_registries_and_user_renames(
    hass: HomeAssistant, fake_tel2: FakeTel2Api
) -> None:
    entry = await async_setup_integration(hass, fake_tel2)
    assert entry.state is ConfigEntryState.LOADED
    _rename_dnd_switch(hass, entry)
    entities_before = _entities(hass, entry)
    devices_before = _devices(hass, entry)
    runtime_before = entry.runtime_data
    assert len(entities_before) > 1
    assert len(devices_before) == 3

    flow_id = await _fail_auth_on_refresh(hass, entry, fake_tel2)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_LOGIN: "ha-login", CONF_PASSWORD: "new-password"}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not runtime_before
    assert entry.unique_id == ACCOUNT_CODE
    assert entry.data[CONF_PASSWORD] == "new-password"
    assert _entities(hass, entry) == entities_before
    assert _devices(hass, entry) == devices_before
    renamed = er.async_get(hass).async_get(RENAMED_ENTITY_ID)
    assert renamed is not None
    assert renamed.name == RENAMED_ENTITY_NAME
    device = dr.async_get(hass).async_get(renamed.device_id or "")
    assert device is not None
    assert device.name_by_user == RENAMED_DEVICE_NAME
    state = hass.states.get(RENAMED_ENTITY_ID)
    assert state is not None
    assert state.attributes["friendly_name"] == RENAMED_ENTITY_NAME
    assert not list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
