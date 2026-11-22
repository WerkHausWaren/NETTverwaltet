"""
Lastschriftläufe: Ein Einzug ist ein Vorgang, kein einmaliger Dateidownload.

Bisher erzeugte ein Klick eine XML-Datei und vergaß sie sofort wieder. Niemand
konnte später sehen, wer in welchem Einzug war, ein zweiter Klick zog wortlos
noch einmal ein, und bei einer Rücklastschrift gab es nichts, worauf man sich
beziehen konnte.

Ein Lauf durchläuft drei Zustände:

  Entwurf      – zusammengestellt, prüfbar, änderbar, verwerfbar
  Eingereicht  – die Datei liegt bei der Bank; die Posten sind gebunden
  Verbucht     – das Geld ist da, die Forderungen sind beglichen

Die Posten werden beim Anlegen eingefroren: Ändert jemand später die IBAN eines
Mitglieds, bleibt im Lauf stehen, was tatsächlich eingezogen wurde.
"""
from datetime import date, datetime
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, Response, abort)
from flask_login import current_user

from ..db import get_db, get_einstellungen, now_iso, aktiver_verein_id
from .. import sepa

bp = Blueprint("lastschrift", __name__, url_prefix="/lastschrift")

# Zustände, in denen ein Lauf eine Forderung „belegt" – sie darf dann nicht in
# einen zweiten Lauf geraten. Ein verworfener Lauf gibt seine Posten wieder frei.
BINDEND = ("entwurf", "eingereicht", "verbucht")

STATUS_TEXT = {
    "entwurf": "Entwurf",
    "eingereicht": "bei der Bank eingereicht",
    "verbucht": "verbucht",
    "verworfen": "verworfen",
}


def _lauf(conn, vid, lid):
    return conn.execute("SELECT * FROM sepa_laeufe WHERE id=? AND verein_id=?",
                        (lid, vid)).fetchone()


def _posten(conn, vid, lid, status=None):
    sql = "SELECT * FROM sepa_lauf_posten WHERE verein_id=? AND lauf_id=?"
    p = [vid, lid]
    if status:
        sql += " AND status=?"
        p.append(status)
    return conn.execute(sql + " ORDER BY name", p).fetchall()


def _letzte_nutzung(conn, vid, mid, ausser_lauf=None):
    """Wann wurde das Mandat dieser Person zuletzt tatsächlich benutzt? (ISO-Datum oder '')"""
    if not mid:
        return ""
    sql = ("SELECT MAX(l.eingereicht_am) d FROM sepa_lauf_posten p "
           "JOIN sepa_laeufe l ON l.id=p.lauf_id "
           "WHERE p.verein_id=? AND p.mitglied_id=? AND p.status='dabei' "
           "AND l.status IN ('eingereicht','verbucht')")
    p = [vid, mid]
    if ausser_lauf:
        sql += " AND l.id!=?"
        p.append(ausser_lauf)
    r = conn.execute(sql, p).fetchone()
    return (r["d"] or "")[:10] if r else ""


def _schon_vergeben(conn, vid, jahr):
    """Beitrags-IDs, die bereits in einem bindenden Lauf stecken."""
    return {r["beitrag_id"] for r in conn.execute(
        "SELECT p.beitrag_id FROM sepa_lauf_posten p JOIN sepa_laeufe l ON l.id=p.lauf_id "
        "WHERE p.verein_id=? AND l.jahr=? AND l.status IN (?,?,?) AND p.status!='ausgelassen'",
        (vid, jahr, *BINDEND)).fetchall() if r["beitrag_id"]}


@bp.route("/")
def index():
    """Übersicht aller Läufe – die Historie, die es bisher nicht gab."""
    conn = get_db()
    vid = aktiver_verein_id()
    laeufe = conn.execute(
        "SELECT * FROM sepa_laeufe WHERE verein_id=? ORDER BY id DESC LIMIT 50", (vid,)).fetchall()
    conn.close()
    return render_template("lastschrift/index.html", aktiv="beitraege", laeufe=laeufe,
                           status_text=STATUS_TEXT, heute=date.today().isoformat(),
                           jahr=datetime.now().year)


