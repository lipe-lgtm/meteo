// Meteo – PWA bez build kroku. Data jen přes /api/v1 (session cookie).
// Bezpečnost: veškerý text z API jde do DOM přes textContent, nikdy innerHTML.

const API = '/api/v1';
const OUTDOOR = 'outdoor_station';
const INDOOR = 'indoor_station';
const STALE_AFTER = 15 * 60;      // hodnota starší než 15 min se zobrazí šedě
const REFRESH_MS = 60 * 1000;
const CACHE_KEY = 'meteo:last';

const $app = document.getElementById('app');
const nf1 = new Intl.NumberFormat('cs-CZ', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat('cs-CZ', { maximumFractionDigits: 0 });
const dateLong = new Intl.DateTimeFormat('cs-CZ', { weekday: 'long', day: 'numeric', month: 'long' });
const timeHM = new Intl.DateTimeFormat('cs-CZ', { hour: '2-digit', minute: '2-digit' });
const dayShort = new Intl.DateTimeFormat('cs-CZ', { weekday: 'short' });
const dayMonth = new Intl.DateTimeFormat('cs-CZ', { day: 'numeric', month: 'numeric' });

// ------------------------------------------------------------------ API

class AuthError extends Error {}

async function api(path, { body, method = body === undefined ? 'GET' : 'POST' } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    headers['X-Requested-With'] = 'meteo-app';
  }
  const res = await fetch(API + path, {
    method, headers, credentials: 'same-origin', cache: 'no-store',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401 && !path.startsWith('/auth/login')) throw new AuthError();
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || String(res.status));
    err.status = res.status;
    err.code = data.error;
    throw err;
  }
  return data;
}

function saveCache(data) {
  try { localStorage.setItem(CACHE_KEY, JSON.stringify({ at: Date.now(), ...data })); } catch (_) { /* soukromý režim */ }
}
function loadCache() {
  try { return JSON.parse(localStorage.getItem(CACHE_KEY) || 'null'); } catch (_) { return null; }
}
function clearCache() {
  try { localStorage.removeItem(CACHE_KEY); } catch (_) { /* nic */ }
}

// ------------------------------------------------------------------ DOM helpers

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
function s(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

function setAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.setAttribute('class', v);
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : String(v));
  }
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

// Ikony: tahové SVG, stejné jako v návrhu
const ICONS = {
  chevron: 'M9 6l6 6-6 6',
  back: 'M15 6l-6 6 6 6',
  drop: 'M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  home: 'M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z',
  wind: 'M3 8h11a3 3 0 1 0-3-3M3 12h15a3 3 0 1 1-3 3M3 16h7',
  grid: 'M5 3h3a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM16 3h3a2 2 0 0 1 2 2v1a2 2 0 0 1-2 2h-3a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM16 12h3a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-3a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2zM5 16h3a2 2 0 0 1 2 2v1a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-1a2 2 0 0 1 2-2z',
  station: 'M12 20v-8M8.5 8.5a5 5 0 0 1 7 0M5.6 5.6a9 9 0 0 1 12.8 0M12 10a1 1 0 1 0 0 2 1 1 0 0 0 0-2z',
  thermo: 'M14 14.8V5a2 2 0 1 0-4 0v9.8a4 4 0 1 0 4 0z',
  water: 'M8 2v4M16 2v4M3 10h18M5 6h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z',
};

function icon(name, { size = '', color } = {}) {
  return s('svg', {
    class: `icon ${size}`, viewBox: '0 0 24 24', fill: 'none', stroke: color || 'currentColor',
    'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true',
  }, s('path', { d: ICONS[name] }));
}

// ------------------------------------------------------------------ formátování a význam hodnot

