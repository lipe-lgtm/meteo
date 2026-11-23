# Vnitřní stanice – Pico WH s displejem

Stávající RSS displej (Pico WH + ST7735 160×128, CircuitPython) dostane čidla
a stane se z něj vnitřní stanice:

- měří teplotu a vlhkost uvnitř a každé 2 minuty je posílá na server jako `indoor_station`
- na displeji střídá **přehled** (doma / venku / kvalita vzduchu) a **RSS titulky**
- posílá diagnostiku čidel jako ESP32 – stav je vidět v aplikaci v záložce Stanice

## Zapojení

Displej zůstává zapojený jako dosud. Přibývá DHT22, volitelně DS18B20.

| Součástka | Vývod | Pico WH (fyzický pin) |
|---|---|---|
| **ST7735** | VCC | 3V3(OUT) – pin 36 |
| | GND | GND – pin 38 |
| | SCK / SCL | GP10 – pin 14 |
| | MOSI / SDA | GP11 – pin 15 |
| | RES / RST | GP12 – pin 16 |
| | DC / A0 | GP13 – pin 17 |
| | CS | GP16 – pin 21 |
| | BL / LED | 3V3(OUT) – pin 36 |
| **DHT22** | VCC (+) | 3V3(OUT) – pin 36 |
| | DATA (out) | **GP14 – pin 19** |
| | GND (−) | GND – pin 18 |
| **DS18B20** *(volitelně)* | VDD (červený) | 3V3(OUT) – pin 36 |
| | DQ (žlutý) | **GP15 – pin 20** |
| | GND (černý) | GND – pin 23 |

Odpory:
- **DHT22** jako samotné čidlo (4 nožky): 10 kΩ mezi DATA a 3V3. Modul na destičce
  (3 vývody) ho už má.
- **DS18B20**: 4,7 kΩ mezi DQ a 3V3 – stejně jako venku.

**Umístění:** čidla dej na kabel 15–30 cm **pod** desku nebo vedle, ne nad ni.
Pico W s Wi-Fi a podsvícení displeje hřejí a čidlo těsně u nich ukazuje o 1–3 °C víc.

**Teplota z DHT22:** tvoje kusy DHT22 ukazovaly zhruba o 3,5 °C víc než referenční
teploměr. Proto:
- máš-li DS18B20, připoj ho – teplota se pak bere z něj, z DHT22 jen vlhkost;
- bez DS18B20 nech stanici hodinu běžet vedle teploměru a rozdíl zapiš do
  `METEO_DHT_OFFSET` (např. `"-3.5"`).

## Instalace

1. **Klíč pro stanici** – na Pi:
   ```bash
   sudo -u meteo meteo-manage add-station indoor_station "Vnitřní stanice"
   ```
   Klíč se ukáže jen jednou.
2. **CircuitPython 9 nebo 10** pro *Raspberry Pi Pico W* (circuitpython.org/board/raspberry_pi_pico_w).
   RSS klient už na něm běží – stačí ověřit verzi v `boot_out.txt`.
3. **Knihovny** do složky `lib` na disku CIRCUITPY (z Adafruit bundle pro stejnou verzi):
   `adafruit_display_text/`, `adafruit_st7735r.mpy`, `adafruit_requests.mpy`,
   `adafruit_connection_manager.mpy`, `adafruit_dht.mpy`,
   a pro DS18B20 `adafruit_onewire/`, `adafruit_ds18x20.mpy`.
   Pohodlněji: `pip install circup` a pak
   ```bash
   circup install adafruit_display_text adafruit_st7735r adafruit_requests adafruit_dht adafruit_ds18x20
   ```
4. **Nastavení:** `settings.example.toml` zkopíruj jako `settings.toml` do kořene CIRCUITPY
   a doplň Wi-Fi, klíč a adresu RSS serveru.
5. **Program:** `code.py` nahraj do kořene CIRCUITPY (přepíše starého RSS klienta –
   ten si předtím zazálohuj). Pico se samo restartuje.

Do minuty by se v aplikaci měla objevit dlaždice **Uvnitř** a v záložce Stanice
vnitřní stanice se stavem čidel. Konzoli (Thonny nebo Mu) ukazuje každé měření
a odpověď serveru.

## Co je na displeji

- **Přehled** (12 s): teplota a vlhkost doma (velké), venku, kvalita vzduchu barevně
  podle PM2.5 a dole stav, když něco nejde (Wi-Fi, server, čidlo).
- **Titulky** (3 × 6 s): zdroj, titulek a dole venku/doma.
- Bez RSS serveru (`METEO_RSS_URL = ""`) jen přehled.

Venkovní data bere z `/data/simple` na stanicovém portu – funguje jen z domácí sítě.
