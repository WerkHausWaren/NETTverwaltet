"""Beitragsverwaltung: Beiträge erzeugen, als bezahlt markieren, SEPA-Datei erstellen."""
from datetime import datetime
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, Response)
from ..db import get_db, get_einstellungen, now_iso, aktiver_verein_id
from ..funktionen import wer
from .. import sepa

bp = Blueprint("beitraege", __name__, url_prefix="/beitraege")


@bp.route("/")
def liste():
    conn = get_db()
    vid = aktiver_verein_id()
    jahr = request.args.get("jahr", type=int) or datetime.now().year
    nur = request.args.get("status", "")

    sql = ("SELECT b.*, m.vorname, m.nachname, m.zahlungsart FROM beitraege b "
           "JOIN mitglieder m ON m.id=b.mitglied_id WHERE b.verein_id=? AND b.jahr=?")
    params = [vid, jahr]
    if nur == "offen":
        sql += " AND b.bezahlt=0"
    elif nur == "bezahlt":
        sql += " AND b.bezahlt=1"
    sql += " ORDER BY m.nachname, m.vorname"
    posten = conn.execute(sql, params).fetchall()

    jahre = [r["jahr"] for r in conn.execute(
        "SELECT DISTINCT jahr FROM beitraege WHERE verein_id=? ORDER BY jahr DESC", (vid,)).fetchall()]
    if jahr not in jahre:
        jahre = [jahr] + jahre

    offen = conn.execute("SELECT COALESCE(SUM(betrag),0) s FROM beitraege WHERE verein_id=? AND jahr=? AND bezahlt=0",
                         (vid, jahr)).fetchone()["s"]
    bezahlt = conn.execute("SELECT COALESCE(SUM(betrag),0) s FROM beitraege WHERE verein_id=? AND jahr=? AND bezahlt=1",
                           (vid, jahr)).fetchone()["s"]
    conn.close()
    return render_template("beitraege/liste.html", aktiv="beitraege",
                           posten=posten, jahr=jahr, jahre=jahre, nur=nur,
                           offen=offen, bezahlt=bezahlt,
                           aktuelles_jahr=datetime.now().year)


@bp.route("/erzeugen", methods=["POST"])
def erzeugen():
    jahr = request.form.get("jahr", type=int) or datetime.now().year
    conn = get_db()
    vid = aktiver_verein_id()
    mitglieder = conn.execute(
        "SELECT * FROM mitglieder WHERE verein_id=? AND status='aktiv' AND beitrag > 0", (vid,)).fetchall()
    angelegt, uebersprungen = 0, 0
    for m in mitglieder:
        schon_da = conn.execute(
            "SELECT 1 FROM beitraege WHERE mitglied_id=? AND jahr=? AND bezeichnung=?",
            (m["id"], jahr, f"Mitgliedsbeitrag {jahr}")).fetchone()
        if schon_da:
            uebersprungen += 1
            continue
        conn.execute(
            "INSERT INTO beitraege (verein_id, mitglied_id, jahr, bezeichnung, betrag, faellig_am, bezahlt, zahlungsart, erstellt_am) "
            "VALUES (?,?,?,?,?,?,0,?,?)",
            (vid, m["id"], jahr, f"Mitgliedsbeitrag {jahr}", m["beitrag"],
             f"{jahr}-01-31", m["zahlungsart"], now_iso()))
        angelegt += 1
    conn.commit()
    conn.close()
    flash(f"{angelegt} Beiträge für {jahr} angelegt ({uebersprungen} bereits vorhanden).", "erfolg")
    return redirect(url_for("beitraege.liste", jahr=jahr))


@bp.route("/<int:bid>/umschalten", methods=["POST"])
def umschalten(bid):
    conn = get_db()
    vid = aktiver_verein_id()
    b = conn.execute("SELECT * FROM beitraege WHERE id=? AND verein_id=?", (bid, vid)).fetchone()
    if b:
        neu = 0 if b["bezahlt"] else 1
        conn.execute("UPDATE beitraege SET bezahlt=?, bezahlt_am=? WHERE id=?",
                     (neu, now_iso() if neu else "", bid))
        if neu:
            conn.execute(
                "INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, mitglied_id, erstellt_am, erstellt_von) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (vid, now_iso()[:10], "einnahme", "Mitgliedsbeitrag", b["bezeichnung"],
                 b["betrag"], b["mitglied_id"], now_iso(), wer()))
        conn.commit()
    jahr = b["jahr"] if b else datetime.now().year
    conn.close()
    return redirect(url_for("beitraege.liste", jahr=jahr))


