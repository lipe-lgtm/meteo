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
SITE=/etc/apache2/sites-available/meteo.conf
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
manage() { runuser -u meteo -- /usr/local/bin/meteo-manage "$@"; }

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

# ------------------------------------------------------------------ 2) porty
say "Kontrola portů"
if port_busy "$APP_PORT" && ! systemctl is-active -q meteo; then
    die "Port $APP_PORT už používá jiný program (sudo ss -tlnp | grep :$APP_PORT). Změň APP_PORT v $CONF a spusť znovu."
fi
if port_busy "$STATION_PORT" && ! grep -qs "^Listen $STATION_PORT" "$SITE"; then
    die "Port $STATION_PORT už používá jiný program (sudo ss -tlnp | grep :$STATION_PORT). Změň STATION_PORT v $CONF a spusť znovu."
fi
ok "volné"

# ------------------------------------------------------------------ 3) balíčky
say "Systémové balíčky"
need=()
python3 -c 'import ensurepip' 2>/dev/null || need+=(python3-venv)
command -v certbot >/dev/null || need+=(certbot python3-certbot-apache)
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
cp "$SRC/wsgi.py" "$SRC/manage.py" "$SRC/requirements.txt" "$APP_DIR/"
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
cp "$SRC/deploy/meteo.service" "$SRC/deploy/meteo-cleanup.service" "$SRC/deploy/meteo-cleanup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q meteo meteo-cleanup.timer
systemctl restart meteo
systemctl start meteo-cleanup.timer
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
install -d -m 755 "$WEB_DIR"
[[ -f $WEB_DIR/index.html ]] || cp "$SRC/deploy/placeholder.html" "$WEB_DIR/index.html"

if [[ ! -f /etc/letsencrypt/live/$DOMAIN/fullchain.pem ]]; then
    getent hosts "$DOMAIN" >/dev/null || die "Doména $DOMAIN zatím nemá DNS záznam. Přidej u registrátora záznam A na stejnou veřejnou IP jako Nextcloud, chvíli počkej a spusť install.sh znovu."
    echo "    Žádám o certifikát pro $DOMAIN (certbot se může na něco zeptat)…"
    certbot certonly --apache -d "$DOMAIN" || die "Certbot neuspěl. Zkontroluj, že $DOMAIN míří na tvou veřejnou IP a port 80 je přesměrovaný na Pi."
fi
ok "certifikát pro $DOMAIN"

backup=""
if [[ -f $SITE ]]; then backup=$(mktemp); cp "$SITE" "$backup"; fi
sed -e "s#__DOMAIN__#$DOMAIN#g" \
    -e "s#__LAN_NET__#$LAN_NET#g" \
    -e "s#__WG_NET__#$WG_NET#g" \
    -e "s#__APP_PORT__#$APP_PORT#g" \
    -e "s#__STATION_PORT__#$STATION_PORT#g" \
    "$SRC/deploy/apache/meteo.conf.in" > "$SITE"
a2ensite -q meteo >/dev/null

if ! apache2ctl configtest >/dev/null 2>&1; then
    apache2ctl configtest || true
    if [[ -n $backup ]]; then cp "$backup" "$SITE"; else a2dissite -q meteo >/dev/null; rm -f "$SITE"; fi
    die "Nová konfigurace Apache neprošla kontrolou. Vrátil jsem předchozí stav – Nextcloud i rádio běží beze změny."
fi
[[ -n $backup ]] && rm -f "$backup"
systemctl reload apache2
ok "web https://$DOMAIN a vstup pro stanice na portu $STATION_PORT"

# ------------------------------------------------------------------ hotovo
iface=$(ip -4 route show default | awk '{print $5; exit}')
PI_IP=$(ip -4 -o addr show dev "$iface" | awk '{print $4; exit}' | cut -d/ -f1)

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
echo "  Příští aktualizace:       git pull && sudo ./install.sh"
echo
