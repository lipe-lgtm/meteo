# Meteo

Backend pro domácí meteostanici: JSON API, přihlášení, API klíče pro stanice.
Běží na Pi 5 za Apache vedle Nextcloudu. Webová aplikace (PWA) přijde ve fázi 2.

```
ESP32 ──HTTP :8088 (jen LAN, jen 3 cesty)──┐
                                           ├─ Apache ─ gunicorn :8765 (localhost) ─ Flask ─ SQLite
Telefon/laptop ──WireGuard──HTTPS :443 ────┘
```

## Instalace (jednou)

1. U registrátora přidej DNS záznam **A** `meteo` → stejná veřejná IP jako Nextcloud.
2. Na Pi 5:

```bash
git clone git@github.com:TVUJ-UCET/meteo.git
cd meteo
sudo ./install.sh
```

Skript se zeptá na doménu, domácí síť a síť VPN (obě se pokusí zjistit sám),
pak na jméno a heslo do aplikace. Na konci vypíše řádky do `config.py` pro ESP32,
včetně API klíče – **ten se zobrazí jen jednou**.

## Aktualizace

```bash
cd meteo && git pull && sudo ./install.sh
```

Na nic se neptá, nic nemaže: aktualizuje kód, restartuje službu a zkontroluje,
že odpovídá. Pokud by nová konfigurace Apache byla vadná, vrátí předchozí
a Nextcloud ani rádio nijak neovlivní.

## Co skript dělá

| Krok | Co |
|---|---|
| nastavení | uloží odpovědi do `/etc/meteo/install.conf` (po změně spusť skript znovu) |
| porty | ověří, že 8765 a 8088 nepoužívá nic jiného (rádio, …) |
| aplikace | kód do `/opt/meteo`, vlastní virtualenv, uživatel `meteo` bez přihlášení |
| konfigurace | `/etc/meteo/meteo.env` s náhodným tajným klíčem (jen poprvé, pak se nemění) |
| databáze | `/var/lib/meteo/meteo.db`, účet do aplikace, API klíč stanice |
| služba | systemd `meteo` s omezenými právy + denní úklid starých dat |
| Apache | certifikát přes certbot (jen poprvé), web pro LAN/VPN, port 8088 pro stanice |
| firewall | když běží ufw, povolí port 8088 jen z domácí sítě |

## ESP32

Ve složce `firmware/`: `main.py` a `config.example.py`. Zkopíruj `config.example.py`
jako `config.py`, vlož do něj řádky, které vypsal `install.sh`, a oba soubory nahraj
přes Thonny. `config.py` je v `.gitignore` – hesla do gitu nepatří.

Displej s RSS (Pico WH): změň `TEMP_SERVER_URL` na adresu, kterou vypsal `install.sh`.

## Správa

```bash
sudo -u meteo meteo-manage list-stations              # kdy se která stanice ozvala
sudo -u meteo meteo-manage rotate-key outdoor_station # nový klíč (starý přestane platit)
sudo -u meteo meteo-manage set-password petr          # změna hesla
sudo journalctl -u meteo -n 50                        # log služby
```

Přenos dat ze starého serveru (volitelné, stará DB se jen čte):

```bash
sudo -u meteo meteo-manage migrate-legacy /tmp/home_center.db --since 2026-11-01
```

## Bezpečnost proti starému serveru

| Dřív | Teď |
|---|---|
| kdokoli v síti mohl spustit relé | relé jen po přihlášení, CSRF ochrana |
| reset databáze heslem `ir86` ve zdrojáku | reset vyžaduje přihlášení + znovu heslo + slovo `SMAZAT` |
| stanice posílala data bez ověření | každá stanice má vlastní API klíč |
| časy v místním čase, posuny o hodinu | vše v UTC, převod až v aplikaci |
| Flask vývojový server | gunicorn za Apache, přístup jen z LAN a VPN |

## API (pro fázi 2)

| Metoda | Cesta | Kdo |
|---|---|---|
| POST | `/api/v1/ingest` | stanice |
| GET | `/api/v1/station/relay` | stanice |
| POST | `/api/v1/auth/login`, `/auth/logout` · GET `/auth/me` | aplikace |
| GET | `/api/v1/stations` – online/offline, relé | aplikace |
| GET | `/api/v1/latest` – poslední hodnota každé veličiny | aplikace |
| GET | `/api/v1/history?station=&metric=&hours=&points=` | aplikace |
| POST | `/api/v1/relay` `{station, state}` | aplikace |
| POST | `/api/v1/admin/reset` `{password, confirm:"SMAZAT"}` | aplikace |
| GET | `/api/v1/health` | kdokoli |

Změnové požadavky z aplikace musí mít hlavičku `X-Requested-With: meteo-app`.

## Testy

```bash
pip install pytest flask && python3 -m pytest -q
```
