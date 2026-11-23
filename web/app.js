// Meteo – PWA bez build kroku. Data jen přes /api/v1 (session cookie).
// Bezpečnost: veškerý text z API jde do DOM přes textContent, nikdy innerHTML.

const API = '/api/v1';
const OUTDOOR = 'outdoor_station';
const INDOOR = 'indoor_station';
const STALE_AFTER = 15 * 60;      // hodnota starší než 15 min se zobrazí šedě
const REFRESH_MS = 60 * 1000;
// Široké okno (počítač): navigace vlevo, obsah ve sloupcích, větší grafy
const WIDE = window.matchMedia('(min-width: 900px)');
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
  radar: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM12 12l6-6',
  play: 'M8 5.5v13l10.5-6.5z',
  bell: 'M6 9a6 6 0 1 1 12 0c0 6 2.5 7.5 2.5 7.5h-17S6 15 6 9zM10 20a2.2 2.2 0 0 0 4 0',
  pause: 'M7 5h3v14H7zM14 5h3v14h-3z',
  cloud: 'M7 18h10a4 4 0 0 0 .4-8A6 6 0 0 0 6.1 11 3.5 3.5 0 0 0 7 18z',
  partly: 'M8 3v1.5M3.6 4.6l1 1M2 9h1.5M12.4 4.6l-1 1M5.2 11.6A3.5 3.5 0 1 1 11.6 8M9 19h8a3.5 3.5 0 0 0 .3-7A5 5 0 0 0 8 12.6 3.2 3.2 0 0 0 9 19z',
  rain: 'M7 15h10a3.5 3.5 0 0 0 .4-7A5.5 5.5 0 0 0 6.6 9 3 3 0 0 0 7 15zM9 18l-1 2M13 18l-1 2M17 18l-1 2',
  snow: 'M7 14h10a3.5 3.5 0 0 0 .4-7A5.5 5.5 0 0 0 6.6 8 3 3 0 0 0 7 14zM8 18h.01M12 18h.01M16 18h.01M10 21h.01M14 21h.01',
  storm: 'M7 14h10a3.5 3.5 0 0 0 .4-7A5.5 5.5 0 0 0 6.6 8 3 3 0 0 0 7 14zM13 14l-2 4h3l-2 4',
  fog: 'M7 12h10a3.5 3.5 0 0 0 .4-7A5.5 5.5 0 0 0 6.6 6 3 3 0 0 0 7 12zM5 16h14M7 20h10',
};

// WMO kódy počasí (Open-Meteo) -> ikona, barva, popis
function weatherOf(code) {
  const c = Number(code);
  if (c === 0 || c === 1) return { icon: 'sun', color: '#e3b341', text: c === 0 ? 'Jasno' : 'Skoro jasno' };
  if (c === 2) return { icon: 'partly', color: '#c4ccd5', text: 'Polojasno' };
  if (c === 45 || c === 48) return { icon: 'fog', color: '#9aa5b1', text: 'Mlha' };
  if ((c >= 51 && c <= 67) || (c >= 80 && c <= 82)) return { icon: 'rain', color: '#7cc4f2', text: c >= 80 ? 'Přeháňky' : c <= 57 ? 'Mrholení' : 'Déšť' };
  if ((c >= 71 && c <= 77) || c === 85 || c === 86) return { icon: 'snow', color: '#e7ebf0', text: 'Sněžení' };
  if (c >= 95) return { icon: 'storm', color: '#e3b341', text: 'Bouřky' };
  return { icon: 'cloud', color: '#9aa5b1', text: 'Zataženo' };
}

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
  const abs = f.format(Math.abs(v));
  return (v < 0 && /[1-9]/.test(abs) ? '−' : '') + abs;   // −0,4 zaokrouhlené na 0 je „0“, ne „−0“
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
    tab('#/radar', 'radar', 'Radar', 'radar'),
    tab('#/upozorneni', 'bell', 'Upozornění', 'alerts'),
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
  const [latest, stations, temp, forecast] = await Promise.all([
    api('/latest'),
    api('/stations'),
    api(`/history?station=${OUTDOOR}&metric=temperature&hours=24&points=96`),
    loadForecast(),
  ]);
  const data = { latest, stations, temp, forecast };
  saveCache(data);
  return data;
}

async function loadForecast() {
  try {
    return await api('/forecast');
  } catch (ex) {
    if (ex instanceof AuthError) throw ex;
    return { error: ex.code || 'unavailable' };
  }
}

