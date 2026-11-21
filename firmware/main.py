from machine import Pin, ADC, I2C, UART, reset
import time
from dht import DHT22
import onewire
import ds18x20
import network
import urequests
import json
import gc

# =========================================================
# VENKOVNÍ METEO STANICE - ESP32 WROOM (MicroPython)
# =========================================================
# GPIO mapování (viz komentáře u každého senzoru):
#   DS18B20    -> GPIO 4   (OneWire)
#   DHT22      -> GPIO 16  (vlhkost)
#   BH1750 SDA -> GPIO 21  (I2C default)
#   BH1750 SCL -> GPIO 22  (I2C default)
#   PMS5003 TX -> GPIO 25  (ESP RX, UART2)
#   PMS5003 RX -> GPIO 17  (ESP TX, UART2)
#   PMS5003 SET-> GPIO 26
#   Relé       -> GPIO 27
#   Soil (jaro)-> GPIO 34  (ADC1, input-only)
#   LED        -> GPIO 2   (onboard)
# =========================================================

# Přístupové údaje jsou v samostatném souboru config.py (nesdílet, necommitovat)
from config import SSID, PASSWORD, SERVER, API_KEY

# Onboard LED (GPIO 2)
led = Pin(2, Pin.OUT)

# DS18B20 teplotní senzor (OneWire) - GPIO 4
ow_outdoor = onewire.OneWire(Pin(4))
ds_outdoor = ds18x20.DS18X20(ow_outdoor)
roms_outdoor = ds_outdoor.scan()
print(f"DS18B20 venkovní nalezeno: {len(roms_outdoor)} senzorů")
if roms_outdoor:
    print(f"  ROM ID: {roms_outdoor[0].hex()}")

# DHT22 senzor (jen vlhkost!) - GPIO 16
sensor_outdoor_hum = DHT22(Pin(16))
print("DHT22 inicializován na GPIO 16")

# I2C pro BH1750 - hardwarové default SDA=21, SCL=22
i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=400000)
print(f"I2C inicializován, nalezená zařízení: {i2c.scan()}")

# BH1750 světelný senzor (I2C adresa 0x23)
BH1750_ADDR = 0x23
BH1750_POWER_ON = 0x01
BH1750_CONTINUOUS_HIGH_RES = 0x10

# PMS5003 Air Quality senzor (UART2)
# TX (PMS) -> GPIO 25 (ESP RX)
# RX (PMS) -> GPIO 17 (ESP TX)
print("Inicializace PMS5003...")
pms_uart = UART(2, baudrate=9600, tx=Pin(17), rx=Pin(25), timeout=1000)
pms_set_pin = Pin(26, Pin.OUT)
pms_set_pin.value(0)  # Začni v sleep mode
print("PMS5003 UART2 inicializován, SET=GPIO 26")

# Půdní vlhkoměr (ADC1) - GPIO 34, připraveno na jaro
# POZOR: GPIO 34 je input-only a ADC1 (funguje i při WiFi)
soil_sensor = ADC(Pin(34))
soil_sensor.atten(ADC.ATTN_11DB)   # Plný rozsah 0-3.3V
soil_sensor.width(ADC.WIDTH_12BIT) # 12-bit (0-4095)
print("Soil moisture na GPIO 34 (ADC1, neaktivní)")

# Relé modul - GPIO 27
relay = Pin(27, Pin.OUT)
relay.value(0)
print("Relé na GPIO 27 (výchozí: VYP)")

# Server nastavení
INGEST_URL = SERVER + "/api/v1/ingest"
RELAY_URL = SERVER + "/api/v1/station/relay"
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
DEVICE_ID = "outdoor_station"

MEASUREMENT_INTERVAL = 120  # 2 minuty
ERROR_RETRY_INTERVAL = 10

wifi = network.WLAN(network.STA_IF)
wifi.active(True)

# Globální proměnné pro air quality
last_aq_measurement = 0
last_valid_air_quality = None

