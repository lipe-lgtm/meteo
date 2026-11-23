# Vnitřní stanice – Raspberry Pi Pico WH + displej ST7735 (CircuitPython 9/10)
#
# Měří teplotu a vlhkost uvnitř, posílá je na meteo server (stanice indoor_station)
# a na displeji střídá přehled počasí (doma / venku / vzduch) s RSS titulky.
#
# Zapojení a instalace: firmware/indoor/README.md
# Přístupové údaje: settings.toml (vzor settings.example.toml) – do gitu nepatří.

import gc
import json
import os
import time

import board
import busio
import displayio
import microcontroller
import socketpool
import terminalio
import wifi
from adafruit_display_text import label
import adafruit_requests
import adafruit_st7735r

try:
    from fourwire import FourWire            # CircuitPython 9+
except ImportError:
    from displayio import FourWire           # starší verze

FW_VERSION = "indoor-2026-11-23"

# ------------------------------------------------------------------ nastavení

def setting(name, default=None):
    value = os.getenv(name)
    return default if value is None or value == "" else value


WIFI_SSID = setting("METEO_WIFI_SSID")
WIFI_PASSWORD = setting("METEO_WIFI_PASSWORD")
SERVER = setting("METEO_SERVER", "http://192.168.1.109:8088")      # stanicový port na Pi
API_KEY = setting("METEO_API_KEY")
RSS_URL = setting("METEO_RSS_URL", "")                              # prázdné = bez titulků
DHT_OFFSET = float(setting("METEO_DHT_OFFSET", "0"))                # korekce teploty z DHT22

INGEST_URL = SERVER + "/api/v1/ingest"
OUTDOOR_URL = SERVER + "/data/simple"

MEASURE_EVERY = 120        # měření a odeslání
OUTDOOR_EVERY = 120        # venkovní data ze serveru
HEADLINES_EVERY = 10 * 60  # RSS titulky
SCREEN_SECONDS = 6         # jak dlouho je vidět jedna obrazovka
HEADLINES_PER_ROUND = 3    # kolik titulků mezi dvěma přehledy počasí

# Piny – displej zůstává, jak byl u RSS klienta
PIN_DHT = board.GP14
PIN_DS18B20 = board.GP15   # volitelné; když čidlo není, teplota jde z DHT22

# ------------------------------------------------------------------ displej

displayio.release_displays()
spi = busio.SPI(clock=board.GP10, MOSI=board.GP11)
display_bus = FourWire(spi, command=board.GP13, chip_select=board.GP16, reset=board.GP12)
display = adafruit_st7735r.ST7735R(display_bus, width=160, height=128, rotation=90,
                                   bgr=True, colstart=0, rowstart=0)

WHITE, GRAY, CYAN, YELLOW = 0xFFFFFF, 0x7D8896, 0x7CC4F2, 0xE3B341
GREEN, ORANGE, RED = 0x3FB950, 0xF0883E, 0xF85149
FONT = terminalio.FONT


def lbl(group, x, y, scale=1, color=WHITE, text=""):
    item = label.Label(FONT, text=text, color=color, scale=scale, x=x, y=y)
    group.append(item)
    return item


# Obrazovky se sestaví jednou a pak se jen mění texty (šetří paměť)
weather = displayio.Group()
lbl(weather, 4, 8, color=GRAY, text="DOMA")
w_in_temp = lbl(weather, 4, 32, scale=3)
w_in_hum = lbl(weather, 112, 34, scale=2, color=GRAY)
lbl(weather, 4, 60, color=GRAY, text="VENKU")
w_out_temp = lbl(weather, 4, 78, scale=2, color=CYAN)
w_out_hum = lbl(weather, 112, 78, scale=2, color=GRAY)
w_air = lbl(weather, 4, 102)
w_status = lbl(weather, 4, 120, color=GRAY)

