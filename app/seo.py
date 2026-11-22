"""Suchmaschinen: Termine eines Vereins für Google & Co. auffindbar machen.

- indexierbar(): Darf dieser Verein überhaupt in die Suche? Nur mit Gastansicht
  (freunde_registrierung), eigenem Schalter seo_index (Standard an) und NIE in
  der öffentlichen Demo (VV_DEMO_LOGIN).
- termin_oeffentlich(): Nur Termine ohne „Nur für Mitglieder“ und ohne Kategorie
  „nicht-öffentlich“ kommen in Sitemap, strukturierte Daten und sprechende URLs –
  dieselbe Regel wie app/termin_maske.py.
- slug()/termin_url(): sprechende Adressen /portal/termin/112-offene-tuer
- event_jsonld(): schema.org/Event für Googles Veranstaltungs-Suche
  (Zeitzone Europe/Berlin, Ende, Ort mit Anschrift, Veranstalter:in, Preis).
16.11.2026.
"""
import re
import unicodedata
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # noqa: BLE001 – ohne Zeitzonen-Daten: nur Datum/Uhrzeit ohne Versatz
    _BERLIN = None

from flask import url_for

from . import termin_maske


def _get(obj, k, standard=None):
    try:
        v = obj[k]
    except (KeyError, IndexError, TypeError):
        return standard
    return standard if v is None else v


def indexierbar(einst):
    from .demo import ist_demo
    if ist_demo() or not einst:
        return False
    return bool(_get(einst, "freunde_registrierung", 0)) and bool(_get(einst, "seo_index", 1))


def termin_oeffentlich(t):
    return not (termin_maske.ist_intern(t) or termin_maske.ist_nicht_oeffentlich(t) or _get(t, "verborgen"))


_UMLAUTE = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"}


def slug(text, laenge=60):
    s = "".join(_UMLAUTE.get(ch, ch) for ch in str(text or ""))
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > laenge:
        s = s[:laenge].rsplit("-", 1)[0] or s[:laenge]
    return s or "termin"


def termin_url(t, extern=False):
    """Sprechende Adresse für öffentliche Termine, sonst die neutrale ID-Adresse
    (bei verborgenen Terminen darf der Titel nie in der URL stehen)."""
    if termin_oeffentlich(t) and _get(t, "titel"):
        return url_for("portal.termin", tid=t["id"], slug=slug(t["titel"]), _external=extern)
    return url_for("portal.termin", tid=t["id"], _external=extern)


def _zeit(datum, uhrzeit):
    if not datum:
        return None
    if not uhrzeit:
        return datum
    try:
        dt = datetime.strptime(f"{datum} {uhrzeit[:5]}", "%Y-%m-%d %H:%M")
    except ValueError:
        return datum
    if _BERLIN:
        dt = dt.replace(tzinfo=_BERLIN)
    return dt.isoformat()


def _anschrift(text):
    """„Schulstraße 6, 17192 Waren (Müritz)“ → PostalAddress (sonst None)."""
    m = re.search(r"([^,]*?\d+\s*[a-zA-Z]?(?:\s*[-–]\s*\d+)?)\s*,?\s*(\d{5})\s+([^,]+)$", str(text or "").strip())
    if not m:
        return None
    return {"@type": "PostalAddress", "streetAddress": m.group(1).strip(" ,"), "postalCode": m.group(2),
            "addressLocality": m.group(3).strip(), "addressCountry": "DE"}


def ort_daten(t, einst):
    """schema.org/Place: eigener Ort des Termins, sonst der Veranstaltungsort des Vereins."""
    ort = str(_get(t, "ort", "") or "").strip()
    haus = str(_get(einst, "werkhaus_name", "") or "").strip()
    haus_adr = str(_get(einst, "werkhaus_adresse", "") or "").strip()
    if not ort:
        ort = haus
    if not ort:
        return None
    name = ort.split(",")[0].strip() or ort
    adresse = _anschrift(ort)
    if not adresse and haus and name.casefold() == haus.casefold():
        adresse = _anschrift(haus_adr)
    if not adresse:
        ver_ort = " ".join(x for x in (_get(einst, "plz", ""), _get(einst, "ort", "")) if x)
        adresse = f"{ort}, {ver_ort}" if ver_ort and ver_ort not in ort else ort
    return {"@type": "Place", "name": name, "address": adresse}


def beschreibung(t, laenge=300):
    import html as _html
    text = str(_get(t, "beschreibung", "") or _get(t, "langbeschreibung", "") or "").strip()
    text = _html.unescape(re.sub(r"<[^>]+>", " ", text))     # importierte Texte können HTML tragen
    text = re.sub(r"\s+", " ", text).strip()
    return text[:laenge].rsplit(" ", 1)[0] + " …" if len(text) > laenge else text


