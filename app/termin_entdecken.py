"""Terminseite: mehr entdecken (21.11.2026).

Bausteine der Terminseite (portal/termin_detail.html):
- C  „Weitere Termine dieser Reihe“ (gleiche serie_id, sonst gleicher Titel) und
     „Das könnte dich auch interessieren“ (gleiche Kategorie bzw. Kategorie-Gruppe,
     sonst gleiche Zielgruppe)
- F  „So war's“: Fotos aus dem Fotoarchiv – NUR mit Veröffentlichungs- UND
     Social-Media-Freigabe des Vorstands (social_ok wird bei einem Nein/Widerruf
     einer teilnehmenden Person automatisch zurückgenommen, siehe db.py-Trigger)
- G  Wetter für Draußen-Termine in den nächsten 7 Tagen (Open-Meteo, ohne Schlüssel)

Gäste sehen in C nur öffentliche Termine: interne/nicht-öffentliche und reine
Mitgliedertermine fallen für sie ganz heraus (kein „Raum belegt“ als Vorschlag).
"""
import time
from datetime import date, datetime, timedelta

from . import termin_maske

_SPALTEN = ("SELECT t.id, t.titel, t.datum, t.uhrzeit, t.uhrzeit_bis, t.ort, t.anmeldung_aktiv, t.anmeldeschluss, "
            "t.anmeldung_token, t.abgesagt, t.nur_mitglieder, t.bild, t.kategorie_id, t.serie_id, t.zielgruppen, "
            "k.bild AS kat_bild, k.farbe, k.name AS kategorie, k.gruppe AS kat_gruppe "
            "FROM termine t LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id ")


def _sichtbar(rows, gast, fuer_gast):
    """Für Gäste nur öffentliche Termine (anonymisierte fallen ganz weg)."""
    aus = []
    for r in rows:
        d = dict(r)
        if gast:
            if termin_maske.ist_intern(d) or termin_maske.ist_nicht_oeffentlich(d) or d.get("nur_mitglieder"):
                continue
            d = dict(fuer_gast(r))
            if d.get("verborgen"):
                continue
        aus.append(d)
    return aus


def reihe(conn, t, vid, gast, fuer_gast, anzahl=4):
    """Nächste Termine derselben Reihe: gleiche serie_id, sonst gleicher Titel."""
    heute = date.today().isoformat()
    if t["serie_id"]:
        rows = conn.execute(_SPALTEN + "WHERE t.verein_id=? AND t.aktiv=1 AND t.serie_id=? AND t.id!=? "
                            "AND t.datum>=? AND COALESCE(t.abgesagt,0)=0 ORDER BY t.datum, t.uhrzeit LIMIT 12",
                            (vid, t["serie_id"], t["id"], heute)).fetchall()
    else:
        rows = conn.execute(_SPALTEN + "WHERE t.verein_id=? AND t.aktiv=1 AND lower(trim(t.titel))=lower(trim(?)) "
                            "AND t.id!=? AND t.datum>=? AND COALESCE(t.abgesagt,0)=0 "
                            "ORDER BY t.datum, t.uhrzeit LIMIT 12",
                            (vid, t["titel"] or "", t["id"], heute)).fetchall()
    return _sichtbar(rows, gast, fuer_gast)[:anzahl]


def aehnliche(conn, t, vid, gast, fuer_gast, ausser=(), anzahl=3):
    """Bis zu drei kommende Termine gleicher Kategorie/Gruppe, sonst gleicher Zielgruppe.
    Termine derselben Reihe bzw. mit gleichem Titel werden nicht vorgeschlagen."""
    heute = date.today().isoformat()
    titel = (t["titel"] or "").strip().lower()
    raus = set(ausser) | {t["id"]}
    kandidaten = []
    gruppe = ""
    if t["kategorie_id"]:
        g = conn.execute("SELECT COALESCE(gruppe,'') AS gruppe FROM termin_kategorien WHERE id=?",
                         (t["kategorie_id"],)).fetchone()
        gruppe = (g["gruppe"] if g else "").strip()
        if gruppe:
            rows = conn.execute(_SPALTEN + "WHERE t.verein_id=? AND t.aktiv=1 AND t.datum>=? "
                                "AND COALESCE(t.abgesagt,0)=0 AND (t.kategorie_id=? OR lower(trim(k.gruppe))=lower(?)) "
                                "ORDER BY t.datum, t.uhrzeit LIMIT 60", (vid, heute, t["kategorie_id"], gruppe)).fetchall()
        else:
            rows = conn.execute(_SPALTEN + "WHERE t.verein_id=? AND t.aktiv=1 AND t.datum>=? "
                                "AND COALESCE(t.abgesagt,0)=0 AND t.kategorie_id=? "
                                "ORDER BY t.datum, t.uhrzeit LIMIT 60", (vid, heute, t["kategorie_id"])).fetchall()
        kandidaten += list(rows)
    ziele = {z for z in (t["zielgruppen"] or "").split(",") if z.strip()}
    if ziele:
        for r in conn.execute(_SPALTEN + "WHERE t.verein_id=? AND t.aktiv=1 AND t.datum>=? "
                              "AND COALESCE(t.abgesagt,0)=0 AND COALESCE(t.zielgruppen,'')!='' "
                              "ORDER BY t.datum, t.uhrzeit LIMIT 80", (vid, heute)).fetchall():
            if ziele & {z for z in (r["zielgruppen"] or "").split(",") if z.strip()}:
                kandidaten.append(r)
    aus, titel_gesehen = [], {titel}
    for d in _sichtbar(kandidaten, gast, fuer_gast):
        tt = (d.get("titel") or "").strip().lower()
        if d["id"] in raus or tt in titel_gesehen or (t["serie_id"] and d.get("serie_id") == t["serie_id"]):
            continue
        titel_gesehen.add(tt)   # je Reihe/Titel nur EIN Vorschlag
        raus.add(d["id"])
        aus.append(d)
        if len(aus) >= anzahl:
            break
    return aus


