"""PPPP/CS2 protokol implementace pro ToSee zvonek.
   Založeno na reverzním inženýrství libPPCS_API.so, libTOCO_P2P.so,
   a existujících implementacích (aiopppp, eye4-rtsp-proxy, ha-pppp-devices)."""
import asyncio
import socket
import struct
import json
import logging
import time
import random
from typing import Optional, Dict, Any, Callable, Awaitable, Tuple, List
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from .const import (
    PPPP_DISCOVERY_PORT, PPPP_BROADCAST_ADDR, DISCOVERY_PAYLOAD,
    CHANNEL_IOCTRL, CHANNEL_VIDEO, CHANNEL_AUDIO,
    AV_STREAM_AUTH, AV_STREAM_VIDEO, AV_STREAM_AUDIO, AV_STREAM_IOCTRL,
    IO_AUTH_CHALLENGE, IO_AUTH_RESPONSE, IO_AUTH_OK, IO_AUTH_FAILED,
    IO_VIDEO_START, IO_VIDEO_STOP, IO_AUDIO_START, IO_AUDIO_STOP,
    DEFAULT_PASSWORD, CONNECT_TIMEOUT, COMMAND_TIMEOUT,
    PPCS_CMD,
)

_LOGGER = logging.getLogger(__name__)

# DRW protokol konstanty
DRW_MAGIC = 0xF1D0
DRW_FLAG_ACK = 0x01
DRW_FLAG_SYN = 0x02
DRW_FLAG_FIN = 0x04
DRW_FLAG_RST = 0x08

# PPCS příkazové kódy (z libTOCO_P2P.so strings)
PPCS_CMD_CODES = {
    "login": 0x01,
    "user_auth": 0x02,
    "get_rec_list": 0x03,
    "get_sharecode": 0x04,
    "send_cmd": 0x05,
    "set_config": 0x06,
    "start_play_video": 0x07,
    "stop_play_video": 0x08,
    "start_play_audio": 0x09,
    "stop_play_audio": 0x0A,
    "start_play_record": 0x0B,
    "stop_play_record": 0x0C,
    "start_intercom": 0x0D,
    "stop_intercom": 0x0E,
    "upgrade": 0x0F,
    "get_stat": 0x10,
    "get_vfy_ulist": 0x11,
    "remove_vfy_usr": 0x12,
    "admmin_check": 0x13,
}

class PPPPException(Exception):
    """Base exception for PPPP protocol errors."""
    pass

class AuthenticationError(PPPPException):
    """Authentication failed."""
    pass

class ConnectionError(PPPPException):
    """Connection error."""
    pass

class DRWChannel:
    """DRW spolehlivý kanál (podobně TCP nad UDP)."""
    
    def __init__(self, channel_id: int, send_func: Callable[[bytes], Awaitable[None]]):
        self.channel_id = channel_id
        self._send = send_func
        self._seq = 0
        self._ack = 0
        self._window = 10
        self._buffer = b""
        self._callbacks: Dict[int, asyncio.Future] = {}
        self._recv_queue: asyncio.Queue = asyncio.Queue()
        
    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return self._seq
    
    def _build_drw_header(self, payload_len: int, flags: int = 0) -> bytes:
        """Sestaví DRW hlavičku: [F1 D0][len BE][flags][channel][seq BE]"""
        header = struct.pack(">HIBBI", DRW_MAGIC, payload_len + 16, flags, self.channel_id, self._seq)
        return header
    
    async def send(self, data: bytes, flags: int = 0) -> None:
        """Pošle data s DRW hlavičkou."""
        seq = self._next_seq()
        header = struct.pack(">HIBBI", DRW_MAGIC, len(data) + 16, flags, self.channel_id, seq)
        await self._send(header + data)
    
    async def send_ack(self, ack_seq: int) -> None:
        """Pošle ACK."""
        header = struct.pack(">HIBBI", DRW_MAGIC, 16, DRW_FLAG_ACK, self.channel_id, ack_seq)
        await self._send(header)
    
    async def feed_data(self, data: bytes) -> List[bytes]:
        """Zpracuje příchozí data, vrací kompletní zprávy."""
        self._buffer += data
        messages = []
        
        while len(self._buffer) >= 16:  # minimální DRW hlavička
            if self._buffer[:2] != b'\xF1\xD0':
                _LOGGER.warning("Invalid DRW magic, dropping byte")
                self._buffer = self._buffer[1:]
                continue
            
            payload_len = struct.unpack(">I", self._buffer[2:6])[0] - 16
            if len(self._buffer) < 16 + payload_len:
                break  # nedostatek dat
            
            flags = self._buffer[6]
            channel = self._buffer[7]
            seq = struct.unpack(">I", self._buffer[8:12])[0]
            payload = self._buffer[12:12+payload_len]
            
            self._buffer = self._buffer[12+payload_len:]
            
            if flags & DRW_FLAG_ACK:
                # ACK zpracování
                if seq in self._callbacks:
                    self._callbacks[seq].set_result(True)
                    del self._callbacks[seq]
            else:
                # Data zpráva
                await self._recv_queue.put((seq, flags, payload))
                # auto-ACK
                asyncio.create_task(self.send_ack(seq))
                
                if payload:
                    messages.append(payload)
        
        return messages


