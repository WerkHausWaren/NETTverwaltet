"""
Mitglieder-Bereich (Portal) – installierbare Web-App für Mitglieder.
Rollen: mitglied, extern (optional) und admin (zur Vorschau).

Erste Ausbaustufe: Startseite, eigene Daten (ansehen/ändern), Termine,
Nuki-Türöffner:in. Schichten/Dokumente/Chat/Push folgen.
"""
import io
import os
import json
import uuid
import calendar as pycal
from datetime import date, timedelta, datetime
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, current_app, abort, jsonify, send_from_directory, session, Response, g)
from flask_login import login_required, current_user

from ..db import (get_db, get_db_fuer, get_einstellungen, aktiver_verein_id,
                  benutzer_aktualisieren, push_abo_speichern, benutzer_ids_mit_rolle,
                  now_iso, get_system_db, schicht_berechtigt, alle_vereine,
                  einstellungen_fuer, oeffentliche_suche)
from .. import speicher, push, erwaehnungen, db, termin_maske, termin_extras
from ..funktionen import wer
from .mitglieder import KOMM_WEGE

bp = Blueprint("portal", __name__, url_prefix="/portal")

# Kontaktfelder, die ein Mitglied selbst ändern darf (nur vorhandene werden genutzt)
EDITIERBAR = ["email", "telefon", "mobil", "strasse", "plz", "ort"]

# Anpassbare Kacheln der Startseite (Schlüssel + Anzeigename)
PORTAL_BLOECKE = [("push", "🔔 Benachrichtigungen"),
                  ("neu", "✨ Was ist neu"),
                  ("news", "📣 Ankündigungen"), ("tuer", "🔓 Tür öffnen"),
                  ("umfragen", "🗳️ Umfragen & Terminfindung"), ("pinnwand", "📌 Schwarzes Brett"),
                  ("termine", "📅 Nächste Termine"), ("reservierungen", "🎟️ Meine Reservierungen"),
                  ("kino", "🎬 Kino-Programm"),
                  ("galerie", "📸 Fotogalerie"),
                  ("geburtstage", "🎂 Geburtstage"), ("infos", "❓ Infos & FAQ"),
                  ("berichte", "📊 Berichte & Besucher:innen"), ("beitrag", "💶 Mein Beitrag")]
PORTAL_BLOCK_KEYS = [k for k, _ in PORTAL_BLOECKE]


# Freunde (Rolle 'extern'): Whitelist der erlaubten Portal-Seiten – alles andere
# ist automatisch gesperrt (auch künftig neu hinzukommende Seiten).
FREUND_ERLAUBT = {
    "home", "termine", "termin", "kalender", "kino", "termin_ics", "hilfe", "app_installieren",
    "ueber",                      # Kennenlern-Seite des Vereins
    "push_key", "push_abo", "push_test", "pwa_installiert",
    "profil",                     # zeigt für Freunde nur das Mini-Profil
    "aussehen",                   # Farben und Textgröße – auch Gäste dürfen lesen können
    "fotoerlaubnis",              # eigene Foto-Antworten – Freunde melden sich ja gerade zu Veranstaltungen an
    "mitteilungen",               # Mitteilungs-Themen an-/abwählen
    "was_neu",                    # „Was ist neu" – neue Funktionen der App
    "hilfe_unterlage",            # Handbuch/Kurzanleitungen herunterladen
    "hilfe_lesen",                # Kurzanleitung als Textansicht lesen
    "merkliste", "termin_merken",  # Herzchenliste – dafür legen Gäste ein Freundekonto an
    "merkliste_erinnern",         # Erinnerung am Vortag an gemerkte Termine (selbst einschalten)
    "kategorie_bild",             # Standardbild einer Terminkategorie
}


@bp.before_request
def _freund_gate():
    if not getattr(current_user, "is_authenticated", False):
        return
    if getattr(current_user, "rolle", "") != "extern":
        return
    ep = (request.endpoint or "").split(".")[-1]
    if ep not in FREUND_ERLAUBT:
        flash("Dieser Bereich ist Mitgliedern vorbehalten.", "info")
        return redirect(url_for("portal.home"))


@bp.route("/gast-vorschau", methods=["POST"])
def gast_vorschau():
    """Vorstand: die App so ansehen, wie Gäste sie sehen (Umschaltung in app/__init__.py)."""
    if not getattr(current_user, "ist_admin", False):
        abort(403)
    e = get_einstellungen()
    if not (e and e["freunde_registrierung"]):
        flash("Die Gästeansicht ist für euren Verein ausgeschaltet (Freunde-Zugang in den Einstellungen).", "info")
        return redirect(url_for("portal.home"))
    session["gast_vorschau"] = True
    return redirect(url_for("portal.termine"))


@bp.route("/gast-vorschau/ende")
def gast_vorschau_ende():
    session.pop("gast_vorschau", None)
    return redirect(url_for("portal.home"))


def _ist_gastansicht():
    """True für nicht angemeldete Besucher:innen UND angemeldete Freunde (Rolle extern)."""
    return (not getattr(current_user, "is_authenticated", False)
            or getattr(current_user, "rolle", "") == "extern")


def _fuer_gast(termin):
    """Interne Termine für Gäste und Freunde anonymisieren.

    Die Maskierung selbst steht in app/termin_maske.py – dasselbe Modul nutzt
    das Kalender-Abo, damit beide Wege nach draußen dieselbe Regel befolgen.
    """
    if not _ist_gastansicht():
        return dict(termin)
    return termin_maske.maskiere(termin)


def _liste_fuer_gast(termine):
    return [_fuer_gast(t) for t in termine]


def _begruessung(mitglied=None, wetter=None):
    """Wechselnde Begrüßung für die Startseite: abhängig von Uhrzeit, Wochentag,
    Jahreszeit, besonderen Tagen UND dem aktuellen Wetter (kein „genieß die
    Sonne" bei Regen) – bei jedem Aufruf zufällig neu gemischt.
    Liefert (gruss, emoji, zusatz)."""
    import random
    from datetime import datetime
    jetzt = datetime.now()
    h, wt = jetzt.hour, jetzt.weekday()
    monat, tag = jetzt.month, jetzt.day
    code = (wetter or {}).get("code")
    temp = (wetter or {}).get("temp")
    sonnig = code in (0, 1)
    regen = code is not None and (51 <= code <= 67 or 80 <= code <= 82 or code >= 95)
    schnee = code is not None and (71 <= code <= 77 or code in (85, 86))

    if 5 <= h < 11:
        gruesse = [("Guten Morgen", "☀️"), ("Moin", "👋"), ("Schönen guten Morgen", "☕"),
                   ("Einen frischen Start", "🌅")]
    elif 11 <= h < 14:
        gruesse = [("Hallo", "👋"), ("Mahlzeit", "🥪"), ("Schönen Mittag", "☀️")]
    elif 14 <= h < 18:
        gruesse = [("Hallo", "👋"), ("Schönen Nachmittag", "🌤️"), ("Hi", "😊"),
                   ("Schön, dich zu sehen", "✨")]
    elif 18 <= h < 23:
        gruesse = [("Guten Abend", "🌆"), ("Schönen Abend", "✨"), ("Hallo", "👋")]
    else:
        gruesse = [("Nachteule unterwegs", "🦉"), ("Noch wach", "🌙"), ("Gute späte Stunde", "✨")]

    zusaetze = ["Schön, dass du vorbeischaust!", "Wie schön, dich zu sehen!",
                "Viel Spaß beim Stöbern!", "Schau dich in Ruhe um."]
    zusaetze += {
        0: ["Guten Start in die neue Woche!", "Neue Woche, neues Glück."],
        2: ["Halbzeit – die Woche ist schon halb geschafft!"],
        4: ["Das Wochenende ist zum Greifen nah!", "Endlich Freitag!"],
        5: ["Genieß das Wochenende!", "Schönen Samstag!"],
        6: ["Einen entspannten Sonntag!", "Sonntag – Zeit zum Durchatmen."],
    }.get(wt, [])
    # Jahreszeit + Wetter: Sonnen-Sprüche nur bei Sonne, Regen-Sprüche bei Regen
    if regen:
        zusaetze += ["Lass dich vom Regen nicht abhalten! ☔",
                     "Schietwetter draußen – umso gemütlicher im Verein. ☔"]
    elif schnee:
        zusaetze += ["Schneegestöber – zieh dich warm an! ❄️"]
    elif monat in (6, 7, 8):
        zusaetze += (["Sommerzeit – genieß die Sonne! 🌻", "Perfektes Wetter für neue Pläne. ☀️"]
                     if sonnig else ["Schöne Sommerzeit dir! 🌼"])
    elif monat in (9, 10, 11):
        zusaetze += ["Goldener Herbst – gemütliche Zeit im Verein. 🍂"]
    elif monat in (12, 1, 2):
        zusaetze += ["Komm gut durch die kalte Jahreszeit! ❄️"]
    else:
        zusaetze += ["Der Frühling bringt frischen Schwung! 🌸"]
    if temp is not None and temp >= 29:
        zusaetze += ["Bleib cool – heute wird's richtig warm! 🥵"]
    elif temp is not None and temp <= -2:
        zusaetze += ["Brrr – zieh dich warm an! 🥶"]
    if (monat, tag) == (1, 1):
        zusaetze = ["Frohes neues Jahr! 🎉"]
    elif (monat, tag) == (12, 6):
        zusaetze = ["Einen schönen Nikolaustag! 🎅"]
    elif (monat, tag) == (12, 24):
        zusaetze = ["Frohe Weihnachten! 🎄"]
    elif (monat, tag) == (12, 31):
        zusaetze = ["Guten Rutsch ins neue Jahr! 🎆"]

    # Geburtstag schlägt alles
    try:
        geb = (mitglied["geburtsdatum"] or "") if mitglied else ""
        if geb and geb[5:10] == jetzt.strftime("%m-%d"):
            return "Herzlichen Glückwunsch", "🎂", "Alles Gute zum Geburtstag – schön, dass es dich gibt!"
    except Exception:  # noqa: BLE001
        pass
    gruss, emoji = random.choice(gruesse)
    # Kein Sonnen-Emoji im Gruß, wenn es gerade regnet oder schneit
    if (regen or schnee) and emoji in ("☀️", "🌤️", "🌅"):
        emoji = "☔" if regen else "❄️"
    return gruss, emoji, random.choice(zusaetze)


_WETTER_CACHE = {}  # ort -> (zeitstempel, daten oder None)


def _wetter_emoji(code):
    """WMO-Wettercode (Open-Meteo) -> passendes Emoji."""
    if code is None:
        return "🌡️"
    if code == 0:
        return "☀️"
    if code in (1, 2):
        return "🌤️"
    if code == 3:
        return "☁️"
    if code in (45, 48):
        return "🌫️"
    if 51 <= code <= 57:
        return "🌦️"
    if 61 <= code <= 67 or 80 <= code <= 82:
        return "🌧️"
    if 71 <= code <= 77 or code in (85, 86):
        return "🌨️"
    if code >= 95:
        return "⛈️"
    return "🌡️"


def _wetter(einst):
    """Aktuelles Wetter (Emoji + Temperatur) für den Vereins-Ort aus den
    Einstellungen – Open-Meteo ohne API-Schlüssel, 15 Minuten gecacht.
    Bei Fehlern (offline, Ort unbekannt) einfach None, die Seite lädt normal."""
    import time
    ort = ((einst["ort"] if einst else "") or "").strip()
    if not ort:
        return None
    eintrag = _WETTER_CACHE.get(ort)
    if eintrag and time.time() - eintrag[0] < 900:
        return eintrag[1]
    daten = None
    try:
        import requests
        q = ort.split("(")[0].strip()
        geo = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                           params={"name": q, "count": 1, "language": "de"},
                           timeout=3).json()
        treffer = (geo.get("results") or [None])[0]
        if treffer:
            w = requests.get("https://api.open-meteo.com/v1/forecast",
                             params={"latitude": treffer["latitude"],
                                     "longitude": treffer["longitude"],
                                     "current": "temperature_2m,weather_code"},
                             timeout=3).json()
            akt = w.get("current") or {}
            temp = akt.get("temperature_2m")
            if temp is not None:
                daten = {"emoji": _wetter_emoji(akt.get("weather_code")),
                         "temp": int(round(temp)), "ort": q,
                         "code": akt.get("weather_code")}
    except Exception:  # noqa: BLE001
        daten = None
    _WETTER_CACHE[ort] = (time.time(), daten)
    return daten


def _vereins_bloecke_aus(einst):
    """Vom Vorstand deaktivierte Startseiten-Blöcke (Einstellungen → Mitglieder-App)."""
    try:
        return {k.strip() for k in ((einst.get("portal_bloecke_aus") if einst else "") or "").split(",")
                if k.strip()}
    except Exception:  # noqa: BLE001
        return set()


def _portal_layout(m):
    """Reihenfolge + ausgeblendete Startseiten-Kacheln des Mitglieds (aus portal_config)."""
    import json
    order = list(PORTAL_BLOCK_KEYS)
    hidden = set()
    if m and "portal_config" in m.keys() and m["portal_config"]:
        try:
            cfg = json.loads(m["portal_config"])
            o = [k for k in cfg.get("order", []) if k in PORTAL_BLOCK_KEYS]
            for k in PORTAL_BLOCK_KEYS:
                if k not in o:
                    o.append(k)
            order = o
            hidden = {k for k in cfg.get("hidden", []) if k in PORTAL_BLOCK_KEYS}
        except (ValueError, TypeError):
            pass
    return order, hidden


def _mitglieder_spalten(conn):
    return [r["name"] for r in conn.execute("PRAGMA table_info(mitglieder)").fetchall()]


def _mein_mitglied():
    """Findet den Mitglieder-Datensatz des angemeldeten Kontos (Verknüpfung oder E-Mail)."""
    # Sicherheitsgrenze: Freunde (Gäste) werden NIE mit Mitgliederdaten verknüpft –
    # auch dann nicht, wenn ihre E-Mail zufällig bei einem Mitglied hinterlegt ist.
    if getattr(current_user, "rolle", "") == "extern":
        return None
    vid = aktiver_verein_id()
    conn = get_db()
    row = None
    mid = getattr(current_user, "mitglied_id", None)
    if mid:
        row = conn.execute("SELECT * FROM mitglieder WHERE id=? AND verein_id=?", (mid, vid)).fetchone()
    if not row and getattr(current_user, "email", ""):
        row = conn.execute(
            "SELECT * FROM mitglieder WHERE verein_id=? AND email=? COLLATE NOCASE AND email!=''",
            (vid, current_user.email)).fetchone()
        if row:
            benutzer_aktualisieren(int(current_user.id), mitglied_id=row["id"])  # dauerhaft verknüpfen
    conn.close()
    return row


def _meine_adressen(m=None):
    """Alle E-Mail-Adressen (klein geschrieben), unter denen diese Person zu
    Veranstaltungen angemeldet sein kann: Konto-Adresse plus die Adressen des
    verknüpften Mitglieds – derselbe Vertrauensanker wie in _mein_mitglied()."""
    if not getattr(current_user, "is_authenticated", False):
        return set()
    adressen = set()
    if getattr(current_user, "email", ""):
        adressen.add(current_user.email.strip().lower())
    if m is None:
        m = _mein_mitglied()
    if m:
        if (m["email"] or "").strip():
            adressen.add(m["email"].strip().lower())
        if "email_weitere" in m.keys() and m["email_weitere"]:
            adressen.update(e.strip().lower() for e in m["email_weitere"].splitlines() if e.strip())
    return adressen


@bp.route("/logo")
def logo():
    """Vereinslogo für die Mitglieder-App (eigene Route, damit es auch für Mitglieder lädt)."""
    einst = get_einstellungen()
    if not einst or not einst["logo_dateiname"]:
        abort(404)
    from .einstellungen import LOGO_DIR, _bild_ohne_skript
    return _bild_ohne_skript(send_from_directory(LOGO_DIR, einst["logo_dateiname"]))


@bp.app_context_processor
def _merk_kontext():
    def merk_zahl():
        try:
            return merk_anzahl_kommend() if getattr(current_user, "is_authenticated", False) else 0
        except Exception:  # noqa: BLE001 – die Zahl darf nie eine Seite kippen
            return 0
    return {"merk_zahl": merk_zahl}


def _meine_merkliste():
    """IDs der gemerkten Termine des angemeldeten Kontos (leer für Gäste ohne Konto)."""
    if not getattr(current_user, "is_authenticated", False):
        return set()
    try:
        return db.merkliste_ids(current_user.id, aktiver_verein_id())
    except Exception:  # noqa: BLE001 – das Herz darf nie eine Seite kippen
        return set()


def merk_anzahl_kommend(ids=None):
    """Anzahl gemerkter, kommender und nicht abgesagter Termine – die Zahl am Herz."""
    ids = _meine_merkliste() if ids is None else ids
    if not ids:
        return 0
    conn = get_db()
    platz = ",".join("?" * len(ids))
    zeilen = conn.execute(f"SELECT t.nur_mitglieder, k.name AS kategorie FROM termine t "
                          f"LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
                          f"WHERE t.verein_id=? AND t.aktiv=1 AND COALESCE(t.abgesagt,0)=0 "
                          f"AND t.datum>=? AND t.id IN ({platz})",
                          [aktiver_verein_id(), date.today().isoformat()] + list(ids)).fetchall()
    conn.close()
    if _ist_gastansicht():
        zeilen = [z for z in zeilen if not (termin_maske.ist_intern(z) or termin_maske.ist_nicht_oeffentlich(z))]
    return len(zeilen)


@bp.route("/termin/<int:tid>/merken", methods=["POST"])
def termin_merken(tid):
    """Herz an/aus. Ohne Konto: Hinweis auf das Freundekonto (JSON für das Herz-Skript)."""
    if not getattr(current_user, "is_authenticated", False):
        return jsonify({"anmelden": url_for("portal.merkliste")}), 401
    vid = aktiver_verein_id()
    conn = get_db()
    t = conn.execute("SELECT t.id, t.nur_mitglieder, k.name AS kategorie FROM termine t "
                     "LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
                     "WHERE t.id=? AND t.verein_id=? AND t.aktiv=1", (tid, vid)).fetchone()
    conn.close()
    if not t:
        abort(404)
    # Freunde dürfen nur merken, was sie auch sehen dürfen
    if _ist_gastansicht() and (termin_maske.ist_intern(t) or termin_maske.ist_nicht_oeffentlich(t)):
        abort(403)
    gemerkt = db.merken_umschalten(current_user.id, vid, tid)
    return jsonify({"gemerkt": gemerkt, "anzahl": merk_anzahl_kommend(db.merkliste_ids(current_user.id, vid))})


@bp.route("/merkliste/erinnern", methods=["POST"])
def merkliste_erinnern():
    """Erinnerung am Vortag an gemerkte Termine ein-/ausschalten (nur für das eigene Konto)."""
    if not getattr(current_user, "is_authenticated", False):
        return redirect(url_for("portal.merkliste"))
    an = 1 if request.form.get("an") else 0
    sys_conn = db.get_system_db()
    sys_conn.execute("UPDATE benutzer SET merk_erinnern=? WHERE id=?", (an, int(current_user.id)))
    sys_conn.commit()
    sys_conn.close()
    flash("🔔 Wir erinnern dich am Vortag um 17 Uhr an deine gemerkten Termine." if an
          else "Erinnerung ausgeschaltet.", "erfolg")
    return redirect(url_for("portal.merkliste"))


@bp.route("/merkliste")
def merkliste():
    """♥ Merkzettel: gemerkte Veranstaltungen. Gäste ohne Konto melden sich direkt hier an
    oder legen ein kostenloses Konto an (19.11.2026)."""
    if not getattr(current_user, "is_authenticated", False):
        e = get_einstellungen()
        return render_template("portal/merkliste.html", aktiv="termine", gast_ohne_konto=True,
                               kommend=[], vergangen=[], gemerkt=set(), heute_iso=date.today().isoformat(),
                               verein_id=aktiver_verein_id(),
                               nc_konfiguriert=bool(e and "ncc_server" in e.keys() and e["ncc_server"]))
    vid = aktiver_verein_id()
    ids = sorted(_meine_merkliste())
    liste = []
    if ids:
        conn = get_db()
        platz = ",".join("?" * len(ids))
        liste = conn.execute(
            "SELECT t.id, t.titel, t.datum, t.uhrzeit, t.uhrzeit_bis, t.ort, t.bild, t.abgesagt, "
            "t.nur_mitglieder, k.farbe, k.name AS kategorie FROM termine t "
            "LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
            f"WHERE t.verein_id=? AND t.aktiv=1 AND t.id IN ({platz}) ORDER BY t.datum, t.uhrzeit",
            [vid] + ids).fetchall()
        conn.close()
        if _ist_gastansicht():
            liste = _liste_fuer_gast(liste)
    heute = date.today().isoformat()
    kommend = [t for t in liste if t["datum"] >= heute]
    vergangen = [t for t in liste if t["datum"] < heute][::-1]
    sys_conn = db.get_system_db()
    zeile = sys_conn.execute("SELECT merk_erinnern FROM benutzer WHERE id=?", (int(current_user.id),)).fetchone()
    sys_conn.close()
    return render_template("portal/merkliste.html", aktiv="termine", gast_ohne_konto=False,
                           kommend=kommend, vergangen=vergangen, gemerkt=set(ids), heute_iso=heute,
                           erinnern=bool(zeile and zeile["merk_erinnern"]))


@bp.route("/kategorie-bild/<int:kid>")
def kategorie_bild(kid):
    """Standardbild einer Terminkategorie (öffentliche Grafik für Karten ohne eigenes Bild)."""
    conn = get_db()
    vid = aktiver_verein_id()
    k = conn.execute("SELECT name, bild FROM termin_kategorien WHERE id=? AND verein_id=?", (kid, vid)).fetchone()
    # Gäste/Freunde: nur Kategorien mit öffentlichen Terminen – sonst wäre das Bild einer
    # internen Kategorie (Vorstand, „nicht-öffentlich“) ein Fingerzeig (Prüfbericht 19.11.)
    offen = True
    if k and _ist_gastansicht():
        offen = (not termin_maske.ist_nicht_oeffentlich({"kategorie": k["name"]})
                 and conn.execute("SELECT 1 FROM termine WHERE verein_id=? AND kategorie_id=? AND aktiv=1 "
                                  "AND COALESCE(nur_mitglieder,0)=0 LIMIT 1", (vid, kid)).fetchone() is not None)
    conn.close()
    if not k or not k["bild"] or not offen:
        abort(404)
    from .termine import _event_bild_dir
    return send_from_directory(_event_bild_dir(), k["bild"], max_age=86400)


@bp.route("/titelbild")
def titelbild():
    """Titelbild der Terminseite im Website-Stil (öffentliche Branding-Grafik)."""
    einst = get_einstellungen()
    name = (einst["website_titelbild"] if einst and "website_titelbild" in einst.keys() else "") or ""
    if not name:
        abort(404)
    from .einstellungen import LOGO_DIR, _bild_ohne_skript
    return _bild_ohne_skript(send_from_directory(LOGO_DIR, name, max_age=86400))


