"""ToSee API wrapper pro Home Assistant."""
import asyncio
import logging
from typing import Optional, Dict, Any, List, Callable, Awaitable
from datetime import datetime, timedelta

from .pppp_protocol import PPPPSession, PPPPException, AuthenticationError, ConnectionError
from .const import DEFAULT_PASSWORD, CONFIG_KEYS, HEARTBEAT_INTERVAL

_LOGGER = logging.getLogger(__name__)


class ToSeeDoorbellAPI:
    """Hlavní API třída pro ToSee zvonek."""
    
    def __init__(
        self,
        host: str,
        password: str = DEFAULT_PASSWORD,
        did: Optional[str] = None,
        port: int = 32108,
    ):
        self.host = host
        self.password = password
        self.did = did
        self.port = port
        
        self._session: Optional[PPPPSession] = None
        self._connected = False
        self._video_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._callbacks: Dict[str, List[Callable]] = {}
        
        # Stavové proměnné pro HA entity
        self._state = {
            "online": False,
            "video_active": False,
            "intercom_active": False,
            "last_doorbell": None,
            "last_motion": None,
            "battery": None,
            "signal_strength": None,
            "firmware": None,
            "config": {},
        }
    
    async def connect(self) -> bool:
        """Připojí k zvonku."""
        try:
            self._session = PPPPSession(
                host=self.host,
                port=self.port,
                password=self.password,
                did=self.did,
            )
            await self._session.connect()
            self._connected = True
            self._state["online"] = True

            # Registruj event callbacky z PPPP session do HA callback systému
            for event in ("doorbell_press", "motion_detected", "pir_triggered", "video_start", "video_stop", "talk_start", "talk_stop"):
                self._session.register_event_callback(event, self._fire_callbacks)

            # Načti počáteční konfiguraci
            await self._refresh_config()
            
            # Spusť video stream callback
            self._video_task = asyncio.create_task(self._video_receiver())
            
            _LOGGER.info("Connected to ToSee doorbell at %s", self.host)
            self._fire_callbacks("connected", True)
            return True
            
        except (ConnectionError, AuthenticationError) as e:
            _LOGGER.error("Failed to connect: %s", e)
            self._connected = False
            self._state["online"] = False
            raise
    
    async def disconnect(self) -> None:
        """Odpojí od zvonku."""
        self._connected = False
        self._state["online"] = False
        
        if self._video_task:
            self._video_task.cancel()
            try:
                await self._video_task
            except asyncio.CancelledError:
                pass
        
        if self._reconnect_task:
            self._reconnect_task.cancel()
        
        if self._session:
            for event in ("doorbell_press", "motion_detected", "pir_triggered", "video_start", "video_stop", "talk_start", "talk_stop"):
                self._session.unregister_event_callback(event, self._fire_callbacks)
            await self._session.disconnect()
            self._session = None
        
        self._fire_callbacks("disconnected", True)
    
    async def _video_receiver(self) -> None:
        """Příjem video streamu."""
        if not self._session:
            return
        
        async def video_callback(frame_data: bytes):
            # Zde by se frame předal do HA camera entity
            # Prostřednictvím callbacku nebo queue
            self._fire_callbacks("video_frame", frame_data)
        
        try:
            await self._session.start_video_stream(video_callback)
            self._state["video_active"] = True
            self._fire_callbacks("video_started", True)
        except Exception as e:
            _LOGGER.error("Video stream error: %s", e)
            self._state["video_active"] = False
    
    async def _refresh_config(self) -> None:
        """Aktualizuje konfiguraci ze zařízení."""
        if not self._session:
            return
        
        for key in CONFIG_KEYS:
            try:
                value = await self._session.get_config(key)
                if value is not None:
                    self._state["config"][key] = value
            except Exception as e:
                _LOGGER.debug("Failed to get config %s: %s", key, e)
    
    # ===== Veřejné API metody =====
    
    async def start_video(self) -> bool:
        """Zapne video stream."""
        if not self._session or not self._connected:
            return False
        try:
            await self._session.start_video_stream(lambda x: None)
            self._state["video_active"] = True
            return True
        except Exception as e:
            _LOGGER.error("Failed to start video: %s", e)
            return False
    
    async def stop_video(self) -> bool:
        """Vypne video stream."""
        if not self._session:
            return False
        try:
            await self._session.stop_video_stream()
            self._state["video_active"] = False
            return True
        except Exception as e:
            _LOGGER.error("Failed to stop video: %s", e)
            return False
    
    async def start_intercom(self) -> bool:
        """Zapne intercom."""
        if not self._session:
            return False
        try:
            await self._session.start_intercom()
            self._state["intercom_active"] = True
            return True
        except Exception as e:
            _LOGGER.error("Failed to start intercom: %s", e)
            return False
    
    async def stop_intercom(self) -> bool:
        """Vypne intercom."""
        if not self._session:
            return False
        try:
            await self._session.stop_intercom()
            self._state["intercom_active"] = False
            return True
        except Exception as e:
            _LOGGER.error("Failed to stop intercom: %s", e)
            return False
    
    async def send_intercom_audio(self, audio_data: bytes) -> bool:
        """Pošle audio data pro intercom."""
        if not self._session:
            return False
        try:
            await self._session.send_intercom_data(audio_data)
            return True
        except Exception as e:
            _LOGGER.error("Failed to send audio: %s", e)
            return False
    
    async def set_config(self, key: str, value: Any) -> bool:
        """Nastaví konfiguraci."""
        if not self._session or key not in CONFIG_KEYS:
            return False
        try:
            result = await self._session.set_config(key, value)
            if result:
                self._state["config"][key] = value
                self._fire_callbacks("config_changed", {key: value})
            return result
        except Exception as e:
            _LOGGER.error("Failed to set config %s: %s", key, e)
            return False
    
    async def get_config(self, key: str) -> Any:
        """Získá konfiguraci."""
        if not self._session:
            return self._state["config"].get(key)
        try:
            value = await self._session.get_config(key)
            if value is not None:
                self._state["config"][key] = value
            return value
        except Exception as e:
            _LOGGER.error("Failed to get config %s: %s", key, e)
            return self._state["config"].get(key)
    
    async def get_recordings(self, date: str) -> List[Dict]:
        """Získá seznam nahrávek pro datum (YYYYMMDD)."""
        if not self._session:
            return []
        try:
            return await self._session.get_recording_list(date)
        except Exception as e:
            _LOGGER.error("Failed to get recordings: %s", e)
            return []
    
    async def play_recording(self, date: str, time: str) -> bool:
        """Přehrá nahrávku."""
        if not self._session:
            return False
        try:
            return await self._session.play_recording(date, time)
        except Exception as e:
            _LOGGER.error("Failed to play recording: %s", e)
            return False
    
    async def take_snapshot(self) -> Optional[bytes]:
        """Pořídí snapshot."""
        if not self._session:
            return None
        try:
            return await self._session.snapshot()
        except Exception as e:
            _LOGGER.error("Failed to take snapshot: %s", e)
            return None
    
    async def reboot(self) -> bool:
        """Restartuje zařízení."""
        if not self._session:
            return False
        try:
            result = await self._session.send_json_command("reboot")
            return result.get("ret", -1) == 0
        except Exception as e:
            _LOGGER.error("Failed to reboot: %s", e)
            return False
    
    # ===== Stavové gettery =====
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    @property
    def is_video_active(self) -> bool:
        return self._state["video_active"]
    
    @property
    def is_intercom_active(self) -> bool:
        return self._state["intercom_active"]
    
    @property
    def state(self) -> Dict[str, Any]:
        return self._state.copy()
    
    def get_config_value(self, key: str) -> Any:
        return self._state["config"].get(key)
    
    # ===== Callback systém =====
    
    def register_callback(self, event: str, callback: Callable) -> None:
        """Registruje callback pro událost."""
        if event not in self._callbacks:
            self._callbacks[event] = []
        self._callbacks[event].append(callback)
    
    def unregister_callback(self, event: str, callback: Callable) -> None:
        """Odregistruje callback."""
        if event in self._callbacks:
            self._callbacks[event].remove(callback)
    
    def _fire_callbacks(self, event: str, data: Any) -> None:
        """Vyvolá zaregistrované callbacky."""
        for callback in self._callbacks.get(event, []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(callback(data))
                else:
                    callback(data)
            except Exception as e:
                _LOGGER.error("Callback error for %s: %s", event, e)
    
    async def async_update(self) -> None:
        """Periodická aktualizace stavu."""
        if not self._connected or not self._session:
            return
        
        try:
            # Aktualizuj konfiguraci
            await self._refresh_config()
            
            # Získej stav baterie, signálu atd.
            status = await self._session.send_json_command("get_status", {})
            if status:
                self._state["battery"] = status.get("battery")
                self._state["signal_strength"] = status.get("signal")
                self._state["firmware"] = status.get("fw_version")
                
        except Exception as e:
            _LOGGER.debug("Update failed: %s", e)