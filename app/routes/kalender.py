"""
Kalender – übergeordnete Ebene über den Terminen.

Vereint eigene Veranstaltungen (Termine mit Teilnehmern & Schichten) und externe
Kalender (ICS-Abo, Weblink). Ansichten: Liste, Woche, Monat, Jahr.
"""
import calendar as _cal
import os
from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response, abort
from ..db import get_db, get_db_fuer, get_einstellungen, now_iso, aktiver_verein_id
from ..design import sichere_farbe
from .. import ics, caldav, ics_export, termin_maske
from .. import db as _db

bp = Blueprint("kalender", __name__, url_prefix="/kalender")

PRO_SEITE = 25  # Termine je Seite in der Listenansicht


WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]

# Standard-Kategorien (Name, Farbe) – werden je Verein einmalig angelegt
STANDARD_KATEGORIEN = [
    ("Vorstand / Sitzung", "#2563eb"),
    ("Kino / Filmvorführung", "#dc2626"),
    ("Veranstaltung / Fest", "#d97706"),
    ("Probe / Workshop", "#16a34a"),
    ("Versammlung", "#7c3aed"),
    ("Sonstiges", "#64748b"),
]
FARBE_EXTERN = "#94a3b8"


def _sichere_kategorien(conn, vid):
    if conn.execute("SELECT COUNT(*) c FROM termin_kategorien WHERE verein_id=?", (vid,)).fetchone()["c"] == 0:
        for name, farbe in STANDARD_KATEGORIEN:
            conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?,?,?)",
                         (vid, name, farbe))
        conn.commit()


# --------------------------------------------------------------------------
# Termine (eigene + externe) für einen Zeitraum einsammeln
# --------------------------------------------------------------------------
def _events_range(conn, vid, von, bis):
    """Gibt ein Dict {ISO-Datum: [event, ...]} für den Zeitraum [von, bis] zurück."""
    tage = {}
    eigene = conn.execute(
        "SELECT t.id, t.titel, t.datum, t.uhrzeit, t.ort, t.beschreibung, t.aktiv, t.serie_id, k.farbe kat_farbe, k.name kat_name "
        "FROM termine t LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
        "WHERE t.verein_id=? AND t.datum>=? AND t.datum<=? ORDER BY t.datum, t.uhrzeit",
        (vid, von.isoformat(), bis.isoformat())).fetchall()
    for t in eigene:
        tage.setdefault(t["datum"][:10], []).append(
            {"art": "eigen", "id": t["id"], "titel": t["titel"], "datum": t["datum"],
             "uhrzeit": t["uhrzeit"] or "", "ort": t["ort"] or "", "beschreibung": t["beschreibung"] or "",
             "farbe": t["kat_farbe"] or "#2563eb", "kategorie": t["kat_name"] or "", "aktiv": t["aktiv"],
             "serie_id": t["serie_id"]})

    quellen = conn.execute("SELECT * FROM kalender WHERE verein_id=? AND typ='ics'", (vid,)).fetchall()
    if quellen:
        heute = date.today()
        max_tage = max(1, (bis - heute).days + 2) if bis >= heute else 1
        for q in quellen:
            if not q["url"]:
                continue
            liste, _ = ics.termine_laden(q["url"], max_tage=max_tage, grenze=300)
            for ev in liste:
                if (ev.get("uid") or "").startswith("vv-"):
                    continue  # von dieser App erzeugt – schon als „eigene" vorhanden
                start = ev["start"]
                d = start.date() if isinstance(start, datetime) else start
                if von <= d <= bis:
                    tage.setdefault(d.isoformat(), []).append(
                        {"art": "extern", "titel": ev.get("titel", ""), "datum": d.isoformat(),
                         "uhrzeit": "" if ev.get("ganztag") else (start.strftime("%H:%M") if isinstance(start, datetime) else ""),
                         "ort": ev.get("ort", ""), "beschreibung": ev.get("beschreibung", ""),
                         "quelle": q["name"], "farbe": FARBE_EXTERN, "aktiv": 1})
    for k in tage:
        tage[k].sort(key=lambda x: x["uhrzeit"])
    return tage


def _nav(prev, nxt, heute):
    return {"prev_iso": prev.isoformat(), "next_iso": nxt.isoformat(), "heute_iso": heute.isoformat()}


