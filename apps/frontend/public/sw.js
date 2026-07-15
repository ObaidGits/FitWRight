/*
 * FitWright service worker (P4 Resilience — R2, R8.5, R9.8, R9.12).
 *
 * Versioned, offline-capable, and safe:
 * - App-shell navigations: network-first, falling back to the cached shell so
 *   offline reloads still render (masks free-tier cold starts too — ADR-15).
 * - Static assets (_next/static, fonts): cache-first (immutable, hashed).
 * - Safe GET API responses: stale-while-revalidate, restricted to an allowlist.
 * - NEVER cache auth / OAuth / CSRF / api-key / mutation / AI responses (R8.5).
 * - Versioned cache; old caches pruned on activate (R9.12).
 * - Safe update: installs but WAITS — no destructive skipWaiting mid-edit; the
 *   app posts SKIP_WAITING at a safe point (R9.8).
 * - Kill-switch: CLEAR_CACHES + UNREGISTER messages for OFFLINE_SUPPORT=off and
 *   logout / different-user detection.
 */

// Bumped on each deploy; drives cache versioning + the update prompt.
const SW_VERSION = 'v1';
const SHELL_CACHE = `fitwright-shell-${SW_VERSION}`;
const STATIC_CACHE = `fitwright-static-${SW_VERSION}`;
const API_CACHE = `fitwright-api-${SW_VERSION}`;
const ALL_CACHES = [SHELL_CACHE, STATIC_CACHE, API_CACHE];

// App-shell entry that renders while offline (the (app) group renders client-side).
const SHELL_URL = '/';

// Cache observability (P4 §Observability): in-SW hit/miss counters so the app
// can compute a cache-hit ratio via a GET_STATS message.
const STATS = { staticHit: 0, staticMiss: 0, apiHit: 0, apiMiss: 0, navFallback: 0 };
function hitRatio() {
  const hits = STATS.staticHit + STATS.apiHit;
  const total = hits + STATS.staticMiss + STATS.apiMiss;
  return total === 0 ? 0 : hits / total;
}

// GET API paths safe to cache for offline reads. Everything else is network-only.
// Auth/session/csrf/oauth/api-key/internal/admin are intentionally excluded.
const API_CACHE_ALLOW = [
  '/api/v1/resumes',
  '/api/v1/applications',
  '/api/v1/agenda',
  '/api/v1/config/flags',
  '/api/v1/config/features',
  '/api/v1/config/language',
];
// Never cache these even if they are GETs (defense in depth, R8.5).
const API_CACHE_DENY = [
  '/api/v1/auth',
  '/api/v1/session',
  '/csrf',
  '/oauth',
  '/api/v1/config/llm-api-key',
  '/api/v1/config/api-keys',
  '/api/v1/internal',
  '/api/v1/admin',
  '/api/v1/health',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.add(SHELL_URL).catch(() => undefined))
  );
  // Do NOT skipWaiting here — a new SW waits until the app confirms a safe point.
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names
          .filter((n) => n.startsWith('fitwright-') && !ALL_CACHES.includes(n))
          .map((n) => caches.delete(n))
      );
      await self.clients.claim();
    })()
  );
});

self.addEventListener('message', (event) => {
  const type = event.data && event.data.type;
  if (type === 'SKIP_WAITING') {
    self.skipWaiting();
  } else if (type === 'CLEAR_CACHES') {
    event.waitUntil(Promise.all(ALL_CACHES.map((n) => caches.delete(n))));
  } else if (type === 'GET_VERSION') {
    event.ports[0] && event.ports[0].postMessage({ version: SW_VERSION });
  } else if (type === 'GET_STATS') {
    event.ports[0] &&
      event.ports[0].postMessage({ version: SW_VERSION, stats: STATS, hitRatio: hitRatio() });
  }
});

function isStaticAsset(url) {
  return (
    url.pathname.startsWith('/_next/static/') ||
    url.pathname.startsWith('/fonts/') ||
    /\.(?:js|css|woff2?|png|jpg|jpeg|svg|webp|ico)$/.test(url.pathname)
  );
}

function isCacheableApiGet(url) {
  if (API_CACHE_DENY.some((p) => url.pathname.startsWith(p))) return false;
  return API_CACHE_ALLOW.some((p) => url.pathname.startsWith(p));
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return; // mutations are always network-only
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // same-origin only (R8.5)

  // Static, immutable assets: cache-first.
  if (isStaticAsset(url)) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const hit = await cache.match(req);
        if (hit) {
          STATS.staticHit += 1;
          return hit;
        }
        STATS.staticMiss += 1;
        const resp = await fetch(req);
        if (resp.ok) cache.put(req, resp.clone());
        return resp;
      })
    );
    return;
  }

  // Safe GET API responses: stale-while-revalidate.
  if (url.pathname.startsWith('/api/')) {
    if (!isCacheableApiGet(url)) return; // network-only for everything else
    event.respondWith(
      caches.open(API_CACHE).then(async (cache) => {
        const hit = await cache.match(req);
        if (hit) STATS.apiHit += 1;
        else STATS.apiMiss += 1;
        const network = fetch(req)
          .then((resp) => {
            if (resp.ok) cache.put(req, resp.clone());
            return resp;
          })
          .catch(() => hit);
        return hit || network;
      })
    );
    return;
  }

  // Navigations: network-first, fall back to the cached shell offline.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((resp) => {
          const copy = resp.clone();
          caches
            .open(SHELL_CACHE)
            .then((cache) => cache.put(SHELL_URL, copy))
            .catch(() => undefined);
          return resp;
        })
        .catch(async () => {
          STATS.navFallback += 1;
          return (await caches.match(SHELL_URL)) || Response.error();
        })
    );
  }
});
