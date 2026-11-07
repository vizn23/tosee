"""Test script pro ověření PPPP protokolu před instalací do HA."""
import asyncio
import sys
import os
import importlib

# Přidej projektový kořen do path pro import custom_components
_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

_pppp = importlib.import_module("custom_components.tosee_doorbell.pppp_protocol")
PPPPSession = _pppp.PPPPSession
ConnectionError = _pppp.ConnectionError
AuthenticationError = _pppp.AuthenticationError

_tosee_api = importlib.import_module("custom_components.tosee_doorbell.tosee_api")
ToSeeDoorbellAPI = _tosee_api.ToSeeDoorbellAPI

async def test_discovery():
    """Test discovery v LAN."""
    print("🔍 Hledám ToSee zařízení v LAN...")
    
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(5)
    sock.bind(('0.0.0.0', 0))
    
    DISCOVERY_PAYLOAD = bytes.fromhex("F130" + "00" * 18)
    sock.sendto(DISCOVERY_PAYLOAD, ('255.255.255.255', 32108))
    
    devices = []
    try:
        while True:
            data, addr = sock.recvfrom(1024)
            if data[:2] == b'\xF1\x41' and len(data) >= 24:
                did = data[4:24].decode('ascii', errors='ignore').strip('\x00')
                print(f"  ✅ Nalezeno: DID={did} IP={addr[0]}")
                devices.append({"did": did, "host": addr[0], "port": addr[1]})
    except socket.timeout:
        pass
    finally:
        sock.close()
    
    return devices


async def test_connection(host: str, password: str = "123456"):
    """Test připojení k konkrétnímu zařízení."""
    print(f"\n🔌 Připojuji k {host}...")
    
    api = ToSeeDoorbellAPI(host=host, password=password)
    
    try:
        await api.connect()
        print("  ✅ Připojeno úspěšně!")
        
        # Test konfigurace
        print("\n📋 Čtu konfiguraci...")
        for key in ["led_on", "pir_on", "ring_volume", "flip_angle"]:
            val = await api.get_config(key)
            print(f"  {key}: {val}")
        
        # Test snapshotu
        print("\n📸 Pořídím snapshot...")
        snapshot = await api.take_snapshot()
        if snapshot:
            print(f"  ✅ Snapshot OK: {len(snapshot)} bytes")
        else:
            print("  ⚠️ Snapshot selhal")
        
        # Test videa (jen start/stop)
        print("\n🎬 Test video streamu...")
        await api.start_video()
        await asyncio.sleep(2)
        await api.stop_video()
        print("  ✅ Video start/stop OK")
        
        await api.disconnect()
        print("\n✅ Všechny testy prošly!")
        return True
        
    except AuthenticationError:
        print("  ❌ Autentizace selhala - špatné heslo")
        return False
    except ConnectionError as e:
        print(f"  ❌ Připojení selhalo: {e}")
        return False
    except Exception as e:
        print(f"  ❌ Neočekávaná chyba: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    print("=" * 60)
    print("ToSee Doorbell - PPPP Protocol Test")
    print("=" * 60)
    
    # 1. Discovery
    devices = await test_discovery()
    
    if not devices:
        print("\n⚠️ Žádná zařízení nenalezena automaticky.")
        host = input("Zadej IP adresu zvonku ručně: ").strip()
        if not host:
            return
        devices = [{"host": host, "did": "manual"}]
    
    # 2. Test připojení k prvnímu zařízení
    device = devices[0]
    password = input(f"Heslo pro {device['host']} (default 123456): ").strip() or "123456"
    
    await test_connection(device["host"], password)


if __name__ == "__main__":
    asyncio.run(main())