print("\n" + "="*40)
print("HARDWARE KONFIGURACE (ESP32):")
print("="*40)
print("GPIO 4  - DS18B20 DATA")
print("GPIO 16 - DHT22 DATA")
print("GPIO 21 - BH1750 SDA (I2C)")
print("GPIO 22 - BH1750 SCL (I2C)")
print("GPIO 25 - PMS5003 TX (ESP RX)")
print("GPIO 17 - PMS5003 RX (ESP TX)")
print("GPIO 26 - PMS5003 SET")
print("GPIO 27 - Relé")
print("GPIO 34 - Soil moisture (ADC1) - JARO")
print("="*40 + "\n")

def connect_wifi():
    """Připojení k WiFi s timeoutem"""
    print("Připojování k WiFi...")
    wifi.connect(SSID, PASSWORD)
    timeout = 20
    start_time = time.time()

    while not wifi.isconnected():
        led.value(not led.value())
        time.sleep(0.5)
        if time.time() - start_time > timeout:
            print("Připojení k WiFi selhalo - restart...")
            time.sleep(1)
            reset()

    led.value(1)
    ip = wifi.ifconfig()[0]
    print(f"✓ Připojeno k WiFi: {ip}")
    return True

def init_bh1750():
    """Inicializace BH1750 světelného senzoru"""
    try:
        devices = i2c.scan()
        if BH1750_ADDR not in devices:
            print(f"✗ BH1750 nenalezen na adrese 0x{BH1750_ADDR:02x}")
            print(f"  Nalezená zařízení: {[hex(d) for d in devices]}")
            return False

        i2c.writeto(BH1750_ADDR, bytes([BH1750_POWER_ON]))
        time.sleep(0.01)
        i2c.writeto(BH1750_ADDR, bytes([BH1750_CONTINUOUS_HIGH_RES]))
        time.sleep(0.18)
        print(f"✓ BH1750 inicializován (0x{BH1750_ADDR:02x})")
        return True
    except Exception as e:
        print(f"✗ Chyba inicializace BH1750: {e}")
        return False

def read_bh1750():
    """Čtení hodnoty z BH1750 v luxech"""
    try:
        data = i2c.readfrom(BH1750_ADDR, 2)
        lux = (data[0] << 8 | data[1]) / 1.2
        return round(lux, 1)
    except Exception as e:
        print(f"Chyba čtení BH1750: {e}")
        return None

def light_category_from_lux(lux):
    """Kategorie podle LUX hodnoty"""
    if lux < 1:
        return "Tma"
    elif lux < 10:
        return "Velmi sero"
    elif lux < 50:
        return "Sero"
    elif lux < 200:
        return "Tmava mistnost"
    elif lux < 500:
        return "Mistnost"
    elif lux < 1000:
        return "Svetla mistnost"
    elif lux < 10000:
        return "Jasno"
    elif lux < 25000:
        return "Venku jasno"
    else:
        return "Prime slunce"

def pms5003_wake():
    """Probuď PMS5003 ze sleep mode"""
    pms_set_pin.value(1)
    time.sleep(1)
    print("  PMS5003: Probuzeno (SET=HIGH)")

def pms5003_sleep():
    """Uspej PMS5003 (šetří energii a prodlužuje životnost)"""
    pms_set_pin.value(0)
    print("  PMS5003: Sleep mode (SET=LOW)")