// Srážky v příštích 3 h z 15minutové předpovědi (hodnota = úhrn za předchozích 15 min)
function nowcast(fc) {
  if (!fc || fc.error || !fc.nowcast || !fc.nowcast.length) return null;
  const steps = fc.nowcast.filter((x) => x.precip !== null);
  if (!steps.length) return null;
  const temp = fc.hours && fc.hours.length ? fc.hours[0].temp : null;
  const snowy = temp !== null && temp <= 0.5;
  const wet = (x) => x.precip >= 0.1;
  const at = (t) => timeHM.format(new Date((t - 900) * 1000));
  if (wet(steps[0])) {
    const end = steps.findIndex((x) => !wet(x));
    return {
      wet: true,
      title: snowy ? 'Právě sněží' : 'Právě prší',
      sub: end === -1 ? 'Podle předpovědi vydrží další 3 hodiny' : `Mělo by přestat kolem ${at(steps[end].t)}`,
    };
  }
  const first = steps.find(wet);
  if (first) {
    return { wet: true, title: `${snowy ? 'Sněžení' : 'Déšť'} kolem ${at(first.t)}`, sub: 'Podle předpovědi na příští 3 hodiny' };
  }
  return { wet: false, title: 'Další 3 hodiny bez srážek', sub: 'Podle předpovědi Open-Meteo' };
}

function forecastCard(fc) {
  if (!fc || fc.error === 'location_not_set') {
    return h('div', { class: 'card' },
      h('h2', { class: 'section-title', text: 'Předpověď' }),
      h('p', { class: 'note', text: 'Chybí poloha domova. Doplníš ji při dalším spuštění install.sh (smaž řádek LOCATION v /etc/meteo/install.conf).' }));
  }
  if (fc.error) {
    return h('div', { class: 'card' },
      h('h2', { class: 'section-title', text: 'Předpověď' }),
      h('p', { class: 'note', text: 'Předpověď je teď nedostupná, zkusím to znovu za chvíli.' }));
  }
  const days = fc.days.slice(0, 5);
  return h('section', { class: 'card', 'aria-label': 'Předpověď na 5 dní' },
    h('div', { class: 'card-head' },
      h('h2', { class: 'section-title', text: 'Předpověď' }),
      h('a', { class: 'source', href: 'https://open-meteo.com/', target: '_blank', rel: 'noopener', text: fc.stale ? 'Open-Meteo · starší data' : 'Open-Meteo' })),
    h('div', { class: 'forecast' }, days.map((d, i) => {
      const w = weatherOf(d.code);
      const name = i === 0 ? 'Dnes' : capitalize(dayShort.format(new Date(d.t * 1000)).replace('.', ''));
      return h('div', { class: 'fday', 'aria-label': `${name}: ${w.text}, ${fmtSigned(d.tmax, 0)} až ${fmtSigned(d.tmin, 0)} stupňů` },
        h('span', { class: 'fname', text: name }),
        icon(w.icon, { size: 'lg', color: w.color }),
        h('span', { class: 'fmax', text: d.tmax === null ? '–' : `${fmtSigned(Math.round(d.tmax), 0)}°` }),
        h('span', { class: 'fmin', text: d.tmin === null ? '–' : `${fmtSigned(Math.round(d.tmin), 0)}°` }),
        h('span', { class: 'fprob', text: d.prob !== null && d.prob >= 30 ? `${nf0.format(d.prob)} %` : '' }));
    })));
}

function radarRow(fc) {
  const nc = nowcast(fc);
  return h('a', { class: 'card row', href: '#/radar' },
    h('span', { class: 'row-icon' }, icon(nc && nc.wet ? 'rain' : 'radar', { color: '#7cc4f2' })),
    h('span', { class: 'row-text' },
      h('span', { class: 'row-title', text: nc ? nc.title : 'Radar srážek' }),
      h('span', { class: 'sub', text: nc ? nc.sub : 'Poslední hodina srážek v okolí' })),
    icon('chevron', { size: 'lg', color: '#7d8896' }));
}

function todayRange(points) {
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  const since = midnight.getTime() / 1000;
  const today = points.filter((p) => p[0] >= since);
  if (!today.length) return null;
  return { min: Math.min(...today.map((p) => p[2])), max: Math.max(...today.map((p) => p[3])) };
}

// Co stanice hlásí o čidle (diagnostika z firmwaru + hodnoty, které server odmítl)
function sensorProblem(station, sensor, metric) {
  const d = station && station.diag;
  if (!d) return null;
  const s = d.station && d.station.sensors && d.station.sensors[sensor];
  if (s && s.ok === false) return s.err || 'chyba čtení';
  const rej = (d.rejected || []).find((r) => r.metric === metric);
  return rej ? `nesmyslná hodnota ${typeof rej.value === "number" ? nf1.format(rej.value) : rej.value} (server ji zahodil)` : null;
}

