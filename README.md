# ToSee Doorbell - Home Assistant Custom Component

**Lokální integrace pro ToSee zvonek (a kompatibilní kamery) pomocí PPPP/CS2 protokolu.**

Žádný cloud, žádná vendor aplikace - vše běží lokálně přes LAN.

## 🎯 Funkce

| Entita | Popis |
|--------|-------|
| 📹 **Camera (Live Stream)** | H.264 video stream přes PPPP kanál 1 |
| 📸 **Camera (Snapshot)** | Jednotlivé snímky (JPEG) |
| 🔋 **Senzory** | Baterie, signál, firmware, poslední zvonění, pohyb, online stav |
| 🔘 **Tlačítka** | Restart, snapshot, zap/vyp intercom |
| 🎚️ **Number** | Hlasitost zvonění, intercom, citlivost světla, antiflicker |
| ☑️ **Switch** | LED, PIR, low-power, Utn open, lowpower push |
| 📋 **Select** | Otočení obrazu (0/90/180/270), scéna (0/1/2), antiflicker (50/60Hz) |
| 🎬 **Služby** | `take_snapshot`, `reboot`, `play_recording`, `set_config` |

## 📋 Požadavky

- Home Assistant 2023.12+
- Python 3.11+
- **Zařízení ve stejné LAN síti** (UDP broadcast 32108)
- Výchozí heslo: `123456` (pokud jste nezměnili v aplikaci)

## 🚀 Instalace

### Metoda 1: HACS (doporučeno)

1. HACS → ⋮ → **Custom repositories**
2. Přidejte toto repozitář, kategorie **Integration**
3. Nainstalujte **ToSee Doorbell (PPPP Local)**
4. Restartujte Home Assistant
5. Settings → Devices & Services → **Add Integration** → **ToSee Doorbell**

### Metoda 2: Ruční

```bash
# Zkopírujte do config adresáře
cp -r custom_components/tosee_doorbell /config/custom_components/
# Restartujte HA
```

## ⚙️ Konfigurace

### Automatické discovery (doporučeno)
1. Vyberte "Discover" v config flow
2. Vyberte zařízení ze seznamu
3. Zadejte heslo (výchozí `123456`)

### Ruční zadání
- **Host**: IP adresa zvonku
- **Password**: Heslo k zařízení (default `123456`)
- **Scan interval**: Jak často se má aktualizovat stav (default 30s)

## 🔧 Podporovaná zařízení

Testováno na:
- ToSee zvonek (ToSee_TO_SY_ZH.2.2.38)
- Mělo by fungovat na všech Tocoding/ToSee zařízeních s PPPP protokolem

## 📡 Protokol

Integrace implementuje **CS2/PPPP** protokol (reverzní inženýrství z `libPPCS_API.so`, `libTOCO_P2P.so`):

1. **Discovery**: UDP broadcast 32108 (`F1 30`)
2. **Handshake**: P2P punch (`F1 41` → echo → `F1 42` ready)
3. **Auth**: AES-128-ECB challenge-response
   - Device pošle 16B challenge
   - Klient odpoví `AES128_ECB(challenge, key=password)`
   - Device vrátí 16B session data
   - **Session key = AES128_ECB_DECRYPT(session_data, password)**
4. **Komunikace**: DRW reliable kanály
   - Kanál 0: IOCTRL (JSON příkazy, XOR + AES)
   - Kanál 1: Video (H.264)
   - Kanál 2: Audio (intercom)

## 🐛 Řešení problémů

### "Cannot connect"
- Zkontrolujte, že jste ve stejné podsíti (UDP broadcast nefunguje přes routery)
- Zkuste ručně zadat IP adresu
- Ověřte heslo (default `123456`)

### "Authentication failed"
- Heslo se liší od výchozího
- Zkuste reset zařízení na výchozí nastavení

### Video nejde
- Zkontrolujte HA logy pro chyby streamu
- Zkuste snapshot kameru jako fallback

### Zařízení nenalezeno v discovery
- Firewall blokuje UDP 32108
- Zařízení je v jiné VLAN/podsíti
- Zkuste `docker run --network host` pro HA

## 📝 Služby

```yaml
# Pořízení snapshotu
service: tosee_doorbell.take_snapshot

# Restart zařízení
service: tosee_doorbell.reboot

# Přehrání nahrávky
service: tosee_doorbell.play_recording
data:
  date: "20241219"  # YYYYMMDD
  time: "143000"    # HHMMSS

# Nastavení konfigurace
service: tosee_doorbell.set_config
data:
  key: "ring_volume"
  value: 8
```

## 🔒 Bezpečnost

- **Plně lokální** - žádná data neopouštějí vaši síť
- **Žádný cloud** - nekomunikuje s `cloud.tocoding.com`
- **Šifrování** - AES-128 pro přenos dat
- **Heslo** - uloženo jen v HA config entry (šifrované)

## 🤝 Příspěvky

Vítány jsou PR pro:
- Další ToSee modely
- Vylepšení video streamu (WebRTC)
- Two-way audio (intercom) integrace
- Motion/doorbell event detekce

## 📄 Licence

MIT License - viz LICENSE soubor.

## 🙏 Poděkování

- [Andrey79999/ha-pppp-devices](https://github.com/Andrey79999/ha-pppp-devices) - základy PPPP implementace
- [hmchan/eye4-rtsp-proxy](https://github.com/hmchan/eye4-rtsp-proxy) - H.264 decoding reference
- [dmitriytim/ha-365cam](https://github.com/dmitriytim/ha-365cam) - HA integrace vzor
- [DavidVentura/cam-reverse](https://github.com/DavidVentura/cam-reverse) - PPPP protokol dokumentace