def read_pms5003():
    """
    Čtení PM2.5 a PM10 z PMS5003
    Vrací: {"pm25": int, "pm10": int, "aqi": int, "status": str}
    """
    print("\n--- PMS5003 Měření začíná ---")
    try:
        pms5003_wake()

        print("  Stabilizace: čekám 30s...")
        for i in range(6):
            time.sleep(5)
            print(f"    {(i+1)*5}s...")

        while pms_uart.any():
            pms_uart.read(1)
        print("  Buffer vyčištěn")

        attempts = 0
        max_attempts = 10

        while attempts < max_attempts:
            time.sleep(1)
            available = pms_uart.any()
            print(f"  Pokus {attempts+1}/{max_attempts}: Dostupno {available} bytů")

            if available >= 32:
                data = pms_uart.read(32)
                print(f"    Start bytes: 0x{data[0]:02x} 0x{data[1]:02x}")

                if data[0] == 0x42 and data[1] == 0x4d:
                    checksum = sum(data[0:30])
                    received_checksum = (data[30] << 8) | data[31]
                    print(f"    Checksum: vypočtený={checksum}, přijatý={received_checksum}")

                    if checksum == received_checksum:
                        pm25 = (data[12] << 8) | data[13]
                        pm10 = (data[14] << 8) | data[15]
                        aqi = calculate_aqi(pm25, pm10)

                        pms5003_sleep()

                        print(f"✓ PMS5003 DATA: PM2.5={pm25} µg/m³, PM10={pm10} µg/m³, AQI={aqi}")
                        print("--- PMS5003 Měření dokončeno ---\n")

                        return {
                            "pm25": pm25,
                            "pm10": pm10,
                            "aqi": aqi,
                            "status": get_aqi_status(aqi)
                        }
                    else:
                        print("    ✗ Checksum error")
                else:
                    print("    ✗ Špatné start bytes")

            attempts += 1

        print("✗ PMS5003: Timeout - žádná platná data")
        pms5003_sleep()
        print("--- PMS5003 Měření selhalo ---\n")
        return None

    except Exception as e:
        print(f"✗ Chyba čtení PMS5003: {e}")
        pms5003_sleep()
        print("--- PMS5003 Měření přerušeno chybou ---\n")
        return None

def calculate_aqi(pm25, pm10):
    """Zjednodušený výpočet AQI podle US EPA (na základě PM2.5)"""
    if pm25 <= 12.0:
        return int((50 - 0) / (12.0 - 0) * (pm25 - 0) + 0)
    elif pm25 <= 35.4:
        return int((100 - 51) / (35.4 - 12.1) * (pm25 - 12.1) + 51)
    elif pm25 <= 55.4:
        return int((150 - 101) / (55.4 - 35.5) * (pm25 - 35.5) + 101)
    elif pm25 <= 150.4:
        return int((200 - 151) / (150.4 - 55.5) * (pm25 - 55.5) + 151)
    elif pm25 <= 250.4:
        return int((300 - 201) / (250.4 - 150.5) * (pm25 - 150.5) + 201)
    else:
        return int((500 - 301) / (500.4 - 250.5) * (pm25 - 250.5) + 301)

def get_aqi_status(aqi):
    """Status kvality vzduchu podle AQI"""
    if aqi <= 50:
        return "Vyborny"
    elif aqi <= 100:
        return "Dobry"
    elif aqi <= 150:
        return "Prijatelny"
    elif aqi <= 200:
        return "Spatny"
    elif aqi <= 300:
        return "Velmi spatny"
    else:
        return "Nebezpecny"

def read_ds18b20(ds_sensor, roms, sensor_name):
    """Čtení teploty z DS18B20"""
    try:
        if not roms:
            print(f"{sensor_name}: Žádný DS18B20 nenalezen!")
            return None

        ds_sensor.convert_temp()
        time.sleep_ms(750)
        temp = ds_sensor.read_temp(roms[0])

        if -40 <= temp <= 85:
            print(f"{sensor_name} teplota: {temp}°C")
            return round(temp, 1)
        else:
            print(f"{sensor_name}: Neplatná teplota {temp}°C")
            return None
    except Exception as e:
        print(f"{sensor_name}: Chyba DS18B20: {e}")
        return None

def read_dht22_humidity(sensor, sensor_name):
    """Čtení POUZE vlhkosti z DHT22"""
    for attempt in range(3):
        try:
            time.sleep(3)
            sensor.measure()
            time.sleep_ms(100)

            humidity = sensor.humidity()  # na ESP32 je to metoda

            if humidity is not None and 0 <= humidity <= 100:
                print(f"{sensor_name} vlhkost: {humidity}%")
                return round(humidity, 1)
            else:
                print(f"{sensor_name}: Neplatná vlhkost, pokus {attempt + 1}/3")
                time.sleep(1)
        except Exception as e:
            print(f"{sensor_name}: Chyba {e}, pokus {attempt + 1}/3")
            time.sleep(1)

    print(f"{sensor_name}: Selhalo všech 3 pokusů čtení vlhkosti")
    return None

