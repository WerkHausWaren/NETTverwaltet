"""Mitgliederverwaltung: Liste, Anlegen, Bearbeiten, Detail, Löschen, CSV-Im-/Export."""
import csv
import io
import os
import time
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, Response, send_from_directory, abort)
from ..sprache import SPRECH_SPRACHEN
from ..db import get_db, get_einstellungen, now_iso, aktiver_verein_id
from ..funktionen import wer
from .. import xlsx, speicher, carddav
from ..kontakte_sync import abgleich as nc_abgleich

bp = Blueprint("mitglieder", __name__, url_prefix="/mitglieder")

FELDER = [
    "mitgliedsnummer", "anrede", "titel", "vorname", "nachname", "firma",
    "strasse", "plz", "ort", "land", "email", "email_weitere", "telefon", "mobil",
    "geburtsdatum", "eintrittsdatum", "austrittsdatum", "status",
    "beitragsgruppe", "beitrag", "zahlungsweise", "zahlungsart",
    "iban", "bic", "kontoinhaber", "mandatsreferenz", "mandatsdatum", "notizen",
    "komm_genutzt", "komm_erlaubt", "mandat_vorhanden",
    "abwesend", "abwesend_von", "abwesend_bis", "abwesend_notiz",
    "sprachen",
]

# Mehrfach-Auswahl-Felder (Kommunikationswege): kommagetrennt gespeichert
MEHRFACH = ["komm_genutzt", "komm_erlaubt", "sprachen"]

# Verfügbare Kommunikationswege
KOMM_WEGE = ["E-Mail", "Telefon", "WhatsApp", "Signal", "Nextcloud", "Post", "Sonstiges"]

STATUS_OPTIONEN = ["aktiv", "inaktiv", "ausgetreten", "Ehrenmitglied"]
ZAHLUNGSWEISEN = ["monatlich", "vierteljährlich", "halbjährlich", "jährlich"]
ZAHLUNGSARTEN = ["Überweisung", "SEPA-Lastschrift", "Bar"]


def _mandat_vervollstaendigen(conn, vid, mid):
    """Vergibt Mandatsreferenz und Unterschriftsdatum, wenn sie fehlen.

    Eine bereits benutzte Referenz bleibt unangetastet – sie steht so bei der
    Bank und muss zu den bisherigen Einzügen passen.
    """
    from .. import mandate
    if mandate.in_benutzung(conn, vid, mid):
        return {}
    return mandate.sicherstellen(conn, vid, mid)


def _mandate_ordner():
    e = get_einstellungen()
    return speicher.mandate_ordner(e["id"], e["vereinsname"], e["dok_speicherort"])


def _mandat_speichern(datei):
    """Speichert eine hochgeladene Mandats-Datei und gibt den Dateinamen zurück (oder '').

    Positivliste wie beim Gesundheitszeugnis (_gz_speichern): Der Upload steht
    auch Mitgliedern im Portal offen – ohne die Liste wäre er der einzige Weg,
    beliebige Fremd-Dateitypen in die Datei-Auslieferung zu bringen (Review 05.10.)."""
    if not datei or not datei.filename:
        return ""
    endung = os.path.splitext(datei.filename)[1].lower()
    if endung not in (".pdf", ".jpg", ".jpeg", ".png", ".heic", ".webp"):
        return ""
    basis = os.path.basename(datei.filename).replace("/", "_")
    dateiname = f"{time.strftime('%Y%m%d-%H%M%S')}_{basis}"
    speicher.speichern(get_einstellungen(), "mandate", dateiname, datei.read())
    return dateiname


def _formdaten():
    daten = {}
    for f in FELDER:
        if f == "sprachen":
            # Checkboxen + Freitextfeld (kommagetrennt) zusammenführen
            teile = []
            for wert in request.form.getlist(f):
                for s in wert.split(","):
                    s = s.strip()
                    if s and s not in teile:
                        teile.append(s)
            daten[f] = ",".join(teile)
            continue
        if f in MEHRFACH:
            daten[f] = ",".join(request.form.getlist(f))
            continue
        if f in ("mandat_vorhanden", "abwesend"):
            daten[f] = 1 if request.form.get(f) else 0
            continue
        if f == "email_weitere":
            weitere = [e.strip() for e in request.form.getlist("email_weitere") if e.strip()]
            daten[f] = "\n".join(weitere)
            continue
        wert = request.form.get(f, "").strip()
        if f == "beitrag":
            try:
                wert = float(wert.replace(",", ".")) if wert else 0
            except ValueError:
                wert = 0
        daten[f] = wert
    return daten


