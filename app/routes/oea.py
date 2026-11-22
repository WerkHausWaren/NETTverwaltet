"""Öffentlichkeitsarbeit: Cockpit für Presse & Social Media.

Redaktionsplan (Beiträge mit Freigabe-Workflow durch den Vorstand),
Beitrags-Werkstatt (fertige Textvorschläge je Kanal aus Veranstaltungen),
Pressefach (Mitteilungen als PDF + Versand an den Presseverteiler),
Fotoarchiv (ZIP-Download) und Presseverteiler. Nutzbar von Admins und
der eigenen Rolle "oeffentlichkeit" (auch für Externe ohne Mitgliedschaft).
"""
import io
import os
import zipfile
from datetime import date

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort, send_file, current_app)
from flask_login import current_user

from .. import db, push, mail, oea_auto, oea_formate, presse_pdf, instagram_api
from ..db import get_db, aktiver_verein_id, now_iso

bp = Blueprint("oea", __name__, url_prefix="/oea")

STATUS = [("entwurf", "Entwurf"), ("freigabe", "Wartet auf Freigabe"),
          ("freigegeben", "Freigegeben"), ("veroeffentlicht", "Veröffentlicht")]
STATUS_NAMEN = dict(STATUS)


def _ist_admin():
    return not current_app.config.get("PRODUKTION") or getattr(current_user, "ist_admin", False)


def _wer():
    return getattr(current_user, "anzeigename", "") or getattr(current_user, "email", "") or "Verwaltung"


def _basis_url():
    host = request.host
    schema = "http" if host.startswith(("localhost", "127.")) else "https"
    return f"{schema}://{host}"


def _anmeldelink(t):
    if t["anmeldung_token"]:
        return _basis_url() + url_for("veranstaltungen.oeffentlich", token=t["anmeldung_token"])
    return ""


def _beitrag(conn, bid, vid):
    b = conn.execute("SELECT * FROM oea_beitraege WHERE id=? AND verein_id=?", (bid, vid)).fetchone()
    if not b:
        conn.close()
        abort(404)
    return b


def _admins_benachrichtigen(vid, titel, text):
    """Push + Mail an alle Admin-Zugänge (Freigabe-Anfragen)."""
    try:
        sconn = db.get_system_db()
        admins = sconn.execute("SELECT id, email FROM benutzer WHERE verein_id=? AND aktiv=1 "
                               "AND rolle='admin'", (vid,)).fetchall()
        sconn.close()
        if admins:
            push.senden([a["id"] for a in admins], titel, text, url="/oea/plan", thema="oea")
        einst = db.get_einstellungen()
        link = _basis_url() + url_for("oea.plan", status="freigabe")
        for a in admins:
            if a["email"] and "@" in a["email"]:
                try:
                    mail.einzelmail(einst, a["email"], titel,
                                    f"{text}\n\nZur Freigabe: {link}")
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass


def _oea_benachrichtigen(vid, titel, text):
    """Push an alle ÖA-Zugänge (z. B. Freigabe erteilt, neuer Presse-Auftrag)."""
    try:
        sconn = db.get_system_db()
        leute = sconn.execute("SELECT id FROM benutzer WHERE verein_id=? AND aktiv=1 "
                              "AND rolle='oeffentlichkeit'", (vid,)).fetchall()
        sconn.close()
        if leute:
            push.senden([p["id"] for p in leute], titel, text, url="/oea/plan", thema="oea")
    except Exception:  # noqa: BLE001
        pass


@bp.route("/")
def index():
    conn = get_db()
    vid = aktiver_verein_id()
    oea_auto.abgelaufene_erinnerungen_entfernen(conn, vid)
    heute = date.today().isoformat()
    zahlen = {
        "geplant": conn.execute("SELECT COUNT(*) n FROM oea_beitraege WHERE verein_id=? "
                                "AND status IN ('entwurf','freigabe','freigegeben')", (vid,)).fetchone()["n"],
        "freigabe": conn.execute("SELECT COUNT(*) n FROM oea_beitraege WHERE verein_id=? "
                                 "AND status='freigabe'", (vid,)).fetchone()["n"],
        "presse": conn.execute("SELECT COUNT(*) n FROM oea_beitraege WHERE verein_id=? "
                               "AND typ='presse'", (vid,)).fetchone()["n"],
        "alben": conn.execute("SELECT COUNT(DISTINCT termin_id) n FROM galerie_fotos "
                              "WHERE verein_id=?", (vid,)).fetchone()["n"],
    }
    naechste = conn.execute(
        "SELECT b.*, t.titel AS termin_titel FROM oea_beitraege b "
        "LEFT JOIN termine t ON t.id=b.termin_id "
        "WHERE b.verein_id=? AND b.status!='veroeffentlicht' "
        "ORDER BY CASE WHEN b.geplant_am='' THEN 1 ELSE 0 END, b.geplant_am, b.id DESC LIMIT 6",
        (vid,)).fetchall()
    presse_auftraege = conn.execute(
        "SELECT t.id, t.titel, t.datum FROM termine t WHERE t.verein_id=? AND t.presse_wunsch=1 "
        "AND t.datum>=? AND NOT EXISTS (SELECT 1 FROM oea_beitraege b WHERE b.termin_id=t.id "
        "AND b.typ='presse' AND b.status IN ('freigegeben','veroeffentlicht')) "
        "ORDER BY t.datum LIMIT 5", (vid, heute)).fetchall()
    conn.close()
    return render_template("oea/index.html", aktiv="oea", zahlen=zahlen, naechste=naechste,
                           presse_auftraege=presse_auftraege, status_namen=STATUS_NAMEN,
                           formate=oea_formate.FORMATE, ist_admin=_ist_admin())


