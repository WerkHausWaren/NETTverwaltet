"""Rechnungsmodul: freie Rechnungen im Stil von WISO MeinVerein.

- Fortlaufender Nummernkreis je Verein und Jahr (RE-2026-001)
- Positionen (Menge × Einzelpreis), USt wählbar (Standard: 0 % mit
  Gemeinnützigkeits-/Kleinunternehmer-Hinweis)
- Druckansicht + öffentlicher Link (Token) für Empfänger:innen, Mail-Versand
- Zahlcode je Rechnung -> automatischer Abgleich beim Bankabruf
- Beim Setzen auf „bezahlt" optional direkt eine Kassenbuch-Buchung
"""
import secrets

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)

from .. import mail
from ..db import (get_db, get_einstellungen, aktiver_verein_id, now_iso,
                  einstellungen_fuer, oeffentliche_suche)
from ..zahlungsabgleich import ALPHABET

bp = Blueprint("rechnungen", __name__, url_prefix="/rechnungen")


@bp.before_request
def _gate():
    if request.endpoint == "rechnungen.oeffentlich":
        return
    e = get_einstellungen()
    if not (e and "modul_rechnungen" in e.keys() and e["modul_rechnungen"]):
        flash("Bitte zuerst das Modul Rechnungen in den Einstellungen aktivieren.", "info")
        return redirect(url_for("dashboard.index"))


def _neuer_zahlcode(conn, vid):
    while True:
        code = "RG-" + "".join(secrets.choice(ALPHABET) for _ in range(5))
        if not conn.execute("SELECT 1 FROM rechnungen WHERE verein_id=? AND zahlcode=?",
                            (vid, code)).fetchone():
            return code


def _neue_nummer(conn, vid, jahr):
    n = conn.execute("SELECT COUNT(*) c FROM rechnungen WHERE verein_id=? AND nummer LIKE ?",
                     (vid, f"RE-{jahr}-%")).fetchone()["c"]
    return f"RE-{jahr}-{n + 1:03d}"


def _summen(positionen, ust_satz):
    netto = round(sum((p["menge"] or 0) * (p["einzelpreis"] or 0) for p in positionen), 2)
    ust = round(netto * (ust_satz or 0) / 100, 2)
    return netto, ust, round(netto + ust, 2)


def _positionen(conn, rid):
    return conn.execute("SELECT * FROM rechnung_positionen WHERE rechnung_id=? ORDER BY id",
                        (rid,)).fetchall()


def _float(wert):
    try:
        return float((wert or "0").replace(",", "."))
    except ValueError:
        return 0.0


@bp.route("/")
def index():
    conn = get_db()
    vid = aktiver_verein_id()
    status = request.args.get("status", "")
    sql = ("SELECT r.*, "
           "(SELECT COALESCE(SUM(menge*einzelpreis),0) FROM rechnung_positionen p "
           " WHERE p.rechnung_id=r.id) AS netto "
           "FROM rechnungen r WHERE r.verein_id=?")
    params = [vid]
    if status in ("offen", "bezahlt", "storniert"):
        sql += " AND r.status=?"
        params.append(status)
    zeilen = conn.execute(sql + " ORDER BY r.datum DESC, r.id DESC", params).fetchall()
    heute = now_iso()[:10]
    rechnungen = []
    offen_summe = ueberfaellig_summe = 0.0
    ueberfaellig_n = 0
    for r in zeilen:
        _n, _u, brutto = _summen([{"menge": 1, "einzelpreis": r["netto"]}], r["ust_satz"])
        ueberfaellig = (r["status"] == "offen" and (r["faellig_am"] or "") and r["faellig_am"] < heute)
        rechnungen.append({"r": r, "brutto": brutto, "ueberfaellig": ueberfaellig})
        if r["status"] == "offen":
            offen_summe += brutto
            if ueberfaellig:
                ueberfaellig_summe += brutto
                ueberfaellig_n += 1
    conn.close()
    einst = get_einstellungen()
    return render_template("rechnungen/index.html", aktiv="rechnungen",
                           rechnungen=rechnungen, status=status, offen_summe=offen_summe,
                           ueberfaellig_summe=ueberfaellig_summe, ueberfaellig_n=ueberfaellig_n,
                           mahn_auto=bool(einst["mahn_auto"]), einst=einst)