@bp.route("/neu", methods=["POST"])
def neu():
    """Stellt aus den offenen Beiträgen eines Jahres einen Entwurf zusammen."""
    vid = aktiver_verein_id()
    jahr = request.form.get("jahr", type=int) or datetime.now().year
    einst = get_einstellungen()

    fehlt = sepa.pruefe_voraussetzungen(einst)
    if fehlt:
        flash("Bevor ein Einzug möglich ist, fehlen dem Verein noch: " + ", ".join(fehlt) +
              ". Das trägst du unter Einstellungen ein.", "fehler")
        return redirect(url_for("beitraege.liste", jahr=jahr))

    conn = get_db()
    vergeben = _schon_vergeben(conn, vid, jahr)
    zeilen = conn.execute(
        "SELECT b.*, m.vorname, m.nachname, m.iban, m.bic, m.kontoinhaber, "
        "m.mandatsreferenz, m.mandatsdatum FROM beitraege b JOIN mitglieder m ON m.id=b.mitglied_id "
        "WHERE b.verein_id=? AND b.jahr=? AND b.bezahlt=0 AND m.zahlungsart='SEPA-Lastschrift'",
        (vid, jahr)).fetchall()

    kandidaten, doppelt = [], 0
    for z in zeilen:
        if z["id"] in vergeben:
            doppelt += 1
            continue
        kandidaten.append({
            "beitrag_id": z["id"], "mitglied_id": z["mitglied_id"],
            "name": z["kontoinhaber"] or f"{z['vorname']} {z['nachname']}".strip(),
            "iban": z["iban"] or "", "bic": z["bic"] or "", "betrag": z["betrag"],
            "mandatsreferenz": z["mandatsreferenz"] or "", "mandatsdatum": z["mandatsdatum"] or "",
            "referenz": f"BEITR-{jahr}-{z['mitglied_id']}",
            "verwendungszweck": f"Mitgliedsbeitrag {jahr} - {einst['vereinsname']}".strip(" -"),
        })

    if not kandidaten:
        conn.close()
        if doppelt:
            flash(f"Alle {doppelt} offenen Lastschrift-Beiträge für {jahr} stehen bereits in einem "
                  "Lauf. Ein zweiter Einzug würde doppelt abbuchen.", "info")
        else:
            flash(f"Für {jahr} gibt es keine offenen Beiträge mit Zahlungsart SEPA-Lastschrift.", "info")
        return redirect(url_for("beitraege.liste", jahr=jahr))

    # Vollständige von unvollständigen trennen – beide werden festgehalten,
    # damit auf der Vorschau steht, wer warum fehlt.
    #
    # Jeder Kandidat wird EINZELN geprüft und trägt sein Ergebnis selbst. Früher
    # lief die Zuordnung über den Namen und über eine Menge von Beitrags-IDs –
    # bei zwei Mitgliedern gleichen Namens bekam die zweite Person den Grund der
    # ersten, und eine fehlende Herkunfts-ID hätte alle unvollständigen Posten
    # als „dabei" gelten lassen.
    gute, probleme = [], []
    for k in kandidaten:
        ok, fehlt = sepa.posten_pruefen([k])
        k["_dabei"] = bool(ok)
        k["_grund"] = fehlt[0]["grund"] if fehlt else ""
        if ok:
            gute.append(ok[0])
        else:
            probleme.extend(fehlt)

    # Ein Lauf ganz ohne einziehbaren Posten wäre nur eine leere Hülle. Dann
    # lieber nichts anlegen und stattdessen sagen, was zu tun ist.
    if not gute:
        conn.close()
        namen = ", ".join(f"{p['name']} ({p['grund']})" for p in probleme[:5])
        weitere = f" und {len(probleme) - 5} weitere" if len(probleme) > 5 else ""
        vorspann = (f"{doppelt} Beitrag/Beiträge stehen bereits in einem Lauf und werden nicht "
                    "doppelt eingezogen. ") if doppelt else ""
        flash(vorspann + f"Von den übrigen ist keiner einziehbar: {namen}{weitere}. "
              "Bitte die Angaben beim Mitglied ergänzen.", "fehler")
        return redirect(url_for("beitraege.liste", jahr=jahr))

    summe = round(sum(g["betrag"] for g in gute), 2)
    faellig = sepa.standard_faelligkeit().isoformat()
    cur = conn.execute(
        "INSERT INTO sepa_laeufe (verein_id, quelle, jahr, bezeichnung, faellig_am, status, "
        "anzahl, summe, erstellt_am, erstellt_von) VALUES (?,'beitrag',?,?,?,'entwurf',?,?,?,?)",
        (vid, jahr, f"Mitgliedsbeiträge {jahr}", faellig, len(gute), summe, now_iso(),
         int(current_user.id) if getattr(current_user, "is_authenticated", False) else None))
    lid = cur.lastrowid

    for k in kandidaten:
        dabei = k["_dabei"]
        conn.execute(
            "INSERT INTO sepa_lauf_posten (verein_id, lauf_id, mitglied_id, beitrag_id, name, iban, "
            "bic, betrag, mandatsreferenz, mandatsdatum, verwendungszweck, referenz, status, grund) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, lid, k["mitglied_id"], k["beitrag_id"], k["name"], k["iban"], k["bic"],
             k["betrag"], k["mandatsreferenz"], k["mandatsdatum"], k["verwendungszweck"],
             k["referenz"], "dabei" if dabei else "ausgelassen",
             "" if dabei else (k["_grund"] or "unvollständige Angaben")))
    conn.commit()
    conn.close()

    if doppelt:
        flash(f"{doppelt} Beitrag/Beiträge stehen schon in einem anderen Lauf und wurden "
              "ausgelassen – so wird niemand doppelt eingezogen.", "info")
    betrag_de = f"{summe:.2f}".replace(".", ",")
    flash(f"Entwurf angelegt: {len(gute)} Posten über {betrag_de} €. "
          "Bitte vor dem Erzeugen der Datei prüfen.", "erfolg")
    return redirect(url_for("lastschrift.detail", lid=lid))


