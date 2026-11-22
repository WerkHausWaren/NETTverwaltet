"""Verwaltung „Lebendiger Adventskalender" (LAK).

Eine Seite für alles rund um den Kalender (nur mit modul_adventskalender):
Türchen der laufenden Saison (nach der LAK-Kategorie gefilterte
Veranstaltungsliste mit Bearbeiten-Links), der eigene Einleitungstext der
öffentlichen Seite und das Archiv vergangener Jahrgänge – je Saison ein Bild,
ein Rückblickstext und die Kennzahlen, exportierbar als PDF im Briefkopf.
Alle Routen liegen automatisch hinter dem Admin-Login (zentraler
Zugriffsschutz); nichts hier gehört in die OEFFENTLICH-Liste.
"""
import io
import os
import time

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   send_file, send_from_directory, url_for)

from .. import termin_maske
from ..db import DATA_DIR, aktiver_verein_id, get_db, get_einstellungen, now_iso

bp = Blueprint("lak", __name__, url_prefix="/lak")

ARCHIV_DIR = os.path.join(DATA_DIR, "lak_archiv")


def _modul_fehlt(einst):
    """True, wenn das Adventskalender-Modul für diesen Verein aus ist."""
    return not (einst and einst.get("modul_adventskalender"))


def _kategorien(conn, vid):
    """Alle Termin-Kategorien, deren Name normalisiert „adventskalender"
    enthält – gleiche Regel wie die öffentliche Seite (portal._lak_termine)."""
    return [k for k in conn.execute(
        "SELECT id, name FROM termin_kategorien WHERE verein_id=? ORDER BY name",
        (vid,)).fetchall()
        if "adventskalender" in termin_maske._normalisiert(k["name"])]


def _alle_termine(conn, vid, kat_ids):
    """ALLE Termine der LAK-Kategorien (auch Entwürfe, nur_mitglieder und
    Abgesagte – die Verwaltung soll alles sehen, anders als die Seite)."""
    if not kat_ids:
        return []
    ph = ",".join("?" * len(kat_ids))
    return conn.execute(
        "SELECT t.*, k.name AS kategorie FROM termine t "
        "JOIN termin_kategorien k ON k.id=t.kategorie_id "
        f"WHERE t.verein_id=? AND t.kategorie_id IN ({ph}) "
        "ORDER BY t.datum, COALESCE(t.uhrzeit,''), t.id",
        (vid, *kat_ids)).fetchall()


def _saison_statistik(conn, rows):
    """Je Saison: Zahl der Türchen und (sofern über die App angemeldet wurde)
    die feste Teilnehmerzahl – Vorschläge fürs Archiv. Zählung wie
    _stand()["teilnehmer"]: storniert=0 AND warteliste=0."""
    from .portal import _lak_saison
    stat = {}
    saison_von = {}
    for r in rows:
        s = _lak_saison(r["datum"])
        saison_von[r["id"]] = s
        eintrag = stat.setdefault(s, {"tuerchen": 0, "teilnehmer": 0})
        eintrag["tuerchen"] += 1
    tids = list(saison_von)
    if tids:
        ph = ",".join("?" * len(tids))
        for z in conn.execute(
                f"SELECT a.termin_id, COUNT(t2.id) n FROM veranstaltung_anmeldung a "
                f"JOIN veranstaltung_teilnehmer t2 ON t2.anmeldung_id=a.id "
                f"WHERE a.termin_id IN ({ph}) AND a.storniert=0 AND a.warteliste=0 "
                f"GROUP BY a.termin_id", tids).fetchall():
            stat[saison_von[z["termin_id"]]]["teilnehmer"] += int(z["n"] or 0)
    return stat


