/* MealMind service worker.

   Strategy is split by request type, and the split matters:

   - Navigations / HTML  -> NETWORK FIRST. The app shell carries the UI logic,
     so a cached copy that is one deploy old shows the wrong app entirely
     (this is what stranded users on the pre-login build). The cache is only
     the offline fallback.
   - Static assets       -> cache first. Icons and the manifest are versioned
     by VERSION and change rarely.
   - API calls           -> never cached. Plans, prices and auth must be live.

   Bump VERSION on every shell change. */
const VERSION = "mealmind-v14";  // price-source indicator
const SHELL = ["./", "index.html", "manifest.webmanifest",
               "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)));
  self.skipWaiting();          // don't wait for old tabs to close
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    const stale = keys.filter((k) => k !== VERSION);
    await Promise.all(stale.map((k) => caches.delete(k)));
    await self.clients.claim();          // take over open tabs immediately

    // Claiming only redirects FUTURE fetches — a tab that already parsed the
    // old HTML keeps rendering it until it navigates again. On an upgrade
    // (never on a first install) push the open tabs to reload themselves,
    // because a shell too old to contain update-handling JS cannot do it.
    if (!stale.length) return;
    for (const tab of await self.clients.matchAll({ type: "window" })) {
      if ("navigate" in tab) {
        try { await tab.navigate(tab.url); continue; } catch (_) { /* fall through */ }
      }
      tab.postMessage("reload");         // iOS/Safari fallback
    }
  })());
});

function isHtml(request) {
  return request.mode === "navigate" ||
    (request.headers.get("accept") || "").includes("text/html");
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // API traffic is never cached — auth tokens, prices and plans must be fresh
  if (url.pathname.startsWith("/api/") || url.port === "8000") return;
  if (e.request.method !== "GET") return;

  if (isHtml(e.request)) {
    // network first: always get the newest shell when online
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          // only a good response may become the offline fallback — caching a
          // 404 or a 500 error page would strand the app permanently
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(VERSION).then((c) => c.put("index.html", copy)).catch(() => {});
          }
          return res;
        })
        .catch(() => caches.match("index.html").then((hit) => hit || caches.match("./")))
    );
    return;
  }

  // static assets: cache first, fill on miss
  e.respondWith(
    caches.match(e.request).then((hit) =>
      hit || fetch(e.request).then((res) => {
        const copy = res.clone();
        caches.open(VERSION).then((c) => c.put(e.request, copy));
        return res;
      })
    )
  );
});

// lets the page force an immediate takeover after an update is detected
self.addEventListener("message", (e) => {
  if (e.data === "skip-waiting") self.skipWaiting();
});