@bp.route("/")
def liste():
    conn = get_db()
    vid = aktiver_verein_id()
    suche = request.args.get("suche", "").strip()
    status = request.args.get("status", "").strip()
    gruppe = request.args.get("gruppe", "").strip()

    sql = "SELECT DISTINCT m.* FROM mitglieder m"
    params = []
    bedingungen = ["m.verein_id = ?"]
    params.append(vid)
    if gruppe == "ohne":
        # Personen, die (noch) keiner Gruppe zugeordnet sind
        bedingungen.append("NOT EXISTS (SELECT 1 FROM mitglied_gruppe mg WHERE mg.mitglied_id = m.id)")
    elif gruppe:
        sql += " JOIN mitglied_gruppe mg ON mg.mitglied_id = m.id"
        bedingungen.append("mg.gruppe_id = ?")
        params.append(gruppe)
    if suche:
        like = f"%{suche}%"
        bedingungen.append("(m.vorname LIKE ? OR m.nachname LIKE ? OR m.email LIKE ? "
                           "OR m.ort LIKE ? OR m.mitgliedsnummer LIKE ? OR m.firma LIKE ?)")
        params += [like, like, like, like, like, like]
    if status:
        bedingungen.append("m.status = ?")
        params.append(status)
    sql += " WHERE " + " AND ".join(bedingungen)
    sql += " ORDER BY m.nachname COLLATE NOCASE, m.vorname COLLATE NOCASE"

    mitglieder = conn.execute(sql, params).fetchall()
    gruppen = conn.execute("SELECT * FROM gruppen WHERE verein_id=? ORDER BY rang, name", (vid,)).fetchall()
    # Gruppen je Mitglied (für die Gruppen-Spalte, sortiert nach Rang)
    gruppen_je_mitglied = {}
    for r in conn.execute(
            "SELECT mg.mitglied_id, g.name FROM mitglied_gruppe mg JOIN gruppen g ON g.id=mg.gruppe_id "
            "WHERE g.verein_id=? ORDER BY g.rang, g.name", (vid,)).fetchall():
        gruppen_je_mitglied.setdefault(r["mitglied_id"], []).append(r["name"])

    mit_schluessel = {r["inhaber_mitglied_id"] for r in conn.execute(
        "SELECT DISTINCT inhaber_mitglied_id FROM schluesselbuch "
        "WHERE verein_id=? AND zurueckgegeben_am='' AND inhaber_mitglied_id IS NOT NULL", (vid,)).fetchall()}

    # Zählung: nur Gruppen, die als „Mitglieder" gelten (Einstellungen → Gruppen & Zählung)
    from ..db import get_einstellungen, mitglied_gruppen_filter
    mg_sql, mg_params = mitglied_gruppen_filter(get_einstellungen())
    gesamt = conn.execute(f"SELECT COUNT(*) c FROM mitglieder m WHERE m.verein_id=?{mg_sql}",
                          [vid] + mg_params).fetchone()["c"]
    aktive = conn.execute(f"SELECT COUNT(*) c FROM mitglieder m WHERE m.verein_id=? AND m.status='aktiv'{mg_sql}",
                          [vid] + mg_params).fetchone()["c"]
    beitrag_summe = conn.execute(
        f"SELECT COALESCE(SUM(m.beitrag),0) s FROM mitglieder m "
        f"WHERE m.verein_id=? AND m.status IN ('aktiv','Ehrenmitglied'){mg_sql}",
        [vid] + mg_params).fetchone()["s"]
    # Übrige Personen (Freunde, Externe …), die nicht als Mitglieder zählen
    alle_personen = conn.execute("SELECT COUNT(*) c FROM mitglieder WHERE verein_id=?", (vid,)).fetchone()["c"]
    weitere = (alle_personen - gesamt) if mg_sql else 0
    # Personen je Gruppe (für die Filterleiste)
    anzahl_je_gruppe = {r["gruppe_id"]: r["n"] for r in conn.execute(
        "SELECT mg.gruppe_id, COUNT(*) n FROM mitglied_gruppe mg "
        "JOIN mitglieder m ON m.id=mg.mitglied_id WHERE m.verein_id=? GROUP BY mg.gruppe_id",
        (vid,)).fetchall()}
    ohne_gruppe = conn.execute(
        "SELECT COUNT(*) c FROM mitglieder m WHERE m.verein_id=? AND NOT EXISTS "
        "(SELECT 1 FROM mitglied_gruppe mg WHERE mg.mitglied_id=m.id)", (vid,)).fetchone()["c"]
    # Eigene Felder als zusätzliche Spalten
    eigene_felder, feldwerte = _felder_export(conn, vid)
    conn.close()
    return render_template("mitglieder/liste.html", aktiv="mitglieder",
                           mitglieder=mitglieder, gruppen=gruppen,
                           suche=suche, status=status, gruppe=gruppe,
                           status_optionen=STATUS_OPTIONEN, mit_schluessel=mit_schluessel,
                           gesamt=gesamt, aktive=aktive, beitrag_summe=beitrag_summe,
                           weitere=weitere, gruppen_je_mitglied=gruppen_je_mitglied,
                           alle_personen=alle_personen, anzahl_je_gruppe=anzahl_je_gruppe,
                           ohne_gruppe=ohne_gruppe,
                           eigene_felder=eigene_felder, feldwerte=feldwerte)


FELD_TYPEN = [("text", "Text"), ("mehrzeilig", "Mehrzeiliger Text"), ("datum", "Datum"),
              ("zahl", "Zahl"), ("ja_nein", "Ja/Nein"), ("auswahl", "Auswahl")]


def _felder_def(conn, vid):
    return conn.execute("SELECT * FROM mitglied_felder WHERE verein_id=? ORDER BY reihenfolge, id",
                        (vid,)).fetchall()


def _feldwerte(conn, mid):
    return {r["feld_id"]: r["wert"] for r in conn.execute(
        "SELECT feld_id, wert FROM mitglied_feldwerte WHERE mitglied_id=?", (mid,)).fetchall()}


def _felder_export(conn, vid):
    """Feld-Definitionen + {mitglied_id: {feld_id: wert}} für Listen und Exporte."""
    felder = _felder_def(conn, vid)
    werte = {}
    if felder:
        for r in conn.execute(
                "SELECT w.mitglied_id, w.feld_id, w.wert FROM mitglied_feldwerte w "
                "JOIN mitglieder m ON m.id = w.mitglied_id WHERE m.verein_id=?", (vid,)).fetchall():
            werte.setdefault(r["mitglied_id"], {})[r["feld_id"]] = r["wert"]
    return felder, werte


def _feldwerte_speichern(conn, vid, mid):
    """Übernimmt die Werte der benutzerdefinierten Felder aus dem Formular."""
    for f in _felder_def(conn, vid):
        if f["typ"] == "ja_nein":
            wert = "1" if request.form.get(f"feld_{f['id']}") else "0"
        else:
            wert = (request.form.get(f"feld_{f['id']}") or "").strip()
        conn.execute(
            "INSERT INTO mitglied_feldwerte (mitglied_id, feld_id, wert) VALUES (?,?,?) "
            "ON CONFLICT(mitglied_id, feld_id) DO UPDATE SET wert=excluded.wert",
            (mid, f["id"], wert))


@bp.route("/felder", methods=["GET", "POST"])
def felder():
    conn = get_db()
    vid = aktiver_verein_id()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        typ = request.form.get("typ") or "text"
        if typ not in dict(FELD_TYPEN):
            typ = "text"
        optionen = (request.form.get("optionen") or "").strip()
        if name:
            maxr = conn.execute("SELECT COALESCE(MAX(reihenfolge),0) m FROM mitglied_felder WHERE verein_id=?",
                                (vid,)).fetchone()["m"]
            conn.execute("INSERT INTO mitglied_felder (verein_id, name, typ, optionen, reihenfolge, erstellt_am) "
                         "VALUES (?,?,?,?,?,?)", (vid, name, typ, optionen, maxr + 1, now_iso()))
            conn.commit()
            flash(f"Feld „{name}“ hinzugefügt.", "erfolg")
        else:
            flash("Bitte einen Feldnamen angeben.", "fehler")
        conn.close()
        return redirect(url_for("mitglieder.felder"))
    felder_liste = _felder_def(conn, vid)
    conn.close()
    return render_template("mitglieder/felder.html", aktiv="mitglieder",
                           felder=felder_liste, feld_typen=FELD_TYPEN)


