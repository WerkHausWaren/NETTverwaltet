// Service Worker der Mitglieder-App (v3): Offline-Shell, Update-Fluss, Push, Badge.
// __VERSION__ wird beim Ausliefern serverseitig durch einen Build-Hash ersetzt.
const VERSION = "__VERSION__";
const STATIK = "statik-" + VERSION;
const SEITEN = "seiten-" + VERSION;
const SHELL = ["/offline", "/static/style.css", "/static/erwaehnungen.js",
               "/app-icon-192.png", "/app-icon-180.png", "/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(STATIK).then((c) => c.addAll(SHELL)).catch(() => {}));
  // KEIN skipWaiting hier: Der Nutzer entscheidet über den Update-Toast im Portal.
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== STATIK && k !== SEITEN).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener("message", (e) => {
  if (e.data && e.data.typ === "SKIP_WAITING") self.skipWaiting();
});

function istStatik(pfad) {
  return pfad.startsWith("/static/") || /^\/app-icon-\d+\.png$/.test(pfad) || pfad === "/manifest.webmanifest";
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  const pfad = url.pathname;
  // Auth-/Push-/SW-Pfade nie anfassen
  if (pfad === "/sw.js" || pfad.startsWith("/login") || pfad === "/logout" || pfad.startsWith("/portal/push")) return;

  if (istStatik(pfad)) {
    // Statik: stale-while-revalidate
    e.respondWith(caches.open(STATIK).then(async (c) => {
      const alt = await c.match(e.request);
      const neu = fetch(e.request).then((r) => {
        if (r && r.ok) c.put(e.request, r.clone());
        return r;
      }).catch(() => null);
      return alt || neu.then((r) => r || Response.error());
    }));
    return;
  }

  if (e.request.mode === "navigate" && (pfad.startsWith("/portal") || pfad === "/offline")) {
    // Portal-Seiten: Netz zuerst, Cache-Fallback, sonst Offline-Seite.
    // Wichtig: niemals Redirect-Antworten cachen (Login-Umleitungen!).
    e.respondWith(fetch(e.request).then((r) => {
      if (r && r.ok && !r.redirected) {
        const kopie = r.clone();
        caches.open(SEITEN).then((c) => c.put(e.request, kopie)).catch(() => {});
      }
      return r;
    }).catch(async () => {
      const m = await caches.match(e.request);
      return m || caches.match("/offline");
    }));
  }
});

// ------------------------------ Push ------------------------------------
self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (_) {}
  const arbeit = [self.registration.showNotification(data.titel || "Mitglieder-App", {
    body: data.text || "",
    icon: "/app-icon-192.png",
    badge: "/app-icon-192.png",
    // Sprache und Leserichtung des Textes – für Vorleseprogramme und
    // Betriebssysteme, die danach gehen.
    lang: data.sprache || "de",
    dir: "auto",
    data: data.url || "/portal/",
  })];
  if ("setAppBadge" in self.navigator && typeof data.zaehler === "number") {
    arbeit.push(self.navigator.setAppBadge(data.zaehler).catch(() => {}));
  }
  e.waitUntil(Promise.all(arbeit));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  // data kann ein String (Alt) oder {url,...} sein
  const d = e.notification.data;
  const ziel = (d && typeof d === "object" ? d.url : d) || "/portal/";
  e.waitUntil((async () => {
    const liste = await clients.matchAll({ type: "window", includeUncontrolled: true });
    const offen = liste.find((c) => new URL(c.url).origin === location.origin);
    if (offen) {
      try { await offen.focus(); } catch (_) {}
      // Die geöffnete Seite per Nachricht auf das Ziel umleiten – zuverlässig auch
      // auf iOS-PWAs, wo WindowClient.navigate() oft ohne Wirkung bleibt.
      offen.postMessage({ typ: "sw-navigiere", ziel: ziel });
      // Zusätzlich navigate() versuchen für offene Seiten ohne Nachrichten-Listener
      // (z. B. eine alte, noch nicht neu geladene Ansicht).
      try { if ("navigate" in offen) await offen.navigate(ziel); } catch (_) {}
      return;
    }
    await clients.openWindow(ziel);
  })());
});

// Abgelaufene Push-Abos automatisch erneuern (Browser rotiert Endpunkte gelegentlich)
self.addEventListener("pushsubscriptionchange", (e) => {
  e.waitUntil((async () => {
    try {
      const alt = e.oldSubscription;
      const key = alt && alt.options ? alt.options.applicationServerKey : null;
      if (!key) return;
      const neu = await self.registration.pushManager.subscribe({
        userVisibleOnly: true, applicationServerKey: key,
      });
      await fetch("/portal/push/abo", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(neu),
      });
    } catch (_) {}
  })());
});
