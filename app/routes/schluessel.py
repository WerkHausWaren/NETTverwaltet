"""Schlüsselbuch: Ausgabe und Rückgabe physischer Schlüssel und Zugangscodes."""
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash
from ..db import get_db, get_einstellungen, now_iso, aktiver_verein_id
from ..funktionen import wer

bp = Blueprint("schluessel", __name__, url_prefix="/schluessel")

TYPEN = ["Schlüssel", "Zugangscode"]


@bp.before_request
def _modul_gate():
    e = get_einstellungen()
    if e and "modul_schluessel" in e.keys() and not e["modul_schluessel"]:
        flash("Das Modul „Schlüsselbuch“ ist ausgeblendet (Einstellungen → Module).", "info")
        return redirect(url_for("dashboard.index"))


@bp.route("/")
def index():
    conn = get_db()
    vid = aktiver_verein_id()
    eintraege = conn.execute(
        "SELECT s.*, m.vorname, m.nachname FROM schluesselbuch s "
        "LEFT JOIN mitglieder m ON m.id=s.inhaber_mitglied_id "
        "WHERE s.verein_id=? ORDER BY (s.zurueckgegeben_am != '') , s.ausgegeben_am DESC",
        (vid,)).fetchall()
    mitglieder = conn.execute(
        "SELECT id, vorname, nachname FROM mitglieder WHERE verein_id=? ORDER BY nachname, vorname",
        (vid,)).fetchall()
    conn.close()
    offen = [e for e in eintraege if not e["zurueckgegeben_am"]]
    zurueck = [e for e in eintraege if e["zurueckgegeben_am"]]
    vorauswahl = request.args.get("mitglied", type=int)
    # Nuki-Türprotokoll (letzte Aktionen) – nur relevant, wenn Nuki eingerichtet ist
    from .. import nuki_api
    einst = get_einstellungen()
    nuki_bereit = nuki_api.konfiguriert(einst)
    tuer_log = []
    if nuki_bereit:
        conn2 = get_db()
        tuer_log = conn2.execute(
            "SELECT * FROM tuer_protokoll WHERE verein_id=? ORDER BY id DESC LIMIT 30", (vid,)).fetchall()
        conn2.close()
    return render_template("schluessel/index.html", aktiv="schluessel",
                           offen=offen, zurueck=zurueck, mitglieder=mitglieder,
                           typen=TYPEN, heute=date.today().isoformat(), vorauswahl=vorauswahl,
                           nuki_bereit=nuki_bereit, tuer_log=tuer_log)


@bp.route("/neu", methods=["POST"])
def neu():
    conn = get_db()
    vid = aktiver_verein_id()
    mitglied_id = request.form.get("inhaber_mitglied_id", type=int)
    conn.execute(
        "INSERT INTO schluesselbuch (verein_id, bezeichnung, typ, code_wert, inhaber_mitglied_id, "
        "inhaber_name, ausgegeben_am, befristet_bis, notiz, erstellt_am, erstellt_von) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (vid, request.form.get("bezeichnung", "").strip(), request.form.get("typ", "Schlüssel"),
         request.form.get("code_wert", "").strip(), mitglied_id or None,
         request.form.get("inhaber_name", "").strip(),
         request.form.get("ausgegeben_am") or now_iso()[:10],
         request.form.get("befristet_bis", ""), request.form.get("notiz", "").strip(), now_iso(), wer()))
    conn.commit()
    conn.close()
    flash("Ausgabe im Schlüsselbuch eingetragen.", "erfolg")
    return redirect(url_for("schluessel.index"))


@bp.route("/<int:sid>/zurueck", methods=["POST"])
def zurueck(sid):
    conn = get_db()
    conn.execute("UPDATE schluesselbuch SET zurueckgegeben_am=? WHERE id=? AND verein_id=?",
                 (request.form.get("datum") or now_iso()[:10], sid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Rückgabe erfasst.", "erfolg")
    return redirect(url_for("schluessel.index"))


@bp.route("/<int:sid>/loeschen", methods=["POST"])
def loeschen(sid):
    conn = get_db()
    conn.execute("DELETE FROM schluesselbuch WHERE id=? AND verein_id=?", (sid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Eintrag gelöscht.", "erfolg")
    return redirect(url_for("schluessel.index"))