@bp.route("/plan")
def plan():
    conn = get_db()
    vid = aktiver_verein_id()
    oea_auto.abgelaufene_erinnerungen_entfernen(conn, vid)
    status = request.args.get("status") or ""
    typ = request.args.get("typ") or ""
    sql = ("SELECT b.*, t.titel AS termin_titel, t.datum AS termin_datum FROM oea_beitraege b "
           "LEFT JOIN termine t ON t.id=b.termin_id WHERE b.verein_id=?")
    params = [vid]
    if status:
        sql += " AND b.status=?"
        params.append(status)
    if typ:
        sql += " AND b.typ=?"
        params.append(typ)
    beitraege = conn.execute(sql + " ORDER BY CASE WHEN b.geplant_am='' THEN 1 ELSE 0 END, "
                             "b.geplant_am, b.id DESC", params).fetchall()
    # Medienecho: Fundstellen je Pressemitteilung (Zahl + Links fürs Ausklappmenü)
    fundstellen = {}
    for f in conn.execute("SELECT * FROM oea_fundstellen WHERE verein_id=? ORDER BY id DESC", (vid,)):
        fundstellen.setdefault(f["beitrag_id"], []).append(f)
    conn.close()
    return render_template("oea/plan.html", aktiv="oea", beitraege=beitraege, status=status,
                           typ=typ, status_liste=STATUS, status_namen=STATUS_NAMEN,
                           formate=oea_formate.FORMATE, ist_admin=_ist_admin(),
                           fundstellen=fundstellen)


