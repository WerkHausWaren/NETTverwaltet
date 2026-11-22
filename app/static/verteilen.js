/* Modul „Verteilen" – Helfer:innen-Ansicht (Liste + Karte), Lauf-Ansicht einer
   Flyer-Route und Plakat-Runde. Nachgebaut aus der SPD-Wahlkampf-App:
   - Flyer: Stand je Straße UND Flyer, Schutz fremder Einträge, GPS-Schnell-
     Leiste (400 m), Routenplanung (Greedy, Luftlinie ×1,3), vor der Übernahme
     Auswahl der mitgenommenen Flyer, Fußweg-Linie über den Server (FOSSGIS),
     Route laufen mit Wake Lock.
   - Plakate: Standorte mit Nummer, Zustände, Fotos, Notizen, Verlauf;
     Plakat-Runde zum Kontrollieren/Abhängen zu Fuß oder im Auto, mit
     Signalton in 30 m Nähe (Mitfahr-Modus).
   - Zustellungen an Kontakte (16.11.2026): je Straße übernehmen; Name,
     Hausnummer und hausgenauer Punkt nur für die übernehmende Person und
     die Leitung; Planer, Lauf-Ansicht „zustellen“, Leitung setzt fehlende
     Kartenpunkte von Hand.
   Externe Verbindungen: nur die OSM-Kartenkacheln. */