@bp.route("/mahnautomatik", methods=["POST"])
def mahnautomatik():
    """Mahnautomatik ein-/ausschalten und Fristen je Stufe einstellen."""
    conn = get_db()
    vid = aktiver_verein_id()
    an = 1 if request.form.get("mahn_auto") else 0
    def tage(feld, standard):
        w = request.form.get(feld, type=int)
        return w if w and 1 <= w <= 365 else standard
    t1 = tage("mahn_tage1", 7)
    t2 = max(tage("mahn_tage2", 21), t1 + 1)
    t3 = max(tage("mahn_tage3", 35), t2 + 1)
    conn.execute("UPDATE vereine SET mahn_auto=?, mahn_tage1=?, mahn_tage2=?, mahn_tage3=? WHERE id=?",
                 (an, t1, t2, t3, vid))
    conn.commit()
    conn.close()
    flash("Mahnautomatik eingeschaltet – der Server mahnt ab jetzt täglich automatisch. ⏰" if an
          else "Mahnautomatik ausgeschaltet. Mahnungen verschickst du weiter von Hand.", "erfolg")
    return redirect(url_for("rechnungen.index"))


@bp.route("/neu", methods=["GET", "POST"])
@bp.route("/<int:rid>/bearbeiten", methods=["GET", "POST"], endpoint="bearbeiten")
def neu(rid=None):
    conn = get_db()
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    r = None
    if rid is not None:
        r = conn.execute("SELECT * FROM rechnungen WHERE id=? AND verein_id=?", (rid, vid)).fetchone()
        if not r:
            conn.close()
            abort(404)
        if r["status"] == "bezahlt":
            conn.close()
            flash("Bezahlte Rechnungen können nicht mehr geändert werden – bei Bedarf stornieren.", "fehler")
            return redirect(url_for("rechnungen.index"))

    if request.method == "POST":
        f = request.form
        daten = {
            "datum": f.get("datum") or now_iso()[:10],
            "faellig_am": (f.get("faellig_am") or "").strip(),
            "empfaenger_name": (f.get("empfaenger_name") or "").strip(),
            "empfaenger_strasse": (f.get("empfaenger_strasse") or "").strip(),
            "empfaenger_plz": (f.get("empfaenger_plz") or "").strip(),
            "empfaenger_ort": (f.get("empfaenger_ort") or "").strip(),
            "empfaenger_email": (f.get("empfaenger_email") or "").strip(),
            "mitglied_id": f.get("mitglied_id", type=int) or None,
            "betreff": (f.get("betreff") or "").strip(),
            "einleitung": (f.get("einleitung") or "").strip(),
            "schluss": (f.get("schluss") or "").strip(),
            "ust_satz": _float(f.get("ust_satz")),
            "leistung_datum": (f.get("leistung_datum") or "").strip(),
            "kontakt_id": f.get("kontakt_id", type=int) or None,
        }
        # Empfänger ohne Mitglied als Kontakt merken (füllt sich beim nächsten Mal von selbst)
        if not daten["mitglied_id"] and f.get("als_kontakt", "1"):
            from .kontakte import finden_oder_anlegen
            kid_neu = finden_oder_anlegen(conn, vid, {
                "name": daten["empfaenger_name"], "strasse": daten["empfaenger_strasse"],
                "plz": daten["empfaenger_plz"], "ort": daten["empfaenger_ort"],
                "email": daten["empfaenger_email"]})
            if kid_neu:
                daten["kontakt_id"] = kid_neu
        if not daten["empfaenger_name"] or not daten["betreff"]:
            conn.close()
            flash("Bitte mindestens Empfänger:in und Betreff angeben.", "fehler")
            return redirect(request.url)
        if r is None:
            jahr = daten["datum"][:4]
            felder = list(daten) + ["verein_id", "nummer", "zahlcode", "token", "status", "erstellt_am"]
            werte = list(daten.values()) + [vid, _neue_nummer(conn, vid, jahr),
                                            _neuer_zahlcode(conn, vid), secrets.token_urlsafe(12),
                                            "offen", now_iso()]
            cur = conn.execute(
                f"INSERT INTO rechnungen ({','.join(felder)}) VALUES ({','.join(['?'] * len(felder))})",
                werte)
            rid = cur.lastrowid
        else:
            setz = ", ".join(f"{k}=?" for k in daten)
            conn.execute(f"UPDATE rechnungen SET {setz} WHERE id=? AND verein_id=?",
                         list(daten.values()) + [rid, vid])
            conn.execute("DELETE FROM rechnung_positionen WHERE rechnung_id=?", (rid,))
        for i, besch in enumerate(f.getlist("pos_text")):
            besch = besch.strip()
            menge = _float(f.getlist("pos_menge")[i] if i < len(f.getlist("pos_menge")) else "1")
            preis = _float(f.getlist("pos_preis")[i] if i < len(f.getlist("pos_preis")) else "0")
            if besch and menge > 0:
                conn.execute("INSERT INTO rechnung_positionen (rechnung_id, beschreibung, menge, "
                             "einzelpreis) VALUES (?,?,?,?)", (rid, besch, menge, preis))
        conn.commit()
        conn.close()
        flash("Rechnung gespeichert. 🧾", "erfolg")
        return redirect(url_for("rechnungen.druck", rid=rid))

    mitglieder = conn.execute("SELECT id, vorname, nachname, strasse, plz, ort, email "
                              "FROM mitglieder WHERE verein_id=? ORDER BY nachname, vorname",
                              (vid,)).fetchall()
    kontakte_liste = conn.execute("SELECT * FROM kontakte WHERE verein_id=? ORDER BY name",
                                  (vid,)).fetchall()
    vorkontakt = None
    if not r and request.args.get("kontakt", type=int):
        vorkontakt = conn.execute("SELECT * FROM kontakte WHERE id=? AND verein_id=?",
                                  (request.args.get("kontakt", type=int), vid)).fetchone()
    positionen = _positionen(conn, rid) if rid else []
    conn.close()
    return render_template("rechnungen/bearbeiten.html", aktiv="rechnungen", r=r,
                           positionen=positionen, mitglieder=mitglieder, einst=einst,
                           kontakte=kontakte_liste, vorkontakt=vorkontakt,
                           heute=now_iso()[:10])


