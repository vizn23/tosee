"""Binary sensors pro ToSee zvonek - motion, doorbell press, online status."""
import logging
from typing import Any, Dict, Optional
from datetime import datetime

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import event as hass_helpers

from .const import DOMAIN
from .tosee_api import ToSeeDoorbellAPI

_LOGGER = logging.getLogger(__name__)


BINARY_SENSOR_TYPES = {
    "motion": BinarySensorEntityDescription(
        key="motion",
        name="Pohyb",
        device_class=BinarySensorDeviceClass.MOTION,
        icon="mdi:motion-sensor",
    ),
    "doorbell": BinarySensorEntityDescription(
        key="doorbell",
        name="Zvonění",
        device_class=BinarySensorDeviceClass.DOORBELL,
        icon="mdi:doorbell-video",
    ),
    "online": BinarySensorEntityDescription(
        key="online",
        name="Online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category="diagnostic",
    ),
    "pir": BinarySensorEntityDescription(
        key="pir",
        name="PIR senzor",
        device_class=BinarySensorDeviceClass.MOTION,
        entity_category="diagnostic",
    ),
    "low_battery": BinarySensorEntityDescription(
        key="low_battery",
        name="Nízká baterie",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category="diagnostic",
    ),
    "sd_card": BinarySensorEntityDescription(
        key="sd_card",
        name="SD karta",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category="diagnostic",
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Nastaví binary sensor entity."""
    data = hass.data[DOMAIN][entry.entry_id]
    api: ToSeeDoorbellAPI = data["api"]
    coordinator = data["coordinator"]
    name = data["name"]
    entry_id = entry.entry_id
    
    entities = [
        ToSeeBinarySensor(coordinator, api, description, name, entry_id, key)
        for key, description in BINARY_SENSOR_TYPES.items()
    ]
    
    async_add_entities(entities)
    
    # Registruj callbacky pro real-time eventy
    _register_event_callbacks(hass, api, entities)


def _register_event_callbacks(hass: HomeAssistant, api: ToSeeDoorbellAPI, entities: list) -> None:
    """Registruje callbacky pro asynchroní události z PPPP."""

    def find_entity(key: str) -> Optional[ToSeeBinarySensor]:
        for e in entities:
            if e._state_key == key:
                return e
        return None

    def _make_off_callback(entity: ToSeeBinarySensor):
        """Vytvoří callback pro vypnutí binary sensoru po časovém limitu."""
        def _turn_off(_now):
            entity._attr_is_on = False
            entity.async_write_ha_state()
        return _turn_off

    async def on_doorbell_press(data: Any) -> None:
        entity = find_entity("doorbell")
        if entity:
            entity._attr_is_on = True
            entity._attr_extra_state_attributes = {
                "last_press": datetime.now().isoformat(),
                "data": data,
            }
            entity.async_write_ha_state()
            # Auto-off po 3 sekundách
            hass_helpers.async_call_later(hass, 3, _make_off_callback(entity))

    async def on_motion_detected(data: Any) -> None:
        entity = find_entity("motion")
        if entity:
            entity._attr_is_on = True
            entity._attr_extra_state_attributes = {
                "last_motion": datetime.now().isoformat(),
                "data": data,
            }
            entity.async_write_ha_state()
            # Auto-off po 30 sekundách
            hass_helpers.async_call_later(hass, 30, _make_off_callback(entity))

    async def on_pir_triggered(data: Any) -> None:
        entity = find_entity("pir")
        if entity:
            entity._attr_is_on = True
            entity.async_write_ha_state()
            hass_helpers.async_call_later(hass, 10, _make_off_callback(entity))

    # Registrace do API (pokud API podporuje event callbacky)
    api.register_callback("doorbell_press", on_doorbell_press)
    api.register_callback("motion_detected", on_motion_detected)
    api.register_callback("pir_triggered", on_pir_triggered)


class ToSeeBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """ToSee binary sensor."""
    
    _attr_has_entity_name = True
    _attr_should_poll = False
    
    def __init__(
        self,
        coordinator,
        api: ToSeeDoorbellAPI,
        description: BinarySensorEntityDescription,
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
        self._attr_extra_state_attributes = {}
    
    @property
    def is_on(self) -> Optional[bool]:
        state = self.coordinator.data or self._api.state
        
        if self._state_key == "online":
            return self._api.is_connected
        elif self._state_key == "motion":
            return bool(state.get("last_motion"))
        elif self._state_key == "doorbell":
            return bool(state.get("last_doorbell"))
        elif self._state_key == "pir":
            config = state.get("config", {})
            return bool(config.get("pir_on", 0))
        elif self._state_key == "low_battery":
            battery = state.get("battery")
            return battery is not None and battery < 20
        elif self._state_key == "sd_card":
            sd_used = state.get("config", {}).get("sd_used", 0)
            sd_total = state.get("config", {}).get("sd_total", 1)
            return sd_total > 0 and (sd_used / sd_total) > 0.95
        
        return None
    
    @property
    def extra_state_attributes(self) -> Dict:
        attrs = super().extra_state_attributes or {}
        attrs.update(self._attr_extra_state_attributes)
        return attrs
    
    @property
    def available(self) -> bool:
        if self._state_key == "online":
            return True
        return self._api.is_connected