@bp.route("/neu", methods=["GET", "POST"])
@bp.route("/<int:bid>", methods=["GET", "POST"], endpoint="bearbeiten")
def neu(bid=None):
    conn = get_db()
    vid = aktiver_verein_id()
    b = _beitrag(conn, bid, vid) if bid is not None else None
    if request.method == "POST":
        daten = (request.form.get("typ") if request.form.get("typ") in ("social", "presse") else "social",
                 request.form.get("kanal", "").strip(),
                 request.form.get("titel", "").strip(),
                 request.form.get("text", "").strip(),
                 request.form.get("termin_id", type=int) or None,
                 request.form.get("geplant_am", "").strip(),
                 request.form.get("notiz", "").strip(),
                 request.form.get("bild_foto_id", type=int) or None,
                 request.form.get("bild_unterschrift", "").strip(),
                 request.form.get("bild_copyright", "").strip(),
                 request.form.get("geplant_um", "").strip(),
                 1 if request.form.get("auto_posten") else 0)
        if not (daten[2] or daten[3]):
            conn.close()
            flash("Bitte mindestens Titel oder Text angeben.", "fehler")
            return redirect(request.url)
        if b is None:
            cur = conn.execute(
                "INSERT INTO oea_beitraege (verein_id, typ, kanal, titel, text, termin_id, "
                "geplant_am, notiz, bild_foto_id, bild_unterschrift, bild_copyright, "
                "geplant_um, auto_posten, status, "
                "erstellt_von, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (vid, *daten, "entwurf", _wer(), now_iso()))
            bid = cur.lastrowid
        else:
            if b["status"] == "veroeffentlicht" and not _ist_admin():
                conn.close()
                flash("Veröffentlichte Beiträge kann nur der Vorstand ändern.", "fehler")
                return redirect(url_for("oea.plan"))
            # Beim Neusetzen der Automatik den alten Fehlerstand zuruecksetzen
            conn.execute("UPDATE oea_beitraege SET typ=?, kanal=?, titel=?, text=?, termin_id=?, "
                         "geplant_am=?, notiz=?, bild_foto_id=?, bild_unterschrift=?, "
                         "bild_copyright=?, geplant_um=?, auto_posten=?, "
                         "auto_fehler=CASE WHEN ?=1 THEN '' ELSE auto_fehler END, "
                         "auto_versuche=CASE WHEN ?=1 THEN 0 ELSE auto_versuche END "
                         "WHERE id=? AND verein_id=?",
                         (*daten, daten[-1], daten[-1], bid, vid))
        conn.commit()
        conn.close()
        flash("Beitrag gespeichert. 📣", "erfolg")
        return redirect(url_for("oea.bearbeiten", bid=bid))
    termine = conn.execute("SELECT id, titel, datum FROM termine WHERE verein_id=? "
                           "ORDER BY datum DESC LIMIT 120", (vid,)).fetchall()
    # Bildauswahl fuer Instagram: freigegebene Fotos aus dem Album der Veranstaltung
    fotos, termin_bild = [], False
    if b and b["termin_id"]:
        fotos = conn.execute("SELECT id, beschreibung FROM galerie_fotos WHERE termin_id=? "
                             "AND verein_id=? AND freigabe_ok=1 ORDER BY id", (b["termin_id"], vid)).fetchall()
        t = conn.execute("SELECT bild FROM termine WHERE id=?", (b["termin_id"],)).fetchone()
        termin_bild = bool(t and t["bild"])
    einst = db.get_einstellungen()
    conn.close()
    vor_termin = request.args.get("termin_id", type=int)
    vor_typ = request.args.get("typ") if request.args.get("typ") in ("social", "presse") else None
    return render_template("oea/beitrag.html", aktiv="oea", b=b, termine=termine,
                           formate=oea_formate.FORMATE, status_namen=STATUS_NAMEN,
                           vor_termin=vor_termin, vor_typ=vor_typ, ist_admin=_ist_admin(),
                           fotos=fotos, termin_bild=termin_bild,
                           presse_geruest=oea_formate.presse_geruest(einst),
                           ig_bereit=bool(einst["ig_token"] and einst["ig_user_id"]))


@bp.route("/<int:bid>/status", methods=["POST"])
def status_setzen(bid):
    conn = get_db()
    vid = aktiver_verein_id()
    b = _beitrag(conn, bid, vid)
    aktion = request.form.get("aktion") or ""
    einst = db.get_einstellungen()
    art = "Pressemitteilung" if b["typ"] == "presse" else "Beitrag"
    if aktion == "freigabe_anfordern" and b["status"] in ("entwurf",):
        conn.execute("UPDATE oea_beitraege SET status='freigabe' WHERE id=?", (bid,))
        _admins_benachrichtigen(vid, f"📣 Freigabe angefragt: {art}",
                                f"„{b['titel'] or (b['text'] or '')[:60]}“ wartet auf die Freigabe des Vorstands.")
        flash("Freigabe beim Vorstand angefragt – er bekommt eine Nachricht. ⏳", "erfolg")
    elif aktion == "freigeben":
        if not _ist_admin():
            conn.close()
            flash("Freigeben kann nur der Vorstand.", "fehler")
            return redirect(url_for("oea.plan"))
        conn.execute("UPDATE oea_beitraege SET status='freigegeben', freigegeben_von=?, "
                     "freigegeben_am=? WHERE id=?", (_wer(), now_iso(), bid))
        _oea_benachrichtigen(vid, f"✅ Freigegeben: {art}",
                             f"„{b['titel'] or (b['text'] or '')[:60]}“ wurde vom Vorstand freigegeben.")
        flash("Freigegeben – die Öffentlichkeitsarbeit wurde benachrichtigt. ✅", "erfolg")
    elif aktion == "veroeffentlicht" and b["status"] in ("freigegeben", "freigabe", "entwurf"):
        if b["status"] != "freigegeben" and not _ist_admin():
            conn.close()
            flash("Bitte erst die Freigabe des Vorstands einholen.", "fehler")
            return redirect(url_for("oea.plan"))
        conn.execute("UPDATE oea_beitraege SET status='veroeffentlicht', veroeffentlicht_am=? "
                     "WHERE id=?", (now_iso(), bid))
        flash("Als veröffentlicht markiert. 🎉", "erfolg")
    elif aktion == "zurueck":
        conn.execute("UPDATE oea_beitraege SET status='entwurf', freigegeben_von='', "
                     "freigegeben_am='', veroeffentlicht_am='' WHERE id=?", (bid,))
        flash("Zurück auf Entwurf gesetzt.", "erfolg")
    conn.commit()
    conn.close()
    from ..html_sicher import lokales_ziel
    return redirect(lokales_ziel(request.form.get("weiter"), url_for("oea.plan")))


@bp.route("/<int:bid>/loeschen", methods=["POST"])
def loeschen(bid):
    conn = get_db()
    vid = aktiver_verein_id()
    _beitrag(conn, bid, vid)
    conn.execute("DELETE FROM oea_beitraege WHERE id=? AND verein_id=?", (bid, vid))
    conn.commit()
    conn.close()
    flash("Beitrag gelöscht.", "erfolg")
    return redirect(url_for("oea.plan"))


@bp.route("/werkstatt")
def werkstatt():
    conn = get_db()
    vid = aktiver_verein_id()
    tid = request.args.get("termin_id", type=int)
    termine = conn.execute("SELECT id, titel, datum FROM termine WHERE verein_id=? AND aktiv=1 "
                           "AND datum>=? ORDER BY datum LIMIT 60",
                           (vid, date.today().isoformat())).fetchall()
    entwuerfe, t = [], None
    if tid:
        t = conn.execute("SELECT * FROM termine WHERE id=? AND verein_id=?", (tid, vid)).fetchone()
        if t:
            einst = db.get_einstellungen()
            link = _anmeldelink(t)
            for kanal, f in oea_formate.FORMATE.items():
                text = oea_formate.baue(kanal, t, einst, link)
                entwuerfe.append({"kanal": kanal, "format": f, "text": text, "laenge": len(text)})
    conn.close()
    return render_template("oea/werkstatt.html", aktiv="oea", termine=termine, t=t,
                           entwuerfe=entwuerfe, formate=oea_formate.FORMATE)


@bp.route("/werkstatt/uebernehmen", methods=["POST"])
def werkstatt_uebernehmen():
    conn = get_db()
    vid = aktiver_verein_id()
    tid = request.form.get("termin_id", type=int)
    kanal = request.form.get("kanal", "")
    t = conn.execute("SELECT * FROM termine WHERE id=? AND verein_id=?", (tid, vid)).fetchone()
    if not t or kanal not in oea_formate.FORMATE:
        conn.close()
        abort(404)
    typ = "presse" if kanal == "presse" else "social"
    cur = conn.execute(
        "INSERT INTO oea_beitraege (verein_id, typ, kanal, titel, text, termin_id, geplant_am, "
        "status, erstellt_von, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (vid, typ, "" if typ == "presse" else kanal, t["titel"],
         request.form.get("text") or oea_formate.baue(kanal, t, db.get_einstellungen(), ""),
         tid, t["datum"] or "", "entwurf", _wer(), now_iso()))
    conn.commit()
    conn.close()
    flash(f"In den Redaktionsplan übernommen ({oea_formate.FORMATE[kanal]['label']}). 📋", "erfolg")
    return redirect(url_for("oea.bearbeiten", bid=cur.lastrowid))