def _rechnung_laden(conn, **wo):
    bed = " AND ".join(f"{k}=?" for k in wo)
    r = conn.execute(f"SELECT * FROM rechnungen WHERE {bed}", list(wo.values())).fetchone()
    return r


def _paypal(einst, r, brutto):
    """PayPal.me-Link mit Betrag - nur fuer offene Rechnungen."""
    if (r["status"] or "offen") != "offen":
        return ""
    from .veranstaltungen import bezahl_link
    return bezahl_link(einst, brutto)


def _girocode_uri(einst, r, brutto):
    """GiroCode-Data-URI für offene Rechnungen ('' wenn nicht möglich)."""
    if (r["status"] or "offen") != "offen":
        return ""
    from .. import girocode
    daten = girocode.payload(einst["bank_kontoinhaber"] or einst["vereinsname"],
                             einst["bank_iban"], brutto,
                             f"{r['zahlcode']} Rechnung {r['nummer']}", einst["bank_bic"])
    return girocode.data_uri(daten) if daten else ""


@bp.route("/<int:rid>/druck")
def druck(rid):
    conn = get_db()
    vid = aktiver_verein_id()
    r = _rechnung_laden(conn, id=rid, verein_id=vid)
    if not r:
        conn.close()
        abort(404)
    positionen = _positionen(conn, rid)
    conn.close()
    netto, ust, brutto = _summen(positionen, r["ust_satz"])
    einst = get_einstellungen()
    return render_template("rechnungen/druck.html", r=r, positionen=positionen,
                           einst=einst, netto=netto, ust=ust, brutto=brutto,
                           girocode=_girocode_uri(einst, r, brutto),
                           paypal_link=_paypal(einst, r, brutto), intern=True)


@bp.route("/ansehen/<token>")
def oeffentlich(token):
    # Vereinsübergreifend: der geteilte Link soll unabhängig vom gerade aktiven
    # Verein funktionieren; Vereinsdaten kommen vom Verein der Rechnung.
    conn, r = oeffentliche_suche("SELECT * FROM rechnungen WHERE token=? AND token!=''", token)
    einst = einstellungen_fuer(r["verein_id"]) if r else None
    if not r or not (einst and einst.get("modul_rechnungen")):
        conn.close()
        abort(404)
    positionen = _positionen(conn, r["id"])
    conn.close()
    netto, ust, brutto = _summen(positionen, r["ust_satz"])
    return render_template("rechnungen/druck.html", r=r, positionen=positionen,
                           einst=einst, netto=netto, ust=ust, brutto=brutto,
                           girocode=_girocode_uri(einst, r, brutto),
                           paypal_link=_paypal(einst, r, brutto), intern=False)