function heroCard(v, temp, now, problem) {
  const hint = problem ? h('a', { class: 'banner warn', href: '#/stanice' },
    h('span', { text: `Čidlo teploty: ${problem}` }), icon('chevron', { color: '#7d8896' })) : null;
  if (!v) {
    return h('div', { class: 'card hero' },
      h('div', { class: 'card-head' }, h('h2', { class: 'section-title', text: 'Venku' })),
      h('p', { class: 'note', text: 'Venkovní stanice zatím neposlala teplotu.' }),
      hint);
  }
  const stale = now - v.ts > STALE_AFTER;
  const range = todayRange(temp.points);
  // starší hodnota + čidlo hlásí chybu → upozornění pod kartou (odkaz nesmí být v odkazu)
  return [h('a', { class: 'card hero', href: '#/detail/temperature', 'aria-label': `Teplota venku ${fmtSigned(v.value, 1)} stupňů, podrobnosti` },
    h('div', { class: 'card-head' }, h('h2', { class: 'section-title', text: 'Venku' }), icon('chevron', { size: 'lg', color: '#7d8896' })),
    h('div', { class: 'hero-row' },
      h('span', { class: `hero-value ${stale ? 'stale' : ''}`, text: `${fmtSigned(v.value, 1)}°` }),
      temp.points.length > 1 ? h('div', { class: 'spark' }, lineChart(temp.points, { width: 150, height: 56, compact: true })) : null),
    h('div', { class: 'hero-meta' },
      h('span', { text: stale ? `Naměřeno ${ago(v.ts, now)}` : `Aktualizováno ${timeHM.format(new Date(v.ts * 1000))}` }),
      range ? h('span', null, 'dnes ', h('strong', { text: `${fmtSigned(range.max, 1)}°` }), ` / ${fmtSigned(range.min, 1)}°`) : null)),
  stale ? hint : null];
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
    h('div', { class: 'cols' },
      h('div', { class: 'col' },
        heroCard(out.temperature, data.temp, now,
          sensorProblem(data.stations.stations.find((x) => x.id === OUTDOOR), 'ds18b20', 'temperature')),
        airCard(out, now),
        h('div', { class: 'grid2' }, humidityTile(out.humidity, now), indoorTile(indoor, now))),
      h('div', { class: 'col' },
        forecastCard(data.forecast),
        radarRow(data.forecast),
        lightRow(out.light, now))),
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
    const svg = WIDE.matches ? lineChart(pts, { width: 720, height: 300 }) : lineChart(pts);
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
    h('div', { class: 'detail-grid' }, h('div', { class: 'col' },
    h('div', { class: 'detail-value' },
      h('div', { class: 'big' },
        h('span', { class: `num ${cur && now - cur.ts > STALE_AFTER ? 'stale' : ''}`, text: cur ? `${meta.fmt(cur.value)}${meta.suffix.trim() === '°' ? '°' : ''}` : '–' }),
        cur && meta.unit ? h('span', { class: 'unit', text: meta.unit }) : null),
      h('span', { class: 'sub', text: cur ? `Naměřeno ${ago(cur.ts, now)} (${timeHM.format(new Date(cur.ts * 1000))})` : 'Zatím bez měření' })),
    seg, chart), h('div', { class: 'col' }, stats,
    h('section', { class: 'card' },
      h('h2', { class: 'section-title', text: 'Měření' }),
      h('div', { class: 'kv' }, h('span', { text: 'Stanice' }), h('span', { text: stationInfo ? `${stationInfo.name} · ${stationInfo.online ? 'online' : 'offline'}` : station })),
      station === OUTDOOR ? h('div', { class: 'kv' }, h('span', { text: 'Čidlo' }), h('span', { text: meta.sensor })) : null,
      h('div', { class: 'kv' }, h('span', { text: 'Body v grafu' }), h('span', { text: `průměr za ${Math.round(hist.bucket_seconds / 60)} min` }))))));
}

// ------------------------------------------------------------------ stanice

const SENSORS = [
  { key: 'ds18b20', label: 'Teplota', part: 'DS18B20', metric: 'temperature', fmt: (s) => s.raw != null ? `${fmtSigned(s.raw, 1)} °C` : '' },
  { key: 'dht22', label: 'Vlhkost', part: 'DHT22', metric: 'humidity' },
  { key: 'bh1750', label: 'Světlo', part: 'BH1750', metric: 'light' },
  { key: 'pms5003', label: 'Kvalita vzduchu', part: 'PMS5003', metric: 'pm25' },
];
const RESET_CAUSES = { 1: 'zapnutí napájení', 2: 'reset tlačítkem', 3: 'watchdog', 4: 'probuzení', 5: 'softwarový restart' };

function rssiLabel(dbm) {
  if (dbm >= -60) return 'výborný';
  if (dbm >= -70) return 'dobrý';
  if (dbm >= -80) return 'slabý';
  return 'velmi slabý';
}