const METRICS = {
  // unit = za číslem velkým písmem, suffix = přímo u čísla (°, %), aby se vešlo do malé dlaždice
  temperature: { label: 'Teplota venku', unit: '', suffix: '°', fmt: (v) => fmtSigned(v, 1), sensor: 'DS18B20 v radiačním krytu' },
  humidity:    { label: 'Vlhkost venku', unit: '%', suffix: ' %', fmt: (v) => nf0.format(v), sensor: 'DHT22' },
  pm25:        { label: 'Jemné částice PM2.5', unit: 'µg/m³', suffix: '', fmt: (v) => nf0.format(v), sensor: 'PMS5003, měří každých 10 minut' },
  pm10:        { label: 'Částice PM10', unit: 'µg/m³', suffix: '', fmt: (v) => nf0.format(v), sensor: 'PMS5003, měří každých 10 minut' },
  light:       { label: 'Osvětlení', unit: 'lx', suffix: '', fmt: (v) => nf0.format(v), sensor: 'BH1750' },
};

function fmtSigned(v, digits) {
  const f = digits ? nf1 : nf0;
  return (v < 0 ? '−' : '') + f.format(Math.abs(v));
}

// Pásma PM2.5 (µg/m³) – stejná jako AQI ve firmwaru
const AIR_BANDS = [
  { max: 12, key: 'good', label: 'Dobrá' },
  { max: 35, key: 'moderate', label: 'Střední' },
  { max: 55, key: 'poor', label: 'Zhoršená' },
  { max: 150, key: 'bad', label: 'Špatná' },
];

function airBand(pm25) {
  const i = AIR_BANDS.findIndex((b) => pm25 <= b.max);
  const idx = i === -1 ? AIR_BANDS.length - 1 : i;
  const lo = idx === 0 ? 0 : AIR_BANDS[idx - 1].max;
  const hi = AIR_BANDS[idx].max;
  const within = Math.min(1, Math.max(0, (pm25 - lo) / (hi - lo)));
  return { ...AIR_BANDS[idx], position: ((idx + within) / AIR_BANDS.length) * 100 };
}

function lightLabel(lux) {
  if (lux < 1) return 'tma';
  if (lux < 10) return 'soumrak';
  if (lux < 100) return 'šero';
  if (lux < 1000) return 'zataženo';
  if (lux < 10000) return 'jasno';
  if (lux < 50000) return 'slunečno';
  return 'přímé slunce';
}

function ago(ts, now) {
  const d = Math.max(0, now - ts);
  if (d < 60) return 'právě teď';
  if (d < 3600) return `před ${Math.floor(d / 60)} min`;
  if (d < 86400) return `před ${Math.floor(d / 3600)} h`;
  const days = Math.floor(d / 86400);
  return days === 1 ? 'před 1 dnem' : `před ${days} dny`;
}

function capitalize(t) { return t.charAt(0).toUpperCase() + t.slice(1); }

// ------------------------------------------------------------------ graf

let chartSeq = 0;