@bp.route("/<int:rid>/senden", methods=["POST"])
def senden(rid):
    conn = get_db()
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    r = _rechnung_laden(conn, id=rid, verein_id=vid)
    if not r:
        conn.close()
        abort(404)
    positionen = _positionen(conn, rid)
    conn.close()
    if not (r["empfaenger_email"] or "").strip():
        flash("Für diese Rechnung ist keine E-Mail-Adresse hinterlegt.", "fehler")
        return redirect(url_for("rechnungen.index"))
    _n, _u, brutto = _summen(positionen, r["ust_satz"])
    link = request.host_url.rstrip("/") + url_for("rechnungen.oeffentlich", token=r["token"])
    text = (f"Hallo {r['empfaenger_name']},\n\n"
            f"anbei erhalten Sie unsere Rechnung {r['nummer']} über "
            + f"{brutto:.2f}".replace(".", ",") + f" € ({r['betreff']}).\n\n"
            f"Rechnung ansehen und drucken:\n{link}\n\n"
            f"Bitte überweisen Sie den Betrag"
            + (f" bis zum {r['faellig_am'][8:10]}.{r['faellig_am'][5:7]}.{r['faellig_am'][0:4]}"
               if r["faellig_am"] else "")
            + f" auf unser Konto:\n{einst['bank_kontoinhaber'] or einst['vereinsname']}\n"
              f"IBAN: {einst['bank_iban'] or '—'}\n"
              f"Verwendungszweck: {r['zahlcode']}\n\n"
              f"Vielen Dank!\n{einst['vereinsname']}")
    anhaenge = None
    try:
        from .. import rechnung_pdf
        netto, ust, brutto2 = _summen(positionen, r["ust_satz"])
        pdf_daten = rechnung_pdf.erzeuge(einst, r, positionen, netto, ust, brutto2)
        anhaenge = [{"name": f"Rechnung-{r['nummer']}.pdf", "daten": pdf_daten,
                     "mime": "application/pdf"}]
    except Exception:  # noqa: BLE001 – PDF darf den Versand nie verhindern
        import logging
        logging.getLogger("rechnungen").exception("Rechnungs-PDF fehlgeschlagen")
    ok, fehler = mail.einzelmail(einst, r["empfaenger_email"],
                                 f"Rechnung {r['nummer']} – {einst['vereinsname']}", text,
                                 anhaenge=anhaenge)
    if ok:
        flash(f"Rechnung per E-Mail an {r['empfaenger_email']} gesendet. ✉️", "erfolg")
    else:
        flash(f"Versand fehlgeschlagen: {fehler}", "fehler")
    return redirect(url_for("rechnungen.index"))


@bp.route("/<int:rid>/status", methods=["POST"])
def status_setzen(rid):
    conn = get_db()
    vid = aktiver_verein_id()
    r = _rechnung_laden(conn, id=rid, verein_id=vid)
    if not r:
        conn.close()
        abort(404)
    neu_status = request.form.get("status")
    if neu_status not in ("offen", "bezahlt", "storniert"):
        conn.close()
        abort(400)
    heute = now_iso()[:10]
    if neu_status == "bezahlt":
        conn.execute("UPDATE rechnungen SET status='bezahlt', bezahlt_am=? WHERE id=?", (heute, rid))
        if request.form.get("ins_kassenbuch"):
            positionen = _positionen(conn, rid)
            _n, _u, brutto = _summen(positionen, r["ust_satz"])
            from .finanzen import _naechste_belegnr
            k = conn.execute("SELECT * FROM konten WHERE verein_id=? AND typ='einnahme' "
                             "ORDER BY (nummer!='5100'), nummer LIMIT 1", (vid,)).fetchone()
            cur = conn.execute(
                "INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, "
                "mitglied_id, konto_id, geldkonto_id, sphaere, belegnr, erstellt_am, erstellt_von) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (vid, heute, "einnahme", "Rechnung",
                 f"Rechnung {r['nummer']} – {r['empfaenger_name']}", brutto, r["mitglied_id"],
                 k["id"] if k else None, request.form.get("geldkonto_id", type=int) or None,
                 (k["sphaere"] if k else ""), _naechste_belegnr(conn, vid, heute[:4]), now_iso(),
                 "Rechnung"))
            conn.execute("UPDATE rechnungen SET buchung_id=? WHERE id=?", (cur.lastrowid, rid))
    else:
        conn.execute("UPDATE rechnungen SET status=?, bezahlt_am='' WHERE id=?", (neu_status, rid))
    conn.commit()
    conn.close()
    flash({"bezahlt": "Rechnung als bezahlt markiert. ✅",
           "storniert": "Rechnung storniert.",
           "offen": "Rechnung wieder auf offen gesetzt."}[neu_status], "erfolg")
    return redirect(url_for("rechnungen.index"))