@bp.route("/felder/<int:fid>/loeschen", methods=["POST"])
def feld_loeschen(fid):
    conn = get_db()
    conn.execute("DELETE FROM mitglied_felder WHERE id=? AND verein_id=?", (fid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Feld gelöscht (samt gespeicherter Werte).", "erfolg")
    return redirect(url_for("mitglieder.felder"))


@bp.route("/neu", methods=["GET", "POST"])
def neu():
    conn = get_db()
    vid = aktiver_verein_id()
    if request.method == "POST":
        daten = _formdaten()
        if not daten["mitgliedsnummer"]:
            maxnr = conn.execute(
                "SELECT MAX(CAST(mitgliedsnummer AS INTEGER)) m FROM mitglieder "
                "WHERE verein_id=? AND mitgliedsnummer GLOB '[0-9]*'", (vid,)).fetchone()["m"]
            daten["mitgliedsnummer"] = str((maxnr or 0) + 1)
        spalten = "verein_id, " + ", ".join(FELDER) + ", erstellt_am, geaendert_am, erstellt_von"
        platzhalter = ", ".join(["?"] * (len(FELDER) + 4))
        werte = [vid] + [daten[f] for f in FELDER] + [now_iso(), now_iso(), wer()]
        cur = conn.execute(f"INSERT INTO mitglieder ({spalten}) VALUES ({platzhalter})", werte)
        mid = cur.lastrowid
        _gruppen_speichern(conn, mid)
        _feldwerte_speichern(conn, vid, mid)
        mandat = _mandat_speichern(request.files.get("mandat_datei"))
        if mandat:
            conn.execute("UPDATE mitglieder SET mandat_dateiname=? WHERE id=?", (mandat, mid))
        _mandat_vervollstaendigen(conn, vid, mid)
        conn.commit()
        conn.close()
        flash(f"Mitglied „{daten['vorname']} {daten['nachname']}“ wurde angelegt.", "erfolg")
        return redirect(url_for("mitglieder.detail", mid=mid))

    gruppen = conn.execute("SELECT * FROM gruppen WHERE verein_id=? ORDER BY rang, name", (vid,)).fetchall()
    # Neue Mitglieder landen standardmäßig in der Gruppe „Mitglied" (abwählbar) –
    # sonst zählen sie bei aktivierter Gruppen-Zählung nirgends mit.
    vorbelegt = [g["id"] for g in gruppen if (g["name"] or "").strip().lower() in ("mitglied", "mitglieder")]
    felder = _felder_def(conn, vid)
    conn.close()
    return render_template("mitglieder/formular.html", aktiv="mitglieder",
                           m=None, gruppen=gruppen, mitglied_gruppen=vorbelegt,
                           status_optionen=STATUS_OPTIONEN, felder=felder, feldwerte={},
                           zahlungsweisen=ZAHLUNGSWEISEN, zahlungsarten=ZAHLUNGSARTEN,
                           komm_wege=KOMM_WEGE, sprachen_auswahl=SPRECH_SPRACHEN)


def _hole_mitglied(conn, mid):
    """Lädt ein Mitglied nur, wenn es zum aktiven Verein gehört."""
    return conn.execute("SELECT * FROM mitglieder WHERE id=? AND verein_id=?",
                        (mid, aktiver_verein_id())).fetchone()


def _austritt_vorschlag(einst, kuendigung_am):
    """Rechnet aus, zu welchem Termin der Austritt nach Satzung wirksam wird.

    Beispiel: Kündigungsfrist 90 Tage zum Jahresende, Kündigung am 15.11. →
    31.12. ist zu knapp (nur 46 Tage), also erst zum 31.12. des Folgejahres.
    """
    from datetime import date as _d, timedelta as _td
    try:
        k = _d.fromisoformat(kuendigung_am)
    except (ValueError, TypeError):
        k = _d.today()
    frist = (einst["austritt_frist_tage"] if einst and "austritt_frist_tage" in einst.keys() else None) or 0
    zum = (einst["austritt_zum"] if einst and "austritt_zum" in einst.keys() else "") or "jahresende"
    frueheste = k + _td(days=frist)
    if zum == "sofort":
        return frueheste.isoformat(), frist, "sofort (Frist eingerechnet)"
    if zum == "quartalsende":
        jahr, q_ende = frueheste.year, None
        for monat, tag in ((3, 31), (6, 30), (9, 30), (12, 31)):
            kandidat = _d(jahr, monat, tag)
            if kandidat >= frueheste:
                q_ende = kandidat
                break
        if q_ende is None:
            q_ende = _d(jahr + 1, 3, 31)
        return q_ende.isoformat(), frist, "zum Quartalsende"
    if zum == "monatsende":
        import calendar as _cal
        letzter = _cal.monthrange(frueheste.year, frueheste.month)[1]
        return _d(frueheste.year, frueheste.month, letzter).isoformat(), frist, "zum Monatsende"
    jahresende = _d(frueheste.year, 12, 31)
    if jahresende < frueheste:
        jahresende = _d(frueheste.year + 1, 12, 31)
    return jahresende.isoformat(), frist, "zum Jahresende"


@bp.route("/<int:mid>/austritt", methods=["GET", "POST"])
def austritt(mid):
    """Geführter Austritt: Frist prüfen, Datum vorschlagen, Bestätigung senden."""
    conn = get_db()
    vid = aktiver_verein_id()
    m = _hole_mitglied(conn, mid)
    einst = get_einstellungen()
    if request.method == "POST":
        kuendigung = request.form.get("kuendigung_am") or now_iso()[:10]
        austritts_datum = request.form.get("austrittsdatum") or ""
        grund = (request.form.get("kuendigung_grund") or "").strip()
        sofort_beenden = bool(request.form.get("sofort_beenden"))
        neuer_status = "ausgetreten" if (sofort_beenden or austritts_datum <= now_iso()[:10]) else m["status"]
        conn.execute("UPDATE mitglieder SET kuendigung_am=?, kuendigung_grund=?, austrittsdatum=?, "
                     "status=?, mandat_vorhanden=0 WHERE id=? AND verein_id=?",
                     (kuendigung, grund, austritts_datum, neuer_status, mid, vid))
        conn.commit()
        conn.close()
        # Bestätigungsschreiben per Mail (best effort)
        gesendet = False
        if request.form.get("bestaetigung") and (m["email"] or "").strip():
            try:
                from .. import mail as _mail, mailhtml
                d = austritts_datum
                d_de = f"{d[8:10]}.{d[5:7]}.{d[:4]}" if len(d) == 10 else d
                inhalt = (f"<p>Hallo {m['vorname']},</p>"
                          f"<p>hiermit bestätigen wir deinen Austritt aus <strong>{einst['vereinsname']}</strong> "
                          f"zum <strong>{d_de}</strong>.</p>"
                          "<p>Ein erteiltes SEPA-Lastschriftmandat endet damit; bis zum Austrittstermin "
                          "fällige Beiträge bleiben davon unberührt.</p>"
                          "<p>Wir danken dir herzlich für deine Zeit und deine Unterstützung. Solltest du "
                          "irgendwann zurückkommen wollen: Die Tür steht dir immer offen.</p>"
                          f"<p>Alles Gute für dich!<br>dein Vorstand von {einst['vereinsname']}</p>")
                html = mailhtml.rahmen(einst, request.host_url.rstrip("/"), inhalt)
                gesendet, _f = _mail.einzelmail(einst, m["email"],
                                                f"Bestätigung deines Austritts aus {einst['vereinsname']}",
                                                _mail._html_zu_text(html), html=html)
            except Exception:  # noqa: BLE001
                pass
        flash(f"Austritt zum {austritts_datum} vermerkt."
              + (" Bestätigung per E-Mail verschickt. ✉️" if gesendet else ""), "erfolg")
        return redirect(url_for("mitglieder.detail", mid=mid))
    heute = now_iso()[:10]
    vorschlag, frist_tage, regel = _austritt_vorschlag(einst, heute)
    offene = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(betrag),0) s FROM beitraege "
        "WHERE mitglied_id=? AND bezahlt=0", (mid,)).fetchone() if _tabelle_da(conn, "beitraege") else None
    conn.close()
    return render_template("mitglieder/austritt.html", aktiv="mitglieder", m=m, heute=heute,
                           vorschlag=vorschlag, frist_tage=frist_tage, regel=regel,
                           einst=einst, offene=offene)


