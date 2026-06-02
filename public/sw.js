/* Service worker: cache ONLY the app shell + static assets. Financial data is
   always network-first and never cached (the server is the record of truth;
   iOS evicts PWA storage anyway). This just stops a home-screen launch from
   ever showing a Safari error. */
const CACHE = "mtd-shell-v1";
const SHELL = [
  "/public/app.css",
  "/public/app.js",
  "/public/manifest.json",
  "/public/vendor/htmx.min.js",
  "/public/vendor/alpine.min.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  // Static assets: cache-first.
  if (url.pathname.startsWith("/public/")) {
    e.respondWith(
      caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
        return res;
      }))
    );
    return;
  }
  // Everything else (data/HTML): network-first, no caching of figures.
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
