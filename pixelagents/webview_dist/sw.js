const CACHE_NAME = 'pixel-agents-pwa-v1';
const STATIC_ASSET_PATHS = [
  '',
  'index.html',
  'manifest.json',
  'favicon.ico',
  'favicon.svg',
  'favicon-16.png',
  'favicon-32.png',
  'apple-touch-icon.png',
  'pwa-192x192.png',
  'pwa-512x512.png',
  'icon.png',
];

function scopeUrl(path = '') {
  return new URL(path, self.registration.scope).toString();
}

function isSameScope(url) {
  const scope = new URL(self.registration.scope);
  return url.origin === scope.origin && url.pathname.startsWith(scope.pathname);
}

async function cacheResponse(cache, request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      await cache.put(request, response.clone());
    }
  } catch {
    // Install and runtime caching are best-effort.
  }
}

async function discoverShellAssets() {
  const urls = new Set(STATIC_ASSET_PATHS.map((path) => scopeUrl(path)));
  const shellUrl = scopeUrl();

  try {
    const response = await fetch(new Request(shellUrl, { cache: 'reload' }));
    if (!response.ok) {
      return [...urls];
    }

    const html = await response.text();
    for (const match of html.matchAll(/(?:href|src)="([^"]+)"/g)) {
      const candidate = match[1];
      if (
        candidate.startsWith('data:') ||
        candidate.startsWith('#') ||
        candidate.startsWith('mailto:') ||
        candidate.startsWith('tel:')
      ) {
        continue;
      }

      const resolved = new URL(candidate, shellUrl);
      if (isSameScope(resolved)) {
        urls.add(resolved.toString());
      }
    }
  } catch {
    // Network might be unavailable during install; static assets still preload.
  }

  return [...urls];
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_NAME);
      const urls = await discoverShellAssets();
      await Promise.all(urls.map((url) => cacheResponse(cache, url)));
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const cacheNames = await caches.keys();
      await Promise.all(
        cacheNames
          .filter((cacheName) => cacheName !== CACHE_NAME)
          .map((cacheName) => caches.delete(cacheName)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;

  if (request.method !== 'GET') {
    return;
  }

  if (request.cache === 'only-if-cached' && request.mode !== 'same-origin') {
    return;
  }

  const url = new URL(request.url);
  if (!isSameScope(url)) {
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      (async () => {
        const cache = await caches.open(CACHE_NAME);
        try {
          const response = await fetch(request);
          if (response.ok) {
            await cache.put(request, response.clone());
          }
          return response;
        } catch {
          return (
            (await cache.match(request)) ??
            (await cache.match(scopeUrl())) ??
            (await cache.match(scopeUrl('index.html'))) ??
            Response.error()
          );
        }
      })(),
    );
    return;
  }

  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE_NAME);
      const cached = await cache.match(request);

      const networkPromise = fetch(request)
        .then(async (response) => {
          if (response.ok) {
            await cache.put(request, response.clone());
          }
          return response;
        })
        .catch(() => null);

      if (cached) {
        void networkPromise;
        return cached;
      }

      return (await networkPromise) ?? Response.error();
    })(),
  );
});
