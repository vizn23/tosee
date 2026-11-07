"""WebRTC signaling pro ToSee zvonek - integrace s HA WebRTC camera."""
import asyncio
import logging
import json
import uuid
from typing import Optional, Dict, Any, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum

from homeassistant.components.webrtc import (
    WebRTCMessage,
    WebRTCSendMessage,
    WebRTCAnswer,
    WebRTCCandidate,
    WebRTCError,
)
from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)


class WebRTCState(Enum):
    NEW = "new"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


@dataclass
class WebRTCSession:
    """Jedna WebRTC session."""
    session_id: str
    peer_connection: Any = None  # aiortc.RTCPeerConnection
    state: WebRTCState = WebRTCState.NEW
    video_track: Any = None
    audio_track: Any = None
    data_channel: Any = None
    on_frame: Optional[Callable[[bytes], Awaitable[None]]] = None
    on_audio: Optional[Callable[[bytes], Awaitable[None]]] = None


class ToSeeWebRTCManager:
    """Správce WebRTC připojení pro ToSee zvonek.
    
    Mostí PPPP H.264/Audio streamy do WebRTC pro HA.
    Vyžaduje aiortc (pip install aiortc av).
    """
    
    def __init__(self, hass: HomeAssistant, api: "ToSeeDoorbellAPI"):
        self.hass = hass
        self.api = api
        self._sessions: Dict[str, WebRTCSession] = {}
        self._rtc_available = False
        self._check_aiortc()
    
    def _check_aiortc(self) -> None:
        """Zkontroluje dostupnost aiortc."""
        try:
            import aiortc
            import av
            self._rtc_available = True
            _LOGGER.info("aiortc available - WebRTC enabled")
        except ImportError:
            _LOGGER.warning("aiortc not installed - WebRTC disabled. Install: pip install aiortc av")
    
    async def async_handle_webrtc_message(self, message: WebRTCMessage) -> None:
        """Hlavní handler pro WebRTC zprávy z HA."""
        if not self._rtc_available:
            _LOGGER.error("WebRTC requested but aiortc not available")
            return
        
        if isinstance(message, WebRTCSendMessage):
            await self._handle_send_message(message)
        elif isinstance(message, WebRTCAnswer):
            await self._handle_answer(message)
        elif isinstance(message, WebRTCCandidate):
            await self._handle_candidate(message)
    
    async def _handle_send_message(self, message: WebRTCSendMessage) -> None:
        """Zpracuje offer od HA."""
        session_id = message.session_id
        
        if session_id in self._sessions:
            await self._cleanup_session(session_id)
        
        session = WebRTCSession(session_id=session_id)
        self._sessions[session_id] = session
        
        try:
            import aiortc
            from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
            from aiortc.contrib.media import MediaPlayer, MediaRelay
            
            pc = RTCPeerConnection()
            session.peer_connection = pc
            session.state = WebRTCState.CONNECTING
            
            # ICE candidate handler
            @pc.on("icecandidate")
            async def on_icecandidate(candidate):
                if candidate:
                    await self.hass.async_add_executor_job(
                        self._send_ice_candidate, session_id, candidate
                    )
            
            # Connection state
            @pc.on("connectionstatechange")
            async def on_connectionstatechange():
                state = pc.connectionState
                _LOGGER.debug("WebRTC connection state: %s", state)
                if state == "connected":
                    session.state = WebRTCState.CONNECTED
                    await self._start_media_tracks(session)
                elif state in ("failed", "disconnected", "closed"):
                    session.state = WebRTCState.DISCONNECTED
                    await self._cleanup_session(session_id)
            
            # Data channel pro příkazy
            @pc.on("datachannel")
            def on_datachannel(channel):
                session.data_channel = channel
                _LOGGER.debug("Data channel opened: %s", channel.label)
                
                @channel.on("message")
                def on_message(message):
                    asyncio.create_task(self._handle_data_channel_message(session, message))
            
            # Nastav remote description (offer od HA)
            offer = RTCSessionDescription(sdp=message.sdp, type=message.type)
            await pc.setRemoteDescription(offer)
            
            # Vytvoř answer
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            
            # Pošli answer zpět do HA
            await self._send_answer(session_id, pc.localDescription)
            
        except Exception as e:
            _LOGGER.error("WebRTC offer handling failed: %s", e)
            await self._cleanup_session(session_id)
            await self._send_error(session_id, str(e))
    
    async def _handle_answer(self, message: WebRTCAnswer) -> None:
        """Zpracuje answer (pro outbound WebRTC - nepoužíváme)."""
        pass
    
    async def _handle_candidate(self, message: WebRTCCandidate) -> None:
        """Přidá ICE kandidáta."""
        session = self._sessions.get(message.session_id)
        if session and session.peer_connection:
            try:
                from aiortc import RTCIceCandidate
                candidate = RTCIceCandidate(
                    sdpMid=message.sdpMid,
                    sdpMLineIndex=message.sdpMLineIndex,
                    candidate=message.candidate,
                )
                await session.peer_connection.addIceCandidate(candidate)
            except Exception as e:
                _LOGGER.error("Failed to add ICE candidate: %s", e)
    
    async def _start_media_tracks(self, session: WebRTCSession) -> None:
        """Spustí video/audio tracky z PPPP streamu."""
        if not session.peer_connection:
            return
        
        try:
            from aiortc import MediaStreamTrack
            from aiortc.contrib.media import MediaRelay
            import av
            
            relay = MediaRelay()
            
            # Video track - bridge z PPPP H.264
            class PPPPVideoTrack(MediaStreamTrack):
                kind = "video"
                
                def __init__(self, api, session):
                    super().__init__()
                    self.api = api
                    self.session = session
                    self._frame_queue: asyncio.Queue = asyncio.Queue(maxsize=30)
                    self._running = True
                    self._task = asyncio.create_task(self._receive_frames())
                
                async def _receive_frames(self):
                    """Přijímá H.264 framy z PPPP a dává do queue."""
                    async def frame_callback(frame_data: bytes):
                        if self._running:
                            try:
                                await self._frame_queue.put(frame_data)
                            except asyncio.QueueFull:
                                pass  # drop staré framy
                    
                    self.api.register_callback("video_frame", frame_callback)
                    await self.api.start_video()
                    
                    while self._running:
                        await asyncio.sleep(0.1)
                
                async def recv(self):
                    frame_data = await self._frame_queue.get()
                    # Parse H.264 frame → av.VideoFrame
                    # Jednoduchý přístup: předáme jako raw H.264
                    packet = av.Packet(frame_data)
                    packet.pts = None
                    packet.dts = None
                    return packet
                
                async def stop(self):
                    self._running = False
                    if self._task:
                        self._task.cancel()
                    await self.api.stop_video()
                    await super().stop()
            
            # Audio track - bridge z PPPP audio
            class PPPPAudioTrack(MediaStreamTrack):
                kind = "audio"
                
                def __init__(self, api):
                    super().__init__()
                    self.api = api
                    self._frame_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
                    self._running = True
                    self._task = asyncio.create_task(self._receive_audio())
                
                async def _receive_audio(self):
                    async def audio_callback(audio_data: bytes):
                        if self._running:
                            try:
                                await self._frame_queue.put(audio_data)
                            except asyncio.QueueFull:
                                pass
                    
                    self.api.register_callback("audio_frame", audio_callback)
                    await self.api.start_intercom()
                    
                    while self._running:
                        await asyncio.sleep(0.01)
                
                async def recv(self):
                    audio_data = await self._frame_queue.get()
                    # Parse audio frame
                    packet = av.Packet(audio_data)
                    return packet
                
                async def stop(self):
                    self._running = False
                    if self._task:
                        self._task.cancel()
                    await self.api.stop_intercom()
                    await super().stop()
            
            # Přidej tracky
            video_track = PPPPVideoTrack(self.api, session)
            audio_track = PPPPAudioTrack(self.api)
            
            session.video_track = video_track
            session.audio_track = audio_track
            
            session.peer_connection.addTrack(relay.subscribe(video_track))
            session.peer_connection.addTrack(relay.subscribe(audio_track))
            
            _LOGGER.info("Media tracks started for session %s", session.session_id)
            
        except Exception as e:
            _LOGGER.error("Failed to start media tracks: %s", e)
    
    async def _handle_data_channel_message(self, session: WebRTCSession, message: str) -> None:
        """Zpracuje zprávu z data channel (příkazy z HA)."""
        try:
            data = json.loads(message)
            cmd = data.get("cmd")
            
            if cmd == "ptz":
                # PTZ ovládání
                await self.api.set_config("ptz", data.get("direction"))
            elif cmd == "snapshot":
                await self.api.take_snapshot()
            elif cmd == "config":
                await self.api.set_config(data.get("key"), data.get("value"))
                
        except json.JSONDecodeError:
            _LOGGER.debug("Non-JSON data channel message: %s", message)
    
    async def _send_answer(self, session_id: str, description) -> None:
        """Pošle WebRTC answer do HA."""
        from homeassistant.components.webrtc import WebRTCAnswer
        answer = WebRTCAnswer(
            session_id=session_id,
            sdp=description.sdp,
            type=description.type,
        )
        await self.hass.helpers.dispatcher.async_dispatcher_send(
            f"webrtc_answer_{session_id}", answer
        )
    
    async def _send_ice_candidate(self, session_id: str, candidate) -> None:
        """Pošle ICE kandidáta do HA."""
        from homeassistant.components.webrtc import WebRTCCandidate
        ice = WebRTCCandidate(
            session_id=session_id,
            candidate=candidate.candidate,
            sdpMid=candidate.sdpMid,
            sdpMLineIndex=candidate.sdpMLineIndex,
        )
        await self.hass.helpers.dispatcher.async_dispatcher_send(
            f"webrtc_candidate_{session_id}", ice
        )
    
    async def _send_error(self, session_id: str, error: str) -> None:
        """Pošle chybu do HA."""
        from homeassistant.components.webrtc import WebRTCError
        err = WebRTCError(session_id=session_id, error=error)
        await self.hass.helpers.dispatcher.async_dispatcher_send(
            f"webrtc_error_{session_id}", err
        )
    
    async def _cleanup_session(self, session_id: str) -> None:
        """Ukončí session a uvolní zdroje."""
        session = self._sessions.pop(session_id, None)
        if session:
            if session.video_track:
                await session.video_track.stop()
            if session.audio_track:
                await session.audio_track.stop()
            if session.peer_connection:
                await session.peer_connection.close()
            _LOGGER.debug("Cleaned up WebRTC session %s", session_id)
    
    async def async_close(self) -> None:
        """Ukončí všechny sessiony."""
        for session_id in list(self._sessions.keys()):
            await self._cleanup_session(session_id)


