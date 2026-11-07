"""Button entity pro ToSee zvonek (akce)."""
import logging
from typing import Any, Dict

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import EntityCategory

from .const import DOMAIN
from .tosee_api import ToSeeDoorbellAPI

_LOGGER = logging.getLogger(__name__)


BUTTON_TYPES = {
    "reboot": ButtonEntityDescription(
        key="reboot",
        name="Restart zařízení",
        icon="mdi:restart",
        entity_category=EntityCategory.CONFIG,
    ),
    "take_snapshot": ButtonEntityDescription(
        key="take_snapshot",
        name="Pořídit snapshot",
        icon="mdi:camera",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "start_intercom": ButtonEntityDescription(
        key="start_intercom",
        name="Zapnout intercom",
        icon="mdi:microphone",
    ),
    "stop_intercom": ButtonEntityDescription(
        key="stop_intercom",
        name="Vypnout intercom",
        icon="mdi:microphone-off",
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Nastaví button entity."""
    data = hass.data[DOMAIN][entry.entry_id]
    api: ToSeeDoorbellAPI = data["api"]
    name = data["name"]
    entry_id = entry.entry_id
    
    entities = [
        ToSeeButton(api, description, name, entry_id)
        for description in BUTTON_TYPES.values()
    ]
    
    async_add_entities(entities)


class ToSeeButton(ButtonEntity):
    """ToSee tlačítko pro akce."""
    
    _attr_has_entity_name = True
    
    def __init__(
        self,
        api: ToSeeDoorbellAPI,
        description: ButtonEntityDescription,
        name: str,
        entry_id: str,
    ):
        self._api = api
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": name,
            "manufacturer": "Tocoding Technology",
            "model": "ToSee Doorbell",
        }
    
    async def async_press(self) -> None:
        """Provede akci při stisku."""
        key = self.entity_description.key
        
        try:
            if key == "reboot":
                await self._api.reboot()
            elif key == "take_snapshot":
                snapshot = await self._api.take_snapshot()
                if snapshot:
                    _LOGGER.info("Snapshot taken: %d bytes", len(snapshot))
            elif key == "start_intercom":
                await self._api.start_intercom()
            elif key == "stop_intercom":
                await self._api.stop_intercom()
        except Exception as e:
            _LOGGER.error("Button %s failed: %s", key, e)
    
    @property
    def available(self) -> bool:
        return self._api.is_connected