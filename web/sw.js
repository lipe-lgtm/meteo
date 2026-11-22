// Service worker: aplikace se načte i bez spojení (data pak z poslední známé odpovědi).
// API se nikdy necachuje. VERSION doplní install.sh (git commit) → nová verze = nová cache.
const VERSION = '__VERSION__';
const CACHE = `meteo-${VERSION}`;
const SHELL = [
  '/',
  '/index.html',
  '/app.css',
  '/app.js',
  '/manifest.webmanifest',
  '/fonts/inter-latin-wght-normal.woff2',
  '/fonts/inter-latin-ext-wght-normal.woff2',
  '/img/icon-192.png',
  '/img/icon.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL.map((u) => new Request(u, { cache: 'reload' }))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('meteo-') && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;            // data vždy ze sítě

  if (event.request.mode === 'navigate') {
    // stránka: nejdřív síť (nová verze), bez spojení z cache
    event.respondWith(fetch(event.request).catch(() => caches.match('/index.html')));
    return;
  }
  // statické soubory: z cache, jinak ze sítě
  event.respondWith(caches.match(event.request).then((hit) => hit || fetch(event.request)));
});