# --------------------------------------------------------------------------
# Hauptansicht mit Umschalter (Liste / Woche / Monat / Jahr)
# --------------------------------------------------------------------------
@bp.route("/")
def index():
    conn = get_db()
    vid = aktiver_verein_id()
    _sichere_kategorien(conn, vid)
    nc = caldav.konfiguriert(get_einstellungen())  # Nextcloud eingerichtet?
    ansicht = request.args.get("ansicht", "liste")
    if ansicht not in ("liste", "woche", "monat", "jahr"):
        ansicht = "liste"
    try:
        ref = date.fromisoformat(request.args.get("d", ""))
    except ValueError:
        ref = date.today()
    heute = date.today()

    if ansicht == "woche":
        start = ref - timedelta(days=ref.weekday())
        ende = start + timedelta(days=6)
        tage = _events_range(conn, vid, start, ende)
        conn.close()
        days = [{"name": WOCHENTAGE[i], "datum": start + timedelta(days=i),
                 "heute": (start + timedelta(days=i)) == heute,
                 "events": tage.get((start + timedelta(days=i)).isoformat(), [])} for i in range(7)]
        titel = f"Woche {start.strftime('%d.%m.')} – {ende.strftime('%d.%m.%Y')}"
        return render_template("kalender/woche.html", aktiv="kalender", ansicht="woche",
                               ref_iso=ref.isoformat(), days=days, titel=titel, nc_konfiguriert=nc,
                               **_nav(ref - timedelta(days=7), ref + timedelta(days=7), heute))

    if ansicht == "monat":
        von = ref.replace(day=1)
        naechster = (von.replace(day=28) + timedelta(days=4)).replace(day=1)
        bis = naechster - timedelta(days=1)
        tage = _events_range(conn, vid, von, bis)
        conn.close()
        wochen = []
        for woche in _cal.Calendar(firstweekday=0).monthdatescalendar(ref.year, ref.month):
            wochen.append([{"tag": d.day, "iso": d.isoformat(), "im_monat": d.month == ref.month,
                            "heute": d == heute, "events": tage.get(d.isoformat(), [])} for d in woche])
        titel = f"{MONATE[ref.month - 1]} {ref.year}"
        prev = (von - timedelta(days=1)).replace(day=1)
        return render_template("kalender/monat.html", aktiv="kalender", ansicht="monat",
                               ref_iso=ref.isoformat(), wochen=wochen, titel=titel, nc_konfiguriert=nc,
                               wochentage=WOCHENTAGE, **_nav(prev, naechster, heute))

    if ansicht == "jahr":
        von, bis = date(ref.year, 1, 1), date(ref.year, 12, 31)
        tage = _events_range(conn, vid, von, bis)
        conn.close()
        monate = []
        for m in range(1, 13):
            wochen, anzahl = [], 0
            for woche in _cal.Calendar(firstweekday=0).monthdatescalendar(ref.year, m):
                zeile = []
                for d in woche:
                    ev = tage.get(d.isoformat(), []) if d.month == m else []
                    if d.month == m:
                        anzahl += len(ev)
                    zeile.append({"tag": d.day, "im_monat": d.month == m,
                                  "heute": d == heute, "hat": bool(ev),
                                  "farbe": ev[0]["farbe"] if ev else None,
                                  "iso": d.isoformat(),
                                  "titel": " · ".join(x["titel"] for x in ev) if ev else ""})
                wochen.append(zeile)
            monate.append({"name": MONATE[m - 1], "wochen": wochen, "anzahl": anzahl,
                           "erster_iso": date(ref.year, m, 1).isoformat()})
        return render_template("kalender/jahr.html", aktiv="kalender", ansicht="jahr",
                               ref_iso=ref.isoformat(), monate=monate, titel=str(ref.year),
                               wochentage=WOCHENTAGE, nc_konfiguriert=nc,
                               **_nav(date(ref.year - 1, 1, 1), date(ref.year + 1, 1, 1), heute))

    # ------------------------------ Liste (Standard) ----------------------
    ctx = _liste_ctx(conn, vid, heute.isoformat())
    conn.close()

    # Kategorie-Filter (basierend auf den Kategorien der vorhandenen kommenden Termine)
    kat = (request.args.get("kat") or "").strip()
    kat_vorhanden = sorted({e["kategorie"] for e in ctx["kommend"] if e.get("kategorie")}, key=str.lower)
    if kat:
        ctx["kommend"] = [e for e in ctx["kommend"] if e.get("kategorie") == kat]

    def _paginate(items, seite):
        seiten = max(1, (len(items) + PRO_SEITE - 1) // PRO_SEITE)
        seite = min(max(1, seite), seiten)
        start = (seite - 1) * PRO_SEITE
        return items[start:start + PRO_SEITE], seite, seiten, len(items)

    k_items, k_seite, k_seiten, k_gesamt = _paginate(ctx["kommend"], request.args.get("sk", type=int) or 1)
    v_items, v_seite, v_seiten, v_gesamt = _paginate(ctx["vergangen"], request.args.get("sv", type=int) or 1)
    ctx["kommend"], ctx["vergangen"] = k_items, v_items
    return render_template("kalender/index.html", aktiv="kalender", ansicht="liste",
                           ref_iso=ref.isoformat(), titel=None, nc_konfiguriert=nc,
                           k_seite=k_seite, k_seiten=k_seiten, k_gesamt=k_gesamt,
                           v_seite=v_seite, v_seiten=v_seiten, v_gesamt=v_gesamt,
                           aktiver_kat=kat, kat_vorhanden=kat_vorhanden, **ctx)


def _liste_ctx(conn, vid, heute):
    eigene = conn.execute(
        "SELECT t.*, k.farbe kat_farbe, k.name kat_name, "
        "(SELECT COUNT(*) FROM teilnahmen tn WHERE tn.termin_id=t.id AND tn.status='zugesagt') zusagen, "
        "(SELECT COUNT(*) FROM schichten s WHERE s.termin_id=t.id) schichten, "
        "(SELECT COUNT(*) FROM veranstaltung_teilnehmer vt JOIN veranstaltung_anmeldung va ON va.id=vt.anmeldung_id "
        " WHERE va.termin_id=t.id AND va.storniert=0 AND va.warteliste=0) anmeldungen "
        "FROM termine t LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
        "WHERE t.verein_id=? ORDER BY t.datum, t.uhrzeit", (vid,)).fetchall()
    kommend, vergangen = [], []
    for t in eigene:
        eintrag = {"art": "eigen", "id": t["id"], "titel": t["titel"], "datum": t["datum"],
                   "uhrzeit": t["uhrzeit"], "ort": t["ort"], "zusagen": t["zusagen"],
                   "schichten": t["schichten"], "farbe": t["kat_farbe"] or "#2563eb",
                   "kategorie": t["kat_name"] or "", "beschreibung": t["beschreibung"] or "",
                   "aktiv": t["aktiv"], "anmeldung_aktiv": t["anmeldung_aktiv"],
                   "anmeldungen": t["anmeldungen"], "max_teilnehmer": t["max_teilnehmer"],
                   "serie_id": t["serie_id"],
                   "sortdatum": (t["datum"] or "9999") + (t["uhrzeit"] or "")}
        (kommend if (t["datum"] or "") >= heute else vergangen).append(eintrag)

    quellen = conn.execute("SELECT * FROM kalender WHERE verein_id=? ORDER BY typ, name", (vid,)).fetchall()
    fehler = []
    for q in quellen:
        if q["typ"] == "ics" and q["url"]:
            liste, err = ics.termine_laden(q["url"])
            if err:
                fehler.append(f"{q['name']}: {err}")
            for t in liste:
                if (t.get("uid") or "").startswith("vv-"):
                    continue  # von dieser App erzeugt – schon als „eigene" vorhanden
                start = t["start"]
                d = start.date().isoformat() if isinstance(start, datetime) else start.isoformat()
                kommend.append({"art": "extern", "titel": t.get("titel", ""), "datum": d,
                                "uhrzeit": "" if t.get("ganztag") else (start.strftime("%H:%M") if isinstance(start, datetime) else ""),
                                "ort": t.get("ort", ""), "quelle": q["name"], "farbe": FARBE_EXTERN,
                                "beschreibung": t.get("beschreibung", ""), "aktiv": 1,
                                "sortdatum": d + ("" if t.get("ganztag") else (start.strftime("%H%M") if isinstance(start, datetime) else ""))})
    kommend.sort(key=lambda x: x["sortdatum"])
    vergangen.sort(key=lambda x: x["sortdatum"], reverse=True)
    # Vorlagen für „Veranstaltung kopieren" (bisherige eigene Veranstaltungen)
    vorlagen = conn.execute(
        "SELECT id, titel, datum, uhrzeit, ort, beschreibung, kategorie_id, "
        "(SELECT COUNT(*) FROM schichten s WHERE s.termin_id=termine.id) schichten "
        "FROM termine WHERE verein_id=? ORDER BY datum DESC", (vid,)).fetchall()
    kategorien = conn.execute("SELECT * FROM termin_kategorien WHERE verein_id=? ORDER BY name", (vid,)).fetchall()
    return {"kommend": kommend, "vergangen": vergangen,
            "weblinks": [q for q in quellen if q["typ"] == "weblink"],
            "fehler": fehler, "anzahl_quellen": len(quellen), "vorlagen": vorlagen,
            "kategorien": kategorien}


# ------------------------- Externe Quellen verwalten ---------------------
@bp.route("/quellen")
def quellen():
    conn = get_db()
    vid = aktiver_verein_id()
    quellen = conn.execute("SELECT * FROM kalender WHERE verein_id=? ORDER BY typ, name", (vid,)).fetchall()
    conn.close()
    return render_template("kalender/quellen.html", aktiv="kalender",
                           quellen=quellen,
                           nc_konfiguriert=caldav.konfiguriert(get_einstellungen()))


@bp.route("/import-veranstaltungen", methods=["POST"])
def import_veranstaltungen():
    """Stufe 1: Veranstaltungen der Quelle (auf-nach-mv.de) holen und zur AUSWAHL
    vorlegen – importiert wird erst nach Bestätigung (Stufe 2)."""
    from .. import eventimport
    einst = get_einstellungen()
    url = (einst["event_import_url"] or "").strip() if "event_import_url" in einst.keys() else ""
    if not url:
        flash("Keine Import-Quelle hinterlegt. Bitte unter Einstellungen die Veranstaltungs-Quelle eintragen.", "fehler")
        return redirect(url_for("kalender.index"))
    evs, fehler = eventimport.hole_events(url, ab_datum=date.today().isoformat())
    if fehler:
        flash("Import nicht möglich: " + fehler, "fehler")
        return redirect(url_for("kalender.index"))
    conn = get_db()
    vid = aktiver_verein_id()
    liste = _import_klassifizieren(conn, vid, evs)
    conn.close()
    # Zwischenspeichern (Einzelplatz-Muster): Stufe 2 liest genau diesen Stand,
    # statt alle Detailseiten erneut abzurufen.
    import json as _json
    with open(_vorschau_pfad(vid), "w", encoding="utf-8") as f:
        _json.dump({"zeit": now_iso(), "liste": liste}, f)
    anz = {"neu": 0, "ergaenzen": 0, "aktuell": 0}
    for e in liste:
        anz[e["status"]] += 1
    return render_template("kalender/import_auswahl.html", aktiv="kalender",
                           liste=liste, anz=anz)


def _vorschau_pfad(vid):
    from ..db import DATA_DIR
    return os.path.join(DATA_DIR, f"import_vorschau_{vid}.json")


def _besch_norm(t):
    """Beschreibungen vergleichbar machen: Leerraum glätten, Kleinschreibung."""
    import re as _re
    return _re.sub(r"\s+", " ", (t or "")).strip().lower()


def _import_klassifizieren(conn, vid, evs):
    """Jede Quelle-Veranstaltung einordnen: neu / ergaenzen (mit Feldliste) / aktuell."""
    vorhanden = {}
    for r in conn.execute("SELECT id, titel, datum, beschreibung, uhrzeit, uhrzeit_bis, ort, bild, quelle_url "
                          "FROM termine WHERE verein_id=?", (vid,)).fetchall():
        if r["datum"]:
            vorhanden[(r["titel"].strip().lower(), (r["datum"] or "")[:10])] = r
    liste = []
    for e in evs:
        alt = vorhanden.get((e["titel"].strip().lower(), e["datum"]))
        if alt is None:
            liste.append({**e, "status": "neu", "felder": [], "alt_id": None})
            continue
        neue_werte, felder = {}, []
        alt_n = _besch_norm(alt["beschreibung"])
        neu_n = _besch_norm(e["beschreibung"])
        # Beschreibung ersetzen, wenn sie fehlt, der alte Anreißer im neuen Text
        # steckt (leerraumtolerant) oder der neue Text deutlich ausführlicher ist.
        if neu_n and neu_n != alt_n and (not alt_n or alt_n in neu_n
                                         or len(neu_n) > 2 * len(alt_n)):
            neue_werte["beschreibung"] = e["beschreibung"]
            felder.append("Beschreibung")
        for feld, wert in (("uhrzeit", e["uhrzeit"]), ("uhrzeit_bis", e["uhrzeit_bis"]),
                           ("ort", e["ort"]), ("quelle_url", e.get("quelle_url", ""))):
            if wert and not (alt[feld] or "").strip():
                neue_werte[feld] = wert
                felder.append(feld)
        if e["bild_url"] and not (alt["bild"] or "").strip():
            felder.append("Bild")
        liste.append({**e, "status": "ergaenzen" if felder else "aktuell",
                      "felder": felder, "alt_id": alt["id"],
                      "neue_werte": neue_werte})
    return liste


@bp.route("/import-ausfuehren", methods=["POST"])
def import_ausfuehren():
    """Stufe 2: Nur die AUSGEWÄHLTEN Veranstaltungen anlegen bzw. ergänzen."""
    from .. import eventimport
    from .termine import event_bild_speichern
    import json as _json
    vid = aktiver_verein_id()
    try:
        with open(_vorschau_pfad(vid), encoding="utf-8") as f:
            stand = _json.load(f)
    except (OSError, ValueError):
        flash("Die Import-Vorschau ist abgelaufen – bitte noch einmal abrufen.", "fehler")
        return redirect(url_for("kalender.index"))
    gewaehlt = set(request.form.getlist("uebernehmen"))
    conn = get_db()
    kategorien = conn.execute("SELECT id, name FROM termin_kategorien WHERE verein_id=?", (vid,)).fetchall()
    neu, mit_bild, mit_kat, ergaenzt = 0, 0, 0, 0
    for i, e in enumerate(stand["liste"]):
        if str(i) not in gewaehlt or e["status"] == "aktuell":
            continue
        try:
            if e["status"] == "neu":
                kid = eventimport.kategorie_finden(e["titel"], kategorien)
                cur = conn.execute(
                    "INSERT INTO termine (verein_id, titel, beschreibung, datum, uhrzeit, uhrzeit_bis, ort, kategorie_id, quelle_url, aktiv, erstellt_am, erstellt_von) "
                    "VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
                    (vid, e["titel"], e["beschreibung"], e["datum"], e["uhrzeit"], e["uhrzeit_bis"], e["ort"],
                     kid, e.get("quelle_url", ""), now_iso(), "Import"))
                if kid:
                    mit_kat += 1
                tid = cur.lastrowid
                neu += 1
                if e["bild_url"]:
                    fn = event_bild_speichern(tid, e["bild_url"], vid)
                    if fn:
                        conn.execute("UPDATE termine SET bild=? WHERE id=?", (fn, tid))
                        mit_bild += 1
            else:  # ergaenzen
                neue_werte = e.get("neue_werte") or {}
                if neue_werte:
                    conn.execute("UPDATE termine SET " + ", ".join(f"{k}=?" for k in neue_werte)
                                 + " WHERE id=? AND verein_id=?",
                                 list(neue_werte.values()) + [e["alt_id"], vid])
                if "Bild" in e["felder"] and e["bild_url"]:
                    fn = event_bild_speichern(e["alt_id"], e["bild_url"], vid)
                    if fn:
                        conn.execute("UPDATE termine SET bild=? WHERE id=?", (fn, e["alt_id"]))
                ergaenzt += 1
            if (neu + ergaenzt) % 5 == 0:    # laufend sichern (timeout-fest)
                conn.commit()
        except Exception:  # noqa: BLE001 – ein fehlerhafter Einzeltermin darf den Rest nicht stoppen
            continue
    conn.commit()
    conn.close()
    try:
        os.remove(_vorschau_pfad(vid))
    except OSError:
        pass
    nachtrag = f" Bei {ergaenzt} vorhandenen Terminen wurden fehlende Angaben ergänzt." if ergaenzt else ""
    if neu:
        flash(f"{neu} neue Termine importiert ({mit_bild} mit Bild, {mit_kat} mit Kategorie)." + nachtrag, "erfolg")
    elif ergaenzt:
        flash(f"Bei {ergaenzt} vorhandenen Terminen wurden fehlende Angaben nachgetragen.", "erfolg")
    else:
        flash("Nichts ausgewählt – es wurde nichts importiert.", "info")
    return redirect(url_for("kalender.index"))


@bp.route("/export")
def export():
    """Export-/Abo-Seite: .ics herunterladen oder Kalender abonnieren (Gmail/Outlook/Apple …)."""
    vid = aktiver_verein_id()
    token = _db.feed_token(vid)
    host = request.host
    schema = "http" if host.startswith(("localhost", "127.")) else "https"
    feed = f"{schema}://{host}" + url_for("kalender.abo", token=token)
    return render_template("kalender/export.html", aktiv="kalender", feed=feed,
                           webcal=feed.replace("https://", "webcal://").replace("http://", "webcal://"))


@bp.route("/export.ics")
def download_ics():
    vid = aktiver_verein_id()
    conn = get_db()
    rows = conn.execute("SELECT id, titel, beschreibung, datum, uhrzeit, ort FROM termine "
                        "WHERE verein_id=? AND aktiv=1 AND datum!=''", (vid,)).fetchall()
    einst = get_einstellungen()
    conn.close()
    ics_text = ics_export.termine_ics([dict(r) for r in rows], name=f"{einst['vereinsname']} – Termine", vid=vid)
    return Response(ics_text, mimetype="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=termine.ics"})


