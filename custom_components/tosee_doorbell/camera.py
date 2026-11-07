"""Camera entity pro ToSee zvonek - snapshot a WebRTC stream."""
import asyncio
import logging
from typing import Optional, Any, Dict

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .tosee_api import ToSeeDoorbellAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Nastaví camera entity."""
    data = hass.data[DOMAIN][entry.entry_id]
    api: ToSeeDoorbellAPI = data["api"]
    name = data["name"]

    async_add_entities([ToSeeDoorbellCamera(hass, api, name, entry.entry_id)])


class ToSeeDoorbellCamera(Camera):
    """ToSee zvonek kamera - snapshot a WebRTC live stream."""

    _attr_has_entity_name = True
    _attr_name = "Live Stream"
    _attr_supported_features = CameraEntityFeature.ON_OFF

    def __init__(
        self,
        hass: HomeAssistant,
        api: ToSeeDoorbellAPI,
        name: str,
        entry_id: str,
    ):
        super().__init__()
        self.hass = hass
        self._api = api
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_camera"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": name,
            "manufacturer": "Tocoding Technology",
            "model": "ToSee Doorbell",
        }

        self._frame_callback_registered = False

    async def async_added_to_hass(self) -> None:
        """Registruje callback pro video framy."""
        await super().async_added_to_hass()
        self._api.register_callback("video_frame", self._handle_video_frame)
        self._frame_callback_registered = True

    async def async_will_remove_from_hass(self) -> None:
        """Odregistruje callback."""
        if self._frame_callback_registered:
            self._api.unregister_callback("video_frame", self._handle_video_frame)
        await super().async_will_remove_from_hass()

    @callback
    def _handle_video_frame(self, frame_data: bytes) -> None:
        """Zpracuje příchozí H.264 frame (pro WebRTC most)."""
        pass

    async def stream_source(self) -> Optional[str]:
        """Vrátí None - live stream běží přes WebRTC, ne přes URL."""
        return None

    async def async_camera_image(
        self, width: Optional[int] = None, height: Optional[int] = None
    ) -> Optional[bytes]:
        """Vrátí snapshot (JPEG)."""
        return await self._api.take_snapshot()

    async def async_turn_on(self) -> None:
        """Zapne video stream."""
        await self._api.start_video()
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        """Vypne video stream."""
        await self._api.stop_video()
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._api.is_video_active

    @property
    def available(self) -> bool:
        return self._api.is_connected


class ToSeeDoorbellSnapshotCamera(Camera):
    """Snapshot-only kamera pro méně náročné zobrazení."""

    _attr_has_entity_name = True
    _attr_name = "Snapshot"
    _attr_supported_features = 0

    def __init__(self, api: ToSeeDoorbellAPI, name: str, entry_id: str):
        super().__init__()
        self._api = api
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_snapshot"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": name,
            "manufacturer": "Tocoding Technology",
            "model": "ToSee Doorbell",
        }

    async def async_camera_image(
        self, width: Optional[int] = None, height: Optional[int] = None
    ) -> Optional[bytes]:
        return await self._api.take_snapshot()

    @property
    def available(self) -> bool:
        return self._api.is_connected