news = displayio.Group()
n_source = lbl(news, 4, 8, color=GREEN)
n_title = lbl(news, 4, 26)
n_out = lbl(news, 4, 104, color=CYAN)
n_in = lbl(news, 4, 120, color=YELLOW)

message = displayio.Group()
m_text = lbl(message, 4, 56)


def show_message(text, color=WHITE):
    m_text.text = ascii_only(text)
    m_text.color = color
    display.root_group = message


# ------------------------------------------------------------------ pomocné

CZ = {"á": "a", "č": "c", "ď": "d", "é": "e", "ě": "e", "í": "i", "ň": "n", "ó": "o",
      "ř": "r", "š": "s", "ť": "t", "ú": "u", "ů": "u", "ý": "y", "ž": "z",
      "Á": "A", "Č": "C", "Ď": "D", "É": "E", "Ě": "E", "Í": "I", "Ň": "N", "Ó": "O",
      "Ř": "R", "Š": "S", "Ť": "T", "Ú": "U", "Ů": "U", "Ý": "Y", "Ž": "Z",
      "„": '"', "“": '"', "”": '"', "–": "-", "—": "-", "…": "..."}


def ascii_only(text):
    """Vestavěné písmo displeje nemá diakritiku (CircuitPython nemá str.translate)."""
    return "".join(CZ.get(ch, ch) for ch in (text or ""))


def wrap(text, width=26, max_lines=5):
    lines, line = [], ""
    for word in ascii_only(text).split():
        if len(line) + len(word) + (1 if line else 0) <= width:
            line = f"{line} {word}" if line else word
        else:
            lines.append(line)
            line = word[:width]
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [lines[max_lines - 1][:width - 3] + "..."]
    return "\n".join(lines)


def fmt_temp(value):
    return "--.-C" if value is None else f"{value:.1f}C"


def fmt_hum(value):
    return "--%" if value is None else f"{value:.0f}%"


def air_band(pm25):
    if pm25 is None:
        return "Vzduch: bez dat", GRAY
    if pm25 <= 12:
        return f"Vzduch dobry  PM2.5 {pm25:.0f}", GREEN
    if pm25 <= 35:
        return f"Vzduch stredni  PM2.5 {pm25:.0f}", YELLOW
    if pm25 <= 55:
        return f"Vzduch zhorseny PM2.5 {pm25:.0f}", ORANGE
    return f"Vzduch spatny  PM2.5 {pm25:.0f}", RED


# ------------------------------------------------------------------ čidla

DIAG = {"dht22": {"ok": None, "err": None}}
dht = None
ds = None
ow_bus = None

try:
    import adafruit_dht
    dht = adafruit_dht.DHT22(PIN_DHT)
except Exception as e:  # noqa: BLE001
    DIAG["dht22"] = {"ok": False, "err": f"init: {e}"}

try:
    from adafruit_onewire.bus import OneWireBus
    from adafruit_ds18x20 import DS18X20
    ow_bus = OneWireBus(PIN_DS18B20)
except Exception:  # noqa: BLE001 – knihovna nebo čidlo chybí, není povinné
    ow_bus = None


def find_ds18b20():
    """DS18B20 je volitelný; hledá se při každém měření, dokud se nenajde."""
    global ds
    if ds is not None or ow_bus is None:
        return ds
    try:
        devices = ow_bus.scan()
        if devices:
            ds = DS18X20(ow_bus, devices[0])
            DIAG["ds18b20"] = {"ok": None, "found": len(devices), "err": None}
            print("DS18B20 nalezen")
    except Exception as e:  # noqa: BLE001
        print("DS18B20 scan:", e)
    return ds


def read_ds18b20():
    sensor = find_ds18b20()
    if sensor is None:
        return None
    try:
        for _ in range(2):
            t = sensor.temperature
            if t != 85.0:
                break
        if t == 85.0:
            DIAG["ds18b20"].update(ok=False, err="85 °C – převod nedoběhl (napájení VDD?)", raw=t)
            return None
        if -55 <= t <= 70:
            DIAG["ds18b20"].update(ok=True, err=None, raw=round(t, 2))
            return round(t, 1)
        DIAG["ds18b20"].update(ok=False, err="nesmyslná hodnota", raw=t)
    except Exception as e:  # noqa: BLE001
        DIAG["ds18b20"].update(ok=False, err=str(e) or type(e).__name__, raw=None)
    return None