function diagSection(st, now) {
  const wrap = h('div', { class: 'diag' }, h('h3', { class: 'diag-title', text: 'Čidla' }));
  const d = st.diag;
  const info = d && d.station;
  const rejected = (d && d.rejected) || [];
  if (!info) {
    for (const r of rejected) {
      const sn = SENSORS.find((x) => x.metric === r.metric);
      wrap.append(h('div', { class: 'sensor' }, h('span', { class: 'dot bad' }),
        h('span', { class: 'sensor-name' }, h('span', { text: sn ? sn.label : r.metric }), h('span', { class: 'part', text: sn ? sn.part : '' })),
        h('span', { class: 'sensor-state bad', text: `nesmyslná hodnota ${typeof r.value === 'number' ? nf1.format(r.value) : r.value} (server ji zahodil)` })));
    }
    wrap.append(h('p', { class: 'note', text: st.last_seen
      ? 'Podrobný stav čidel ukáže nový firmware (firmware/main.py).'
      : 'Stanice se ještě neozvala.' }));
    return wrap;
  }
  for (const sn of SENSORS) {
    const s = (info.sensors || {})[sn.key];
    const rej = rejected.find((r) => r.metric === sn.metric);
    let state = 'idle';
    let text = 'zatím neměřeno';
    if (s && s.ok === true) { state = 'ok'; text = (sn.fmt && sn.fmt(s)) || 'v pořádku'; }
    if (rej) { state = 'bad'; text = `nesmyslná hodnota ${typeof rej.value === "number" ? nf1.format(rej.value) : rej.value} (server ji zahodil)`; }
    if (s && s.ok === false) { state = 'bad'; text = s.err || 'chyba čtení'; }
    if (sn.key === 'pms5003' && s && s.bytes != null && state !== 'ok') text += ` · UART ${s.bytes} B`;
    wrap.append(h('div', { class: 'sensor' },
      h('span', { class: `dot ${state}` }),
      h('span', { class: 'sensor-name' }, h('span', { text: sn.label }), h('span', { class: 'part', text: sn.part })),
      h('span', { class: `sensor-state ${state}`, text }),
    ));
  }
  const rows = [];
  if (typeof info.rssi === 'number') rows.push(['Wi-Fi signál', `${fmtSigned(info.rssi, 0)} dBm · ${rssiLabel(info.rssi)}`]);
  if (info.reset != null) rows.push(['Poslední start', RESET_CAUSES[info.reset] || `kód ${info.reset}`]);
  if (typeof info.mem === 'number') rows.push(['Volná paměť', `${nf0.format(info.mem / 1024)} kB`]);
  if (info.fw) rows.push(['Firmware', String(info.fw)]);
  if (st.diag_at) rows.push(['Stav k', `${timeHM.format(new Date(st.diag_at * 1000))} (${ago(st.diag_at, now)})`]);
  if (rows.length) wrap.append(h('div', { class: 'diag-sep' }));
  for (const [k, v] of rows) wrap.append(h('div', { class: 'kv' }, h('span', { text: k }), h('span', { text: v })));
  return wrap;
}

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
      diagSection(st, now),
      relay.desired !== null && relay.desired !== undefined ? h('div', { class: 'switch-row' },
        h('div', { class: 'row-text' },
          h('span', { class: 'row-title', text: 'Zavlažování' }),
          h('span', { class: 'sub', text: pending ? 'Čeká, až si to stanice převezme…' : desired ? 'Zapnuto' : 'Vypnuto' })),
        btn) : null);
  });

  render(
    h('header', { class: 'header' }, h('div', { class: 'header-titles' }, h('span', { class: 'eyebrow', text: 'Zařízení' }), h('h1', { text: 'Stanice' }))),
    cards.length ? h('div', { class: 'cards' }, cards) : h('p', { class: 'note', text: 'Zatím není zaregistrovaná žádná stanice.' }),
    h('div', { class: 'btn-row account' },
      h('button', {
        class: 'btn ghost', type: 'button', text: `Odhlásit (${me.user})`,
        onclick: async () => { await api('/auth/logout', { body: {} }).catch(() => {}); clearCache(); location.hash = '#/login'; },
      }),
      h('button', {
        class: 'btn ghost', type: 'button', text: 'Odhlásit všechna zařízení',
        onclick: async () => {
          if (!confirm(`Odhlásit účet ${me.user} na všech zařízeních včetně tohoto? Přihlásit se bude potřeba znovu. Upozornění chodit nepřestanou.`)) return;
          await api('/auth/logout-all', { body: {} }).catch(() => {});
          clearCache(); location.hash = '#/login';
        },
      })),
    h('p', { class: 'footer', text: 'Meteo' }),
    tabbar('stations'));
}

// ------------------------------------------------------------------ upozornění

const RULES = [
  { key: 'offline', title: 'Výpadek stanice', fields: [['Bez dat déle než', 'minutes', 'min', 5]],
    note: 'Dám vědět i když se stanice zase ozve.' },
  { key: 'frost', title: 'Mráz', fields: [['Naměřeno', 'below', '°C nebo méně', 0.5]],
    note: 'Přijde i v nočním klidu.' },
  { key: 'frost_forecast', title: 'Mráz v noci podle předpovědi', fields: [['Večer, když má klesnout na', 'below', '°C', 0.5]],
    note: 'Jednou denně po 17. hodině.' },
  { key: 'rain', title: 'Déšť do hodiny', fields: [['Předpověď na hodinu aspoň', 'mm', 'mm', 0.1]],
    note: 'Nejvýš jednou za 3 hodiny.' },
  { key: 'air', title: 'Zhoršený vzduch', fields: [['Průměr PM2.5 za půl hodiny od', 'pm25', 'µg/m³', 5]] },
  { key: 'sensor', title: 'Porucha čidla', fields: [], note: 'Když čidlo hlásí chybu déle než 10 minut.' },
  { key: 'quiet', title: 'Noční klid', fields: [['Od', 'from', 'h', 1], ['Do', 'to', 'h', 1]],
    note: 'V klidu přijde jen mráz. Ostatní ráno, pokud pořád platí.' },
];

