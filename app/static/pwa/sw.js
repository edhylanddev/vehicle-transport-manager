/* Vehicle Transport Manager service worker.
   - Makes the site installable as a home-screen app (iPhone, Android browsers, desktop).
   - Shows the Sunrise report notification sent by the server.
   - Shows a friendly page when there's no signal. Nothing else is cached: the app always shows live data. */
const OFFLINE = "/static/pwa/offline.html";
const CACHE = "vtm-offline-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.add(OFFLINE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE)));
});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data && event.data.text() }; }
  const title = data.title || "Vehicle Transport Manager";
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || "",
    tag: data.tag || "vtm",
    icon: "/static/img/icon-192.png",
    badge: "/static/img/icon-192.png",
    data: { url: data.url || "/dashboard" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || "/dashboard", self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if (w.url.startsWith(self.location.origin) && "focus" in w) {
          return w.focus().then(() => (w.navigate ? w.navigate(url) : null));
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