def fotos_oeffentlich(conn, tid, vid, anzahl=12):
    """„So war's“: nur Fotos mit Veröffentlichungs- UND Social-Media-Freigabe."""
    return conn.execute(
        "SELECT id, dateiname, beschreibung FROM galerie_fotos WHERE termin_id=? AND verein_id=? "
        "AND COALESCE(freigabe_ok,0)=1 AND COALESCE(social_ok,0)=1 ORDER BY id LIMIT ?",
        (tid, vid, anzahl)).fetchall()


def foto_oeffentlich_erlaubt(conn, fid, vid):
    """Zeile des Fotos, wenn es öffentlich gezeigt werden darf (Freigaben + öffentlicher Termin)."""
    f = conn.execute(
        "SELECT f.id, f.dateiname, f.termin_id FROM galerie_fotos f JOIN termine t ON t.id=f.termin_id "
        "WHERE f.id=? AND f.verein_id=? AND t.verein_id=? AND t.aktiv=1 AND COALESCE(f.freigabe_ok,0)=1 "
        "AND COALESCE(f.social_ok,0)=1", (fid, vid, vid)).fetchone()
    if not f:
        return None
    t = conn.execute("SELECT t.*, k.name AS kategorie FROM termine t LEFT JOIN termin_kategorien k "
                     "ON k.id=t.kategorie_id WHERE t.id=?", (f["termin_id"],)).fetchone()
    td = dict(t) if t else {}
    if not t or termin_maske.ist_intern(td) or termin_maske.ist_nicht_oeffentlich(td) or td.get("nur_mitglieder"):
        return None
    return f


# ----------------------------------------------------------------- Wetter ---
_GEO_CACHE = {}      # ort -> (zeitstempel, (lat, lon) oder None)
_WETTER_CACHE = {}   # (lat, lon, datum) -> (zeitstempel, stundenwerte oder None)


def _hole_json(url, params):
    """Eine Anfrage an Open-Meteo (Tests ersetzen diese Funktion)."""
    import requests
    return requests.get(url, params=params, timeout=3).json()


def _koordinaten(ort):
    eintrag = _GEO_CACHE.get(ort)
    if eintrag and time.time() - eintrag[0] < 86400:
        return eintrag[1]
    punkt = None
    try:
        geo = _hole_json("https://geocoding-api.open-meteo.com/v1/search",
                         {"name": ort, "count": 1, "language": "de"})
        treffer = (geo.get("results") or [None])[0]
        if treffer:
            punkt = (round(float(treffer["latitude"]), 3), round(float(treffer["longitude"]), 3))
    except Exception:  # noqa: BLE001 – offline/Dienst weg: einfach kein Wetter
        punkt = None
    _GEO_CACHE[ort] = (time.time(), punkt)
    return punkt


def wetter(einst, termin, heute=None):
    """Vorhersage für einen Draußen-Termin der nächsten 7 Tage am Vereinsort.
    Liefert {emoji, temp, regen, text} oder None (kein Draußen-Termin, zu weit weg,
    kein Ort, Dienst nicht erreichbar)."""
    from .routes.portal import _wetter_emoji
    if not termin or not termin.get("draussen") or termin.get("abgesagt") or termin.get("verborgen"):
        return None
    heute = heute or date.today()
    try:
        tag = date.fromisoformat(termin.get("datum") or "")
    except ValueError:
        return None
    if not (0 <= (tag - heute).days <= 7):
        return None
    ort = (((einst["ort"] if einst and "ort" in einst.keys() else "") or "").split("(")[0]).strip()
    if not ort:
        return None
    punkt = _koordinaten(ort)
    if not punkt:
        return None
    schluessel = (punkt[0], punkt[1], tag.isoformat())
    eintrag = _WETTER_CACHE.get(schluessel)
    if eintrag and time.time() - eintrag[0] < 3600:
        stunden = eintrag[1]
    else:
        stunden = None
        try:
            w = _hole_json("https://api.open-meteo.com/v1/forecast",
                           {"latitude": punkt[0], "longitude": punkt[1], "timezone": "Europe/Berlin",
                            "hourly": "temperature_2m,precipitation_probability,weather_code",
                            "start_date": tag.isoformat(), "end_date": tag.isoformat()})
            h = w.get("hourly") or {}
            stunden = list(zip(h.get("time") or [], h.get("temperature_2m") or [],
                               h.get("precipitation_probability") or [], h.get("weather_code") or []))
        except Exception:  # noqa: BLE001
            stunden = None
        _WETTER_CACHE[schluessel] = (time.time(), stunden)
    if not stunden:
        return None
    try:
        stunde = int((termin.get("uhrzeit") or "12:00")[:2])
    except ValueError:
        stunde = 12
    wahl = next((s for s in stunden if s[0].endswith(f"T{stunde:02d}:00")), stunden[min(stunde, len(stunden) - 1)])
    _, temp, regen, code = wahl
    if temp is None:
        return None
    return {"emoji": _wetter_emoji(code), "temp": int(round(temp)),
            "regen": int(regen) if regen is not None else None,
            "uhr": f"{stunde:02d} Uhr", "ort": ort}