@bp.route("/sepa.xml")
def sepa_export():
    jahr = request.args.get("jahr", type=int) or datetime.now().year
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    fehlt = sepa.pruefe_voraussetzungen(einst)
    if fehlt:
        flash("Für die SEPA-Datei fehlen noch: " + ", ".join(fehlt) +
              ". Bitte unter Einstellungen ergänzen.", "fehler")
        return redirect(url_for("beitraege.liste", jahr=jahr))

    conn = get_db()
    zeilen = conn.execute(
        "SELECT b.*, m.vorname, m.nachname, m.iban, m.bic, m.kontoinhaber, "
        "m.mandatsreferenz, m.mandatsdatum FROM beitraege b JOIN mitglieder m ON m.id=b.mitglied_id "
        "WHERE b.verein_id=? AND b.jahr=? AND b.bezahlt=0 AND m.zahlungsart='SEPA-Lastschrift' "
        "AND m.iban != '' AND m.mandat_vorhanden=1",
        (vid, jahr)).fetchall()
    # Mitglieder mit offener Lastschrift, aber OHNE vorliegendes Mandat – werden ausgelassen
    ohne_mandat = conn.execute(
        "SELECT COUNT(DISTINCT m.id) c FROM beitraege b JOIN mitglieder m ON m.id=b.mitglied_id "
        "WHERE b.verein_id=? AND b.jahr=? AND b.bezahlt=0 AND m.zahlungsart='SEPA-Lastschrift' "
        "AND m.iban != '' AND (m.mandat_vorhanden IS NULL OR m.mandat_vorhanden=0)",
        (vid, jahr)).fetchone()["c"]
    conn.close()
    if ohne_mandat:
        flash(f"⚠️ {ohne_mandat} Mitglied(er) wurden ausgelassen, weil kein Lastschriftmandat vorliegt. "
              "Bitte Mandat beim Mitglied dokumentieren.", "fehler")
    if not zeilen:
        flash("Keine einziehbaren SEPA-Lastschriften (offen + Mandat vorliegend) für dieses Jahr gefunden.", "info")
        return redirect(url_for("beitraege.liste", jahr=jahr))

    posten = [{
        "name": z["kontoinhaber"] or f"{z['vorname']} {z['nachname']}",
        "iban": z["iban"], "bic": z["bic"], "betrag": z["betrag"],
        "mandatsreferenz": z["mandatsreferenz"], "mandatsdatum": z["mandatsdatum"],
        # Eigene Kennung je Posten – die Bank verlangt sie eindeutig, und bei
        # einem Rückläufer erkennt man daran, um wen es geht.
        "referenz": f"BEITR-{jahr}-{z['mitglied_id']}",
        "verwendungszweck": f"Mitgliedsbeitrag {jahr} - {einst['vereinsname']}".strip(" -"),
    } for z in zeilen]

    xml, probleme = sepa.erzeuge_xml(einst, posten)
    if probleme:
        namen = ", ".join(f"{p['name']} ({p['grund']})" for p in probleme[:6])
        weitere = f" und {len(probleme) - 6} weitere" if len(probleme) > 6 else ""
        flash(f"⚠️ {len(probleme)} Mitglied(er) sind NICHT in der Datei: {namen}{weitere}. "
              "Bitte die Angaben beim Mitglied ergänzen und die Datei danach neu erzeugen.", "fehler")
    if not xml or "<DrctDbtTxInf>" not in xml:
        flash("Es blieb kein einziger einziehbarer Posten übrig – es wurde keine Datei erzeugt.", "fehler")
        return redirect(url_for("beitraege.liste", jahr=jahr))
    return Response(xml, mimetype="application/xml",
                    headers={"Content-Disposition": f"attachment; filename=SEPA-Beitraege-{jahr}.xml"})
