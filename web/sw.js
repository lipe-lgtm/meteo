// Service worker: aplikace se načte i bez spojení (data pak z poslední známé odpovědi).
// API se nikdy necachuje. VERSION doplní install.sh (git commit) → nová verze = nová cache.
const VERSION = '__VERSION__';
const CACHE = `meteo-${VERSION}`;
const SHELL = [
  '/',
  '/index.html',
  '/app.css',
  '/app.js',
  '/moon.js',
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

// ---------------------------------------------------------------- upozornění (Web Push)
self.addEventListener('push', (event) => {
  let msg = {};
  try { msg = event.data ? event.data.json() : {}; } catch (_) { msg = { body: event.data ? event.data.text() : '' }; }
  event.waitUntil(self.registration.showNotification(msg.title || 'Meteo', {
    body: msg.body || '',
    tag: msg.tag || 'meteo',
    renotify: true,
    icon: '/img/icon-192.png',
    timestamp: msg.ts ? msg.ts * 1000 : Date.now(),
    data: { url: typeof msg.url === 'string' && msg.url.startsWith('/') ? msg.url : '/' },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = new URL(event.notification.data && event.notification.data.url || '/', self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    const win = list.find((c) => c.url.startsWith(self.location.origin));
    if (win) return win.focus().then((c) => (c && c.navigate ? c.navigate(url) : c)).catch(() => {});
    return self.clients.openWindow(url);
  }));
});

// Prohlížeč odběr obnovil (nové klíče) – nahlásit serveru, jinak by upozornění přestala chodit
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil((async () => {
    const res = await fetch('/api/v1/push/key', { credentials: 'same-origin' });
    if (!res.ok) return;
    const { publicKey } = await res.json();
    const raw = publicKey.replace(/-/g, '+').replace(/_/g, '/');
    const key = Uint8Array.from(atob(raw + '='.repeat((4 - (raw.length % 4)) % 4)), (c) => c.charCodeAt(0));
    const sub = event.newSubscription || await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    await fetch('/api/v1/push/subscribe', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'meteo-app' },
      body: JSON.stringify(sub.toJSON()),
    });
  })());
});