@bp.route("/presse/<int:bid>/pdf")
def presse_pdf_export(bid):
    conn = get_db()
    vid = aktiver_verein_id()
    b = _beitrag(conn, bid, vid)
    if b["typ"] != "presse":
        conn.close()
        abort(404)
    bild = _bild_datei(conn, b)
    conn.close()
    einst = db.get_einstellungen()
    daten = presse_pdf.erzeuge(einst, b, bild=bild)
    name = f"Pressemitteilung_{(b['titel'] or 'Verein')[:40].replace(' ', '_')}.pdf"
    return send_file(io.BytesIO(daten), mimetype="application/pdf",
                     as_attachment=True, download_name=name)


@bp.route("/presse/<int:bid>/senden", methods=["POST"])
def presse_senden(bid):
    conn = get_db()
    vid = aktiver_verein_id()
    b = _beitrag(conn, bid, vid)
    if b["typ"] != "presse":
        conn.close()
        abort(404)
    if b["status"] not in ("freigegeben", "veroeffentlicht"):
        conn.close()
        flash("Erst nach der Freigabe durch den Vorstand versendbar.", "fehler")
        return redirect(url_for("oea.bearbeiten", bid=bid))
    verteiler = conn.execute("SELECT * FROM kontakte WHERE verein_id=? AND kategorie='presse' "
                             "AND email!='' ORDER BY name", (vid,)).fetchall()
    if not verteiler:
        conn.close()
        flash("Der Presseverteiler ist leer – bitte zuerst Kontakte mit E-Mail anlegen.", "fehler")
        return redirect(url_for("oea.verteiler"))
    einst = db.get_einstellungen()
    # Das Beitragsbild (Galerie-Foto bzw. Veranstaltungsbild) kommt ins PDF UND
    # in Druckqualität als Anhang – Redaktionen brauchen die Bilddatei extra.
    bild = _bild_datei(conn, b)
    pdf = presse_pdf.erzeuge(einst, b, bild=bild)
    anhaenge = [{"name": f"Pressemitteilung_{(b['titel'] or 'Verein')[:40].replace(' ', '_')}.pdf",
                 "daten": pdf, "mime": "application/pdf"}]
    if bild:
        anhaenge.append(bild)
    betreff = f"Pressemitteilung: {b['titel']}"
    n = 0
    for k in verteiler:
        anrede = f"Guten Tag{', ' + k['ansprechpartner'] if (k['ansprechpartner'] or '').strip() else ''},"
        bild_satz = ("Das beigefügte Foto dürfen Sie im Rahmen der Berichterstattung "
                     "honorarfrei verwenden.\n\n" if bild else "")
        text = (f"{anrede}\n\nanbei erhalten Sie eine Pressemitteilung von "
                f"{einst['vereinsname']}. Abdruck honorarfrei – über ein Belegexemplar freuen wir uns.\n\n"
                f"{bild_satz}{b['text']}\n\nFreundliche Grüße\n{einst['vereinsname']}")
        try:
            ok, _fehler = mail.einzelmail(einst, k["email"], betreff, text, anhaenge=anhaenge)
            n += 1 if ok else 0
        except Exception:  # noqa: BLE001
            pass
    # Interne Kopie (Einstellungen -> "Kopie bei Pressemitteilungen an"): dieselben
    # Anhaenge, als Nachweis mit Empfaengerliste im Text. Zaehlt nicht in n.
    kopie_an = (einst.get("presse_kopie_email") or "").strip()
    kopie_ok = False
    if kopie_an:
        empfaenger_liste = "\n".join(f"- {k['name']} <{k['email']}>" for k in verteiler)
        kopie_text = (f"Interne Kopie des Presseversands ({n} von {len(verteiler)} zugestellt).\n\n"
                      f"Empfänger:\n{empfaenger_liste}\n\n"
                      f"--- Versendeter Text ---\n\n{b['text']}")
        try:
            kopie_ok, _fehler = mail.einzelmail(einst, kopie_an, f"Kopie: {betreff}",
                                                kopie_text, anhaenge=anhaenge)
        except Exception:  # noqa: BLE001
            pass
    conn.execute("UPDATE oea_beitraege SET status='veroeffentlicht', veroeffentlicht_am=? WHERE id=?",
                 (now_iso(), bid))
    conn.commit()
    conn.close()
    zusatz = ""
    if kopie_an:
        zusatz = f" Kopie an {kopie_an}." if kopie_ok else f" ⚠️ Kopie an {kopie_an} FEHLGESCHLAGEN."
    flash(f"Pressemitteilung an {n} von {len(verteiler)} Presse-Kontakte versendet. 📰{zusatz}", "erfolg")
    return redirect(url_for("oea.plan", typ="presse"))


