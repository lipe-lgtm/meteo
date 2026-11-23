# Meteo

Domácí meteostanice: backend (JSON API, přihlášení, API klíče pro stanice)
a mobilní webová aplikace (PWA). Běží na Pi 5 za Apache vedle Nextcloudu.

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
| lokální DNS | dnsmasq: doma i přes VPN míří doména přímo na Pi (viz níže) |
| firewall | když běží ufw, povolí port 8088 (a DNS) jen z domácí sítě a VPN |

## Lokální DNS (split DNS)

Router bez NAT loopbacku neumí z domácí sítě otočit požadavek na veřejnou IP
zpátky na Pi a místo toho ukáže svou administraci. Proto install.sh (po souhlasu)
spustí na Pi dnsmasq, který odpovídá:

- v domácí síti: `meteo.<doména>` (a nadřazená doména s `www`, pokud veřejně
  míří na stejnou IP – typicky Nextcloud) → LAN IP Pi
- ve VPN: stejná jména → VPN adresa Pi
- všechno ostatní přepošle routeru, případně 1.1.1.1

Po instalaci zbývá ručně:
1. **Router → DHCP → DNS server** = LAN IP Pi (bez záložního, jinak by zařízení
   občas dostala veřejnou IP). Na telefonu se pak odpoj a připoj k Wi-Fi.
2. **WireGuard klienti** – do sekce `[Interface]` přidej `DNS = <VPN IP Pi>`.

Seznam jmen je v `/etc/meteo/install.conf` (`LOCAL_NAMES`); po úpravě spusť
`sudo ./install.sh`. Vypnutí: `LOCAL_DNS=0` a znovu install.sh, pak
`sudo systemctl disable --now dnsmasq`.

## Aplikace

Na telefonu otevři `https://meteo.<doména>` (doma nebo přes VPN) a přihlas se.

- **iPhone:** Safari → Sdílet → *Přidat na plochu*
- **Android:** Chrome → menu → *Přidat na plochu* / *Instalovat aplikaci*

Pak se spouští z ikony bez lišty prohlížeče. Bez spojení ukáže poslední známá data.

Kód je ve složce `web/` (bez build kroku: HTML, CSS, JavaScript). `install.sh` ho
nasadí do `/srv/meteo/web` a service workeru dá verzi podle git commitu, takže se
telefony po aktualizaci samy přepnou na novou verzi. Apache posílá přísnou CSP:
aplikace načítá jen vlastní soubory (i písmo Inter je lokálně) a mluví jen se svým API.

Pozor na cestu `/icons/`: Apache na Debianu ji globálně přesměrovává na své ikony,
proto jsou obrázky aplikace v `web/img/`.

## Předpověď a radar

- **Předpověď:** [Open-Meteo](https://open-meteo.com/) (zdarma pro nekomerční použití,
  data CC BY 4.0). Ptá se server, poloha domova tak zůstává na Pi; odpověď drží
  15 minut a při výpadku vrátí poslední známou. Srážky na příští 3 hodiny jsou
  z 15minutové předpovědi.