def read_soil_moisture():
    """Čtení půdní vlhkosti - ZATÍM NEAKTIVNÍ (čeká se na jaro)"""
    return None

    # Pro jaro - odkomentuj:
    # try:
    #     readings = []
    #     for _ in range(5):
    #         raw = soil_sensor.read_u16()
    #         readings.append(raw)
    #         time.sleep(0.01)
    #
    #     avg_raw = sum(readings) // len(readings)
    #
    #     # Kalibrace pro ESP32 (12-bit ADC, read_u16 škáluje na 16-bit)
    #     MAX_DRY = 65535  # Senzor ve vzduchu
    #     MIN_WET = 25000  # Senzor ve vodě
    #
    #     moisture = 100 - ((avg_raw - MIN_WET) / (MAX_DRY - MIN_WET) * 100)
    #     moisture = max(0, min(100, moisture))
    #
    #     return {
    #         "moisture": round(moisture, 1),
    #         "raw": avg_raw,
    #         "status": get_soil_status(moisture)
    #     }
    # except Exception as e:
    #     print(f"Chyba čtení půdního senzoru: {e}")
    #     return None

def get_soil_status(moisture):
    """Status půdy podle vlhkosti"""
    if moisture < 20:
        return "Velmi sucho"
    elif moisture < 40:
        return "Sucho"
    elif moisture < 60:
        return "Optimalni"
    elif moisture < 80:
        return "Vlhko"
    else:
        return "Velmi vlhko"

def set_relay(state):
    """Nastavení stavu relé"""
    try:
        relay.value(1 if state else 0)
        print(f"Relé nastaveno: {'ZAP' if state else 'VYP'}")
        return True
    except Exception as e:
        print(f"Chyba při nastavení relé: {e}")
        return False

def check_relay_command():
    """Kontrola příkazů relé ze serveru"""
    try:
        response = urequests.get(RELAY_URL, headers=HEADERS, timeout=5)
        if response.status_code != 200:
            print(f"Relé: server vrátil HTTP {response.status_code}")
            response.close()
            return relay.value()
        data = response.json()
        response.close()

        if 'relay_state' in data:
            current_state = relay.value()
            new_state = data['relay_state']

            if current_state != new_state:
                print(f"Synchronizace relé: {current_state} -> {new_state}")
                set_relay(new_state)

        return data.get('relay_state', 0)
    except Exception as e:
        print(f"Chyba při kontrole relé: {e}")
        return relay.value()

def send_data(temp_outdoor, hum_outdoor, light_lux, soil_data, air_quality):
    """Odesílání dat na Flask server - NEPOSÍLÁ None pro air_quality"""
    try:
        sensors_data = {
            "outdoor": {
                "temperature": temp_outdoor,
                "humidity": hum_outdoor
            },
            "light": {
                "lux": light_lux,
                "category": light_category_from_lux(light_lux) if light_lux else None
            }
        }

        if soil_data is not None:
            sensors_data["soil"] = soil_data

        if air_quality is not None:
            sensors_data["air_quality"] = air_quality

        data = {
            "device_id": DEVICE_ID,
            "sensors": sensors_data,
            "relay_state": relay.value(),
            "voltage": 5.0,
            "uptime": time.time()
        }

        print(f"\n>>> Odesílám data na server:")
        print(f"  Venkovní: {temp_outdoor}°C / {hum_outdoor}%")
        if light_lux:
            print(f"  Světlo: {light_lux} lux ({light_category_from_lux(light_lux)})")
        if soil_data:
            print(f"  Půda: {soil_data['moisture']}% ({soil_data['status']})")
        if air_quality:
            print(f"  Air Quality: PM2.5={air_quality['pm25']}, PM10={air_quality['pm10']}, AQI={air_quality['aqi']} ({air_quality['status']})")
        else:
            print(f"  Air Quality: (neměřeno)")
        print(f"  Relé: {relay.value()}")

        response = urequests.post(INGEST_URL, data=json.dumps(data), headers=HEADERS, timeout=10)
        status = response.status_code
        response.close()

        if status == 200:
            print(f"✓ Data odeslána (HTTP {status})")
            return True
        else:
            print(f"✗ Server vrátil HTTP {status}")
            return False

    except Exception as e:
        print(f"✗ Chyba při odesílání dat: {e}")
        return False

