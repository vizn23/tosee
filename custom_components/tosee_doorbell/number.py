"""Number entity pro ToSee zvonek (celočíselné nastavení)."""
import logging
from typing import Any, Dict, Optional

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
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
    """Nastaví number entity pro konfigurační čísla."""
    data = hass.data[DOMAIN][entry.entry_id]
    api: ToSeeDoorbellAPI = data["api"]
    name = data["name"]
    entry_id = entry.entry_id
    
    entities = []
    
    # Vytvoř number pro každou konfigurační volbu typu "int"
    for config_key, config_info in CONFIG_KEYS.items():
        if config_info["type"] == "int" and "min" in config_info and "max" in config_info:
            entities.append(ToSeeConfigNumber(
                api, name, entry_id, config_key, config_info
            ))
    
    async_add_entities(entities)


class ToSeeConfigNumber(NumberEntity):
    """Number pro konfiguraci zařízení."""
    
    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER
    
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
        
        self._attr_unique_id = f"{entry_id}_number_{config_key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": name,
            "manufacturer": "Tocoding Technology",
            "model": "ToSee Doorbell",
        }
        self._attr_name = config_info["name"]
        self._attr_icon = "mdi:cog"
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_native_min_value = config_info["min"]
        self._attr_native_max_value = config_info["max"]
        self._attr_native_step = 1
    
    @property
    def native_value(self) -> Optional[float]:
        value = self._api.get_config_value(self._config_key)
        if value is not None:
            return float(value)
        return None
    
    async def async_set_native_value(self, value: float) -> None:
        """Nastaví hodnotu."""
        await self._api.set_config(self._config_key, int(value))
        self.async_write_ha_state()
    
    @property
    def available(self) -> bool:
        return self._api.is_connected