def status(t):
    """eventStatus für Google – immer gesetzt (fehlt er, meldet die Search Console es)."""
    return ("https://schema.org/EventCancelled" if _get(t, "abgesagt")
            else "https://schema.org/EventScheduled")


def veranstalter(einst):
    """organizer: der Verein (Name, Website) – None ohne Vereinsnamen."""
    verein = str(_get(einst, "vereinsname", "") or "").strip()
    if not verein:
        return None
    org = {"@type": "Organization", "name": verein}
    web = str(_get(einst, "website", "") or "").strip()
    if web:
        org["url"] = web if web.startswith("http") else "https://" + web
    return org


# Wörter, an denen eine Gruppe statt einer Einzelperson zu erkennen ist
_GRUPPE = re.compile(r"\b(band|chor|ensemble|duo|trio|quartett|quintett|orchester|theater|"
                     r"gruppe|team|verein|e\.\s?v\.|kapelle|combo|crew|compagnie|kompanie|"
                     r"gmbh|schule|kita|initiative|bündnis|club|klub)\b", re.I)


def mitwirkende(t):
    """performer aus termine.mitwirkende: kommagetrennt, je Eintrag Person oder
    Gruppe (PerformingGroup, wenn ein Gruppen-Wort darin steht). None ohne Angabe."""
    roh = str(_get(t, "mitwirkende", "") or "")
    namen = [n.strip() for n in re.split(r"[,;\n]", roh) if n.strip()][:10]
    if not namen:
        return None
    liste = [{"@type": "PerformingGroup" if _GRUPPE.search(n) else "Person", "name": n[:100]}
             for n in namen]
    return liste[0] if len(liste) == 1 else liste


def angebot_ab(t):
    """offers.validFrom: seit wann man sich anmelden/Karten bekommen kann. Ein eigenes
    Feld dafür gibt es nicht – der Termin ist ab seiner Anlage veröffentlicht."""
    roh = str(_get(t, "erstellt_am", "") or "").strip()
    return _zeit(roh[:10], roh[11:16]) if len(roh) >= 10 else None


def event_jsonld(t, einst, url=None, frei=None):
    """schema.org/Event als dict – None, wenn der Termin nicht öffentlich ist oder
    ein Pflichtfeld (Name, Beginn, Ort) fehlt."""
    if not termin_oeffentlich(t) or not _get(t, "titel") or not _get(t, "datum"):
        return None
    ort = ort_daten(t, einst)
    if not ort:
        return None
    ev = {"@context": "https://schema.org", "@type": "Event", "name": t["titel"],
          "startDate": _zeit(t["datum"], _get(t, "uhrzeit", "")),
          "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
          "eventStatus": status(t),
          "location": ort, "url": url or termin_url(t, extern=True)}
    if _get(t, "uhrzeit_bis") and _get(t, "uhrzeit"):
        ende = _get(t, "datum_bis", "") or t["datum"]
        ev["endDate"] = _zeit(ende, t["uhrzeit_bis"])
    if _get(t, "einlass_ab"):
        ev["doorTime"] = _zeit(t["datum"], t["einlass_ab"])
    text = beschreibung(t)
    if text:
        ev["description"] = text
    bild = bild_url(t)
    if bild:
        ev["image"] = [bild]
    org = veranstalter(einst)
    if org:
        ev["organizer"] = org
    leute = mitwirkende(t)
    if leute:
        ev["performer"] = leute
    preis = _get(t, "preis")
    if _get(t, "anmeldung_aktiv") and _get(t, "anmeldung_token"):
        try:
            anmelde_url = url_for("veranstaltungen.oeffentlich", token=t["anmeldung_token"], _external=True)
        except Exception:  # noqa: BLE001
            anmelde_url = ev["url"]
        ev["offers"] = {"@type": "Offer", "priceCurrency": "EUR", "price": f"{float(preis or 0):.2f}",
                        "availability": ("https://schema.org/SoldOut" if frei == 0
                                         else "https://schema.org/InStock"),
                        "url": anmelde_url}
    elif _preis_zahl(preis) > 0:
        # Ohne Anmeldung nur einen echten Preis melden: termine.preis steht standardmäßig
        # auf 0 – sonst erschiene jeder Termin ohne Preisangabe als „kostenlos“.
        ev["offers"] = {"@type": "Offer", "priceCurrency": "EUR", "price": f"{_preis_zahl(preis):.2f}",
                        "url": ev["url"]}
    elif _get(t, "eintritt_art") == "frei":
        # Ausdrücklich „Eintritt frei“ angehakt (Feld eintritt_art, 19.11.2026) – erst damit
        # ist 0 € eine echte Aussage und darf als kostenloses Angebot an Google gehen.
        ev["offers"] = {"@type": "Offer", "priceCurrency": "EUR", "price": "0.00",
                        "availability": "https://schema.org/InStock", "url": ev["url"]}
    if "offers" in ev:
        ab = angebot_ab(t)
        if ab:
            ev["offers"]["validFrom"] = ab
    if "offers" in ev and ev["offers"]["price"] == "0.00":
        ev["isAccessibleForFree"] = True    # Anmeldung mit 0 € oder ausdrücklich „Eintritt frei“
    return ev


