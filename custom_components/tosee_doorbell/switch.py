"""Switch entity pro ToSee zvonek (boolean nastavení)."""
import logging
from typing import Any, Dict, Optional

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import EntityCategory

from .const import DOMAIN, CONFIG_KEYS
from .tosee_api import ToSeeDoorbellAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Nastaví switch entity pro boolean konfigurace."""
    data = hass.data[DOMAIN][entry.entry_id]
    api: ToSeeDoorbellAPI = data["api"]
    name = data["name"]
    entry_id = entry.entry_id
    
    entities = []
    
    # Vytvoř switch pro každou konfigurační volbu typu "bool"
    for config_key, config_info in CONFIG_KEYS.items():
        if config_info["type"] == "bool":
            entities.append(ToSeeConfigSwitch(
                api, name, entry_id, config_key, config_info
            ))
    
    async_add_entities(entities)


class ToSeeConfigSwitch(SwitchEntity):
    """Switch pro konfiguraci zařízení."""
    
    _attr_has_entity_name = True
    
    def __init__(
        self,
        api: ToSeeDoorbellAPI,
        name: str,
        entry_id: str,
        config_key: str,
        config_info: Dict,
    ):
        self._api = api
        self._config_key = config_key
        self._config_info = config_info
        
        self._attr_unique_id = f"{entry_id}_switch_{config_key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": name,
            "manufacturer": "Tocoding Technology",
            "model": "ToSee Doorbell",
        }
        self._attr_name = config_info["name"]
        self._attr_icon = "mdi:toggle-switch"
        self._attr_entity_category = EntityCategory.CONFIG
    
    @property
    def is_on(self) -> bool:
        value = self._api.get_config_value(self._config_key)
        return bool(value) if value is not None else False
    
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Zapne."""
        await self._api.set_config(self._config_key, 1)
        self.async_write_ha_state()
    
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Vypne."""
        await self._api.set_config(self._config_key, 0)
        self.async_write_ha_state()
    
    @property
    def available(self) -> bool:
        return self._api.is_connected