def _tabelle_da(conn, name):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


@bp.route("/<int:mid>")
def detail(mid):
    conn = get_db()
    m = _hole_mitglied(conn, mid)
    if not m:
        conn.close()
        flash("Mitglied nicht gefunden.", "fehler")
        return redirect(url_for("mitglieder.liste"))
    gruppen = conn.execute(
        "SELECT g.* FROM gruppen g JOIN mitglied_gruppe mg ON mg.gruppe_id=g.id "
        "WHERE mg.mitglied_id=? ORDER BY g.name", (mid,)).fetchall()
    beitraege = conn.execute(
        "SELECT * FROM beitraege WHERE mitglied_id=? ORDER BY jahr DESC, faellig_am DESC", (mid,)).fetchall()
    spenden = conn.execute(
        "SELECT datum, betrag, zweck, art FROM spenden WHERE verein_id=? AND mitglied_id=? "
        "ORDER BY datum DESC, id DESC", (aktiver_verein_id(), mid)).fetchall()
    spenden_summe = sum((s["betrag"] or 0) for s in spenden)
    # Kassenbuch-Buchungen, die diesem Mitglied zugeordnet sind (Beiträge, Spenden …)
    buchungen_mitglied = conn.execute(
        "SELECT b.id, b.datum, b.typ, b.betrag, b.beschreibung, b.kategorie, "
        "k.nummer AS konto_nr, k.name AS konto_name "
        "FROM buchungen b LEFT JOIN konten k ON k.id=b.konto_id "
        "WHERE b.verein_id=? AND b.mitglied_id=? ORDER BY b.datum DESC LIMIT 30",
        (aktiver_verein_id(), mid)).fetchall()
    schluessel = conn.execute(
        "SELECT * FROM schluesselbuch WHERE inhaber_mitglied_id=? AND verein_id=? "
        "ORDER BY (zurueckgegeben_am != ''), ausgegeben_am DESC", (mid, aktiver_verein_id())).fetchall()
    felder = _felder_def(conn, aktiver_verein_id())
    werte = _feldwerte(conn, mid)
    eigene_felder = [{"name": f["name"], "typ": f["typ"], "wert": werte.get(f["id"], "")} for f in felder]
    kinder = conn.execute(
        "SELECT * FROM mitglied_kinder WHERE mitglied_id=? ORDER BY austritt_datum='', austritt_datum, name",
        (mid,)).fetchall()
    conn.close()
    hat_schluessel = any(not s["zurueckgegeben_am"] for s in schluessel)
    return render_template("mitglieder/detail.html", aktiv="mitglieder",
                           m=m, gruppen=gruppen, beitraege=beitraege, eigene_felder=eigene_felder,
                           spenden=spenden, spenden_summe=spenden_summe,
                           buchungen_mitglied=buchungen_mitglied,
                           schluessel=schluessel, hat_schluessel=hat_schluessel, kinder=kinder)


@bp.route("/<int:mid>/kind", methods=["POST"])
def kind_neu(mid):
    from ..db import get_einstellungen
    einst = get_einstellungen()
    if not (einst and einst.get("modul_kinder")):
        flash("Der Bereich Kinder/Angehörige ist für diesen Verein nicht aktiviert (Einstellungen → Dashboard-Module).", "fehler")
        return redirect(url_for("mitglieder.detail", mid=mid))
    conn = get_db()
    vid = aktiver_verein_id()
    if not _hole_mitglied(conn, mid):
        conn.close()
        flash("Mitglied nicht gefunden.", "fehler")
        return redirect(url_for("mitglieder.liste"))
    name = (request.form.get("name") or "").strip()
    if name:
        conn.execute(
            "INSERT INTO mitglied_kinder (verein_id, mitglied_id, name, geburtsdatum, austritt_datum, notiz, erstellt_am) "
            "VALUES (?,?,?,?,?,?,?)",
            (vid, mid, name, (request.form.get("geburtsdatum") or "").strip(),
             (request.form.get("austritt_datum") or "").strip(),
             (request.form.get("notiz") or "").strip(), now_iso()))
        conn.commit()
        flash(f"Kind „{name}“ hinzugefügt.", "erfolg")
    else:
        flash("Bitte einen Namen angeben.", "fehler")
    conn.close()
    return redirect(url_for("mitglieder.detail", mid=mid) + "#kinder")