def read_all_sensors():
    """Čtení ze všech senzorů"""
    global last_aq_measurement, last_valid_air_quality

    print("\n" + "="*40)
    print("ČTENÍ SENZORŮ")
    print("="*40)

    temp_out = read_ds18b20(ds_outdoor, roms_outdoor, "Venkovní")
    time.sleep(1)

    hum_out = read_dht22_humidity(sensor_outdoor_hum, "Venkovní")
    time.sleep(1)

    light_lux = read_bh1750()
    if light_lux:
        print(f"Světlo: {light_lux} lux ({light_category_from_lux(light_lux)})")

    soil_data = read_soil_moisture()
    if soil_data:
        print(f"Půda: {soil_data['moisture']}% ({soil_data['status']}, raw={soil_data['raw']})")

    air_quality = None
    current_time = time.time()
    time_since_last = current_time - last_aq_measurement

    if time_since_last > 600:  # 10 minut
        print(f"Air Quality měření (poslední před {int(time_since_last)}s)")
        air_quality = read_pms5003()

        if air_quality is not None:
            last_valid_air_quality = air_quality

        last_aq_measurement = current_time
    else:
        remaining = int(600 - time_since_last)
        print(f"Air Quality: příští měření za {remaining}s")
        if last_valid_air_quality:
            print(f"  (poslední platná: PM2.5={last_valid_air_quality['pm25']}, AQI={last_valid_air_quality['aqi']})")

    print("="*40 + "\n")

    return temp_out, hum_out, light_lux, soil_data, air_quality

def main():
    """Hlavní smyčka programu"""
    connect_wifi()

    bh1750_ok = init_bh1750()
    if not bh1750_ok:
        print("⚠ VAROVÁNÍ: BH1750 není dostupný!")

    pms5003_sleep()

    last_measurement_time = 0
    last_relay_check = 0

    print("\n" + "="*40)
    print("VENKOVNÍ STANICE SPUŠTĚNA (ESP32)")
    print("="*40)
    print(f"Device ID: {DEVICE_ID}")
    print(f"Server: {SERVER}")
    print(f"Měřicí interval: {MEASUREMENT_INTERVAL}s")
    print(f"Air Quality interval: 600s (10 min)")
    print("="*40 + "\n")

    while True:
        current_time = time.time()

        if not wifi.isconnected():
            print("⚠ WiFi odpojeno, znovu připojuji...")
            connect_wifi()

        if current_time - last_relay_check >= 5:
            check_relay_command()
            last_relay_check = current_time

        if current_time - last_measurement_time >= MEASUREMENT_INTERVAL:
            temp_out, hum_out, light_lux, soil_data, air_quality = read_all_sensors()

            has_data = any([
                temp_out is not None,
                light_lux is not None
            ])

            if has_data:
                if send_data(temp_out, hum_out, light_lux, soil_data, air_quality):
                    last_measurement_time = current_time
                else:
                    print(f"⚠ Retry za {ERROR_RETRY_INTERVAL}s")
                    time.sleep(ERROR_RETRY_INTERVAL)
            else:
                print("⚠ Žádná platná data ze senzorů")
                time.sleep(ERROR_RETRY_INTERVAL)

            gc.collect()
            print(f"Volná paměť: {gc.mem_free()} bytů\n")

        time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n>>> Program ukončen uživatelem")
        pms5003_sleep()
    except Exception as e:
        print(f"\n!!! KRITICKÁ CHYBA: {e}")
        try:
            with open('error_log.txt', 'a') as f:
                f.write(f"{time.time()}: {str(e)}\n")
        except:
            pass
        print("Restart za 5s...")
        time.sleep(5)
        reset()
