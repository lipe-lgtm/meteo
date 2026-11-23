#!/usr/bin/env bash
# Meteo backend – instalace i aktualizace na Pi 5 (Apache vedle Nextcloudu).
#
#   Poprvé:       git clone … && cd meteo && sudo ./install.sh
#   Aktualizace:  git pull && sudo ./install.sh
#
# Skript je idempotentní: opakované spuštění jen aktualizuje kód a konfiguraci,
# nic nesmaže. Na hodnoty, které nezná, se zeptá jen při prvním běhu
# a uloží je do /etc/meteo/install.conf.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR=/opt/meteo
DATA_DIR=/var/lib/meteo
ETC_DIR=/etc/meteo
WEB_DIR=/srv/meteo/web
CONF="$ETC_DIR/install.conf"
ENV_FILE="$ETC_DIR/meteo.env"
# "zz-" = načte se jako poslední, takže meteo nikdy není výchozí web pro cizí domény
SITE=/etc/apache2/sites-available/zz-meteo.conf
OLD_SITE=/etc/apache2/sites-available/meteo.conf   # název do verze b516668
STATION_ID=outdoor_station

say()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

ask() {  # ask PROMĚNNÁ "otázka" [výchozí]
    local var=$1 question=$2 default=${3:-} answer
    if [[ -n $default ]]; then
        read -rp "    $question [$default]: " answer
    else
        read -rp "    $question: " answer
    fi
    answer=${answer:-$default}
    [[ -n $answer ]] || die "Tahle hodnota je povinná."
    printf -v "$var" '%s' "$answer"
}

net_of() { python3 -c 'import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)' "$1"; }
valid_net() { python3 -c 'import ipaddress,sys; ipaddress.ip_network(sys.argv[1], strict=False)' "$1" 2>/dev/null; }
port_busy() { ss -Htln "sport = :$1" | grep -q .; }
dns53_foreign() { ss -Hlnup "sport = :53" 2>/dev/null; ss -Hlntp "sport = :53" 2>/dev/null; } # kdo drží port 53
public_a() {  # veřejná IPv4 adresa jména (ne odpověď našeho lokálního DNS)
    local ip=""
    command -v dig >/dev/null && ip=$(dig +short +time=2 +tries=1 A "$1" @1.1.1.1 2>/dev/null | grep -E '^[0-9.]+$' | head -1)
    [[ -n $ip ]] || ip=$(getent ahostsv4 "$1" 2>/dev/null | awk '{print $1; exit}')
    printf '%s' "$ip"
}
manage() { runuser -u meteo -- /usr/local/bin/meteo-manage "$@"; }
set_env() {  # set_env KLÍČ HODNOTA – nastaví nebo přidá řádek v meteo.env, zbytek nechá
    if grep -q "^$1=" "$ENV_FILE"; then sed -i "s#^$1=.*#$1=$2#" "$ENV_FILE"; else echo "$1=$2" >> "$ENV_FILE"; fi
}
parse_coords() {  # "50.0875, 14.4214" (jak to zkopíruje mapy.cz / Google) -> "50.0875 14.4214"
    python3 - "$1" <<'PY'
import re, sys
nums = re.findall(r"-?\d+(?:[.,]\d+)?", sys.argv[1].replace(", ", " "))
vals = [float(n.replace(",", ".")) for n in nums]
if len(vals) == 2 and -90 <= vals[0] <= 90 and -180 <= vals[1] <= 180:
    print(f"{vals[0]:.4f} {vals[1]:.4f}")
else:
    sys.exit(1)
PY
}

health() {
    python3 - "$1" <<'PY'
import sys, urllib.request
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/v1/health", timeout=2) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
}

# ------------------------------------------------------------------ předpoklady
[[ $EUID -eq 0 ]] || die "Spusť přes sudo:  sudo ./install.sh"
[[ -f $SRC/wsgi.py && -d $SRC/meteo ]] || die "install.sh musí ležet v kořeni repozitáře meteo."
command -v apache2ctl >/dev/null || die "Apache (apache2) není nainstalovaný."
command -v systemctl >/dev/null || die "Chybí systemd."

FIRST_RUN=0
[[ -f $CONF ]] || FIRST_RUN=1