function lineChart(points, { width = 350, height = 200, compact = false } = {}) {
  const vals = points.map((p) => p[1]);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const lo = compact ? min - span * 0.15 : Math.floor(min - span * 0.1);
  const hi = compact ? max + span * 0.15 : Math.ceil(max + span * 0.1);
  const top = compact ? 4 : 20;
  const bottom = height - (compact ? 4 : 6);
  const right = compact ? width - 4 : width - 36;
  const t0 = points[0][0];
  const t1 = points[points.length - 1][0];
  const x = (t) => (t1 === t0 ? right : ((t - t0) / (t1 - t0)) * right);
  const y = (v) => bottom - ((v - lo) / (hi - lo || 1)) * (bottom - top);
  const coords = points.map((p) => [x(p[0]).toFixed(1), y(p[1]).toFixed(1)]);
  const line = coords.map((c) => c.join(',')).join(' ');
  const last = coords[coords.length - 1];
  const id = `g${++chartSeq}`;

  const svg = s('svg', { viewBox: `0 0 ${width} ${height}`, 'aria-hidden': compact ? 'true' : null, role: compact ? null : 'img' });
  if (!compact) {
    svg.append(s('defs', null,
      s('linearGradient', { id, x1: 0, y1: 0, x2: 0, y2: 1 },
        s('stop', { offset: 0, 'stop-color': '#7cc4f2', 'stop-opacity': 0.22 }),
        s('stop', { offset: 1, 'stop-color': '#7cc4f2', 'stop-opacity': 0 }))));
    for (const frac of [0, 0.5, 1]) {
      const gy = top + (bottom - top) * frac;
      const gv = hi - (hi - lo) * frac;
      svg.append(s('line', { x1: 0, y1: gy, x2: width, y2: gy, stroke: '#1f2630' }));
      svg.append(s('text', { x: width, y: gy - 6, 'text-anchor': 'end', fill: '#7d8896', 'font-size': 11, text: nf0.format(gv) }));
    }
    svg.append(s('path', { d: `M${coords.map((c) => c.join(',')).join(' L')} L${last[0]},${bottom} L${coords[0][0]},${bottom} Z`, fill: `url(#${id})` }));
  }
  svg.append(s('polyline', { points: line, fill: 'none', stroke: '#7cc4f2', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  svg.append(compact
    ? s('circle', { cx: last[0], cy: last[1], r: 3, fill: '#7cc4f2' })
    : s('circle', { cx: last[0], cy: last[1], r: 4, fill: '#0e1217', stroke: '#7cc4f2', 'stroke-width': 2 }));
  return svg;
}

// ------------------------------------------------------------------ společné části

function tabbar(active) {
  const tab = (href, name, label, key) => h('a', { href, 'aria-current': active === key ? 'page' : null }, icon(name, { size: 'lg' }), h('span', { text: label }));
  return h('nav', { class: 'tabbar', 'aria-label': 'Hlavní navigace' },
    tab('#/', 'grid', 'Přehled', 'home'),
    tab('#/stanice', 'station', 'Stanice', 'stations'));
}

function statusPill(stations, now) {
  const st = stations.find((x) => x.id === OUTDOOR);
  if (!st || !st.last_seen) return h('span', { class: 'pill' }, h('span', { class: 'dot' }), h('span', { text: 'Bez dat' }));
  return h('a', { class: 'pill', href: '#/stanice' },
    h('span', { class: `dot ${st.online ? 'ok' : 'off'}` }),
    h('span', { text: `${st.online ? 'Online' : 'Offline'} · ${ago(st.last_seen, now)}` }));
}

function offlineBanner(cachedAt) {
  return h('div', { class: 'banner', role: 'status' },
    h('span', { text: `Bez spojení se serverem – zobrazuji data z ${timeHM.format(new Date(cachedAt))}.` }));
}

function render(...nodes) {
  $app.replaceChildren(...nodes.flat(Infinity).filter(Boolean));
}

// ------------------------------------------------------------------ přihlášení

function viewLogin() {
  document.title = 'Meteo – přihlášení';
  $app.classList.add('no-tabbar');
  const err = h('p', { class: 'form-error', role: 'alert' });
  const user = h('input', { id: 'u', name: 'username', autocomplete: 'username', autocapitalize: 'none', required: true });
  const pass = h('input', { id: 'p', name: 'password', type: 'password', autocomplete: 'current-password', required: true });
  const submit = h('button', { class: 'btn primary block', type: 'submit', text: 'Přihlásit' });

  const form = h('form', {
    class: 'form',
    onsubmit: async (e) => {
      e.preventDefault();
      submit.disabled = true;
      err.textContent = '';
      try {
        await api('/auth/login', { body: { username: user.value.trim(), password: pass.value } });
        location.hash = '#/';
      } catch (ex) {
        err.textContent = ex.code === 'too_many_attempts'
          ? 'Příliš mnoho pokusů. Zkus to znovu za 15 minut.'
          : ex.code === 'invalid_credentials' ? 'Nesprávné jméno nebo heslo.'
          : 'Server je nedostupný. Jsi doma, nebo připojený přes VPN?';
        submit.disabled = false;
      }
    },
  },
  h('div', { class: 'field' }, h('label', { for: 'u', text: 'Uživatel' }), user),
  h('div', { class: 'field' }, h('label', { for: 'p', text: 'Heslo' }), pass),
  err, submit);

  render(h('section', { class: 'login', 'aria-labelledby': 'lt' },
    h('div', { class: 'login-brand' },
      h('img', { src: '/img/icon-192.png', alt: '' }),
      h('h1', { id: 'lt', text: 'Meteo' })),
    form));
  user.focus();
}

// ------------------------------------------------------------------ přehled

async function loadOverview() {
  const [latest, stations, temp] = await Promise.all([
    api('/latest'),
    api('/stations'),
    api(`/history?station=${OUTDOOR}&metric=temperature&hours=24&points=96`),
  ]);
  const data = { latest, stations, temp };
  saveCache(data);
  return data;
}

function todayRange(points) {
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  const since = midnight.getTime() / 1000;
  const today = points.filter((p) => p[0] >= since);
  if (!today.length) return null;
  return { min: Math.min(...today.map((p) => p[2])), max: Math.max(...today.map((p) => p[3])) };
}

function heroCard(v, temp, now) {
  if (!v) {
    return h('div', { class: 'card hero' },
      h('div', { class: 'card-head' }, h('h2', { class: 'section-title', text: 'Venku' })),
      h('p', { class: 'note', text: 'Venkovní stanice zatím neposlala teplotu.' }));
  }
  const stale = now - v.ts > STALE_AFTER;
  const range = todayRange(temp.points);
  return h('a', { class: 'card hero', href: '#/detail/temperature', 'aria-label': `Teplota venku ${fmtSigned(v.value, 1)} stupňů, podrobnosti` },
    h('div', { class: 'card-head' }, h('h2', { class: 'section-title', text: 'Venku' }), icon('chevron', { size: 'lg', color: '#7d8896' })),
    h('div', { class: 'hero-row' },
      h('span', { class: `hero-value ${stale ? 'stale' : ''}`, text: `${fmtSigned(v.value, 1)}°` }),
      temp.points.length > 1 ? h('div', { class: 'spark' }, lineChart(temp.points, { width: 150, height: 56, compact: true })) : null),
    h('div', { class: 'hero-meta' },
      h('span', { text: stale ? `Naměřeno ${ago(v.ts, now)}` : `Aktualizováno ${timeHM.format(new Date(v.ts * 1000))}` }),
      range ? h('span', null, 'dnes ', h('strong', { text: `${fmtSigned(range.max, 1)}°` }), ` / ${fmtSigned(range.min, 1)}°`) : null));
}

function airCard(m, now) {
  const pm25 = m.pm25;
  if (!pm25) {
    return h('div', { class: 'card air' },
      h('div', { class: 'card-head' }, h('h2', { class: 'section-title', text: 'Kvalita vzduchu' }), h('span', { class: 'badge none', text: 'Bez dat' })),
      h('p', { class: 'note', text: 'Čidlo prachových částic zatím neposlalo měření.' }));
  }
  const band = airBand(pm25.value);
  const stale = now - pm25.ts > 2 * STALE_AFTER;   // PM se měří jen každých 10 min
  const marker = h('span', { class: 'scale-marker' });
  marker.style.left = `${band.position}%`;
  return h('a', { class: 'card air', href: '#/detail/pm25', 'aria-label': `Kvalita vzduchu ${band.label.toLowerCase()}, PM2.5 ${nf0.format(pm25.value)} mikrogramů, podrobnosti` },
    h('div', { class: 'card-head' },
      h('h2', { class: 'section-title', text: 'Kvalita vzduchu' }),
      h('span', { class: `badge ${band.key}` }, h('span', { class: 'dot' }), band.label)),
    h('div', { class: 'air-row' },
      h('div', null,
        h('div', { class: 'big' }, h('span', { class: `num ${stale ? 'stale' : ''}`, text: nf0.format(pm25.value) }), h('span', { class: 'unit', text: 'µg/m³' })),
        h('span', { class: 'sub', text: stale ? `PM2.5 · ${ago(pm25.ts, now)}` : 'Jemné částice PM2.5' })),
      h('div', { class: 'minor' },
        m.pm10 ? h('div', null, h('span', { class: 'v', text: nf0.format(m.pm10.value) }), h('span', { class: 'k', text: 'PM10' })) : null,
        m.aqi ? h('div', null, h('span', { class: 'v', text: nf0.format(m.aqi.value) }), h('span', { class: 'k', text: 'AQI' })) : null)),
    h('div', { class: 'scale', 'aria-hidden': 'true' },
      h('div', { class: 'scale-bar' }, h('span'), h('span'), h('span'), h('span')),
      marker,
      h('div', { class: 'scale-labels' }, AIR_BANDS.map((b) => h('span', { text: b.label.toLowerCase() })))));
}

function humidityTile(v, now) {
  if (!v) return null;
  return h('a', { class: 'card tile', href: '#/detail/humidity' },
    h('div', { class: 'tile-label' }, icon('drop', { size: 'sm', color: '#7cc4f2' }), h('span', { text: 'Vlhkost' })),
    h('div', { class: 'tile-value' },
      h('span', { class: `num ${now - v.ts > STALE_AFTER ? 'stale' : ''}`, text: nf0.format(v.value) }),
      h('span', { class: 'unit', text: '%' })));
}

function indoorTile(indoor, now) {
  if (!indoor || !indoor.temperature) {
    return h('div', { class: 'card tile placeholder' },
      h('div', { class: 'tile-label' }, icon('home', { size: 'sm', color: '#7d8896' }), h('span', { text: 'Uvnitř' })),
      h('p', null, 'Vnitřní stanice', h('br'), 'zatím nepřipojena'));
  }
  const t = indoor.temperature;
  return h('a', { class: 'card tile', href: `#/detail/temperature/${INDOOR}` },
    h('div', { class: 'tile-label' }, icon('home', { size: 'sm', color: '#7cc4f2' }), h('span', { text: 'Uvnitř' })),
    h('div', { class: 'tile-value' },
      h('span', { class: `num ${now - t.ts > STALE_AFTER ? 'stale' : ''}`, text: `${fmtSigned(t.value, 1)}°` }),
      indoor.humidity ? h('span', { class: 'unit', text: `${nf0.format(indoor.humidity.value)} %` }) : null));
}

function lightRow(v, now) {
  if (!v) return null;
  return h('a', { class: 'card row', href: '#/detail/light' },
    h('span', { class: 'row-icon warm' }, icon('sun', { color: '#e3b341' })),
    h('span', { class: 'row-text' }, h('span', { class: 'row-title', text: 'Světlo' })),
    h('span', { class: now - v.ts > STALE_AFTER ? 'stale' : '', text: `${nf0.format(v.value)} lx` }),
    h('span', { class: 'sub', text: lightLabel(v.value) }));
}

function overviewView(data, { cachedAt } = {}) {
  const now = data.latest.now;
  const out = data.latest.stations[OUTDOOR] || {};
  const indoor = data.latest.stations[INDOOR];
  document.title = 'Meteo';
  $app.classList.remove('no-tabbar');
  render(
    h('header', { class: 'header' },
      h('div', { class: 'header-titles' },
        h('span', { class: 'eyebrow', text: capitalize(dateLong.format(new Date())) }),
        h('h1', { text: 'Doma' })),
      statusPill(data.stations.stations, now)),
    cachedAt ? offlineBanner(cachedAt) : null,
    heroCard(out.temperature, data.temp, now),
    airCard(out, now),
    h('div', { class: 'grid2' }, humidityTile(out.humidity, now), indoorTile(indoor, now)),
    lightRow(out.light, now),
    tabbar('home'));
}

// ------------------------------------------------------------------ detail

const RANGES = [
  { key: 'day', label: '24 h', hours: 24 },
  { key: 'week', label: '7 dní', hours: 168 },
  { key: 'month', label: '30 dní', hours: 720 },
];
let detailRange = 'day';

function ticksFor(points, rangeKey) {
  if (points.length < 2) return [];
  const t0 = points[0][0];
  const t1 = points[points.length - 1][0];
  const out = [];
  for (let i = 0; i < 5; i++) {
    const d = new Date((t0 + ((t1 - t0) * i) / 4) * 1000);
    out.push(rangeKey === 'day' ? timeHM.format(d) : rangeKey === 'week' ? dayShort.format(d) : dayMonth.format(d));
  }
  return out;
}

async function viewDetail(metric, station = OUTDOOR) {
  const meta = METRICS[metric];
  if (!meta) { location.hash = '#/'; return; }
  const range = RANGES.find((r) => r.key === detailRange);
  const [hist, latest, st] = await Promise.all([
    api(`/history?station=${encodeURIComponent(station)}&metric=${metric}&hours=${range.hours}&points=240`),
    api('/latest'),
    api('/stations'),
  ]);
  const stationInfo = st.stations.find((x) => x.id === station);
  const now = latest.now;
  const cur = (latest.stations[station] || {})[metric];
  const title = station === INDOOR ? meta.label.replace('venku', 'uvnitř') : meta.label;
  const pts = hist.points;

  document.title = `Meteo – ${title}`;
  $app.classList.add('no-tabbar');

  const seg = h('div', { class: 'segmented', role: 'group', 'aria-label': 'Období' },
    RANGES.map((r) => h('button', {
      type: 'button', 'aria-pressed': r.key === detailRange ? 'true' : 'false', text: r.label,
      onclick: () => { detailRange = r.key; route(); },
    })));

  let chart;
  if (pts.length > 1) {
    const svg = lineChart(pts);
    const vals = pts.map((p) => p[1]);
    svg.setAttribute('aria-label', `Průběh za ${range.label}: od ${meta.fmt(Math.min(...vals))}${meta.suffix} do ${meta.fmt(Math.max(...vals))}${meta.suffix} ${meta.unit}`);
    chart = h('figure', { class: 'chart' }, svg,
      h('div', { class: 'chart-ticks', 'aria-hidden': 'true' }, ticksFor(pts, detailRange).map((t) => h('span', { text: t }))));
  } else {
    chart = h('div', { class: 'chart-empty', text: 'Za toto období zatím nejsou data.' });
  }

  let stats = null;
  if (pts.length) {
    const mins = pts.map((p) => p[2]);
    const maxs = pts.map((p) => p[3]);
    const avg = pts.reduce((a, p) => a + p[1], 0) / pts.length;
    const stat = (k, v) => h('div', { class: 'card stat' }, h('span', { class: 'k', text: k }), h('span', { class: 'v', text: `${meta.fmt(v)}${meta.suffix}` }));
    stats = h('div', { class: 'grid3' }, stat('Minimum', Math.min(...mins)), stat('Průměr', avg), stat('Maximum', Math.max(...maxs)));
  }

  render(
    h('div', { class: 'topbar' },
      h('a', { class: 'back', href: '#/', 'aria-label': 'Zpět na přehled' }, icon('back', { size: 'lg' })),
      h('h1', { text: title })),
    h('div', { class: 'detail-value' },
      h('div', { class: 'big' },
        h('span', { class: `num ${cur && now - cur.ts > STALE_AFTER ? 'stale' : ''}`, text: cur ? `${meta.fmt(cur.value)}${meta.suffix.trim() === '°' ? '°' : ''}` : '–' }),
        cur && meta.unit ? h('span', { class: 'unit', text: meta.unit }) : null),
      h('span', { class: 'sub', text: cur ? `Naměřeno ${ago(cur.ts, now)} (${timeHM.format(new Date(cur.ts * 1000))})` : 'Zatím bez měření' })),
    seg, chart, stats,
    h('section', { class: 'card' },
      h('h2', { class: 'section-title', text: 'Měření' }),
      h('div', { class: 'kv' }, h('span', { text: 'Stanice' }), h('span', { text: stationInfo ? `${stationInfo.name} · ${stationInfo.online ? 'online' : 'offline'}` : station })),
      station === OUTDOOR ? h('div', { class: 'kv' }, h('span', { text: 'Čidlo' }), h('span', { text: meta.sensor })) : null,
      h('div', { class: 'kv' }, h('span', { text: 'Body v grafu' }), h('span', { text: `průměr za ${Math.round(hist.bucket_seconds / 60)} min` }))));
}

// ------------------------------------------------------------------ stanice

async function viewStations() {
  const { stations, now } = await api('/stations');
  const me = await api('/auth/me');
  document.title = 'Meteo – stanice';
  $app.classList.remove('no-tabbar');

  const cards = stations.map((st) => {
    const relay = st.relay || {};
    const desired = relay.desired === 1;
    const pending = relay.reported !== null && relay.reported !== undefined && relay.reported !== relay.desired;
    const btn = h('button', {
      class: `btn ${desired ? '' : 'primary'}`, type: 'button',
      text: desired ? 'Vypnout' : 'Zapnout',
      onclick: async () => {
        const next = desired ? 0 : 1;
        if (next === 1 && !confirm('Opravdu zapnout zavlažování?')) return;
        btn.disabled = true;
        try { await api('/relay', { body: { station: st.id, state: next } }); }
        catch (ex) {
          if (ex instanceof AuthError) { location.hash = '#/login'; return; }
          alert('Nepodařilo se přepnout relé.');
        }
        route();
      },
    });
    return h('section', { class: 'card' },
      h('div', { class: 'card-head' },
        h('h2', { class: 'row-title', text: st.name }),
        h('span', { class: 'pill' }, h('span', { class: `dot ${st.online ? 'ok' : 'off'}` }), h('span', { text: st.online ? 'Online' : 'Offline' }))),
      h('div', { class: 'kv' }, h('span', { text: 'Poslední data' }), h('span', { text: st.last_seen ? `${ago(st.last_seen, now)} (${timeHM.format(new Date(st.last_seen * 1000))})` : 'nikdy' })),
      st.uptime && st.uptime < 3e8 ? h('div', { class: 'kv' }, h('span', { text: 'Běží bez restartu' }), h('span', { text: ago(now - st.uptime, now).replace('před ', '') })) : null,
      relay.desired !== null && relay.desired !== undefined ? h('div', { class: 'switch-row' },
        h('div', { class: 'row-text' },
          h('span', { class: 'row-title', text: 'Zavlažování' }),
          h('span', { class: 'sub', text: pending ? 'Čeká, až si to stanice převezme…' : desired ? 'Zapnuto' : 'Vypnuto' })),
        btn) : null);
  });

  render(
    h('header', { class: 'header' }, h('div', { class: 'header-titles' }, h('span', { class: 'eyebrow', text: 'Zařízení' }), h('h1', { text: 'Stanice' }))),
    cards.length ? cards : h('p', { class: 'note', text: 'Zatím není zaregistrovaná žádná stanice.' }),
    h('button', {
      class: 'btn ghost block', type: 'button', text: `Odhlásit (${me.user})`,
      onclick: async () => { await api('/auth/logout', { body: {} }).catch(() => {}); clearCache(); location.hash = '#/login'; },
    }),
    h('p', { class: 'footer', text: 'Meteo' }),
    tabbar('stations'));
}

// ------------------------------------------------------------------ router

let routing = 0;

async function route() {
  const my = ++routing;
  const parts = (location.hash || '#/').slice(2).split('/').filter(Boolean);
  try {
    if (parts[0] === 'login') return viewLogin();
    if (parts[0] === 'detail') return await viewDetail(parts[1], parts[2] || OUTDOOR);
    if (parts[0] === 'stanice') return await viewStations();
    const data = await loadOverview();
    if (my === routing) overviewView(data);
  } catch (ex) {
    if (my !== routing) return;
    if (ex instanceof AuthError) { clearCache(); history.replaceState(null, '', '#/login'); return viewLogin(); }
    const cached = loadCache();
    if (!parts.length && cached && cached.latest) return overviewView(cached, { cachedAt: cached.at });
    render(h('div', { class: 'banner error', role: 'alert' },
      h('span', { text: 'Server je nedostupný. Jsi doma, nebo připojený přes VPN?' })),
      h('button', { class: 'btn block', type: 'button', text: 'Zkusit znovu', onclick: route }));
  }
}

window.addEventListener('hashchange', () => { window.scrollTo(0, 0); route(); });
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
function refresh() {
  const p = (location.hash || '#/').slice(2);
  if (p === '' || p === 'stanice') route();
}
setInterval(() => { if (!document.hidden) refresh(); }, REFRESH_MS);

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => { /* bez offline režimu */ });
}

route();
