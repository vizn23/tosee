"""Konstanty pro ToSee Doorbell integraci."""
from homeassistant.const import Platform

DOMAIN = "tosee_doorbell"
NAME = "ToSee Doorbell (PPPP Local)"
VERSION = "1.0.0"

PLATFORMS = [
    Platform.CAMERA,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SWITCH,
]

# PPPP/CS2 protokol
PPPP_DISCOVERY_PORT = 32108
PPPP_BROADCAST_ADDR = "255.255.255.255"
DISCOVERY_TIMEOUT = 5
DISCOVERY_PAYLOAD = bytes.fromhex("F130" + "00" * 18)  # F1 30 LAN search

# DRW kanály
CHANNEL_IOCTRL = 0      # JSON příkazy
CHANNEL_VIDEO = 1       # H.264 video
CHANNEL_AUDIO = 2       # Audio (intercom)

# PPCS příkazy (z libTOCO_P2P.so)
class PPCS_CMD:
    LOGIN = "login"
    USER_AUTH = "user_auth"
    GET_REC_LIST = "get_rec_list"
    GET_SHARECODE = "get_sharecode"
    SEND_CMD = "send_cmd"
    SET_CONFIG = "set_config"
    START_PLAY_VIDEO = "start_play_video"
    START_PLAY_RECORD = "start_play_record"
    START_INTERCOM = "start_intercom"
    STOP_INTERCOM = "stop_intercom"
    UPGRADE = "upgrade"
    GET_STAT = "get_stat"
    GET_VFY_ULIST = "get_vfy_ulist"
    REMOVE_VFY_USR = "remove_vfy_usr"
    AMDMIN_CHECK = "admmin_check"

# AV stream typy
AV_STREAM_AUTH = 1
AV_STREAM_VIDEO = 2
AV_STREAM_AUDIO = 3
AV_STREAM_IOCTRL = 4

# IO typy
IO_AUTH_CHALLENGE = 1
IO_AUTH_RESPONSE = 2
IO_AUTH_OK = 3
IO_AUTH_FAILED = 4
IO_VIDEO_START = 1
IO_VIDEO_STOP = 2
IO_AUDIO_START = 1
IO_AUDIO_STOP = 2

# Výchozí heslo (z reverzního inženýrství VStarcam/Kodak)
DEFAULT_PASSWORD = "123456"

# Konfigurační klíče (z DoorBell.setConfig)
CONFIG_KEYS = {
    "low_power_mode": {"name": "Nízký režim spotřeby", "type": "bool", "cmd_id": 6},
    "led_on": {"name": "LED indikátor", "type": "bool", "cmd_id": 10},
    "pir_on": {"name": "PIR senzor pohybu", "type": "bool", "cmd_id": 11},
    "flip_angle": {"name": "Otočení obrazu", "type": "select", "cmd_id": 27, "options": [0, 90, 180, 270]},
    "ring_volume": {"name": "Hlasitost zvonění", "type": "int", "cmd_id": 13, "min": 0, "max": 10},
    "intercom_volume": {"name": "Hlasitost intercomu", "type": "int", "cmd_id": 14, "min": 0, "max": 10},
    "scene": {"name": "Scéna", "type": "select", "cmd_id": 15, "options": [0, 1, 2]},
    "utn_open": {"name": "UTN otevřeno", "type": "bool", "cmd_id": 20},
    "anti_freq": {"name": "Antiflicker frekvence", "type": "select", "cmd_id": 30, "options": [50, 60]},
    "light_sensitivity": {"name": "Citlivost světla", "type": "int", "cmd_id": 12, "min": 0, "max": 2},
    "lowpower_push": {"name": "Push v low-power", "type": "bool", "cmd_id": 28},
}

# Event typy z EventBus (com.tocoding.tosee.a.f)
class EventType:
    CONNECTING = 1
    CONNECTED = 2
    ADMIN_LOGIN = 3
    GET_AUTH_CODE = 4
    GET_REC_DAY_LIST = 5
    GET_REC_YEAR_LIST = 6
    VIDEO_START = 7
    VIDEO_STOP = 8
    TALK_START = 9
    TALK_STOP = 10
    SNAP = 11
    RECORD_PLAY = 12
    PLAY_ABNORMAL = 13
    DOWNLOAD_REC = 14
    GET_STATUS = 15
    OTHER_PLAYING = 16

# Timeouty
CONNECT_TIMEOUT = 15
COMMAND_TIMEOUT = 10
HEARTBEAT_INTERVAL = 30

# Attribution
ATTRIBUTION = "Data provided by ToSee Doorbell via local PPPP protocol"