@bp.route("/fotos")
def fotos():
    conn = get_db()
    vid = aktiver_verein_id()
    album = request.args.get("album", type=int)
    if album:
        t = conn.execute("SELECT id, titel, datum FROM termine WHERE id=? AND verein_id=?",
                         (album, vid)).fetchone()
        if not t:
            conn.close()
            abort(404)
        bilder = conn.execute("SELECT f.*, mg.vorname, mg.nachname FROM galerie_fotos f "
                              "LEFT JOIN mitglieder mg ON mg.id=f.mitglied_id "
                              "WHERE f.termin_id=? AND f.verein_id=? ORDER BY f.id", (album, vid)).fetchall()
        einst = db.get_einstellungen()
        hub_an = bool(einst and einst["modul_contenthub"])
        lage = None
        if hub_an:
            from .oea_hub import social_lage
            lage = social_lage(conn, vid, album)
        conn.close()
        return render_template("oea/fotos.html", aktiv="oea", t=t, bilder=bilder, alben=None,
                               ist_admin=_ist_admin(), hub_an=hub_an, lage=lage,
                               pruefen=request.args.get("pruefen", type=int))
    alben = conn.execute(
        "SELECT t.id, t.titel, t.datum, COUNT(f.id) AS anzahl, MAX(f.id) AS cover, "
        "SUM(CASE WHEN f.freigabe_ok THEN 0 ELSE 1 END) AS gesperrt "
        "FROM galerie_fotos f JOIN termine t ON t.id=f.termin_id "
        "WHERE f.verein_id=? GROUP BY t.id, t.titel, t.datum ORDER BY t.datum DESC", (vid,)).fetchall()
    conn.close()
    return render_template("oea/fotos.html", aktiv="oea", alben=alben, t=None, bilder=None,
                           ist_admin=_ist_admin())


def _bild_signatur(fid):
    """Kurze Signatur, damit die öffentliche Bild-Adresse nicht erratbar ist."""
    import hashlib
    import hmac
    schluessel = str(current_app.config.get("SECRET_KEY", "")).encode()
    return hmac.new(schluessel, f"oea-bild-{fid}".encode(), hashlib.sha256).hexdigest()[:16]


@bp.route("/bild/<int:fid>/<sig>")
def bild_oeffentlich(fid, sig):
    """Öffentliche Adresse eines freigegebenen Galerie-Fotos.

    Instagram lädt das Bild selbst herunter und kann sich dabei nicht anmelden –
    darum diese Route. Sie liefert NUR freigegebene Fotos und nur mit gültiger
    Signatur, damit niemand die Galerie durchprobieren kann.
    """
    import hmac as _hmac
    from flask import send_from_directory
    from .portal import _galerie_dir
    if not _hmac.compare_digest(sig, _bild_signatur(fid)):
        abort(404)
    conn = get_db()
    row = conn.execute("SELECT dateiname, freigabe_ok FROM galerie_fotos WHERE id=?", (fid,)).fetchone()
    conn.close()
    if not row or not row["dateiname"] or not row["freigabe_ok"]:
        abort(404)
    return send_from_directory(_galerie_dir(), row["dateiname"])


def _bild_datei(conn, b):
    """Bilddatei eines Beitrags als Mail-Anhang {name, daten, mime} – oder None.

    Reihenfolge wie bei _bild_url: gewähltes Galerie-Foto (nur freigegebene),
    sonst das Veranstaltungsbild des verknüpften Termins."""
    import mimetypes
    pfad, beschreibung = None, ""
    if b["bild_foto_id"]:
        row = conn.execute("SELECT dateiname, freigabe_ok, beschreibung FROM galerie_fotos WHERE id=?",
                           (b["bild_foto_id"],)).fetchone()
        if row and row["dateiname"] and row["freigabe_ok"]:
            from .portal import _galerie_dir
            pfad = os.path.join(_galerie_dir(), row["dateiname"])
            beschreibung = (row["beschreibung"] or "").strip()
    elif b["termin_id"]:
        t = conn.execute("SELECT bild, titel FROM termine WHERE id=?", (b["termin_id"],)).fetchone()
        if t and t["bild"]:
            from .termine import _event_bild_dir
            pfad = os.path.join(_event_bild_dir(), t["bild"])
            beschreibung = (t["titel"] or "").strip()
    if not pfad or not os.path.exists(pfad):
        return None
    endung = os.path.splitext(pfad)[1].lower() or ".jpg"
    mime = mimetypes.guess_type(pfad)[0] or "image/jpeg"
    titel = (b["titel"] or "Pressebild")[:40].replace(" ", "_")
    with open(pfad, "rb") as f:
        return {"name": f"Pressebild_{titel}{endung}", "daten": f.read(), "mime": mime,
                "beschreibung": beschreibung}


def _bild_url(conn, b):
    """Öffentliche https-Adresse des Bildes für einen Beitrag (oder '')."""
    basis = _basis_url()
    if b["bild_foto_id"]:
        return f"{basis}{url_for('oea.bild_oeffentlich', fid=b['bild_foto_id'], sig=_bild_signatur(b['bild_foto_id']))}"
    if b["termin_id"]:
        t = conn.execute("SELECT bild FROM termine WHERE id=?", (b["termin_id"],)).fetchone()
        if t and t["bild"]:
            return f"{basis}{url_for('termine.bild', tid=b['termin_id'])}"
    return ""