@bp.route("/<int:lid>")
def detail(lid):
    """Die Vorschau: wer wird eingezogen, wer fehlt warum, wann wird abgebucht."""
    conn = get_db()
    vid = aktiver_verein_id()
    lauf = _lauf(conn, vid, lid)
    if not lauf:
        conn.close()
        abort(404)
    dabei = _posten(conn, vid, lid, "dabei")
    ausgelassen = _posten(conn, vid, lid, "ausgelassen")
    zurueck = _posten(conn, vid, lid, "ruecklaeufer")
    einst = get_einstellungen()

    # Mandate, die lange nicht genutzt wurden, verfallen nach 36 Monaten. Wir
    # schließen sie NICHT aus – wer bisher ohne diese Verwaltung eingezogen hat,
    # hat sie ja genutzt, nur wussten wir davon nichts. Also nur ein Hinweis.
    from .. import mandate as mandat_modul
    heute = date.today().isoformat()
    alt = []
    for p in dabei:
        verfall = mandat_modul.verfaellt_am(p["mandatsdatum"],
                                            _letzte_nutzung(conn, vid, p["mitglied_id"], lid))
        if verfall and verfall < heute:
            alt.append({"name": p["name"], "verfall": verfall})

    # Stand der Vorabankündigung: wer wurde erreicht, wer hat gar keine Adresse?
    angekuendigt = sum(1 for p in dabei if (p["angekuendigt_am"] or "").strip())
    ohne_mail = 0
    for p in dabei:
        if not p["mitglied_id"]:
            continue
        m = conn.execute("SELECT email FROM mitglieder WHERE id=?", (p["mitglied_id"],)).fetchone()
        if not m or not (m["email"] or "").strip():
            ohne_mail += 1
    # Wie viel Vorlauf bleibt bis zum Einzug? (Vorabankündigung: üblich 14 Tage)
    try:
        vorlauf = (date.fromisoformat(lauf["faellig_am"]) - date.today()).days
    except (ValueError, TypeError):
        vorlauf = None
    conn.close()
    return render_template("lastschrift/detail.html", aktiv="beitraege", lauf=lauf,
                           dabei=dabei, ausgelassen=ausgelassen, ruecklaeufer=zurueck,
                           einst=einst, alt=alt,
                           status_text=STATUS_TEXT, angekuendigt=angekuendigt,
                           ohne_mail=ohne_mail, vorlauf=vorlauf,
                           heute_iso=date.today().isoformat(),
                           frueheste=sepa.standard_faelligkeit(1).isoformat(),
                           summe=round(sum(p["betrag"] for p in dabei), 2))