def bild_url(t):
    """Bild für Google und Link-Vorschauen: eigenes Veranstaltungsbild, sonst das
    Standardbild der Kategorie (kat_bild + kategorie_id in der Zeile), sonst None."""
    try:
        if _get(t, "bild"):
            return url_for("termine.bild", tid=t["id"], v=t["bild"], _external=True)
        if _get(t, "kat_bild") and _get(t, "kategorie_id"):
            return url_for("portal.kategorie_bild", kid=t["kategorie_id"], v=t["kat_bild"], _external=True)
    except Exception:  # noqa: BLE001 – ohne Bild ist besser als ein Seitenfehler
        return None
    return None


def _preis_zahl(preis):
    try:
        return float(preis or 0)
    except (TypeError, ValueError):
        return 0.0


def meta_beschreibung(t, einst):
    """Kurztext für <meta name="description">: Datum, Uhrzeit, Ort, Anfang der Beschreibung."""
    teile = []
    d = _get(t, "datum", "")
    if d:
        teile.append(f"{d[8:10]}.{d[5:7]}.{d[0:4]}" + (f", {t['uhrzeit']} Uhr" if _get(t, "uhrzeit") else ""))
    ort = _get(t, "ort", "") or _get(einst, "werkhaus_name", "")
    if ort:
        teile.append(str(ort))
    kopf = " · ".join(teile)
    text = beschreibung(t, 150) if termin_oeffentlich(t) else ""
    return (kopf + (" – " + text if text else ""))[:300]


# ------------------------------------------------- robots.txt / Sitemap ---
# Seiten, die Suchmaschinen indexieren dürfen (alles andere: X-Robots-Tag noindex)
INDEX_SEITEN = {"portal.home", "portal.termine", "portal.termin", "portal.kino", "portal.ueber",
                "portal.adventskalender"}


def _robots_txt(einst, sitemap_url):
    if not indexierbar(einst):
        return "User-agent: *\nDisallow: /\n"
    return "\n".join([
        "User-agent: *",
        "Allow: /portal/$",
        "Allow: /portal/termine$",
        "Allow: /portal/termin/",
        "Allow: /portal/kino$",
        "Allow: /portal/ueber$",
        "Allow: /portal/adventskalender$",
        "Allow: /adventskalender$",
        "Allow: /termine/",            # Veranstaltungsbilder (Bildersuche, Event-Vorschau)
        "Allow: /portal/kategorie-bild/",  # Standardbild je Kategorie (Ersatz, wenn ein Termin kein Bild hat)
        "Allow: /static/",
        "Allow: /app-icon",
        "Allow: /portal/logo$",        # Vereinslogo + Manifest gehören zur Darstellung der Seite
        "Allow: /portal/titelbild",    # Titelbild der Terminseite (Website-Stil; Adresse trägt ?v=…)
        "Allow: /manifest.webmanifest$",
        "Allow: /favicon.ico$",
        "Allow: /sitemap.xml$",        # sonst darf Google die eigene Sitemap nicht lesen („Konnte nicht abgerufen werden“)
        "Disallow: /portal/termin/*.ics$",
        "Disallow: /portal/adventskalender.ics",
        "Disallow: /",
        f"Sitemap: {sitemap_url}",
        ""])


