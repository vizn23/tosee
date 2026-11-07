"""Select entity pro ToSee zvonek (výběr z možností)."""
import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.select import SelectEntity, SelectEntityDescription
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
    """Nastaví select entity pro konfigurační volby."""
    data = hass.data[DOMAIN][entry.entry_id]
    api: ToSeeDoorbellAPI = data["api"]
    name = data["name"]
    entry_id = entry.entry_id
    
    entities = []
    
    # Vytvoř select pro každou konfigurační volbu typu "select"
    for config_key, config_info in CONFIG_KEYS.items():
        if config_info["type"] == "select" and "options" in config_info:
            entities.append(ToSeeConfigSelect(
                api, name, entry_id, config_key, config_info
            ))
    
    async_add_entities(entities)


class ToSeeConfigSelect(SelectEntity):
    """Select pro konfiguraci zařízení."""
    
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
        
        self._attr_unique_id = f"{entry_id}_select_{config_key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": name,
            "manufacturer": "Tocoding Technology",
            "model": "ToSee Doorbell",
        }
        self._attr_name = config_info["name"]
        self._attr_icon = "mdi:cog"
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_options = [str(opt) for opt in config_info["options"]]
    
    @property
    def current_option(self) -> Optional[str]:
        value = self._api.get_config_value(self._config_key)
        if value is not None:
            return str(value)
        return None
    
    async def async_select_option(self, option: str) -> None:
        """Nastaví vybranou hodnotu."""
        # Převede zpět na int pokud je potřeba
        try:
            value = int(option)
        except ValueError:
            value = option
        
        await self._api.set_config(self._config_key, value)
        self.async_write_ha_state()
    
    @property
    def available(self) -> bool:
        return self._api.is_connected