@bp.route("/")
def index():
    from .portal import _lak_saison
    from .veranstaltungen import belegte_plaetze_je_termin
    einst = get_einstellungen()
    if _modul_fehlt(einst):
        flash("Das Adventskalender-Modul ist für diesen Verein nicht eingeschaltet "
              "(Einstellungen → Module).", "fehler")
        return redirect(url_for("kalender.index"))
    vid = aktiver_verein_id()
    conn = get_db()
    kats = _kategorien(conn, vid)
    rows = _alle_termine(conn, vid, [k["id"] for k in kats])
    stat = _saison_statistik(conn, rows)
    saison = max((_lak_saison(r["datum"]) for r in rows), default=None)
    tuerchen = [dict(r) for r in rows if saison and _lak_saison(r["datum"]) == saison]
    aktive = [t["id"] for t in tuerchen
              if einst.get("modul_veranstaltungen") and t["anmeldung_aktiv"]
              and (t["max_teilnehmer"] or 0) > 0]
    belegt = belegte_plaetze_je_termin(conn, aktive)
    for t in tuerchen:
        t["belegt"] = belegt.get(t["id"]) if t["id"] in set(aktive) else None
    archiv = conn.execute(
        "SELECT * FROM adventskalender_archiv WHERE verein_id=? ORDER BY saison DESC",
        (vid,)).fetchall()
    conn.close()
    # Vorschlagsliste fürs Archiv: vergangene Saisons zuerst, ohne schon
    # archivierte Jahrgänge (die stehen oben in der Liste)
    archiviert = {a["saison"] for a in archiv}
    vorschlaege = [{"saison": s, **werte} for s, werte in sorted(stat.items(), reverse=True)
                   if s not in archiviert]
    return render_template("adventskalender_verwaltung.html", aktiv="adventskalender",
                           einst=einst, kats=kats, saison=saison, tuerchen=tuerchen,
                           archiv=archiv, vorschlaege=vorschlaege, stat=stat)