(function () {
  "use strict";
  var root = document.getElementById("vt");
  if (!root) return;
  var MODUS = root.getAttribute("data-ansicht") || "liste";   // liste | laufen | runde | zustellen
  var API = root.getAttribute("data-api");
  var LISTE_URL = root.getAttribute("data-liste");
  var LAUF_URL = root.getAttribute("data-laufen");
  var RUNDE_URL = root.getAttribute("data-runde");
  var ZUST_URL = root.getAttribute("data-zustellen");
  var FOTO_URL = root.getAttribute("data-foto");
  var BILD_URL = root.getAttribute("data-flyerbild");
  var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  var ST = null, MAP = null, SCHICHT = null, PSCHICHT = null, PLAN_SCHICHT = null, WEG_SCHICHT = null, GEO_MARKER = null;
  var GEO = null, GEO_WATCH = null, WAKE = null, FOLGEN = true;
  var START = null, START_WAHL = false, VERSUCH = 0, PARAMS = "", PLAN = null, VORORT_WEG = null;
  var PANEL_HTML = "", TAB = "flyer", SETZEN = false, WAHL = null, WEG_SCHLUESSEL = "";
  var ORT_OFFEN = {};   // von Hand aufgeklappte Orte (Standard: alle zu, 16.11.2026)
  var RUNDE = null, NAH = null, TON = null;
  var ZSCHICHT = null, ZPLAN = null, ZPLAN_SCHICHT = null, PUNKT_ZID = null, Z_REIHE = [];
  var ERLEDIGT = {}, K_ERLEDIGT = "vt-erledigt:" + API, K_RUNDE = "vt-runde:" + API;   // je Aktion
  try { ERLEDIGT = JSON.parse(sessionStorage.getItem(K_ERLEDIGT) || "{}"); } catch (e) { ERLEDIGT = {}; }

  // ------------------------------------------------------------ Helfer
  function esc(t) {
    return String(t == null ? "" : t).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function zahl(n) { return Number(n || 0).toLocaleString("de-DE"); }
  function css(name, ersatz) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || ersatz;
  }
  function hav(a, b) {
    var R = 6371, dLa = (b[0] - a[0]) * Math.PI / 180, dLo = (b[1] - a[1]) * Math.PI / 180;
    var x = Math.sin(dLa / 2) * Math.sin(dLa / 2) +
      Math.cos(a[0] * Math.PI / 180) * Math.cos(b[0] * Math.PI / 180) * Math.sin(dLo / 2) * Math.sin(dLo / 2);
    return 2 * R * Math.asin(Math.sqrt(x));
  }
  function mStr(d) {
    if (d == null) return "";
    return d < 1 ? Math.round(d * 100) * 10 + " m" : d.toFixed(1).replace(".", ",") + " km";
  }
  function toast(t) {
    var el = document.getElementById("vt-toast");
    if (!el) { el = document.createElement("div"); el.id = "vt-toast"; document.body.appendChild(el); }
    el.textContent = t; el.className = "zeigen";
    clearTimeout(toast._t); toast._t = setTimeout(function () { el.className = ""; }, 2600);
  }
  function post(pfad, daten) {
    return fetch(API + pfad, {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRFToken": CSRF},
      body: JSON.stringify(daten || {})
    }).then(function (r) {
      return r.json().catch(function () { return {ok: false, fehler: "Die Verbindung ist abgebrochen – bitte noch einmal versuchen."}; });
    }, function () { return {ok: false, fehler: "Keine Verbindung – bitte noch einmal versuchen."}; });
  }
  function bildUrl(mid) { return BILD_URL.replace(/\/0\/bild$/, "/" + mid + "/bild"); }
  function fotoUrl(fid) { return FOTO_URL.replace(/0$/, fid); }
  function koord(s) { return (s && s.lat != null && s.lon != null) ? [s.lat, s.lon] : null; }
  function merke() { try { sessionStorage.setItem(K_ERLEDIGT, JSON.stringify(ERLEDIGT)); } catch (e) { /* egal */ } }

  // ---------------------------------------------------- Flyer-Daten
  function mats() { return (ST && ST.materialien) || []; }
  function mehrere() { return mats().length > 1; }
  function alleIds() { return mats().map(function (m) { return m.id; }); }
  function matTitel(mid) { var m = mats().filter(function (x) { return x.id === mid; })[0]; return m ? m.titel : "Flyer"; }
  function strasse(id) {
    for (var i = 0; i < ST.strassen.length; i++) if (ST.strassen[i].id === id) return ST.strassen[i];
    return null;
  }
  function matStat(sid, mid) { return (ST.status[String(sid)] || {})[String(mid)] || {status: "offen"}; }
  /* Gesamtstand einer Straße über die gewählten Flyer (Standard: alle). */
  function agg(sid, ids) {
    ids = ids && ids.length ? ids : alleIds();
    var e = ids.map(function (mid) { return matStat(sid, mid); });
    var verteilt = e.filter(function (x) { return x.status === "verteilt"; }).length;
    var eigen = e.some(function (x) { return x.status === "laeuft" && x.eigen; });
    var fremdL = e.filter(function (x) { return x.status === "laeuft" && !x.eigen; });
    var rest = e.filter(function (x) { return x.status !== "verteilt"; });
    var status = verteilt === e.length ? "verteilt" : eigen ? "laeuft" : fremdL.length ? "laeuft" : verteilt ? "teil" : "offen";
    var mitWer = fremdL[0] || e.filter(function (x) { return x.wer; })[0] || {};
    return {status: status, eigen: eigen || (status === "verteilt" && e.every(function (x) { return x.eigen; })),
      fremd: !eigen && fremdL.length > 0,
      alleFremd: rest.length > 0 && rest.every(function (x) { return x.status === "laeuft" && !x.eigen; }),
      offenIrgendwo: e.some(function (x) { return x.status === "offen"; }),
      wer: mitWer.wer || "", einzel: e};
  }
  function routeMats() { return (ST.route && ST.route.materialien && ST.route.materialien.length) ? ST.route.materialien : alleIds(); }
  function gewaehlt() {
    if (!mehrere()) return alleIds();
    if (!WAHL) return alleIds();
    var ids = alleIds().filter(function (id) { return WAHL[id]; });
    return ids;
  }

  function lade() {
    return fetch(API + "/state", {credentials: "same-origin", cache: "no-store"})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.fehler) { toast(d.fehler); return; }
        ST = d; zeichneAlles();
      }).catch(function () { toast("Die Daten konnten nicht geladen werden."); });
  }

  // ------------------------------------------------------------- Karte
  function farbe(s) {
    var x = agg(s.id);
    if (x.status === "verteilt") return css("--gruen", "#2e7d32");
    if (x.status === "laeuft") return x.eigen ? css("--blau", "#2563eb") : css("--gelb", "#e65100");
    if (x.status === "teil") return css("--gruen", "#2e7d32");
    return css("--text-schwach", "#888");
  }
  var PFARBE = {geplant: "#8a94a6", haengt: "#2e7d32", beschaedigt: "#d32f2f", weg: "#7b1fa2", abgehaengt: "#37474f"};
  function karteAufbauen() {
    if (MAP || typeof L === "undefined") return;
    var el = document.getElementById("vt-karte");
    if (!el) return;
    MAP = L.map(el, {zoomControl: MODUS === "liste", scrollWheelZoom: MODUS !== "liste"});
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, referrerPolicy: "strict-origin-when-cross-origin",
      attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>-Mitwirkende'
    }).addTo(MAP);
    var pkt = (ST.strassen || []).map(koord).filter(Boolean)
      .concat(((ST.plakate || {}).liste || []).map(function (p) { return [p.lat, p.lon]; }))
      .concat(zPunkte());
    if (pkt.length) MAP.fitBounds(pkt, {padding: [20, 20]}); else MAP.setView([53.5175, 12.6829], 13);
    MAP.on("click", function (e) {
      if (SETZEN) { SETZEN = false; setzKnopf(); plakatForm([e.latlng.lat, e.latlng.lng], false); return; }
      if (PUNKT_ZID) { zPunktSpeichern(PUNKT_ZID, [e.latlng.lat, e.latlng.lng]); PUNKT_ZID = null; return; }
      if (!START_WAHL) return;
      START = [e.latlng.lat, e.latlng.lng]; START_WAHL = false;
      toast("🚩 Startpunkt gesetzt"); planAnzeigen(); zPlanAnzeigen();
    });
  }
  function markerZeichnen() {
    if (!MAP) return;
    if (SCHICHT) { SCHICHT.remove(); SCHICHT = null; }
    if (PSCHICHT) { PSCHICHT.remove(); PSCHICHT = null; }
    if (ZSCHICHT) { ZSCHICHT.remove(); ZSCHICHT = null; }
    if (ST.zustellungen && (MODUS === "zustellen" || (MODUS === "liste" && TAB === "zustellen"))) zMarker();
    var zeigFlyer = ST.aktion.flyer && (MODUS === "laufen" || (MODUS === "liste" && TAB === "flyer"));
    var zeigPlakate = ST.plakate && (MODUS === "runde" || (MODUS === "liste" && TAB === "plakate"));
    if (zeigFlyer) {
      SCHICHT = L.layerGroup().addTo(MAP);
      var inRoute = {};
      (ST.route ? ST.route.strassen : []).forEach(function (id) { inRoute[id] = true; });
      ST.strassen.forEach(function (s) {
        var p = koord(s); if (!p) return;
        var teil = agg(s.id).status === "teil";
        var m = L.circleMarker(p, {radius: inRoute[s.id] ? 9 : 7, color: inRoute[s.id] ? css("--blau", "#2563eb") : (teil ? css("--gruen", "#2e7d32") : "#fff"),
          weight: inRoute[s.id] ? 3 : (teil ? 2.5 : 1.5), fillColor: farbe(s), fillOpacity: teil ? 0.35 : 0.9});
        m.bindPopup(function () { return popupHtml(s); });
        m.addTo(SCHICHT);
      });
    }
    if (zeigPlakate) {
      PSCHICHT = L.layerGroup().addTo(MAP);
      ST.plakate.liste.forEach(function (p) {
        if (p.status === "abgehaengt") return;
        var m = L.circleMarker([p.lat, p.lon], {radius: 9, color: "#fff", weight: 2, fillColor: PFARBE[p.status], fillOpacity: 0.95});
        m.bindPopup(function () { return plakatPopup(p); }, {maxWidth: 290});
        m.bindTooltip(p.nr, {direction: "top", offset: [0, -8]});
        m.addTo(PSCHICHT);
      });
    }
  }
  /* Karte auf den aktiven Reiter einpassen (Plakate bzw. Straßen). */
  function einpassen() {
    if (!MAP || !ST) return;
    var pkt = (MODUS === "zustellen" || (MODUS === "liste" && TAB === "zustellen")) ? zPunkte()
      : (TAB === "plakate" || MODUS === "runde")
      ? ((ST.plakate || {}).liste || []).filter(function (p) { return p.status !== "abgehaengt"; }).map(function (p) { return [p.lat, p.lon]; })
      : (ST.strassen || []).map(koord).filter(Boolean);
    if (pkt.length === 1) MAP.setView(pkt[0], 17);
    else if (pkt.length) MAP.fitBounds(pkt, {padding: [30, 30], maxZoom: 17});
  }
  function popupHtml(s) {
    return '<div class="vt-popup"><b>' + esc(s.name) + "</b><br><small>" + esc(s.ort) + " · " + zahl(s.haushalte) +
      " Haushalte</small>" + strassenStand(s) + "</div>";
  }
  /* Fußweg/Autoweg über den Server; Rückfall: gestrichelte Luftlinie. */
  function wegZeichnen(punkte, profil, farbeLinie, fertig) {
    if (!MAP) return;
    var schluessel = profil + "|" + punkte.map(function (p) { return p[0].toFixed(5) + "," + p[1].toFixed(5); }).join(";");
    if (schluessel === WEG_SCHLUESSEL) return;
    WEG_SCHLUESSEL = schluessel;
    if (WEG_SCHICHT) { WEG_SCHICHT.remove(); WEG_SCHICHT = null; }
    if (punkte.length < 2) return;
    WEG_SCHICHT = L.layerGroup().addTo(MAP);
    var luft = L.polyline(punkte, {dashArray: "6 8", weight: 3, color: farbeLinie}).addTo(WEG_SCHICHT);
    post("/weg", {punkte: punkte.slice(0, 41), profil: profil}).then(function (r) {
      if (schluessel !== WEG_SCHLUESSEL || !WEG_SCHICHT) return;
      if (r.ok && r.linie && r.linie.length > 1) {
        WEG_SCHICHT.removeLayer(luft);
        L.polyline(r.linie, {weight: 5, opacity: 0.75, color: farbeLinie}).addTo(WEG_SCHICHT);
      }
      if (fertig) fertig(r.ok ? r : null);
    });
  }

  // ------------------------------------------------------ Status-Knöpfe
  function statusText(x, mitWer) {
    if (x.status === "verteilt") return '<span class="vt-st-verteilt">✓ verteilt' + (x.eigen ? " von dir" : (x.wer ? " von " + esc(x.wer) : "")) + "</span>";
    if (x.status === "teil") return '<span class="vt-st-verteilt">◐ teilweise verteilt</span>';
    if (x.status === "laeuft") return '<span class="vt-st-laeuft">läuft ' + (x.eigen ? "bei dir" : "bei " + esc(x.wer)) + "</span>";
    return '<span class="vt-st-offen">offen</span>';
  }
  function knoepfeMat(s, mid) {
    if (!ST.aktion.offen) return "";
    var x = matStat(s.id, mid), d = ' data-id="' + s.id + '" data-mat="' + mid + '"', h = "";
    if (x.fremd) return "";
    if (x.status === "offen") h += '<button class="mp-btn vt-k" data-tun="status"' + d + ' data-st="laeuft">' + (mehrere() ? "✋ Übernehmen" : "✋ Ich übernehme") + "</button>";
    if (x.status === "laeuft") h += '<button class="mp-btn vt-k" data-tun="status"' + d + ' data-st="verteilt">✓ Verteilt</button>' +
      '<button class="mp-btn sekundaer vt-k" data-tun="status"' + d + ' data-st="offen">↩︎ Freigeben</button>';
    if (x.status === "verteilt") h += '<button class="mp-btn sekundaer vt-k" data-tun="status"' + d + ' data-st="offen">↩︎ Zurück</button>';
    return h;
  }
  function fotoKnopf(s) {
    return ST.aktion.offen ? '<label class="mp-btn sekundaer vt-k vt-foto" title="Foto hinzufügen">📷<input type="file" accept="image/*" capture="environment" data-foto="' + s.id + '"></label>' : "";
  }
  /* Stand + Knöpfe einer Straße: ein Flyer = eine Zeile, mehrere = je Flyer eine Zeile. */
  function strassenStand(s) {
    if (!mehrere()) {
      var mid = alleIds()[0], x = matStat(s.id, mid);
      return "<br>" + statusText(x) + '<div class="vt-reihe">' + knoepfeMat(s, mid) + fotoKnopf(s) + "</div>";
    }
    var e = mats().map(function (m) { return matStat(s.id, m.id); });
    var alleOffen = e.every(function (x) { return x.status === "offen"; });
    var alleMeine = e.every(function (x) { return x.status === "laeuft" && x.eigen; });
    var sammel = "";
    if (ST.aktion.offen && alleOffen) sammel = '<button class="mp-btn vt-k" data-tun="status" data-id="' + s.id + '" data-st="laeuft">✋ Alle Flyer übernehmen</button>';
    if (ST.aktion.offen && alleMeine) sammel = '<button class="mp-btn vt-k" data-tun="status" data-id="' + s.id + '" data-st="verteilt">✓ Alle verteilt</button>';
    return '<div class="vt-mats">' + mats().map(function (m) {
      var x = matStat(s.id, m.id);
      return '<div class="vt-mat-zeile"><span><span class="vt-mat-name">' + esc(m.titel) + "</span> " + statusText(x) + "</span>" +
        '<span class="vt-mat-knoepfe">' + knoepfeMat(s, m.id) + "</span></div>";
    }).join("") + '</div><div class="vt-reihe">' + sammel + fotoKnopf(s) + "</div>";
  }
  function statusSetzen(id, neu, ids, nurEigene) {
    var s = strasse(id), vorher = agg(id);
    var schonMeine = vorher.einzel.some(function (x) { return x.status === "verteilt" && x.eigen; });
    var fehlen = alleIds().filter(function (mid) { return matStat(id, mid).status !== "verteilt"; });
    var komplett = fehlen.every(function (mid) { return !ids || ids.indexOf(mid) !== -1; });
    return post("/status", {strasse: id, status: neu, materialien: ids || null, nur_eigene: !!nurEigene}).then(function (r) {
      if (!r.ok) { alert(r.fehler || "Das hat nicht geklappt."); return lade(); }
      if (neu === "verteilt") {
        try { if (navigator.vibrate) navigator.vibrate(60); } catch (e) { /* egal */ }
        if (komplett) {
          var n = (ST.fortschritt.meine_strassen || 0) + (schonMeine ? 0 : 1);
          toast("🎉 " + (s ? s.name : "Straße") + " ist verteilt – deine " + n + ". Straße. Danke für dein Ehrenamt!");
        } else toast("✓ " + (ids && ids.length === 1 ? matTitel(ids[0]) : "Flyer") + " in " + (s ? s.name : "der Straße") + " verteilt");
      } else if (neu === "laeuft") toast("✋ " + (s ? s.name : "Straße") + " läuft jetzt bei dir");
      return lade();
    });
  }
  function fotoHoch(input) {
    var datei = input.files && input.files[0];
    if (!datei) return;
    if (!confirm("Foto hochladen?\n\nBitte keine erkennbaren Personen fotografieren – nur Briefkästen, Plakat, Straßenschild o. Ä.")) { input.value = ""; return; }
    var fd = new FormData(); fd.append("foto", datei);
    if (input.getAttribute("data-pfoto")) fd.append("plakat", input.getAttribute("data-pfoto"));
    else fd.append("strasse", input.getAttribute("data-foto"));
    toast("📷 Foto wird hochgeladen …");
    fetch(API + "/foto", {method: "POST", credentials: "same-origin", headers: {"X-CSRFToken": CSRF}, body: fd})
      .then(function (r) { return r.json(); })
      .then(function (r) { if (!r.ok) alert(r.fehler || "Das Foto konnte nicht gespeichert werden."); else toast("📷 Foto gespeichert – danke!"); lade(); })
      .catch(function () { alert("Das Foto konnte nicht hochgeladen werden."); });
  }

  // ------------------------------------------------------- Liste
  function zeichneTabs() {
    var t = document.getElementById("vt-tabs");
    var da = [];
    if (ST.aktion.flyer) da.push("flyer");
    if (ST.aktion.plakate) da.push("plakate");
    if (ST.aktion.zustellungen) da.push("zustellen");
    if (da.indexOf(TAB) === -1) TAB = da[0] || "flyer";
    if (t) {
      t.style.display = da.length > 1 ? "" : "none";
      t.querySelectorAll("[data-tab]").forEach(function (b) { b.classList.toggle("sekundaer", b.getAttribute("data-tab") !== TAB); });
    }
    root.setAttribute("data-tab", TAB);   // CSS blendet den anderen Reiter aus
  }
  function zeichneFlyerKopf() {
    var F = ST.fortschritt, kopf = document.getElementById("vt-kopf");
    if (!kopf || !F) return;
    var bilder = mats().filter(function (m) { return m.bild; });
    kopf.innerHTML = (bilder.length ? '<div class="vt-flyerbilder">' + bilder.map(function (m) {
        return '<a href="' + bildUrl(m.id) + '" target="_blank" rel="noopener"><img src="' + bildUrl(m.id) + '" alt="' + esc(m.titel) + '"><span>' + esc(m.titel) + "</span></a>";
      }).join("") + "</div>" : "") +
      '<div class="vt-balken"><div style="width:' + F.prozent + '%"></div></div>' +
      "<div><b>" + F.prozent + " %</b> verteilt · " + F.strassen_verteilt + " von " + F.strassen_gesamt + " Straßen komplett" +
      (mehrere() ? "" : " · ≈ " + zahl(F.haushalte_verteilt) + " von " + zahl(F.haushalte_gesamt) + " Haushalten") + "</div>" +
      (mehrere() ? '<div class="feld-hinweis">' + F.materialien.map(function (m) { return esc(m.titel) + ": " + m.strassen_verteilt + " Straßen"; }).join(" · ") + "</div>" : "") +
      (F.meine_strassen ? '<div class="vt-lob">💪 Deine Bilanz: <b>' + F.meine_strassen + "</b> Straße" + (F.meine_strassen === 1 ? "" : "n") +
        " · ≈ " + zahl(F.meine_haushalte) + " Haushalte" + (F.stufe ? " · " + F.stufe[0] + " <b>" + esc(F.stufe[1]) + "</b>" : "") + "</div>"
        : '<div class="vt-lob">💪 Schnapp dir deine erste Straße – oder lass dir mit „🧭 Route planen“ eine Tour zusammenstellen.</div>');
  }
  function zeichneMatWahl() {
    var el = document.getElementById("vt-mat-wahl"); if (!el) return;
    if (!mehrere()) { el.innerHTML = ""; el.style.display = "none"; return; }
    if (!WAHL) { WAHL = {}; alleIds().forEach(function (id) { WAHL[id] = true; }); }
    el.style.display = "";
    el.innerHTML = '<div class="vt-feld-titel">Was nimmst du mit?</div>' + mats().map(function (m) {
      return '<label class="vt-mat-wahl"><input type="checkbox" data-matwahl="' + m.id + '"' + (WAHL[m.id] ? " checked" : "") + ">" +
        (m.bild ? '<img src="' + bildUrl(m.id) + '" alt="">' : "") + "<span>" + esc(m.titel) + "</span></label>";
    }).join("");
  }
  function zeichneListe() {
    zeichneTabs();
    var hinweis = document.getElementById("vt-abgeschlossen");
    if (hinweis) hinweis.style.display = ST.aktion.offen ? "none" : "";
    if (ST.aktion.flyer) {
      zeichneFlyerKopf();
      zeichneRouteKarte();
      zeichneMatWahl();
      var pl = document.getElementById("vt-planer"); if (pl) pl.style.display = ST.aktion.offen ? "" : "none";
      var orte = [], gruppen = {};
      ST.strassen.forEach(function (s) { if (!gruppen[s.ort]) { gruppen[s.ort] = []; orte.push(s.ort); } gruppen[s.ort].push(s); });
      var html = "";
      orte.forEach(function (ort) {
        var liste = gruppen[ort], hh = 0, fertig = 0;
        liste.forEach(function (s) { hh += s.haushalte || 0; if (agg(s.id).status === "verteilt") fertig += s.haushalte || 0; });
        html += '<details class="vt-ort" data-ort="' + esc(ort) + '"' + (ORT_OFFEN[ort] ? " open" : "") + "><summary><b>📍 " + esc(ort) + "</b> – " + liste.length +
          " Straßen · " + zahl(fertig) + "/" + zahl(hh) + " Haushalte komplett</summary>";
        liste.forEach(function (s) {
          var fotos = (ST.fotos[String(s.id)] || []);
          html += '<div class="vt-zeile" id="vz-' + s.id + '" data-name="' + esc((s.name + " " + s.ort).toLowerCase()) + '">' +
            '<div class="vt-name"><b>' + esc(s.name) + "</b> <small>" + zahl(s.haushalte) + " HH</small>" +
            fotos.map(function (f) { return ' <a href="' + fotoUrl(f) + '" target="_blank" rel="noopener" title="Foto ansehen">🖼</a>'; }).join("") +
            strassenStand(s) + "</div></div>";
        });
        html += "</details>";
      });
      document.getElementById("vt-liste").innerHTML = html;
      filtern();
    }
    if (ST.plakate) zeichnePlakate();
    if (ST.zustellungen) zeichneZustellungen();
    var neu = !MAP;
    karteAufbauen(); markerZeichnen(); if (neu) einpassen(); vorOrt();
  }
  function zeichneRouteKarte() {
    var el = document.getElementById("vt-route"); if (!el) return;
    var stopps = routeStopps();
    if (!stopps.length) { el.innerHTML = ""; el.style.display = "none"; return; }
    var fertig = stopps.filter(function (x) { return x.fertig; }).length;
    el.style.display = "";
    el.innerHTML = "<b>🚶 Deine Route</b> – " + stopps.length + " Straße" + (stopps.length === 1 ? "" : "n") + ", " + fertig + " geschafft" +
      (mehrere() ? '<div class="feld-hinweis">Du verteilst: ' + routeMats().map(matTitel).map(esc).join(", ") + "</div>" : "") +
      '<div class="vt-balken"><div style="width:' + Math.round(100 * fertig / stopps.length) + '%"></div></div>' +
      '<div class="vt-reihe"><a class="mp-btn" href="' + LAUF_URL + '">▶️ Als Route laufen</a>' +
      '<button class="mp-btn sekundaer" data-tun="beenden">Route beenden</button></div>';
  }
  function filtern() {
    var f = document.getElementById("vt-suche"); if (!f) return;
    var q = f.value.trim().toLowerCase();
    document.querySelectorAll(".vt-ort").forEach(function (d) {
      var treffer = 0;
      d.querySelectorAll(".vt-zeile").forEach(function (z) {
        var ok = !q || z.getAttribute("data-name").indexOf(q) !== -1;
        z.style.display = ok ? "" : "none"; if (ok) treffer++;
      });
      d.style.display = treffer ? "" : "none";
      d.open = q ? treffer > 0 : !!ORT_OFFEN[d.getAttribute("data-ort")];   // Suche klappt Treffer auf
    });
  }

  // ------------------------------------------------- Standort & vor Ort
  function standortAn(an) {
    if (an && GEO_WATCH === null) {
      if (!navigator.geolocation) { alert("Dieses Gerät kann den Standort nicht bestimmen."); return; }
      GEO_WATCH = navigator.geolocation.watchPosition(function (p) {
        GEO = [p.coords.latitude, p.coords.longitude];
        if (MAP) {
          if (!GEO_MARKER) GEO_MARKER = L.circleMarker(GEO, {radius: 8, color: "#fff", weight: 3, fillColor: css("--blau", "#2563eb"), fillOpacity: 1}).addTo(MAP);
          else GEO_MARKER.setLatLng(GEO);
          if (MODUS !== "liste" && FOLGEN) MAP.panTo(GEO, {animate: true, duration: 0.6});
        }
        vorOrt();
      }, function (e) {
        standortAn(false);
        alert(e.code === 1 ? "Der Standort ist nicht freigegeben. Bitte in den Browser-Einstellungen erlauben." : "Der Standort ist gerade nicht verfügbar.");
      }, {enableHighAccuracy: true, maximumAge: 5000, timeout: 15000});
    } else if (!an && GEO_WATCH !== null) {
      navigator.geolocation.clearWatch(GEO_WATCH); GEO_WATCH = null; GEO = null;
      if (GEO_MARKER) { GEO_MARKER.remove(); GEO_MARKER = null; }
    }
    var b = document.getElementById("vt-geo"); if (b) b.classList.toggle("sekundaer", GEO_WATCH === null);
    vorOrt();
  }
  function naechsteStrasse(max) {
    if (!GEO || !ST || !ST.strassen) return null;
    var best = null, bd = 1e9;
    ST.strassen.forEach(function (s) { var p = koord(s); if (!p) return; var d = hav(GEO, p); if (d < bd) { bd = d; best = s; } });
    return (best && bd <= max) ? {s: best, d: bd} : null;
  }
  function vorOrt() {
    if (MODUS === "laufen") { panel(); return; }
    if (MODUS === "runde") { rundePanel(); return; }
    if (MODUS === "zustellen") { zPanel(); return; }
    var el = document.getElementById("vt-vorort"); if (!el) return;
    document.querySelectorAll(".vt-zeile.hier").forEach(function (z) { z.classList.remove("hier"); });
    var n = (ST && ST.aktion.flyer && TAB === "flyer") ? naechsteStrasse(0.4) : null;
    if (!n || VORORT_WEG === n.s.id) { el.style.display = "none"; return; }
    var z = document.getElementById("vz-" + n.s.id); if (z) z.classList.add("hier");
    el.innerHTML = '<div style="display:flex;justify-content:space-between;gap:8px"><span>📍 Du bist in: <b>' + esc(n.s.name) + "</b> <small>(" + esc(n.s.ort) + ") · " +
      zahl(n.s.haushalte) + ' Haushalte</small></span><button class="mp-btn sekundaer vt-k" style="width:auto" data-tun="vorort-weg" data-id="' + n.s.id + '" aria-label="Hinweis schließen">✕</button></div>' +
      strassenStand(n.s);
    el.style.display = "";
  }

  // ------------------------------------------------------ Route planen
  function naechster(kand, pos) {
    var sortiert = kand.map(function (s) { return {s: s, d: hav(pos, koord(s))}; }).sort(function (a, b) { return a.d - b.d; });
    var i = VERSUCH <= 1 ? 0 : Math.floor(Math.random() * Math.min(3, sortiert.length));
    return sortiert[i];
  }
  /* Straße passt zu den gewählten Flyern: mindestens einer fehlt noch dort,
     und keiner davon läuft gerade bei anderen. */
  function brauchtFlyer(s, ids) {
    var x = agg(s.id, ids);
    return x.offenIrgendwo && !x.einzel.some(function (e) { return e.status === "laeuft" && !e.eigen; });
  }
  function rechneRoute() {
    if (!START) { alert("Bitte zuerst den Startpunkt wählen: 📍 Mein Standort oder 🎯 auf der Karte tippen."); return; }
    var ids = gewaehlt();
    if (!ids.length) { alert("Bitte mindestens einen Flyer ankreuzen, den du mitnimmst."); return; }
    var budget = +document.getElementById("vt-zeit").value, radius = +document.getElementById("vt-radius").value;
    var p = [budget, radius, START.join(), ids.join()].join("|");
    VERSUCH = (p === PARAMS) ? VERSUCH + 1 : 1; PARAMS = p;
    var kand = ST.strassen.filter(function (s) {
      return koord(s) && (s.haushalte || 0) > 0 && brauchtFlyer(s, ids) && hav(START, koord(s)) <= radius;
    });
    var route = [], zeit = 0, weg = 0, arbeitGes = 0, pos = START;
    while (kand.length) {
      var n = naechster(kand, pos), geh = n.d * 1.3 / 4 * 60, arbeit = 2 + (n.s.haushalte || 0) * 0.35;  // 4 km/h, ≈ 160 Flyer/h
      kand = kand.filter(function (k) { return k !== n.s; });
      if (zeit + geh + arbeit > budget) continue;   // zu groß fürs Restbudget → kleinere Straße suchen
      zeit += geh + arbeit; arbeitGes += arbeit; weg += n.d * 1.3; pos = koord(n.s); route.push(n.s);
    }
    PLAN = {strassen: route, zeit: zeit, weg: weg, arbeit: arbeitGes, ids: ids, fussweg: false};
    planAnzeigen();
  }
  function planAnzeigen() {
    var el = document.getElementById("vt-plan-erg"); if (!el) return;
    if (PLAN_SCHICHT) { PLAN_SCHICHT.remove(); PLAN_SCHICHT = null; }
    startText();
    if (!PLAN) { el.innerHTML = ""; wegZeichnen([], "fuss"); return; }
    if (!PLAN.strassen.length) { el.innerHTML = '<p class="vt-hinweis">In diesem Umkreis ist keine Straße mehr, in der deine Flyer fehlen – größeren Umkreis wählen?</p>'; return; }
    var hh = PLAN.strassen.reduce(function (a, s) { return a + (s.haushalte || 0); }, 0);
    var bedarf = PLAN.ids.map(function (mid) {
      var n = PLAN.strassen.reduce(function (a, s) { return a + (matStat(s.id, mid).status === "offen" ? (s.haushalte || 0) : 0); }, 0);
      return "<b>" + zahl(Math.ceil(n * 1.1)) + "</b> × " + esc(matTitel(mid));
    });
    el.innerHTML = "<p><b>" + PLAN.strassen.length + " Straßen</b> · ≈ " + zahl(hh) + " Haushalte · ≈ " + Math.round(PLAN.zeit) + " min · " +
      mStr(PLAN.weg) + (PLAN.fussweg ? " Fußweg" : " zu Fuß (geschätzt)") + "</p>" +
      '<p class="vt-bedarf">🎒 Mitnehmen (+10 %): ' + (mehrere() ? bedarf.join(" · ") : "<b>" + zahl(Math.ceil(hh * 1.1)) + " Flyer</b>") + "</p>" +
      '<ol class="vt-plan">' + PLAN.strassen.map(function (s) { return "<li>" + esc(s.name) + " <small>" + zahl(s.haushalte) + " HH</small></li>"; }).join("") + "</ol>" +
      '<div class="vt-reihe"><button class="mp-btn" data-tun="plan-uebernehmen">✅ Route übernehmen</button>' +
      '<button class="mp-btn sekundaer" data-tun="plan-alternative">🔁 Alternative</button></div>' +
      '<p class="feld-hinweis">' + (PLAN.fussweg ? "Die Linie folgt den Fußwegen (OpenStreetMap)." : "Die gestrichelte Linie ist die Luftlinie – der Fußweg wird gerade berechnet.") + "</p>";
    if (MAP) {
      PLAN_SCHICHT = L.layerGroup().addTo(MAP);
      PLAN.strassen.forEach(function (s, i) {
        L.marker(koord(s), {icon: L.divIcon({className: "vt-nr", html: String(i + 1), iconSize: [22, 22]})}).addTo(PLAN_SCHICHT);
      });
      MAP.fitBounds([START].concat(PLAN.strassen.map(koord)), {padding: [24, 24]});
      var plan = PLAN;
      wegZeichnen([START].concat(PLAN.strassen.map(koord)), "fuss", css("--blau", "#2563eb"), function (r) {
        if (!r || PLAN !== plan || plan.fussweg) return;
        plan.fussweg = true; plan.weg = r.meter / 1000; plan.zeit = r.sekunden / 60 + plan.arbeit;
        planAnzeigen();
      });
    }
  }
  function startText() {
    document.querySelectorAll("#vt-start-txt, .vt-start-txt").forEach(function (el) {
      el.textContent = START ? "🚩 Startpunkt gesetzt" : "Noch kein Startpunkt";
    });
  }
  function planUebernehmen() {
    if (!PLAN || !PLAN.strassen.length) return;
    if (ST.route && ST.route.strassen.length && !confirm("Deine bisherige Route wird durch die neue ersetzt. Noch nicht verteilte Straßen daraus geben wir wieder frei. Weiter?")) return;
    post("/route", {strassen: PLAN.strassen.map(function (s) { return s.id; }), start: START, materialien: PLAN.ids}).then(function (r) {
      if (!r.ok) { alert(r.fehler || "Die Route konnte nicht gespeichert werden."); return lade(); }
      PLAN = null; planAnzeigen();
      toast("🚶 Route mit " + r.n + " Straßen übernommen" + (r.belegt && r.belegt.length ? " – " + r.belegt.length + " waren inzwischen vergeben" : ""));
      return lade();
    });
  }

  // ------------------------------------------------------ Route laufen
  function routeStopps() {
    if (!ST || !ST.route || !ST.aktion.offen) return [];
    var ids = routeMats();
    return ST.route.strassen.map(function (id) {
      var s = strasse(id); if (!s) return null;
      var x = agg(id, ids);
      return {s: s, x: x, fertig: x.status === "verteilt", fremd: x.alleFremd,
        fehlt: ids.filter(function (mid) { return matStat(id, mid).status !== "verteilt"; })};
    }).filter(Boolean);
  }
  function routeAktuell(st) {
    var offen = st.filter(function (y) { return !y.fertig; });
    if (!offen.length) return null;
    if (GEO) {
      var best = null, bd = 1e9;
      offen.forEach(function (y) { var p = koord(y.s); if (!p) return; var d = hav(GEO, p); if (d < bd) { bd = d; best = y; } });
      if (best && bd <= 0.15) return {y: best, hier: true, d: bd};
    }
    var y = offen[0];
    return {y: y, hier: false, d: (GEO && koord(y.s)) ? hav(GEO, koord(y.s)) : null};
  }
  function panel() {
    var el = document.getElementById("vt-panel"); if (!el || !ST) return;
    var st = routeStopps(), html;
    if (!st.length) {
      html = '<b>Keine Route gespeichert.</b><br><small>Plane zuerst eine Route oder übernimm Straßen in der Liste.</small>' +
        '<div class="vt-reihe"><a class="mp-btn" href="' + LISTE_URL + '">‹ Zur Liste</a></div>';
    } else {
      var n = st.length, fertig = st.filter(function (y) { return y.fertig; }).length, akt = routeAktuell(st);
      if (!akt) {
        var hh = st.reduce(function (a, y) { return a + (y.s.haushalte || 0); }, 0);
        html = '<div style="text-align:center"><div style="font-size:32px">🎉</div><b style="font-size:19px">Route geschafft!</b><br>' +
          "<small>" + n + " Straße" + (n === 1 ? "" : "n") + " · ≈ " + zahl(hh) + " Haushalte – danke dir!</small>" +
          '<div class="vt-reihe"><button class="mp-btn" data-tun="abschliessen">✓ Abschließen</button>' +
          '<button class="mp-btn sekundaer" data-tun="dazu">➕ Noch eine Straße</button></div></div>';
      } else {
        var y = akt.y;
        html = '<div class="vt-zeile-klein"><span>🚶 Straße ' + (fertig + 1) + " von " + n + "</span><span>" + fertig + " ✓ geschafft</span></div>" +
          '<div class="vt-balken"><div style="width:' + Math.round(100 * fertig / n) + '%"></div></div>' +
          '<div class="vt-wo">' + (akt.hier ? "📍 Du bist hier:" : "➡️ Als Nächstes") + '<span id="vt-dist"></span></div>' +
          '<div class="vt-strasse">' + esc(y.s.name) + "</div><small>" + esc(y.s.ort) + " · " + zahl(y.s.haushalte) + " Haushalte" +
          (mehrere() ? " · einwerfen: " + y.fehlt.map(matTitel).map(esc).join(" + ") : "") +
          (y.fremd ? " · ⚠️ läuft inzwischen bei " + esc(y.x.wer) : "") + "</small>" +
          '<div class="vt-reihe">' + (y.fremd
            ? '<button class="mp-btn sekundaer" style="flex:2" data-tun="raus" data-id="' + y.s.id + '">✕ Aus der Route nehmen</button>'
            : '<button class="mp-btn" style="flex:2" data-tun="status-route" data-id="' + y.s.id + '" data-st="verteilt">✅ Fertig verteilt</button>') +
          ((n - fertig) > 1 ? '<button class="mp-btn sekundaer" data-tun="spaeter" data-id="' + y.s.id + '">⏭ Später</button>' : "") + "</div>";
      }
    }
    if (html !== PANEL_HTML) { el.innerHTML = html; PANEL_HTML = html; }   // sonst gehen Taps verloren
    var d = document.getElementById("vt-dist");
    if (d) { var a = st.length ? routeAktuell(st) : null; d.textContent = (a && !a.hier && a.d != null) ? " · " + mStr(a.d) + " entfernt" : ""; }
    var lst = document.getElementById("vt-lauf-liste");
    if (lst && !lst.hidden) lst.querySelector(".vt-lauf-inhalt").innerHTML = laufListeHtml();
    // Fußweg-Linie durch die noch offenen Stopps (nur bei Änderungen neu)
    var rest = st.filter(function (y) { return !y.fertig; }).map(function (y) { return koord(y.s); }).filter(Boolean);
    var anfang = (ST.route && ST.route.start) || null;
    wegZeichnen((anfang ? [anfang] : []).concat(rest), "fuss", css("--blau", "#2563eb"));
  }
  function laufListeHtml() {
    var st = routeStopps();
    if (!st.length) return "<p>Keine Route gespeichert.</p>";
    return st.map(function (y, j) {
      return '<div class="vt-lauf-zeile">' +
        '<button class="vt-haken' + (y.fertig ? " an" : "") + '"' + (y.fremd ? " disabled" : "") + ' data-tun="status-route" data-id="' + y.s.id +
        '" data-st="' + (y.fertig ? "laeuft" : "verteilt") + '" aria-label="' + (y.fertig ? "Haken entfernen" : "als verteilt abhaken") + '">' + (y.fertig ? "✓" : "") + "</button>" +
        '<div style="flex:1;min-width:0"><b>' + (j + 1) + ". " + esc(y.s.name) + "</b><br><small>" + zahl(y.s.haushalte) + " Haushalte" +
        (y.fremd ? " · läuft bei " + esc(y.x.wer) : "") + "</small></div>" +
        (y.fertig ? "" : '<button class="mp-btn sekundaer vt-k" data-tun="raus" data-id="' + y.s.id + '" aria-label="aus der Route nehmen">✕</button>') + "</div>";
    }).join("");
  }
  function routeDazu() {
    var st = routeStopps(), letzte = st.length ? st[st.length - 1].s : null;
    var ref = GEO || (letzte ? koord(letzte) : null);
    if (!ref) { alert("Bitte zuerst den Standort freigeben."); return; }
    var drin = {}, ids = routeMats(); st.forEach(function (y) { drin[y.s.id] = true; });
    var best = null, bd = 1e9;
    ST.strassen.forEach(function (s) {
      var p = koord(s); if (!p || !(s.haushalte > 0) || drin[s.id] || !brauchtFlyer(s, ids)) return;
      var d = hav(ref, p); if (d < bd) { bd = d; best = s; }
    });
    if (!best) { alert("In der Nähe ist keine freie Straße mehr – stark!"); return; }
    if (!confirm("„" + best.name + "“ (" + mStr(bd) + " entfernt, " + zahl(best.haushalte) + " Haushalte) in deine Route aufnehmen?")) return;
    post("/route", {strassen: st.map(function (y) { return y.s.id; }).concat([best.id]), materialien: ids}).then(function (r) {
      if (!r.ok) alert(r.fehler || "Das hat nicht geklappt."); else toast("➕ " + best.name + " ist jetzt in deiner Route");
      lade();
    });
  }
  function wachHalten(an) {
    if (!("wakeLock" in navigator)) return;
    if (an) navigator.wakeLock.request("screen").then(function (w) { WAKE = w; }, function () { WAKE = null; });
    else if (WAKE) { WAKE.release(); WAKE = null; }
  }

  // --------------------------------------------------------- Plakate
  var PTEXT = {geplant: "⏳ geplant", haengt: "✅ hängt", beschaedigt: "⚠️ beschädigt", weg: "❌ weg", abgehaengt: "⬇️ abgehängt"};
  function plakat(pid) {
    var l = (ST.plakate || {}).liste || [];
    for (var i = 0; i < l.length; i++) if (l[i].id === pid) return l[i];
    return null;
  }
  function plakatKnoepfe(p, klein) {
    if (!ST.aktion.offen) return "";
    var k = klein ? " vt-k" : "", d = ' data-pid="' + p.id + '"', h = "";
    function b(m, txt, zweit) { return '<button class="mp-btn' + (zweit ? " sekundaer" : "") + k + '" data-tun="p-melden" data-m="' + m + '"' + d + ">" + txt + "</button>"; }
    if (p.status === "geplant") h += b("haengt", "✅ Hängt jetzt") + (p.loeschbar ? '<button class="mp-btn sekundaer' + k + '" data-tun="p-loeschen"' + d + ' aria-label="Plakat löschen">🗑</button>' : "");
    if (p.status === "haengt") h += b("ok", "✅ Alles ok") + b("beschaedigt", "⚠️ Schaden", true) + b("weg", "❌ Weg", true) + b("abgehaengt", "⬇️ Abgehängt", true);
    if (p.status === "beschaedigt" || p.status === "weg") h += b("ersetzt", "🔁 Ersatz hängt") + (p.status === "beschaedigt" ? b("weg", "❌ Weg", true) : "") + b("abgehaengt", "⬇️ Abgehängt", true);
    if (p.status !== "abgehaengt") h += '<button class="mp-btn sekundaer' + k + '" data-tun="p-notiz"' + d + ' aria-label="Bemerkung">📝</button>' +
      '<label class="mp-btn sekundaer vt-k vt-foto" title="Foto hinzufügen">📷<input type="file" accept="image/*" capture="environment" data-pfoto="' + p.id + '"></label>';
    return h;
  }
  function plakatPopup(p) {
    return '<div class="vt-popup"><b style="font-size:16px">' + esc(p.nr) + "</b> – " + PTEXT[p.status] +
      (p.strasse ? "<br>" + esc(p.strasse) : "") + (p.hinweis ? "<br><i>" + esc(p.hinweis) + "</i>" : "") +
      '<div class="vt-reihe">' + plakatKnoepfe(p, true) + "</div>" +
      (p.fotos.length ? '<div class="vt-pfotos">' + p.fotos.map(function (f) { return '<a href="' + fotoUrl(f) + '" target="_blank" rel="noopener"><img src="' + fotoUrl(f) + '" alt="Foto"></a>'; }).join("") + "</div>" : "") +
      (p.verlauf.length ? '<details><summary class="feld-hinweis">Verlauf</summary><div class="feld-hinweis">' +
        p.verlauf.map(function (v) { return esc(v.am.slice(8, 10) + "." + v.am.slice(5, 7) + ". " + v.am.slice(11, 16)) + " " + esc(v.wer) + ": " + esc(v.was); }).join("<br>") + "</div></details>" : "") + "</div>";
  }
  function zeichnePlakate() {
    var P = ST.plakate, z = P.zaehler, kopf = document.getElementById("vt-pkopf");
    if (kopf) {
      var frist = "";
      if (z.abhaengen_bis) {
        var tage = Math.ceil((new Date(z.abhaengen_bis + "T23:59:59") - new Date()) / 864e5);
        frist = '<div class="' + (tage <= 3 && z.aktiv ? "vt-hinweis" : "feld-hinweis") + '">⏰ Abhängen bis ' + esc(z.abhaengen_bis.slice(8, 10) + "." + z.abhaengen_bis.slice(5, 7) + "." + z.abhaengen_bis.slice(0, 4)) +
          (z.aktiv ? (tage < 0 ? " – die Frist ist abgelaufen, bitte abhängen!" : " – noch " + tage + " Tag" + (tage === 1 ? "" : "e")) : " – alles abgehängt ✓") + "</div>";
      }
      kopf.innerHTML = '<div class="vt-pzahlen">' +
        '<span><b>' + z.haengt + "</b> hängen" + (z.max ? " von " + z.max : "") + "</span>" +
        "<span><b>" + z.geplant + "</b> geplant</span>" +
        (z.beschaedigt ? '<span style="color:var(--rot)"><b>' + z.beschaedigt + "</b> beschädigt</span>" : "") +
        (z.weg ? '<span style="color:var(--rot)"><b>' + z.weg + "</b> weg</span>" : "") +
        "<span><b>" + z.abgehaengt + "</b> abgehängt</span></div>" + frist +
        (P.meine_gehaengt ? '<div class="vt-lob">💪 Du hast <b>' + P.meine_gehaengt + "</b> Plakat" + (P.meine_gehaengt === 1 ? "" : "e") + " aufgehängt – danke!</div>" : "") +
        (z.max && z.aktiv >= z.max ? '<div class="vt-hinweis">Die erlaubte Zahl an Standorten ist erreicht – neue Plakate erst nach dem Abhängen eines anderen.</div>' : "");
    }
    var knoepfe = document.getElementById("vt-pknoepfe");
    if (knoepfe) knoepfe.style.display = ST.aktion.offen ? "" : "none";
    var liste = document.getElementById("vt-pliste");
    if (liste) {
      var l = P.liste.filter(function (p) { return p.status !== "abgehaengt"; });
      liste.innerHTML = l.length ? l.map(function (p) {
        return '<div class="vt-zeile"><div class="vt-name"><b>' + esc(p.nr) + "</b> " + PTEXT[p.status] +
          (p.strasse ? " · " + esc(p.strasse) : "") + (p.hinweis ? '<br><small>' + esc(p.hinweis) + "</small>" : "") +
          '<div class="vt-reihe">' + plakatKnoepfe(p, true) + '<button class="mp-btn sekundaer vt-k" data-tun="p-zeigen" data-pid="' + p.id + '" aria-label="Auf der Karte zeigen">🗺</button></div></div></div>';
      }).join("") : '<p class="feld-hinweis">Noch hängt kein Plakat. Setze das erste über „➕ Plakat hier“ oder auf der Karte.</p>';
    }
  }
  function setzKnopf() {
    var b = document.getElementById("vt-psetzen"); if (b) b.classList.toggle("sekundaer", !SETZEN);
    var h = document.getElementById("vt-psetz-hinweis"); if (h) h.textContent = SETZEN ? "Jetzt auf die Karte tippen – genau auf den Mast." : "";
  }
  function plakatForm(ll, vorOrtJetzt) {
    var el = document.getElementById("vt-pform"); if (!el) return;
    var dl = (ST.strassen || []).map(function (s) { return '<option value="' + esc(s.name) + '">'; }).join("");
    el.innerHTML = "<b>➕ Neues Plakat</b>" +
      '<input type="text" id="vt-pf-str" list="vt-pf-liste" placeholder="Straße" maxlength="80" autocomplete="off"><datalist id="vt-pf-liste">' + dl + "</datalist>" +
      '<input type="text" id="vt-pf-hin" placeholder="Mast-Beschreibung, z. B. Lichtmast vor Nr. 12" maxlength="160">' +
      '<div class="vt-reihe">' +
      '<button class="mp-btn' + (vorOrtJetzt ? "" : " sekundaer") + '" data-tun="p-speichern" data-st="haengt" data-lat="' + ll[0] + '" data-lon="' + ll[1] + '">✅ Hängt jetzt</button>' +
      '<button class="mp-btn' + (vorOrtJetzt ? " sekundaer" : "") + '" data-tun="p-speichern" data-st="geplant" data-lat="' + ll[0] + '" data-lon="' + ll[1] + '">⏳ Nur planen</button>' +
      '<button class="mp-btn sekundaer" data-tun="p-abbrechen">Abbrechen</button></div>';
    el.style.display = "";
    // nächstgelegene bekannte Straße vorschlagen (≤ 250 m)
    var best = null, bd = 1e9;
    (ST.strassen || []).forEach(function (s) { var p = koord(s); if (!p) return; var d = hav(ll, p); if (d < bd) { bd = d; best = s; } });
    if (best && bd < 0.25) document.getElementById("vt-pf-str").value = best.name;
    el.scrollIntoView({behavior: "smooth", block: "center"});
  }
  function plakatSpeichern(b) {
    var lat = +b.getAttribute("data-lat"), lon = +b.getAttribute("data-lon");
    post("/plakat", {lat: lat, lon: lon, status: b.getAttribute("data-st"),
      strasse: (document.getElementById("vt-pf-str") || {}).value || "", hinweis: (document.getElementById("vt-pf-hin") || {}).value || ""})
      .then(function (r) {
        if (!r.ok) { alert(r.fehler || "Das Plakat konnte nicht gespeichert werden."); return; }
        var el = document.getElementById("vt-pform"); if (el) { el.style.display = "none"; el.innerHTML = ""; }
        if (r.status === "haengt") { ERLEDIGT[r.id] = Date.now(); merke(); }
        alert("Plakat " + r.nr + (r.status === "haengt" ? " hängt ✓" : " ist geplant") + "\n\nBitte die Nummer " + r.nr + " aufs Plakat schreiben!");
        lade();
      });
  }
  function plakatMelden(pid, meldung) {
    var p = plakat(pid), daten = {meldung: meldung};
    if (meldung === "beschaedigt") {
      var s = prompt("Was ist beschädigt? (z. B. zerrissen, beschmiert, abgefallen)");
      if (s === null) return Promise.resolve();
      daten.schaden = s;
    }
    if (meldung === "weg" && !confirm((p ? p.nr : "Plakat") + " ist komplett verschwunden?\n\nDer Standort bleibt reserviert – bitte bald Ersatz aufhängen.")) return Promise.resolve();
    if (meldung === "abgehaengt" && !confirm((p ? p.nr : "Plakat") + " als abgehängt melden?")) return Promise.resolve();
    if (meldung === "notiz") {
      var t = prompt("Bemerkung zu " + (p ? p.nr : "dem Plakat") + ":");
      if (!t) return Promise.resolve();
      daten.notiz = t;
    }
    return post("/plakat/" + pid, daten).then(function (r) {
      if (!r.ok) { alert(r.fehler || "Das hat nicht geklappt."); return lade(); }
      ERLEDIGT[pid] = Date.now(); merke();
      if (NAH === pid) NAH = null;
      var txt = {haengt: "hängt ✓", ok: "kontrolliert ✓ – danke!", beschaedigt: "Schaden gemeldet ✓", weg: "als weg gemeldet ✓",
        ersetzt: "Ersatz hängt ✓", abgehaengt: "abgehängt ✓", notiz: "Bemerkung notiert ✓"}[meldung];
      toast(r.nr + ": " + txt + (r.vorstand_informiert ? " – der Vorstand ist informiert" : ""));
      return lade();
    });
  }

  // ------------------------------------------------------- Plakat-Runde
  function piep() {
    try {
      if (!TON) return;
      [0, 0.22].forEach(function (t) {
        var o = TON.createOscillator(), g = TON.createGain();
        o.frequency.value = 880; o.connect(g); g.connect(TON.destination);
        g.gain.setValueAtTime(0.0001, TON.currentTime + t);
        g.gain.exponentialRampToValueAtTime(0.3, TON.currentTime + t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, TON.currentTime + t + 0.18);
        o.start(TON.currentTime + t); o.stop(TON.currentTime + t + 0.2);
      });
    } catch (e) { /* ohne Ton weiter */ }
    try { if (navigator.vibrate) navigator.vibrate([120, 80, 120]); } catch (e) { /* egal */ }
  }
  function rundeZiele(art) {
    var l = (ST.plakate || {}).liste || [];
    if (art === "abhaengen") return l.filter(function (p) { return p.status === "haengt" || p.status === "beschaedigt" || p.status === "weg"; });
    if (art === "aufhaengen") return l.filter(function (p) { return p.status === "geplant"; });
    return l.filter(function (p) { return p.status === "haengt" || p.status === "beschaedigt" || p.status === "weg"; });
  }
  function rundeErledigt(p) {
    if (!RUNDE) return true;
    if (RUNDE.art === "abhaengen") return p.status === "abgehaengt";
    if (RUNDE.art === "aufhaengen") return p.status !== "geplant";
    return (ERLEDIGT[p.id] || 0) > RUNDE.seit;
  }
  function rundeStarten(art, profil) {
    try { TON = TON || new (window.AudioContext || window.webkitAudioContext)(); if (TON.resume) TON.resume(); } catch (e) { TON = null; }
    var start = GEO || (MAP ? [MAP.getCenter().lat, MAP.getCenter().lng] : null);
    var ziele = art === "frei" ? [] : rundeZiele(art).slice();
    var reihe = [], pos = start;
    while (ziele.length && pos) {
      var best = 0, bd = 1e9;
      ziele.forEach(function (p, i) { var d = hav(pos, [p.lat, p.lon]); if (d < bd) { bd = d; best = i; } });
      var p = ziele.splice(best, 1)[0]; reihe.push(p.id); pos = [p.lat, p.lon];
    }
    RUNDE = {art: art, profil: profil, reihe: reihe, seit: Date.now(), start: start};
    try { sessionStorage.setItem(K_RUNDE, JSON.stringify(RUNDE)); } catch (e) { /* egal */ }
    FOLGEN = true; standortAn(true); wachHalten(true);
    PANEL_HTML = ""; rundePanel();
  }
  function rundeOffen() {
    if (!RUNDE) return [];
    return RUNDE.reihe.map(plakat).filter(function (p) { return p && !rundeErledigt(p); });
  }
  function rundePanel() {
    var el = document.getElementById("vt-panel"); if (!el || !ST || !ST.plakate) return;
    var html;
    // 30-m-Nähe: Mitfahr-/Kontrollfrage (für jedes Plakat höchstens alle 15 min)
    if (RUNDE && GEO && !NAH) {
      var nah = null, nd = 1e9;
      ST.plakate.liste.forEach(function (p) {
        if (p.status === "abgehaengt" || (RUNDE.art !== "aufhaengen" && p.status === "geplant")) return;
        if (Date.now() - (ERLEDIGT[p.id] || 0) < 15 * 60 * 1000) return;
        var d = hav(GEO, [p.lat, p.lon]); if (d < nd) { nd = d; nah = p; }
      });
      if (nah && nd <= 0.03) { NAH = nah.id; piep(); }
    }
    if (!RUNDE) {
      html = '<b>📍 Plakat-Runde</b><div class="vt-feld"><label>Was hast du vor?<select id="vt-r-art">' +
        '<option value="kontrolle">🔍 Kontrollieren</option><option value="abhaengen">⬇️ Abhängen</option>' +
        '<option value="aufhaengen">✅ Geplante aufhängen</option><option value="frei">🚗 Nur mitfahren</option></select></label>' +
        '<label>Unterwegs<select id="vt-r-profil"><option value="fuss">🚶 zu Fuß</option><option value="auto">🚗 im Auto</option></select></label></div>' +
        '<p class="feld-hinweis">Kommst du einem Plakat auf 30 m nahe, piept die App und fragt „Alles ok?“. Im Auto bitte nur als Beifahrer:in bedienen.</p>' +
        '<div class="vt-reihe"><button class="mp-btn" data-tun="r-start">▶️ Los geht’s</button></div>';
    } else if (NAH && plakat(NAH)) {
      var p = plakat(NAH);
      html = '<div class="vt-wo">🔔 Du bist bei Plakat</div><div class="vt-strasse">' + esc(p.nr) + " · " + PTEXT[p.status] + "</div>" +
        "<small>" + esc(p.strasse) + (p.hinweis ? " · " + esc(p.hinweis) : "") + "</small>" +
        '<div class="vt-reihe">' + plakatKnoepfe(p, false).replace(/vt-k vt-foto/, "vt-foto") + '<button class="mp-btn sekundaer" data-tun="r-weiter" data-pid="' + p.id + '">Weiter ›</button></div>';
    } else {
      var offen = rundeOffen(), n = RUNDE.reihe.length, fertig = n - offen.length;
      if (RUNDE.art === "frei") {
        html = "<b>🚗 Mitfahr-Modus</b><br><small>Die Karte folgt dir. In 30 m Nähe eines Plakats piept die App.</small>" +
          '<div class="vt-reihe"><button class="mp-btn sekundaer" data-tun="r-neu">🔁 Andere Runde</button></div>';
      } else if (!offen.length) {
        html = '<div style="text-align:center"><div style="font-size:32px">🎉</div><b style="font-size:19px">Runde geschafft!</b><br><small>' +
          n + " Plakat" + (n === 1 ? "" : "e") + " – danke dir!</small>" +
          '<div class="vt-reihe"><a class="mp-btn" href="' + LISTE_URL + '#plakate">✓ Fertig</a><button class="mp-btn sekundaer" data-tun="r-neu">🔁 Neue Runde</button></div></div>';
      } else {
        var z = offen[0], d = GEO ? hav(GEO, [z.lat, z.lon]) : null;
        html = '<div class="vt-zeile-klein"><span>📍 Plakat ' + (fertig + 1) + " von " + n + "</span><span>" + fertig + " ✓</span></div>" +
          '<div class="vt-balken"><div style="width:' + Math.round(100 * fertig / Math.max(n, 1)) + '%"></div></div>' +
          '<div class="vt-wo">➡️ Als Nächstes' + (d != null ? " · " + mStr(d) : "") + '</div><div class="vt-strasse">' + esc(z.nr) + " · " + PTEXT[z.status] + "</div>" +
          "<small>" + esc(z.strasse) + (z.hinweis ? " · " + esc(z.hinweis) : "") + "</small>" +
          '<div class="vt-reihe">' + plakatKnoepfe(z, false).replace(/vt-k vt-foto/, "vt-foto") + '<button class="mp-btn sekundaer" data-tun="r-weiter" data-pid="' + z.id + '">⏭ Überspringen</button></div>';
      }
    }
    if (html !== PANEL_HTML) { el.innerHTML = html; PANEL_HTML = html; }
    var lst = document.getElementById("vt-lauf-liste");
    if (lst && !lst.hidden) lst.querySelector(".vt-lauf-inhalt").innerHTML = rundeListeHtml();
    if (RUNDE && RUNDE.art !== "frei") {
      var rest = rundeOffen().map(function (p) { return [p.lat, p.lon]; });
      var von = RUNDE.start ? [RUNDE.start] : [];
      wegZeichnen(von.concat(rest).slice(0, 41), RUNDE.profil, css("--gruen", "#2e7d32"));
    }
  }
  function rundeListeHtml() {
    var l = RUNDE && RUNDE.reihe.length ? RUNDE.reihe.map(plakat).filter(Boolean) : ST.plakate.liste.filter(function (p) { return p.status !== "abgehaengt"; });
    if (!l.length) return "<p>Keine Plakate in dieser Runde.</p>";
    return l.map(function (p, j) {
      var fertig = RUNDE && rundeErledigt(p);
      return '<div class="vt-lauf-zeile"><span class="vt-haken' + (fertig ? " an" : "") + '" aria-hidden="true">' + (fertig ? "✓" : "") + "</span>" +
        '<div style="flex:1;min-width:0"><b>' + (j + 1) + ". " + esc(p.nr) + "</b> " + PTEXT[p.status] + "<br><small>" + esc(p.strasse) + "</small></div>" +
        '<button class="mp-btn sekundaer vt-k" data-tun="p-zeigen" data-pid="' + p.id + '" aria-label="Auf der Karte zeigen">🗺</button></div>';
    }).join("");
  }


  // --------------------------------------------------------- Zustellungen
  var ZTEXT = {offen: "offen", laeuft: "bei dir in Arbeit", zugestellt: "✅ zugestellt", nicht_zustellbar: "⚠️ nicht zustellbar"};
  var ZFARBE = {offen: "--text-schwach", laeuft: "--blau", zugestellt: "--gruen", nicht_zustellbar: "--rot"};
  function zGruppen() { return (ST && ST.zustellungen && ST.zustellungen.gruppen) || []; }
  function zEintraege() { var a = []; zGruppen().forEach(function (g) { g.eintraege.forEach(function (e) { e.gruppe = g; a.push(e); }); }); return a; }
  function zEintrag(zid) { var l = zEintraege(); for (var i = 0; i < l.length; i++) if (l[i].id === zid) return l[i]; return null; }
  function zOrt(e) { return e.lat != null ? [e.lat, e.lon] : (e.gruppe && e.gruppe.punkt) || null; }
  function zPunkte() {
    var pkt = [];
    zGruppen().forEach(function (g) {
      var eig = g.eintraege.filter(function (e) { return e.lat != null; });
      if (eig.length) eig.forEach(function (e) { pkt.push([e.lat, e.lon]); });
      else if (g.punkt) pkt.push(g.punkt);
    });
    return pkt;
  }
  function zKnoepfe(e, gross) {
    if (!ST.aktion.offen) return "";
    var leitung = ST.leitung, k = gross ? "" : " vt-k", d = ' data-zid="' + e.id + '"', h = "";
    if (!(e.eigen || leitung)) return "";
    if (e.status === "laeuft" || (leitung && e.status === "offen")) {
      h += '<button class="mp-btn' + k + '" data-tun="z-setzen"' + d + ' data-st="zugestellt">✅ Zugestellt</button>' +
        '<button class="mp-btn sekundaer' + k + '" data-tun="z-nicht"' + d + '>⚠️ Nicht zustellbar</button>';
      if (e.status === "laeuft") h += '<button class="mp-btn sekundaer' + k + '" data-tun="z-setzen"' + d + ' data-st="offen" aria-label="Freigeben">↩︎</button>';
    } else if (e.status !== "offen") {
      h += '<button class="mp-btn sekundaer' + k + '" data-tun="z-setzen"' + d + ' data-st="laeuft">↩︎ Zurück</button>';
    }
    if (leitung && e.lat == null) h += '<button class="mp-btn sekundaer' + k + '" data-tun="z-punkt"' + d + '>📌 Punkt setzen</button>';
    if (e.lat != null && MODUS === "liste") h += '<button class="mp-btn sekundaer vt-k" data-tun="z-zeigen"' + d + ' aria-label="Auf der Karte zeigen">🗺</button>';
    return h;
  }
  function zEintragHtml(e) {
    var st = e.status === "laeuft" && !e.eigen ? "in Arbeit" + (e.wer ? " bei " + esc(e.wer) : "") : ZTEXT[e.status];
    return '<div class="vt-zeintrag' + (e.status === "zugestellt" ? " erledigt" : "") + (e.status === "nicht_zustellbar" ? " rueck" : "") + '">' +
      '<span><span class="vt-hnr">' + esc(e.hausnr || "–") + "</span> <b>" + esc(e.name) + "</b>" +
      (e.ansprechpartner ? " · " + esc(e.ansprechpartner) : "") + "<br><small>" + st + (e.grund ? " – " + esc(e.grund) : "") +
      (ST.leitung && e.wer && e.status !== "laeuft" ? " · " + esc(e.wer) : "") +
      (e.lat == null ? " · ohne Kartenpunkt" : "") + "</small></span>" +
      '<span class="vt-mat-knoepfe">' + zKnoepfe(e) + "</span></div>";
  }
  function zGruppeKopf(g) {
    var teile = [];
    if (g.offen) teile.push(g.offen + " offen");
    if (g.laeuft_ich) teile.push(g.laeuft_ich + " bei dir");
    if (g.laeuft_fremd) teile.push(g.laeuft_fremd + " bei " + g.fremd_wer.map(esc).join(", "));
    if (g.erledigt) teile.push(g.erledigt + " erledigt");
    return "<b>" + esc(g.strasse) + "</b> <small>" + esc(g.ort) + "</small><br><small>" + teile.join(" · ") + "</small>";
  }
  function zUebernehmKnopf(g) {
    return (ST.aktion.offen && g.offen) ? '<button class="mp-btn vt-k" data-tun="z-uebernehmen" data-key="' + esc(g.key) + '">✋ Übernehmen (' + g.offen + ")</button>" : "";
  }
  function zeichneZustellungen() {
    var Z = ST.zustellungen, z = Z.zaehler, v = Z.verortung;
    var kopf = document.getElementById("vt-zkopf");
    if (kopf) {
      var proz = z.gesamt ? Math.round(100 * z.zugestellt / z.gesamt) : 0;
      kopf.innerHTML = (Z.was ? '<div style="font-weight:700;margin-bottom:4px">📬 ' + esc(Z.was) + "</div>" : "") +
        '<div class="vt-balken"><div style="width:' + proz + '%"></div></div>' +
        "<div><b>" + z.zugestellt + "</b> von " + z.gesamt + " zugestellt · " + z.laeuft + " in Arbeit · " + z.offen + " offen" +
        (z.nicht_zustellbar ? ' · <span style="color:var(--rot)">' + z.nicht_zustellbar + " nicht zustellbar</span>" : "") + "</div>" +
        (v && v.gesamt && v.fertig < v.gesamt ? '<div class="feld-hinweis">📍 Kartenpunkte werden noch ermittelt (' + v.fertig + " von " + v.gesamt + ").</div>" : "") +
        (z.meine_zugestellt ? '<div class="vt-lob">💪 Du hast <b>' + z.meine_zugestellt + "</b> Zustellung" + (z.meine_zugestellt === 1 ? "" : "en") + " erledigt – danke!</div>" : "");
    }
    var meine = zEintraege().filter(function (e) { return e.eigen && e.status === "laeuft"; });
    var mk = document.getElementById("vt-zmeine");
    if (mk) {
      if (meine.length && ST.aktion.offen) {
        mk.style.display = "";
        mk.innerHTML = "<b>📬 Deine Zustellungen</b> – " + meine.length + " offen bei dir" +
          '<div class="vt-reihe"><a class="mp-btn" href="' + ZUST_URL + '">▶️ Losgehen</a></div>';
      } else { mk.style.display = "none"; mk.innerHTML = ""; }
    }
    var pl = document.getElementById("vt-zplaner"); if (pl) pl.style.display = ST.aktion.offen ? "" : "none";
    var el = document.getElementById("vt-zliste");
    if (el) {
      var gr = zGruppen();
      el.innerHTML = gr.length ? gr.map(function (g) {
        return '<div class="vt-zgruppe"><div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start;flex-wrap:wrap"><span>' +
          zGruppeKopf(g) + "</span>" + zUebernehmKnopf(g) + "</div>" + g.eintraege.map(zEintragHtml).join("") + "</div>";
      }).join("") : '<p class="feld-hinweis">Für diese Aktion sind noch keine Zustellungen eingetragen.</p>';
    }
  }
  function zMarker() {
    ZSCHICHT = L.layerGroup().addTo(MAP);
    zGruppen().forEach(function (g) {
      // Straßenpunkt für alles, was ich nicht im Klartext sehen darf
      var verdeckt = g.offen + g.laeuft_fremd - g.eintraege.filter(function (e) { return e.status === "offen" || (e.status === "laeuft" && !e.eigen); }).length;
      if (g.punkt && verdeckt > 0) {
        var m = L.circleMarker(g.punkt, {radius: 10, color: "#fff", weight: 2, fillOpacity: 0.85,
          fillColor: css(g.offen ? "--text-schwach" : "--gelb", "#888")});
        m.bindPopup(function () {
          return '<div class="vt-popup">' + zGruppeKopf(g) + '<div class="vt-reihe">' + zUebernehmKnopf(g) + "</div></div>";
        });
        m.addTo(ZSCHICHT);
      }
      g.eintraege.forEach(function (e) {
        if (e.lat == null) return;
        var m2 = L.circleMarker([e.lat, e.lon], {radius: 8, color: "#fff", weight: 2, fillOpacity: 0.95,
          fillColor: css(ZFARBE[e.status] || "--blau", "#2563eb")});
        m2.bindPopup(function () { return '<div class="vt-popup">' + zEintragHtml(e) + "</div>"; });
        m2.bindTooltip(esc(e.hausnr || ""), {direction: "top", offset: [0, -6]});
        m2.addTo(ZSCHICHT);
      });
    });
  }
  function zUebernehmen(keys, ausPlan) {
    return post("/zustellungen/uebernehmen", {gruppen: keys}).then(function (r) {
      if (!r.ok) { alert(r.fehler || "Das hat nicht geklappt."); return lade(); }
      toast("✋ " + r.uebernommen + " Zustellung" + (r.uebernommen === 1 ? "" : "en") + " übernommen – Namen und Hausnummern siehst du jetzt");
      if (ausPlan) { ZPLAN = null; zPlanAnzeigen(); }
      return lade();
    });
  }
  function zSetzen(zid, status, grund, grundText) {
    return post("/zustellung/" + zid, {status: status, grund: grund || "", grund_text: grundText || ""}).then(function (r) {
      if (!r.ok) { alert(r.fehler || "Das hat nicht geklappt."); return lade(); }
      try { if (status === "zugestellt" && navigator.vibrate) navigator.vibrate(60); } catch (e) { /* egal */ }
      toast({zugestellt: "✅ Zugestellt – danke!", nicht_zustellbar: "⚠️ Gemeldet – der Vorstand prüft die Anschrift",
        offen: "↩︎ Freigegeben", laeuft: "↩︎ Zurückgesetzt"}[status] || "Gespeichert");
      PANEL_HTML = "";
      return lade();
    });
  }
  function zNicht(zid) {
    var g = (ST.zustellungen || {}).gruende || [];
    var wahl = prompt("Warum klappt die Zustellung nicht?\n" + g.map(function (x, i) { return (i + 1) + " = " + x[1]; }).join("\n") + "\n\nBitte die Zahl eingeben:");
    if (wahl === null) return;
    var key = (g[(+wahl || 0) - 1] || [])[0];
    if (!key) { alert("Bitte eine Zahl von 1 bis " + g.length + " eingeben."); return; }
    var text = "";
    if (key === "sonstiges") { text = prompt("Kurz beschreiben, was los war:") || ""; }
    zSetzen(zid, "nicht_zustellbar", key, text);
  }
  function zPunktSpeichern(zid, ll) {
    post("/zustellung/" + zid + "/punkt", {lat: ll[0], lon: ll[1]}).then(function (r) {
      if (!r.ok) alert(r.fehler || "Der Punkt konnte nicht gespeichert werden."); else toast("📌 Kartenpunkt gespeichert");
      lade();
    });
  }
  // Planer: Greedy über Straßen mit offenen Zustellungen (Weg ×1,3 bei 4 km/h + 1 min je Zustellung)
  function zRechne() {
    if (!START) { alert("Bitte zuerst den Startpunkt wählen: 📍 Mein Standort oder 🎯 auf der Karte tippen."); return; }
    var budget = +document.getElementById("vt-zzeit").value, radius = +document.getElementById("vt-zradius").value;
    var p = ["z", budget, radius, START.join()].join("|");
    VERSUCH = (p === PARAMS) ? VERSUCH + 1 : 1; PARAMS = p;
    var kand = zGruppen().filter(function (g) { return g.offen && g.punkt && hav(START, g.punkt) <= radius; });
    var wahl = [], zeit = 0, weg = 0, arbeit = 0, pos = START;
    while (kand.length) {
      var sortiert = kand.map(function (g) { return {g: g, d: hav(pos, g.punkt)}; }).sort(function (a, b) { return a.d - b.d; });
      var n = sortiert[VERSUCH <= 1 ? 0 : Math.floor(Math.random() * Math.min(3, sortiert.length))];
      kand = kand.filter(function (k) { return k !== n.g; });
      var geh = n.d * 1.3 / 4 * 60, a = n.g.offen;
      if (zeit + geh + a > budget) continue;
      zeit += geh + a; arbeit += a; weg += n.d * 1.3; pos = n.g.punkt; wahl.push(n.g);
    }
    ZPLAN = {gruppen: wahl, zeit: zeit, weg: weg, arbeit: arbeit, fussweg: false};
    zPlanAnzeigen();
  }
  function zPlanAnzeigen() {
    startText();
    var el = document.getElementById("vt-zplan-erg"); if (!el) return;
    if (ZPLAN_SCHICHT) { ZPLAN_SCHICHT.remove(); ZPLAN_SCHICHT = null; }
    if (!ZPLAN) { el.innerHTML = ""; return; }
    if (!ZPLAN.gruppen.length) { el.innerHTML = '<p class="vt-hinweis">In diesem Umkreis ist nichts mehr offen – größeren Umkreis wählen?</p>'; return; }
    var anzahl = ZPLAN.gruppen.reduce(function (a, g) { return a + g.offen; }, 0);
    el.innerHTML = "<p><b>" + ZPLAN.gruppen.length + " Straße" + (ZPLAN.gruppen.length === 1 ? "" : "n") + "</b> · " + anzahl + " Zustellung" +
      (anzahl === 1 ? "" : "en") + " · ≈ " + Math.round(ZPLAN.zeit) + " min · " + mStr(ZPLAN.weg) + (ZPLAN.fussweg ? " Fußweg" : " zu Fuß (geschätzt)") + "</p>" +
      '<ol class="vt-plan">' + ZPLAN.gruppen.map(function (g) { return "<li>" + esc(g.strasse) + " <small>" + g.offen + "×</small></li>"; }).join("") + "</ol>" +
      '<div class="vt-reihe"><button class="mp-btn" data-tun="z-plan-uebernehmen">✅ Übernehmen</button>' +
      '<button class="mp-btn sekundaer" data-tun="z-alternative">🔁 Alternative</button></div>';
    if (MAP) {
      ZPLAN_SCHICHT = L.layerGroup().addTo(MAP);
      ZPLAN.gruppen.forEach(function (g, i) {
        L.marker(g.punkt, {icon: L.divIcon({className: "vt-nr", html: String(i + 1), iconSize: [22, 22]})}).addTo(ZPLAN_SCHICHT);
      });
      MAP.fitBounds([START].concat(ZPLAN.gruppen.map(function (g) { return g.punkt; })), {padding: [24, 24]});
      var plan = ZPLAN;
      wegZeichnen([START].concat(ZPLAN.gruppen.map(function (g) { return g.punkt; })), "fuss", css("--blau", "#2563eb"), function (r) {
        if (!r || ZPLAN !== plan || plan.fussweg) return;
        plan.fussweg = true; plan.weg = r.meter / 1000; plan.zeit = r.sekunden / 60 + plan.arbeit;
        zPlanAnzeigen();
      });
    }
  }
  // Lauf-Ansicht „zustellen“: eigene Zustellungen, greedy sortiert
  function zStopps() {
    var meine = zEintraege().filter(function (e) { return e.eigen && e.status !== "offen"; });
    var ids = meine.map(function (e) { return e.id; });
    Z_REIHE = Z_REIHE.filter(function (x) { return ids.indexOf(x) !== -1; });
    var neu = meine.filter(function (e) { return Z_REIHE.indexOf(e.id) === -1; });
    var pos = GEO || (Z_REIHE.length ? zOrt(zEintrag(Z_REIHE[Z_REIHE.length - 1])) : null);
    while (neu.length) {
      var bi = 0, bd = 1e9;
      neu.forEach(function (e, i) { var p = zOrt(e); var d = (pos && p) ? hav(pos, p) : 0; if (d < bd) { bd = d; bi = i; } });
      var e = neu.splice(bi, 1)[0]; Z_REIHE.push(e.id); pos = zOrt(e) || pos;
    }
    return Z_REIHE.map(zEintrag).filter(Boolean);
  }
  function zPanel() {
    var el = document.getElementById("vt-panel"); if (!el || !ST || !ST.zustellungen) return;
    var st = zStopps(), offen = st.filter(function (e) { return e.status === "laeuft"; }), html;
    if (!st.length) {
      html = "<b>Du hast noch keine Zustellungen übernommen.</b><br><small>Übernimm in der Liste eine Straße oder lass dir einen Vorschlag machen.</small>" +
        '<div class="vt-reihe"><a class="mp-btn" href="' + LISTE_URL + '#zustellen">‹ Zur Liste</a></div>';
    } else if (!offen.length) {
      html = '<div style="text-align:center"><div style="font-size:32px">🎉</div><b style="font-size:19px">Alles zugestellt!</b><br><small>' +
        st.length + " Anschrift" + (st.length === 1 ? "" : "en") + " – danke dir!</small>" +
        '<div class="vt-reihe"><a class="mp-btn" href="' + LISTE_URL + '#zustellen">✓ Fertig</a></div></div>';
    } else {
      var akt = offen[0], hier = false;
      if (GEO) {
        var bd = 1e9;
        offen.forEach(function (e) { var p = zOrt(e); if (!p) return; var d = hav(GEO, p); if (d < bd) { bd = d; if (d <= 0.05) { akt = e; hier = true; } } });
      }
      var d = (GEO && zOrt(akt)) ? hav(GEO, zOrt(akt)) : null, fertig = st.length - offen.length;
      html = '<div class="vt-zeile-klein"><span>📬 Zustellung ' + (fertig + 1) + " von " + st.length + "</span><span>" + fertig + " ✓</span></div>" +
        '<div class="vt-balken"><div style="width:' + Math.round(100 * fertig / st.length) + '%"></div></div>' +
        '<div class="vt-wo">' + (hier ? "📍 Du bist hier:" : "➡️ Als Nächstes" + (d != null ? " · " + mStr(d) : "")) + "</div>" +
        '<div class="vt-strasse">' + esc(akt.name) + "</div><small>" + esc(akt.gruppe.strasse) + " " + esc(akt.hausnr) +
        (akt.ansprechpartner ? " · " + esc(akt.ansprechpartner) : "") + (akt.lat == null ? " · ohne genauen Kartenpunkt" : "") + "</small>" +
        '<div class="vt-reihe"><button class="mp-btn" style="flex:2" data-tun="z-setzen" data-zid="' + akt.id + '" data-st="zugestellt">✅ Zugestellt</button>' +
        '<button class="mp-btn sekundaer" data-tun="z-nicht" data-zid="' + akt.id + '">⚠️ Geht nicht</button>' +
        (offen.length > 1 ? '<button class="mp-btn sekundaer" data-tun="z-spaeter" data-zid="' + akt.id + '">⏭ Später</button>' : "") + "</div>";
    }
    if (html !== PANEL_HTML) { el.innerHTML = html; PANEL_HTML = html; }
    var lst = document.getElementById("vt-lauf-liste");
    if (lst && !lst.hidden) lst.querySelector(".vt-lauf-inhalt").innerHTML = st.length ? st.map(function (e, j) {
      var fertig = e.status !== "laeuft";
      return '<div class="vt-lauf-zeile"><span class="vt-haken' + (fertig ? " an" : "") + '" aria-hidden="true">' + (fertig ? "✓" : "") + "</span>" +
        '<div style="flex:1;min-width:0"><b>' + (j + 1) + ". " + esc(e.name) + "</b><br><small>" + esc(e.gruppe.strasse) + " " + esc(e.hausnr) +
        " · " + ZTEXT[e.status] + "</small></div>" +
        (e.lat != null ? '<button class="mp-btn sekundaer vt-k" data-tun="z-zeigen" data-zid="' + e.id + '" aria-label="Auf der Karte zeigen">🗺</button>' : "") + "</div>";
    }).join("") : "<p>Keine Zustellungen übernommen.</p>";
    // Linie durch die offenen Stopps – bewusst ohne den laufenden Standort,
    // sonst gäbe es bei jeder GPS-Bewegung eine neue Wege-Anfrage
    wegZeichnen(offen.map(zOrt).filter(Boolean).slice(0, 41), "fuss", css("--blau", "#2563eb"));
  }

  // ------------------------------------------------------ Ereignisse
  document.addEventListener("click", function (e) {
    var sum = e.target.closest(".vt-ort > summary");
    if (sum) {   // Auf-/Zuklappen von Hand merken, damit es nach dem Neuladen bleibt
      var det = sum.parentNode;
      setTimeout(function () { ORT_OFFEN[det.getAttribute("data-ort")] = det.open; }, 0);
      return;
    }
    var b = e.target.closest("[data-tun]"); if (!b || b.disabled) return;
    var tun = b.getAttribute("data-tun"), id = +b.getAttribute("data-id"), pid = +b.getAttribute("data-pid");
    if (tun === "status") {
      b.disabled = true;
      var mid = b.getAttribute("data-mat");
      statusSetzen(id, b.getAttribute("data-st"), mid ? [+mid] : null, false);
    }
    else if (tun === "status-route") { b.disabled = true; statusSetzen(id, b.getAttribute("data-st"), routeMats(), true); }
    else if (tun === "vorort-weg") { VORORT_WEG = id; vorOrt(); }
    else if (tun === "geo") standortAn(GEO_WATCH === null);
    else if (tun === "tab") {
      TAB = b.getAttribute("data-tab"); SETZEN = false; setzKnopf();
      try { history.replaceState(null, "", TAB === "flyer" ? "#" : "#" + TAB); } catch (x) { /* egal */ }
      zeichneTabs(); markerZeichnen(); einpassen(); vorOrt();
    }
    else if (tun === "start-hier") {
      if (GEO) { START = GEO.slice(); planAnzeigen(); zPlanAnzeigen(); toast("🚩 Start = dein Standort"); }
      else if (navigator.geolocation) navigator.geolocation.getCurrentPosition(function (p) {
        START = [p.coords.latitude, p.coords.longitude]; planAnzeigen(); zPlanAnzeigen(); toast("🚩 Start = dein Standort");
      }, function () { alert("Der Standort ist nicht verfügbar – tippe stattdessen auf „🎯 Auf der Karte wählen“."); });
    }
    else if (tun === "start-karte") { START_WAHL = true; toast("🎯 Tippe auf der Karte auf deinen Startpunkt"); document.getElementById("vt-karte").scrollIntoView({behavior: "smooth", block: "center"}); }
    else if (tun === "rechnen") rechneRoute();
    else if (tun === "plan-alternative") rechneRoute();
    else if (tun === "plan-uebernehmen") planUebernehmen();
    else if (tun === "spaeter") post("/route/spaeter", {strasse: id}).then(function (r) { if (!r.ok) alert(r.fehler); else toast("⏭ Kommt ans Ende der Route"); lade(); });
    else if (tun === "raus") {
      var s = strasse(id);
      if (!confirm("„" + (s ? s.name : "Straße") + "“ aus deiner Route nehmen und für andere freigeben?")) return;
      post("/route/raus", {strasse: id}).then(function (r) { if (!r.ok) alert(r.fehler); else toast("Freigegeben"); lade(); });
    }
    else if (tun === "dazu") routeDazu();
    else if (tun === "liste") { var l = document.getElementById("vt-lauf-liste"); l.hidden = !l.hidden; PANEL_HTML = ""; if (MODUS === "runde") rundePanel(); else if (MODUS === "zustellen") zPanel(); else panel(); }
    else if (tun === "folgen") { FOLGEN = !FOLGEN; b.classList.toggle("sekundaer", !FOLGEN); if (FOLGEN && GEO && MAP) MAP.panTo(GEO); }
    else if (tun === "beenden") {
      if (MODUS === "runde") { location.href = LISTE_URL + "#plakate"; return; }
      var offen = routeStopps().filter(function (y) { return !y.fertig && y.x.eigen; }).length;
      if (!confirm("Route beenden?" + (offen ? "\n\nDie " + offen + " noch offene" + (offen === 1 ? "" : "n") + " Straße" + (offen === 1 ? "" : "n") + " geben wir wieder frei, damit andere sie übernehmen können." : ""))) return;
      post("/route/beenden", {freigeben: true}).then(function () { if (MODUS === "laufen") location.href = LISTE_URL; else { toast("Route beendet"); lade(); } });
    }
    else if (tun === "abschliessen") {
      post("/route/beenden", {freigeben: false}).then(function () {
        alert("🎉 Danke! Du hast jetzt " + ((ST.fortschritt.meine_strassen || 0)) + " Straßen verteilt. Ehrenamt wirkt!");
        location.href = LISTE_URL;
      });
    }
    // Plakate
    else if (tun === "p-setzen") { SETZEN = !SETZEN; setzKnopf(); if (SETZEN) document.getElementById("vt-karte").scrollIntoView({behavior: "smooth", block: "center"}); }
    else if (tun === "p-hier") {
      var nimm = function (pos) { plakatForm(pos, true); };
      if (GEO) nimm(GEO.slice());
      else if (navigator.geolocation) navigator.geolocation.getCurrentPosition(function (p) { nimm([p.coords.latitude, p.coords.longitude]); },
        function () { alert("Der Standort ist nicht verfügbar – nutze „➕ Plakat setzen“ und tippe auf die Karte."); }, {enableHighAccuracy: true, timeout: 15000});
    }
    else if (tun === "p-speichern") plakatSpeichern(b);
    else if (tun === "p-abbrechen") { var f = document.getElementById("vt-pform"); f.style.display = "none"; f.innerHTML = ""; }
    else if (tun === "p-melden") { b.disabled = true; plakatMelden(pid, b.getAttribute("data-m")).then(function () { b.disabled = false; }); }
    else if (tun === "p-notiz") plakatMelden(pid, "notiz");
    else if (tun === "p-loeschen") {
      var pl = plakat(pid);
      if (!confirm((pl ? pl.nr : "Plakat") + " löschen?")) return;
      post("/plakat/" + pid + "/loeschen", {}).then(function (r) { if (!r.ok) alert(r.fehler); else toast("Gelöscht"); lade(); });
    }
    else if (tun === "p-zeigen") {
      var pz = plakat(pid); if (!pz || !MAP) return;
      var ll = document.getElementById("vt-lauf-liste"); if (ll) ll.hidden = true;
      FOLGEN = false; MAP.setView([pz.lat, pz.lon], 18);
      document.getElementById("vt-karte").scrollIntoView({behavior: "smooth", block: "center"});
    }
    // Zustellungen
    else if (tun === "z-uebernehmen") { b.disabled = true; zUebernehmen([b.getAttribute("data-key")]); }
    else if (tun === "z-setzen") { b.disabled = true; zSetzen(+b.getAttribute("data-zid"), b.getAttribute("data-st")); }
    else if (tun === "z-nicht") zNicht(+b.getAttribute("data-zid"));
    else if (tun === "z-spaeter") { var zs = +b.getAttribute("data-zid"); Z_REIHE = Z_REIHE.filter(function (x) { return x !== zs; }).concat([zs]); PANEL_HTML = ""; zPanel(); }
    else if (tun === "z-punkt") { PUNKT_ZID = +b.getAttribute("data-zid"); toast("📌 Tippe auf der Karte auf die Haustür"); document.getElementById("vt-karte").scrollIntoView({behavior: "smooth", block: "center"}); }
    else if (tun === "z-zeigen") {
      var ze = zEintrag(+b.getAttribute("data-zid")); if (!ze || !MAP || ze.lat == null) return;
      var zl = document.getElementById("vt-lauf-liste"); if (zl) zl.hidden = true;
      FOLGEN = false; MAP.setView([ze.lat, ze.lon], 18);
      document.getElementById("vt-karte").scrollIntoView({behavior: "smooth", block: "center"});
    }
    else if (tun === "z-rechnen" || tun === "z-alternative") zRechne();
    else if (tun === "z-plan-uebernehmen") { if (ZPLAN && ZPLAN.gruppen.length) zUebernehmen(ZPLAN.gruppen.map(function (g) { return g.key; }), true); }
    else if (tun === "r-start") rundeStarten(document.getElementById("vt-r-art").value, document.getElementById("vt-r-profil").value);
    else if (tun === "r-weiter") { ERLEDIGT[pid] = Date.now(); merke(); NAH = null; PANEL_HTML = ""; rundePanel(); }
    else if (tun === "r-neu") {
      RUNDE = null; NAH = null; PANEL_HTML = ""; WEG_SCHLUESSEL = "";
      if (WEG_SCHICHT) { WEG_SCHICHT.remove(); WEG_SCHICHT = null; }
      try { sessionStorage.removeItem(K_RUNDE); } catch (x) { /* egal */ }
      rundePanel();
    }
  });
  document.addEventListener("change", function (e) {
    if (e.target.matches("input[data-foto], input[data-pfoto]")) fotoHoch(e.target);
    if (e.target.matches("input[data-matwahl]")) { WAHL[+e.target.getAttribute("data-matwahl")] = e.target.checked; PLAN = null; planAnzeigen(); }
  });
  var suche = document.getElementById("vt-suche"); if (suche) suche.addEventListener("input", filtern);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") { lade(); if (MODUS !== "liste") wachHalten(true); }
  });

  function zeichneAlles() {
    if (MODUS === "laufen") {
      karteAufbauen(); markerZeichnen();
      var st = routeStopps(), pkt = st.map(function (y) { return koord(y.s); }).filter(Boolean);
      if (!zeichneAlles._eingepasst && pkt.length && MAP) { MAP.fitBounds(pkt, {padding: [30, 30]}); zeichneAlles._eingepasst = true; }
      panel();
    } else if (MODUS === "runde" || MODUS === "zustellen") {
      var neu = !MAP;
      karteAufbauen(); markerZeichnen(); if (neu) einpassen();
      if (MODUS === "runde") rundePanel(); else zPanel();
    } else zeichneListe();
  }

  if (location.hash === "#plakate") TAB = "plakate";
  if (location.hash === "#zustellen") TAB = "zustellen";
  if (MODUS === "laufen" || MODUS === "zustellen") { standortAn(true); wachHalten(true); }
  if (MODUS === "runde") {
    try { RUNDE = JSON.parse(sessionStorage.getItem(K_RUNDE) || "null"); } catch (e) { RUNDE = null; }
    if (RUNDE) { standortAn(true); wachHalten(true); }
  }
  lade();
})();