@bp.route("/instagram", methods=["GET", "POST"])
def instagram():
    """Instagram-Verbindung einrichten und prüfen (nur Vorstand)."""
    if not _ist_admin():
        flash("Die Instagram-Verbindung richtet der Vorstand ein.", "fehler")
        return redirect(url_for("oea.index"))
    from .. import krypto
    vid = aktiver_verein_id()
    if request.method == "POST":
        aktion = request.form.get("aktion") or "verbinden"
        conn = get_db()
        if aktion == "trennen":
            conn.execute("UPDATE vereine SET ig_token='', ig_user_id='', ig_konto='' WHERE id=?", (vid,))
            conn.commit()
            conn.close()
            flash("Instagram-Verbindung getrennt.", "erfolg")
            return redirect(url_for("oea.instagram"))
        app_id = (request.form.get("ig_app_id") or "").strip()
        app_secret = (request.form.get("ig_app_secret") or "").strip()
        kurz = (request.form.get("kurz_token") or "").strip()
        if not (app_id and app_secret and kurz):
            conn.close()
            flash("Bitte App-ID, App-Geheimnis und das Token aus dem Graph-API-Explorer angeben.", "fehler")
            return redirect(url_for("oea.instagram"))
        try:
            werte = instagram_api.verbinden(app_id, app_secret, kurz)
        except instagram_api.IGFehler as e:
            conn.close()
            flash(f"Verbindung fehlgeschlagen: {e}", "fehler")
            return redirect(url_for("oea.instagram"))
        conn.execute("UPDATE vereine SET ig_app_id=?, ig_app_secret=?, ig_token=?, ig_user_id=?, "
                     "ig_konto=? WHERE id=?",
                     (app_id, krypto.verschluesseln(app_secret), krypto.verschluesseln(werte["ig_token"]),
                      werte["ig_user_id"], werte["ig_konto"], vid))
        conn.commit()
        conn.close()
        flash(f"Instagram verbunden: @{werte['ig_konto']} 🎉", "erfolg")
        return redirect(url_for("oea.instagram"))
    einst = db.get_einstellungen()
    ok, meldung = (False, "")
    if request.args.get("test"):
        ok, meldung = instagram_api.zugang_testen(einst)
    return render_template("oea/instagram.html", aktiv="oea", einst=einst, test_ok=ok,
                           test_meldung=meldung, rechte=instagram_api.RECHTE)


@bp.route("/<int:bid>/instagram", methods=["POST"])
def instagram_posten(bid):
    """Einen freigegebenen Beitrag direkt auf Instagram veröffentlichen."""
    conn = get_db()
    vid = aktiver_verein_id()
    b = _beitrag(conn, bid, vid)
    einst_hub = db.get_einstellungen()
    if einst_hub and einst_hub["modul_contenthub"] and einst_hub["hub_instagram"]:
        conn.close()
        flash("Instagram läuft über den Content-Hub – bitte „An den Hub geben“ nutzen "
              "(sonst würde doppelt gepostet).", "fehler")
        return redirect(url_for("oea.bearbeiten", bid=bid))
    # Doppelposten zuerst prüfen – sonst meldet die App nach dem Veröffentlichen
    # irreführend "erst nach der Freigabe" (der Status ist dann schon weiter).
    if b["ig_media_id"]:
        conn.close()
        flash("Dieser Beitrag wurde bereits auf Instagram veröffentlicht.", "fehler")
        return redirect(url_for("oea.bearbeiten", bid=bid))
    if b["status"] != "freigegeben":
        conn.close()
        flash("Erst nach der Freigabe durch den Vorstand veröffentlichbar.", "fehler")
        return redirect(url_for("oea.bearbeiten", bid=bid))
    bild = _bild_url(conn, b)
    if not bild:
        conn.close()
        flash("Instagram braucht ein Bild – bitte im Beitrag ein Foto auswählen.", "fehler")
        return redirect(url_for("oea.bearbeiten", bid=bid))
    einst = db.get_einstellungen()
    try:
        media_id = instagram_api.veroeffentlichen(einst, bild, b["text"] or "")
    except instagram_api.IGFehler as e:
        conn.close()
        flash(f"Instagram: {e}", "fehler")
        return redirect(url_for("oea.bearbeiten", bid=bid))
    conn.execute("UPDATE oea_beitraege SET status='veroeffentlicht', veroeffentlicht_am=?, "
                 "ig_media_id=? WHERE id=?", (now_iso(), media_id, bid))
    conn.commit()
    conn.close()
    flash("Auf Instagram veröffentlicht. 🎉 Kommentare erscheinen im Postfach.", "erfolg")
    return redirect(url_for("oea.kommentare"))


@bp.route("/kommentare")
def kommentare():
    """Postfach der Instagram-Kommentare."""
    conn = get_db()
    vid = aktiver_verein_id()
    nur_offen = request.args.get("alle") != "1"
    sql = ("SELECT k.*, b.titel AS beitrag_titel FROM oea_kommentare k "
           "LEFT JOIN oea_beitraege b ON b.id=k.beitrag_id WHERE k.verein_id=?")
    params = [vid]
    if nur_offen:
        sql += " AND k.erledigt=0"
    liste = conn.execute(sql + " ORDER BY k.erstellt_am DESC, k.id DESC LIMIT 200", params).fetchall()
    offen = conn.execute("SELECT COUNT(*) n FROM oea_kommentare WHERE verein_id=? AND erledigt=0",
                         (vid,)).fetchone()["n"]
    conn.close()
    einst = db.get_einstellungen()
    return render_template("oea/kommentare.html", aktiv="oea", kommentare=liste, offen=offen,
                           nur_offen=nur_offen, ig_konto=(einst["ig_konto"] or ""))