@bp.route("/<int:mid>/kind/<int:kid>/loeschen", methods=["POST"])
def kind_loeschen(mid, kid):
    conn = get_db()
    conn.execute("DELETE FROM mitglied_kinder WHERE id=? AND mitglied_id=? AND verein_id=?",
                 (kid, mid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Eintrag gelöscht.", "erfolg")
    return redirect(url_for("mitglieder.detail", mid=mid) + "#kinder")


@bp.route("/<int:mid>/bearbeiten", methods=["GET", "POST"])
def bearbeiten(mid):
    conn = get_db()
    vid = aktiver_verein_id()
    m = _hole_mitglied(conn, mid)
    if not m:
        conn.close()
        flash("Mitglied nicht gefunden.", "fehler")
        return redirect(url_for("mitglieder.liste"))
    if request.method == "POST":
        daten = _formdaten()
        setzt = ", ".join(f"{f}=?" for f in FELDER) + ", geaendert_am=?"
        werte = [daten[f] for f in FELDER] + [now_iso(), mid]
        conn.execute(f"UPDATE mitglieder SET {setzt} WHERE id=?", werte)
        conn.execute("DELETE FROM mitglied_gruppe WHERE mitglied_id=?", (mid,))
        _gruppen_speichern(conn, mid)
        _feldwerte_speichern(conn, vid, mid)
        mandat = _mandat_speichern(request.files.get("mandat_datei"))
        if mandat:
            conn.execute("UPDATE mitglieder SET mandat_dateiname=? WHERE id=?", (mandat, mid))
        ergaenzt = _mandat_vervollstaendigen(conn, vid, mid)
        conn.commit()
        conn.close()
        if ergaenzt.get("referenz"):
            flash(f"Änderungen gespeichert. Mandatsreferenz {ergaenzt['referenz']} wurde vergeben.",
                  "erfolg")
        else:
            flash("Änderungen gespeichert.", "erfolg")
        return redirect(url_for("mitglieder.detail", mid=mid))

    gruppen = conn.execute("SELECT * FROM gruppen WHERE verein_id=? ORDER BY name", (vid,)).fetchall()
    mitglied_gruppen = [r["gruppe_id"] for r in conn.execute(
        "SELECT gruppe_id FROM mitglied_gruppe WHERE mitglied_id=?", (mid,)).fetchall()]
    felder = _felder_def(conn, vid)
    feldwerte = _feldwerte(conn, mid)
    conn.close()
    return render_template("mitglieder/formular.html", aktiv="mitglieder",
                           m=m, gruppen=gruppen, mitglied_gruppen=mitglied_gruppen,
                           status_optionen=STATUS_OPTIONEN, felder=felder, feldwerte=feldwerte,
                           zahlungsweisen=ZAHLUNGSWEISEN, zahlungsarten=ZAHLUNGSARTEN,
                           komm_wege=KOMM_WEGE, sprachen_auswahl=SPRECH_SPRACHEN)


@bp.route("/<int:mid>/loeschen", methods=["POST"])
def loeschen(mid):
    conn = get_db()
    m = _hole_mitglied(conn, mid)
    if m:
        conn.execute("DELETE FROM mitglieder WHERE id=?", (mid,))
        conn.commit()
        flash(f"Mitglied „{m['vorname']} {m['nachname']}“ wurde gelöscht.", "erfolg")
    conn.close()
    return redirect(url_for("mitglieder.liste"))


@bp.route("/<int:mid>/mandat")
def mandat(mid):
    conn = get_db()
    m = conn.execute("SELECT mandat_dateiname FROM mitglieder WHERE id=? AND verein_id=?",
                     (mid, aktiver_verein_id())).fetchone()
    conn.close()
    if not m or not m["mandat_dateiname"]:
        abort(404)
    return speicher.sende(get_einstellungen(), "mandate", m["mandat_dateiname"])


def _gruppen_speichern(conn, mid):
    vid = aktiver_verein_id()
    for gid in request.form.getlist("gruppen"):
        # nur Gruppen des aktiven Vereins zulassen
        ok = conn.execute("SELECT 1 FROM gruppen WHERE id=? AND verein_id=?", (gid, vid)).fetchone()
        if ok:
            conn.execute("INSERT OR IGNORE INTO mitglied_gruppe (mitglied_id, gruppe_id) VALUES (?, ?)",
                         (mid, gid))


# --------------------------- CSV Export / Import ---------------------------
@bp.route("/nextcloud-abgleich", methods=["GET", "POST"])
def nextcloud_abgleich():
    einst = get_einstellungen()
    plan = None
    konfig = carddav.konfiguriert(einst)

    aktion = request.form.get("aktion") if request.method == "POST" else None

    # Adressbuch-Auswahl (im selben Formular) übernehmen und speichern
    if request.method == "POST" and "adressbuch" in request.form:
        wahl = (request.form.get("adressbuch") or "").strip()
        conn = get_db()
        conn.execute("UPDATE vereine SET ncc_adressbuch=? WHERE id=?", (wahl, aktiver_verein_id()))
        conn.commit()
        conn.close()
        einst = get_einstellungen()      # neu laden, damit Vorschau/Abgleich das gewählte Buch nutzt

    if aktion in ("vorschau", "anwenden"):
        anwenden = aktion == "anwenden"
        app_zugang = request.form.get("app_zugang") == "1"
        plan = nc_abgleich(einst, anwenden=anwenden, app_zugang=app_zugang)
        if plan.get("fehler"):
            flash(plan["fehler"], "fehler")
        elif anwenden:
            msg = "Abgleich mit Nextcloud durchgeführt."
            if plan.get("neu_konto"):
                msg += f" {plan['neu_konto']} App-Zugänge angelegt."
            flash(msg, "erfolg")

    buecher = []
    if konfig:
        try:
            buecher = carddav.adressbuecher(einst)
        except Exception as e:  # noqa: BLE001
            flash(f"Adressbücher konnten nicht geladen werden: {e}", "fehler")

    # Vorschau automatisch anzeigen, damit der passende Button immer sichtbar ist
    if plan is None and konfig:
        plan = nc_abgleich(einst, anwenden=False)
        if plan.get("fehler"):
            plan = None

    aktuell = (einst["ncc_adressbuch"] or "").strip() or "werkhaus-mitglieder"
    return render_template("mitglieder/nextcloud.html", einst=einst, plan=plan,
                           konfiguriert=konfig, buecher=buecher, aktuell=aktuell)


@bp.route("/export.csv")
def export_csv():
    conn = get_db()
    rows = conn.execute("SELECT * FROM mitglieder WHERE verein_id=? ORDER BY nachname, vorname",
                        (aktiver_verein_id(),)).fetchall()
    felder, werte_map = _felder_export(conn, aktiver_verein_id())
    conn.close()
    ausgabe = io.StringIO()
    if rows:
        writer = csv.writer(ausgabe, delimiter=";")
        writer.writerow(list(rows[0].keys()) + [f["name"] for f in felder])
        for r in rows:
            writer.writerow([r[k] for k in r.keys()]
                            + [werte_map.get(r["id"], {}).get(f["id"], "") for f in felder])
    return Response(
        "﻿" + ausgabe.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=mitglieder.csv"})


EXPORT_SPALTEN = [
    ("mitgliedsnummer", "Mitgliedsnr."), ("anrede", "Anrede"), ("titel", "Titel"),
    ("vorname", "Vorname"), ("nachname", "Nachname"), ("firma", "Firma"),
    ("strasse", "Straße"), ("plz", "PLZ"), ("ort", "Ort"), ("land", "Land"),
    ("email", "E-Mail"), ("telefon", "Telefon"), ("mobil", "Mobil"),
    ("geburtsdatum", "Geburtsdatum"), ("eintrittsdatum", "Eintritt"), ("austrittsdatum", "Austritt"),
    ("status", "Status"), ("beitragsgruppe", "Beitragsgruppe"), ("beitrag", "Beitrag (€)"),
    ("zahlungsweise", "Zahlungsweise"), ("zahlungsart", "Zahlungsart"),
    ("iban", "IBAN"), ("bic", "BIC"), ("kontoinhaber", "Kontoinhaber"),
    ("mandatsreferenz", "Mandatsreferenz"), ("mandatsdatum", "Mandatsdatum"),
    ("komm_genutzt", "Kommunikation genutzt"), ("komm_erlaubt", "Kommunikation erlaubt"),
    ("notizen", "Notizen"),
]


@bp.route("/export.xlsx")
def export_xlsx():
    conn = get_db()
    rows = conn.execute("SELECT * FROM mitglieder WHERE verein_id=? ORDER BY nachname, vorname",
                        (aktiver_verein_id(),)).fetchall()
    felder, werte_map = _felder_export(conn, aktiver_verein_id())
    conn.close()
    kopf = [bez for _, bez in EXPORT_SPALTEN] + [f["name"] for f in felder]
    zeilen = []
    for r in rows:
        zeile = []
        for feld, _ in EXPORT_SPALTEN:
            wert = r[feld]
            if feld == "beitrag":
                wert = float(wert or 0)
            zeile.append(wert)
        for f in felder:
            zeile.append(werte_map.get(r["id"], {}).get(f["id"], ""))
        zeilen.append(zeile)
    daten = xlsx.schreibe_xlsx(kopf, zeilen, blattname="Mitglieder")
    return Response(
        daten,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=mitglieder.xlsx"})


@bp.route("/import", methods=["GET", "POST"])
def import_csv():
    if request.method == "POST":
        vid = aktiver_verein_id()
        datei = request.files.get("datei")
        if not datei or datei.filename == "":
            flash("Bitte eine CSV-Datei auswählen.", "fehler")
            return redirect(url_for("mitglieder.import_csv"))
        inhalt = datei.read().decode("utf-8-sig", errors="replace")
        trenner = ";" if inhalt.count(";") >= inhalt.count(",") else ","
        leser = csv.DictReader(io.StringIO(inhalt), delimiter=trenner)
        conn = get_db()
        anzahl = 0
        for zeile in leser:
            z = {(k or "").strip().lower(): (v or "").strip() for k, v in zeile.items()}
            daten = {f: z.get(f, "") for f in FELDER}
            if not daten["vorname"] and not daten["nachname"] and not daten["firma"] and not daten["email"]:
                continue
            try:
                daten["beitrag"] = float(str(daten["beitrag"]).replace(",", ".")) if daten["beitrag"] else 0
            except ValueError:
                daten["beitrag"] = 0
            if not daten["status"]:
                daten["status"] = "aktiv"
            spalten = "verein_id, " + ", ".join(FELDER) + ", erstellt_am, geaendert_am, erstellt_von"
            platzhalter = ", ".join(["?"] * (len(FELDER) + 4))
            werte = [vid] + [daten[f] for f in FELDER] + [now_iso(), now_iso(), "CSV-Import"]
            conn.execute(f"INSERT INTO mitglieder ({spalten}) VALUES ({platzhalter})", werte)
            anzahl += 1
        conn.commit()
        conn.close()
        flash(f"{anzahl} Mitglieder importiert.", "erfolg")
        return redirect(url_for("mitglieder.liste"))
    return render_template("mitglieder/import.html", aktiv="mitglieder", felder=FELDER)



def _einst_wert(einst, feld, standard=""):
    """Einstellung lesen, ohne bei fehlender Spalte zu stolpern (ältere Datenbanken)."""
    try:
        return (einst[feld] if einst and feld in einst.keys() else "") or standard
    except (KeyError, IndexError):
        return standard


@bp.route("/antrag", methods=["GET", "POST"])
def antrag_public():
    """Öffentliches „Mitglied werden"-Formular (ohne Login)."""
    from ..db import get_einstellungen
    einst = get_einstellungen()
    if request.method == "POST":
        if (request.form.get("website") or "").strip():        # Honeypot
            abort(400)
        f = request.form
        if not (f.get("vorname") or "").strip() or not (f.get("nachname") or "").strip() \
                or not f.get("datenschutz_ok"):
            flash("Bitte Name angeben und die Datenschutzhinweise bestätigen.", "fehler")
            return redirect(url_for("mitglieder.antrag_public"))
    if request.method == "POST":
        conn = get_db()
        vid = aktiver_verein_id()
        conn.execute(
            "INSERT INTO beitrittsantraege (verein_id, anrede, vorname, nachname, geburtsdatum, "
            "strasse, plz, ort, email, telefon, mitgliedsart, iban, sepa_ok, bemerkung, "
            "organisation, beitragsart, kontoinhaber, bic, zahlungsweg, vertreter, satzung_ok, "
            "status, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'offen', ?)",
            (vid, f.get("anrede", ""), f.get("vorname", "").strip(), f.get("nachname", "").strip(),
             f.get("geburtsdatum", ""), f.get("strasse", "").strip(), f.get("plz", "").strip(),
             f.get("ort", "").strip(), f.get("email", "").strip(), f.get("telefon", "").strip(),
             f.get("mitgliedsart", ""), f.get("iban", "").replace(" ", "").upper(),
             1 if f.get("zahlungsweg") == "lastschrift" else 0, f.get("bemerkung", "").strip(),
             f.get("organisation", "").strip(), f.get("beitragsart", ""),
             f.get("kontoinhaber", "").strip(), f.get("bic", "").replace(" ", "").upper(),
             f.get("zahlungsweg", ""), f.get("vertreter", "").strip(),
             1 if f.get("satzung_ok") else 0, now_iso()))
        conn.commit()
        conn.close()
        stunden = _einst_wert(einst, "antwort_stunden", "48")
        # Vorstand benachrichtigen + Eingang bestätigen (best effort)
        from .. import mail as _mail
        try:
            if einst["email"]:
                _mail.einzelmail(
                    einst, einst["email"],
                    f"Neuer Beitrittsantrag: {f.get('vorname')} {f.get('nachname')}",
                    f"{f.get('vorname')} {f.get('nachname')} möchte Mitglied werden "
                    f"({f.get('mitgliedsart') or 'Mitgliedschaft'}, {f.get('beitragsart') or 'Beitrag offen'}).\n\n"
                    f"E-Mail: {f.get('email') or '–'}\nTelefon: {f.get('telefon') or '–'}\n"
                    f"Zahlungsweg: {f.get('zahlungsweg') or '–'}\n\n"
                    f"Zugesagt ist eine Rückmeldung innerhalb von {stunden} Stunden.\n"
                    "Zur Prüfung: Verwaltung → Mitglieder → Mitgliedschaft.")
            if (f.get("email") or "").strip():
                _mail.einzelmail(
                    einst, f.get("email").strip(),
                    f"Dein Aufnahmeantrag – {einst['vereinsname']}",
                    f"Hallo {f.get('vorname')},\n\n"
                    "dein Antrag ist bei uns angekommen – schön, dass du dabei sein möchtest!\n\n"
                    f"Wir melden uns innerhalb von {stunden} Stunden bei dir. "
                    "Bis dahin musst du nichts weiter tun.\n\n"
                    "Herzliche Grüße\n"
                    f"{einst['vereinsname']}")
        except Exception:  # noqa: BLE001
            pass
        return render_template("mitglieder/antrag_danke.html", einst=einst, stunden=stunden)
    return render_template("mitglieder/antrag.html", einst=einst,
                           antwort_stunden=_einst_wert(einst, "antwort_stunden", "48"),
                           angebote=[a.strip() for a in
                                     (_einst_wert(einst, "angebote", "") or "").split(",") if a.strip()])


@bp.route("/antraege")
def antraege():
    """Mitgliedschaft: Eintritt und Austritt an einem Ort.

    Vorher zeigte diese Seite nur offene Beitrittsanträge. Kündigungen versteckten
    sich auf der Detailseite des jeweiligen Mitglieds, und Ausgetretene
    verschwanden ganz – für Mitgliederversammlung und Kassenbericht braucht man
    beides aber.
    """
    from datetime import date as _date, timedelta as _td
    conn = get_db()
    vid = aktiver_verein_id()
    heute = _date.today()
    liste = conn.execute("SELECT * FROM beitrittsantraege WHERE verein_id=? AND status='offen' "
                         "ORDER BY erstellt_am", (vid,)).fetchall()

    # Angekündigte Austritte: gekündigt, aber noch Mitglied
    kuendigungen = []
    for m in conn.execute(
            "SELECT * FROM mitglieder WHERE verein_id=? AND IFNULL(kuendigung_am,'')!='' "
            "AND status!='ausgetreten' ORDER BY IFNULL(austrittsdatum, kuendigung_am)", (vid,)).fetchall():
        d = dict(m)
        ziel = (m["austrittsdatum"] or "").strip()
        try:
            d["tage"] = (_date.fromisoformat(ziel) - heute).days if ziel else None
        except ValueError:
            d["tage"] = None
        kuendigungen.append(d)

    grenze = (heute - _td(days=365)).isoformat()
    ausgetreten = conn.execute(
        "SELECT * FROM mitglieder WHERE verein_id=? AND status='ausgetreten' "
        "AND IFNULL(austrittsdatum,'') >= ? ORDER BY austrittsdatum DESC", (vid, grenze)).fetchall()

    jahr = str(heute.year)
    eintritte = conn.execute("SELECT COUNT(*) c FROM mitglieder WHERE verein_id=? "
                             "AND substr(IFNULL(eintrittsdatum,''),1,4)=?", (vid, jahr)).fetchone()["c"]
    austritte = conn.execute("SELECT COUNT(*) c FROM mitglieder WHERE verein_id=? "
                             "AND substr(IFNULL(austrittsdatum,''),1,4)=?", (vid, jahr)).fetchone()["c"]
    conn.close()

    einst = get_einstellungen()
    basis = (einst["app_url"] or "").strip().rstrip("/") if einst and einst["app_url"] else ""
    antrag_url = (basis + url_for("mitglieder.antrag_public")) if basis \
        else url_for("mitglieder.antrag_public", _external=True)
    qr = None
    try:
        import segno, io as _io, base64
        puffer = _io.BytesIO()
        segno.make(antrag_url, error="m").save(puffer, kind="png", scale=5, border=2)
        qr = "data:image/png;base64," + base64.b64encode(puffer.getvalue()).decode()
    except Exception:  # noqa: BLE001 – ohne QR-Code funktioniert die Seite weiterhin
        qr = None

    return render_template("mitglieder/antraege.html", aktiv="mitgliedschaft", antraege=liste,
                           kuendigungen=kuendigungen, ausgetreten=ausgetreten,
                           eintritte=eintritte, austritte=austritte, jahr=jahr,
                           antrag_url=antrag_url, qr=qr, heute=heute.isoformat())


@bp.route("/antraege/<int:aid>/entscheiden", methods=["POST"])
def antrag_entscheiden(aid):
    conn = get_db()
    vid = aktiver_verein_id()
    a = conn.execute("SELECT * FROM beitrittsantraege WHERE id=? AND verein_id=?", (aid, vid)).fetchone()
    if not a:
        conn.close()
        abort(404)
    if request.form.get("entscheidung") == "annehmen":
        cur = conn.execute(
            "INSERT INTO mitglieder (verein_id, anrede, vorname, nachname, geburtsdatum, strasse, plz, "
            "ort, email, telefon, eintrittsdatum, status, zahlungsart, iban, bic, kontoinhaber, "
            "mandat_vorhanden, erstellt_von) VALUES (?,?,?,?,?,?,?,?,?,?,?,'aktiv',?,?,?,?,?,?)",
            (vid, a["anrede"], a["vorname"], a["nachname"], a["geburtsdatum"], a["strasse"], a["plz"],
             a["ort"], a["email"], a["telefon"], now_iso()[:10],
             "SEPA-Lastschrift" if (a["iban"] and a["sepa_ok"]) else "",
             a["iban"] or "", a["bic"] or "", a["kontoinhaber"] or "",
             1 if (a["iban"] and a["sepa_ok"]) else 0, wer()))
        conn.execute("UPDATE beitrittsantraege SET status='angenommen' WHERE id=?", (aid,))
        mid = cur.lastrowid
        # Mandatsreferenz und Unterschriftsdatum sofort vergeben – als Datum gilt
        # der Tag, an dem der Antrag gestellt wurde. Ohne beides wäre das Mandat
        # unvollständig und die Person fiele beim Einzug still heraus.
        if a["iban"] and a["sepa_ok"]:
            from .. import mandate
            mandate.sicherstellen(conn, vid, mid, unterschrift=(a["erstellt_am"] or "")[:10])
        conn.commit()
        conn.close()
        # Gestaltete Willkommensmail mit App-Kurzanleitung (best effort)
        willkommen_ok = False
        if (a["email"] or "").strip():
            try:
                from .. import mail as _mail, mailhtml
                einst = get_einstellungen()
                host = request.host_url.rstrip("/")
                betreff, html = mailhtml.willkommen_mitglied(einst, host, a["vorname"] or "du")
                willkommen_ok, _f = _mail.einzelmail(einst, a["email"], betreff,
                                                     _mail._html_zu_text(html), html=html)
            except Exception:  # noqa: BLE001
                pass
        flash(f"🎉 {a['vorname']} {a['nachname']} ist jetzt Mitglied – bitte Beitragsgruppe und "
              "Gruppen im Mitgliedsprofil ergänzen."
              + (" Die Willkommensmail mit App-Anleitung ist unterwegs. ✉️" if willkommen_ok else ""),
              "erfolg")
        return redirect(url_for("mitglieder.detail", mid=mid))
    conn.execute("UPDATE beitrittsantraege SET status='abgelehnt' WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    flash("Antrag abgelehnt.", "info")
    return redirect(url_for("mitglieder.antraege"))


@bp.route("/<int:mid>/zahlungsdaten", methods=["GET", "POST"])
def zahlungsdaten(mid):
    """Nur die Bank- und Mandatsangaben – für die Kasse.

    Die Schatzmeisterin erzeugt den Einzug und sieht, bei wem etwas fehlt. Ohne
    diese Seite bräuchte sie für jede Korrektur jemanden mit vollem Zugriff auf
    die Mitgliederakte. Geburtsdaten, Notizen und Austritte bleiben verschlossen.
    """
    from .. import mandate, sepa
    conn = get_db()
    vid = aktiver_verein_id()
    m = _hole_mitglied(conn, mid)
    if not m:
        conn.close()
        flash("Mitglied nicht gefunden.", "fehler")
        return redirect(url_for("mitglieder.liste"))

    if request.method == "POST":
        iban = (request.form.get("iban") or "").replace(" ", "").upper()
        if iban and not sepa.iban_gueltig(iban):
            conn.close()
            flash("Diese IBAN stimmt nicht – bitte noch einmal prüfen. "
                  "(Die Prüfziffer passt nicht zum Rest.)", "fehler")
            return redirect(url_for("mitglieder.zahlungsdaten", mid=mid))
        gebunden = mandate.in_benutzung(conn, vid, mid)
        felder = {
            "zahlungsart": (request.form.get("zahlungsart") or "").strip(),
            "iban": iban,
            "bic": (request.form.get("bic") or "").replace(" ", "").upper(),
            "kontoinhaber": (request.form.get("kontoinhaber") or "").strip(),
            "mandatsdatum": (request.form.get("mandatsdatum") or "").strip(),
            "mandat_vorhanden": 1 if request.form.get("mandat_vorhanden") else 0,
        }
        # Eine bereits benutzte Referenz bleibt, wie sie ist – sie steht so bei der Bank.
        if not gebunden:
            felder["mandatsreferenz"] = (request.form.get("mandatsreferenz") or "").strip()
        conn.execute("UPDATE mitglieder SET " + ", ".join(f"{k}=?" for k in felder)
                     + ", geaendert_am=? WHERE id=? AND verein_id=?",
                     list(felder.values()) + [now_iso(), mid, vid])
        datei = _mandat_speichern(request.files.get("mandat_datei"))
        if datei:
            conn.execute("UPDATE mitglieder SET mandat_dateiname=? WHERE id=?", (datei, mid))
        ergaenzt = _mandat_vervollstaendigen(conn, vid, mid)
        conn.commit()
        conn.close()
        if ergaenzt.get("referenz"):
            flash(f"Gespeichert. Die Mandatsreferenz {ergaenzt['referenz']} wurde vergeben.", "erfolg")
        else:
            flash("Zahlungsangaben gespeichert.", "erfolg")
        return redirect(url_for("mitglieder.zahlungsdaten", mid=mid))

    gebunden = mandate.in_benutzung(conn, vid, mid)
    verfaellt = mandate.verfaellt_am(m["mandatsdatum"])
    conn.close()
    return render_template("mitglieder/zahlungsdaten.html", aktiv="mitglieder", m=m,
                           zahlungsarten=ZAHLUNGSARTEN, gebunden=gebunden, verfaellt=verfaellt)


@bp.route("/mandate-nachtragen", methods=["POST"])
def mandate_nachtragen():
    """Vergibt für alle Bestandsmitglieder die fehlenden Mandatsreferenzen."""
    from .. import mandate
    conn = get_db()
    vid = aktiver_verein_id()
    ergaenzt = mandate.nachruesten(conn, vid)
    conn.commit()
    conn.close()
    if ergaenzt:
        flash(f"{len(ergaenzt)} Mandat(e) vervollständigt – Referenz und Datum sind jetzt "
              "hinterlegt. Bitte teile den Betroffenen die Mandatsreferenz mit, wenn sie "
              "noch keine kennen.", "erfolg")
    else:
        flash("Es war nichts nachzutragen – alle Mandate sind vollständig.", "info")
    return redirect(request.referrer or url_for("mitglieder.liste"))