- **Radar:** otevřená data [ČHMÚ](https://opendata.chmi.cz/meteorology/weather/radar/) –
  kompozit radarů Brdy a Skalky po 5 minutách, 1 km rozlišení. Aplikace ukazuje
  poslední hodinu a extrapolaci pohybu srážek na +60 minut (ČHMÚ posouvá srážky
  podle větru, intenzitu nemění). Varianta „maskovaná“: plnou barvou jen srážky,
  které dopadají na zem. Snímky stahuje server, jen když je někdo otevře
  (nejvýš jednou za minutu), ořízne z nich řezy a popisky a drží je v paměti.
  Když ČHMÚ neodpovídá, aplikace přepne na [RainViewer](https://www.rainviewer.com/)
  (jen minulost, hrubší). Atribuce: „Radar © ČHMÚ“.
- **Mapa:** OpenStreetMap, ztmavená filtrem. Podle pravidel OSM aplikace posílá
  Referer, zobrazuje atribuci a dlaždice neukládá pro offline.

Polohu se `install.sh` zeptá jednou (souřadnice stačí vložit z Mapy.cz).
Změna: smaž řádek `LOCATION` v `/etc/meteo/install.conf` a spusť skript znovu.

## Upozornění

Do telefonu i počítače přes Web Push – přímo z aplikace, bez další aplikace nebo účtu.
Zapíná se v záložce **Upozornění** na každém zařízení zvlášť. Na iPhonu jen v aplikaci
přidané na plochu (Safari → Sdílet → Přidat na plochu, iOS 16.4+).

Zprávy jsou šifrované klíčem zařízení, push služba (Apple/Google/Mozilla) obsah nevidí.
Doručí se i bez VPN – ven odchází jen zpráva, do aplikace se dál chodí jen z domova/VPN.

Pravidla (prahy se nastavují v aplikaci), vyhodnocení každých 5 minut (`meteo-alerts.timer`):

| Pravidlo | Kdy | Poznámka |
|---|---|---|
| Výpadek stanice | bez dat déle než 20 min | i zpráva o návratu |
| Mráz | naměřeno ≤ 0 °C (2 měření po sobě) | projde nočním klidem, hystereze 1 °C |
| Mráz v noci | předpověď na 16 h ≤ 0 °C | jednou denně po 17:00 |
| Déšť do hodiny | 15min předpověď ≥ 0,2 mm | nejvýš jednou za 3 h |
| Zhoršený vzduch | průměr PM2.5 za 30 min ≥ 35 µg/m³ | konec pod 70 % prahu |
| Porucha čidla | chyba z diagnostiky déle než 10 min | i zpráva o opravě |
| Noční klid | 22–7 h | ostatní zprávy ráno, pokud pořád platí |

Klíč serveru (VAPID) vytvoří `install.sh` do `/etc/meteo/vapid.pem`.
Ruční vyhodnocení: `sudo -u meteo meteo-manage check-alerts -v`.

## ESP32

Ve složce `firmware/`: `main.py` a `config.example.py`. Zkopíruj `config.example.py`
jako `config.py`, vlož do něj řádky, které vypsal `install.sh`, a oba soubory nahraj
přes Thonny. `config.py` je v `.gitignore` – hesla do gitu nepatří.

**Diagnostika čidel:** firmware posílá s daty i stav každého čidla (nalezeno na
sběrnici, chyba čtení, 85 °C u DS18B20 = nedoběhlý převod, kolik bajtů poslal
PMS5003), sílu Wi-Fi a volnou paměť. Vidíš to v aplikaci v záložce **Stanice**
nebo přes `meteo-manage status`. Hodnoty mimo fyzikální rozsah server zahodí
a zapíše do logu i do diagnostiky, takže nezmizí potichu.

Displej s RSS (Pico WH): změň `TEMP_SERVER_URL` na adresu, kterou vypsal `install.sh`.

## Správa

```bash
sudo -u meteo meteo-manage status                     # poslední hodnoty a stav čidel
sudo -u meteo meteo-manage set-password jana          # nový uživatel / změna hesla (odhlásí jeho zařízení)
sudo -u meteo meteo-manage list-users                 # uživatelé, poslední přihlášení, zařízení s upozorněním
sudo -u meteo meteo-manage logout-all petr            # odhlásit všude (ztracený telefon)
sudo -u meteo meteo-manage delete-user jana           # smazat uživatele a jeho upozornění
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

## API

| Metoda | Cesta | Kdo |
|---|---|---|
| POST | `/api/v1/ingest` | stanice |
| GET | `/api/v1/station/relay` | stanice |
| POST | `/api/v1/auth/login`, `/auth/logout` · GET `/auth/me` | aplikace |
| GET | `/api/v1/stations` – online/offline, relé, diagnostika čidel | aplikace |
| GET | `/api/v1/latest` – poslední hodnota každé veličiny | aplikace |
| GET | `/api/v1/history?station=&metric=&hours=&points=` | aplikace |
| POST | `/api/v1/relay` `{station, state}` | aplikace |
| POST | `/api/v1/admin/reset` `{password, confirm:"SMAZAT"}` | aplikace |
| GET | `/api/v1/config`, `/api/v1/forecast` – poloha, předpověď | aplikace |
| GET | `/api/v1/radar`, `/api/v1/radar/<id>.png` – snímky ČHMÚ | aplikace |
| GET | `/api/v1/alerts` · POST `/alerts/settings` – pravidla, historie | aplikace |
| GET | `/api/v1/push/key` · POST `/push/subscribe`, `/push/unsubscribe`, `/push/test` | aplikace |
| GET | `/api/v1/health` | kdokoli |

Změnové požadavky z aplikace musí mít hlavičku `X-Requested-With: meteo-app`.

## Testy

```bash
pip install pytest flask && python3 -m pytest -q
```