@bp.route("/")
def home():
    einst = get_einstellungen()
    heute = date.today().isoformat()
    conn = get_db()
    vid = aktiver_verein_id()
    # „Nächste Termine“ (19.11.2026): höchstens 3, im Look der Terminliste (Bild, Kategorie);
    # Gäste sehen hier nur öffentliche Termine – „Raum belegt“ gehört in die volle Liste.
    _zeilen = "SELECT t.id, t.titel, t.datum, t.uhrzeit, t.uhrzeit_bis, t.ort, t.anmeldung_aktiv, t.anmeldeschluss, " \
              "t.anmeldung_token, t.abgesagt, t.nur_mitglieder, t.bild, t.kategorie_id, k.bild AS kat_bild, " \
              "k.farbe, k.name AS kategorie FROM termine t LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
    termine = [dict(t) for t in _liste_fuer_gast(conn.execute(
        _zeilen + "WHERE t.verein_id=? AND t.aktiv=1 AND t.datum>=? "
        "ORDER BY t.datum, t.uhrzeit LIMIT 15", (vid, heute)).fetchall())]
    if _ist_gastansicht():
        termine = [t for t in termine if not t.get("verborgen")]
    termine = termine[:3]
    for t in termine:
        t["tag"] = termin_extras.relativ(t["datum"]) if termin_extras.relativ(t["datum"]) in ("Heute", "Morgen") else ""
    # Highlights für die Startseite (max. 2, nur kommende, nicht abgesagte)
    highlights = [h for h in _liste_fuer_gast(conn.execute(
        "SELECT t.id, t.titel, t.datum, t.uhrzeit, t.ort, t.beschreibung, t.bild, t.highlight, t.highlight_text, "
        "t.anmeldung_aktiv, t.anmeldung_token, t.anmeldeschluss, t.abgesagt, t.nur_mitglieder, t.serie_id, "
        "k.farbe, k.name AS kategorie FROM termine t LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
        "WHERE t.verein_id=? AND t.aktiv=1 AND t.datum>=? AND COALESCE(t.highlight,0)=1 "
        "AND COALESCE(t.abgesagt,0)=0 ORDER BY t.datum, t.uhrzeit LIMIT 20", (vid, heute)).fetchall())
        if h["highlight"]]
    highlights = termin_extras.highlights_einmal([dict(h) for h in highlights], 2)
    nur_oeff = " AND oeffentlich=1" if _ist_gastansicht() else ""
    zeitfenster = (" AND (sichtbar_von IS NULL OR sichtbar_von='' OR sichtbar_von<=date('now'))"
                   " AND (sichtbar_bis IS NULL OR sichtbar_bis='' OR sichtbar_bis>=date('now'))")
    news = conn.execute(f"SELECT * FROM ankuendigungen WHERE verein_id=? AND aktiv=1{nur_oeff}{zeitfenster} "
                        "ORDER BY id DESC LIMIT 3", (vid,)).fetchall()
    # Interne Vorführungen tauchen bei Gästen/Freund:innen gar nicht erst auf –
    # eine maskierte Kino-Karte ohne Filmtitel hätte keinen Nutzen.
    kino_gast = " AND COALESCE(t.nur_mitglieder,0)=0" if _ist_gastansicht() else ""
    kino_next = conn.execute(
        _zeilen.replace("LEFT JOIN termin_kategorien", "JOIN termin_kategorien") +
        "WHERE t.verein_id=? AND t.aktiv=1 AND t.abgesagt=0 AND t.datum>=? AND k.name LIKE '%Kino%'"
        f"{kino_gast} ORDER BY t.datum, t.uhrzeit LIMIT 1", (vid, heute)).fetchone()
    if kino_next:
        kino_next = dict(kino_next)
        kino_next["tag"] = termin_extras.relativ(kino_next["datum"]) if termin_extras.relativ(kino_next["datum"]) in ("Heute", "Morgen") else ""
    geburtstage = _geburtstage(conn, vid)
    conn.close()
    m = _mein_mitglied()
    order, hidden = _portal_layout(m)
    aus = _vereins_bloecke_aus(einst)
    bloecke = [k for k in order if k not in hidden and k not in aus]
    if _ist_gastansicht():
        # Gäste/Freunde sehen nur öffentliche Blöcke (keine Geburtstage, Pinnwand, Beiträge …);
        # Push und Reservierungen brauchen ein Konto und bleiben anonymen Besucher:innen verborgen
        erlaubt = ("news", "termine", "reservierungen", "kino", "push") \
            if getattr(current_user, "is_authenticated", False) else ("news", "termine", "kino")
        bloecke = [k for k in bloecke if k in erlaubt]
    meine_aufgaben = []
    reservierungen = []
    conn2 = get_db()
    if m:
        meine_aufgaben = conn2.execute(
            "SELECT id, titel, faellig_am, faellig_zeit, wiederholung, COALESCE(zielgruppe,'') AS zielgruppe "
            "FROM aufgaben WHERE verein_id=? AND (mitglied_id=? OR COALESCE(zielgruppe,'')='mitglieder') "
            "AND erledigt=0 ORDER BY faellig_am, faellig_zeit LIMIT 10",
            (aktiver_verein_id(), m["id"])).fetchall()
    # Meine Reservierungen: Veranstaltungs-Anmeldungen zur E-Mail dieses Kontos (v.a. für Freunde)
    if getattr(current_user, "is_authenticated", False) and getattr(current_user, "email", ""):
        from .veranstaltungen import bezahl_link
        reservierungen = [dict(r) for r in conn2.execute(
            "SELECT a.token, a.warteliste, a.bezahlstatus, a.anmelder_name, a.betrag, t.id AS termin_id, "
            "t.titel, t.datum, t.uhrzeit, COALESCE(a.foto_status,'') AS foto_status, "
            "COALESCE(a.foto_social,'') AS foto_social, "
            "(SELECT COUNT(*) FROM veranstaltung_teilnehmer tn WHERE tn.anmeldung_id=a.id) plaetze "
            "FROM veranstaltung_anmeldung a JOIN termine t ON t.id=a.termin_id "
            "WHERE a.verein_id=? AND a.email=? COLLATE NOCASE AND a.storniert=0 AND t.datum>=? "
            "ORDER BY t.datum, t.uhrzeit", (vid, current_user.email, heute)).fetchall()]
        for r in reservierungen:
            r["bezahl_link"] = bezahl_link(einst, r["betrag"]) if r["bezahlstatus"] == "offen" else ""
    conn2.close()
    from .aufgaben import WIEDERHOLUNGEN
    wetter_daten = _wetter(einst)
    from .. import nuki_api
    nuki_bereit = nuki_api.konfiguriert(einst)
    darf_tuer, tuer_gueltig_bis = False, None
    if m and nuki_bereit:
        conn3 = get_db()
        darf_tuer = _darf_tuer(conn3, vid, m["id"])
        if darf_tuer:
            tuer_gueltig_bis = _tuer_gueltig_bis(conn3, vid, m["id"])
        conn3.close()
    # „Was ist neu": Zähler seit dem letzten Öffnen der Neuigkeiten-Seite
    neu_anzahl = 0
    if getattr(current_user, "is_authenticated", False):
        from .. import neuigkeiten as _neuigkeiten
        sys_neu = get_system_db()
        zeile_neu = sys_neu.execute("SELECT COALESCE(neuigkeiten_gesehen_am,'') g FROM benutzer WHERE id=?",
                                    (int(current_user.id),)).fetchone()
        sys_neu.close()
        neu_anzahl = _neuigkeiten.anzahl_neu(
            einst, vorstand=getattr(current_user, "rolle", "") in ("admin", "oeffentlichkeit"),
            seit=(zeile_neu["g"] or None) if zeile_neu else None)
    # Modul „Verteilen": Karte mit laufenden Verteilaktionen (nur angemeldet)
    verteil_karte = []
    if einst and einst.get("modul_verteilen") and getattr(current_user, "is_authenticated", False):
        from .. import verteilen as _vt
        _c = get_db()
        try:
            verteil_karte = _vt.startkarte(_c, vid, int(current_user.id))
        finally:
            _c.close()
    return render_template("portal/home.html", aktiv="home", einst=einst, termine=termine, mitglied=m,
                           highlights=highlights, gemerkt=_meine_merkliste(),
                           verteil_karte=verteil_karte,
                           neu_anzahl=neu_anzahl,
                           ist_gast=_ist_gastansicht(),
                           bloecke=bloecke, news=news, geburtstage=geburtstage, ist_admin=_ist_admin(),
                           meine_aufgaben=meine_aufgaben, heute_iso=heute,
                           reservierungen=reservierungen, wiederholungen=WIEDERHOLUNGEN,
                           kino_next=kino_next, kino_titel=_kino_titel(),
                           nuki_bereit=nuki_bereit, darf_tuer=darf_tuer, tuer_gueltig_bis=tuer_gueltig_bis,
                           begruessung=_begruessung(m, wetter_daten), wetter=wetter_daten)


def _aktive_schluessel(conn, vid, mid):
    """Offene, aktuell gültige Schlüsselbuch-Einträge des Mitglieds (Berechtigung
    inkl. Befristung: befristet_bis leer = dauerhaft, sonst muss es >= heute sein)."""
    if not mid:
        return []
    heute = date.today().isoformat()
    return conn.execute(
        "SELECT befristet_bis FROM schluesselbuch WHERE verein_id=? AND inhaber_mitglied_id=? "
        "AND zurueckgegeben_am='' AND (befristet_bis='' OR befristet_bis IS NULL OR befristet_bis>=?)",
        (vid, mid, heute)).fetchall()


def _darf_tuer(conn, vid, mid):
    """True, wenn das Mitglied mindestens einen gültigen Schlüsselbuch-Eintrag hat."""
    return bool(_aktive_schluessel(conn, vid, mid))


def _tuer_gueltig_bis(conn, vid, mid):
    """None = unbefristet gültig; sonst spätestes Ablaufdatum (ISO) der Berechtigung.
    Eine dauerhafte Ausgabe schlägt jede Befristung – dann keine Ablaufanzeige."""
    fristen = [(r["befristet_bis"] or "").strip() for r in _aktive_schluessel(conn, vid, mid)]
    if not fristen or any(f == "" for f in fristen):
        return None
    return max(fristen)


@bp.route("/ausweis")
@login_required
def ausweis():
    """Digitaler Mitgliedsausweis fürs Handy – mit prüfbarem QR-Code."""
    m = _mein_mitglied()
    if not m:
        flash("Wir konnten dich noch keinem Mitglied zuordnen. Bitte beim Vorstand melden.", "fehler")
        return redirect(url_for("portal.home"))
    einst = get_einstellungen()
    vid = aktiver_verein_id()
    # Prüf-QR: Link auf die Prüfseite mit Signatur (fälschungssicher ohne Datenbank-Abfrage)
    import hashlib
    import hmac
    from flask import current_app
    schluessel = str(current_app.config.get("SECRET_KEY", "")).encode()
    jahr = date.today().year
    sig = hmac.new(schluessel, f"ausweis-{vid}-{m['id']}-{jahr}".encode(), hashlib.sha256).hexdigest()[:12]
    host = request.host_url.rstrip("/")
    pruef_url = f"{host}/portal/ausweis/pruefen/{m['id']}/{jahr}/{sig}"
    from .. import qr as qrmod
    return render_template("portal/ausweis.html", aktiv="profil", m=m, einst=einst,
                           qr=qrmod.data_uri(pruef_url, scale=6), jahr=jahr,
                           avatar=bool(m["avatar_dateiname"] if "avatar_dateiname" in m.keys() else None))


@bp.route("/ausweis/pruefen/<int:mid>/<int:jahr>/<sig>")
def ausweis_pruefen(mid, jahr, sig):
    """Öffentliche Prüfseite: bestätigt einen gescannten Ausweis (ohne Login)."""
    import hashlib
    import hmac
    from flask import current_app
    schluessel = str(current_app.config.get("SECRET_KEY", "")).encode()
    # Die Signatur bindet die Vereins-id – darüber den Verein des Ausweises
    # finden, statt den gerade aktiven Verein anzunehmen.
    vid = None
    for v in alle_vereine():
        erwartet = hmac.new(schluessel, f"ausweis-{v['id']}-{mid}-{jahr}".encode(),
                            hashlib.sha256).hexdigest()[:12]
        if hmac.compare_digest(sig, erwartet):
            vid = v["id"]
            break
    gueltig = vid is not None and jahr >= date.today().year
    m = None
    if gueltig:
        conn = get_db_fuer(vid)
        m = conn.execute("SELECT vorname, nachname, mitgliedsnummer, status FROM mitglieder "
                         "WHERE id=? AND verein_id=?", (mid, vid)).fetchone()
        conn.close()
        gueltig = bool(m) and m["status"] != "ausgetreten"
    einst = einstellungen_fuer(vid) if vid else get_einstellungen()
    return render_template("portal/ausweis_pruefen.html", gueltig=gueltig, m=m,
                           einst=einst, jahr=jahr)


@bp.route("/tuer", methods=["POST"])
@login_required
def tuer_aktion():
    """Tür öffnen/schließen über Nuki Web – nur für Schlüsselbuch-Berechtigte."""
    from .. import nuki_api
    vid = aktiver_verein_id()
    m = _mein_mitglied()
    if not m:
        flash("Für die Türöffnung müssen wir dich einem Mitglied zuordnen können.", "fehler")
        return redirect(url_for("portal.home"))
    conn = get_db()
    berechtigt = _darf_tuer(conn, vid, m["id"])
    conn.close()
    if not berechtigt:
        flash("Du hast keine Türberechtigung. Dafür brauchst du einen Eintrag im Schlüsselbuch – "
              "bitte frag den Vorstand.", "fehler")
        return redirect(url_for("portal.home"))
    oeffnen = request.form.get("aktion") != "schliessen"
    wort = "geöffnet" if oeffnen else "geschlossen"
    aktion_name = "öffnen" if oeffnen else "schließen"
    uid = int(current_user.id)
    name = f"{m['vorname']} {m['nachname']}".strip()
    if db.tuer_zu_haeufig(uid):
        flash("Bitte einen Moment warten – die letzte Türaktion ist gerade erst erfolgt.", "info")
        return redirect(url_for("portal.home"))
    einst = get_einstellungen()
    if not nuki_api.konfiguriert(einst):
        flash("Die Türöffnung ist noch nicht eingerichtet.", "fehler")
        return redirect(url_for("portal.home"))
    try:
        nuki_api.aktion(einst, oeffnen=oeffnen)
        db.tuer_protokoll_schreiben(vid, uid, m["id"], name, aktion_name, True)
        flash(f"🔓 Tür {wort}.", "erfolg")
        _tuer_admins_pushen(vid, name, wort)
    except nuki_api.NukiFehler as e:
        db.tuer_protokoll_schreiben(vid, uid, m["id"], name, aktion_name, False, str(e))
        flash(f"Tür konnte nicht {wort} werden: {e}", "fehler")
    except Exception as e:  # noqa: BLE001
        db.tuer_protokoll_schreiben(vid, uid, m["id"], name, aktion_name, False, str(e))
        flash(f"Tür konnte nicht {wort} werden: {e}", "fehler")
    return redirect(url_for("portal.home"))


def _tuer_admins_pushen(vid, name, wort):
    """Optionale Info-Push an die Admins, wer die Tür betätigt hat (best effort)."""
    try:
        sys = get_system_db()
        ids = [r["id"] for r in sys.execute(
            "SELECT id FROM benutzer WHERE verein_id=? AND aktiv=1 AND rolle='admin'", (vid,)).fetchall()]
        sys.close()
        if ids:
            push.senden(ids, f"🔓 Tür {wort}", f"{name} hat die Tür {wort}.", url="/schluessel/", thema="schluessel")
    except Exception:  # noqa: BLE001
        pass


@bp.route("/aufgabe/neu", methods=["POST"])
@login_required
def aufgabe_neu():
    """Mitglied legt sich selbst eine Aufgabe an (auch wiederkehrend)."""
    m = _mein_mitglied()
    if not m:
        flash("Wir konnten dich noch keinem Mitglied zuordnen.", "fehler")
        return redirect(url_for("portal.home"))
    titel = (request.form.get("titel") or "").strip()
    if not titel:
        flash("Bitte einen Titel für die Aufgabe angeben.", "fehler")
        return redirect(url_for("portal.home"))
    from .aufgaben import WIEDERHOLUNGEN
    wdh = request.form.get("wiederholung", "einmalig")
    if wdh not in dict(WIEDERHOLUNGEN):
        wdh = "einmalig"
    faellig = request.form.get("faellig_am") or date.today().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO aufgaben (verein_id, titel, beschreibung, faellig_am, faellig_zeit, wiederholung, "
        "vorlauf_min, mitglied_id, erledigt, benachrichtigt, erstellt_am, erstellt_von) VALUES (?,?,?,?,?,?,60,?,0,0,?,?)",
        (aktiver_verein_id(), titel, "", faellig, request.form.get("faellig_zeit", ""), wdh,
         m["id"], now_iso(), wer()))
    conn.commit()
    conn.close()
    flash("Aufgabe angelegt – du schaffst das! 💪", "erfolg")
    return redirect(url_for("portal.home"))


def _gesehen_merken(spalte):
    """Zeitstempel „Seite zuletzt geöffnet" am Konto fortschreiben (für die
    Zahlen an den App-Symbolen). spalte ist ein fester Spaltenname aus dem Code."""
    if not getattr(current_user, "is_authenticated", False):
        return
    try:
        sys = get_system_db()
        sys.execute(f"UPDATE benutzer SET {spalte}=? WHERE id=?",  # noqa: S608 – spalte fest
                    (now_iso(), int(current_user.id)))
        sys.commit()
        sys.close()
    except Exception:  # noqa: BLE001
        pass


def _nav_zaehler():
    """Kleine Zahlen an den Symbolen der unteren App-Leiste (nur angemeldet).

    schichten = Live-Zustand: künftige Schichten unbesetzt oder Tausch gesucht,
    termine   = neue künftige Termine seit dem letzten Besuch der Termine-Seite,
    aufgaben  = meine offenen Aufgaben (inkl. „Alle Mitglieder"),
    pinnwand  = neue Beiträge seit dem letzten Besuch des Schwarzen Bretts,
    umfragen  = laufende Umfragen, bei denen ich noch nicht abgestimmt habe.

    Termine/Pinnwand zählen erst ab dem ersten Besuch der jeweiligen Seite –
    sonst stünde beim allerersten Öffnen der komplette Bestand als „neu" dran.
    """
    z = {"schichten": 0, "termine": 0, "aufgaben": 0, "pinnwand": 0, "umfragen": 0}
    if not getattr(current_user, "is_authenticated", False):
        return z
    try:
        vid = aktiver_verein_id()
        sys = get_system_db()
        row = sys.execute("SELECT termine_gesehen_am, pinnwand_gesehen_am FROM benutzer WHERE id=?",
                          (int(current_user.id),)).fetchone()
        sys.close()
        t_seit = (row["termine_gesehen_am"] if row and "termine_gesehen_am" in row.keys() else "") or ""
        p_seit = (row["pinnwand_gesehen_am"] if row and "pinnwand_gesehen_am" in row.keys() else "") or ""
        heute = date.today().isoformat()
        conn = get_db()
        if t_seit:
            z["termine"] = conn.execute(
                "SELECT COUNT(*) FROM termine WHERE verein_id=? AND aktiv=1 AND datum>=? "
                "AND COALESCE(erstellt_am,'') > ?", (vid, heute, t_seit)).fetchone()[0]
        m = _mein_mitglied()
        if m:
            einst = get_einstellungen()
            if einst and einst["modul_schichten"]:
                z["schichten"] = conn.execute(
                    "SELECT COUNT(*) FROM schicht_slots ss JOIN schichten s ON s.id=ss.schicht_id "
                    "WHERE s.verein_id=? AND s.datum>=? AND (ss.mitglied_id IS NULL OR ss.tausch_gesucht=1)",
                    (vid, heute)).fetchone()[0]
            z["aufgaben"] = conn.execute(
                "SELECT COUNT(*) FROM aufgaben WHERE verein_id=? "
                "AND (mitglied_id=? OR COALESCE(zielgruppe,'')='mitglieder') AND erledigt=0",
                (vid, m["id"])).fetchone()[0]
            if p_seit:
                z["pinnwand"] = conn.execute(
                    "SELECT COUNT(*) FROM pinnwand WHERE verein_id=? AND aktiv=1 "
                    "AND COALESCE(erstellt_am,'') > ?", (vid, p_seit)).fetchone()[0]
            z["umfragen"] = conn.execute(
                "SELECT COUNT(*) FROM umfragen u WHERE u.verein_id=? AND u.aktiv=1 "
                "AND (u.ende_am IS NULL OR u.ende_am='' OR u.ende_am>=?) "
                "AND NOT EXISTS (SELECT 1 FROM umfrage_stimmen s "
                "WHERE s.umfrage_id=u.id AND s.mitglied_id=?)",
                (vid, heute, m["id"])).fetchone()[0]
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    return z


@bp.context_processor
def _nav_kontext():
    return {"nav_zaehler": _nav_zaehler()}


@bp.route("/pwa-installiert", methods=["POST"])
@login_required
def pwa_installiert():
    """Merkt am Konto, dass die App installiert läuft. Der „App verfügbar"-Hinweis
    verschwindet damit auch im normalen Browser – wichtig auf dem iPhone, wo sich
    Safari und die installierte App keinen Speicher teilen."""
    sys = get_system_db()
    sys.execute("UPDATE benutzer SET pwa_installiert=1 WHERE id=?", (current_user.id,))
    sys.commit()
    sys.close()
    return "", 204


@bp.route("/aufgabe/<int:aid>/erledigt", methods=["POST"])
@login_required
def aufgabe_erledigt(aid):
    """Mitglied hakt seine zugewiesene Aufgabe ab (wiederkehrende rollen weiter)."""
    m = _mein_mitglied()
    if not m:
        return redirect(url_for("portal.home"))
    vid = aktiver_verein_id()
    conn = get_db()
    a = conn.execute("SELECT * FROM aufgaben WHERE id=? AND verein_id=? "
                     "AND (mitglied_id=? OR COALESCE(zielgruppe,'')='mitglieder')",
                     (aid, vid, m["id"])).fetchone()
    if a:
        if a["wiederholung"] and a["wiederholung"] != "einmalig":
            from .aufgaben import wiederholung_fortschreiben
            wiederholung_fortschreiben(conn, a)
        else:
            conn.execute("UPDATE aufgaben SET erledigt=1, erledigt_am=? WHERE id=?", (now_iso(), aid))
        conn.commit()
        flash("Als erledigt markiert – danke!", "erfolg")
        session["aufgabe_feier"] = 1
    conn.close()
    return redirect(url_for("portal.home"))


@bp.route("/start-anpassen", methods=["GET", "POST"])
@login_required
def start_anpassen():
    import json
    m = _mein_mitglied()
    if not m:
        flash("Wir konnten dich noch keinem Mitglied zuordnen. Bitte beim Vorstand melden.", "fehler")
        return redirect(url_for("portal.home"))
    order, hidden = _portal_layout(m)
    if request.method == "POST":
        neu_hidden = {k for k in PORTAL_BLOCK_KEYS if not request.form.get("show_" + k)}
        neu_order = list(order)
        move = request.form.get("move", "")
        if ":" in move:
            richtung, key = move.split(":", 1)
            if key in neu_order:
                i = neu_order.index(key)
                if richtung == "up" and i > 0:
                    neu_order[i - 1], neu_order[i] = neu_order[i], neu_order[i - 1]
                elif richtung == "down" and i < len(neu_order) - 1:
                    neu_order[i + 1], neu_order[i] = neu_order[i], neu_order[i + 1]
        cfg = json.dumps({"order": neu_order, "hidden": sorted(neu_hidden)})
        conn = get_db()
        conn.execute("UPDATE mitglieder SET portal_config=? WHERE id=? AND verein_id=?",
                     (cfg, m["id"], aktiver_verein_id()))
        conn.commit()
        conn.close()
        if not move:
            flash("Deine Startseite wurde gespeichert.", "erfolg")
            return redirect(url_for("portal.home"))
        return redirect(url_for("portal.start_anpassen"))
    aus = _vereins_bloecke_aus(get_einstellungen())
    return render_template("portal/start_anpassen.html", aktiv="home",
                           order=[k for k in order if k not in aus], hidden=hidden,
                           labels=dict(PORTAL_BLOECKE))