const pushSupported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

function deviceLabel() {
  const ua = navigator.userAgent;
  const os = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) || isIOS() ? 'iPad' : /Android/.test(ua) ? 'Android'
    : /Windows/.test(ua) ? 'Windows' : /Mac OS X/.test(ua) ? 'Mac' : /Linux/.test(ua) ? 'Linux' : 'Zařízení';
  const br = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : '';
  return br && os !== 'iPhone' && os !== 'iPad' ? `${os} · ${br}` : os;
}

function b64uToBytes(text) {
  const b64 = text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (text.length % 4)) % 4);
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

async function endpointId(endpoint) {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(endpoint));
  return Array.from(new Uint8Array(hash)).map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 16);
}

// serviceWorker.ready se nevyřeší, když se worker nezaregistroval – nečekat věčně
function swReady(ms = 4000) {
  return Promise.race([navigator.serviceWorker.ready, new Promise((resolve) => setTimeout(() => resolve(null), ms))]);
}

async function currentSubscription() {
  if (!pushSupported()) return null;
  const reg = await swReady();
  return reg ? reg.pushManager.getSubscription() : null;
}

async function enablePush(status) {
  // iOS: žádost o povolení musí přijít přímo z klepnutí, proto hned jako první
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') {
    status.textContent = 'Upozornění jsi nepovolil. Změnit to jde v nastavení telefonu nebo prohlížeče.';
    return false;
  }
  const { publicKey } = await api('/push/key');
  const reg = await swReady();
  if (!reg) throw new Error('service worker není aktivní');
  const opts = { userVisibleOnly: true, applicationServerKey: b64uToBytes(publicKey) };
  let sub;
  try {
    sub = await reg.pushManager.subscribe(opts);
  } catch (_) {
    // starý odběr s jiným klíčem serveru – zrušit a znovu
    const old = await reg.pushManager.getSubscription();
    if (old) await old.unsubscribe();
    sub = await reg.pushManager.subscribe(opts);
  }
  await api('/push/subscribe', { body: { ...sub.toJSON(), label: deviceLabel() } });
  return true;
}

async function deviceSection(data) {
  const box = h('section', { class: 'card' }, h('h2', { class: 'section-title', text: 'Toto zařízení' }));
  const status = h('p', { class: 'note', role: 'status' });
  const say = (text, state) => box.append(h('div', { class: 'device-state' },
    h('span', { class: `dot ${state}` }), h('span', { class: 'row-title', text })));

  if (!data.configured) {
    say('Server ještě nemá klíč pro upozornění', 'idle');
    box.append(h('p', { class: 'note', text: 'Na Pi spusť znovu  sudo ./install.sh  – klíč se vytvoří sám.' }));
    return box;
  }
  if (!pushSupported()) {
    say('Upozornění tu nejdou zapnout', 'idle');
    box.append(h('p', { class: 'note', text: isIOS() && !isStandalone()
      ? 'Na iPhonu fungují jen v aplikaci přidané na plochu: v Safari Sdílet → Přidat na plochu, pak otevři Meteo z plochy (iOS 16.4 a novější).'
      : 'Tento prohlížeč upozornění nepodporuje.' }));
    return box;
  }
  if (Notification.permission === 'denied') {
    say('Upozornění jsou zakázaná', 'bad');
    box.append(h('p', { class: 'note', text: 'Povol je v nastavení telefonu (Nastavení → Oznámení → Meteo) nebo v nastavení stránky v prohlížeči.' }));
    return box;
  }

  let sub = await currentSubscription();
  let registered = false;
  if (sub) {
    const id = await endpointId(sub.endpoint);
    registered = data.devices.some((d) => d.id === id);
    if (!registered) {   // server o zařízení neví (např. po resetu) – přihlásit znovu
      try { await api('/push/subscribe', { body: { ...sub.toJSON(), label: deviceLabel() } }); registered = true; } catch (_) { /* níž tlačítko */ }
    }
  }

  if (registered) {
    say('Dostává upozornění', 'ok');
    const test = h('button', { class: 'btn', type: 'button', text: 'Poslat zkušební' });
    test.addEventListener('click', async () => {
      test.disabled = true;
      try {
        const r = await api('/push/test', { body: { endpoint: sub.endpoint } });
        status.textContent = r.sent ? 'Odesláno – mělo by dorazit během pár sekund.' : 'Nepodařilo se doručit. Zkus upozornění vypnout a zapnout.';
      } catch (_) { status.textContent = 'Server zprávu neodeslal.'; }
      test.disabled = false;
    });
    const off = h('button', { class: 'btn ghost', type: 'button', text: 'Vypnout' });
    off.addEventListener('click', async () => {
      off.disabled = true;
      await api('/push/unsubscribe', { body: { endpoint: sub.endpoint } }).catch(() => {});
      await sub.unsubscribe().catch(() => {});
      route();
    });
    box.append(h('div', { class: 'btn-row' }, test, off));
  } else {
    say('Upozornění jsou vypnutá', 'idle');
    const on = h('button', { class: 'btn primary block', type: 'button' }, icon('bell'), h('span', { text: 'Zapnout upozornění' }));
    on.addEventListener('click', async () => {
      on.disabled = true;
      try { if (await enablePush(status)) { route(); return; } }
      catch (_) { status.textContent = 'Zapnutí se nepovedlo. Zkus to znovu.'; }
      on.disabled = false;
    });
    box.append(on);
  }
  box.append(status);
  const others = data.devices.length;
  if (others) {
    box.append(h('p', { class: 'note', text: `Přihlášená zařízení: ${data.devices.map((d) => `${d.label || 'zařízení'} (od ${dayMonth.format(new Date(d.since * 1000))})`).join(', ')}` }));
  }
  return box;
}