# ===== HA Camera s WebRTC podporou =====

class ToSeeWebRTCCamera:
    """WebRTC camera entity pro HA."""
    
    def __init__(self, hass: HomeAssistant, api: "ToSeeDoorbellAPI", entry_id: str):
        self.hass = hass
        self.api = api
        self.entry_id = entry_id
        self.webrtc_manager = ToSeeWebRTCManager(hass, api)
        self._stream_source = None
    
    async def async_handle_async_webrtc_offer(self, offer_sdp: str, session_id: str) -> str:
        """HA volá tuto metodu pro WebRTC offer."""
        from homeassistant.components.webrtc import WebRTCSendMessage
        
        message = WebRTCSendMessage(
            session_id=session_id,
            sdp=offer_sdp,
            type="offer",
        )
        await self.webrtc_manager.async_handle_webrtc_message(message)
        
        # Počkej na answer
        # V reálné implementaci by se použil dispatcher callback
        # Zde zjednodušeně vrátíme placeholder
        return ""  # answer bude doručen async přes dispatcher
    
    async def async_on_webrtc_candidate(self, candidate: dict, session_id: str) -> None:
        """HA volá pro ICE kandidáty."""
        from homeassistant.components.webrtc import WebRTCCandidate
        message = WebRTCCandidate(
            session_id=session_id,
            candidate=candidate["candidate"],
            sdpMid=candidate["sdpMid"],
            sdpMLineIndex=candidate["sdpMLineIndex"],
        )
        await self.webrtc_manager.async_handle_webrtc_message(message)
    
    async def async_close_webrtc_session(self, session_id: str) -> None:
        """HA volá pro ukončení session."""
        await self.webrtc_manager._cleanup_session(session_id)