@bp.route("/<int:lid>/datum", methods=["POST"])
def datum(lid):
    """Einzugstag ändern – nur solange der Lauf ein Entwurf ist."""
    conn = get_db()
    vid = aktiver_verein_id()
    lauf = _lauf(conn, vid, lid)
    if not lauf or lauf["status"] != "entwurf":
        conn.close()
        flash("Der Einzugstag lässt sich nur im Entwurf ändern.", "fehler")
        return redirect(url_for("lastschrift.detail", lid=lid))
    gewuenscht = (request.form.get("faellig_am") or "").strip()
    try:
        d = date.fromisoformat(gewuenscht)
    except ValueError:
        conn.close()
        flash("Bitte ein gültiges Datum wählen.", "fehler")
        return redirect(url_for("lastschrift.detail", lid=lid))
    korrigiert = sepa.naechster_bankarbeitstag(d)
    conn.execute("UPDATE sepa_laeufe SET faellig_am=? WHERE id=?", (korrigiert.isoformat(), lid))
    conn.commit()
    conn.close()
    if korrigiert != d:
        flash(f"Der {d.strftime('%d.%m.%Y')} ist kein Bankarbeitstag – der Einzug wurde auf den "
              f"{korrigiert.strftime('%d.%m.%Y')} gelegt.", "info")
    else:
        flash("Einzugstag gespeichert.", "erfolg")
    return redirect(url_for("lastschrift.detail", lid=lid))


@bp.route("/<int:lid>/ankuendigen", methods=["POST"])
def ankuendigen(lid):
    """Verschickt die Vorabankündigung an alle Betroffenen mit E-Mail-Adresse.

    Vor jedem Einzug muss die Person wissen, wann welcher Betrag abgeht. Wer
    keine Adresse hinterlegt hat, erscheint danach in einer Liste zum Ausdrucken.
    """
    from .. import mail as _mail, mailhtml
    conn = get_db()
    vid = aktiver_verein_id()
    lauf = _lauf(conn, vid, lid)
    if not lauf or lauf["status"] not in ("entwurf", "eingereicht"):
        conn.close()
        flash("Für diesen Lauf lässt sich keine Ankündigung verschicken.", "fehler")
        return redirect(url_for("lastschrift.detail", lid=lid))

    einst = get_einstellungen()
    host = request.host_url.rstrip("/")
    nur_offene = request.form.get("nur_offene") == "1"

    gesendet, ohne_mail, fehlgeschlagen = 0, 0, 0
    for p in _posten(conn, vid, lid, "dabei"):
        if nur_offene and (p["angekuendigt_am"] or "").strip():
            continue
        m = conn.execute("SELECT vorname, nachname, email FROM mitglieder WHERE id=?",
                         (p["mitglied_id"],)).fetchone() if p["mitglied_id"] else None
        adresse = ((m["email"] if m else "") or "").strip()
        if not adresse:
            ohne_mail += 1
            continue
        vorname = (m["vorname"] or "").strip() or p["name"]
        betreff, html = mailhtml.vorabankuendigung(
            einst, host, vorname, p["betrag"], lauf["faellig_am"],
            p["mandatsreferenz"], p["verwendungszweck"])
        try:
            erfolg, _fehler = _mail.einzelmail(einst, adresse, betreff,
                                               _mail._html_zu_text(html), html=html)
        except Exception:  # noqa: BLE001 – eine kaputte Adresse darf den Rest nicht stoppen
            erfolg = False
        if erfolg:
            conn.execute("UPDATE sepa_lauf_posten SET angekuendigt_am=? WHERE id=?",
                         (now_iso(), p["id"]))
            gesendet += 1
        else:
            fehlgeschlagen += 1
    conn.commit()
    conn.close()

    teile = []
    if gesendet:
        teile.append(f"{gesendet} Ankündigung(en) verschickt")
    if fehlgeschlagen:
        teile.append(f"{fehlgeschlagen} konnten NICHT zugestellt werden")
    if ohne_mail:
        teile.append(f"{ohne_mail} ohne E-Mail-Adresse – bitte die Liste zum Ausdrucken nutzen")
    flash(". ".join(teile) + "." if teile else "Es gab niemanden zu benachrichtigen.",
          "fehler" if fehlgeschlagen else "erfolg")
    return redirect(url_for("lastschrift.detail", lid=lid))