function rulesSection(settings) {
  const state = JSON.parse(JSON.stringify(settings));
  const saved = h('span', { class: 'source', role: 'status' });
  let timer = null;
  const save = () => {
    clearTimeout(timer);
    saved.textContent = '';
    timer = setTimeout(async () => {
      try {
        const r = await api('/alerts/settings', { body: { settings: state } });
        Object.assign(state, r.settings);
        saved.textContent = 'Uloženo';
      } catch (ex) {
        if (ex instanceof AuthError) { location.hash = '#/login'; return; }
        saved.textContent = 'Neuloženo – zkus znovu';
      }
    }, 500);
  };

  const rows = RULES.map((rule) => {
    const cfg = state[rule.key];
    const params = h('div', { class: 'rule-params' }, rule.fields.map(([label, key, unit, step]) => {
      const input = h('input', {
        type: 'number', inputmode: 'decimal', step, value: cfg[key], 'aria-label': `${rule.title}: ${label}`,
        onchange: (e) => { const v = parseFloat(String(e.target.value).replace(',', '.')); if (!Number.isNaN(v)) { cfg[key] = v; save(); } },
      });
      return h('label', { class: 'param' }, h('span', { text: label }), input, h('span', { class: 'unit', text: unit }));
    }));
    const sw = h('input', {
      type: 'checkbox', class: 'switch', role: 'switch', checked: cfg.on, 'aria-label': rule.title,
      onchange: (e) => { cfg.on = e.target.checked; row.classList.toggle('off', !cfg.on); save(); },
    });
    const row = h('div', { class: `rule ${cfg.on ? '' : 'off'}` },
      h('div', { class: 'rule-head' },
        h('div', { class: 'row-text' },
          h('span', { class: 'row-title', text: rule.title }),
          rule.note ? h('span', { class: 'sub', text: rule.note }) : null),
        sw),
      rule.fields.length ? params : null);
    return row;
  });
  return h('section', { class: 'card rules' },
    h('div', { class: 'card-head' }, h('h2', { class: 'section-title', text: 'Kdy dát vědět' }), saved),
    rows);
}