def read_dht22():
    """(teplota, vlhkost). DHT22 občas nepřečte – zkusit víckrát s pauzou 2 s."""
    if dht is None:
        return None, None
    last_err = None
    for attempt in range(3):
        try:
            t = dht.temperature
            h = dht.humidity
            if h is not None and 0 <= h <= 100 and t is not None:
                DIAG["dht22"] = {"ok": True, "err": None}
                return round(t + DHT_OFFSET, 1), round(h, 1)
            last_err = "neplatná hodnota"
        except RuntimeError as e:          # běžné: "Checksum did not validate" apod.
            last_err = str(e)
        except Exception as e:  # noqa: BLE001
            last_err = str(e) or type(e).__name__
        time.sleep(2.2)
    DIAG["dht22"] = {"ok": False, "err": last_err or "chyba čtení"}
    return None, None


def measure():
    t_dht, hum = read_dht22()
    t_ds = read_ds18b20()
    temp = t_ds if t_ds is not None else t_dht
    print(f"Uvnitr: DS18B20 {t_ds}  DHT22 {t_dht} / {hum} %  -> {temp}")
    return temp, hum


# ------------------------------------------------------------------ síť

pool = None
session = None


def connect_wifi():
    global pool, session
    if wifi.radio.connected and session is not None:
        return True
    try:
        show_message("Pripojuji Wi-Fi...", GRAY)
        wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD, timeout=20)
        pool = socketpool.SocketPool(wifi.radio)
        session = adafruit_requests.Session(pool)
        print("Wi-Fi:", wifi.radio.ipv4_address)
        return True
    except Exception as e:  # noqa: BLE001
        print("Wi-Fi chyba:", e)
        return False


def rssi():
    try:
        return wifi.radio.ap_info.rssi
    except Exception:  # noqa: BLE001
        return None


RESET_NAMES = {"POWER_ON": 1, "RESET_PIN": 2, "WATCHDOG": 3, "DEEP_SLEEP_ALARM": 4, "SOFTWARE": 5}


def reset_cause():
    name = str(microcontroller.cpu.reset_reason).split(".")[-1]
    return RESET_NAMES.get(name, name.lower())


RESET = reset_cause()
started = time.monotonic()


def send(temp, hum):
    payload = {
        "device_id": "indoor_station",
        "sensors": {"indoor": {"temperature": temp, "humidity": hum}},
        "uptime": int(time.monotonic() - started),
        "diag": {"fw": FW_VERSION, "rssi": rssi(), "mem": gc.mem_free(), "reset": RESET, "sensors": DIAG},
    }
    # Posílat bajty: délka těla se pak počítá v bajtech i s diakritikou v chybových textech
    body = json.dumps(payload).encode("utf-8")
    gc.collect()
    resp = session.post(INGEST_URL, data=body, timeout=10, headers={
        "X-API-Key": API_KEY, "Content-Type": "application/json"})
    status = resp.status_code
    try:
        if status == 200:
            rejected = resp.json().get("rejected")
            if rejected:
                print("Server odmitl:", rejected)
    finally:
        resp.close()
    print("Odeslano, HTTP", status)
    return status == 200


def get_json(url):
    gc.collect()
    resp = session.get(url, timeout=10)
    try:
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        return resp.json()
    finally:
        resp.close()
        gc.collect()


def fetch_outdoor():
    data = get_json(OUTDOOR_URL).get("sensors", {})
    out = data.get("outdoor") or {}
    air = data.get("air_quality") or {}
    return {"temp": out.get("temperature"), "hum": out.get("humidity"), "pm25": air.get("pm25")}