class PPPPSession:
    """Hlavní PPPP session pro komunikaci se zvonkem."""
    
    def __init__(
        self,
        host: str,
        port: int = 32108,
        password: str = DEFAULT_PASSWORD,
        did: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.password = password.encode('ascii')
        self.did = did
        self._socket: Optional[socket.socket] = None
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol: Optional[asyncio.DatagramProtocol] = None
        self._recv_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        
        # Kanály
        self.ioctrl = DRWChannel(CHANNEL_IOCTRL, self._raw_send)
        self.video = DRWChannel(CHANNEL_VIDEO, self._raw_send)
        self.audio = DRWChannel(CHANNEL_AUDIO, self._raw_send)
        
        # Stav
        self._session_key: Optional[bytes] = None
        self._xor_key: int = 0
        self._xor_state: int = 0
        self._authenticated = False
        self._callbacks: Dict[str, asyncio.Future] = {}
        
        # Video callback
        self._video_callback: Optional[Callable[[bytes], Awaitable[None]]] = None
        self._audio_callback: Optional[Callable[[bytes], Awaitable[None]]] = None
        
    async def _raw_send(self, data: bytes) -> None:
        """Pošle raw UDP paket."""
        if self._transport:
            self._transport.sendto(data, (self.host, self.port))
    
    async def connect(self) -> bool:
        """Naváže PPPP spojení: discovery → handshake → auth."""
        try:
            # 1. Vytvoř UDP socket
            self._transport, self._protocol = await asyncio.get_event_loop().create_datagram_endpoint(
                lambda: PPPPProtocol(self._handle_raw_packet),
                local_addr=('0.0.0.0', 0),
                allow_broadcast=True,
            )
            
            # 2. Pokud nemáme DID, zjistíme ho discovery
            if not self.did:
                await self._discover_did()
            
            # 3. P2P handshake (punch)
            await self._p2p_handshake()
            
            # 4. Autentizace
            await self._authenticate()
            
            self._running = True
            asyncio.create_task(self._recv_loop())
            asyncio.create_task(self._heartbeat_loop())
            
            _LOGGER.info("PPPP session established with %s", self.host)
            return True
            
        except Exception as e:
            _LOGGER.error("Connection failed: %s", e)
            await self.disconnect()
            raise ConnectionError(f"Failed to connect: {e}")
    
    async def _discover_did(self) -> None:
        """Zjistí DID zařízení přes LAN broadcast."""
        _LOGGER.debug("Starting DID discovery on port %d", PPPP_DISCOVERY_PORT)
        
        discovery_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        discovery_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        discovery_socket.settimeout(5)
        discovery_socket.bind(('0.0.0.0', 0))
        
        # Pošli F1 30 discovery
        discovery_socket.sendto(DISCOVERY_PAYLOAD, (PPPP_BROADCAST_ADDR, PPPP_DISCOVERY_PORT))
        
        start = time.time()
        while time.time() - start < 5:
            try:
                data, addr = discovery_socket.recvfrom(1024)
                if data[:2] == b'\xF1\x41':  # F1 41 = punch response
                    # Extrahuj DID z odpovědi
                    # Formát: F1 41 [len] [DID...]
                    if len(data) >= 20:
                        self.did = data[4:24].decode('ascii', errors='ignore').strip('\x00')
                        self.host = addr[0]
                        self.port = addr[1]
                        _LOGGER.info("Discovered device: DID=%s at %s:%d", self.did, self.host, self.port)
                        break
            except socket.timeout:
                break
        
        discovery_socket.close()
        
        if not self.did:
            raise ConnectionError("No device responded to discovery")
    
    async def _p2p_handshake(self) -> None:
        """P2P punch handshake: F1 41 → echo → wait F1 42."""
        _LOGGER.debug("Starting P2P handshake with %s", self.did)
        
        # Pošli punch (F1 41)
        punch = struct.pack(">HHI", 0xF141, 0, 0) + self.did.encode('ascii').ljust(20, b'\x00')
        await self._raw_send(punch)
        
        # Čekáme na F1 42 (ready)
        for _ in range(10):
            try:
                seq, flags, payload = await asyncio.wait_for(
                    self._recv_queue.get(), timeout=1.0
                )
                if payload[:2] == b'\xF1\x42':
                    _LOGGER.debug("P2P handshake complete")
                    return
            except asyncio.TimeoutError:
                continue
        
        raise ConnectionError("P2P handshake timeout")
    
    async def _authenticate(self) -> None:
        """AES-ECB challenge-response autentizace."""
        _LOGGER.debug("Starting authentication")
        
        # 1. Pošleme auth request (IOCTRL kanál, stream=AV_STREAM_AUTH, io=IO_AUTH_CHALLENGE)
        auth_req = self._build_av_frame(
            stream_type=AV_STREAM_AUTH,
            io_type=IO_AUTH_CHALLENGE,
            payload=b''
        )
        await self.ioctrl.send(auth_req)
        
        # 2. Čekáme na challenge (16 bytů)
        challenge = None
        for _ in range(20):
            try:
                seq, flags, payload = await asyncio.wait_for(
                    self._recv_queue.get(), timeout=1.0
                )
                frame = self._parse_av_frame(payload)
                if frame and frame['stream_type'] == AV_STREAM_AUTH and frame['io_type'] == IO_AUTH_CHALLENGE:
                    challenge = frame['payload']
                    break
            except asyncio.TimeoutError:
                continue
        
        if not challenge or len(challenge) != 16:
            raise AuthenticationError("No challenge received")
        
        _LOGGER.debug("Received challenge: %s", challenge.hex())
        
        # 3. Vypočítáme response: AES128-ECB(challenge, key=password)
        cipher = AES.new(self.password.ljust(16, b'\x00')[:16], AES.MODE_ECB)
        response = cipher.encrypt(challenge)
        
        # 4. Pošleme response (io_type=2)
        auth_resp = self._build_av_frame(
            stream_type=AV_STREAM_AUTH,
            io_type=IO_AUTH_RESPONSE,
            payload=response
        )
        await self.ioctrl.send(auth_resp)
        
        # 5. Čekáme na AUTH_OK (io_type=3) s 16 byty session dat
        session_data = None
        for _ in range(20):
            try:
                seq, flags, payload = await asyncio.wait_for(
                    self._recv_queue.get(), timeout=1.0
                )
                frame = self._parse_av_frame(payload)
                if frame and frame['stream_type'] == AV_STREAM_AUTH:
                    if frame['io_type'] == IO_AUTH_OK:
                        session_data = frame['payload']
                        break
                    elif frame['io_type'] == IO_AUTH_FAILED:
                        raise AuthenticationError("Authentication failed (wrong password)")
            except asyncio.TimeoutError:
                continue
        
        if not session_data or len(session_data) != 16:
            raise AuthenticationError("No session data received")
        
        # 6. Session key = AES128-ECB-Decrypt(session_data, password)
        cipher = AES.new(self.password.ljust(16, b'\x00')[:16], AES.MODE_ECB)
        self._session_key = cipher.decrypt(session_data)
        self._xor_key = self._session_key[4]  # 5. byte jako XOR klíč
        self._xor_state = 0
        self._authenticated = True
        
        _LOGGER.info("Authentication successful, session key: %s", self._session_key.hex())
    
    def _build_av_frame(self, stream_type: int, io_type: int, payload: bytes) -> bytes:
        """Sestaví AV rámec: [payload_len+16][stream_type(3B)][io_type(2B)][payload_len(2B)][12B reserved][payload]"""
        payload_len = len(payload)
        total_len = payload_len + 16
        # 3 byte stream type (LE), 2 byte io_type (LE), 2 byte payload_len (LE), 12 byte reserved
        header = struct.pack("<IHHI", (stream_type & 0xFFFFFF) | ((io_type & 0xFFFF) << 24) | ((payload_len & 0xFFFF) << 16), 0, 0, 0)
        # Opravím formát podle逆向工程
        frame = bytearray()
        frame += struct.pack("<I", total_len)  # 4B total length
        frame += bytes([stream_type & 0xFF, (stream_type >> 8) & 0xFF, (stream_type >> 16) & 0xFF])
        frame += struct.pack("<H", io_type)
        frame += struct.pack("<H", payload_len)
        frame += b'\x00' * 12
        frame += payload
        return bytes(frame)
    
    def _parse_av_frame(self, data: bytes) -> Optional[Dict]:
        """Parse AV frame."""
        if len(data) < 20:
            return None
        total_len = struct.unpack("<I", data[:4])[0]
        stream_type = data[4] | (data[5] << 8) | (data[6] << 16)
        io_type = struct.unpack("<H", data[7:9])[0]
        payload_len = struct.unpack("<H", data[9:11])[0]
        payload = data[20:20+payload_len] if len(data) >= 20+payload_len else b''
        return {
            'total_len': total_len,
            'stream_type': stream_type,
            'io_type': io_type,
            'payload_len': payload_len,
            'payload': payload
        }
    
    def _xor_encrypt(self, data: bytes) -> bytes:
        """Rolling XOR encryption pro IOCTRL příkazy."""
        if not self._session_key:
            return data
        result = bytearray(data)
        for i in range(len(result)):
            self._xor_state ^= self._xor_key
            result[i] ^= self._xor_state & 0xFF
        return bytes(result)
    
    def _xor_decrypt(self, data: bytes) -> bytes:
        """Rolling XOR decryption (symetrické)."""
        return self._xor_encrypt(data)
    
    def _aes_encrypt_payload(self, payload: bytes) -> bytes:
        """AES encrypt frame header (bytes 4-19) with session key."""
        if not self._session_key or len(payload) < 20:
            return payload
        cipher = AES.new(self._session_key, AES.MODE_ECB)
        header = payload[4:20]
        encrypted = cipher.encrypt(header)
        return payload[:4] + encrypted + payload[20:]
    
    def _aes_decrypt_payload(self, payload: bytes) -> bytes:
        """AES decrypt frame header."""
        if not self._session_key or len(payload) < 20:
            return payload
        cipher = AES.new(self._session_key, AES.MODE_ECB)
        header = payload[4:20]
        decrypted = cipher.decrypt(header)
        return payload[:4] + decrypted + payload[20:]
    
    async def send_json_command(self, cmd: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Pošle JSON příkaz přes IOCTRL kanál."""
        if not self._authenticated:
            raise ConnectionError("Not authenticated")
        
        cmd_id = PPCS_CMD_CODES.get(cmd, 0xFF)
        
        payload = {
            "cmd": cmd,
            "seq": random.randint(1, 0xFFFF),
        }
        if params:
            payload.update(params)
        
        json_data = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        
        # XOR + AES encrypt
        encrypted = self._xor_encrypt(json_data)
        encrypted = self._aes_encrypt_payload(encrypted)
        
        # Zabal do AV frame (IOCTRL stream)
        av_frame = self._build_av_frame(
            stream_type=AV_STREAM_IOCTRL,
            io_type=cmd_id,
            payload=encrypted
        )
        
        future = asyncio.get_event_loop().create_future()
        self._callbacks[payload["seq"]] = future
        
        await self.ioctrl.send(av_frame)
        
        try:
            response = await asyncio.wait_for(future, timeout=COMMAND_TIMEOUT)
            return response
        except asyncio.TimeoutError:
            raise ConnectionError(f"Command {cmd} timeout")
    
    async def start_video_stream(self, callback: Callable[[bytes], Awaitable[None]]) -> None:
        """Zahájí video stream (H.264)."""
        self._video_callback = callback
        
        # Pošle VIDEO_START příkaz
        await self.send_json_command(PPCS_CMD.START_PLAY_VIDEO, {"channel": 0})
        
        _LOGGER.info("Video stream started")
    
    async def stop_video_stream(self) -> None:
        """Zastaví video stream."""
        self._video_callback = None
        await self.send_json_command(PPCS_CMD.STOP_PLAY_VIDEO, {"channel": 0})
        _LOGGER.info("Video stream stopped")
    
    async def start_intercom(self) -> None:
        """Zahájí dvousměrný hovor."""
        await self.send_json_command(PPCS_CMD.START_INTERCOM)
        _LOGGER.info("Intercom started")
    
    async def stop_intercom(self) -> None:
        """Ukončí hovor."""
        await self.send_json_command(PPCS_CMD.STOP_INTERCOM)
        _LOGGER.info("Intercom stopped")
    
    async def send_intercom_data(self, audio_data: bytes) -> None:
        """Pošle audio data pro intercom."""
        if not self._authenticated:
            return
        av_frame = self._build_av_frame(
            stream_type=AV_STREAM_AUDIO,
            io_type=IO_AUDIO_START,
            payload=audio_data
        )
        await self.audio.send(av_frame)
    
    async def set_config(self, key: str, value: Any) -> bool:
        """Nastaví konfiguraci zařízení."""
        result = await self.send_json_command(PPCS_CMD.SET_CONFIG, {key: value})
        return result.get("ret", -1) == 0
    
    async def get_config(self, key: str) -> Any:
        """Získá konfiguraci."""
        result = await self.send_json_command(PPCS_CMD.GET_STAT, {"attr": key})
        return result.get("value")
    
    async def get_recording_list(self, date: str) -> List[Dict]:
        """Získá seznam nahrávek pro datum (YYYYMMDD)."""
        result = await self.send_json_command(PPCS_CMD.GET_REC_LIST, {"date": date})
        return result.get("files", [])
    
    async def play_recording(self, date: str, time: str) -> bool:
        """Přehrá nahrávku."""
        result = await self.send_json_command(PPCS_CMD.START_PLAY_RECORD, {
            "date": date,
            "time": time
        })
        return result.get("ret", -1) == 0
    
    async def snapshot(self) -> bytes:
        """Pořídí snapshot."""
        result = await self.send_json_command(PPCS_CMD.SEND_CMD, {"cmd": "snap"})
        # Snapshot přijde jako binární data v eventu
        return result.get("image", b'')
    
    def _handle_raw_packet(self, data: bytes, addr: Tuple[str, int]) -> None:
        """Callback pro příchozí UDP pakety."""
        if len(data) < 16:
            return
        
        # Parse DRW header
        magic = struct.unpack(">H", data[:2])[0]
        if magic != DRW_MAGIC:
            # Možná P2P handshake packet (F1 41, F1 42)
            if data[:2] in (b'\xF1\x41', b'\xF1\x42', b'\xF1\x30', b'\xF1\x31'):
                asyncio.create_task(self._recv_queue.put((0, 0, data)))
            return
        
        payload_len = struct.unpack(">I", data[2:6])[0] - 16
        flags = data[6]
        channel = data[7]
        seq = struct.unpack(">I", data[8:12])[0]
        payload = data[12:12+payload_len]
        
        if channel == CHANNEL_IOCTRL:
            asyncio.create_task(self.ioctrl.feed_data(payload))
        elif channel == CHANNEL_VIDEO:
            asyncio.create_task(self.video.feed_data(payload))
        elif channel == CHANNEL_AUDIO:
            asyncio.create_task(self.audio.feed_data(payload))
    
    async def _recv_loop(self) -> None:
        """Hlavní smyčka pro zpracování příchozích dat."""
        while self._running:
            try:
                # Zpracuj IOCTRL frontu
                while not self.ioctrl._recv_queue.empty():
                    seq, flags, payload = await self.ioctrl._recv_queue.get()
                    await self._handle_ioctrl_frame(seq, flags, payload)
                
                # Zpracuj VIDEO frontu
                while not self.video._recv_queue.empty() and self._video_callback:
                    seq, flags, payload = await self.video._recv_queue.get()
                    frame = self._parse_av_frame(payload)
                    if frame and frame['stream_type'] == AV_STREAM_VIDEO:
                        # Dešifruj video frame
                        decrypted = self._aes_decrypt_payload(frame['payload'])
                        await self._video_callback(decrypted)
                
                # Zpracuj AUDIO frontu
                while not self.audio._recv_queue.empty() and self._audio_callback:
                    seq, flags, payload = await self.audio._recv_queue.get()
                    frame = self._parse_av_frame(payload)
                    if frame and frame['stream_type'] == AV_STREAM_AUDIO:
                        decrypted = self._aes_decrypt_payload(frame['payload'])
                        await self._audio_callback(decrypted)
                
                await asyncio.sleep(0.001)
            except Exception as e:
                _LOGGER.error("Error in recv loop: %s", e)
                await asyncio.sleep(0.1)
    
    async def _handle_ioctrl_frame(self, seq: int, flags: int, payload: bytes) -> None:
        """Zpracuje IOCTRL rámec."""
        # Dešifruj
        decrypted = self._aes_decrypt_payload(payload)
        decrypted = self._xor_decrypt(decrypted)
        
        try:
            msg = json.loads(decrypted.decode('utf-8'))
            cmd_seq = msg.get("seq")
            if cmd_seq in self._callbacks:
                self._callbacks[cmd_seq].set_result(msg)
                del self._callbacks[cmd_seq]
            
            # Handle async events (doorbell press, motion, etc.)
            if "event" in msg:
                await self._handle_event(msg["event"], msg.get("data"))
                
        except json.JSONDecodeError:
            _LOGGER.debug("Non-JSON IOCTRL payload: %s", decrypted[:100].hex())
    
    async def _handle_event(self, event_type: str, data: Any) -> None:
        """Zpracuje asynchronní události."""
        _LOGGER.debug("Event: %s, data: %s", event_type, data)
        # Zde by se volaly HA event bus callbacks
    
    async def _heartbeat_loop(self) -> None:
        """Periodické heartbeaty pro udržení spojení."""
        while self._running:
            await asyncio.sleep(30)
            if self._running:
                try:
                    await self.send_json_command(PPCS_CMD.GET_STAT, {"attr": "heartbeat"})
                except Exception:
                    pass  # ignoruj chyby heartbeatu
    
    async def disconnect(self) -> None:
        """Ukončí spojení."""
        self._running = False
        if self._transport:
            self._transport.close()
            self._transport = None
        _LOGGER.info("Disconnected from %s", self.host)


class PPPPProtocol(asyncio.DatagramProtocol):
    """Asyncio UDP protokol handler."""
    
    def __init__(self, callback: Callable[[bytes, Tuple[str, int]], None]):
        self._callback = callback
    
    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        self._callback(data, addr)
    
    def error_received(self, exc: Exception) -> None:
        _LOGGER.error("UDP error: %s", exc)