function historySection(history) {
  const fmt = new Intl.DateTimeFormat('cs-CZ', { day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit' });
  return h('section', { class: 'card' },
    h('h2', { class: 'section-title', text: 'Poslední upozornění' }),
    history.length ? h('ul', { class: 'history' }, history.map((n) => h('li', null,
      h('div', { class: 'card-head' },
        h('span', { class: 'row-title', text: n.title }),
        h('span', { class: 'age', text: fmt.format(new Date(n.ts * 1000)) })),
      h('span', { class: 'sub', text: n.body }),
      n.sent === 0 ? h('span', { class: 'tag', text: 'čeká na konec nočního klidu' })
        : n.sent === 2 ? h('span', { class: 'tag', text: 'v nočním klidu, mezitím pominulo' }) : null)))
      : h('p', { class: 'note', text: 'Zatím nic. Až se něco stane, najdeš to tady.' }));
}

async function viewAlerts() {
  const data = await api('/alerts');
  document.title = 'Meteo – upozornění';
  $app.classList.remove('no-tabbar');
  const device = await deviceSection(data);
  render(
    h('header', { class: 'header' }, h('div', { class: 'header-titles' },
      h('span', { class: 'eyebrow', text: 'Do telefonu i počítače' }), h('h1', { text: 'Upozornění' }))),
    h('div', { class: 'cols' },
      h('div', { class: 'col' }, device, historySection(data.history)),
      h('div', { class: 'col' }, rulesSection(data.settings))),
    tabbar('alerts'));
}

// ------------------------------------------------------------------ radar

// Radar: primárně ČHMÚ přes náš server (ostřejší, české radary, výhled 60 min),
// při výpadku ČHMÚ záloha RainViewer (jen minulost, hrubší).
const RAINVIEWER_META = 'https://api.rainviewer.com/public/weather-maps.json';
const RAINVIEWER_FRAMES = 7;       // poslední hodina po 10 min (limit 100 dotazů/min)
const CZ_CENTER = [49.8, 15.5];
let radar = null;                  // { map, timer } aktivní mapy, ať jde uklidit

function stopRadar() {
  if (!radar) return;
  clearInterval(radar.timer);
  radar.map.remove();
  radar = null;
}

let leafletLoading = null;
function loadLeaflet() {
  if (window.L) return Promise.resolve(window.L);
  if (!leafletLoading) {
    leafletLoading = new Promise((resolve, reject) => {
      document.head.append(h('link', { rel: 'stylesheet', href: '/vendor/leaflet/leaflet.css' }));
      const sc = h('script', { src: '/vendor/leaflet/leaflet.js' });
      sc.addEventListener('load', () => resolve(window.L));
      sc.addEventListener('error', () => { leafletLoading = null; reject(new Error('leaflet')); });
      document.head.append(sc);
    });
  }
  return leafletLoading;
}

async function chmiFrames(L) {
  const d = await api('/radar');
  const bounds = L.latLngBounds(d.bounds);
  return {
    source: 'chmi',
    stale: d.stale,
    frames: d.frames.map((f) => ({
      t: f.t, kind: f.kind,
      layer: L.imageOverlay(f.url, bounds, {
        opacity: 0, zIndex: 5, interactive: false, className: 'radar-img',
        attribution: 'Radar © <a href="https://www.chmi.cz/" target="_blank" rel="noopener">ČHMÚ</a>',
      }),
    })),
  };
}

async function rainviewerFrames(L) {
  const res = await fetch(RAINVIEWER_META, { cache: 'no-store', referrerPolicy: 'no-referrer' });
  const meta = await res.json();
  const past = ((meta.radar && meta.radar.past) || []).slice(-RAINVIEWER_FRAMES);
  return {
    source: 'rainviewer',
    stale: false,
    frames: past.map((f) => ({
      t: f.time, kind: 'obs',
      layer: L.tileLayer(`${meta.host}${f.path}/512/{z}/{x}/{y}/2/1_1.png`, {
        tileSize: 512, zoomOffset: -1, maxNativeZoom: 8, maxZoom: 11, opacity: 0, zIndex: 5,
        attribution: 'Radar <a href="https://www.rainviewer.com/" target="_blank" rel="noopener">RainViewer</a>',
      }),
    })),
  };
}

function frameLabel(f, i, frames, lastObs) {
  if (i === lastObs) return 'nyní';
  if (i === 0) return timeHM.format(new Date(f.t * 1000));
  if (i === frames.length - 1 && f.kind === 'fct') return `+${Math.round((f.t - frames[lastObs].t) / 60)} min`;
  return '';
}

async function viewRadar() {
  document.title = 'Meteo – radar';
  $app.classList.remove('no-tabbar');
  const [cfg, fc, L] = await Promise.all([api('/config'), loadForecast(), loadLeaflet()]);
  const home = cfg.location ? [cfg.location.lat, cfg.location.lon] : null;

  const mapEl = h('div', { class: 'radar-map', role: 'img', 'aria-label': 'Mapa srážek v okolí domova' });
  const eyebrow = h('span', { class: 'eyebrow', text: 'Radar' });
  const status = h('p', { class: 'note radar-status', role: 'status' });
  const playBtn = h('button', { class: 'play', type: 'button', 'aria-label': 'Přehrát animaci' }, icon('play', { size: 'lg' }));
  const framesEl = h('div', { class: 'frames', role: 'group', 'aria-label': 'Čas snímku' });
  const nc = nowcast(fc);

  render(
    h('header', { class: 'header' },
      h('div', { class: 'header-titles' }, eyebrow, h('h1', { text: 'Radar srážek' }))),
    mapEl,
    h('div', { class: 'radar-controls' }, playBtn, framesEl),
    status,
    h('section', { class: 'card row' },
      h('span', { class: 'row-icon' }, icon(nc && nc.wet ? 'rain' : 'radar', { color: '#7cc4f2' })),
      h('span', { class: 'row-text' },
        h('span', { class: 'row-title', text: nc ? nc.title : 'Předpověď srážek nedostupná' }),
        h('span', { class: 'sub', text: nc ? nc.sub : (home ? 'Zkusím to znovu za chvíli.' : 'Chybí poloha domova (install.sh).') }))),
    tabbar('radar'));

  stopRadar();
  const map = L.map(mapEl, {
    center: home || CZ_CENTER, zoom: home ? 8 : 7, minZoom: 5, maxZoom: 11,
    zoomControl: false, attributionControl: true,
  });
  map.attributionControl.setPrefix(false);
  radar = { map, timer: null };

  // Podklad OSM, ztmavený filtrem v CSS. Pravidla OSM: atribuce + Referer, žádné offline ukládání.
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 11, className: 'basemap', referrerPolicy: 'strict-origin-when-cross-origin',
    attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
  }).addTo(map);

  if (home) {
    for (const km of [10, 25]) {
      L.circle(home, { radius: km * 1000, interactive: false, color: '#7d8896', weight: 1, opacity: 0.7, dashArray: '3 6', fill: false }).addTo(map);
    }
    L.circleMarker(home, { radius: 6, interactive: false, color: '#0e1217', weight: 2, fillColor: '#7cc4f2', fillOpacity: 1, pane: 'markerPane' }).addTo(map);
  }

  let set = null;
  try {
    set = await chmiFrames(L);
    if (!set.frames.length) set = null;
  } catch (ex) {
    if (ex instanceof AuthError) throw ex;
  }
  if (!set) {
    try { set = await rainviewerFrames(L); } catch (_) { set = null; }
  }
  if (!radar || radar.map !== map) return;   // mezitím odešel jinam
  if (!set || !set.frames.length) {
    status.textContent = 'Radar je teď nedostupný. Zkus to za chvíli.';
    return;
  }

  const { frames } = set;
  const lastObs = frames.map((f) => f.kind).lastIndexOf('obs');
  const hasFct = frames.some((f) => f.kind === 'fct');
  eyebrow.textContent = set.source === 'chmi'
    ? (hasFct ? 'ČHMÚ · hodina zpět a výhled' : 'ČHMÚ · posledních 60 minut')
    : 'RainViewer · posledních 60 minut (ČHMÚ nedostupný)';

  // ČHMÚ snímky jsou malé – načíst všechny hned, ať animace neblikne
  if (set.source === 'chmi') frames.forEach((f) => f.layer.addTo(map));

  let current = -1;
  const buttons = frames.map((f, i) => h('button', {
    type: 'button', class: f.kind === 'fct' ? 'fct' : '', 'aria-pressed': 'false',
    'aria-label': `${f.kind === 'fct' ? 'Výhled na' : 'Snímek'} ${timeHM.format(new Date(f.t * 1000))}`,
    onclick: () => { stop(); show(i); },
  }, h('span', { class: 'bar' }), h('span', { class: 'lbl', text: frameLabel(f, i, frames, lastObs) })));
  framesEl.replaceChildren(...buttons);

  function show(i) {
    const f = frames[i];
    if (!map.hasLayer(f.layer)) f.layer.addTo(map);
    f.layer.setOpacity(0.8);
    if (current >= 0 && current !== i) frames[current].layer.setOpacity(0);
    if (current >= 0) buttons[current].setAttribute('aria-pressed', 'false');
    buttons[i].setAttribute('aria-pressed', 'true');
    current = i;
    const at = timeHM.format(new Date(f.t * 1000));
    status.textContent = f.kind === 'fct'
      ? `Výhled na ${at} · extrapolace pohybu, ne vývoje srážek`
      : `Snímek z ${at}${set.stale ? ' · ČHMÚ teď neodpovídá, poslední dostupný stav' : ''}`;
  }
  function stop() {
    clearInterval(radar && radar.timer);
    if (radar) radar.timer = null;
    playBtn.replaceChildren(icon('play', { size: 'lg' }));
    playBtn.setAttribute('aria-label', 'Přehrát animaci');
  }
  playBtn.addEventListener('click', () => {
    if (radar.timer) { stop(); return; }
    playBtn.replaceChildren(icon('pause', { size: 'lg' }));
    playBtn.setAttribute('aria-label', 'Zastavit animaci');
    show(0);
    radar.timer = setInterval(() => show((current + 1) % frames.length), 600);
  });
  show(lastObs >= 0 ? lastObs : frames.length - 1);
}

// ------------------------------------------------------------------ router

let routing = 0;

async function route() {
  const my = ++routing;
  const parts = (location.hash || '#/').slice(2).split('/').filter(Boolean);
  if (parts[0] !== 'radar') stopRadar();
  try {
    if (parts[0] === 'radar') return await viewRadar();
    if (parts[0] === 'login') return viewLogin();
    if (parts[0] === 'detail') return await viewDetail(parts[1], parts[2] || OUTDOOR);
    if (parts[0] === 'stanice') return await viewStations();
    if (parts[0] === 'upozorneni') return await viewAlerts();
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
// přechod mezi telefonním a širokým rozvržením (otočení tabletu, změna okna) – grafy se překreslí
WIDE.addEventListener('change', () => { if (!(location.hash || '').startsWith('#/radar')) route(); });
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
