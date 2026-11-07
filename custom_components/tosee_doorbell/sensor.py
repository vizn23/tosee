"""Sensor entity pro ToSee zvonek."""
import logging
from typing import Any, Dict, Optional
from datetime import datetime

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
)

from .const import DOMAIN, CONFIG_KEYS
from .tosee_api import ToSeeDoorbellAPI

_LOGGER = logging.getLogger(__name__)


SENSOR_TYPES = {
    "battery": SensorEntityDescription(
        key="battery",
        name="Baterie",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "signal_strength": SensorEntityDescription(
        key="signal_strength",
        name="Síla signálu",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "firmware": SensorEntityDescription(
        key="firmware",
        name="Firmware",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    "last_doorbell": SensorEntityDescription(
        key="last_doorbell",
        name="Poslední zvonění",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "last_motion": SensorEntityDescription(
        key="last_motion",
        name="Poslední pohyb",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "online": SensorEntityDescription(
        key="online",
        name="Online",
        device_class=SensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Nastaví sensor entity."""
    data = hass.data[DOMAIN][entry.entry_id]
    api: ToSeeDoorbellAPI = data["api"]
    coordinator = data["coordinator"]
    name = data["name"]
    entry_id = entry.entry_id
    
    entities = []
    
    # Základní senzory
    for key, description in SENSOR_TYPES.items():
        entities.append(ToSeeSensor(coordinator, api, description, name, entry_id, key))
    
    # Konfigurační senzory (pro čtení aktuálních hodnot)
    for config_key, config_info in CONFIG_KEYS.items():
        if config_info["type"] in ("bool", "int"):
            entities.append(ToSeeConfigSensor(
                coordinator, api, name, entry_id, config_key, config_info
            ))
    
    async_add_entities(entities)


class ToSeeSensor(CoordinatorEntity, SensorEntity):
    """Základní ToSee sensor."""
    
    _attr_has_entity_name = True
    
    def __init__(
        self,
        coordinator,
        api: ToSeeDoorbellAPI,
        description: SensorEntityDescription,
        name: str,
        entry_id: str,
        state_key: str,
    ):
        super().__init__(coordinator)
        self._api = api
        self._state_key = state_key
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{state_key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": name,
            "manufacturer": "Tocoding Technology",
            "model": "ToSee Doorbell",
        }
    
    @property
    def native_value(self) -> Any:
        state = self.coordinator.data or self._api.state
        value = state.get(self._state_key)
        
        # Speciální zpracování
        if self._state_key == "online":
            return value
        elif self._state_key in ("last_doorbell", "last_motion") and value:
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(value)
            return value
        elif self._state_key == "firmware":
            return value
        return value
    
    @property
    def available(self) -> bool:
        return self._api.is_connected


class ToSeeConfigSensor(CoordinatorEntity, SensorEntity):
    """Sensor pro konfigurační hodnoty."""
    
    _attr_has_entity_name = True
    
    def __init__(
        self,
        coordinator,
        api: ToSeeDoorbellAPI,
        name: str,
        entry_id: str,
        config_key: str,
        config_info: Dict,
    ):
        super().__init__(coordinator)
        self._api = api
        self._config_key = config_key
        self._config_info = config_info
        
        self.entity_description = SensorEntityDescription(
            key=f"config_{config_key}",
            name=config_info["name"],
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        self._attr_unique_id = f"{entry_id}_config_{config_key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": name,
            "manufacturer": "Tocoding Technology",
            "model": "ToSee Doorbell",
        }
        
        # Nastav unit podle typu
        if config_info["type"] == "int":
            self._attr_native_unit_of_measurement = None
            self._attr_state_class = SensorStateClass.MEASUREMENT
    
    @property
    def native_value(self) -> Any:
        return self._api.get_config_value(self._config_key)
    
    @property
    def available(self) -> bool:
        return self._api.is_connected
    
    @property
    def extra_state_attributes(self) -> Dict:
        attrs = {}
        if "min" in self._config_info:
            attrs["min"] = self._config_info["min"]
        if "max" in self._config_info:
            attrs["max"] = self._config_info["max"]
        if "options" in self._config_info:
            attrs["options"] = self._config_info["options"]
        return attrs