def kurznamen(personen):
    """Anzeigenamen für Schicht-Übersichten (21.11.2026): der Vorname; teilen sich
    mehrere Personen den Vornamen, kommt der Nachname bis zum ersten
    unterscheidenden Buchstaben dazu („Anna M.“ / „Anna Sch.“ statt Initialen).

    personen: Iterable aus (id, vorname, nachname) – am besten ALLE Personen des
    Vereins, damit derselbe Mensch überall gleich heißt. Liefert {id: name}."""
    gruppen = {}
    for pid, vor, nach in personen:
        vor, nach = (vor or "").strip(), (nach or "").strip()
        gruppen.setdefault(vor.casefold(), []).append((pid, vor, nach))
    namen = {}
    for liste in gruppen.values():
        for pid, vor, nach in liste:
            basis = vor or nach or "?"
            andere = [n.casefold() for p2, _, n in liste if p2 != pid]
            if not vor or not andere or not nach:
                namen[pid] = basis
                continue
            k = 1
            while k < len(nach) and any(o.startswith(nach[:k].casefold()) for o in andere):
                k += 1
            namen[pid] = f"{vor} {nach}" if k >= len(nach) else f"{vor} {nach[:k]}."
    return namen


def _schicht_info(conn, vid, termin_ids):
    """Je Termin: Anzahl Schicht-Plätze, offene Plätze und die Namen der Eingetragenen
    (Vorname, bei gleichen Vornamen mit unterscheidendem Nachnamen-Anfang)."""
    if not termin_ids:
        return {}
    ph = ",".join("?" * len(termin_ids))
    rows = conn.execute(
        f"SELECT s.termin_id AS tid, ss.mitglied_id AS mid "
        f"FROM schichten s JOIN schicht_slots ss ON ss.schicht_id=s.id "
        f"WHERE s.verein_id=? AND s.termin_id IN ({ph})", [vid] + list(termin_ids)).fetchall()
    namen = {}
    if any(r["mid"] for r in rows):
        namen = kurznamen((m["id"], m["vorname"], m["nachname"]) for m in conn.execute(
            "SELECT id, vorname, nachname FROM mitglieder WHERE verein_id=?", (vid,)).fetchall())
    info = {}
    for r in rows:
        d = info.setdefault(r["tid"], {"gesamt": 0, "offen": 0, "namen": []})
        d["gesamt"] += 1
        if r["mid"]:
            d["namen"].append(namen.get(r["mid"], "?"))
        else:
            d["offen"] += 1
    return info


@bp.route("/termine")
def termine():
    vid = aktiver_verein_id()
    heute = date.today().isoformat()
    _gesehen_merken("termine_gesehen_am")
    q = request.args.get("q", "").strip()
    kat = request.args.get("kat", type=int)
    # „Für wen“: mehrere Zielgruppen wählbar – angezeigt werden Termine für IRGENDEINE davon
    gruppe = (request.args.get("gruppe") or "").strip()[:40]
    ziele = []
    for z in request.args.getlist("ziel"):
        if z in termin_extras.ZIELGRUPPEN_KEYS and z not in ziele:
            ziele.append(z)
    zeitraum = request.args.get("zeitraum", "kommend")
    if zeitraum not in ("kommend", "vergangen", "alle"):
        zeitraum = "kommend"

    def _datum_ok(w):
        try:
            return date.fromisoformat(w).isoformat() if w else ""
        except ValueError:
            return ""
    von = _datum_ok(request.args.get("von", "").strip())
    bis = _datum_ok(request.args.get("bis", "").strip())
    conn = get_db()
    where = ["t.verein_id=?", "t.aktiv=1"]
    params = [vid]
    if von or bis:
        # Datumsbereich hat Vorrang vor dem Zeitraum-Schalter
        if von:
            where.append("t.datum>=?")
            params.append(von)
        if bis:
            where.append("t.datum<=?")
            params.append(bis)
    elif zeitraum == "kommend":
        where.append("t.datum>=?")
        params.append(heute)
    elif zeitraum == "vergangen":
        where.append("t.datum<?")
        params.append(heute)
    if kat:
        where.append("t.kategorie_id=?")
        params.append(kat)
        # Sonst ließe sich über den Kategoriefilter erschließen, dass die
        # „Mitgliederveranstaltung“ am Dienstag eine Vorstandssitzung ist.
        if _ist_gastansicht():
            where.append("COALESCE(t.nur_mitglieder,0)=0")
    if gruppe:
        # Kategorie-Gruppe (Filterknopf, der mehrere Kategorien zusammenfasst)
        gids = [r[0] for r in conn.execute("SELECT id FROM termin_kategorien WHERE verein_id=? AND COALESCE(gruppe,'')=?",
                                           (vid, gruppe))]
        where.append(("t.kategorie_id IN (" + ",".join("?" * len(gids)) + ")") if gids else "0")
        params += gids
        if _ist_gastansicht():
            where.append("COALESCE(t.nur_mitglieder,0)=0")
    if ziele:
        where.append("(" + " OR ".join(["(',' || COALESCE(t.zielgruppen,'') || ',') LIKE ?"] * len(ziele)) + ")")
        params += [f"%,{z},%" for z in ziele]
        if _ist_gastansicht():
            where.append("COALESCE(t.nur_mitglieder,0)=0")
    if q:
        # Gäste dürfen nicht über die Suche an Titel oder Beschreibung interner
        # Termine kommen – dort wird nur der Ort durchsucht.
        if _ist_gastansicht():
            where.append("(((t.titel LIKE ? OR t.beschreibung LIKE ?) AND COALESCE(t.nur_mitglieder,0)=0) "
                         "OR t.ort LIKE ?)")
        else:
            where.append("(t.titel LIKE ? OR t.beschreibung LIKE ? OR t.ort LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    reihenfolge = "t.datum DESC, t.uhrzeit DESC" if zeitraum == "vergangen" and not (von or bis) \
        else "t.datum, t.uhrzeit"
    liste = conn.execute(
        "SELECT t.id, t.titel, t.datum, t.uhrzeit, t.uhrzeit_bis, t.ort, t.beschreibung, "
        "k.farbe, k.name AS kategorie, t.serie_id, t.bild, t.preis, t.anmeldung_token, "
        "t.mitwirkende, t.sponsor_logo, t.sponsor_url, t.trailer_url, t.highlight, t.highlight_text, "
        "t.eintritt_art, t.zielgruppen, t.kategorie_id, k.bild AS kat_bild, "
        "t.anmeldung_aktiv, t.anmeldeschluss, t.sponsor_name, t.abgesagt, t.max_teilnehmer, t.nur_mitglieder, "
        "(SELECT COUNT(*) FROM veranstaltung_teilnehmer tn JOIN veranstaltung_anmeldung a ON a.id=tn.anmeldung_id "
        " WHERE a.termin_id=t.id AND a.storniert=0 AND a.warteliste=0) AS belegt, "
        "(SELECT ROUND(AVG(b.sterne), 1) FROM bewertungen b WHERE b.termin_id=t.id) AS bewertung "
        "FROM termine t LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
        f"WHERE {' AND '.join(where)} ORDER BY {reihenfolge}", params).fetchall()
    if ziele and _ist_gastansicht():
        # Zielgruppen-Filter darf nichts über nicht-öffentliche Termine verraten
        liste = [t for t in liste if not termin_maske.ist_nicht_oeffentlich(t)]
    if q and _ist_gastansicht():
        # Gleiche Regel wie beim Mitglieder-Haken, nur in Python (SQL faltet
        # das „Ö“ im Kategorienamen nicht): Ein nicht-öffentlicher Termin darf
        # über die Suche nur per ORT gefunden werden – sonst bestätigte ein
        # maskierter Treffer den eingetippten Klarnamen.
        ql = q.casefold()
        liste = [t for t in liste
                 if not (termin_maske.ist_nicht_oeffentlich(t)
                         and ql not in (t["ort"] or "").casefold())]
    if _ist_gastansicht():
        # Nur Kategorien zur Auswahl stellen, die auch auf offenen Terminen vorkommen –
        # sonst stünde „Vorstand / Sitzung“ im Filter und wäre ein Fingerzeig.
        kategorien = conn.execute(
            "SELECT DISTINCT k.id, k.name, k.farbe, k.gruppe FROM termin_kategorien k "
            "JOIN termine t ON t.kategorie_id=k.id AND t.aktiv=1 AND COALESCE(t.nur_mitglieder,0)=0 "
            "WHERE k.verein_id=? ORDER BY k.name", (vid,)).fetchall()
    else:
        kategorien = conn.execute(
            "SELECT id, name, farbe, gruppe FROM termin_kategorien WHERE verein_id=? ORDER BY name", (vid,)).fetchall()
    # Kompakte Knopfleiste: nur Kategorien mit KOMMENDEN Terminen (+ die gerade gewählte)
    kommende_kats = {r[0] for r in conn.execute(
        "SELECT DISTINCT kategorie_id FROM termine WHERE verein_id=? AND aktiv=1 AND datum>=?"
        + (" AND COALESCE(nur_mitglieder,0)=0" if _ist_gastansicht() else ""), (vid, heute))}
    if zeitraum == "kommend" and not (von and von < heute):
        kategorien = [k for k in kategorien if k["id"] in kommende_kats or k["id"] == kat
                      or (gruppe and (k["gruppe"] or "") == gruppe)]
    # Knöpfe: gruppierte Kategorien als EIN Knopf; „nicht-öffentlich“ für Gäste nie (19.11.)
    kat_knoepfe, gruppen_gesehen = [], set()
    for k in kategorien:
        if _ist_gastansicht() and termin_maske.ist_nicht_oeffentlich({"kategorie": k["name"]}):
            continue
        g = (k["gruppe"] or "").strip()
        if g:
            if g in gruppen_gesehen:
                continue
            gruppen_gesehen.add(g)
            kat_knoepfe.append({"label": g, "farbe": k["farbe"], "gruppe": g, "kat": None, "aktiv": gruppe == g})
        else:
            kat_knoepfe.append({"label": k["name"], "farbe": k["farbe"], "gruppe": None, "kat": k["id"],
                                "aktiv": kat == k["id"]})
    kat_knoepfe.sort(key=lambda x: x["label"].casefold())
    # „Für wen“-Knöpfe nur für Zielgruppen, zu denen es kommende Termine gibt (+ gewählte)
    vorhanden = set()
    for zg in conn.execute(
            "SELECT DISTINCT t.zielgruppen, k.name AS kategorie FROM termine t "
            "LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id WHERE t.verein_id=? AND t.aktiv=1 AND t.datum>=? "
            "AND COALESCE(t.zielgruppen,'')!=''" + (" AND COALESCE(t.nur_mitglieder,0)=0" if _ist_gastansicht() else ""),
            (vid, heute)):
        if _ist_gastansicht() and termin_maske.ist_nicht_oeffentlich(zg):
            continue
        vorhanden.update(k for k, _ in termin_extras.zielgruppen_liste(zg["zielgruppen"]))
    ziel_knoepfe = [(k, n) for k, n in termin_extras.ZIELGRUPPEN if k in vorhanden or k in ziele]
    # Schicht-Besetzung ist Vereins-intern – Gäste/Freunde sehen sie nicht
    info = {} if _ist_gastansicht() else _schicht_info(conn, vid, [t["id"] for t in liste])
    liste = [dict(t) for t in _liste_fuer_gast(liste)]
    conn.close()
    # Kurz-Hinweise je Karte: „Heute/Morgen“, „Beginnt in 3 Std.“, „Anmeldung nur noch bis …“
    modul_va = bool((get_einstellungen() or {}).get("modul_veranstaltungen"))
    for t in liste:
        anmeldbar = bool(modul_va and not t.get("abgesagt") and t.get("anmeldung_aktiv") and t["datum"] >= heute
                         and (not t.get("anmeldeschluss") or t["anmeldeschluss"] >= heute))
        t["hinweis"] = termin_extras.hinweise(t, anmeldbar=anmeldbar)
    # Wiederkehrendes (≥ 3× gleicher Titel) für Gäste zu einer Karte bündeln – nicht bei Suche
    # Nur bündeln, wenn ausschließlich KOMMENDE Termine in der Liste stehen (Prüfbericht 19.11.)
    nur_kommende = (von >= heute) if von else (not bis and zeitraum == "kommend")
    if _ist_gastansicht() and not q and nur_kommende:
        liste = termin_extras.buendeln(liste)
    # Zähler im Titelbild: echte Termine, nicht Karten (gebündelte zählen mit ihrer Anzahl)
    anzahl_termine = sum((t.get("wiederkehrend") or {}).get("anzahl", 1) for t in liste)
    # Highlights oben in der Liste nur in der ungefilterten Ansicht – maskierte Termine
    # haben nach _liste_fuer_gast kein highlight mehr und fallen hier heraus.
    ungefiltert = not q and not kat and not gruppe and not ziele and zeitraum == "kommend" and not bis and (not von or von == heute)
    highlights = termin_extras.highlights_einmal(
        [t for t in liste if t["highlight"] and not t["abgesagt"] and t["datum"] >= heute], 3) if ungefiltert else []
    return render_template("portal/termine.html", aktiv="termine", termine=liste, schicht_info=info,
                           gemerkt=_meine_merkliste(), highlights=highlights, ziele=ziele, ziel_knoepfe=ziel_knoepfe,
                           anzahl_termine=anzahl_termine, gruppe=gruppe, kat_knoepfe=kat_knoepfe,
                           schnell=termin_extras.schnellfilter(),
                           kategorien=kategorien, q=q, kat=kat, zeitraum=zeitraum,
                           von=von, bis=bis, heute_iso=heute)


MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]
WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
WOCHENTAGE_LANG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


@bp.route("/kalender")
def kalender():
    # Auch für Gäste/Freunde zugänglich – zeigt nur Termine, keine internen Daten.
    vid = aktiver_verein_id()
    heute = date.today()
    ansicht = request.args.get("ansicht", "monat")
    if ansicht not in ("monat", "woche"):
        ansicht = "monat"
    try:
        ref = date.fromisoformat(request.args.get("d", "")) if request.args.get("d") else heute
    except ValueError:
        ref = heute

    if ansicht == "monat":
        wochen = pycal.Calendar(firstweekday=0).monthdatescalendar(ref.year, ref.month)
        titel = f"{MONATE[ref.month - 1]} {ref.year}"
        erster = date(ref.year, ref.month, 1)
        prev = (erster - timedelta(days=1)).replace(day=1)
        letzter = date(ref.year, ref.month, pycal.monthrange(ref.year, ref.month)[1])
        nxt = (letzter + timedelta(days=1))
    else:
        start = ref - timedelta(days=ref.weekday())
        wochen = [[start + timedelta(days=i) for i in range(7)]]
        ende = start + timedelta(days=6)
        titel = f"{start.day}.{start.month}. – {ende.day}.{ende.month}.{ende.year}"
        prev = start - timedelta(days=7)
        nxt = start + timedelta(days=7)

    von, bis = wochen[0][0], wochen[-1][-1]
    conn = get_db()
    rows = conn.execute(
        "SELECT t.id, t.titel, t.datum, t.uhrzeit, t.uhrzeit_bis, t.nur_mitglieder, k.farbe, k.name AS kategorie "
        "FROM termine t LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
        "WHERE t.verein_id=? AND t.aktiv=1 AND t.datum>=? AND t.datum<=? ORDER BY t.datum, t.uhrzeit",
        (vid, von.isoformat(), bis.isoformat())).fetchall()
    conn.close()
    rows = _liste_fuer_gast(rows)
    nach_tag = {}
    for r in rows:
        nach_tag.setdefault(r["datum"], []).append(r)

    grid = []
    for woche in wochen:
        zeile = []
        for d in woche:
            zeile.append({"iso": d.isoformat(), "tag": d.day,
                          "wtag": WOCHENTAGE_LANG[d.weekday()],
                          "im_monat": ansicht == "woche" or d.month == ref.month,
                          "heute": d == heute, "termine": nach_tag.get(d.isoformat(), [])})
        grid.append(zeile)

    return render_template("portal/kalender.html", aktiv="termine", ansicht=ansicht, grid=grid,
                           titel=titel, wochentage=WOCHENTAGE, prev=prev.isoformat(), nxt=nxt.isoformat(),
                           heute_iso=heute.isoformat(), agenda=rows)


@bp.route("/termin/<int:tid>")
@bp.route("/termin/<int:tid>-<slug>")
def termin(tid, slug=None):
    vid = aktiver_verein_id()
    conn = get_db()
    t = conn.execute(
        "SELECT t.*, k.farbe, k.name AS kategorie, k.bild AS kat_bild FROM termine t "
        "LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
        "WHERE t.id=? AND t.verein_id=? AND t.aktiv=1", (tid, vid)).fetchone()
    if not t:
        conn.close()
        abort(404)
    schichten = conn.execute(
        "SELECT * FROM schichten WHERE termin_id=? AND verein_id=? ORDER BY zeit_von", (tid, vid)).fetchall()
    slots = {}
    for s in schichten:
        slots[s["id"]] = conn.execute(
            "SELECT ss.*, r.name AS rolle_name, r.farbe AS rolle_farbe, mg.vorname, mg.nachname "
            "FROM schicht_slots ss LEFT JOIN rollen r ON r.id=ss.rolle_id "
            "LEFT JOIN mitglieder mg ON mg.id=ss.mitglied_id WHERE ss.schicht_id=? ORDER BY ss.id",
            (s["id"],)).fetchall()
    # RSVP: Zu-/Absagen + erwartete Besucher:innen
    rsvp_rows = conn.execute(
        "SELECT r.status, r.gaeste, r.mitglied_id, mg.vorname, mg.nachname "
        "FROM termin_rsvp r LEFT JOIN mitglieder mg ON mg.id=r.mitglied_id "
        "WHERE r.termin_id=? AND r.verein_id=?", (tid, vid)).fetchall()
    m = _mein_mitglied()
    gast = _ist_gastansicht()
    # Belegte Plätze (für Ausgebucht-Hinweis bei Veranstaltungen mit Anmeldung)
    va_belegt = conn.execute(
        "SELECT COUNT(*) c FROM veranstaltung_teilnehmer tn JOIN veranstaltung_anmeldung a ON a.id=tn.anmeldung_id "
        "WHERE a.termin_id=? AND a.storniert=0 AND a.warteliste=0", (tid,)).fetchone()["c"]
    rsvp = {"komme": 0, "gaeste": 0, "vielleicht": 0, "abgesagt": 0, "namen": [], "meine": None, "meine_gaeste": 0}
    for r in rsvp_rows:
        if r["status"] in rsvp:
            rsvp[r["status"]] += 1
        if r["status"] == "komme":
            rsvp["gaeste"] += r["gaeste"] or 0
            rsvp["namen"].append(f"{r['vorname']} {(r['nachname'] or '')[:1]}.")
        if m and r["mitglied_id"] == m["id"]:
            rsvp["meine"] = r["status"]
            rsvp["meine_gaeste"] = r["gaeste"] or 0
    rsvp["erwartet"] = rsvp["komme"] + rsvp["gaeste"]
    # Bewertungen (für vergangene Veranstaltungen)
    bew = conn.execute(
        "SELECT b.sterne, b.kommentar, b.mitglied_id, mg.vorname, mg.nachname "
        "FROM bewertungen b LEFT JOIN mitglieder mg ON mg.id=b.mitglied_id "
        "WHERE b.termin_id=? AND b.verein_id=? ORDER BY b.id DESC", (tid, vid)).fetchall()
    bew_schnitt = round(sum(x["sterne"] for x in bew) / len(bew), 1) if bew else 0
    meine_bew = next((x for x in bew if m and x["mitglied_id"] == m["id"]), None)
    ist_vergangen = (t["datum"] or "") < date.today().isoformat()
    # Foto-Galerie
    fotos = conn.execute(
        "SELECT f.id, f.dateiname, f.beschreibung, f.mitglied_id, mg.vorname, mg.nachname "
        "FROM galerie_fotos f LEFT JOIN mitglieder mg ON mg.id=f.mitglied_id "
        "WHERE f.termin_id=? AND f.verein_id=? ORDER BY f.id", (tid, vid)).fetchall()
    # Serien-Zusammenhang (Badge + „nächster Termin"-Hinweis in der App)
    from .termine import _serie_info
    serie_info = _serie_info(conn, t)
    # Eigene Anmeldung(en) zu dieser Veranstaltung – für die Fotoerlaubnis-Karte
    meine_va = []
    adressen = _meine_adressen(m)
    if adressen:
        platzhalter = ",".join("?" for _ in adressen)
        meine_va = conn.execute(
            "SELECT token, anmelder_name, COALESCE(foto_status,'') AS foto_status, "
            "COALESCE(foto_social,'') AS foto_social "
            "FROM veranstaltung_anmeldung WHERE termin_id=? AND verein_id=? AND storniert=0 "
            "AND token!='' AND lower(email) IN (" + platzhalter + ")",
            (tid, vid, *sorted(adressen))).fetchall()
    einlass_ok = bool(t["anmeldung_aktiv"]) and _einlass_erlaubt(conn, tid)
    from .veranstaltungen import einlass_auto_schliessen, einlass_schwelle
    einlass_laeuft = einlass_auto_schliessen(conn, t)
    _schwelle = einlass_schwelle(t)
    einlass_zu = bool(not einlass_laeuft and _schwelle
                      and t["datum"] == date.today().isoformat()
                      and datetime.now() >= _schwelle)
    # Mehr entdecken (21.11.2026): Reihe, Ähnliches, „So war's“ – Gäste nur Öffentliches
    from .. import termin_entdecken as te
    weitere_reihe = te.reihe(conn, t, vid, gast, _fuer_gast)
    aehnliche = te.aehnliche(conn, t, vid, gast, _fuer_gast, ausser=[x["id"] for x in weitere_reihe])
    so_wars = te.fotos_oeffentlich(conn, tid, vid) if ist_vergangen and gast else []
    conn.close()
    # Die Vorlage bekommt den Termin als „termin" – der Name „t" gehört in allen
    # Vorlagen der Mitglieder-App der Übersetzungsfunktion (app/sprache.py).
    termin_dict = _fuer_gast(t)
    termin_dict["einlass_start"] = einlass_laeuft
    # Plakate interner Termine (Plakat = Klarname!) nicht als Datei ins
    # Teilen-Blatt legen – dieselbe Regel wie _bild_spalte in routes/termine.py.
    # Für Gäste ist bild ohnehin geleert; das Flag schützt den Mitglieder-Share.
    bild_teilbar = bool(termin_dict.get("bild")) and not (
        termin_maske.ist_intern(termin_dict) or termin_maske.ist_nicht_oeffentlich(termin_dict))
    # Suchmaschinen (app/seo.py): sprechende Adresse als canonical, falscher/alter
    # Name in der URL → 301; verborgene Termine nie indexieren und nie mit Namen.
    from .. import seo
    einst_seo = get_einstellungen()
    seo_oeffentlich = seo.termin_oeffentlich(t) and not termin_dict.get("verborgen")
    kanonisch = seo.termin_url(termin_dict if seo_oeffentlich else {"id": tid, "nur_mitglieder": 1},
                               extern=True)
    if slug is not None and (not seo_oeffentlich or slug != seo.slug(termin_dict.get("titel"))):
        return redirect(kanonisch, code=301)
    if not seo_oeffentlich:
        g.seo_noindex = True
    seo_ld = None
    if seo_oeffentlich and seo.indexierbar(einst_seo):
        frei = None
        if termin_dict.get("max_teilnehmer"):
            frei = max(0, int(termin_dict["max_teilnehmer"]) - int(va_belegt or 0))
        seo_ld = seo.event_jsonld(termin_dict, einst_seo, url=kanonisch, frei=frei)
    return render_template("portal/termin_detail.html", aktiv="termine", termin=termin_dict,
                           seo_kanonisch=kanonisch, seo_ld=seo_ld,
                           seo_beschreibung=seo.meta_beschreibung(termin_dict, einst_seo),
                           bild_teilbar=bild_teilbar,
                           einlass_ok=einlass_ok, einlass_zu=einlass_zu,
                           schichten=schichten, slots=slots, rsvp=rsvp, zugeordnet=bool(m),
                           bew=bew, bew_schnitt=bew_schnitt, meine_bew=meine_bew, ist_vergangen=ist_vergangen,
                           fotos=fotos, ist_admin=_ist_admin(), meine_mid=(m["id"] if m else None),
                           gast=gast, va_belegt=va_belegt, meine_va=meine_va,
                           serie_info=serie_info, gemerkt=_meine_merkliste(),
                           relativ=termin_extras.relativ(termin_dict.get("datum")),
                           weitere_reihe=weitere_reihe, aehnliche=aehnliche, so_wars=so_wars,
                           wetter_termin=te.wetter(einst_seo, termin_dict), heute_iso=date.today().isoformat(),
                           frage_mail=((einst_seo["email"] if einst_seo and "email" in einst_seo.keys() else "") or "").strip())