@bp.route("/einleitung", methods=["POST"])
def einleitung_speichern():
    einst = get_einstellungen()
    if _modul_fehlt(einst):
        abort(404)
    conn = get_db()
    conn.execute("UPDATE vereine SET lak_einleitung=? WHERE id=?",
                 (request.form.get("lak_einleitung", "").strip(), aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Einleitungstext gespeichert – die öffentliche Seite zeigt ihn ab sofort "
          "(Zwischenspeicher: bis zu 1 Minute). 🎄", "erfolg")
    return redirect(url_for("lak.index"))


def _bild_ablegen(vid, saison, daten):
    """Archivbild verkleinern und ablegen; Vorgänger derselben Saison löschen.
    Dateiname mit Zeitstempel = Cache-Busting (Muster Veranstaltungsbild)."""
    from PIL import Image
    im = Image.open(io.BytesIO(daten)).convert("RGB")
    im.thumbnail((1600, 1600))
    os.makedirs(ARCHIV_DIR, exist_ok=True)
    for alt in os.listdir(ARCHIV_DIR):
        if alt.startswith(f"lak_v{vid}_{saison}_"):
            try:
                os.remove(os.path.join(ARCHIV_DIR, alt))
            except OSError:
                pass
    fname = f"lak_v{vid}_{saison}_{int(time.time())}.jpg"
    im.save(os.path.join(ARCHIV_DIR, fname), "JPEG", quality=85)
    return fname


def _bild_aus_formular(vid, saison):
    """Bild aus dem Formular (Datei-Upload vor Bild-URL). None = keins."""
    datei = request.files.get("bild_datei")
    if datei and datei.filename:
        try:
            daten = datei.read()
            if daten:
                return _bild_ablegen(vid, saison, daten)
        except Exception:  # noqa: BLE001 – kaputter Upload bricht das Speichern nicht
            flash("Das hochgeladene Bild konnte nicht verarbeitet werden.", "fehler")
        return None
    url = request.form.get("bild_url", "").strip()
    if url:
        try:
            from .. import netz
            with netz.urlopen(url, timeout=10) as antwort:
                daten = antwort.read(15 * 1024 * 1024)
            return _bild_ablegen(vid, saison, daten)
        except Exception:  # noqa: BLE001
            flash("Das Bild unter der angegebenen Adresse konnte nicht geladen "
                  "werden – bitte Link prüfen oder Datei hochladen.", "fehler")
    return None


@bp.route("/archiv", methods=["POST"])
def archiv_speichern():
    einst = get_einstellungen()
    if _modul_fehlt(einst):
        abort(404)
    vid = aktiver_verein_id()
    try:
        saison = int(request.form.get("saison", ""))
    except ValueError:
        saison = 0
    if not 2000 <= saison <= 2100:
        flash("Bitte ein Jahr zwischen 2000 und 2100 angeben.", "fehler")
        return redirect(url_for("lak.index"))
    conn = get_db()
    # Leere Zahlenfelder füllt die Automatik aus den App-Anmeldungen
    kats = _kategorien(conn, vid)
    stat = _saison_statistik(conn, _alle_termine(conn, vid, [k["id"] for k in kats]))
    auto = stat.get(saison, {})

    def _zahl(feld, auto_wert):
        roh = request.form.get(feld, "").strip()
        if roh.isdigit():
            return int(roh)
        return auto_wert if auto_wert else None

    teilnehmer = _zahl("teilnehmerzahl", auto.get("teilnehmer"))
    tuerchen = _zahl("tuerchen", auto.get("tuerchen"))
    text = request.form.get("text", "").strip()
    bestehend = conn.execute(
        "SELECT * FROM adventskalender_archiv WHERE verein_id=? AND saison=?",
        (vid, saison)).fetchone()
    bild = _bild_aus_formular(vid, saison)
    if bestehend:
        neu_bild = ("" if request.form.get("bild_entfernen") else
                    (bild or bestehend["bild"] or ""))
        if (request.form.get("bild_entfernen") or bild) and bestehend["bild"]:
            try:
                os.remove(os.path.join(ARCHIV_DIR, bestehend["bild"]))
            except OSError:
                pass
        conn.execute("UPDATE adventskalender_archiv SET text=?, bild=?, "
                     "teilnehmerzahl=?, tuerchen=? WHERE id=?",
                     (text, neu_bild, teilnehmer, tuerchen, bestehend["id"]))
        flash(f"Jahrgang {saison} aktualisiert. 📚", "erfolg")
    else:
        conn.execute("INSERT INTO adventskalender_archiv (verein_id, saison, text, "
                     "bild, teilnehmerzahl, tuerchen, erstellt_am) VALUES (?,?,?,?,?,?,?)",
                     (vid, saison, text, bild or "", teilnehmer, tuerchen, now_iso()))
        flash(f"Jahrgang {saison} ins Archiv aufgenommen. 📚", "erfolg")
    conn.commit()
    conn.close()
    return redirect(url_for("lak.index"))


@bp.route("/archiv/<int:saison>/loeschen", methods=["POST"])
def archiv_loeschen(saison):
    einst = get_einstellungen()
    if _modul_fehlt(einst):
        abort(404)
    vid = aktiver_verein_id()
    conn = get_db()
    zeile = conn.execute("SELECT * FROM adventskalender_archiv WHERE verein_id=? AND saison=?",
                         (vid, saison)).fetchone()
    if zeile:
        if zeile["bild"]:
            try:
                os.remove(os.path.join(ARCHIV_DIR, zeile["bild"]))
            except OSError:
                pass
        conn.execute("DELETE FROM adventskalender_archiv WHERE id=?", (zeile["id"],))
        conn.commit()
        flash(f"Jahrgang {saison} aus dem Archiv entfernt.", "erfolg")
    conn.close()
    return redirect(url_for("lak.index"))


@bp.route("/archiv-bild/<int:saison>")
def archiv_bild(saison):
    """Archivbild für die Verwaltungs-Vorschau (nur hinter dem Login)."""
    einst = get_einstellungen()
    if _modul_fehlt(einst):
        abort(404)
    conn = get_db()
    zeile = conn.execute(
        "SELECT bild FROM adventskalender_archiv WHERE verein_id=? AND saison=?",
        (aktiver_verein_id(), saison)).fetchone()
    conn.close()
    if not (zeile and zeile["bild"]):
        abort(404)
    return send_from_directory(ARCHIV_DIR, zeile["bild"], max_age=3600)


@bp.route("/archiv/<int:saison>.pdf")
def archiv_pdf(saison):
    """Ein Jahrgang als PDF: Briefkopf, Bild, Rückblickstext, Kennzahlen."""
    from .. import lak_pdf
    einst = get_einstellungen()
    if _modul_fehlt(einst):
        abort(404)
    conn = get_db()
    zeile = conn.execute(
        "SELECT * FROM adventskalender_archiv WHERE verein_id=? AND saison=?",
        (aktiver_verein_id(), saison)).fetchone()
    conn.close()
    if not zeile:
        abort(404)
    bild_bytes = None
    if zeile["bild"]:
        try:
            with open(os.path.join(ARCHIV_DIR, zeile["bild"]), "rb") as f:
                bild_bytes = f.read()
        except OSError:
            bild_bytes = None
    daten = lak_pdf.erzeuge(einst, zeile, bild_bytes)
    return send_file(io.BytesIO(daten), mimetype="application/pdf", as_attachment=True,
                     download_name=f"Adventskalender-{saison}.pdf")
