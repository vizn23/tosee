"""Media Player entity pro Two-Way Audio (Intercom) - integrace s HA Voice Assistant."""
import asyncio
import logging
from typing import Any, Dict, Optional, List
from enum import Enum

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import STATE_IDLE, STATE_PLAYING

from .const import DOMAIN
from .tosee_api import ToSeeDoorbellAPI

_LOGGER = logging.getLogger(__name__)


class IntercomState(Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    ACTIVE = "active"
    ENDING = "ending"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Nastaví media player pro intercom."""
    data = hass.data[DOMAIN][entry.entry_id]
    api: ToSeeDoorbellAPI = data["api"]
    name = data["name"]
    entry_id = entry.entry_id
    
    async_add_entities([ToSeeIntercomMediaPlayer(api, name, entry_id)])


class ToSeeIntercomMediaPlayer(MediaPlayerEntity):
    """Two-Way Audio (Intercom) jako Media Player.
    
    Podporuje:
    - Přijímání hovoru (zvonění → auto-answer)
    - Odchozí hovor (tlačítko v HA)
    - Audio stream z mikrofonu HA → zvonek
    - Audio stream ze zvonku → HA reproduktory
    - Integrace s HA Voice Assistant (pipeline)
    """
    
    _attr_has_entity_name = True
    _attr_name = "Intercom"
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA |
        MediaPlayerEntityFeature.STOP |
        MediaPlayerEntityFeature.VOLUME_SET |
        MediaPlayerEntityFeature.VOLUME_MUTE
    )
    _attr_media_content_type = MediaType.AUDIO
    
    def __init__(
        self,
        api: ToSeeDoorbellAPI,
        name: str,
        entry_id: str,
    ):
        self._api = api
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_intercom"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": name,
            "manufacturer": "Tocoding Technology",
            "model": "ToSee Doorbell",
        }
        
        self._state = IntercomState.IDLE
        self._volume = 0.5
        self._muted = False
        self._audio_task: Optional[asyncio.Task] = None
        self._input_stream: Optional[asyncio.Queue] = None
        self._output_stream: Optional[asyncio.Queue] = None
    
    @property
    def state(self) -> str:
        if self._state == IntercomState.ACTIVE:
            return STATE_PLAYING
        elif self._state == IntercomState.CONNECTING:
            return "connecting"
        return STATE_IDLE
    
    @property
    def volume_level(self) -> float:
        return self._volume
    
    @property
    def is_volume_muted(self) -> bool:
        return self._muted
    
    @property
    def available(self) -> bool:
        return self._api.is_connected
    
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return {
            "intercom_state": self._state.value,
            "api_state": "connected" if self._api.is_connected else "disconnected",
        }
    
    async def async_set_volume_level(self, volume: float) -> None:
        """Nastaví hlasitost intercomu (0-1)."""
        self._volume = max(0.0, min(1.0, volume))
        # Převede na 0-10 pro API
        api_volume = int(self._volume * 10)
        await self._api.set_config("intercom_volume", api_volume)
        self.async_write_ha_state()
    
    async def async_mute_volume(self, mute: bool) -> None:
        """Ztlumí/odtlumí mikrofon."""
        self._muted = mute
        # TODO: Implementovat mute v API (poslat prázdná data nebo příkaz)
        self.async_write_ha_state()
    
    async def async_media_play(self) -> None:
        """Zahájí intercom hovor (odchozí)."""
        if self._state != IntercomState.IDLE:
            return
        
        self._state = IntercomState.CONNECTING
        self.async_write_ha_state()
        
        try:
            success = await self._api.start_intercom()
            if success:
                self._state = IntercomState.ACTIVE
                await self._start_audio_streams()
            else:
                self._state = IntercomState.IDLE
        except Exception as e:
            _LOGGER.error("Failed to start intercom: %s", e)
            self._state = IntercomState.IDLE
        
        self.async_write_ha_state()
    
    async def async_media_stop(self) -> None:
        """Ukončí intercom hovor."""
        if self._state == IntercomState.IDLE:
            return
        
        self._state = IntercomState.ENDING
        self.async_write_ha_state()
        
        try:
            await self._stop_audio_streams()
            await self._api.stop_intercom()
        except Exception as e:
            _LOGGER.error("Failed to stop intercom: %s", e)
        finally:
            self._state = IntercomState.IDLE
            self.async_write_ha_state()
    
    async def async_play_media(
        self,
        media_type: MediaType | str,
        media_id: str,
        **kwargs: Any,
    ) -> None:
        """HA volá toto pro Voice Assistant pipeline.
        
        media_id může obsahovat:
        - "intercom_start" - začni hovor
        - "intercom_stop" - ukonči hovor
        - base64 audio data pro posílání na zvonek
        """
        if media_id == "intercom_start":
            await self.async_media_play()
        elif media_id == "intercom_stop":
            await self.async_media_stop()
        elif media_id.startswith("audio:"):
            # Přijmi audio data z Voice Assistant pipeline
            import base64
            audio_b64 = media_id[6:]
            audio_data = base64.b64decode(audio_b64)
            await self._send_audio_to_doorbell(audio_data)
    
    async def _start_audio_streams(self) -> None:
        """Spustí audio streamy obousměrně."""
        self._input_stream = asyncio.Queue(maxsize=100)
        self._output_stream = asyncio.Queue(maxsize=100)
        
        # Registruj callbacky
        self._api.register_callback("audio_frame", self._on_audio_from_doorbell)
        self._api.register_callback("intercom_started", self._on_intercom_started)
        self._api.register_callback("intercom_stopped", self._on_intercom_stopped)
        
        # Spusť sender task
        self._audio_task = asyncio.create_task(self._audio_sender_loop())
    
    async def _stop_audio_streams(self) -> None:
        """Zastaví audio streamy."""
        self._api.unregister_callback("audio_frame", self._on_audio_from_doorbell)
        self._api.unregister_callback("intercom_started", self._on_intercom_started)
        self._api.unregister_callback("intercom_stopped", self._on_intercom_stopped)
        
        if self._audio_task:
            self._audio_task.cancel()
            try:
                await self._audio_task
            except asyncio.CancelledError:
                pass
        
        self._input_stream = None
        self._output_stream = None
    
    async def _audio_sender_loop(self) -> None:
        """Posílá audio data z HA na zvonek."""
        while self._state == IntercomState.ACTIVE and self._input_stream:
            try:
                audio_data = await asyncio.wait_for(
                    self._input_stream.get(), timeout=0.1
                )
                if audio_data and not self._muted:
                    await self._api.send_intercom_audio(audio_data)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                _LOGGER.error("Audio sender error: %s", e)
                break
    
    async def _on_audio_from_doorbell(self, audio_data: bytes) -> None:
        """Callback pro audio z vahonku → HA reproduktory."""
        if self._output_stream and self._state == IntercomState.ACTIVE:
            try:
                await self._output_stream.put(audio_data)
                # TODO: Push do HA audio stream (vyžaduje custom component audio sink)
            except Exception as e:
                _LOGGER.debug("Audio output queue full: %s", e)
    
    def _on_intercom_started(self, data: Any) -> None:
        _LOGGER.debug("Intercom started remotely")
    
    def _on_intercom_stopped(self, data: Any) -> None:
        _LOGGER.debug("Intercom stopped remotely")
        if self._state == IntercomState.ACTIVE:
            self._state = IntercomState.ENDING
            asyncio.create_task(self._stop_audio_streams())
            self._state = IntercomState.IDLE
            self.async_write_ha_state()
    
    async def _send_audio_to_doorbell(self, audio_data: bytes) -> None:
        """Posílá audio data z HA (microphone/Voice Assistant) na zvonek."""
        if self._input_stream and self._state == IntercomState.ACTIVE:
            try:
                await self._input_stream.put(audio_data)
            except Exception as e:
                _LOGGER.debug("Audio input queue full: %s", e)
    
    # ===== Voice Assistant Pipeline Support =====
    
    async def async_process_voice_command(self, command: str) -> bool:
        """Zpracuje hlasový příkaz z HA Voice Assistant."""
        command = command.lower()
        
        if any(word in command for word in ["otevři intercom", "zahaj hovor", "mluv se zvonkem"]):
            await self.async_media_play()
            return True
        elif any(word in command for word in ["zavři intercom", "ukonči hovor", "přeruš hovor"]):
            await self.async_media_stop()
            return True
        elif "hlasitost" in command:
            # Parse čísla z příkazu
            import re
            numbers = re.findall(r'\d+', command)
            if numbers:
                volume = int(numbers[0]) / 100.0
                await self.async_set_volume_level(volume)
                return True
        
        return False


# ===== Audio Stream Helper pro HA =====

class ToSeeAudioStream:
    """Helper pro streamování audio do HA (pro TTS, notifikace, atd.)."""
    
    def __init__(self, api: ToSeeDoorbellAPI):
        self.api = api
        self._playing = False
    
    async def play_tts(self, text: str, language: str = "cs") -> bool:
        """Přehraje TTS na zvonku (vyžaduje TTS engine v HA)."""
        # HA volá tuto metodu s TTS audio daty
        # Implementace přes media_player.play_media
        pass
    
    async def play_notification(self, sound: str) -> bool:
        """Přehraje notifikační zvuk."""
        pass