@bp.route("/termin/<int:tid>/foto/<int:fid>")
def termin_foto_oeffentlich(tid, fid):
    """„So war's“ (21.11.2026): ein Foto aus dem Fotoarchiv öffentlich ausliefern –
    nur mit Veröffentlichungs- UND Social-Media-Freigabe und nur bei öffentlichen Terminen."""
    from .. import termin_entdecken as te
    from .einstellungen import _bild_ohne_skript
    conn = get_db()
    f = te.foto_oeffentlich_erlaubt(conn, fid, aktiver_verein_id())
    conn.close()
    if not f or f["termin_id"] != tid or not f["dateiname"]:
        abort(404)
    return _bild_ohne_skript(send_from_directory(_galerie_dir(), os.path.basename(f["dateiname"]), max_age=3600))


@bp.route("/termin/<int:tid>/bewerten", methods=["POST"])
@login_required
def termin_bewerten(tid):
    m = _mein_mitglied()
    if not m:
        return redirect(url_for("portal.termin", tid=tid))
    vid = aktiver_verein_id()
    sterne = request.form.get("sterne", type=int)
    if not sterne or not 1 <= sterne <= 5:
        return redirect(url_for("portal.termin", tid=tid))
    conn = get_db()
    conn.execute("DELETE FROM bewertungen WHERE termin_id=? AND mitglied_id=?", (tid, m["id"]))
    conn.execute("INSERT INTO bewertungen (verein_id, termin_id, mitglied_id, sterne, kommentar, erstellt_am) "
                 "VALUES (?,?,?,?,?,?)", (vid, tid, m["id"], sterne, request.form.get("kommentar", "").strip(),
                                         now_iso()))
    conn.commit()
    conn.close()
    flash("Danke für deine Bewertung!", "erfolg")
    return redirect(url_for("portal.termin", tid=tid))


# -------------------------- Kino / Beitrag / Spenden ----------------------
def _kino_titel():
    """Vereins-eigener Name fürs Kino (Einstellungen), sonst neutral."""
    e = get_einstellungen()
    eigen = ((e["kino_name"] if e and "kino_name" in e.keys() else "") or "").strip()
    return eigen or "Kino-Programm"


@bp.route("/kino")
def kino():
    # Auch für Gäste/Freunde: zeigt nur das öffentliche Kino-Programm.
    vid = aktiver_verein_id()
    heute = date.today().isoformat()
    conn = get_db()
    # Interne Vorführungen (Mitglieder-Haken) bleiben für Gäste/Freund:innen
    # komplett draußen – maskierte Karten ohne Filmtitel nützen niemandem.
    kino_gast = " AND COALESCE(t.nur_mitglieder,0)=0" if _ist_gastansicht() else ""
    filme = conn.execute(
        "SELECT t.*, k.name AS kategorie FROM termine t JOIN termin_kategorien k ON k.id=t.kategorie_id "
        "WHERE t.verein_id=? AND t.aktiv=1 AND t.datum>=? AND k.name LIKE '%Kino%'"
        f"{kino_gast} ORDER BY t.datum, t.uhrzeit", (vid, heute)).fetchall()
    conn.close()
    return render_template("portal/kino.html", aktiv="termine", filme=filme, kino_titel=_kino_titel())


@bp.route("/beitrag")
@login_required
def beitrag():
    m = _mein_mitglied()
    einst = get_einstellungen()
    schichten_an = bool(einst and einst.get("modul_schichten"))
    rows, offen, spenden, spenden_summe = [], 0, [], 0
    ea_anzahl, ea_stunden, ea_jahr = 0, 0, 0
    if m:
        conn = get_db()
        rows = conn.execute("SELECT * FROM beitraege WHERE verein_id=? AND mitglied_id=? ORDER BY jahr DESC, id DESC",
                            (aktiver_verein_id(), m["id"])).fetchall()
        try:
            spenden = conn.execute(
                "SELECT datum, betrag, zweck, art FROM spenden WHERE verein_id=? AND mitglied_id=? "
                "ORDER BY datum DESC, id DESC", (aktiver_verein_id(), m["id"])).fetchall()
            spenden_summe = sum((s["betrag"] or 0) for s in spenden)
        except Exception:  # noqa: BLE001 – Tabelle fehlt in sehr alten Datenbeständen
            pass
        if schichten_an:
            ea_anzahl, ea_stunden, ea_jahr = _ehrenamt(conn, aktiver_verein_id(), m["id"])
        conn.close()
        offen = sum((r["betrag"] or 0) for r in rows if not r["bezahlt"])
    return render_template("portal/beitrag.html", aktiv="profil", mitglied=m, rows=rows, offen=offen,
                           spenden=spenden, spenden_summe=spenden_summe,
                           ea_anzahl=ea_anzahl, ea_stunden=ea_stunden, ea_jahr=ea_jahr,
                           schichten_an=schichten_an)


@bp.route("/fotoerlaubnis")
@login_required
def fotoerlaubnis():
    """Meine Fotoerlaubnisse: alle eigenen Veranstaltungs-Anmeldungen mit Antwort-Status.

    Bewusst auch vergangene Veranstaltungen – ein Widerruf muss jederzeit
    möglich sein, nicht nur bis zum Veranstaltungstag."""
    adressen = _meine_adressen()
    rows = []
    if adressen:
        conn = get_db()
        platzhalter = ",".join("?" for _ in adressen)
        rows = conn.execute(
            "SELECT a.token, a.anmelder_name, COALESCE(a.foto_status,'') AS foto_status, "
            "COALESCE(a.foto_social,'') AS foto_social, COALESCE(a.foto_antwort_am,'') AS foto_antwort_am, "
            "t.id AS termin_id, t.titel, t.datum, t.uhrzeit "
            "FROM veranstaltung_anmeldung a JOIN termine t ON t.id=a.termin_id "
            "WHERE a.verein_id=? AND a.storniert=0 AND a.token!='' "
            "AND lower(a.email) IN (" + platzhalter + ") "
            "ORDER BY t.datum DESC, t.uhrzeit DESC",
            (aktiver_verein_id(), *sorted(adressen))).fetchall()
        conn.close()
    offen = sum(1 for r in rows if not r["foto_status"])
    return render_template("portal/fotoerlaubnis.html", aktiv="profil", rows=rows, offen=offen)


@bp.route("/mitteilungen", methods=["GET", "POST"])
@login_required
def mitteilungen():
    """Themenauswahl für Push-Mitteilungen. Standard: alles an – gespeichert
    werden nur die abgewählten Themen (benutzer.push_aus)."""
    verwaltung = getattr(current_user, "rolle", "") in ("admin", "oeffentlichkeit")
    themen = [t for t in push.THEMEN if verwaltung or not t[3]]
    if request.method == "POST":
        aus = [schluessel for schluessel, _, _, _ in themen
               if not request.form.get(f"thema_{schluessel}")]
        benutzer_aktualisieren(int(current_user.id), push_aus=",".join(aus))
        flash("Gespeichert – du bekommst nur noch die angehakten Mitteilungen.", "erfolg")
        return redirect(url_for("portal.mitteilungen"))
    sys_conn = get_system_db()
    karte = sys_conn.execute("SELECT COALESCE(push_aus,'') AS push_aus FROM benutzer WHERE id=?",
                             (int(current_user.id),)).fetchone()
    sys_conn.close()
    aus = {t.strip() for t in (karte["push_aus"] if karte else "").split(",") if t.strip()}
    return render_template("portal/mitteilungen.html", aktiv="profil",
                           themen=themen, aus=aus, verwaltung=verwaltung)


@bp.route("/neuigkeiten")
@login_required
def was_neu():
    """„Was ist neu": die neuen Funktionen der App, in Klartext.

    Öffnen setzt den Merker – die Startseiten-Kachel verschwindet dann,
    bis wieder etwas dazukommt."""
    from .. import neuigkeiten as _neuigkeiten
    einst = get_einstellungen()
    vorstand = getattr(current_user, "rolle", "") in ("admin", "oeffentlichkeit")
    sys_conn = get_system_db()
    zeile = sys_conn.execute("SELECT COALESCE(neuigkeiten_gesehen_am,'') g FROM benutzer WHERE id=?",
                             (int(current_user.id),)).fetchone()
    vorher = (zeile["g"] or "") if zeile else ""
    sys_conn.execute("UPDATE benutzer SET neuigkeiten_gesehen_am=? WHERE id=?",
                     (now_iso(), int(current_user.id)))
    sys_conn.commit()
    sys_conn.close()
    eintraege = _neuigkeiten.fuer_anzeige(einst, vorstand=vorstand)
    return render_template("portal/neuigkeiten.html", aktiv="home",
                           eintraege=eintraege, vorher=vorher[:10])


# ------------------------------ Foto-Galerie ------------------------------
def _galerie_dir():
    from .einstellungen import LOGO_DIR
    pfad = os.path.join(os.path.dirname(LOGO_DIR), "galerie")
    os.makedirs(pfad, exist_ok=True)
    return pfad


def _foto_speichern(tid, datei):
    """Speichert ein hochgeladenes Foto verkleinert (max. 1600px, JPEG) und gibt den Dateinamen zurück."""
    daten = datei.read()
    if not daten:
        return None
    fname = f"t{tid}_{uuid.uuid4().hex[:10]}.jpg"
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(daten))
        im = ImageOps.exif_transpose(im).convert("RGB")  # Handy-Drehung berücksichtigen
        im.thumbnail((1600, 1600), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82)
        daten = buf.getvalue()
    except Exception:  # noqa: BLE001 – ohne Pillow: Rohbild ablegen
        pass
    with open(os.path.join(_galerie_dir(), fname), "wb") as f:
        f.write(daten)
    return fname


@bp.route("/galerie")
@login_required
def galerie():
    vid = aktiver_verein_id()
    conn = get_db()
    alben = conn.execute(
        "SELECT t.id, t.titel, t.datum, COUNT(f.id) AS anzahl, MAX(f.id) AS cover "
        "FROM galerie_fotos f JOIN termine t ON t.id=f.termin_id "
        "WHERE f.verein_id=? GROUP BY t.id, t.titel, t.datum ORDER BY t.datum DESC", (vid,)).fetchall()
    conn.close()
    return render_template("portal/galerie.html", aktiv="termine", alben=alben)


@bp.route("/foto/<int:fid>")
@login_required
def galerie_foto(fid):
    conn = get_db()
    row = conn.execute("SELECT dateiname FROM galerie_fotos WHERE id=? AND verein_id=?",
                       (fid, aktiver_verein_id())).fetchone()
    conn.close()
    if not row or not row["dateiname"]:
        abort(404)
    return send_from_directory(_galerie_dir(), row["dateiname"])


@bp.route("/termin/<int:tid>/foto", methods=["POST"])
@login_required
def termin_foto(tid):
    vid = aktiver_verein_id()
    m = _mein_mitglied()
    conn = get_db()
    da = conn.execute("SELECT 1 FROM termine WHERE id=? AND verein_id=?", (tid, vid)).fetchone()
    if not da:
        conn.close()
        abort(404)
    # Pflicht-Bestätigung: eigenes Foto, Einverständnis der Abgebildeten, Nutzung für Öffentlichkeitsarbeit
    if not request.form.get("freigabe"):
        conn.close()
        flash("Bitte bestätige die Foto-Freigabe (eigenes Foto, Einverständnis der Abgebildeten).", "fehler")
        return redirect(url_for("portal.termin", tid=tid))
    beschr = request.form.get("beschreibung", "").strip()
    n = 0
    for datei in request.files.getlist("fotos"):
        if not datei or not datei.filename:
            continue
        fname = _foto_speichern(tid, datei)
        if fname:
            conn.execute("INSERT INTO galerie_fotos (verein_id, termin_id, mitglied_id, dateiname, beschreibung, "
                         "erstellt_am, freigabe_ok) VALUES (?,?,?,?,?,?,1)",
                         (vid, tid, m["id"] if m else None, fname, beschr, now_iso()))
            n += 1
    conn.commit()
    conn.close()
    flash(f"{n} Foto{'s' if n != 1 else ''} hochgeladen – danke!" if n else "Bitte mindestens ein Foto auswählen.",
          "erfolg" if n else "fehler")
    return redirect(url_for("portal.termin", tid=tid) + "#galerie")


def foto_entfernen(conn, vid, row):
    """Ein Galerie-Foto endgültig löschen: Datei, Datensatz und Verweise aus
    ÖA-Beiträgen (Beitragsbild) – nur im eigenen Verein. Ruft commit NICHT auf."""
    try:
        os.remove(os.path.join(_galerie_dir(), os.path.basename(row["dateiname"])))
    except OSError:
        pass
    conn.execute("DELETE FROM galerie_fotos WHERE id=? AND verein_id=?", (row["id"], vid))
    try:
        conn.execute("UPDATE oea_beitraege SET bild_foto_id=NULL WHERE bild_foto_id=? AND verein_id=?", (row["id"], vid))
    except Exception:  # noqa: BLE001 – ältere Datenbanken ohne die Spalte
        pass


def _darf_foto_loeschen(row):
    m = _mein_mitglied()
    return _ist_admin() or bool(m and row["mitglied_id"] and row["mitglied_id"] == m["id"])


@bp.route("/foto/<int:fid>/loeschen", methods=["POST"])
@login_required
def foto_loeschen(fid):
    vid = aktiver_verein_id()
    m = _mein_mitglied()
    conn = get_db()
    row = conn.execute("SELECT * FROM galerie_fotos WHERE id=? AND verein_id=?", (fid, vid)).fetchone()
    if not row:
        conn.close()
        return redirect(url_for("portal.galerie"))
    tid = row["termin_id"]
    if _darf_foto_loeschen(row):
        foto_entfernen(conn, vid, row)
        conn.commit()
        flash("Foto gelöscht.", "erfolg")
    else:
        flash("Du kannst nur deine eigenen Fotos löschen.", "fehler")
    conn.close()
    return redirect(url_for("portal.termin", tid=tid) + "#galerie")


@bp.route("/termin/<int:tid>/rsvp", methods=["POST"])
@login_required
def termin_rsvp(tid):
    m = _mein_mitglied()
    if not m:
        flash("Zum An-/Abmelden muss dein Konto einem Mitglied zugeordnet sein.", "fehler")
        return redirect(url_for("portal.termin", tid=tid))
    vid = aktiver_verein_id()
    status = request.form.get("status")
    gaeste = request.form.get("gaeste", type=int) or 0
    if status not in ("komme", "vielleicht", "abgesagt"):
        return redirect(url_for("portal.termin", tid=tid))
    conn = get_db()
    da = conn.execute("SELECT 1 FROM termine WHERE id=? AND verein_id=?", (tid, vid)).fetchone()
    if da:
        conn.execute("DELETE FROM termin_rsvp WHERE termin_id=? AND mitglied_id=?", (tid, m["id"]))
        conn.execute("INSERT INTO termin_rsvp (verein_id, termin_id, mitglied_id, status, gaeste, erstellt_am) "
                     "VALUES (?,?,?,?,?,?)", (vid, tid, m["id"], status, max(0, gaeste) if status == "komme" else 0,
                                             now_iso()))
        conn.commit()
        flash("Danke für deine Rückmeldung!", "erfolg")
    conn.close()
    return redirect(url_for("portal.termin", tid=tid))


EINWILLIGUNGEN = ["sepa_ok", "dsgvo_ok", "kontakt_ok", "geb_sichtbar"]


def _avatar_dir():
    from .einstellungen import LOGO_DIR
    pfad = os.path.join(os.path.dirname(LOGO_DIR), "avatare")
    os.makedirs(pfad, exist_ok=True)
    return pfad


