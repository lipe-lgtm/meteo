# Zkopíruj jako config.py na ESP32 (vedle main.py) a vyplň.
# Tenhle soubor obsahuje tajné údaje – nesdílej ho a nedávej do gitu.

SSID = "nazev-wifi"
PASSWORD = "heslo-wifi"

# Pi 5 v domácí síti, port 8088 = vstup pouze pro stanice (viz deploy/apache/meteo.conf)
SERVER = "http://192.168.1.XXX:8088"

# Klíč vypíše na Pi 5 příkaz:  python3 manage.py add-station outdoor_station
API_KEY = "mk_..."