# ------------------------------------------------------------------ 1) nastavení
say "Nastavení"
if [[ $FIRST_RUN -eq 1 ]]; then
    iface=$(ip -4 route show default | awk '{print $5; exit}')
    lan_guess=$(ip -4 -o route show dev "$iface" scope link 2>/dev/null | awk '{print $1; exit}')
    wg_guess=""
    if ip -4 -o addr show wg0 >/dev/null 2>&1 && [[ -n $(ip -4 -o addr show wg0) ]]; then
        wg_guess=$(net_of "$(ip -4 -o addr show wg0 | awk '{print $4; exit}')")
    fi

    echo "    První instalace – pár otázek (Enter = hodnota v závorce)."
    ask DOMAIN  "Doména aplikace (např. meteo.mojedomena.cz)"
    ask LAN_NET "Domácí síť"         "${lan_guess:-192.168.1.0/24}"
    ask WG_NET  "Síť WireGuard VPN"  "${wg_guess:-10.8.0.0/24}"
    valid_net "$LAN_NET" || die "Neplatný rozsah sítě: $LAN_NET"
    valid_net "$WG_NET"  || die "Neplatný rozsah sítě: $WG_NET"
    APP_PORT=8765
    STATION_PORT=8088

    install -d -m 755 "$ETC_DIR"
    cat > "$CONF" <<EOF
# Nastavení meteo z install.sh. Po změně spusť znovu:  sudo ./install.sh
DOMAIN=$DOMAIN
LAN_NET=$LAN_NET
WG_NET=$WG_NET
APP_PORT=$APP_PORT
STATION_PORT=$STATION_PORT
EOF
    chmod 644 "$CONF"
else
    # shellcheck source=/dev/null
    . "$CONF"
fi
ok "doména $DOMAIN · LAN $LAN_NET · VPN $WG_NET · porty $APP_PORT / $STATION_PORT"

IFACE=$(ip -4 route show default | awk '{print $5; exit}')
GATEWAY=$(ip -4 route show default | awk '{print $3; exit}')
PI_IP=$(ip -4 -o addr show dev "$IFACE" | awk '{print $4; exit}' | cut -d/ -f1)
WG_IP=$(ip -4 -o addr show wg0 2>/dev/null | awk '{print $4; exit}' | cut -d/ -f1 || true)  # bez VPN prázdné

if [[ -z ${LOCATION:-} ]]; then
    echo
    echo "    Poloha domova pro předpověď počasí a radar."
    echo "    Na mapy.cz klikni pravým tlačítkem na dům → zkopíruj souřadnice a vlož je sem."
    while true; do
        read -rp "    Souřadnice (Enter = zatím bez předpovědi): " coords_in
        if [[ -z $coords_in ]]; then LOCATION=none; break; fi
        if parsed=$(parse_coords "$coords_in"); then LOCATION="${parsed// /,}"; break; fi
        warn "tomu nerozumím – zkus tvar 50.0875, 14.4214"
    done
    echo "LOCATION=$LOCATION" >> "$CONF"
fi
[[ $LOCATION == none ]] && ok "předpověď vypnutá (zapneš smazáním řádku LOCATION v $CONF)" || ok "poloha $LOCATION"

if [[ -z ${LOCAL_DNS:-} ]]; then
    echo
    echo "    Lokální DNS: doma (a přes VPN) bude $DOMAIN mířit přímo na Pi,"
    echo "    takže nepotřebuješ interní IP ani NAT loopback v routeru."
    ask LOCAL_DNS_ANSWER "Zapnout lokální DNS na Pi? (a/n)" "a"
    case $LOCAL_DNS_ANSWER in a|A|ano|y|Y|yes) LOCAL_DNS=1 ;; *) LOCAL_DNS=0 ;; esac
    echo "LOCAL_DNS=$LOCAL_DNS" >> "$CONF"
fi

# ------------------------------------------------------------------ 2) porty
say "Kontrola portů"
if port_busy "$APP_PORT" && ! systemctl is-active -q meteo; then
    die "Port $APP_PORT už používá jiný program (sudo ss -tlnp | grep :$APP_PORT). Změň APP_PORT v $CONF a spusť znovu."
fi
if port_busy "$STATION_PORT" && ! grep -qs "^Listen $STATION_PORT" "$SITE" "$OLD_SITE"; then
    die "Port $STATION_PORT už používá jiný program (sudo ss -tlnp | grep :$STATION_PORT). Změň STATION_PORT v $CONF a spusť znovu."
