// Minimal service worker — shell only, required for installability. No audio caching.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('fetch', () => {});