@bp.route("/kommentare/holen", methods=["POST"])
def kommentare_holen():
    from ..oea_auto import kommentare_holen as _holen
    neu, fehler = _holen(aktiver_verein_id())
    if fehler:
        flash(f"Instagram: {fehler}", "fehler")
    else:
        flash(f"{neu} neue Kommentar(e) geholt." if neu else "Keine neuen Kommentare.", "erfolg")
    return redirect(url_for("oea.kommentare"))


@bp.route("/kommentar/<int:kid>/antwort", methods=["POST"])
def kommentar_antwort(kid):
    conn = get_db()
    vid = aktiver_verein_id()
    k = conn.execute("SELECT * FROM oea_kommentare WHERE id=? AND verein_id=?", (kid, vid)).fetchone()
    if not k:
        conn.close()
        abort(404)
    if request.form.get("aktion") == "erledigt":
        conn.execute("UPDATE oea_kommentare SET erledigt=1 WHERE id=?", (kid,))
        conn.commit()
        conn.close()
        flash("Als erledigt abgehakt.", "erfolg")
        return redirect(url_for("oea.kommentare"))
    text = (request.form.get("antwort") or "").strip()
    if not text:
        conn.close()
        flash("Bitte eine Antwort schreiben.", "fehler")
        return redirect(url_for("oea.kommentare"))
    einst = db.get_einstellungen()
    try:
        instagram_api.antworten(einst, k["ig_comment_id"], text)
    except instagram_api.IGFehler as e:
        conn.close()
        flash(f"Instagram: {e}", "fehler")
        return redirect(url_for("oea.kommentare"))
    conn.execute("UPDATE oea_kommentare SET antwort_text=?, beantwortet_am=?, erledigt=1 WHERE id=?",
                 (text, now_iso(), kid))
    conn.commit()
    conn.close()
    flash("Antwort auf Instagram veröffentlicht. 💬", "erfolg")
    return redirect(url_for("oea.kommentare"))


@bp.route("/foto/<int:fid>/freigabe", methods=["POST"])
def foto_freigabe(fid):
    """Veröffentlichungs-Freigabe eines Fotos setzen/zurückziehen (nur Vorstand).

    Wichtig für die DSGVO: Wer sein Einverständnis widerruft (Art. 7 Abs. 3),
    wird hier gesperrt – das Foto verschwindet dann aus allen ZIP-Downloads.
    """
    conn = get_db()
    vid = aktiver_verein_id()
    if not _ist_admin():
        conn.close()
        flash("Die Veröffentlichungs-Freigabe kann nur der Vorstand ändern.", "fehler")
        return redirect(url_for("oea.fotos", album=request.form.get("album", type=int)))
    f = conn.execute("SELECT * FROM galerie_fotos WHERE id=? AND verein_id=?", (fid, vid)).fetchone()
    if not f:
        conn.close()
        abort(404)
    neu = 0 if f["freigabe_ok"] else 1
    conn.execute("UPDATE galerie_fotos SET freigabe_ok=? WHERE id=? AND verein_id=?", (neu, fid, vid))
    conn.commit()
    conn.close()
    flash("Foto für die Veröffentlichung freigegeben. ✅" if neu
          else "Foto gesperrt – es wird nicht mehr zum Download angeboten. 🚫", "erfolg")
    return redirect(url_for("oea.fotos", album=f["termin_id"]))


@bp.route("/foto/<int:fid>/loeschen", methods=["POST"])
def foto_loeschen(fid):
    """Foto endgültig löschen (nur Vorstand) – Datei, Datensatz, Beitragsbild-Verweise."""
    from .portal import foto_entfernen
    if not _ist_admin():
        flash("Fotos löschen kann nur der Vorstand.", "fehler")
        return redirect(url_for("oea.fotos", album=request.form.get("album", type=int)))
    conn = get_db()
    vid = aktiver_verein_id()
    f = conn.execute("SELECT * FROM galerie_fotos WHERE id=? AND verein_id=?", (fid, vid)).fetchone()
    if not f:
        conn.close()
        abort(404)
    foto_entfernen(conn, vid, f)
    conn.commit()
    rest = conn.execute("SELECT COUNT(*) FROM galerie_fotos WHERE termin_id=? AND verein_id=?",
                        (f["termin_id"], vid)).fetchone()[0]
    conn.close()
    flash("Foto gelöscht. 🗑", "erfolg")
    return redirect(url_for("oea.fotos", album=f["termin_id"]) if rest else url_for("oea.fotos"))


