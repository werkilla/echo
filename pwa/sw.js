// Shell-only service worker: makes Echo installable. No audio caching in v1 (§11).
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('fetch', () => {});