@bp.route("/<int:lid>/ankuendigung-drucken")
def ankuendigung_drucken(lid):
    """Druckansicht für alle Betroffenen ohne E-Mail-Adresse."""
    conn = get_db()
    vid = aktiver_verein_id()
    lauf = _lauf(conn, vid, lid)
    if not lauf:
        conn.close()
        abort(404)
    zeilen = []
    for p in _posten(conn, vid, lid, "dabei"):
        m = conn.execute(
            "SELECT vorname, nachname, email, strasse, plz, ort FROM mitglieder WHERE id=?",
            (p["mitglied_id"],)).fetchone() if p["mitglied_id"] else None
        if m and (m["email"] or "").strip():
            continue                       # hat eine Adresse, wurde per Mail erreicht
        zeilen.append({"posten": p, "m": m})
    einst = get_einstellungen()
    conn.close()
    return render_template("lastschrift/ankuendigung_druck.html", lauf=lauf, zeilen=zeilen,
                           einst=einst)


@bp.route("/<int:lid>/datei.xml")
def datei(lid):
    """Die SEPA-Datei zum Lauf. Der Lauf merkt sich, wann sie erzeugt wurde."""
    conn = get_db()
    vid = aktiver_verein_id()
    lauf = _lauf(conn, vid, lid)
    if not lauf:
        conn.close()
        abort(404)
    if lauf["status"] == "verworfen":
        conn.close()
        flash("Dieser Lauf wurde verworfen – dafür gibt es keine Datei.", "fehler")
        return redirect(url_for("lastschrift.detail", lid=lid))
    dabei = _posten(conn, vid, lid, "dabei")
    einst = get_einstellungen()
    posten = [{"name": p["name"], "iban": p["iban"], "bic": p["bic"], "betrag": p["betrag"],
               "mandatsreferenz": p["mandatsreferenz"], "mandatsdatum": p["mandatsdatum"],
               "referenz": p["referenz"], "verwendungszweck": p["verwendungszweck"]}
              for p in dabei]
    xml, probleme = sepa.erzeuge_xml(einst, posten, ausfuehrungsdatum=lauf["faellig_am"])
    if "<DrctDbtTxInf>" not in xml:
        conn.close()
        flash("In diesem Lauf ist kein einziger einziehbarer Posten – es wurde keine Datei erzeugt.",
              "fehler")
        return redirect(url_for("lastschrift.detail", lid=lid))
    msg_id = xml.split("<MsgId>")[1].split("</MsgId>")[0]
    conn.execute("UPDATE sepa_laeufe SET msg_id=?, datei_am=? WHERE id=?", (msg_id, now_iso(), lid))
    conn.commit()
    conn.close()
    name = f"SEPA-{(lauf['bezeichnung'] or 'Einzug').replace(' ', '-')}-{lauf['faellig_am']}.xml"
    return Response(xml, mimetype="application/xml",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


@bp.route("/<int:lid>/eingereicht", methods=["POST"])
def eingereicht(lid):
    """Bestätigt: Die Datei ist bei der Bank. Ab jetzt sind die Posten gebunden."""
    conn = get_db()
    vid = aktiver_verein_id()
    lauf = _lauf(conn, vid, lid)
    if not lauf or lauf["status"] != "entwurf":
        conn.close()
        flash("Nur ein Entwurf lässt sich als eingereicht markieren.", "fehler")
        return redirect(url_for("lastschrift.detail", lid=lid))
    conn.execute("UPDATE sepa_laeufe SET status='eingereicht', eingereicht_am=? WHERE id=?",
                 (now_iso(), lid))
    conn.commit()
    conn.close()
    flash("Als eingereicht vermerkt. Diese Posten kommen in keinen weiteren Lauf – "
          "so kann niemand doppelt eingezogen werden.", "erfolg")
    return redirect(url_for("lastschrift.detail", lid=lid))


def _bankkonto(conn, vid):
    """Das Geldkonto, auf dem die Lastschriften eingehen (Typ „bank")."""
    r = conn.execute("SELECT id FROM geldkonten WHERE verein_id=? AND typ='bank' AND aktiv=1 "
                     "ORDER BY sortierung, id LIMIT 1", (vid,)).fetchone()
    return r["id"] if r else None


def _beitragskonto(conn, vid):
    """Das Ertragskonto für Mitgliedsbeiträge (0100 im gemeinnützigen Kontenrahmen)."""
    return conn.execute("SELECT * FROM konten WHERE verein_id=? AND nummer='0100'", (vid,)).fetchone()


@bp.route("/<int:lid>/verbuchen", methods=["POST"])
def verbuchen(lid):
    """Das Geld ist da: Beiträge als bezahlt markieren und EINE Sammelbuchung anlegen.

    Eine Sammelbuchung statt vieler Einzelbuchungen – so steht im Kassenbuch
    genau das, was auch auf dem Kontoauszug steht: ein Betrag von der Bank.
    """
    from .finanzen import _naechste_belegnr
    conn = get_db()
    vid = aktiver_verein_id()
    lauf = _lauf(conn, vid, lid)
    if not lauf or lauf["status"] != "eingereicht":
        conn.close()
        flash("Nur ein eingereichter Lauf lässt sich verbuchen.", "fehler")
        return redirect(url_for("lastschrift.detail", lid=lid))

    dabei = _posten(conn, vid, lid, "dabei")
    tag = (request.form.get("datum") or "").strip() or lauf["faellig_am"]
    ins_kassenbuch = request.form.get("ins_kassenbuch") != "0"
    summe = round(sum(p["betrag"] for p in dabei), 2)

    beitraege = 0
    for p in dabei:
        if p["beitrag_id"]:
            conn.execute("UPDATE beitraege SET bezahlt=1, bezahlt_am=?, zahlungsart=? "
                         "WHERE id=? AND verein_id=?",
                         (tag, "SEPA-Lastschrift", p["beitrag_id"], vid))
            beitraege += 1

    buchung_id = None
    if ins_kassenbuch and summe > 0:
        konto = _beitragskonto(conn, vid)
        cur = conn.execute(
            "INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, "
            "konto_id, geldkonto_id, sphaere, belegnr, erstellt_am, erstellt_von) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, tag, "einnahme", (konto["name"] if konto else "Mitgliedsbeiträge"),
             f"{lauf['bezeichnung']} – Sammellastschrift, {len(dabei)} Posten",
             summe, konto["id"] if konto else None, _bankkonto(conn, vid),
             (konto["sphaere"] if konto else "ideell"),
             _naechste_belegnr(conn, vid, tag[:4]), now_iso(), "Lastschriftlauf"))
        buchung_id = cur.lastrowid

    conn.execute("UPDATE sepa_laeufe SET status='verbucht', verbucht_am=?, buchung_id=? WHERE id=?",
                 (now_iso(), buchung_id, lid))
    conn.commit()
    conn.close()
    betrag_de = f"{summe:.2f}".replace(".", ",")
    flash(f"Lauf verbucht: {beitraege} Beitrag/Beiträge sind bezahlt"
          + (f", {betrag_de} € als Sammelbuchung im Kassenbuch." if buchung_id
             else ". (Ohne Eintrag ins Kassenbuch.)"), "erfolg")
    return redirect(url_for("lastschrift.detail", lid=lid))


