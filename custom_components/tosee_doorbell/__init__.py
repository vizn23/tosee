"""ToSee Doorbell (PPPP Local) - Home Assistant Custom Component.
   
   Lokální integrace pro ToSee zvonek pomocí reverzního inženýrství PPPP/CS2 protokolu.
   Žádný cloud, žádná vendor aplikace - vše lokálně přes LAN.
"""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    Platform,
)

from .const import (
    DOMAIN,
    NAME,
    VERSION,
    PLATFORMS,
    DEFAULT_PASSWORD,
    HEARTBEAT_INTERVAL,
    CONFIG_KEYS,
)
from .tosee_api import ToSeeDoorbellAPI, PPPPException, ConnectionError, AuthenticationError

_LOGGER = logging.getLogger(__name__)

# Konfigurační schema
CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): cv.string,
        vol.Optional(CONF_NAME, default=NAME): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL, default=HEARTBEAT_INTERVAL): cv.positive_int,
    })
}, extra=vol.ALLOW_EXTRA)

# Služby
SERVICE_TAKE_SNAPSHOT = "take_snapshot"
SERVICE_REBOOT = "reboot"
SERVICE_PLAY_RECORDING = "play_recording"
SERVICE_SET_CONFIG = "set_config"

SERVICE_SCHEMA_TAKE_SNAPSHOT = vol.Schema({
    vol.Optional("camera_entity"): cv.entity_id,
})

SERVICE_SCHEMA_REBOOT = vol.Schema({})

SERVICE_SCHEMA_PLAY_RECORDING = vol.Schema({
    vol.Required("date"): cv.string,  # YYYYMMDD
    vol.Required("time"): cv.string,  # HHMMSS
})

SERVICE_SCHEMA_SET_CONFIG = vol.Schema({
    vol.Required("key"): vol.In(CONFIG_KEYS.keys()),
    vol.Required("value"): vol.Any(bool, int, str),
})


async def async_setup(hass: HomeAssistant, config: Dict) -> bool:
    """Set up z YAML (pro zpětnou kompatibilitu)."""
    if DOMAIN not in config:
        return True
    
    # Migrace na config flow
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "import"},
            data=config[DOMAIN],
        )
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up z config entry."""
    _LOGGER.info("Setting up ToSee Doorbell: %s", entry.data.get(CONF_HOST))
    
    host = entry.data[CONF_HOST]
    password = entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD)
    name = entry.data.get(CONF_NAME, NAME)
    scan_interval = entry.data.get(CONF_SCAN_INTERVAL, HEARTBEAT_INTERVAL)
    
    # Vytvoř API instanci
    api = ToSeeDoorbellAPI(host=host, password=password)
    
    # Coordinator pro periodické aktualizace
    coordinator = ToSeeDataUpdateCoordinator(hass, api, scan_interval)
    
    # Připoj
    try:
        await api.connect()
        await coordinator.async_config_entry_first_refresh()
    except (ConnectionError, AuthenticationError) as e:
        _LOGGER.error("Failed to connect to ToSee doorbell: %s", e)
        # Pokus o reconnect na pozadí
        hass.async_create_task(_async_reconnect_loop(hass, entry, api, coordinator))
    
    # Ulož do hass.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "name": name,
    }
    
    # Nastav platformy
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Registruj služby
    async def handle_take_snapshot(call: ServiceCall) -> None:
        """Pořídí snapshot."""
        snapshot = await api.take_snapshot()
        if snapshot:
            _LOGGER.info("Snapshot taken, size: %d bytes", len(snapshot))
            # Ulož do /tmp nebo pošli notify
            hass.bus.async_fire("tosee_doorbell_snapshot", {"data": snapshot.hex()})
        else:
            _LOGGER.warning("Failed to take snapshot")
    
    async def handle_reboot(call: ServiceCall) -> None:
        """Restartuje zařízení."""
        await api.reboot()
    
    async def handle_play_recording(call: ServiceCall) -> None:
        """Přehrá nahrávku."""
        date = call.data["date"]
        time = call.data["time"]
        await api.play_recording(date, time)
    
    async def handle_set_config(call: ServiceCall) -> None:
        """Nastaví konfiguraci."""
        key = call.data["key"]
        value = call.data["value"]
        await api.set_config(key, value)
        await coordinator.async_request_refresh()
    
    hass.services.async_register(
        DOMAIN, SERVICE_TAKE_SNAPSHOT, handle_take_snapshot,
        schema=SERVICE_SCHEMA_TAKE_SNAPSHOT
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REBOOT, handle_reboot,
        schema=SERVICE_SCHEMA_REBOOT
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PLAY_RECORDING, handle_play_recording,
        schema=SERVICE_SCHEMA_PLAY_RECORDING
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_CONFIG, handle_set_config,
        schema=SERVICE_SCHEMA_SET_CONFIG
    )
    
    # Cleanup při odinstalování
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    
    return True


async def _async_reconnect_loop(hass: HomeAssistant, entry: ConfigEntry, api: ToSeeDoorbellAPI, coordinator: "ToSeeDataUpdateCoordinator") -> None:
    """Pokusí se znovu připojit na pozadí."""
    while True:
        await asyncio.sleep(30)
        if not api.is_connected:
            try:
                _LOGGER.info("Attempting reconnect to %s", api.host)
                await api.connect()
                await coordinator.async_request_refresh()
                _LOGGER.info("Reconnected successfully")
                break
            except Exception as e:
                _LOGGER.debug("Reconnect failed: %s", e)


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Aktualizace options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Odstraní integraci."""
    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data:
        api: ToSeeDoorbellAPI = data["api"]
        await api.disconnect()
    
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Úplné odstranění."""
    # Služby se odstraní automaticky při unload


class ToSeeDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator pro pravidelné aktualizace stavu."""
    
    def __init__(self, hass: HomeAssistant, api: ToSeeDoorbellAPI, scan_interval: int):
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{api.host}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api
    
    async def _async_update_data(self) -> Dict[str, Any]:
        """Aktualizuje data ze zařízení."""
        try:
            await self.api.async_update()
            return self.api.state
        except Exception as e:
            raise UpdateFailed(f"Error updating ToSee doorbell: {e}") from e