@bp.route("/fotos/zip", methods=["POST"])
def fotos_zip():
    from .portal import _galerie_dir
    conn = get_db()
    vid = aktiver_verein_id()
    album = request.form.get("album", type=int)
    ids = [int(x) for x in request.form.getlist("foto") if str(x).isdigit()]
    sql = "SELECT id, dateiname, freigabe_ok FROM galerie_fotos WHERE verein_id=? AND termin_id=?"
    params = [vid, album]
    if ids:
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        params += ids
    alle = conn.execute(sql, params).fetchall()
    titel = conn.execute("SELECT titel FROM termine WHERE id=? AND verein_id=?", (album, vid)).fetchone()
    conn.close()
    # Gesperrte Fotos (Einverständnis fehlt/widerrufen) nie ausliefern
    rows = [r for r in alle if r["freigabe_ok"]]
    gesperrt = len(alle) - len(rows)
    if not rows:
        flash(f"Keine Fotos zum Herunterladen – {gesperrt} sind für die Veröffentlichung gesperrt."
              if gesperrt else "Keine Fotos ausgewählt.", "fehler")
        return redirect(url_for("oea.fotos", album=album))
    if gesperrt:
        flash(f"{gesperrt} gesperrte Foto(s) wurden nicht mit heruntergeladen.", "erfolg")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in rows:
            pfad = os.path.join(_galerie_dir(), r["dateiname"] or "")
            if r["dateiname"] and os.path.exists(pfad):
                z.write(pfad, arcname=r["dateiname"])
    buf.seek(0)
    name = f"Fotos_{((titel['titel'] if titel else '') or 'Album')[:40].replace(' ', '_')}.zip"
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name=name)


@bp.route("/verteiler", methods=["GET", "POST"])
def verteiler():
    conn = get_db()
    vid = aktiver_verein_id()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            from .kontakte import finden_oder_anlegen
            kid = finden_oder_anlegen(conn, vid, {
                "name": name, "ansprechpartner": request.form.get("ansprechpartner", "").strip(),
                "email": request.form.get("email", "").strip(),
                "telefon": request.form.get("telefon", "").strip()})
            conn.execute("UPDATE kontakte SET kategorie='presse', redaktionsschluss=? WHERE id=?",
                         (request.form.get("redaktionsschluss", "").strip(), kid))
            conn.commit()
            flash("Presse-Kontakt gespeichert. 📇", "erfolg")
        conn.close()
        return redirect(url_for("oea.verteiler"))
    kontakte = conn.execute("SELECT * FROM kontakte WHERE verein_id=? AND kategorie='presse' "
                            "ORDER BY name", (vid,)).fetchall()
    conn.close()
    einst = db.get_einstellungen()
    return render_template("oea/verteiler.html", aktiv="oea", kontakte=kontakte, einst=einst,
                           ist_admin=_ist_admin())


@bp.route("/verteiler/<int:kid>/bearbeiten", methods=["GET", "POST"])
def verteiler_bearbeiten(kid):
    from .. import krypto
    conn = get_db()
    vid = aktiver_verein_id()
    k = conn.execute("SELECT * FROM kontakte WHERE id=? AND verein_id=? AND kategorie='presse'",
                     (kid, vid)).fetchone()
    if not k:
        conn.close()
        abort(404)
    if request.method == "POST":
        felder = {f: (request.form.get(f) or "").strip()
                  for f in ("name", "ansprechpartner", "email", "telefon", "ort",
                            "redaktionsschluss", "notizen")}
        if not felder["name"]:
            conn.close()
            flash("Bitte einen Namen angeben.", "fehler")
            return redirect(request.url)
        # Zugangsdaten verschlüsselt ablegen (Feld zeigt den Klartext, leer = löschen)
        login_neu = (request.form.get("logindaten") or "").strip()
        felder["logindaten"] = krypto.verschluesseln(login_neu) if login_neu else ""
        setz = ", ".join(f"{f}=?" for f in felder)
        conn.execute(f"UPDATE kontakte SET {setz} WHERE id=? AND verein_id=?",
                     list(felder.values()) + [kid, vid])
        conn.commit()
        conn.close()
        flash("Presse-Kontakt gespeichert. 📇", "erfolg")
        return redirect(url_for("oea.verteiler"))
    logindaten = krypto.entschluesseln(k["logindaten"] or "")
    conn.close()
    return render_template("oea/verteiler_bearbeiten.html", aktiv="oea", k=k,
                           logindaten=logindaten)


@bp.route("/verteiler/<int:kid>/schluss", methods=["POST"])
def verteiler_schluss(kid):
    conn = get_db()
    conn.execute("UPDATE kontakte SET redaktionsschluss=? WHERE id=? AND verein_id=? "
                 "AND kategorie='presse'",
                 (request.form.get("redaktionsschluss", "").strip(), kid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Redaktionsschluss gespeichert – der Monats-Automat trägt ihn in den Redaktionsplan ein.", "erfolg")
    return redirect(url_for("oea.verteiler"))


@bp.route("/verteiler/<int:kid>/entfernen", methods=["POST"])
def verteiler_entfernen(kid):
    conn = get_db()
    conn.execute("UPDATE kontakte SET kategorie='' WHERE id=? AND verein_id=? AND kategorie='presse'",
                 (kid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Aus dem Presseverteiler entfernt (Kontakt bleibt erhalten).", "erfolg")
    return redirect(url_for("oea.verteiler"))


@bp.route("/stammtext", methods=["POST"])
def stammtext():
    conn = get_db()
    vid = aktiver_verein_id()
    conn.execute("UPDATE vereine SET presse_boilerplate=?, presse_kontakt=? WHERE id=?",
                 (request.form.get("presse_boilerplate", "").strip(),
                  request.form.get("presse_kontakt", "").strip(), vid))
    conn.commit()
    conn.close()
    flash("Stammtext für Pressemitteilungen gespeichert.", "erfolg")
    return redirect(url_for("oea.verteiler"))


# Content-Hub-Bereich (/oea/hub) – eigene Datei, gleicher Blueprint (Rolle „oeffentlichkeit“ über Präfix oea.)
from . import oea_hub  # noqa: E402,F401