@bp.route("/<int:lid>/ruecklaeufer/<int:pid>", methods=["POST"])
def ruecklaeufer(lid, pid):
    """Eine Abbuchung kam zurück: Forderung wieder offen, Geld gegenbuchen.

    Die Bank holt sich den Betrag zurück und berechnet meist eine Gebühr. Beides
    gehört ins Kassenbuch, sonst stimmt der Kassenstand nicht mehr.
    """
    from .finanzen import _naechste_belegnr
    from .. import mail as _mail
    conn = get_db()
    vid = aktiver_verein_id()
    lauf = _lauf(conn, vid, lid)
    p = conn.execute("SELECT * FROM sepa_lauf_posten WHERE id=? AND lauf_id=? AND verein_id=?",
                     (pid, lid, vid)).fetchone()
    if not lauf or not p or p["status"] != "dabei":
        conn.close()
        flash("Dieser Posten lässt sich nicht als Rückläufer erfassen.", "fehler")
        return redirect(url_for("lastschrift.detail", lid=lid))

    grund = (request.form.get("grund") or "").strip() or "von der Bank zurückgegeben"
    tag = (request.form.get("datum") or "").strip() or date.today().isoformat()
    try:
        gebuehr = round(float((request.form.get("gebuehr") or "0").replace(",", ".")), 2)
    except ValueError:
        gebuehr = 0.0

    conn.execute("UPDATE sepa_lauf_posten SET status='ruecklaeufer', grund=? WHERE id=?",
                 (grund, pid))
    # Die Forderung ist wieder offen
    if p["beitrag_id"]:
        conn.execute("UPDATE beitraege SET bezahlt=0, bezahlt_am='' WHERE id=? AND verein_id=?",
                     (p["beitrag_id"], vid))

    konto = _beitragskonto(conn, vid)
    geldkonto = _bankkonto(conn, vid)
    # Rückbuchung des Beitrags (Ausgabe, damit der Kassenstand wieder stimmt)
    conn.execute(
        "INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, "
        "mitglied_id, konto_id, geldkonto_id, sphaere, belegnr, erstellt_am, erstellt_von) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (vid, tag, "ausgabe", (konto["name"] if konto else "Mitgliedsbeiträge"),
         f"Rücklastschrift {p['name']} – {grund}", p["betrag"], p["mitglied_id"] or None,
         konto["id"] if konto else None, geldkonto, (konto["sphaere"] if konto else "ideell"),
         _naechste_belegnr(conn, vid, tag[:4]), now_iso(), "Lastschriftlauf"))
    if gebuehr > 0:
        gk = conn.execute("SELECT * FROM konten WHERE verein_id=? AND nummer='0200'", (vid,)).fetchone()
        conn.execute(
            "INSERT INTO buchungen (verein_id, datum, typ, kategorie, beschreibung, betrag, "
            "mitglied_id, konto_id, geldkonto_id, sphaere, belegnr, erstellt_am, erstellt_von) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, tag, "ausgabe", (gk["name"] if gk else "Verwaltungskosten"),
             f"Bankgebühr Rücklastschrift {p['name']}", gebuehr, p["mitglied_id"] or None,
             gk["id"] if gk else None, geldkonto, (gk["sphaere"] if gk else "ideell"),
             _naechste_belegnr(conn, vid, tag[:4]), now_iso(), "Lastschriftlauf"))

    # Betroffene Person informieren (best effort – ein Mailproblem darf nichts blockieren)
    benachrichtigt = False
    if request.form.get("benachrichtigen") and p["mitglied_id"]:
        m = conn.execute("SELECT vorname, email FROM mitglieder WHERE id=?",
                         (p["mitglied_id"],)).fetchone()
        adresse = ((m["email"] if m else "") or "").strip()
        if adresse:
            einst = get_einstellungen()
            betrag_de = f"{p['betrag']:.2f}".replace(".", ",")
            text = (f"Hallo {(m['vorname'] or '').strip() or p['name']},\n\n"
                    f"unsere Abbuchung über {betrag_de} € ist von deiner Bank zurückgegeben worden"
                    f" ({grund}). Das kann viele Gründe haben – oft ist das Konto nur "
                    "vorübergehend nicht gedeckt oder die Bankverbindung hat sich geändert.\n\n"
                    "Bitte melde dich kurz bei uns, dann klären wir gemeinsam, wie es weitergeht. "
                    "Falls sich deine Bankverbindung geändert hat, gib uns gern die neue durch.\n\n"
                    f"Herzliche Grüße\n{einst['vereinsname']}")
            try:
                benachrichtigt, _f = _mail.einzelmail(
                    einst, adresse, "Rückfrage zu deinem Beitrag", text)
            except Exception:  # noqa: BLE001
                benachrichtigt = False
    conn.commit()
    conn.close()
    flash(f"Rückläufer erfasst: {p['name']}. Der Beitrag ist wieder offen, die Rückbuchung"
          + (f" und {gebuehr:.2f} € Gebühr".replace(".", ",") if gebuehr > 0 else "")
          + " stehen im Kassenbuch."
          + (" Die Person wurde benachrichtigt." if benachrichtigt else ""), "erfolg")
    return redirect(url_for("lastschrift.detail", lid=lid))


@bp.route("/<int:lid>/verwerfen", methods=["POST"])
def verwerfen(lid):
    """Entwurf verwerfen. Die Forderungen sind danach wieder frei."""
    conn = get_db()
    vid = aktiver_verein_id()
    lauf = _lauf(conn, vid, lid)
    if not lauf:
        conn.close()
        abort(404)
    if lauf["status"] not in ("entwurf", "eingereicht"):
        conn.close()
        flash("Ein verbuchter Lauf lässt sich nicht mehr verwerfen.", "fehler")
        return redirect(url_for("lastschrift.detail", lid=lid))
    conn.execute("UPDATE sepa_laeufe SET status='verworfen' WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    if lauf["status"] == "eingereicht":
        flash("Lauf verworfen. Achtung: Wenn die Datei schon bei der Bank liegt, muss die "
              "Rückgabe dort angestoßen werden – die Verwaltung kann das nicht.", "info")
    else:
        flash("Entwurf verworfen. Die Beiträge sind wieder für einen neuen Lauf frei.", "erfolg")
    return redirect(url_for("lastschrift.index"))