@bp.route("/<int:rid>/mahnen", methods=["POST"])
def mahnen(rid):
    """Sendet eine freundliche Zahlungserinnerung per E-Mail (mit PDF-Anhang)."""
    conn = get_db()
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    r = _rechnung_laden(conn, id=rid, verein_id=vid)
    if not r or r["status"] != "offen":
        conn.close()
        abort(404)
    positionen = _positionen(conn, rid)
    if not (r["empfaenger_email"] or "").strip():
        conn.close()
        flash("Keine E-Mail-Adresse hinterlegt – bitte die Erinnerung ausdrucken/anrufen.", "fehler")
        return redirect(url_for("rechnungen.index"))
    _n, _u, brutto = _summen(positionen, r["ust_satz"])
    link = request.host_url.rstrip("/") + url_for("rechnungen.oeffentlich", token=r["token"])
    text = (f"Hallo {r['empfaenger_name']},\n\n"
            f"sicher ist es nur untergegangen: Unsere Rechnung {r['nummer']} über "
            + f"{brutto:.2f}".replace(".", ",") + f" € ({r['betreff']})"
            + (f", zahlbar bis {r['faellig_am'][8:10]}.{r['faellig_am'][5:7]}.{r['faellig_am'][0:4]},"
               if r["faellig_am"] else "")
            + " ist noch offen.\n\n"
            f"Rechnung ansehen:\n{link}\n\n"
            f"Konto: {einst['bank_kontoinhaber'] or einst['vereinsname']}\n"
            f"IBAN: {einst['bank_iban'] or '—'}\nVerwendungszweck: {r['zahlcode']}\n\n"
            "Schon überwiesen? Dann hat sich diese Nachricht überschnitten – danke!\n\n"
            f"{einst['vereinsname']}")
    anhaenge = None
    try:
        from .. import rechnung_pdf
        pdf_daten = rechnung_pdf.erzeuge(einst, r, positionen, _n, _u, brutto)
        anhaenge = [{"name": f"Rechnung-{r['nummer']}.pdf", "daten": pdf_daten,
                     "mime": "application/pdf"}]
    except Exception:  # noqa: BLE001
        pass
    ok, fehler = mail.einzelmail(einst, r["empfaenger_email"],
                                 f"Zahlungserinnerung – Rechnung {r['nummer']}", text,
                                 anhaenge=anhaenge)
    if ok:
        conn.execute("UPDATE rechnungen SET gemahnt_am=?, mahnstufe=MAX(COALESCE(mahnstufe,0)+1,1) "
                     "WHERE id=?", (now_iso()[:10], rid))
        conn.commit()
        flash("Zahlungserinnerung gesendet. ⏰", "erfolg")
    else:
        flash(f"Versand fehlgeschlagen: {fehler}", "fehler")
    conn.close()
    return redirect(url_for("rechnungen.index"))


@bp.route("/forderungen.csv")
def forderungen_export():
    """Übergabeliste aller überfälligen Forderungen (z. B. für Inkasso/Rechtsanwalt)."""
    import csv
    import io
    from flask import Response
    conn = get_db()
    vid = aktiver_verein_id()
    heute = now_iso()[:10]
    zeilen = conn.execute(
        "SELECT r.*, (SELECT COALESCE(SUM(menge*einzelpreis),0) FROM rechnung_positionen p "
        "WHERE p.rechnung_id=r.id) AS netto FROM rechnungen r "
        "WHERE r.verein_id=? AND r.status='offen' AND r.faellig_am!='' AND r.faellig_am<? "
        "ORDER BY r.faellig_am", (vid, heute)).fetchall()
    conn.close()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Rechnungsnummer", "Rechnungsdatum", "Fällig am", "Gemahnt am", "Schuldner",
                "Straße", "PLZ", "Ort", "E-Mail", "Betreff", "Betrag EUR", "Zahlcode"])
    for r in zeilen:
        _n, _u, brutto = _summen([{"menge": 1, "einzelpreis": r["netto"]}], r["ust_satz"])
        w.writerow([r["nummer"], r["datum"], r["faellig_am"], r["gemahnt_am"] or "",
                    r["empfaenger_name"], r["empfaenger_strasse"], r["empfaenger_plz"],
                    r["empfaenger_ort"], r["empfaenger_email"], r["betreff"],
                    f"{brutto:.2f}".replace(".", ","), r["zahlcode"]])
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             f"attachment; filename=Forderungen-{heute}.csv"})


@bp.route("/<int:rid>/loeschen", methods=["POST"])
def loeschen(rid):
    conn = get_db()
    vid = aktiver_verein_id()
    r = _rechnung_laden(conn, id=rid, verein_id=vid)
    if r and r["status"] == "offen":
        conn.execute("DELETE FROM rechnungen WHERE id=? AND verein_id=?", (rid, vid))
        conn.commit()
        flash("Rechnung gelöscht.", "erfolg")
    else:
        flash("Nur offene Rechnungen können gelöscht werden – sonst bitte stornieren.", "fehler")
    conn.close()
    return redirect(url_for("rechnungen.index"))