def _avatar_speichern(mid, datei):
    """Speichert das hochgeladene Profilbild (quadratisch, verkleinert) und gibt den Dateinamen zurück."""
    daten = datei.read()
    if not daten:
        return None
    fname = f"m{mid}.jpg"
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(daten)).convert("RGB")
        s = min(im.size)
        im = im.crop(((im.width - s) // 2, (im.height - s) // 2,
                      (im.width + s) // 2, (im.height + s) // 2)).resize((400, 400), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        daten = buf.getvalue()
    except Exception:  # noqa: BLE001 – ohne Pillow/HEIC-Fehler: Rohbild ablegen
        pass
    with open(os.path.join(_avatar_dir(), fname), "wb") as f:
        f.write(daten)
    return fname


@bp.route("/avatar/<int:mid>")
@login_required
def avatar(mid):
    conn = get_db()
    row = conn.execute("SELECT avatar_dateiname FROM mitglieder WHERE id=? AND verein_id=?",
                       (mid, aktiver_verein_id())).fetchone()
    conn.close()
    if not row or not row["avatar_dateiname"]:
        abort(404)
    return send_from_directory(_avatar_dir(), row["avatar_dateiname"])


# ------------------- Gesundheitszeugnis (für Schichten) --------------------
def _gz_dir():
    from .einstellungen import LOGO_DIR
    pfad = os.path.join(os.path.dirname(LOGO_DIR), "gesundheitszeugnisse")
    os.makedirs(pfad, exist_ok=True)
    return pfad


def _gz_speichern(mid, datei):
    """Speichert das Gesundheitszeugnis (PDF oder Bild) und gibt den Dateinamen zurück."""
    endung = os.path.splitext(datei.filename)[1].lower()
    if endung not in (".pdf", ".jpg", ".jpeg", ".png", ".heic", ".webp"):
        return None
    daten = datei.read()
    if not daten:
        return None
    fname = f"gz_m{mid}{endung}"
    # alte Datei (evtl. andere Endung) entfernen
    for alt in os.listdir(_gz_dir()):
        if alt.startswith(f"gz_m{mid}."):
            try:
                os.remove(os.path.join(_gz_dir(), alt))
            except OSError:
                pass
    with open(os.path.join(_gz_dir(), fname), "wb") as f:
        f.write(daten)
    return fname


@bp.route("/gesundheitszeugnis")
@login_required
def gesundheitszeugnis():
    """Zeigt das EIGENE Gesundheitszeugnis (Admins dürfen per ?mid= auch andere sehen)."""
    m = _mein_mitglied()
    mid = request.args.get("mid", type=int)
    if mid and not _ist_admin():
        abort(403)
    if not mid:
        mid = m["id"] if m else None
    if not mid:
        abort(404)
    conn = get_db()
    row = conn.execute("SELECT gz_dateiname FROM mitglieder WHERE id=? AND verein_id=?",
                       (mid, aktiver_verein_id())).fetchone()
    conn.close()
    if not row or not row["gz_dateiname"]:
        abort(404)
    return send_from_directory(_gz_dir(), row["gz_dateiname"])


def _gz_vorhanden(m):
    try:
        return bool(m and "gz_dateiname" in m.keys() and m["gz_dateiname"])
    except Exception:  # noqa: BLE001
        return False


@bp.route("/profil", methods=["GET", "POST"])
@login_required
def profil():
    m = _mein_mitglied()
    conn = get_db()
    spalten = _mitglieder_spalten(conn)
    felder = [f for f in EDITIERBAR if f in spalten]
    # sepa_ok und kontakt_ok haben eigene Abschnitte (Mandat bzw. Kommunikationswege) –
    # daher hier NICHT als allgemeine Einwilligung anzeigen (sonst Doppelabfrage).
    einw = [e for e in EINWILLIGUNGEN if e in spalten and e not in ("sepa_ok", "kontakt_ok")]
    if request.method == "POST":
        if not m:
            conn.close()
            flash("Wir konnten dich noch keinem Mitglied zuordnen. Bitte beim Vorstand melden.", "fehler")
            return redirect(url_for("portal.profil"))
        vid = aktiver_verein_id()
        setz = [f + "=?" for f in felder] + [e + "=?" for e in einw]
        werte = [request.form.get(f, "").strip() for f in felder] + [1 if request.form.get(e) else 0 for e in einw]
        # Hauptadresse: Die gewählte Adresse wird mitglieder.email (dorthin gehen
        # Rundmails); die bisherige Hauptadresse rückt in die weiteren Adressen.
        haupt = (request.form.get("hauptadresse") or "").strip()
        if haupt and "email" in felder and "email_weitere" in _mitglieder_spalten(conn):
            eingegeben = (request.form.get("email") or "").strip()
            weitere = [e.strip() for e in (m["email_weitere"] or "").splitlines() if e.strip()]
            if haupt.lower() != eingegeben.lower() and haupt.lower() in {w.lower() for w in weitere}:
                weitere = [w for w in weitere if w.lower() != haupt.lower()]
                if eingegeben:
                    weitere.insert(0, eingegeben)
                werte[felder.index("email")] = haupt
                setz.append("email_weitere=?")
                werte.append("\n".join(weitere))
        # Kommunikationswege (Mehrfachauswahl → Komma-getrennt).
        # „erlaubt" IST zugleich die Kontakt-Einwilligung → kontakt_ok davon ableiten.
        kontakt_erlaubt = False
        for kf in ("komm_genutzt", "komm_erlaubt"):
            if kf in spalten:
                gewaehlt = request.form.getlist(kf)
                setz.append(kf + "=?")
                werte.append(",".join(gewaehlt))
                if kf == "komm_erlaubt" and gewaehlt:
                    kontakt_erlaubt = True
        if "kontakt_ok" in spalten:
            setz.append("kontakt_ok=?")
            werte.append(1 if kontakt_erlaubt else 0)
        # Gesprochene Sprachen (Checkboxen + Freitext) – nur wenn das Formular
        # den Block enthielt (Marker), sonst würde ein altes Formular sie leeren.
        if "sprachen" in spalten and request.form.get("sprachen_dabei"):
            teile = []
            for wert in request.form.getlist("sprachen"):
                for s in wert.split(","):
                    s = s.strip()
                    if s and s not in teile:
                        teile.append(s)
            setz.append("sprachen=?")
            werte.append(",".join(teile))
        # Lastschriftmandat (Selbstbedienung): Häkchen + optional unterschriebenes Mandat hochladen
        mandat_hochgeladen = False
        if "mandat_dateiname" in spalten:
            mdatei = request.files.get("mandat_datei")
            if mdatei and mdatei.filename:
                from .mitglieder import _mandat_speichern
                fname = _mandat_speichern(mdatei)
                if fname:
                    setz.append("mandat_dateiname=?")
                    werte.append(fname)
                    mandat_hochgeladen = True
        mandat_an = bool(request.form.get("mandat_vorhanden") or mandat_hochgeladen)
        if "mandat_vorhanden" in spalten:
            setz.append("mandat_vorhanden=?")
            werte.append(1 if mandat_an else 0)
        # Das Mandat deckt zugleich die SEPA-Einwilligung ab → sepa_ok mitführen.
        if "sepa_ok" in spalten:
            setz.append("sepa_ok=?")
            werte.append(1 if mandat_an else 0)
        # Abwesenheit (Selbstbedienung): an/aus + bis-Datum + Notiz
        for af in ("abwesend", "abwesend_von", "abwesend_bis", "abwesend_notiz"):
            if af in spalten:
                setz.append(af + "=?")
                if af == "abwesend":
                    werte.append(1 if request.form.get("abwesend") else 0)
                else:
                    werte.append(request.form.get(af, "").strip())
        # Profilbild
        datei = request.files.get("avatar")
        if datei and datei.filename and "avatar_dateiname" in spalten:
            fname = _avatar_speichern(m["id"], datei)
            if fname:
                setz.append("avatar_dateiname=?")
                werte.append(fname)
        # Gesundheitszeugnis (PDF/Bild) – Voraussetzung für den Schichten-Bereich
        gzdatei = request.files.get("gz_datei")
        if gzdatei and gzdatei.filename and "gz_dateiname" in spalten:
            fname = _gz_speichern(m["id"], gzdatei)
            if fname:
                setz += ["gz_dateiname=?", "gz_hochgeladen_am=?"]
                werte += [fname, now_iso()]
        if setz:
            conn.execute(f"UPDATE mitglieder SET {', '.join(setz)} WHERE id=? AND verein_id=?",
                         werte + [m["id"], vid])
            # Wer hier das Mandat erteilt, soll auch Referenz und Datum bekommen –
            # sonst fällt die Person beim Einzug still heraus.
            if mandat_an:
                from .. import mandate
                mandate.sicherstellen(conn, vid, m["id"], nur_wenn_lastschrift=False)
            conn.commit()
        conn.close()
        flash("Deine Daten wurden gespeichert.", "erfolg")
        return redirect(url_for("portal.profil"))
    komm_felder = [f for f in ("komm_genutzt", "komm_erlaubt") if f in spalten]
    conn.close()
    mandat_datei = m["mandat_dateiname"] if (m and "mandat_dateiname" in spalten and m["mandat_dateiname"]) else None
    einst = get_einstellungen()
    gz_datei = m["gz_dateiname"] if (m and "gz_dateiname" in spalten and m["gz_dateiname"]) else None
    gz_am = m["gz_hochgeladen_am"] if (m and "gz_hochgeladen_am" in spalten and m["gz_hochgeladen_am"]) else ""
    # Verdiente Abzeichen (Schichten/Stunden/Vereinsjahre) neben dem Namen zeigen
    abzeichen = []
    if m:
        try:
            conn2 = get_db()
            if einst and einst.get("modul_schichten"):
                anz, std, std_jahr = _ehrenamt(conn2, aktiver_verein_id(), m["id"])
            else:
                anz, std, std_jahr = 0, 0, 0
            abzeichen = _abzeichen(conn2, m, anz, std, std_jahr)
            conn2.close()
        except Exception:  # noqa: BLE001
            abzeichen = []
    return render_template("portal/profil.html", aktiv="profil", mitglied=m, felder=felder, einw=einw,
                           abzeichen=abzeichen, sprachen_auswahl=PINNWAND_SPRACHEN,
                           komm_felder=komm_felder, komm_wege=KOMM_WEGE,
                           mandat_da=("mandat_vorhanden" in spalten),
                           mandat_upload=("mandat_dateiname" in spalten), mandat_datei=mandat_datei,
                           gz_datei=gz_datei, gz_am=gz_am,
                           schichten_an=bool(einst and einst.get("modul_schichten")),
                           nc_verein=bool(einst and einst.get("ncc_server")),
                           passkeys=db.passkeys_liste(int(current_user.id)),
                           faceid_nc_frist=(db.faceid_nc_abgelaufen(db.benutzer_laden(int(current_user.id)))
                                            if (current_user.nc_benutzer or "").strip() else False))


@bp.route("/mandat")
@login_required
def mandat():
    """Zeigt dem Mitglied sein eigenes hinterlegtes SEPA-Mandat."""
    m = _mein_mitglied()
    if not m or not m["mandat_dateiname"]:
        flash("Es ist kein Mandat hinterlegt.", "info")
        return redirect(url_for("portal.profil"))
    return speicher.sende(get_einstellungen(), "mandate", m["mandat_dateiname"])


# ------------------------------- Dateiablage (nur Lesen) ------------------
def _dms_crumbs(relpfad):
    crumbs, akk = [], ""
    for teil in [t for t in (relpfad or "").strip("/").split("/") if t]:
        akk = (akk + "/" + teil).strip("/")
        crumbs.append({"name": teil, "pfad": akk})
    return crumbs


def _meine_gruppen_ids():
    """Gruppen-IDs des angemeldeten Mitglieds (für die Ordner-Sichtbarkeit)."""
    from .. import dateiablage as dms
    m = _mein_mitglied()
    return dms.mitglied_gruppen_ids(m["id"]) if m else []


@bp.route("/dateien")
@login_required
def dateien():
    einst = get_einstellungen()
    if not (einst and "modul_dms" in einst.keys() and einst["modul_dms"]):
        abort(404)
    from .. import dateiablage as dms
    vid = aktiver_verein_id()
    dms.struktur_sicherstellen(vid)
    relpfad = (request.args.get("pfad") or "").strip()
    sort = request.args.get("sort") if request.args.get("sort") in ("name", "datum", "groesse") else "name"
    inhalt = dms.liste(vid, relpfad, mitglied=True, sort=sort, gruppen_ids=_meine_gruppen_ids())
    if inhalt is None:
        return redirect(url_for("portal.dateien"))
    return render_template("portal/dateien.html", aktiv="dateien", relpfad=relpfad,
                           inhalt=inhalt, crumbs=_dms_crumbs(relpfad), sort=sort)


@bp.route("/dateien/vorschau")
@login_required
def dateien_vorschau():
    einst = get_einstellungen()
    if not (einst and "modul_dms" in einst.keys() and einst["modul_dms"]):
        abort(404)
    from .. import dateiablage as dms
    import os as _os
    vid = aktiver_verein_id()
    relpfad = request.args.get("pfad") or ""
    if dms.ist_vorstand(relpfad) or not dms.darf_sehen(relpfad, dms.zugriffsregeln(vid), _meine_gruppen_ids()):
        abort(403)
    ziel = dms.aufloesen(vid, relpfad)
    if not ziel or not _os.path.isfile(ziel):
        abort(404)
    # HTML/SVG aus der Ablage nie im Browser ausführen (Cross-Site-Scripting)
    import mimetypes as _mt
    from .. import speicher as _speicher
    typ = _mt.guess_type(ziel)[0] or ""
    if typ not in _speicher.ANZEIGBAR:
        return send_from_directory(_os.path.dirname(ziel), _os.path.basename(ziel),
                                   as_attachment=True, mimetype="application/octet-stream")
    # Passive Typen dürfen in die Vorschau – Kopfzeilen zentral (speicher.inline_schutz):
    # die alte Sandbox-CSP schaltete hier den PDF-Viewer ab (behoben 05.10.).
    antwort = send_from_directory(_os.path.dirname(ziel), _os.path.basename(ziel), mimetype=typ)
    return _speicher.inline_schutz(antwort)


@bp.route("/dateien/suche")
@login_required
def dateien_suche():
    einst = get_einstellungen()
    if not (einst and "modul_dms" in einst.keys() and einst["modul_dms"]):
        abort(404)
    from .. import dateiablage as dms
    begriff = (request.args.get("q") or "").strip()
    treffer = dms.suche(aktiver_verein_id(), begriff, mitglied=True,
                        gruppen_ids=_meine_gruppen_ids()) if begriff else []
    return render_template("portal/dateien_suche.html", aktiv="dateien", begriff=begriff, treffer=treffer)


@bp.route("/dateien/download")
@login_required
def dateien_download():
    einst = get_einstellungen()
    if not (einst and "modul_dms" in einst.keys() and einst["modul_dms"]):
        abort(404)
    from .. import dateiablage as dms
    import os as _os
    vid = aktiver_verein_id()
    relpfad = request.args.get("pfad") or ""
    # Vorstand-Ordner und gruppenbeschränkte Ordner für Unbefugte gesperrt
    if dms.ist_vorstand(relpfad) or not dms.darf_sehen(relpfad, dms.zugriffsregeln(vid), _meine_gruppen_ids()):
        abort(403)
    ziel = dms.aufloesen(vid, relpfad)
    if not ziel or not _os.path.isfile(ziel):
        abort(404)
    return send_from_directory(_os.path.dirname(ziel), _os.path.basename(ziel), as_attachment=True)


# ------------------------------- Schichten --------------------------------
# Abzeichen-Definitionen: je Verein konfigurierbar (Einstellungen → Abzeichen),
# Standard + Auswertung liegen in app/abzeichen.py
from .. import abzeichen as abz


def _dauer_stunden(von, bis):
    try:
        a = datetime.strptime((von or "")[:5], "%H:%M")
        b = datetime.strptime((bis or "")[:5], "%H:%M")
        d = (b - a).total_seconds() / 3600.0
        if d < 0:
            d += 24.0   # Über-Mitternacht-Schicht (22:00–02:00) zählt 4 h, nicht 0
        return d if d > 0 else 0.0
    except (ValueError, TypeError):
        return 0.0


def _ehrenamt(conn, vid, mid):
    """(Anzahl Schichten, Stunden gesamt, Stunden dieses Jahr)."""
    rows = conn.execute(
        "SELECT s.datum, COALESCE(NULLIF(ss.zeit_von,''), s.zeit_von) AS zeit_von, "
        "COALESCE(NULLIF(ss.zeit_bis,''), s.zeit_bis) AS zeit_bis, t.uhrzeit, t.uhrzeit_bis "
        "FROM schicht_slots ss JOIN schichten s ON s.id=ss.schicht_id "
        "LEFT JOIN termine t ON t.id=s.termin_id WHERE s.verein_id=? AND ss.mitglied_id=?",
        (vid, mid)).fetchall()
    jahr = str(date.today().year)
    gesamt = jahr_h = 0.0
    for r in rows:
        h = _dauer_stunden(r["zeit_von"] or r["uhrzeit"], r["zeit_bis"] or r["uhrzeit_bis"])
        gesamt += h
        if (r["datum"] or "").startswith(jahr):
            jahr_h += h
    return len(rows), round(gesamt, 1), round(jahr_h, 1)


def _mitglied_jahre(m):
    jahre = 0
    if m and "eintrittsdatum" in m.keys() and m["eintrittsdatum"]:
        try:
            jahre = (date.today() - date.fromisoformat(m["eintrittsdatum"][:10])).days // 365
        except ValueError:
            jahre = 0
    return jahre


def _abzeichen(conn, m, anzahl, stunden, stunden_jahr=0):
    """Verdiente Abzeichen; schickt Push für neu erreichte und merkt sie."""
    jahre = _mitglied_jahre(m)
    verdient = abz.verdiente(conn, aktiver_verein_id(), anzahl, stunden, jahre, stunden_jahr)
    if not m:
        return verdient
    try:
        alt = set(json.loads(m["abzeichen"]) if ("abzeichen" in m.keys() and m["abzeichen"]) else [])
    except (ValueError, TypeError):
        alt = set()
    neu = [(k, ic, tx) for (k, ic, tx) in verdient if k not in alt]
    if neu:
        conn.execute("UPDATE mitglieder SET abzeichen=? WHERE id=?",
                     (json.dumps(sorted(alt | {k for k, _, _ in verdient})), m["id"]))
        conn.commit()
        try:
            sys = get_system_db()
            bids = [r["id"] for r in sys.execute(
                "SELECT id FROM benutzer WHERE mitglied_id=? AND aktiv=1", (m["id"],)).fetchall()]
            sys.close()
            for k, ic, tx in neu:
                if bids:
                    push.senden(bids, "🎉 Abzeichen erreicht!", f"{ic} {tx} – danke für deinen Einsatz!",
                                url_for("portal.schichten"), thema="verein")
        except Exception:  # noqa: BLE001
            pass
    return verdient


@bp.route("/schichten")
@login_required
def schichten():
    einst = get_einstellungen()
    if not (einst and einst.get("modul_schichten")):
        abort(404)                     # Schichten sind für diesen Verein abgeschaltet
    vid = aktiver_verein_id()
    heute = date.today().isoformat()
    q = request.args.get("q", "").strip()
    zeitraum = request.args.get("zeitraum", "kommend")
    if zeitraum not in ("kommend", "alle", "vergangen"):
        zeitraum = "kommend"
    m = _mein_mitglied()
    mid = m["id"] if m else None
    # Schichten setzen ein hinterlegtes Gesundheitszeugnis voraus (Admins ausgenommen)
    if m and not _ist_admin() and not _gz_vorhanden(m):
        return render_template("portal/schichten_gesperrt.html", aktiv="schichten")
    conn = get_db()
    # Nur Gruppen mit Schicht-Berechtigung dürfen übernehmen (Einstellungen → Gruppen & Zählung)
    darf_uebernehmen = bool(m) and schicht_berechtigt(conn, einst, m["id"])

    # Gemeinsamer Filter (Zeitraum + Suchtext) für beide Listen
    filt, fparams = [], []
    if zeitraum == "kommend":
        filt.append("s.datum>=?")
        fparams.append(heute)
    elif zeitraum == "vergangen":
        filt.append("s.datum<?")
        fparams.append(heute)
    if q:
        filt.append("(s.titel LIKE ? OR s.ort LIKE ?)")
        fparams += [f"%{q}%", f"%{q}%"]
    filt_sql = (" AND " + " AND ".join(filt)) if filt else ""
    reihenfolge = "s.datum DESC, s.zeit_von DESC" if zeitraum == "vergangen" else "s.datum, s.zeit_von"

    meine, statistik, stunden_gesamt, stunden_jahr, abzeichen = [], 0, 0, 0, []
    if mid:
        meine = conn.execute(
            "SELECT ss.id AS slot_id, ss.tausch_gesucht, ss.erinnerung, s.titel, s.datum, "
            "COALESCE(NULLIF(ss.zeit_von,''), s.zeit_von) AS zeit_von, "
            "COALESCE(NULLIF(ss.zeit_bis,''), s.zeit_bis) AS zeit_bis, "
            "s.ort, s.termin_id, r.name AS rolle_name, r.beschreibung AS rolle_beschreibung, "
            "t.uhrzeit AS termin_uhrzeit, "
            "COALESCE(t.anmeldung_aktiv,0) AS termin_anmeldung "
            "FROM schicht_slots ss JOIN schichten s ON s.id=ss.schicht_id LEFT JOIN rollen r ON r.id=ss.rolle_id "
            "LEFT JOIN termine t ON t.id=s.termin_id "
            f"WHERE s.verein_id=? AND ss.mitglied_id=?{filt_sql} ORDER BY {reihenfolge}",
            [vid, mid] + fparams).fetchall()
        statistik, stunden_gesamt, stunden_jahr = _ehrenamt(conn, vid, mid)
        abzeichen = _abzeichen(conn, m, statistik, stunden_gesamt, stunden_jahr)
    offene = conn.execute(
        "SELECT ss.id AS slot_id, ss.mitglied_id, ss.tausch_gesucht, s.titel, s.datum, "
        "COALESCE(NULLIF(ss.zeit_von,''), s.zeit_von) AS zeit_von, "
        "COALESCE(NULLIF(ss.zeit_bis,''), s.zeit_bis) AS zeit_bis, "
        "s.ort, s.termin_id, r.name AS rolle_name, r.beschreibung AS rolle_beschreibung, "
        "mg.vorname, mg.nachname, t.uhrzeit AS termin_uhrzeit "
        "FROM schicht_slots ss JOIN schichten s ON s.id=ss.schicht_id LEFT JOIN rollen r ON r.id=ss.rolle_id "
        "LEFT JOIN mitglieder mg ON mg.id=ss.mitglied_id LEFT JOIN termine t ON t.id=s.termin_id "
        f"WHERE s.verein_id=?{filt_sql} AND (ss.mitglied_id IS NULL OR ss.tausch_gesucht=1) "
        f"ORDER BY {reihenfolge}", [vid] + fparams).fetchall()

    # Ampel: aktuelle (kommende) offene Schichten – unabhängig vom Filter
    offen_kommend = conn.execute(
        "SELECT COUNT(*) FROM schicht_slots ss JOIN schichten s ON s.id=ss.schicht_id "
        "WHERE s.verein_id=? AND s.datum>=? AND (ss.mitglied_id IS NULL OR ss.tausch_gesucht=1)",
        (vid, heute)).fetchone()[0]

    rollen = conn.execute(
        "SELECT name, beschreibung, farbe FROM rollen WHERE verein_id=? ORDER BY name", (vid,)).fetchall()

    # Offene Nachbereitungen: meine vergangenen Schichten (letzte 40 Tage) ohne Rückmeldung
    nachbereitung = []
    if mid:
        vor40 = (date.today() - timedelta(days=40)).isoformat()
        nachbereitung = conn.execute(
            "SELECT ss.id AS slot_id, s.titel, s.datum FROM schicht_slots ss JOIN schichten s ON s.id=ss.schicht_id "
            "WHERE s.verein_id=? AND ss.mitglied_id=? AND s.datum<? AND s.datum>=? "
            "AND NOT EXISTS (SELECT 1 FROM rueckmeldungen r WHERE r.schicht_id=s.id AND r.mitglied_id=ss.mitglied_id) "
            "ORDER BY s.datum DESC", (vid, mid, heute, vor40)).fetchall()
    conn.close()
    return render_template("portal/schichten.html", aktiv="schichten", meine=meine, offene=offene,
                           statistik=statistik, meine_mid=mid, zugeordnet=bool(m),
                           darf_uebernehmen=darf_uebernehmen,
                           q=q, zeitraum=zeitraum, offen_kommend=offen_kommend, rollen=rollen,
                           nachbereitung=nachbereitung, heute_iso=heute,
                           stunden_gesamt=stunden_gesamt, stunden_jahr=stunden_jahr, abzeichen=abzeichen)


def _slot_pruefen(conn, sid):
    return conn.execute(
        "SELECT ss.*, s.titel, s.datum FROM schicht_slots ss JOIN schichten s ON s.id=ss.schicht_id "
        "WHERE ss.id=? AND s.verein_id=?", (sid, aktiver_verein_id())).fetchone()


def _schicht_modul_pruefen():
    """Alle Schicht-POSTs gelten nur mit eingeschaltetem Modul – die Seite
    prüft das schon, aber ein direkter POST darf es nicht umgehen (Audit 06.11.)."""
    einst = get_einstellungen()
    if not (einst and einst.get("modul_schichten")):
        abort(404)


@bp.route("/schichten/slot/<int:sid>/eintragen", methods=["POST"])
@login_required
def slot_eintragen(sid):
    _schicht_modul_pruefen()
    m = _mein_mitglied()
    if not m:
        flash("Wir konnten dich noch keinem Mitglied zuordnen – bitte beim Vorstand melden.", "fehler")
        return redirect(url_for("portal.schichten"))
    # Gesundheitszeugnis ist Pflicht fürs Übernehmen – die Seite sperrt das
    # schon, aber der direkte POST muss es ebenso tun (Audit 06.11.: Lebensmittel!)
    if not _ist_admin() and not _gz_vorhanden(m):
        flash("Zum Übernehmen von Schichten brauchst du ein hinterlegtes "
              "Gesundheitszeugnis – bitte beim Vorstand melden.", "fehler")
        return redirect(url_for("portal.schichten"))
    conn = get_db()
    if not schicht_berechtigt(conn, get_einstellungen(), m["id"]):
        conn.close()
        flash("Schichten können nur Mitglieder der dafür freigegebenen Gruppen übernehmen – "
              "bitte beim Vorstand melden.", "fehler")
        return redirect(url_for("portal.schichten"))
    slot = _slot_pruefen(conn, sid)
    if slot and slot["mitglied_id"] == m["id"]:
        flash("Das ist bereits deine Schicht.", "info")
    elif slot and (not slot["mitglied_id"] or slot["tausch_gesucht"]):
        # Guard in der WHERE-Klausel gegen das Wettrennen zweier gleichzeitiger
        # Klicks (Audit: der zweite überschrieb sonst still den ersten);
        # die Erinnerungs-Einstellung der Vorperson wird nicht vererbt.
        cur = conn.execute(
            "UPDATE schicht_slots SET mitglied_id=?, tausch_gesucht=0, "
            "erinnerung=0, erinnerung_gesendet=0 "
            "WHERE id=? AND (mitglied_id IS NULL OR tausch_gesucht=1 OR mitglied_id=?)",
            (m["id"], sid, m["id"]))
        conn.commit()
        if cur.rowcount:
            flash("Eingetragen – danke für deinen Einsatz!", "erfolg")
        else:
            flash("Dieser Platz ist inzwischen vergeben.", "info")
    else:
        flash("Dieser Platz ist inzwischen vergeben.", "info")
    conn.close()
    return redirect(url_for("portal.schichten"))


@bp.route("/schichten/slot/<int:sid>/austragen", methods=["POST"])
@login_required
def slot_austragen(sid):
    _schicht_modul_pruefen()
    m = _mein_mitglied()
    conn = get_db()
    slot = _slot_pruefen(conn, sid)
    if m and slot and slot["mitglied_id"] == m["id"]:
        conn.execute("UPDATE schicht_slots SET mitglied_id=NULL, tausch_gesucht=0, "
                     "erinnerung=0, erinnerung_gesendet=0 WHERE id=?", (sid,))
        conn.commit()
        flash("Du wurdest ausgetragen.", "info")
        # Der Vorstand erfährt davon – sonst fällt die Lücke erst am Tag selbst auf
        try:
            datum = slot["datum"] or ""
            wann = datum[8:10] + "." + datum[5:7] + "." if len(datum) >= 10 else datum
            empf = benutzer_ids_mit_rolle(["admin"], aktiver_verein_id())
            if empf:
                push.senden(empf, "🚪 Schicht wieder frei",
                            f"{m['vorname']} {m['nachname']} hat sich aus „{slot['titel']}“ "
                            f"am {wann} ausgetragen.",
                            url_for("portal.schichten"), thema="schichten")
        except Exception:  # noqa: BLE001
            pass
    conn.close()
    return redirect(url_for("portal.schichten"))


@bp.route("/schichten/slot/<int:sid>/tausch", methods=["POST"])
@login_required
def slot_tausch(sid):
    """Markiert die eigene Schicht zur Abgabe (Tausch) und benachrichtigt andere per Push."""
    _schicht_modul_pruefen()
    m = _mein_mitglied()
    conn = get_db()
    slot = _slot_pruefen(conn, sid)
    if not (m and slot and slot["mitglied_id"] == m["id"]):
        conn.close()
        return redirect(url_for("portal.schichten"))
    neu = 0 if slot["tausch_gesucht"] else 1
    conn.execute("UPDATE schicht_slots SET tausch_gesucht=? WHERE id=?", (neu, sid))
    conn.commit()
    titel, datum = slot["titel"], slot["datum"]
    conn.close()
    if neu:
        flash("Deine Schicht ist zur Abgabe markiert – andere können sie übernehmen.", "info")
        try:
            wann = datum[8:10] + "." + datum[5:7] + "." if len(datum) >= 10 else datum
            empf = benutzer_ids_mit_rolle(["mitglied", "admin", "extern"], aktiver_verein_id())
            if empf:
                push.senden(empf, "🔄 Schicht abzugeben",
                            f"{titel} am {wann} – jemand sucht Ersatz. Magst du übernehmen?",
                            url_for("portal.schichten"), thema="schichten")
        except Exception:  # noqa: BLE001
            pass
    else:
        flash("Abgabe zurückgezogen.", "info")
    return redirect(url_for("portal.schichten"))


ERINNERUNG_OPTIONEN = [0, 1, 3, 24, 48]  # Vorlaufzeit in Stunden


@bp.route("/schichten/slot/<int:sid>/erinnerung", methods=["POST"])
@login_required
def slot_erinnerung(sid):
    """Setzt die selbst gewählte Erinnerungs-Vorlaufzeit (in Stunden) für die eigene Schicht."""
    _schicht_modul_pruefen()
    m = _mein_mitglied()
    stunden = request.form.get("stunden", type=int) or 0
    if stunden not in ERINNERUNG_OPTIONEN:
        stunden = 0
    conn = get_db()
    slot = _slot_pruefen(conn, sid)
    if m and slot and slot["mitglied_id"] == m["id"]:
        conn.execute("UPDATE schicht_slots SET erinnerung=?, erinnerung_gesendet=0 WHERE id=?", (stunden, sid))
        conn.commit()
        flash("Erinnerung gespeichert." if stunden else "Erinnerung ausgeschaltet.", "info")
    conn.close()
    return redirect(url_for("portal.schichten"))


@bp.route("/schicht/<int:sid>/nachbereitung", methods=["GET", "POST"])
@login_required
def nachbereitung(sid):
    m = _mein_mitglied()
    if not m:
        return redirect(url_for("portal.schichten"))
    vid = aktiver_verein_id()
    conn = get_db()
    slot = conn.execute(
        "SELECT ss.id AS slot_id, ss.mitglied_id, s.id AS schicht_id, s.termin_id, s.titel, s.datum "
        "FROM schicht_slots ss JOIN schichten s ON s.id=ss.schicht_id WHERE ss.id=? AND s.verein_id=?",
        (sid, vid)).fetchone()
    if not slot or slot["mitglied_id"] != m["id"]:
        conn.close()
        abort(404)
    if request.method == "POST":
        besucher = request.form.get("besucher", type=int)
        einnahmen = request.form.get("einnahmen", "").strip()
        fragen = request.form.get("fragen", "").strip()
        nachricht = request.form.get("nachricht", "").strip()
        conn.execute(
            "INSERT INTO rueckmeldungen (verein_id, termin_id, schicht_id, mitglied_id, besucher, einnahmen, "
            "fragen, nachricht, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?)",
            (vid, slot["termin_id"], slot["schicht_id"], m["id"], besucher, einnahmen, fragen, nachricht, now_iso()))
        conn.commit()
        conn.close()
        flash("Danke für deine Rückmeldung!", "erfolg")
        # Vorstand benachrichtigen, wenn es eine Nachricht/Fragen gibt
        if nachricht or fragen:
            try:
                empf = benutzer_ids_mit_rolle(["admin"], vid)
                if empf:
                    push.senden(empf, "📝 Rückmeldung zur Schicht",
                                f"{slot['titel']}: {(str(besucher) + ' Besucher:innen') if besucher is not None else 'Rückmeldung'}"
                                + (" · mit Nachricht" if nachricht else ""),
                                url_for("portal.rueckmeldungen"), thema="schichten")
            except Exception:  # noqa: BLE001
                pass
        return redirect(url_for("portal.schichten"))
    conn.close()
    return render_template("portal/nachbereitung.html", aktiv="schichten", slot=slot)


@bp.route("/rueckmeldungen")
@login_required
def rueckmeldungen():
    vid = aktiver_verein_id()
    conn = get_db()
    # Rückmeldungen enthalten Klarnamen und Einnahmen: Der Vorstand sieht alle,
    # Mitglieder nur die eigenen; Konten ohne Mitglied (Freund:innen) keine
    # (Audit 06.11. – vorher lagen alle für jedes angemeldete Konto offen).
    m = _mein_mitglied()
    if getattr(current_user, "ist_admin", False):
        wer_sql, wer_params = "", []
    elif m:
        wer_sql, wer_params = " AND r.mitglied_id=?", [m["id"]]
    else:
        wer_sql, wer_params = " AND 1=0", []
    liste = conn.execute(
        "SELECT r.*, s.titel AS schicht_titel, t.titel AS termin_titel, mg.vorname, mg.nachname "
        "FROM rueckmeldungen r LEFT JOIN schichten s ON s.id=r.schicht_id "
        "LEFT JOIN termine t ON t.id=r.termin_id LEFT JOIN mitglieder mg ON mg.id=r.mitglied_id "
        f"WHERE r.verein_id=?{wer_sql} ORDER BY r.erstellt_am DESC LIMIT 60",
        [vid] + wer_params).fetchall()
    vor30 = (date.today() - timedelta(days=30)).isoformat()
    besucher_30 = conn.execute(
        "SELECT COALESCE(SUM(besucher),0) FROM rueckmeldungen WHERE verein_id=? AND substr(erstellt_am,1,10)>=?",
        (vid, vor30)).fetchone()[0]
    conn.close()
    return render_template("portal/rueckmeldungen.html", aktiv="home", liste=liste, besucher_30=besucher_30,
                           ist_admin=getattr(current_user, "ist_admin", False))


@bp.route("/rueckmeldung/<int:rid>/gelesen", methods=["POST"])
@login_required
def rueckmeldung_gelesen(rid):
    """Markiert eine Rückmeldung als gelesen / ungelesen (nur Vorstand/Admin)."""
    if not getattr(current_user, "ist_admin", False):
        abort(403)
    conn = get_db()
    r = conn.execute("SELECT gelesen FROM rueckmeldungen WHERE id=? AND verein_id=?",
                     (rid, aktiver_verein_id())).fetchone()
    if r:
        conn.execute("UPDATE rueckmeldungen SET gelesen=? WHERE id=?", (0 if r["gelesen"] else 1, rid))
        conn.commit()
    conn.close()
    return redirect(url_for("portal.rueckmeldungen"))


# ------------------------- Umfragen / Terminfindung -----------------------
def _ist_admin():
    return getattr(current_user, "ist_admin", False)


def _umfrage_ergebnis(conn, uid):
    optionen = conn.execute("SELECT * FROM umfrage_optionen WHERE umfrage_id=? ORDER BY sortierung, id",
                            (uid,)).fetchall()
    stimmen = conn.execute(
        "SELECT s.option_id, s.mitglied_id, s.wert, mg.vorname, mg.nachname "
        "FROM umfrage_stimmen s LEFT JOIN mitglieder mg ON mg.id=s.mitglied_id WHERE s.umfrage_id=?",
        (uid,)).fetchall()
    erg = {o["id"]: {"text": o["text"], "ja": 0, "vielleicht": 0, "nein": 0, "namen": []} for o in optionen}
    teilnehmer = set()
    for s in stimmen:
        teilnehmer.add(s["mitglied_id"])
        e = erg.get(s["option_id"])
        if not e:
            continue
        if s["wert"] in ("ja", "vielleicht", "nein"):
            e[s["wert"]] += 1
        if s["wert"] in ("ja", "vielleicht"):
            e["namen"].append(((s["vorname"] or " ")[:1] + (s["nachname"] or " ")[:1]).upper())
    return optionen, erg, len(teilnehmer)


@bp.route("/umfragen")
@login_required
def umfragen():
    vid = aktiver_verein_id()
    conn = get_db()
    liste = conn.execute(
        "SELECT u.*, (SELECT COUNT(DISTINCT mitglied_id) FROM umfrage_stimmen WHERE umfrage_id=u.id) AS teilnehmer "
        "FROM umfragen u WHERE u.verein_id=? ORDER BY u.aktiv DESC, u.id DESC", (vid,)).fetchall()
    conn.close()
    return render_template("portal/umfragen.html", aktiv="umfragen", liste=liste, ist_admin=_ist_admin())


@bp.route("/umfrage/<int:uid>")
@login_required
def umfrage(uid):
    vid = aktiver_verein_id()
    m = _mein_mitglied()
    conn = get_db()
    u = conn.execute("SELECT * FROM umfragen WHERE id=? AND verein_id=?", (uid, vid)).fetchone()
    if not u:
        conn.close()
        abort(404)
    optionen, erg, teilnehmer = _umfrage_ergebnis(conn, uid)
    meine = {}
    if m:
        for s in conn.execute("SELECT option_id, wert FROM umfrage_stimmen WHERE umfrage_id=? AND mitglied_id=?",
                              (uid, m["id"])).fetchall():
            meine[s["option_id"]] = s["wert"]
    conn.close()
    return render_template("portal/umfrage.html", aktiv="umfragen", u=u, optionen=optionen, erg=erg,
                           teilnehmer=teilnehmer, meine=meine, zugeordnet=bool(m), ist_admin=_ist_admin())


@bp.route("/umfrage/<int:uid>/abstimmen", methods=["POST"])
@login_required
def umfrage_abstimmen(uid):
    m = _mein_mitglied()
    if not m:
        flash("Zum Abstimmen muss dein Konto einem Mitglied zugeordnet sein.", "fehler")
        return redirect(url_for("portal.umfrage", uid=uid))
    vid = aktiver_verein_id()
    conn = get_db()
    u = conn.execute("SELECT * FROM umfragen WHERE id=? AND verein_id=? AND aktiv=1", (uid, vid)).fetchone()
    if not u:
        conn.close()
        flash("Diese Umfrage ist geschlossen.", "info")
        return redirect(url_for("portal.umfrage", uid=uid))
    optionen = [o["id"] for o in conn.execute("SELECT id FROM umfrage_optionen WHERE umfrage_id=?", (uid,)).fetchall()]
    conn.execute("DELETE FROM umfrage_stimmen WHERE umfrage_id=? AND mitglied_id=?", (uid, m["id"]))
    if u["typ"] == "termin":
        for oid in optionen:
            wert = request.form.get(f"opt_{oid}")
            if wert in ("ja", "vielleicht", "nein"):
                conn.execute("INSERT INTO umfrage_stimmen (umfrage_id, option_id, mitglied_id, wert, erstellt_am) "
                             "VALUES (?,?,?,?,?)", (uid, oid, m["id"], wert, now_iso()))
    else:
        gewaehlt = request.form.getlist("auswahl") if u["mehrfach"] else [request.form.get("auswahl")]
        for oid in gewaehlt:
            try:
                oid = int(oid)
            except (TypeError, ValueError):
                continue
            if oid in optionen:
                conn.execute("INSERT INTO umfrage_stimmen (umfrage_id, option_id, mitglied_id, wert, erstellt_am) "
                             "VALUES (?,?,?,?,?)", (uid, oid, m["id"], "ja", now_iso()))
    conn.commit()
    conn.close()
    flash("Danke für deine Stimme!", "erfolg")
    return redirect(url_for("portal.umfrage", uid=uid))


@bp.route("/umfrage/neu", methods=["GET", "POST"])
@login_required
def umfrage_neu():
    if not _ist_admin():
        abort(403)
    vid = aktiver_verein_id()
    if request.method == "POST":
        typ = "termin" if request.form.get("typ") == "termin" else "auswahl"
        titel = request.form.get("titel", "").strip()
        optionen = [o.strip() for o in request.form.getlist("option") if o.strip()]
        if not titel or len(optionen) < 2:
            flash("Bitte einen Titel und mindestens zwei Optionen angeben.", "fehler")
            return redirect(url_for("portal.umfrage_neu"))
        conn = get_db()
        uid = conn.execute(
            "INSERT INTO umfragen (verein_id, titel, beschreibung, typ, mehrfach, anonym, aktiv, ende_am, erstellt_am, erstellt_von) "
            "VALUES (?,?,?,?,?,?,1,?,?,?)",
            (vid, titel, request.form.get("beschreibung", "").strip(), typ,
             1 if request.form.get("mehrfach") else 0, 1 if request.form.get("anonym") else 0,
             request.form.get("ende_am", "").strip(), now_iso(), wer())).lastrowid
        for i, text in enumerate(optionen):
            conn.execute("INSERT INTO umfrage_optionen (umfrage_id, text, sortierung) VALUES (?,?,?)", (uid, text, i))
        conn.commit()
        conn.close()
        flash("Umfrage erstellt.", "erfolg")
        try:
            empf = benutzer_ids_mit_rolle(["mitglied", "admin", "extern"], vid)
            if empf:
                push.senden(empf, "🗳️ Neue Umfrage", titel, url_for("portal.umfrage", uid=uid), thema="neuigkeiten")
        except Exception:  # noqa: BLE001
            pass
        return redirect(url_for("portal.umfrage", uid=uid))
    return render_template("portal/umfrage_neu.html", aktiv="umfragen")


@bp.route("/umfrage/<int:uid>/schliessen", methods=["POST"])
@login_required
def umfrage_schliessen(uid):
    if not _ist_admin():
        abort(403)
    conn = get_db()
    u = conn.execute("SELECT aktiv FROM umfragen WHERE id=? AND verein_id=?", (uid, aktiver_verein_id())).fetchone()
    if u:
        conn.execute("UPDATE umfragen SET aktiv=? WHERE id=?", (0 if u["aktiv"] else 1, uid))
        conn.commit()
    conn.close()
    return redirect(url_for("portal.umfrage", uid=uid))


# --------------------------- Schwarzes Brett ------------------------------
def _benutzer_des_mitglieds(mid):
    sys = get_system_db()
    ids = [r["id"] for r in sys.execute("SELECT id FROM benutzer WHERE mitglied_id=? AND aktiv=1", (mid,)).fetchall()]
    sys.close()
    return ids


from ..sprache import SPRECH_SPRACHEN as PINNWAND_SPRACHEN  # eine Quelle für alle Sprach-Auswahlen


def _pinnwand_sprachen_lesen():
    """Sprachen aus dem Formular (Checkboxen + Freitext) als Komma-Text."""
    gewaehlt = [s for s in request.form.getlist("sprachen") if s in PINNWAND_SPRACHEN]
    sonst = (request.form.get("sprachen_sonst") or "").strip()
    if sonst:
        gewaehlt.append(sonst)
    return ", ".join(gewaehlt)


def _pinnwand_kategorien(einst):
    """Kategorien des Vereins (kommagetrennt in den Einstellungen; leer = aus)."""
    roh = (einst["pinnwand_kategorien"] if einst and "pinnwand_kategorien" in einst.keys() else "") or ""
    return [k.strip() for k in roh.split(",") if k.strip()]


def _pinnwand_offen_tage(erstellt_am):
    """Wie viele Tage ein Eintrag schon ohne Rückmeldung wartet."""
    try:
        start = datetime.strptime((erstellt_am or "")[:10], "%Y-%m-%d").date()
        return (date.today() - start).days
    except ValueError:
        return 0


def _lak_saison(datum):
    # Januar-Türchen (z. B. 06.01.) gehören zur Saison des Vorjahres
    return int(datum[:4]) - (1 if datum[5:7] == "01" else 0)


# Zahlwörter für die Auflage („zum vierten Mal") – auch für den [AUFLAGE]-
# Platzhalter im eigenen Einleitungstext; jenseits der Zehn: „11. Mal".
LAK_ZAHLWORT = {2: "zweiten", 3: "dritten", 4: "vierten", 5: "fünften", 6: "sechsten",
                7: "siebten", 8: "achten", 9: "neunten", 10: "zehnten"}


def _lak_termine(conn, vid):
    """Türchen der jüngsten Adventskalender-Saison (Filter wie die Seite):
    Kategorie-Name enthält normalisiert „adventskalender", aktiv, keine
    nur_mitglieder-Termine. 404, wenn es keine passende Kategorie gibt.
    Python-Vergleich statt SQL-LIKE: SQLite faltet Umlaute/Schreibweisen nicht."""
    advent_ids = [k["id"] for k in conn.execute(
        "SELECT id, name FROM termin_kategorien WHERE verein_id=?", (vid,)).fetchall()
        if "adventskalender" in termin_maske._normalisiert(k["name"])]
    if not advent_ids:
        conn.close()
        abort(404)
    platzhalter = ",".join("?" * len(advent_ids))
    rows = conn.execute(
        "SELECT t.*, k.name AS kategorie FROM termine t "
        "JOIN termin_kategorien k ON k.id=t.kategorie_id "
        "WHERE t.verein_id=? AND t.aktiv=1 AND COALESCE(t.nur_mitglieder,0)=0 "
        f"AND t.kategorie_id IN ({platzhalter}) "
        "ORDER BY t.datum, COALESCE(t.uhrzeit,''), t.id",
        (vid, *advent_ids)).fetchall()
    saison = max((_lak_saison(r["datum"]) for r in rows), default=None)
    return ([r for r in rows if saison is not None and _lak_saison(r["datum"]) == saison],
            saison)


@bp.route("/adventskalender")
def adventskalender():
    """Öffentliche Programmseite „Lebendiger Adventskalender" (LAK).

    Ohne Anmeldung erreichbar (OEFFENTLICH-Whitelist, unabhängig vom
    Freunde-Schalter). Gezeigt werden aktive Termine von Kategorien, deren
    Name normalisiert „adventskalender" ENTHÄLT („Lebendiger Adventskalender
    LAK" passt) – bewusst NICHT nur „advent", sonst würde eine harmlose
    Kategorie „Adventsfeier" den Verein ungewollt veröffentlichen (Review
    04.11.). nur_mitglieder-Termine bleiben draußen, Abgesagte bekommen einen
    Vermerk. Gezeigt wird nur die JÜNGSTE Saison (Januar-Türchen zählen zur
    Saison des Vorjahres), sonst kumulieren Vorjahres-Türchen. Restplätze
    rechnen exakt wie die öffentliche Anmeldeseite (belegte_plaetze_je_termin
    = _stand + reservierte_plaetze, aber ohne N+1). Der nächste Termin steht
    oben, vergangene rutschen ausgegraut ans Ende."""
    from flask import make_response
    from .veranstaltungen import belegte_plaetze_je_termin
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    # Modul-Schalter je Verein (Einstellungen → Module): aus = Seite existiert nicht
    if not (einst and einst.get("modul_adventskalender")):
        abort(404)
    modul_an = bool(einst and einst["modul_veranstaltungen"])
    conn = get_db()
    rows, saison = _lak_termine(conn, vid)
    aktive = [r["id"] for r in rows
              if modul_an and r["anmeldung_aktiv"] and (r["max_teilnehmer"] or 0) > 0]
    belegt = belegte_plaetze_je_termin(conn, aktive)
    conn.close()
    # Unterstützungs-Band: alle Kooperationspartner:innen/Sponsor:innen der
    # Saison, in Termin-Reihenfolge. Dedupliziert über den NAMEN (Logo-Dateien
    # sind je Termin-Upload verschieden benannt – ein Datei-Schlüssel griffe
    # nie, Review-Fund); die Logo-Variante gewinnt über den bloßen Namen, die
    # erste Partner-Adresse bleibt. Muss VOR dem Pseudo-Heiligabend-Türchen
    # gesammelt werden (das Dict hat keine sponsor-Felder).
    sponsoren, _index = [], {}
    for r in rows:
        s_name = (r["sponsor_name"] or "").strip()
        s_logo = r["sponsor_logo"] or ""
        if not (s_name or s_logo):
            continue
        eintrag = {"name": s_name, "url": r["sponsor_url"] or "",
                   "logo_tid": r["id"] if s_logo else 0,
                   "token": r["anmeldung_token"] or ""}
        k = s_name.casefold() or "logo:" + s_logo
        alt = _index.get(k)
        if alt is not None:
            if eintrag["logo_tid"] and not alt["logo_tid"]:
                alt["logo_tid"], alt["token"] = eintrag["logo_tid"], eintrag["token"]
            if eintrag["url"] and not alt["url"]:
                alt["url"] = eintrag["url"]
            continue
        _index[k] = eintrag
        sponsoren.append(eintrag)
    # Festes Heiligabend-Türchen (nur Warener Kalender, wie 2025): am 24.12.
    # gibt es nie eine eigene Veranstaltung, nur den Verweis auf die
    # Gottesdienste. Bewusst NICHT im ICS-Export (kein echter Termin).
    if saison and "waren" in (einst["ort"] or "").casefold():
        rows = list(rows) + [{
            "id": 0, "datum": f"{saison}-12-24", "uhrzeit": "", "uhrzeit_bis": "",
            "titel": "Gottesdienste in den Warener Kirchen",
            "beschreibung": "Heiligabend laden die Warener Kirchen ein - alle "
                            "Gottesdienste und Uhrzeiten findet ihr gesammelt "
                            "beim Kirchenkreis.",
            "ort": "", "bild": "", "abgesagt": 0, "anmeldung_aktiv": 0,
            "anmeldung_token": "", "max_teilnehmer": 0, "preis": 0,
            "preis_mitglied": None, "kategorie": "",
            "extern_url": "https://www.kirche-mv.de/aktuell/veranstaltungen",
            "extern_text": "⛪ Zu den Gottesdiensten",
        }]
        rows.sort(key=lambda r: (r["datum"], r["uhrzeit"] or "", r["id"]))
    heute = date.today().isoformat()
    WTAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
    kommende, vergangene = [], []
    for r in rows:
        t = dict(r)
        t["frei"] = (max(0, (t["max_teilnehmer"] or 0) - belegt.get(t["id"], 0))
                     if t["id"] in set(aktive) else None)
        t["vorbei"] = t["datum"] < heute
        try:
            t["wtag"] = WTAGE[date.fromisoformat(t["datum"]).weekday()]
        except (ValueError, TypeError):
            t["wtag"] = ""
        # Lange Beschreibungen: Anfang sichtbar, Rest klappt ohne JavaScript
        # per <details> auf (mobil stand der Anmelde-Knopf sonst sehr tief).
        besch = t.get("beschreibung") or ""
        if len(besch) > 420:
            schnitt = besch.rfind(" ", 200, 360)
            if schnitt >= 0:   # am Leerzeichen trennen (das Leerzeichen entfällt)
                t["kurz"], t["rest"] = besch[:schnitt], besch[schnitt + 1:]
            else:              # kein Leerzeichen im Fenster: verlustfrei hart schneiden
                t["kurz"], t["rest"] = besch[:360], besch[360:]
        else:
            t["kurz"], t["rest"] = besch, ""
        (vergangene if t["vorbei"] else kommende).append(t)
    if not (kommende or vergangene):
        abort(404)
    # Mehrere Durchgänge desselben Angebots am selben Tag kennzeichnen
    laufnr = {}
    for t in kommende + vergangene:
        k = (t["datum"], t["titel"])
        laufnr[k] = laufnr.get(k, 0) + 1
        t["durchgang"] = laufnr[k] if laufnr[k] > 1 else 0
    # „Aktuell" ist der TAG des nächsten anstehenden Termins – alle Türchen
    # dieses Datums öffnen sich animiert (mehrere Angebote am selben Tag!).
    aktuell_datum = kommende[0]["datum"] if kommende else ""
    tage_bis = ((date.fromisoformat(aktuell_datum) - date.today()).days
                if aktuell_datum else None)
    # Tages-Sprungleiste: jeder Tag vom ersten bis zum letzten Türchen –
    # Tage ohne Türchen erscheinen stumm (erklärt die Lücken nebenbei).
    daten_mit = {t["datum"] for t in kommende + vergangene}
    alle_tage, lauf = [], date.fromisoformat(min(daten_mit))
    while lauf.isoformat() <= max(daten_mit):
        iso = lauf.isoformat()
        alle_tage.append({"datum": iso, "tag": lauf.day, "hat": iso in daten_mit,
                          "vorbei": iso < heute, "aktuell": iso == aktuell_datum})
        lauf += timedelta(days=1)
    # Strukturierte Daten für die Veranstaltungs-Suche (schema.org/Event);
    # das feste Heiligabend-Türchen (id 0, externer Verweis) bleibt draußen.
    basis_url = url_for("portal.adventskalender", _external=True)
    ld_events = []
    for t in kommende + vergangene:
        if not t["id"]:
            continue
        from .. import seo as _seo
        ev = {"@type": "Event", "name": t["titel"],
              # echte Zeitzone Europe/Berlin (statt fest +01:00) und Ende, wenn bekannt
              "startDate": _seo._zeit(t["datum"], t.get("uhrzeit") or ""),
              "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
              "url": f"{basis_url}#t-{t['datum']}"}
        if t.get("beschreibung"):
            ev["description"] = t["beschreibung"][:300]
        # location ist für Googles Event-Anreicherung Pflicht: eigener Ort,
        # sonst der Vereinsort – fehlt beides, bleibt das Türchen draußen.
        if t.get("uhrzeit") and t.get("uhrzeit_bis"):
            ev["endDate"] = _seo._zeit(t["datum"], t["uhrzeit_bis"])
        ort = _seo.ort_daten(t, einst) if (t.get("ort") or "").strip() else None
        if not ort and (einst["ort"] or "").strip():
            ort = {"@type": "Place", "name": einst["ort"].strip(), "address": einst["ort"].strip()}
        if not ort:
            continue
        ev["location"] = ort
        ev["eventStatus"] = _seo.status(t)
        org = _seo.veranstalter(einst)
        if org:
            ev["organizer"] = org
        leute = _seo.mitwirkende(t)
        if leute:
            ev["performer"] = leute
        if t.get("bild"):
            ev["image"] = url_for("termine.bild", tid=t["id"], v=t["bild"], _external=True)
        if modul_an and t.get("anmeldung_aktiv") and t.get("anmeldung_token"):
            ev["offers"] = {"@type": "Offer", "priceCurrency": "EUR",
                            "price": f"{(t.get('preis') or 0):.2f}",
                            "availability": ("https://schema.org/SoldOut" if t.get("frei") == 0
                                             else "https://schema.org/InStock"),
                            "url": url_for("veranstaltungen.oeffentlich",
                                           token=t["anmeldung_token"], _external=True)}
            ab = _seo.angebot_ab(t)
            if ab:
                ev["offers"]["validFrom"] = ab
        ld_events.append(ev)
    ld = {"@context": "https://schema.org", "@type": "ItemList",
          "itemListElement": [{"@type": "ListItem", "position": i + 1, "item": ev}
                              for i, ev in enumerate(ld_events)]}
    # Auflagen-Zählung aus der Einstellung „Jahr des ersten Kalenders“ (Signage → Hausfolien)
    erste = int((einst["lak_erste_saison"] if "lak_erste_saison" in einst.keys() else 0) or 0)
    auflage = (saison - erste + 1) if erste and saison >= erste else None
    # Eigener Einleitungstext aus der Adventskalender-Verwaltung (leer = die
    # eingebauten Zweige im Template). Platzhalter werden hier ersetzt, damit
    # der Text in der Datenbank jahresneutral bleiben kann.
    einleitung_eigen = (einst.get("lak_einleitung") or "").strip()
    if einleitung_eigen:
        einleitung_eigen = (einleitung_eigen
                            .replace("[JAHR]", str(saison))
                            .replace("[AUFLAGE]", (LAK_ZAHLWORT.get(auflage) or f"{auflage}.")
                                     if auflage else ""))
    antwort = make_response(render_template(
        "portal/adventskalender_public.html", einst=einst, modul_an=modul_an,
        kommende=kommende, vergangene=vergangene, jahr=str(saison), heute=heute,
        aktuell_datum=aktuell_datum, auflage=auflage, tage_bis=tage_bis,
        alle_tage=alle_tage, ld=ld, sponsoren=sponsoren,
        einleitung_eigen=einleitung_eigen))
    # Öffentlich cachen nur, wenn der Aufruf OHNE Sitzungs-Cookie kam – bei
    # angemeldeter Sitzung schickt Flask ein frisches Set-Cookie mit, das
    # nicht in geteilte Zwischenspeicher gehört. Zusätzlich (Review-Fund an der
    # Anmeldestand-Seite, gleiches Muster): nie cachen, wenn das Rendern die
    # Session beschrieben hat (?lang-Verarbeitung, Remember-Re-Login) – sonst
    # trüge eine public-Antwort ein frisches Set-Cookie in geteilte Caches.
    if not request.cookies and "lang" not in request.args and not session.modified:
        antwort.headers["Cache-Control"] = "public, max-age=60"
    else:
        antwort.headers["Cache-Control"] = "no-store"
    return antwort


@bp.route("/adventskalender.ics")
def adventskalender_ics():
    """Alle Türchen der laufenden Saison als Kalenderdatei (öffentlich).

    Gleiche Auswahl wie die Seite; Abgesagte bleiben draußen. Dateiname
    bewusst reines ASCII (Content-Disposition ist Latin-1)."""
    from .. import ics_export
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    if not (einst and einst.get("modul_adventskalender")):
        abort(404)
    conn = get_db()
    rows, saison = _lak_termine(conn, vid)
    conn.close()
    termine = [dict(r) for r in rows if not r["abgesagt"]]
    if not termine:
        abort(404)
    daten = ics_export.termine_ics(termine, name=f"Lebendiger Adventskalender {saison}", vid=vid)
    return Response(daten, mimetype="text/calendar",
                    headers={"Content-Disposition":
                             f'attachment; filename="Adventskalender-{saison}.ics"'})


@bp.route("/pinnwand")
def pinnwand():
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    angemeldet = getattr(current_user, "is_authenticated", False)
    gast_ok = bool(einst and "pinnwand_gast" in einst.keys() and einst["pinnwand_gast"])
    if not angemeldet and not gast_ok:
        return redirect(url_for("auth.login"))
    m = _mein_mitglied() if angemeldet else None
    if angemeldet:
        _gesehen_merken("pinnwand_gesehen_am")
    conn = get_db()
    kategorien = _pinnwand_kategorien(einst)
    kat_filter = (request.args.get("kat") or "").strip()
    if kat_filter and kat_filter not in kategorien:
        kat_filter = ""
    sql = ("SELECT p.*, mg.vorname, mg.nachname, "
           "(SELECT COUNT(*) FROM pinnwand_antworten a WHERE a.pinnwand_id=p.id) AS antworten "
           "FROM pinnwand p LEFT JOIN mitglieder mg ON mg.id=p.mitglied_id "
           "WHERE p.verein_id=? AND p.aktiv=1 AND COALESCE(p.moderation,0)=0 ")
    params = [vid]
    if kat_filter:
        sql += "AND p.kategorie=? "
        params.append(kat_filter)
    posts = conn.execute(sql + "ORDER BY p.id DESC", params).fetchall()
    offen_tage = {p["id"]: _pinnwand_offen_tage(p["erstellt_am"])
                  for p in posts if p["typ"] == "gesuch" and p["antworten"] == 0}
    meine_antw = set()
    if m:
        for r in conn.execute("SELECT pinnwand_id FROM pinnwand_antworten WHERE mitglied_id=?", (m["id"],)).fetchall():
            meine_antw.add(r["pinnwand_id"])
    mention_users = erwaehnungen.mitglieder_liste(conn, vid) if angemeldet else []
    conn.close()
    return render_template("portal/pinnwand.html", aktiv="pinnwand", posts=posts, zugeordnet=bool(m),
                           meine_mid=(m["id"] if m else None), meine_antw=meine_antw, ist_admin=_ist_admin(),
                           mention_users=mention_users, kategorien=kategorien,
                           kat_filter=kat_filter, offen_tage=offen_tage,
                           sprachen_auswahl=PINNWAND_SPRACHEN,
                           gast_schreiben=(not angemeldet and gast_ok))


@bp.route("/pinnwand/neu", methods=["POST"])
@login_required
def pinnwand_neu():
    m = _mein_mitglied()
    if not m:
        flash("Zum Posten muss dein Konto einem Mitglied zugeordnet sein.", "fehler")
        return redirect(url_for("portal.pinnwand"))
    vid = aktiver_verein_id()
    titel = request.form.get("titel", "").strip()
    text = request.form.get("text", "").strip()
    typ = request.form.get("typ", "gesuch")
    if typ not in ("gesuch", "angebot", "info"):
        typ = "gesuch"
    if not titel:
        flash("Bitte einen Titel eingeben.", "fehler")
        return redirect(url_for("portal.pinnwand"))
    einst = get_einstellungen()
    kategorie = (request.form.get("kategorie") or "").strip()
    if kategorie not in _pinnwand_kategorien(einst):
        kategorie = ""
    conn = get_db()
    conn.execute("INSERT INTO pinnwand (verein_id, titel, text, typ, kategorie, sprachen, "
                 "mitglied_id, aktiv, erstellt_am, erstellt_von) VALUES (?,?,?,?,?,?,?,1,?,?)",
                 (vid, titel, text, typ, kategorie, _pinnwand_sprachen_lesen(),
                  m["id"], now_iso(), wer()))
    conn.commit()
    try:
        erwaehnungen.pushe_erwaehnungen(conn, vid, titel + " " + text, "📌 Schwarzes Brett",
                                        url_for("portal.pinnwand"), ausser_mid=m["id"])
    except Exception:  # noqa: BLE001
        pass
    conn.close()
    flash("Am Schwarzen Brett veröffentlicht.", "erfolg")
    try:
        empf = benutzer_ids_mit_rolle(["mitglied", "admin", "extern"], vid)
        if empf:
            praefix = {"gesuch": "🙋 Gesuch", "angebot": "🎁 Angebot", "info": "📌 Info"}[typ]
            push.senden(empf, f"{praefix} am Schwarzen Brett", titel, url_for("portal.pinnwand"), thema="neuigkeiten")
    except Exception:  # noqa: BLE001
        pass
    # Optionale Mail an den Helferkreis (Einstellungen: pinnwand_mail_an, kommagetrennt)
    try:
        mail_an = ((einst["pinnwand_mail_an"] if einst and "pinnwand_mail_an" in einst.keys() else "") or "")
        adressen = [a.strip() for a in mail_an.split(",") if a.strip()]
        if adressen:
            from .. import mail
            praefix = {"gesuch": "🙋 Gesuch", "angebot": "🎁 Angebot", "info": "📌 Info"}[typ]
            link = url_for("portal.pinnwand", _external=True)
            inhalt = (f"{praefix} am Schwarzen Brett"
                      + (f" ({kategorie})" if kategorie else "") + ":\n\n"
                      f"{titel}\n\n{text}\n\n"
                      f"von {m['vorname']} {m['nachname']}\n\nZum Schwarzen Brett: {link}")
            for adresse in adressen:
                mail.einzelmail(einst, adresse, f"{praefix}: {titel}", inhalt)
    except Exception:  # noqa: BLE001
        pass
    return redirect(url_for("portal.pinnwand"))


@bp.route("/pinnwand/<int:pid>/melden", methods=["POST"])
@login_required
def pinnwand_melden(pid):
    m = _mein_mitglied()
    if not m:
        return redirect(url_for("portal.pinnwand"))
    vid = aktiver_verein_id()
    conn = get_db()
    post = conn.execute("SELECT * FROM pinnwand WHERE id=? AND verein_id=?", (pid, vid)).fetchone()
    if post and post["mitglied_id"] != m["id"]:
        da = conn.execute("SELECT 1 FROM pinnwand_antworten WHERE pinnwand_id=? AND mitglied_id=?",
                          (pid, m["id"])).fetchone()
        if not da:
            conn.execute("INSERT INTO pinnwand_antworten (pinnwand_id, mitglied_id, erstellt_am) VALUES (?,?,?)",
                         (pid, m["id"], now_iso()))
            conn.commit()
            try:
                bids = _benutzer_des_mitglieds(post["mitglied_id"])
                if bids:
                    push.senden(bids, "🙌 Jemand meldet sich",
                                f"{m['vorname']} {m['nachname']} zu: {post['titel']}", url_for("portal.pinnwand"), thema="neuigkeiten")
            except Exception:  # noqa: BLE001
                pass
        flash("Danke – die Person wird benachrichtigt.", "erfolg")
    conn.close()
    return redirect(url_for("portal.pinnwand"))


@bp.route("/pinnwand/<int:pid>/schliessen", methods=["POST"])
@login_required
def pinnwand_schliessen(pid):
    m = _mein_mitglied()
    conn = get_db()
    post = conn.execute("SELECT * FROM pinnwand WHERE id=? AND verein_id=?", (pid, aktiver_verein_id())).fetchone()
    if post and (_ist_admin() or (m and post["mitglied_id"] == m["id"])):
        conn.execute("UPDATE pinnwand SET aktiv=0 WHERE id=?", (pid,))
        conn.commit()
        flash("Eintrag entfernt.", "info")
    conn.close()
    return redirect(url_for("portal.pinnwand"))


# --------------------------- Ankündigungen / News -------------------------
@bp.route("/news/neu", methods=["POST"])
@login_required
def news_neu():
    if not _ist_admin():
        abort(403)
    titel = request.form.get("titel", "").strip()
    text = request.form.get("text", "").strip()
    if not titel:
        flash("Bitte einen Titel eingeben.", "fehler")
        return redirect(url_for("portal.home"))
    oeff = 1 if request.form.get("oeffentlich") else 0
    conn = get_db()
    conn.execute("INSERT INTO ankuendigungen (verein_id, titel, text, aktiv, erstellt_am, oeffentlich, push_gesendet) "
                 "VALUES (?,?,?,1,?,?,1)",
                 (aktiver_verein_id(), titel, text, now_iso(), oeff))
    conn.commit()
    conn.close()
    flash("Ankündigung veröffentlicht – " + ("öffentlich sichtbar. 🌍" if oeff else "nur intern. 🔒"), "erfolg")
    try:
        # Push passend zur Sichtbarkeit: intern nur an Mitglieder & Vorstand
        empf = benutzer_ids_mit_rolle(["mitglied", "admin", "extern"] if oeff else ["mitglied", "admin"],
                                      aktiver_verein_id())
        if empf:
            push.senden(empf, "📣 " + titel, text[:120] or "Neue Ankündigung", url_for("portal.home"), thema="neuigkeiten")
    except Exception:  # noqa: BLE001
        pass
    return redirect(url_for("portal.home"))


@bp.route("/news/<int:nid>/loeschen", methods=["POST"])
@login_required
def news_loeschen(nid):
    if not _ist_admin():
        abort(403)
    conn = get_db()
    conn.execute("UPDATE ankuendigungen SET aktiv=0 WHERE id=? AND verein_id=?", (nid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    return redirect(url_for("portal.home"))


# ------------------------------- FAQ / Infos ------------------------------
@bp.route("/infos", methods=["GET", "POST"])
@login_required
def infos():
    vid = aktiver_verein_id()
    conn = get_db()
    if request.method == "POST" and _ist_admin():
        if request.form.get("loeschen"):
            conn.execute("UPDATE faq SET aktiv=0 WHERE id=? AND verein_id=?",
                         (request.form.get("loeschen", type=int), vid))
        else:
            frage = request.form.get("frage", "").strip()
            if frage:
                conn.execute("INSERT INTO faq (verein_id, frage, antwort, sortierung, aktiv) VALUES (?,?,?,0,1)",
                             (vid, frage, request.form.get("antwort", "").strip()))
        conn.commit()
        conn.close()
        return redirect(url_for("portal.infos"))
    liste = conn.execute("SELECT * FROM faq WHERE verein_id=? AND aktiv=1 ORDER BY sortierung, id", (vid,)).fetchall()
    conn.close()
    return render_template("portal/infos.html", aktiv="infos", liste=liste, ist_admin=_ist_admin())


def _geburtstage(conn, vid, tage=30):
    heute = date.today()
    rows = conn.execute(
        "SELECT vorname, nachname, geburtsdatum FROM mitglieder "
        "WHERE verein_id=? AND geb_sichtbar=1 AND IFNULL(geburtsdatum,'')!='' "
        "AND IFNULL(status,'') NOT IN ('ausgetreten','inaktiv','verstorben')", (vid,)).fetchall()
    out = []
    for r in rows:
        try:
            g = date.fromisoformat(r["geburtsdatum"][:10])
        except ValueError:
            continue
        naechster = g.replace(year=heute.year)
        if naechster < heute:
            try:
                naechster = g.replace(year=heute.year + 1)
            except ValueError:
                continue
        diff = (naechster - heute).days
        if 0 <= diff <= tage:
            out.append({"name": f"{r['vorname']} {r['nachname']}", "tag": naechster.day,
                        "monat": naechster.month, "in_tagen": diff})
    out.sort(key=lambda x: x["in_tagen"])
    return out


# ------------------------------- Dokumente --------------------------------
@bp.route("/dokumente")
@login_required
def dokumente():
    conn = get_db()
    docs = conn.execute(
        "SELECT * FROM vereinsdokumente WHERE verein_id=? AND fuer_mitglieder=1 "
        "ORDER BY kategorie, hochgeladen_am DESC", (aktiver_verein_id(),)).fetchall()
    conn.close()
    return render_template("portal/dokumente.html", aktiv="dokumente", dokumente=docs)


@bp.route("/dokument/<int:did>")
@login_required
def dokument(did):
    conn = get_db()
    d = conn.execute("SELECT * FROM vereinsdokumente WHERE id=? AND verein_id=? AND fuer_mitglieder=1",
                     (did, aktiver_verein_id())).fetchone()
    conn.close()
    if not d:
        abort(404)
    return speicher.sende(get_einstellungen(), "dokumente", d["dateiname"], download_name=d["original_name"])


@bp.route("/dokument/<int:did>/ansehen")
@login_required
def dokument_ansicht(did):
    """Viewer-Seite mit Leiste (Schließen / Teilen / Drucken) – bettet die Datei ein."""
    conn = get_db()
    d = conn.execute("SELECT * FROM vereinsdokumente WHERE id=? AND verein_id=? AND fuer_mitglieder=1",
                     (did, aktiver_verein_id())).fetchone()
    conn.close()
    if not d:
        abort(404)
    name = (d["original_name"] or d["dateiname"] or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    ist_bild = ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "heic")
    return render_template("portal/dokument_ansicht.html", d=d, ist_pdf=(ext == "pdf"), ist_bild=ist_bild)


# --------------------------------- Chat -----------------------------------
@bp.route("/chat")
@login_required
def chat():
    vid = aktiver_verein_id()
    conn = get_db()
    # Reihenfolge nach Gruppen-Rang (Vorstand vor Mitgliedern vor Fördermitgliedern …);
    # jede Person erscheint unter ihrer ranghöchsten Gruppe, ohne Gruppe unter „Weitere".
    mitglieder = conn.execute(
        "SELECT m.id, m.vorname, m.nachname, m.sprachen, m.avatar_dateiname, m.abwesend, m.abwesend_von, m.abwesend_bis, "
        "m.abwesend_notiz, m.email, m.telefon, m.mobil, m.kontakt_ok, m.komm_erlaubt, "
        "COALESCE((SELECT MIN(g.rang) FROM mitglied_gruppe mg JOIN gruppen g ON g.id=mg.gruppe_id "
        "          WHERE mg.mitglied_id=m.id), 999) AS gruppe_rang, "
        "(SELECT g.name FROM mitglied_gruppe mg JOIN gruppen g ON g.id=mg.gruppe_id "
        " WHERE mg.mitglied_id=m.id ORDER BY g.rang, g.name LIMIT 1) AS gruppe_name "
        "FROM mitglieder m WHERE m.verein_id=? AND IFNULL(m.status,'') NOT IN ('ausgetreten','inaktiv','verstorben') "
        "ORDER BY gruppe_rang, gruppe_name, m.vorname, m.nachname", (vid,)).fetchall()
    conn.close()
    eigen = _mein_mitglied()
    return render_template("portal/chat.html", aktiv="chat", mitglieder=mitglieder,
                           ist_admin=_ist_admin(), meine_mid=(eigen["id"] if eigen else None))


@bp.route("/mitglied/<int:mid>")
@login_required
def mitglied(mid):
    """Kontakt-Seite eines Mitglieds: E-Mail, Anruf, WhatsApp (nur wenn freigegeben)."""
    vid = aktiver_verein_id()
    conn = get_db()
    mg = conn.execute("SELECT * FROM mitglieder WHERE id=? AND verein_id=?", (mid, vid)).fetchone()
    conn.close()
    if not mg:
        abort(404)
    eigen = _mein_mitglied()
    darf_kontakt = bool(getattr(current_user, "ist_admin", False) or mg["kontakt_ok"]
                        or (eigen and eigen["id"] == mid))
    # Freigegebene Kommunikationswege (leer = keine Einschränkung -> Standardwege)
    wege = {w.strip() for w in (mg["komm_erlaubt"] or "").split(",") if w.strip()} \
        if "komm_erlaubt" in mg.keys() else set()
    return render_template("portal/mitglied.html", aktiv="chat", mg=mg, darf_kontakt=darf_kontakt,
                           wege=wege)


# ---------------------- Push-Benachrichtigungen ---------------------------
@bp.route("/push/key")
@login_required
def push_key():
    """Liefert den öffentlichen VAPID-Schlüssel für das Browser-Abo."""
    return jsonify({"key": push.public_key()})


@bp.route("/push/abo", methods=["POST"])
@login_required
def push_abo():
    """Speichert das Push-Abo des aktuellen Nutzers (vom Service-Worker geschickt).

    CSRF-befreit (siehe unten): Der Service Worker erneuert Abos auch ohne offene
    Seite (pushsubscriptionchange) und hat dann kein Token. SameSite=Lax schützt,
    weil fremde Seiten das Session-Cookie bei Cross-Site-POSTs nicht mitschicken."""
    daten = request.get_json(silent=True) or {}
    endpoint = daten.get("endpoint")
    keys = daten.get("keys") or {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (endpoint and p256dh and auth):
        return jsonify({"ok": False, "fehler": "unvollständig"}), 400
    if not push.endpoint_erlaubt(endpoint):
        # Nur echte Push-Dienste – sonst würde der Server beliebige Adressen
        # (auch interne) mit POSTs beschicken (Demo-Schutz-Audit 16.11.2026).
        return jsonify({"ok": False, "fehler": "unbekannter Push-Dienst"}), 400
    push_abo_speichern(int(current_user.id), endpoint, p256dh, auth)
    return jsonify({"ok": True})


@bp.route("/push/test", methods=["POST"])
@login_required
def push_test():
    """Schickt dem aktuellen Nutzer eine Test-Benachrichtigung."""
    n = push.senden([int(current_user.id)], "Test ✅",
                    "Benachrichtigungen sind aktiv. So melden wir dich bei Neuigkeiten.",
                    url_for("portal.home"))
    return jsonify({"ok": True, "gesendet": n})


# --------------- Viewer: Fotos/Dateien mit Zurück- und Teilen-Knopf --------
@bp.route("/ansehen")
@login_required
def ansehen():
    """Vollbild-Ansicht für Fotos (Galerie) und Dateien (Dateiablage) – mit
    Zurück- und Teilen-Knopf, wichtig in der installierten App ohne Browserleiste."""
    was = request.args.get("was")
    vid = aktiver_verein_id()
    if was == "foto":
        fid = request.args.get("fid", type=int)
        conn = get_db()
        row = conn.execute("SELECT f.id, f.beschreibung, f.mitglied_id, f.termin_id, t.titel FROM galerie_fotos f "
                           "LEFT JOIN termine t ON t.id=f.termin_id WHERE f.id=? AND f.verein_id=?",
                           (fid, vid)).fetchone()
        conn.close()
        if not row:
            abort(404)
        darf = getattr(current_user, "is_authenticated", False) and _darf_foto_loeschen(row)
        return render_template("portal/viewer.html",
                               inhalt_url=url_for("portal.galerie_foto", fid=fid),
                               name=row["beschreibung"] or row["titel"] or "Foto",
                               ist_bild=True, zurueck=url_for("portal.galerie"),
                               loeschen_url=url_for("portal.foto_loeschen", fid=fid) if darf else None)
    if was == "datei":
        einst = get_einstellungen()
        if not (einst and "modul_dms" in einst.keys() and einst["modul_dms"]):
            abort(404)
        from .. import dateiablage as dms
        relpfad = request.args.get("pfad") or ""
        if dms.ist_vorstand(relpfad) or not dms.darf_sehen(relpfad, dms.zugriffsregeln(vid), _meine_gruppen_ids()):
            abort(403)
        ziel = dms.aufloesen(vid, relpfad)
        if not ziel or not os.path.isfile(ziel):
            abort(404)
        name = os.path.basename(relpfad)
        ist_bild = os.path.splitext(name)[1].lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".heic")
        return render_template("portal/viewer.html",
                               inhalt_url=url_for("portal.dateien_vorschau", pfad=relpfad),
                               name=name, ist_bild=ist_bild, zurueck=url_for("portal.dateien"),
                               download_url=url_for("portal.dateien_download", pfad=relpfad))
    abort(404)


@bp.route("/hilfe/unterlagen/<dateiname>")
@login_required
def hilfe_unterlage(dateiname):
    from .. import hilfe_unterlagen as _unterlagen
    return _unterlagen.senden(dateiname, anhang=bool(request.args.get("laden")))


@bp.route("/hilfe/lesen/<schluessel>")
@login_required
def hilfe_lesen(schluessel):
    """Lese-Ansicht einer Kurzanleitung: großer Text statt kleinem PDF,
    mit Zurück, „nächste Anleitung“ und Teilen – keine Sackgasse mehr."""
    from .. import hilfe_unterlagen as _unterlagen
    rolle = getattr(current_user, "rolle", "")
    eintrag, naechste = _unterlagen.lesen(schluessel, rolle)
    if not eintrag:
        abort(404)
    return render_template("portal/anleitung_lesen.html", aktiv="profil",
                           eintrag=eintrag, naechste=naechste)


@bp.route("/hilfe")
def hilfe():
    """Kleine Anleitung für Mitglieder (+ Hinweis auf den Adminbereich für den Vorstand)."""
    einst = get_einstellungen()
    dq = (einst.get("dateien_quelle") or "") if einst else ""
    nc = bool(einst and einst.get("ncc_server"))
    from .. import hilfe_unterlagen as _unterlagen
    rolle_u = getattr(current_user, "rolle", "") if getattr(current_user, "is_authenticated", False) else ""
    return render_template("portal/hilfe.html", aktiv="profil",
                           unterlagen=_unterlagen.liste(rolle_u) if rolle_u else [],
                           schichten_an=bool(einst and einst.get("modul_schichten")),
                           nc_verein=nc and dq in ("", "nextcloud", "beide"),
                           dms_an=bool(einst and einst.get("modul_dms"))
                                  and (dq in ("server", "beide") or (dq == "" and not nc)),
                           ist_admin=_ist_admin())


@bp.route("/termin/<int:tid>.ics")
def termin_ics(tid):
    """Einzelnen Termin als Kalenderdatei (ICS) – „In meinen Kalender speichern"."""
    vid = aktiver_verein_id()
    conn = get_db()
    t = conn.execute("SELECT t.*, k.name AS kategorie FROM termine t "
                     "LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
                     "WHERE t.id=? AND t.verein_id=? AND t.aktiv=1", (tid, vid)).fetchone()
    conn.close()
    if not t:
        abort(404)
    t = _fuer_gast(t)
    from ..ics_export import termine_ics
    daten = termine_ics([t], name=t["titel"], vid=vid)
    return Response(daten, mimetype="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="termin-{tid}.ics"'})


@bp.route("/ueber")
def ueber():
    """Kennenlern-Seite des Vereins – nur für Gäste (ohne Login) und Freunde.

    Mitglieder kennen ihren Verein; sie landen auf der Startseite."""
    if not _ist_gastansicht():
        return redirect(url_for("portal.home"))
    einst = get_einstellungen()
    insta = (einst.get("instagram") or "").strip().lstrip("@").replace("https://", "").replace("instagram.com/", "").strip("/")
    website = (einst.get("website") or "").strip()
    if website and not website.startswith(("http://", "https://")):
        website = "https://" + website
    return render_template("portal/ueber.html", aktiv="ueber", einst=einst,
                           website=website, instagram=insta)


# ------------------------------ App-Seite + Badge --------------------------
@bp.route("/app")
def app_installieren():
    """Anleitung „Als App installieren" mit QR-Code der Portal-Adresse."""
    from ..qr import data_uri
    portal_url = request.url_root.rstrip("/") + url_for("portal.home")
    return render_template("portal/app_installieren.html", aktiv="profil",
                           portal_url=portal_url, qr=data_uri(portal_url, scale=6))


def _badge_zahl():
    """Neuigkeiten seit dem letzten „Gesehen": Ankündigungen + Pinnwand + eigene offene Aufgaben."""
    vid = aktiver_verein_id()
    sys = get_system_db()
    row = sys.execute("SELECT badge_gesehen_am FROM benutzer WHERE id=?",
                      (int(current_user.id),)).fetchone()
    sys.close()
    seit = (row["badge_gesehen_am"] if row and "badge_gesehen_am" in row.keys() else "") or "1970-01-01"
    conn = get_db()
    n = 0
    try:
        nur_oeff = " AND oeffentlich=1" if _ist_gastansicht() else ""
        zeitfenster = (" AND (sichtbar_von IS NULL OR sichtbar_von='' OR sichtbar_von<=date('now'))"
                       " AND (sichtbar_bis IS NULL OR sichtbar_bis='' OR sichtbar_bis>=date('now'))")
        n += conn.execute(f"SELECT COUNT(*) FROM ankuendigungen WHERE verein_id=? AND aktiv=1{nur_oeff}{zeitfenster} "
                          "AND erstellt_am > ?", (vid, seit)).fetchone()[0]
        n += conn.execute("SELECT COUNT(*) FROM pinnwand WHERE verein_id=? AND aktiv=1 "
                          "AND erstellt_am > ?", (vid, seit)).fetchone()[0]
        m = _mein_mitglied()
        if m:
            n += conn.execute("SELECT COUNT(*) FROM aufgaben WHERE verein_id=? "
                              "AND (mitglied_id=? OR COALESCE(zielgruppe,'')='mitglieder') "
                              "AND erledigt=0", (vid, m["id"])).fetchone()[0]
    except Exception:  # noqa: BLE001
        pass
    conn.close()
    return n


@bp.route("/zaehler")
@login_required
def zaehler():
    """Zähler für das App-Icon-Badge (navigator.setAppBadge)."""
    return jsonify({"neu": _badge_zahl()})


@bp.route("/zaehler/gesehen", methods=["POST"])
@login_required
def zaehler_gesehen():
    """Startseite besucht -> Neuigkeiten gelten als gesehen."""
    sys = get_system_db()
    sys.execute("UPDATE benutzer SET badge_gesehen_am=? WHERE id=?",
                (now_iso(), int(current_user.id)))
    sys.commit()
    sys.close()
    return jsonify({"ok": True})


@bp.route("/aussehen", methods=["GET", "POST"])
def aussehen():
    """Aussehen der App – gilt nur für dieses Mitglied.

    Der Verein gibt etwas vor; wer hier etwas wählt, weicht davon ab. „Wie der
    Verein" setzt die Abweichung wieder zurück.

    Bewusst ohne @login_required: In der Lokalen Verwaltung gibt es keine
    Anmeldung – dort wird die Vereinsvorgabe für die App bearbeitet. Online
    schützt _zugriffsschutz die Seite weiterhin (Freunde dürfen, siehe
    FREUND_ERLAUBT; ganz ohne Konto kommt niemand hierher).
    """
    from .. import design as design_modul
    angemeldet = getattr(current_user, "is_authenticated", False)

    if request.method == "POST":
        werte = None if request.form.get("zuruecksetzen") else \
            design_modul.saeubern(request.form.to_dict())
        if angemeldet:
            sys_conn = get_system_db()
            sys_conn.execute("UPDATE benutzer SET design_config=? WHERE id=?",
                             (json.dumps(werte, ensure_ascii=False) if werte else None,
                              int(current_user.id)))
            sys_conn.commit()
            sys_conn.close()
        else:
            # Lokale Verwaltung: als Vereinsvorgabe für die App speichern
            from .einstellungen import _bereich_speichern
            conn = get_db()
            if werte is None:
                # Nur den App-Teil leeren – die Vorgabe der Verwaltung bleibt
                vid = aktiver_verein_id()
                zeile = conn.execute("SELECT design_config FROM vereine WHERE id=?",
                                     (vid,)).fetchone()
                try:
                    daten = json.loads((zeile["design_config"] if zeile else "") or "{}")
                except (ValueError, TypeError):
                    daten = {}
                if isinstance(daten, dict):
                    daten.pop("app", None)
                conn.execute("UPDATE vereine SET design_config=? WHERE id=?",
                             (json.dumps(daten, ensure_ascii=False) if daten else None, vid))
            else:
                _bereich_speichern(conn, "app", request.form)
            conn.commit()
            conn.close()
        flash("Wieder wie beim Verein. 🎨" if werte is None else "Gespeichert. 🎨", "erfolg")
        return redirect(url_for("portal.aussehen"))

    e = get_einstellungen()
    eigen = None
    if angemeldet:
        sys_conn = get_system_db()
        zeile = sys_conn.execute("SELECT design_config FROM benutzer WHERE id=?",
                                 (int(current_user.id),)).fetchone()
        sys_conn.close()
        eigen = zeile["design_config"] if zeile and "design_config" in zeile.keys() else None
    return render_template(
        "portal/aussehen.html", aktiv="ich",
        werte=design_modul.zusammenfuehren(e, eigen, "app"),
        vereinsvorgabe=design_modul.zusammenfuehren(e, None, "app"),
        eigen_gesetzt=bool(eigen),
        schemata=design_modul.SCHEMATA, modi=design_modul.MODI,
        groessen=design_modul.SCHRIFTGROESSEN,
        schriftarten=sorted(design_modul.SCHRIFTARTEN))


# ---------------------------------------------------------------------------
# Öffentliches Schwarzes Brett für Gäste (ohne Konto, mit Moderation).
# Nur aktiv, wenn der Verein es in den Einstellungen eingeschaltet hat
# (pinnwand_gast). Pflicht: Name, Kontakt und Sprachen – bei Suchenden
# wie bei Helfenden. Einträge erscheinen erst nach Freigabe.
# ---------------------------------------------------------------------------
def _brett_gast_einst():
    einst = get_einstellungen()
    if not einst or "pinnwand_gast" not in einst.keys() or not einst["pinnwand_gast"]:
        return None
    return einst


@bp.route("/brett")
def brett_gast():
    einst = _brett_gast_einst()
    if einst is None:
        abort(404)
    return render_template("portal/brett_gast.html",
                           kategorien=_pinnwand_kategorien(einst),
                           sprachen_auswahl=PINNWAND_SPRACHEN,
                           vereinsname=(einst["vereinsname"] or "Schwarzes Brett"),
                           sprachen_an=bool(einst["portal_sprachen"] if "portal_sprachen" in einst.keys() else 0),
                           gesendet=False)


@bp.route("/brett", methods=["POST"])
def brett_gast_absenden():
    einst = _brett_gast_einst()
    if einst is None:
        abort(404)
    kategorien = _pinnwand_kategorien(einst)
    # Honigtopf gegen Spam-Bots: Menschen lassen das unsichtbare Feld leer.
    if (request.form.get("website") or "").strip():
        return render_template("portal/brett_gast.html", kategorien=kategorien,
                               sprachen_auswahl=PINNWAND_SPRACHEN,
                               vereinsname=(einst["vereinsname"] or "Schwarzes Brett"),
                           sprachen_an=bool(einst["portal_sprachen"] if "portal_sprachen" in einst.keys() else 0),
                               gesendet=True)
    typ = request.form.get("typ", "gesuch")
    if typ not in ("gesuch", "angebot"):
        typ = "gesuch"
    titel = (request.form.get("titel") or "").strip()
    text = (request.form.get("text") or "").strip()
    name = (request.form.get("name") or "").strip()
    kontakt = (request.form.get("kontakt") or "").strip()
    sprachen = _pinnwand_sprachen_lesen()
    kategorie = (request.form.get("kategorie") or "").strip()
    if kategorie not in kategorien:
        kategorie = ""
    fehlend = []
    if not titel:
        fehlend.append("Worum es geht")
    if not name:
        fehlend.append("Name")
    if not kontakt:
        fehlend.append("Kontakt (Telefon oder E-Mail)")
    if not sprachen:
        fehlend.append("Sprache(n)")
    if fehlend:
        flash("Bitte noch ausfüllen: " + ", ".join(fehlend), "fehler")
        return render_template("portal/brett_gast.html", kategorien=kategorien,
                               sprachen_auswahl=PINNWAND_SPRACHEN,
                               vereinsname=(einst["vereinsname"] or "Schwarzes Brett"),
                           sprachen_an=bool(einst["portal_sprachen"] if "portal_sprachen" in einst.keys() else 0),
                               gesendet=False, form=request.form)
    vid = aktiver_verein_id()
    token = uuid.uuid4().hex
    conn = get_db()
    conn.execute("INSERT INTO pinnwand (verein_id, titel, text, typ, kategorie, sprachen, "
                 "gast_name, gast_kontakt, gast_token, mitglied_id, aktiv, moderation, erstellt_am) "
                 "VALUES (?,?,?,?,?,?,?,?,?,NULL,1,1,?)",
                 (vid, titel, text, typ, kategorie, sprachen, name, kontakt, token, now_iso()))
    conn.commit()
    conn.close()
    # Verwaltung benachrichtigen: Push an Admins + optionale Helferkreis-Mail
    try:
        admins = benutzer_ids_mit_rolle(["admin"], vid)
        if admins:
            push.senden(admins, "🕒 Neuer Gast-Eintrag wartet auf Freigabe", titel,
                        url_for("kommunikation.schwarzes_brett"), thema="neuigkeiten")
    except Exception:  # noqa: BLE001
        pass
    try:
        mail_an = ((einst["pinnwand_mail_an"] if "pinnwand_mail_an" in einst.keys() else "") or "")
        adressen = [a.strip() for a in mail_an.split(",") if a.strip()]
        if adressen:
            from .. import mail
            praefix = "🙋 Gesuch" if typ == "gesuch" else "🎁 Angebot"
            inhalt = (f"Neuer Gast-Eintrag (wartet auf Freigabe):\n\n{praefix}: {titel}\n\n{text}\n\n"
                      f"Von: {name} · {kontakt} · Sprachen: {sprachen}")
            for adresse in adressen:
                mail.einzelmail(einst, adresse, f"Gast-Eintrag: {titel}", inhalt)
    except Exception:  # noqa: BLE001
        pass
    return render_template("portal/brett_gast.html", kategorien=kategorien,
                           sprachen_auswahl=PINNWAND_SPRACHEN,
                           vereinsname=(einst["vereinsname"] or "Schwarzes Brett"),
                           sprachen_an=bool(einst["portal_sprachen"] if "portal_sprachen" in einst.keys() else 0),
                           gesendet=True,
                           bearbeiten_link=url_for("portal.brett_gast_bearbeiten",
                                                   token=token, _external=True))


def _brett_gast_eintrag(token):
    if not token or len(token) < 16:
        return None
    # Vereinsübergreifend: der persönliche Link soll unabhängig vom gerade
    # aktiven Verein funktionieren (Lokale Verwaltung kennt mehrere Vereine).
    conn, row = oeffentliche_suche(
        "SELECT * FROM pinnwand WHERE gast_token=? AND gast_token!='' AND aktiv=1", token)
    conn.close()
    return row


def _brett_gast_kontext(token):
    """(einst, eintrag) zum Gast-Token – Gäste-Brett-Freigabe und Vereinsdaten
    kommen vom Verein des Eintrags, nicht vom gerade aktiven Verein."""
    eintrag = _brett_gast_eintrag(token)
    if eintrag is None:
        return None, None
    einst = einstellungen_fuer(eintrag["verein_id"])
    if not einst or not einst.get("pinnwand_gast"):
        return None, None
    return einst, eintrag


@bp.route("/brett/eintrag/<token>")
def brett_gast_bearbeiten(token):
    """Gast bearbeitet den eigenen Eintrag über seinen persönlichen Link."""
    einst, eintrag = _brett_gast_kontext(token)
    if einst is None or eintrag is None:
        abort(404)
    return render_template("portal/brett_gast_bearbeiten.html",
                           kategorien=_pinnwand_kategorien(einst),
                           sprachen_auswahl=PINNWAND_SPRACHEN,
                           sprachen_an=bool(einst["portal_sprachen"] if "portal_sprachen" in einst.keys() else 0),
                           e=eintrag, token=token)


@bp.route("/brett/eintrag/<token>", methods=["POST"])
def brett_gast_speichern(token):
    einst, eintrag = _brett_gast_kontext(token)
    if einst is None or eintrag is None:
        abort(404)
    vid = eintrag["verein_id"]
    if request.form.get("aktion") == "zurueckziehen":
        conn = get_db_fuer(vid)
        conn.execute("UPDATE pinnwand SET aktiv=0 WHERE gast_token=? AND verein_id=?",
                     (token, vid))
        conn.commit()
        conn.close()
        return render_template("portal/brett_gast_bearbeiten.html", zurueckgezogen=True,
                               sprachen_an=bool(einst["portal_sprachen"] if "portal_sprachen" in einst.keys() else 0),
                               kategorien=[], sprachen_auswahl=PINNWAND_SPRACHEN,
                               e=eintrag, token=token)
    typ = request.form.get("typ", eintrag["typ"])
    if typ not in ("gesuch", "angebot"):
        typ = eintrag["typ"]
    titel = (request.form.get("titel") or "").strip() or eintrag["titel"]
    kategorie = (request.form.get("kategorie") or "").strip()
    if kategorie not in _pinnwand_kategorien(einst):
        kategorie = ""
    sprachen = _pinnwand_sprachen_lesen() or eintrag["sprachen"]
    kontakt = (request.form.get("kontakt") or "").strip() or eintrag["gast_kontakt"]
    conn = get_db_fuer(vid)
    # Änderungen gehen sicherheitshalber erneut durch die Freigabe
    conn.execute("UPDATE pinnwand SET titel=?, text=?, typ=?, kategorie=?, sprachen=?, "
                 "gast_kontakt=?, moderation=1 WHERE gast_token=? AND verein_id=?",
                 (titel, (request.form.get("text") or "").strip(), typ, kategorie,
                  sprachen, kontakt, token, vid))
    conn.commit()
    conn.close()
    try:
        admins = benutzer_ids_mit_rolle(["admin"], vid)
        if admins:
            push.senden(admins, "🕒 Geänderter Gast-Eintrag wartet auf Freigabe", titel,
                        url_for("kommunikation.schwarzes_brett"), thema="neuigkeiten")
    except Exception:  # noqa: BLE001
        pass
    eintrag = _brett_gast_eintrag(token)
    return render_template("portal/brett_gast_bearbeiten.html", gespeichert=True,
                           kategorien=_pinnwand_kategorien(einst),
                           sprachen_auswahl=PINNWAND_SPRACHEN,
                           sprachen_an=bool(einst["portal_sprachen"] if "portal_sprachen" in einst.keys() else 0),
                           e=eintrag, token=token)


@bp.route("/brett/aktuell/<token>")
def brett_aktuell(token):
    """Bestätigungs-Link aus der Aktualitäts-Prüf-Mail: ein Klick genügt.

    Setzt bestaetigt_am neu (Prüf-Uhr startet von vorn) und holt einen
    inzwischen automatisch archivierten Eintrag wieder zurück (aktiv=1) –
    wer zu spät klickt, verliert seinen Eintrag also nicht."""
    from datetime import datetime as _dt
    # Vereinsübergreifend: der Bestätigungs-Link aus der Mail soll unabhängig
    # vom gerade aktiven Verein funktionieren.
    conn, row = oeffentliche_suche(
        "SELECT id, verein_id FROM pinnwand WHERE pruef_token=? AND pruef_token!=''", token)
    if row:
        conn.execute("UPDATE pinnwand SET bestaetigt_am=?, pruef_token='', "
                     "pruef_gesendet_am='', aktiv=1 WHERE id=?",
                     (_dt.now().strftime("%Y-%m-%d %H:%M:%S"), row["id"]))
        conn.commit()
    conn.close()
    einst = einstellungen_fuer(row["verein_id"]) if row else get_einstellungen()
    if not einst:
        abort(404)
    return render_template("portal/brett_aktuell.html", gueltig=row is not None,
                           sprachen_an=bool(einst.get("portal_sprachen") or 0))


# --------------------------------------------------------------------------- #
#  Einlass aus der Mitglieder-App: nur Vorstand und Schichteninhaber:innen
#  des Termins – Gäste (extern) nie. Mehrere Geräte gleichzeitig sind ok,
#  der Stand wird laufend abgeglichen.
# --------------------------------------------------------------------------- #
def _einlass_erlaubt(conn, tid):
    """Darf die angemeldete Person am Termin einchecken?"""
    if getattr(current_user, "rolle", "") == "admin":
        return True
    if getattr(current_user, "rolle", "") == "extern":
        return False
    m = _mein_mitglied()
    if not m:
        return False
    return conn.execute(
        "SELECT 1 FROM schicht_slots sl JOIN schichten s ON s.id=sl.schicht_id "
        "WHERE s.termin_id=? AND sl.mitglied_id=?", (tid, m["id"])).fetchone() is not None


def _einlass_termin(conn, tid):
    from .veranstaltungen import _verein_modul_an
    vid = aktiver_verein_id()
    t = conn.execute("SELECT * FROM termine WHERE id=? AND verein_id=? AND aktiv=1 "
                     "AND anmeldung_aktiv=1", (tid, vid)).fetchone()
    if not t or not _verein_modul_an(conn, vid):
        return None
    return t


@bp.route("/einlass/<int:tid>")
@login_required
def einlass(tid):
    conn = get_db()
    t = _einlass_termin(conn, tid)
    if not t or not _einlass_erlaubt(conn, tid):
        conn.close()
        flash("Der Einlass steht nur dem Vorstand und den eingeteilten "
              "Schichteninhaber:innen dieses Termins offen.", "fehler")
        return redirect(url_for("portal.termin", tid=tid))
    from .veranstaltungen import einlass_seite_daten, einlass_auto_schliessen
    da, gesamt, suchliste = einlass_seite_daten(conn, tid)
    start = einlass_auto_schliessen(conn, t)
    conn.close()
    urls = {"pruefen": url_for("portal.einlass_pruefen", tid=tid),
            "checkin": url_for("portal.einlass_checkin", tid=tid),
            "stand": url_for("portal.einlass_stand", tid=tid),
            "schalter": url_for("portal.einlass_schalter", tid=tid),
            "zurueck": url_for("portal.termin", tid=tid)}
    return render_template("veranstaltungen/einlass.html", t=t, da=da, gesamt=gesamt,
                           suchliste=suchliste, urls=urls,
                           einlass_start=start[11:16] if start else "")


@bp.route("/einlass/<int:tid>/pruefen", methods=["POST"])
@login_required
def einlass_pruefen(tid):
    from flask import jsonify as _jsonify
    from .veranstaltungen import einlass_pruefen as _va_pruefen
    conn = get_db()
    ok = _einlass_termin(conn, tid) and _einlass_erlaubt(conn, tid)
    conn.close()
    if not ok:
        return _jsonify({"status": "rot", "gruende": ["Kein Zugriff auf den Einlass."]}), 403
    return _va_pruefen(tid)


@bp.route("/einlass/<int:tid>/checkin", methods=["POST"])
@login_required
def einlass_checkin(tid):
    from flask import jsonify as _jsonify
    from .veranstaltungen import einlass_checkin as _va_checkin
    conn = get_db()
    ok = _einlass_termin(conn, tid) and _einlass_erlaubt(conn, tid)
    a_tid = None
    if ok:
        aid = (request.get_json(silent=True) or {}).get("anmeldung_id")
        z = conn.execute("SELECT termin_id FROM veranstaltung_anmeldung WHERE id=? AND verein_id=?",
                         (aid, aktiver_verein_id())).fetchone() if aid else None
        a_tid = z["termin_id"] if z else None
    conn.close()
    # Schichteninhaber:innen dürfen nur an IHREM Termin einchecken
    if not ok or a_tid != tid:
        return _jsonify({"status": "rot", "gruende": ["Kein Zugriff auf den Einlass."]}), 403
    return _va_checkin()


@bp.route("/einlass/<int:tid>/stand")
@login_required
def einlass_stand(tid):
    from flask import jsonify as _jsonify
    from .veranstaltungen import einlass_stand_paket
    conn = get_db()
    if not (_einlass_termin(conn, tid) and _einlass_erlaubt(conn, tid)):
        conn.close()
        return _jsonify({}), 403
    paket = einlass_stand_paket(conn, tid)
    conn.close()
    return _jsonify(paket)


@bp.route("/einlass/<int:tid>/schalter", methods=["POST"])
@login_required
def einlass_schalter(tid):
    from flask import jsonify as _jsonify
    from .veranstaltungen import einlass_schalten, einlass_stand_paket
    conn = get_db()
    if not (_einlass_termin(conn, tid) and _einlass_erlaubt(conn, tid)):
        conn.close()
        return _jsonify({}), 403
    einlass_schalten(conn, tid, (request.get_json(silent=True) or {}).get("aktion") or "start")
    paket = einlass_stand_paket(conn, tid)
    conn.close()
    return _jsonify(paket)


# Modul „Verteilen": Helfer:innen-Seiten hängen am Portal-Blueprint, liegen aber
# in einer eigenen Datei (Endpunkte portal.verteilen_*).
from . import portal_verteilen  # noqa: E402,F401
