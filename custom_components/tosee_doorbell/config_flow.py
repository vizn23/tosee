"""Config flow pro ToSee Doorbell integraci."""
import asyncio
import logging
import socket
import time
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    NAME,
    DEFAULT_PASSWORD,
    HEARTBEAT_INTERVAL,
    PPPP_DISCOVERY_PORT,
    PPPP_BROADCAST_ADDR,
    DISCOVERY_PAYLOAD,
)
from .tosee_api import ToSeeDoorbellAPI
from .pppp_protocol import ConnectionError, AuthenticationError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
    vol.Optional(CONF_NAME, default=NAME): str,
    vol.Optional(CONF_SCAN_INTERVAL, default=HEARTBEAT_INTERVAL): int,
})

STEP_DISCOVERY_DATA_SCHEMA = vol.Schema({
    vol.Required("device"): str,
    vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
    vol.Optional(CONF_NAME): str,
    vol.Optional(CONF_SCAN_INTERVAL, default=HEARTBEAT_INTERVAL): int,
})


class ToSeeDoorbellConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow pro ToSee Doorbell."""
    
    VERSION = 1
    MINOR_VERSION = 1
    
    def __init__(self):
        self._discovered_devices: Dict[str, Dict] = {}
    
    async def async_step_user(self, user_input: Optional[Dict] = None) -> FlowResult:
        """První krok: ruční zadání nebo discovery."""
        errors = {}
        
        if user_input is not None:
            host = user_input[CONF_HOST]
            
            # Test připojení
            api = ToSeeDoorbellAPI(
                host=host,
                password=user_input.get(CONF_PASSWORD, DEFAULT_PASSWORD),
            )
            
            try:
                await api.connect()
                await api.disconnect()
                
                # Úspěšné připojení
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME, NAME),
                    data=user_input,
                )
            except AuthenticationError:
                errors["base"] = "auth_failed"
            except ConnectionError as e:
                errors["base"] = "cannot_connect"
            except Exception as e:
                _LOGGER.exception("Unexpected error: %s", e)
                errors["base"] = "unknown"
        
        # Nabídni discovery jako alternativu
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "discovery_hint": "Nebo klikni na 'Discover' pro automatické hledání v LAN"
            },
        )
    
    async def async_step_discovery(self, user_input: Optional[Dict] = None) -> FlowResult:
        """Discovery krok."""
        if user_input is not None:
            selected = user_input["device"]
            device = self._discovered_devices[selected]
            
            return self.async_create_entry(
                title=user_input.get(CONF_NAME, f"ToSee {device['did'][:8]}"),
                data={
                    CONF_HOST: device["host"],
                    CONF_PASSWORD: user_input.get(CONF_PASSWORD, DEFAULT_PASSWORD),
                    CONF_NAME: user_input.get(CONF_NAME, f"ToSee {device['did'][:8]}"),
                    CONF_SCAN_INTERVAL: user_input.get(CONF_SCAN_INTERVAL, HEARTBEAT_INTERVAL),
                },
            )
        
        # Spusť discovery
        devices = await self._async_discover_devices()
        
        if not devices:
            return self.async_show_form(
                step_id="discovery",
                data_schema=vol.Schema({}),
                errors={"base": "no_devices_found"},
                description_placeholders={"message": "Žádná zařízení nenalezena. Zkontroluj, že jsi ve stejné síti."},
            )
        
        self._discovered_devices = {f"{d['did']} ({d['host']})": d for d in devices}
        
        return self.async_show_form(
            step_id="discovery",
            data_schema=vol.Schema({
                vol.Required("device"): vol.In(list(self._discovered_devices.keys())),
                vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
                vol.Optional(CONF_NAME): str,
                vol.Optional(CONF_SCAN_INTERVAL, default=HEARTBEAT_INTERVAL): int,
            }),
        )
    
    async def _async_discover_devices(self) -> list:
        """Zjistí ToSee zařízení v LAN."""
        return await self.hass.async_add_executor_job(self._discover_devices_sync)

    def _discover_devices_sync(self) -> list:
        """Blokující discovery voláno přes executor."""
        import socket
        devices = []
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(3)
        sock.bind(('0.0.0.0', 0))

        try:
            sock.sendto(DISCOVERY_PAYLOAD, (PPPP_BROADCAST_ADDR, PPPP_DISCOVERY_PORT))
            start = time.time()
            while time.time() - start < 3:
                try:
                    data, addr = sock.recvfrom(1024)
                    if data[:2] == b'\xF1\x41' and len(data) >= 24:
                        did = data[4:24].decode('ascii', errors='ignore').strip('\x00')
                        if did and (did.startswith(('20', '21', '22', '23')) or len(did) == 20):
                            devices.append({
                                "did": did,
                                "host": addr[0],
                                "port": addr[1],
                            })
                            _LOGGER.info("Discovered ToSee device: %s at %s", did, addr[0])
                except socket.timeout:
                    break
                except Exception:
                    continue
        finally:
            sock.close()

        return devices
    
    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "ToSeeOptionsFlow":
        return ToSeeOptionsFlow(config_entry)


class ToSeeOptionsFlow(config_entries.OptionsFlow):
    """Options flow pro úpravu nastavení."""
    
    def __init__(self, config_entry: config_entries.ConfigEntry):
        self.config_entry = config_entry
    
    async def async_step_init(self, user_input: Optional[Dict] = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_PASSWORD,
                    default=self.config_entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD)
                ): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.data.get(CONF_SCAN_INTERVAL, HEARTBEAT_INTERVAL)
                ): int,
            }),
        )