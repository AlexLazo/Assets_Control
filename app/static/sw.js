/* Service worker mínimo, solo para que la app sea instalable y avise cuando no
 * hay conexión. NO guarda páginas ni datos de la app a propósito: cada escaneo
 * debe ir siempre al servidor (si se guardara en el teléfono podría parecer
 * registrado sin estarlo). Solo cachea los archivos estáticos (íconos, JS) y
 * una pantalla de "sin conexión".
 */
const VERSION = "v1";
const CACHE = "equipos-estaticos-" + VERSION;
const OFFLINE_URL = "/static/offline.html";
const PRECARGA = [OFFLINE_URL, "/static/icons/icon-192.png"];

self.addEventListener("install", (evento) => {
  evento.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECARGA)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches.keys()
      .then((claves) => Promise.all(claves.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (evento) => {
  const req = evento.request;
  if (req.method !== "GET") return; // los envíos (escaneos) nunca se interceptan
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Navegación: siempre a la red; si no hay conexión, pantalla de aviso.
  if (req.mode === "navigate") {
    evento.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)));
    return;
  }

  // Estáticos: se sirve lo guardado y se actualiza en segundo plano.
  if (url.pathname.startsWith("/static/")) {
    evento.respondWith(
      caches.open(CACHE).then((cache) =>
        cache.match(req).then((guardado) => {
          const red = fetch(req).then((resp) => {
            if (resp.ok) cache.put(req, resp.clone());
            return resp;
          }).catch(() => guardado);
          return guardado || red;
        })
      )
    );
  }
});