@bp.route("/abo/<token>.ics", endpoint="abo")
def abo(token):
    """Abonnier-Feed für fremde Kalender-Apps – erreichbar ohne Anmeldung, nur
    über das Token. Interne Termine erscheinen deshalb anonymisiert: Wer den
    Link weitergibt (oder wessen Kalender-App ihn weiterreicht), gibt keine
    Tagesordnungen mit heraus."""
    vid = _db.verein_per_feed_token(token)
    if not vid:
        abort(404)
    conn = get_db_fuer(vid)
    rows = conn.execute("SELECT t.id, t.titel, t.beschreibung, t.datum, t.uhrzeit, t.ort, t.nur_mitglieder, "
                        "k.name AS kategorie FROM termine t "
                        "LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
                        "WHERE t.verein_id=? AND t.aktiv=1 AND t.datum!=''", (vid,)).fetchall()
    name = conn.execute("SELECT vereinsname FROM vereine WHERE id=?", (vid,)).fetchone()["vereinsname"]
    conn.close()
    ics_text = ics_export.termine_ics(termin_maske.maskiere_liste(rows),
                                      name=f"{name} – Termine", vid=vid)
    return Response(ics_text, mimetype="text/calendar; charset=utf-8")


@bp.route("/import", methods=["GET", "POST"])
def ics_import():
    if request.method == "POST":
        datei = request.files.get("datei")
        if not datei or not datei.filename:
            flash("Bitte eine .ics-Datei auswählen.", "fehler")
            return redirect(url_for("kalender.ics_import"))
        text = datei.read().decode("utf-8", errors="replace")
        events = ics.alle_events(text)
        aktiv = 0 if request.form.get("status") == "entwurf" else 1
        conn = get_db()
        vid = aktiver_verein_id()
        n = 0
        for e in events:
            if not e["datum"]:
                continue
            conn.execute(
                "INSERT INTO termine (verein_id, titel, beschreibung, datum, uhrzeit, ort, aktiv, erstellt_am, erstellt_von) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (vid, e["titel"], e["beschreibung"], e["datum"], e["uhrzeit"], e["ort"], aktiv, now_iso(), "ICS-Import"))
            n += 1
        conn.commit()
        conn.close()
        flash(f"{n} Termine importiert{' (als Entwurf)' if not aktiv else ''}. Sie sind jetzt bearbeitbar.", "erfolg")
        return redirect(url_for("kalender.index"))
    return render_template("kalender/import.html", aktiv="kalender")


@bp.route("/neu", methods=["POST"])
def neu():
    conn = get_db()
    conn.execute(
        "INSERT INTO kalender (verein_id, name, typ, url, erstellt_am) VALUES (?,?,?,?,?)",
        (aktiver_verein_id(), request.form.get("name", "").strip() or "Kalender",
         request.form.get("typ", "ics"), request.form.get("url", "").strip(), now_iso()))
    conn.commit()
    conn.close()
    flash("Kalenderquelle hinzugefügt.", "erfolg")
    return redirect(url_for("kalender.quellen"))


@bp.route("/<int:kid>/bearbeiten", methods=["GET", "POST"])
def bearbeiten(kid):
    conn = get_db()
    vid = aktiver_verein_id()
    q = conn.execute("SELECT * FROM kalender WHERE id=? AND verein_id=?", (kid, vid)).fetchone()
    if not q:
        conn.close()
        flash("Kalenderquelle nicht gefunden.", "fehler")
        return redirect(url_for("kalender.quellen"))
    if request.method == "POST":
        conn.execute("UPDATE kalender SET name=?, typ=?, url=? WHERE id=? AND verein_id=?",
                     (request.form.get("name", "").strip() or "Kalender",
                      request.form.get("typ", "ics"), request.form.get("url", "").strip(), kid, vid))
        conn.commit()
        conn.close()
        flash("Kalenderquelle aktualisiert.", "erfolg")
        return redirect(url_for("kalender.quellen"))
    conn.close()
    return render_template("kalender/bearbeiten.html", aktiv="kalender", q=q)


@bp.route("/<int:kid>/loeschen", methods=["POST"])
def loeschen(kid):
    conn = get_db()
    conn.execute("DELETE FROM kalender WHERE id=? AND verein_id=?", (kid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Kalenderquelle entfernt.", "erfolg")
    return redirect(url_for("kalender.quellen"))


# ------------------------------ Nextcloud --------------------------------
@bp.route("/nextcloud")
def nextcloud():
    einst = get_einstellungen()
    return render_template("kalender/nextcloud.html", aktiv="kalender", e=einst,
                           zugang=caldav.zugang_da(einst), konfiguriert=caldav.konfiguriert(einst))


@bp.route("/nextcloud/suchen", methods=["POST"])
def nextcloud_suchen():
    einst = get_einstellungen()
    gefunden, fehler = caldav.kalender_finden(einst)
    if fehler:
        flash("Kalender-Suche fehlgeschlagen: " + fehler, "fehler")
        return redirect(url_for("kalender.nextcloud"))
    return render_template("kalender/nextcloud.html", aktiv="kalender", e=einst,
                           zugang=True, konfiguriert=caldav.konfiguriert(einst), gefunden=gefunden)


@bp.route("/nextcloud/waehlen", methods=["POST"])
def nextcloud_waehlen():
    url = request.form.get("url", "").strip()
    conn = get_db()
    conn.execute("UPDATE vereine SET ncc_url=? WHERE id=?", (url, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Ziel-Kalender gespeichert – neue Veranstaltungen erscheinen ab jetzt in Nextcloud.", "erfolg")
    return redirect(url_for("kalender.nextcloud"))


@bp.route("/nextcloud/abo", methods=["POST"])
def nextcloud_abo():
    ics_url = caldav.oeffentlich_zu_ics(request.form.get("link", ""))
    if not ics_url:
        flash("Bitte einen gültigen Nextcloud-Kalenderlink angeben.", "fehler")
        return redirect(url_for("kalender.nextcloud"))
    conn = get_db()
    conn.execute("INSERT INTO kalender (verein_id, name, typ, url, erstellt_am) VALUES (?,?,?,?,?)",
                 (aktiver_verein_id(), "Nextcloud-Kalender", "ics", ics_url, now_iso()))
    conn.commit()
    conn.close()
    flash("Nextcloud-Kalender als Abo hinzugefügt – die Termine erscheinen jetzt im Kalender.", "erfolg")
    return redirect(url_for("kalender.index"))


@bp.route("/nextcloud/sync", methods=["POST"])
def nextcloud_sync():
    einst = get_einstellungen()
    if not caldav.konfiguriert(einst):
        flash("Bitte zuerst den Nextcloud-Zugang unter Einstellungen hinterlegen.", "fehler")
        return redirect(url_for("einstellungen.index"))
    conn = get_db()
    vid = aktiver_verein_id()
    termine = conn.execute(
        "SELECT t.id, t.titel, t.datum, t.uhrzeit, t.ort, t.beschreibung, t.aktiv, "
        "t.nur_mitglieder, k.name AS kategorie FROM termine t "
        "LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id WHERE t.verein_id=?", (vid,)).fetchall()
    conn.close()
    erfolg, fehler, entwuerfe = 0, 0, 0
    for t in termine:
        if t["aktiv"]:
            ok, _ = caldav.push_termin(einst, dict(t), vid)
            erfolg += 1 if ok else 0
            fehler += 0 if ok else 1
        else:
            # Entwürfe nicht veröffentlichen – ggf. aus Nextcloud entfernen
            caldav.delete_termin(einst, vid, t["id"])
            entwuerfe += 1
    meldung = f"Nextcloud: {erfolg} veröffentlichte Termine übertragen"
    if entwuerfe:
        meldung += f", {entwuerfe} Entwurf/Entwürfe nicht veröffentlicht"
    meldung += (f", {fehler} fehlgeschlagen." if fehler else ".")
    flash(meldung, "erfolg" if fehler == 0 else "info")
    return redirect(url_for("kalender.index"))


# ------------------------------ Kategorien -------------------------------
@bp.route("/kategorien", methods=["GET", "POST"])
def kategorien():
    conn = get_db()
    vid = aktiver_verein_id()
    _sichere_kategorien(conn, vid)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            conn.execute("INSERT INTO termin_kategorien (verein_id, name, farbe) VALUES (?,?,?)",
                         (vid, name, sichere_farbe(request.form.get("farbe"))))
            conn.commit()
            flash(f"Kategorie „{name}“ angelegt.", "erfolg")
        conn.close()
        return redirect(url_for("kalender.kategorien"))
    liste = conn.execute(
        "SELECT k.*, (SELECT COUNT(*) FROM termine t WHERE t.kategorie_id=k.id) anzahl "
        "FROM termin_kategorien k WHERE k.verein_id=? ORDER BY k.name", (vid,)).fetchall()
    conn.close()
    return render_template("kalender/kategorien.html", aktiv="kalender", kategorien=liste)


@bp.route("/kategorien/<int:kat_id>/speichern", methods=["POST"])
def kategorie_speichern(kat_id):
    conn = get_db()
    vid = aktiver_verein_id()
    conn.execute("UPDATE termin_kategorien SET name=?, farbe=?, gruppe=? WHERE id=? AND verein_id=?",
                 (request.form.get("name", "").strip() or "Kategorie",
                  sichere_farbe(request.form.get("farbe")),
                  (request.form.get("gruppe") or "").strip()[:40], kat_id, vid))
    # Standardbild der Kategorie: Terminkarten ohne eigenes Bild zeigen dieses (19.11.2026)
    alt = conn.execute("SELECT bild FROM termin_kategorien WHERE id=? AND verein_id=?", (kat_id, vid)).fetchone()
    if alt is not None:
        import os
        import time
        from .termine import _event_bild_dir
        alt_name = alt["bild"] or ""
        neu_name = ""

        def _alt_weg():
            if alt_name.startswith(f"kategorie_v{vid}_{kat_id}_") and alt_name != (neu_name or ""):
                try:
                    os.remove(os.path.join(_event_bild_dir(), alt_name))
                except OSError:
                    pass
        datei = request.files.get("bild")
        if datei and datei.filename:
            endung = os.path.splitext(datei.filename)[1].lower()
            if endung in (".jpg", ".jpeg", ".png", ".webp"):
                name = neu_name = f"kategorie_v{vid}_{kat_id}_{int(time.time())}{endung}"
                datei.save(os.path.join(_event_bild_dir(), name))
                conn.execute("UPDATE termin_kategorien SET bild=? WHERE id=? AND verein_id=?", (name, kat_id, vid))
                _alt_weg()
            else:
                flash("Das Kategoriebild muss ein Foto sein (JPG, PNG oder WEBP).", "fehler")
        elif request.form.get("bild_weg"):
            conn.execute("UPDATE termin_kategorien SET bild='' WHERE id=? AND verein_id=?", (kat_id, vid))
            _alt_weg()
    conn.commit()
    conn.close()
    flash("Kategorie gespeichert.", "erfolg")
    return redirect(url_for("kalender.kategorien"))


@bp.route("/kategorien/<int:kat_id>/loeschen", methods=["POST"])
def kategorie_loeschen(kat_id):
    conn = get_db()
    vid = aktiver_verein_id()
    # Termine dieser Kategorie behalten, nur Zuordnung entfernen
    conn.execute("UPDATE termine SET kategorie_id=NULL WHERE kategorie_id=? AND verein_id=?", (kat_id, vid))
    conn.execute("DELETE FROM termin_kategorien WHERE id=? AND verein_id=?", (kat_id, vid))
    conn.commit()
    conn.close()
    flash("Kategorie gelöscht.", "erfolg")
    return redirect(url_for("kalender.kategorien"))