def fetch_headlines():
    if not RSS_URL:
        return []
    items = get_json(RSS_URL.rstrip("/") + "/headlines")
    # jen to, co se ukáže – zbytek pryč z paměti
    return [{"title": i.get("title", ""), "source": (i.get("source") or "")[:26]} for i in items[:20]]


# ------------------------------------------------------------------ obrazovky

state = {"in_temp": None, "in_hum": None, "out": {}, "sent_ok": None, "server_ok": None}


def show_weather():
    w_in_temp.text = fmt_temp(state["in_temp"])
    w_in_hum.text = fmt_hum(state["in_hum"])
    out = state["out"]
    w_out_temp.text = fmt_temp(out.get("temp"))
    w_out_hum.text = fmt_hum(out.get("hum"))
    text, color = air_band(out.get("pm25"))
    w_air.text, w_air.color = text, color
    if not wifi.radio.connected:
        w_status.text, w_status.color = "Wi-Fi odpojena", RED
    elif state["sent_ok"] is False:
        w_status.text, w_status.color = "Server neprijima data", ORANGE
    elif state["in_temp"] is None:
        w_status.text, w_status.color = "Cidlo uvnitr nemeri", ORANGE
    else:
        w_status.text, w_status.color = "", GRAY
    display.root_group = weather


def show_headline(item):
    n_source.text = ascii_only(item["source"])
    n_title.text = wrap(item["title"])
    out = state["out"]
    n_out.text = f"Venku {fmt_temp(out.get('temp'))} {fmt_hum(out.get('hum'))}"
    n_in.text = f"Doma  {fmt_temp(state['in_temp'])} {fmt_hum(state['in_hum'])}"
    display.root_group = news


# ------------------------------------------------------------------ hlavní smyčka

def main():
    global session
    if not (WIFI_SSID and API_KEY):
        show_message("Chybi settings.toml\n(METEO_WIFI_SSID,\nMETEO_API_KEY)", RED)
        while True:
            time.sleep(60)

    show_message("Meteo - vnitrni\nstanice startuje", GRAY)
    connect_wifi()

    last = {"measure": -MEASURE_EVERY, "outdoor": -OUTDOOR_EVERY, "headlines": -HEADLINES_EVERY}
    headlines, cursor, failures = [], 0, 0

    while True:
        now = time.monotonic()
        try:
            if now - last["measure"] >= MEASURE_EVERY:
                last["measure"] = now
                state["in_temp"], state["in_hum"] = measure()
                if connect_wifi():
                    state["sent_ok"] = send(state["in_temp"], state["in_hum"])
                    failures = 0 if state["sent_ok"] else failures + 1

            if now - last["outdoor"] >= OUTDOOR_EVERY and connect_wifi():
                last["outdoor"] = now
                state["out"] = fetch_outdoor()

            if now - last["headlines"] >= HEADLINES_EVERY and RSS_URL and connect_wifi():
                last["headlines"] = now
                try:
                    headlines = fetch_headlines()
                except Exception as e:  # noqa: BLE001 – titulky nejsou nutné
                    print("RSS:", e)
        except MemoryError:
            gc.collect()
            show_message("Malo pameti\nrestartuji...", RED)
            time.sleep(3)
            microcontroller.reset()
        except Exception as e:  # noqa: BLE001
            failures += 1
            print("Chyba:", e)
            if failures >= 5:
                try:
                    wifi.radio.enabled = False
                    time.sleep(1)
                    wifi.radio.enabled = True
                except Exception:  # noqa: BLE001
                    pass
                session = None
            if failures >= 30:              # ~hodina bez úspěchu → restart
                microcontroller.reset()

        show_weather()
        time.sleep(SCREEN_SECONDS * 2)
        for _ in range(min(HEADLINES_PER_ROUND, len(headlines))):
            show_headline(headlines[cursor % len(headlines)])
            cursor += 1
            time.sleep(SCREEN_SECONDS)
        gc.collect()


main()
