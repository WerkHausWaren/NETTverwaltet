"""
Vereinsunterlagen: wichtige Dokumente zentral ablegen und schnell wiederfinden
(Freistellungsbescheid, Registerauszug, Satzung, Beitragsordnung, Geschäftsordnung ...).

Dateien werden lokal oder – auf Wunsch – im OneDrive-Ordner gespeichert,
sodass OneDrive sie automatisch in die Cloud sichert.
"""
import os
import time
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, send_from_directory, abort)
from ..db import get_db, get_einstellungen, now_iso, aktiver_verein_id
from ..funktionen import wer
from .. import speicher, webdav

bp = Blueprint("unterlagen", __name__, url_prefix="/unterlagen")

# Feste Kategorien in sinnvoller Reihenfolge (leicht auffindbar)
KATEGORIEN = [
    "Freistellungsbescheid", "Registerauszug", "Satzung", "Beitragsordnung",
    "Geschäftsordnung", "Protokolle", "Finanzamt / Steuer", "Verträge",
    "Bescheinigungen", "Sonstiges",
]


def _ordner():
    einst = get_einstellungen()
    return speicher.dokumente_ordner(einst["id"], einst["vereinsname"], einst["dok_speicherort"])


@bp.route("/")
def index():
    conn = get_db()
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    dokumente = conn.execute(
        "SELECT * FROM vereinsdokumente WHERE verein_id=? ORDER BY kategorie, hochgeladen_am DESC",
        (vid,)).fetchall()
    conn.close()
    # Nach Kategorie gruppieren
    nach_kat = {k: [] for k in KATEGORIEN}
    for d in dokumente:
        nach_kat.setdefault(d["kategorie"], []).append(d)
    if speicher.nextcloud_aktiv(einst):
        speicher_pfad = f"Nextcloud → {speicher.UNTERORDNER}/… /Dokumente"
    else:
        speicher_pfad = _ordner()
    return render_template("unterlagen/index.html", aktiv="unterlagen",
                           kategorien=KATEGORIEN, nach_kat=nach_kat, einst=einst,
                           onedrive_da=speicher.onedrive_verfuegbar(),
                           nextcloud_da=webdav.konfiguriert(einst),
                           speicher_pfad=speicher_pfad)


@bp.route("/hochladen", methods=["POST"])
def hochladen():
    vid = aktiver_verein_id()
    datei = request.files.get("datei")
    kategorie = request.form.get("kategorie", "Sonstiges")
    titel = request.form.get("titel", "").strip()
    notiz = request.form.get("notiz", "").strip()
    if not datei or not datei.filename:
        flash("Bitte eine Datei auswählen.", "fehler")
        return redirect(url_for("unterlagen.index"))

    # Eindeutigen, sicheren Dateinamen bilden
    basis = os.path.basename(datei.filename).replace("/", "_")
    zeitstempel = time.strftime("%Y%m%d-%H%M%S")
    dateiname = f"{zeitstempel}_{basis}"
    speicher.speichern(get_einstellungen(), "dokumente", dateiname, datei.read())

    conn = get_db()
    conn.execute(
        "INSERT INTO vereinsdokumente (verein_id, kategorie, titel, dateiname, original_name, notiz, hochgeladen_am, hochgeladen_von) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (vid, kategorie, titel or basis, dateiname, basis, notiz, now_iso(), wer()))
    conn.commit()
    conn.close()
    flash(f"Dokument „{titel or basis}“ gespeichert.", "erfolg")
    return redirect(url_for("unterlagen.index", _anchor=kategorie))


@bp.route("/<int:did>/oeffnen")
def oeffnen(did):
    conn = get_db()
    d = conn.execute("SELECT * FROM vereinsdokumente WHERE id=? AND verein_id=?",
                     (did, aktiver_verein_id())).fetchone()
    conn.close()
    if not d:
        abort(404)
    return speicher.sende(get_einstellungen(), "dokumente", d["dateiname"],
                          download_name=d["original_name"])


@bp.route("/<int:did>/ansehen")
def ansehen(did):
    """Viewer-Seite mit Leiste (Schließen / Teilen / Drucken) – bettet die Datei ein."""
    conn = get_db()
    d = conn.execute("SELECT * FROM vereinsdokumente WHERE id=? AND verein_id=?",
                     (did, aktiver_verein_id())).fetchone()
    conn.close()
    if not d:
        abort(404)
    name = (d["original_name"] or d["dateiname"] or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    ist_bild = ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "heic")
    return render_template("unterlagen/ansicht.html", d=d, ist_pdf=(ext == "pdf"), ist_bild=ist_bild)


@bp.route("/<int:did>/loeschen", methods=["POST"])
def loeschen(did):
    conn = get_db()
    d = conn.execute("SELECT * FROM vereinsdokumente WHERE id=? AND verein_id=?",
                     (did, aktiver_verein_id())).fetchone()
    if d:
        speicher.loeschen(get_einstellungen(), "dokumente", d["dateiname"])
        conn.execute("DELETE FROM vereinsdokumente WHERE id=?", (did,))
        conn.commit()
        flash("Dokument gelöscht.", "erfolg")
    conn.close()
    return redirect(url_for("unterlagen.index"))


@bp.route("/<int:did>/mitglieder", methods=["POST"])
def mitglieder_sichtbar(did):
    """Schaltet um, ob ein Dokument in der Mitglieder-App sichtbar ist."""
    conn = get_db()
    d = conn.execute("SELECT fuer_mitglieder FROM vereinsdokumente WHERE id=? AND verein_id=?",
                     (did, aktiver_verein_id())).fetchone()
    if d:
        conn.execute("UPDATE vereinsdokumente SET fuer_mitglieder=? WHERE id=?",
                     (0 if d["fuer_mitglieder"] else 1, did))
        conn.commit()
    conn.close()
    return redirect(url_for("unterlagen.index"))


@bp.route("/speicherort", methods=["POST"])
def speicherort():
    ort = request.form.get("dok_speicherort", "lokal")
    if ort not in ("lokal", "onedrive", "nextcloud"):
        ort = "lokal"
    einst = get_einstellungen()
    if ort == "nextcloud" and not webdav.konfiguriert(einst):
        flash("Für „Nextcloud“ fehlt der Zugang (Einstellungen → Nextcloud). Es wird vorerst lokal gespeichert.", "info")
        ort = "lokal"
    elif ort == "nextcloud" and not webdav.test_zugriff(einst):
        flash("Nextcloud ist nicht erreichbar oder der Zugang stimmt nicht. Es wird vorerst lokal gespeichert.", "fehler")
        ort = "lokal"
    conn = get_db()
    conn.execute("UPDATE vereine SET dok_speicherort=? WHERE id=?", (ort, aktiver_verein_id()))
    conn.commit()
    conn.close()
    if ort == "onedrive" and not speicher.onedrive_verfuegbar():
        flash("OneDrive-Ordner wurde nicht gefunden – es wird vorerst lokal gespeichert.", "info")
    elif ort == "nextcloud":
        flash("Speicherort aktualisiert. Neue Dokumente landen in deiner Nextcloud.", "erfolg")
    else:
        flash("Speicherort aktualisiert." + (" Neue Dokumente landen in OneDrive." if ort == "onedrive" else ""), "erfolg")
    return redirect(url_for("unterlagen.index"))