def _sitemap_xml(conn, vid, einst):
    from datetime import date, timedelta
    from xml.sax.saxutils import escape
    heute = date.today()
    eintraege = [(url_for("portal.home", _external=True), None, "daily"),
                 (url_for("portal.termine", _external=True), None, "daily")]
    if not indexierbar(einst):
        eintraege = []
    else:
        rows = conn.execute(
            "SELECT t.*, k.name AS kategorie FROM termine t LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
            "WHERE t.verein_id=? AND t.aktiv=1 AND COALESCE(t.abgesagt,0)=0 AND t.datum>=? AND t.datum<=? "
            "ORDER BY t.datum LIMIT 2000",
            (vid, heute.isoformat(), (heute + timedelta(days=400)).isoformat())).fetchall()
        kino = False
        for t in rows:
            if not termin_oeffentlich(t):
                continue
            kino = kino or "kino" in str(t["kategorie"] or "").lower()
            geaendert = str(_get(t, "geaendert_am", "") or _get(t, "erstellt_am", "") or "")[:10]
            eintraege.append((termin_url(t, extern=True), geaendert or None, "weekly"))
        if kino:
            eintraege.append((url_for("portal.kino", _external=True), None, "daily"))
        if _get(einst, "modul_adventskalender"):
            eintraege.append((url_for("portal.adventskalender", _external=True), None, "daily"))
        eintraege.append((url_for("portal.ueber", _external=True), None, "monthly"))
    zeilen = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod, freq in eintraege:
        zeilen.append("  <url><loc>" + escape(loc) + "</loc>"
                      + (f"<lastmod>{escape(lastmod)}</lastmod>" if lastmod else "")
                      + f"<changefreq>{freq}</changefreq></url>")
    zeilen.append("</urlset>")
    return "\n".join(zeilen) + "\n"


# ------------------------------------------------ Termin-Feed (JSON) -----
# Für die Vereins-Website (WordPress-Zusatz „WerkHaus-App-Termine“, 17.11.2026):
# öffentliche, kommende Termine je Kategorie als JSON. Dieselbe Auswahlregel wie
# Sitemap/Event-Daten (termin_oeffentlich), nur mit Gastansicht, nie in der Demo.
FEED_TAGE_MAX = 400
FEED_ANZAHL_MAX = 300


def feed_erlaubt(einst):
    from .demo import ist_demo
    return bool(einst) and not ist_demo() and bool(_get(einst, "freunde_registrierung", 0))