fi
if [[ $LOCAL_DNS -eq 1 ]]; then
    holder=$(dns53_foreign | grep -v '"dnsmasq"' || true)
    if [[ -n $holder ]]; then
        LOCAL_DNS=0
        warn "port 53 (DNS) už používá jiný program – lokální DNS přeskakuji:"
        warn "$(echo "$holder" | grep -o 'users:(("[^"]*' | head -1 | sed 's/users:(("//')"
    fi
fi
ok "volné"

# ------------------------------------------------------------------ 3) balíčky
say "Systémové balíčky"
need=()
python3 -c 'import ensurepip' 2>/dev/null || need+=(python3-venv)
command -v certbot >/dev/null || need+=(certbot python3-certbot-apache)
if [[ $LOCAL_DNS -eq 1 ]]; then
    # Pozor: Raspberry Pi OS má kvůli NetworkManageru jen dnsmasq-base (program bez
    # služby a bez /etc/dnsmasq.d), proto se ptáme na balíček, ne na příkaz.
    dpkg-query -W -f='${Status}' dnsmasq 2>/dev/null | grep -q "install ok installed" || need+=(dnsmasq)
    command -v dig >/dev/null || need+=(dnsutils)
fi
if ((${#need[@]})); then
    apt-get update -qq
    apt-get install -y -qq "${need[@]}"
    ok "doinstalováno: ${need[*]}"
else
    ok "vše potřebné je k dispozici"
fi

# ------------------------------------------------------------------ 4) kód
say "Aplikace"
id meteo >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin meteo
install -d -m 755 "$APP_DIR"
install -d -o meteo -g meteo -m 750 "$DATA_DIR"

rm -rf "$APP_DIR/meteo"
cp -r "$SRC/meteo" "$APP_DIR/"
cp "$SRC/wsgi.py" "$SRC/manage.py" "$SRC/requirements.txt" "$SRC/deploy/gunicorn.conf.py" "$APP_DIR/"
find "$APP_DIR/meteo" -name __pycache__ -prune -exec rm -rf {} +
[[ -x $APP_DIR/venv/bin/python ]] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --disable-pip-version-check -r "$APP_DIR/requirements.txt"
install -m 755 "$SRC/deploy/meteo-manage" /usr/local/bin/meteo-manage
ok "kód v $APP_DIR ($(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo bez gitu))"

if [[ ! -f $ENV_FILE ]]; then
    secret=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
    sed -e "s#^METEO_SECRET_KEY=.*#METEO_SECRET_KEY=$secret#" \
        -e "s#^METEO_BIND=.*#METEO_BIND=127.0.0.1:$APP_PORT#" \
        -e "s#^METEO_LEGACY_NETS=.*#METEO_LEGACY_NETS=$LAN_NET#" \
        "$SRC/deploy/meteo.env.example" > "$ENV_FILE"
    ok "vytvořen $ENV_FILE s novým tajným klíčem"
fi
if [[ $LOCATION != none ]]; then
    set_env METEO_LAT "${LOCATION%,*}"
    set_env METEO_LON "${LOCATION#*,}"
else
    sed -i '/^METEO_LAT=/d; /^METEO_LON=/d' "$ENV_FILE"
fi
# Klíč pro Web Push (upozornění do telefonu) – vytvoří se jednou, služba ho jen čte
VAPID_FILE="$ETC_DIR/vapid.pem"
if [[ ! -f $VAPID_FILE ]]; then
    (cd "$APP_DIR" && "$APP_DIR/venv/bin/python" manage.py vapid-init "$VAPID_FILE" >/dev/null)
    ok "vytvořen klíč pro upozornění ($VAPID_FILE)"
fi
chown root:meteo "$VAPID_FILE"
chmod 640 "$VAPID_FILE"
set_env METEO_VAPID_KEY "$VAPID_FILE"
set_env METEO_VAPID_SUB "https://$DOMAIN"

chown meteo:meteo "$ENV_FILE"
chmod 600 "$ENV_FILE"

# ------------------------------------------------------------------ 5) databáze, účet, stanice
say "Databáze"
manage init-db >/dev/null
ok "$DATA_DIR/meteo.db"

if [[ $(manage user-count) == 0 ]]; then
    say "Účet pro přihlášení do aplikace"
    ask APP_USER "Uživatelské jméno" "petr"
    tries=0
    until manage set-password "$APP_USER"; do
        tries=$((tries + 1))
        [[ $tries -lt 3 ]] || die "Heslo se nepodařilo nastavit. Spusť install.sh znovu."
        warn "zkus to znovu"
    done
fi

NEW_KEY=$(manage ensure-station "$STATION_ID" "Venkovní stanice")

# ------------------------------------------------------------------ 6) služba
say "Služba meteo"
cp "$SRC/deploy/meteo.service" "$SRC/deploy/meteo-cleanup.service" "$SRC/deploy/meteo-cleanup.timer" \
   "$SRC/deploy/meteo-alerts.service" "$SRC/deploy/meteo-alerts.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q meteo meteo-cleanup.timer meteo-alerts.timer
systemctl restart meteo
systemctl start meteo-cleanup.timer meteo-alerts.timer
up=0
for _ in $(seq 1 30); do
    if health "$APP_PORT"; then up=1; break; fi
    sleep 0.5
done
[[ $up -eq 1 ]] || die "Služba nenaběhla. Podívej se do:  sudo journalctl -u meteo -n 50"
ok "běží na 127.0.0.1:$APP_PORT"

# ------------------------------------------------------------------ 7) Apache
say "Apache"
for mod in ssl proxy proxy_http headers; do a2enmod -q "$mod" >/dev/null; done
# Aplikace: připravit vedle a pak vyměnit najednou, ať nikdy neběží napůl nahraná verze
WEB_VERSION=$(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || date +%s)
install -d -m 755 "$(dirname "$WEB_DIR")"
rm -rf "$WEB_DIR.new"
cp -r "$SRC/web" "$WEB_DIR.new"
sed -i "s/__VERSION__/$WEB_VERSION/" "$WEB_DIR.new/sw.js"
chmod -R a+rX "$WEB_DIR.new"
rm -rf "$WEB_DIR.old"
[[ -d $WEB_DIR ]] && mv "$WEB_DIR" "$WEB_DIR.old"
mv "$WEB_DIR.new" "$WEB_DIR"
rm -rf "$WEB_DIR.old"

if [[ ! -f /etc/letsencrypt/live/$DOMAIN/fullchain.pem ]]; then
    getent hosts "$DOMAIN" >/dev/null || die "Doména $DOMAIN zatím nemá DNS záznam. Přidej u registrátora záznam A na stejnou veřejnou IP jako Nextcloud, chvíli počkej a spusť install.sh znovu."
    echo "    Žádám o certifikát pro $DOMAIN (certbot se může na něco zeptat)…"
    certbot certonly --apache -d "$DOMAIN" || die "Certbot neuspěl. Zkontroluj, že $DOMAIN míří na tvou veřejnou IP a port 80 je přesměrovaný na Pi."
fi
ok "certifikát pro $DOMAIN"

backup=""
if [[ -f $SITE ]]; then backup=$(mktemp); cp "$SITE" "$backup"; fi
old_enabled=0
if [[ -f /etc/apache2/sites-enabled/meteo.conf ]]; then
    a2dissite -q meteo >/dev/null
    old_enabled=1
fi
sed -e "s#__DOMAIN__#$DOMAIN#g" \
    -e "s#__LAN_NET__#$LAN_NET#g" \
    -e "s#__WG_NET__#$WG_NET#g" \
    -e "s#__APP_PORT__#$APP_PORT#g" \
    -e "s#__STATION_PORT__#$STATION_PORT#g" \
    "$SRC/deploy/apache/meteo.conf.in" > "$SITE"
a2ensite -q zz-meteo >/dev/null

if ! apache2ctl configtest >/dev/null 2>&1; then
    apache2ctl configtest || true
    if [[ -n $backup ]]; then cp "$backup" "$SITE"; else a2dissite -q zz-meteo >/dev/null; rm -f "$SITE"; fi
    [[ $old_enabled -eq 1 ]] && a2ensite -q meteo >/dev/null
    die "Nová konfigurace Apache neprošla kontrolou. Vrátil jsem předchozí stav – Nextcloud i rádio běží beze změny."
fi
[[ -n $backup ]] && rm -f "$backup"
rm -f "$OLD_SITE"
systemctl reload apache2
ok "aplikace $WEB_VERSION na https://$DOMAIN, vstup pro stanice na portu $STATION_PORT"

# ------------------------------------------------------------------ 8) lokální DNS
DNS_CONF=/etc/dnsmasq.d/meteo.conf
HOSTS_FILE=$ETC_DIR/local-hosts
DNS_OK=0
if [[ $LOCAL_DNS -eq 1 ]]; then
    say "Lokální DNS (dnsmasq)"
    if [[ -z ${LOCAL_NAMES:-} ]]; then
        # Kromě meteo i nadřazená doména (Nextcloud) – ale jen jména, která
        # veřejně míří na stejnou IP jako meteo, ať nepřesměrujeme nic cizího.
        target=$(public_a "$DOMAIN")
        LOCAL_NAMES=$DOMAIN
        parent=${DOMAIN#*.}
        if [[ $parent == *.* && -n $target ]]; then
            for n in "$parent" "www.$parent"; do
                [[ $(public_a "$n") == "$target" ]] && LOCAL_NAMES="$LOCAL_NAMES $n"
            done
        fi
        echo "LOCAL_NAMES=\"$LOCAL_NAMES\"" >> "$CONF"
    fi

    {
        echo "# Spravuje meteo install.sh – ručně neupravovat, přepíše se."
        echo "$PI_IP $LOCAL_NAMES"
        [[ -n $WG_IP ]] && echo "$WG_IP $LOCAL_NAMES"
    } > "$HOSTS_FILE"
    chmod 644 "$HOSTS_FILE"

    install -d -m 755 "$(dirname "$DNS_CONF")"
    {
        echo "# Spravuje meteo install.sh – ručně neupravovat, přepíše se."
        echo "# Odpovídá jen v domácí síti a ve VPN; zbytek dotazů posílá dál."
        echo "interface=$IFACE"
        echo "interface=wg0"
        echo "bind-dynamic"
        echo "no-resolv"
        echo "server=$GATEWAY"
        echo "server=1.1.1.1"
        echo "domain-needed"
        echo "bogus-priv"
        echo "cache-size=1000"
        echo "# Doma vrátí LAN IP Pi, přes VPN jeho VPN adresu"
        echo "localise-queries"
        echo "addn-hosts=$HOSTS_FILE"
    } > "$DNS_CONF"

    systemctl enable -q dnsmasq
    systemctl restart dnsmasq || true
    sleep 1
    answer=$(dig +short +time=2 +tries=2 A "$DOMAIN" @"$PI_IP" 2>/dev/null | head -1)
    if [[ $answer == "$PI_IP" ]]; then
        DNS_OK=1
        ok "$LOCAL_NAMES → $PI_IP (doma)${WG_IP:+ / $WG_IP (VPN)}"
        if [[ -z $(dig +short +time=3 +tries=1 A debian.org @"$PI_IP" 2>/dev/null) ]]; then
            warn "lokální jména fungují, ale přeposílání ostatních dotazů ne – zkontroluj spojení na $GATEWAY"
        fi
    else
        rm -f "$DNS_CONF"
        systemctl stop dnsmasq 2>/dev/null || true
        systemctl disable -q dnsmasq 2>/dev/null || true
        warn "dnsmasq neodpověděl správně ($answer) – lokální DNS vypnuto, zbytek instalace je v pořádku."
        warn "Detail:  sudo journalctl -u dnsmasq -n 30"
    fi
fi

# ------------------------------------------------------------------ 9) firewall
if command -v ufw >/dev/null && ufw status | grep -q "^Status: active"; then
    say "Firewall (ufw)"
    ufw allow from "$LAN_NET" to any port "$STATION_PORT" proto tcp comment "meteo stanice" >/dev/null
    ok "port $STATION_PORT povolen pro $LAN_NET"
    if [[ $DNS_OK -eq 1 ]]; then
        for net in "$LAN_NET" "$WG_NET"; do
            ufw allow from "$net" to any port 53 comment "meteo dns" >/dev/null
        done
        ok "DNS (port 53) povoleno pro $LAN_NET a $WG_NET"
    fi
    if ufw status | grep -E "^$STATION_PORT(/tcp)? +ALLOW +Anywhere" >/dev/null; then
        warn "port $STATION_PORT je navíc otevřený odkudkoli. Apache sice cizí IP odmítne,"
        warn "ale můžeš to zúžit:  sudo ufw delete allow $STATION_PORT"
    fi
fi

# ------------------------------------------------------------------ hotovo
printf '\n\033[1;32m✓ Hotovo.\033[0m\n\n'
echo "  Aplikace (z domova nebo přes VPN):  https://$DOMAIN"
echo
echo "  Do ESP32 souboru config.py:"
echo "      SERVER  = \"http://$PI_IP:$STATION_PORT\""
if [[ -n $NEW_KEY ]]; then
    echo "      API_KEY = \"$NEW_KEY\""
    echo
    printf '  \033[33mAPI klíč se zobrazuje jen teď – ulož si ho.\033[0m\n'
else
    echo "      API_KEY = (beze změny; nový klíč:  sudo -u meteo meteo-manage rotate-key $STATION_ID)"
fi
echo
echo "  Displej s RSS (Pico WH):  TEMP_SERVER_URL = \"http://$PI_IP:$STATION_PORT\""
if [[ $DNS_OK -eq 1 ]]; then
    echo
    printf '  \033[1mLokální DNS – zbývá nastavit:\033[0m\n'
    echo "    • Router, DHCP: jako DNS server rozdávej $PI_IP (bez záložního)."
    echo "      Pak se na telefonu odpoj a připoj k Wi-Fi."
    [[ -n $WG_IP ]] && echo "    • WireGuard klienti: do sekce [Interface] přidej  DNS = $WG_IP"
    echo "    • Test z laptopu:  nslookup $DOMAIN $PI_IP"
fi
echo
echo "  Příští aktualizace:       git pull && sudo ./install.sh"
echo