def _klartext(html_text):
    """HTML → Klartext mit Absätzen (für die Beschreibung auf der Website)."""
    import html as _html
    s = str(html_text or "")
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s).replace("\r", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", s)
    return s.strip()


def _anschrift_text(ort):
    adr = ort.get("address") if ort else None
    if isinstance(adr, dict):
        return f"{adr['streetAddress']}, {adr['postalCode']} {adr['addressLocality']}"
    return str(adr or "")


def termine_feed(conn, vid, einst, kat_ids=None, tage=365, nur_highlights=False):
    from . import termin_extras
    from datetime import date, timedelta
    heute = date.today()
    tage = max(1, min(int(tage or 365), FEED_TAGE_MAX))
    sql = ("SELECT t.*, k.name AS kategorie FROM termine t LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
           "WHERE t.verein_id=? AND t.aktiv=1 AND t.datum>=? AND t.datum<=?")
    params = [vid, heute.isoformat(), (heute + timedelta(days=tage)).isoformat()]
    if kat_ids:
        sql += " AND t.kategorie_id IN (" + ",".join("?" * len(kat_ids)) + ")"
        params += list(kat_ids)
    if nur_highlights:
        sql += " AND COALESCE(t.highlight,0)=1 AND COALESCE(t.abgesagt,0)=0"
    sql += " ORDER BY t.datum, t.uhrzeit LIMIT ?"
    params.append(FEED_ANZAHL_MAX)
    termine = []
    for t in conn.execute(sql, params).fetchall():
        if not termin_oeffentlich(t):
            continue
        ort = ort_daten(t, einst)
        anmeldung = None
        if _get(t, "anmeldung_aktiv") and _get(t, "anmeldung_token"):
            try:
                anmeldung = url_for("veranstaltungen.oeffentlich", token=t["anmeldung_token"], _external=True)
            except Exception:  # noqa: BLE001
                anmeldung = None
        preis = _get(t, "preis")
        termine.append({
            "id": t["id"],
            "titel": t["titel"],
            "datum": t["datum"],
            "uhrzeit": (_get(t, "uhrzeit", "") or "")[:5],
            "uhrzeit_bis": (_get(t, "uhrzeit_bis", "") or "")[:5],
            "ort": (ort or {}).get("name", "") if ort else "",
            "adresse": _anschrift_text(ort),
            "kurzbeschreibung": _klartext(_get(t, "beschreibung", "")),
            "beschreibung": _klartext(_get(t, "langbeschreibung", "") or _get(t, "beschreibung", "")),
            "preis": _preis_zahl(preis) if preis is not None else None,
            "abgesagt": bool(_get(t, "abgesagt")),
            "kategorie_id": _get(t, "kategorie_id"),
            "kategorie": _get(t, "kategorie", "") or "",
            "bild": (url_for("termine.bild", tid=t["id"], v=t["bild"], _external=True) if _get(t, "bild") else None),
            "trailer_url": _get(t, "trailer_url", "") or "",
            "mitwirkende": str(_get(t, "mitwirkende", "") or "").strip(),
            "highlight": bool(_get(t, "highlight")),
            "highlight_text": str(_get(t, "highlight_text", "") or "").strip(),
            "eintritt_art": str(_get(t, "eintritt_art", "") or ""),
            "zielgruppen": [n for _, n in termin_extras.zielgruppen_liste(_get(t, "zielgruppen", ""))],
            "url": termin_url(t, extern=True),
            "anmeldung_url": anmeldung,
            "geaendert": str(_get(t, "geaendert_am", "") or _get(t, "erstellt_am", "") or ""),
        })
    if nur_highlights:
        # Terminreihe mit Highlight-Haken nur einmal (nächstes Vorkommen) an die Website
        gesehen, einmal = set(), []
        for e in termine:
            schluessel = re.sub(r"\s+", " ", e["titel"]).strip().casefold()
            if schluessel not in gesehen:
                gesehen.add(schluessel)
                einmal.append(e)
        termine = einmal
    return {"verein": _get(einst, "vereinsname", "") or "", "stand": datetime.now().isoformat(timespec="seconds"),
            "anzahl": len(termine), "termine": termine}


def einrichten(app):
    """Registriert /robots.txt, /sitemap.xml, den Termin-Feed und die noindex-Kennzeichnung."""
    from flask import Response, g, request

    from . import db

    @app.route("/portal/termine.json", endpoint="termine_json")
    def termine_json():
        import json as _json
        einst = db.get_einstellungen()
        if not feed_erlaubt(einst):
            return Response('{"fehler": "nicht verfügbar"}', status=404, mimetype="application/json")
        kats = []
        for teil in (request.args.get("kat", "") or "").split(","):
            teil = teil.strip()
            if teil.isdigit():
                kats.append(int(teil))
        try:
            tage = int(request.args.get("tage", "365"))
        except ValueError:
            tage = 365
        conn = db.get_db()
        try:
            daten = termine_feed(conn, db.aktiver_verein_id(), einst, kats[:20], tage,
                                 nur_highlights=request.args.get("highlight") == "1")
        finally:
            conn.close()
        r = Response(_json.dumps(daten, ensure_ascii=False), mimetype="application/json")
        r.headers["Cache-Control"] = "public, max-age=300"
        r.headers["X-Robots-Tag"] = "noindex"
        return r

    @app.route("/robots.txt", endpoint="robots_txt")
    def robots_txt():
        text = _robots_txt(db.get_einstellungen(), url_for("sitemap_xml", _external=True))
        r = Response(text, mimetype="text/plain")
        r.headers["Cache-Control"] = "public, max-age=3600"
        return r

    @app.route("/sitemap.xml", endpoint="sitemap_xml")
    def sitemap_xml():
        conn = db.get_db()
        try:
            xml = _sitemap_xml(conn, db.aktiver_verein_id(), db.get_einstellungen())
        finally:
            conn.close()
        r = Response(xml, mimetype="application/xml")
        r.headers["Cache-Control"] = "public, max-age=3600"
        return r

    @app.after_request
    def _seo_robots_kopf(antwort):
        if antwort.mimetype != "text/html" or "X-Robots-Tag" in antwort.headers:
            return antwort
        try:
            einst = db.get_einstellungen()
        except Exception:  # noqa: BLE001
            return antwort
        ep = request.endpoint or ""
        if not indexierbar(einst):
            antwort.headers["X-Robots-Tag"] = "noindex, nofollow"
        elif ep not in INDEX_SEITEN or getattr(g, "seo_noindex", False):
            antwort.headers["X-Robots-Tag"] = "noindex"
        elif ep in ("portal.termine", "portal.home") and request.args:
            antwort.headers["X-Robots-Tag"] = "noindex, follow"   # gefilterte Listen: nur folgen
        return antwort


def termin_link(t):
    """Für Vorlagen (Jinja-Global): sprechender Link nur, wenn die Zeile alles zur
    Entscheidung mitbringt (Haken + Kategorie-Name) – sonst neutral per ID, damit
    ein nicht-öffentlicher Titel nie in einer geteilten URL landet."""
    try:
        t["nur_mitglieder"], t["kategorie"]
    except (KeyError, IndexError, TypeError):
        return url_for("portal.termin", tid=t["id"])
    return termin_url(t)
