"""
Aufgaben: einmalige oder wiederkehrende Aufgaben des Vorstands, an einzelne
Mitglieder oder an alle Mitglieder delegierbar, mit Vorab-Benachrichtigung
(Standard 1 Stunde) per Push. Wiederkehrende Aufgaben legen beim Erledigen
automatisch die nächste Aufgabe an – die erledigte bleibt als Verlauf stehen.
Die Benachrichtigung selbst versendet der stündliche Cron (app/erinnerungen.py).
"""
import calendar as _cal
from datetime import date, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response, abort, session
from ..db import get_db, get_db_fuer, get_einstellungen, aktiver_verein_id, now_iso
from ..funktionen import wer
from .. import erwaehnungen, ics_export
from .. import db as _db

bp = Blueprint("aufgaben", __name__, url_prefix="/aufgaben")


@bp.route("/export")
def export():
    vid = aktiver_verein_id()
    token = _db.feed_token(vid)
    host = request.host
    schema = "http" if host.startswith(("localhost", "127.")) else "https"
    feed = f"{schema}://{host}" + url_for("aufgaben.abo", token=token)
    return render_template("aufgaben/export.html", aktiv="aufgaben", feed=feed,
                           webcal=feed.replace("https://", "webcal://").replace("http://", "webcal://"))


@bp.route("/export.ics")
def download_ics():
    vid = aktiver_verein_id()
    conn = get_db()
    rows = conn.execute("SELECT id, titel, beschreibung, faellig_am, faellig_zeit, erledigt FROM aufgaben "
                        "WHERE verein_id=? AND erledigt=0", (vid,)).fetchall()
    einst = get_einstellungen()
    conn.close()
    text = ics_export.aufgaben_ics([dict(r) for r in rows], name=f"{einst['vereinsname']} – Aufgaben", vid=vid)
    return Response(text, mimetype="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=aufgaben.ics"})


@bp.route("/abo/<token>.ics", endpoint="abo")
def abo(token):
    vid = _db.verein_per_feed_token(token)
    if not vid:
        abort(404)
    conn = get_db_fuer(vid)
    rows = conn.execute("SELECT id, titel, beschreibung, faellig_am, faellig_zeit, erledigt FROM aufgaben "
                        "WHERE verein_id=? AND erledigt=0", (vid,)).fetchall()
    name = conn.execute("SELECT vereinsname FROM vereine WHERE id=?", (vid,)).fetchone()["vereinsname"]
    conn.close()
    text = ics_export.aufgaben_ics([dict(r) for r in rows], name=f"{name} – Aufgaben", vid=vid)
    return Response(text, mimetype="text/calendar; charset=utf-8")

WIEDERHOLUNGEN = [("einmalig", "Einmalig"), ("taeglich", "Täglich"), ("woechentlich", "Wöchentlich"),
                  ("monatlich", "Monatlich"), ("jaehrlich", "Jährlich")]
VORLAUF_OPTIONEN = [(0, "keine"), (15, "15 Minuten"), (30, "30 Minuten"), (60, "1 Stunde"),
                    (180, "3 Stunden"), (1440, "1 Tag"), (2880, "2 Tage")]


def naechste_faelligkeit(datum_iso, wiederholung):
    """Nächstes Fälligkeitsdatum einer wiederkehrenden Aufgabe (ISO-String)."""
    try:
        d = date.fromisoformat((datum_iso or "")[:10])
    except (ValueError, TypeError):
        return datum_iso
    if wiederholung == "taeglich":
        d = d + timedelta(days=1)
    elif wiederholung == "woechentlich":
        d = d + timedelta(weeks=1)
    elif wiederholung == "monatlich":
        m = d.month + 1
        y = d.year + (1 if m > 12 else 0)
        m = m - 12 if m > 12 else m
        d = date(y, m, min(d.day, _cal.monthrange(y, m)[1]))
    elif wiederholung == "jaehrlich":
        try:
            d = d.replace(year=d.year + 1)
        except ValueError:
            d = date(d.year + 1, 3, 1)   # 29.02. → 01.03.
    return d.isoformat()


def _zuweisung():
    """Zuweisungs-Feld des Formulars auswerten.

    Liefert (mitglied_id, zielgruppe): eine Mitglieds-Nummer, '' (= Vorstand)
    oder die Zielgruppe 'mitglieder' (= alle Mitglieder, Wert "alle" im Select).
    """
    roh = (request.form.get("mitglied_id") or "").strip()
    if roh == "alle":
        return None, "mitglieder"
    return (int(roh) if roh.isdigit() else None), ""


def _zielgruppe(a):
    return a["zielgruppe"] if "zielgruppe" in a.keys() else ""


def wiederholung_fortschreiben(conn, a):
    """Wiederkehrende Aufgabe abschließen: die erledigte Zeile bleibt als
    Verlauf stehen, für den nächsten Termin legt die Verwaltung automatisch
    eine neue Aufgabe an. Gibt das neue Fälligkeitsdatum (ISO) zurück."""
    neu_datum = naechste_faelligkeit(a["faellig_am"] or date.today().isoformat(), a["wiederholung"])
    jetzt = now_iso()
    conn.execute("UPDATE aufgaben SET erledigt=1, erledigt_am=? WHERE id=?", (jetzt, a["id"]))
    conn.execute(
        "INSERT INTO aufgaben (verein_id, titel, beschreibung, faellig_am, faellig_zeit, wiederholung, "
        "vorlauf_min, mitglied_id, zielgruppe, erledigt, benachrichtigt, erstellt_am, erstellt_von) VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?)",
        (a["verein_id"], a["titel"], a["beschreibung"], neu_datum, a["faellig_zeit"],
         a["wiederholung"], a["vorlauf_min"], a["mitglied_id"], _zielgruppe(a), jetzt, ""))
    return neu_datum


@bp.route("/")
def index():
    conn = get_db()
    vid = aktiver_verein_id()
    offen = conn.execute(
        "SELECT a.*, m.vorname, m.nachname, m.abwesend, m.abwesend_von, m.abwesend_bis, m.abwesend_notiz "
        "FROM aufgaben a LEFT JOIN mitglieder m ON m.id=a.mitglied_id "
        "WHERE a.verein_id=? AND a.erledigt=0 ORDER BY a.faellig_am, a.faellig_zeit", (vid,)).fetchall()
    erledigt = conn.execute(
        "SELECT a.*, m.vorname, m.nachname FROM aufgaben a LEFT JOIN mitglieder m ON m.id=a.mitglied_id "
        "WHERE a.verein_id=? AND a.erledigt=1 ORDER BY a.erledigt_am DESC LIMIT 20", (vid,)).fetchall()
    mitglieder = conn.execute(
        "SELECT id, vorname, nachname, abwesend, abwesend_von, abwesend_bis FROM mitglieder "
        "WHERE verein_id=? AND status IN ('aktiv','Ehrenmitglied') ORDER BY nachname, vorname", (vid,)).fetchall()
    mention_users = erwaehnungen.mitglieder_liste(conn, vid)
    conn.close()
    return render_template("aufgaben/index.html", aktiv="aufgaben", offen=offen, erledigt=erledigt,
                           mitglieder=mitglieder, wiederholungen=WIEDERHOLUNGEN,
                           vorlauf_optionen=VORLAUF_OPTIONEN, heute_iso=date.today().isoformat(),
                           mention_users=mention_users)


@bp.route("/neu", methods=["POST"])
def neu():
    conn = get_db()
    vid = aktiver_verein_id()
    titel = request.form.get("titel", "").strip()
    if not titel:
        conn.close()
        flash("Bitte einen Titel für die Aufgabe angeben.", "fehler")
        return redirect(url_for("aufgaben.index"))
    wdh = request.form.get("wiederholung", "einmalig")
    if wdh not in [k for k, _ in WIEDERHOLUNGEN]:
        wdh = "einmalig"
    vorlauf = request.form.get("vorlauf_min", type=int)
    vorlauf = 60 if vorlauf is None else max(0, vorlauf)
    mid, zielgruppe = _zuweisung()
    conn.execute(
        "INSERT INTO aufgaben (verein_id, titel, beschreibung, faellig_am, faellig_zeit, wiederholung, "
        "vorlauf_min, mitglied_id, zielgruppe, erledigt, benachrichtigt, erstellt_am, erstellt_von) VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?)",
        (vid, titel, request.form.get("beschreibung", "").strip(),
         request.form.get("faellig_am", "") or date.today().isoformat(),
         request.form.get("faellig_zeit", ""), wdh, vorlauf, mid, zielgruppe, now_iso(), wer()))
    conn.commit()
    try:
        erwaehnungen.pushe_erwaehnungen(conn, vid, request.form.get("beschreibung", ""),
                                        "✅ Aufgabe: " + titel, url_for("aufgaben.index"))
    except Exception:  # noqa: BLE001
        pass
    conn.close()
    flash("Aufgabe angelegt.", "erfolg")
    return redirect(url_for("aufgaben.index"))


@bp.route("/<int:aid>/bearbeiten", methods=["POST"])
def bearbeiten(aid):
    """Bestehende Aufgabe ändern (Titel, Beschreibung, Fälligkeit, Wiederholung, Zuweisung)."""
    conn = get_db()
    vid = aktiver_verein_id()
    a = conn.execute("SELECT * FROM aufgaben WHERE id=? AND verein_id=?", (aid, vid)).fetchone()
    if not a:
        conn.close()
        flash("Aufgabe nicht gefunden.", "fehler")
        return redirect(url_for("aufgaben.index"))
    titel = request.form.get("titel", "").strip()
    if not titel:
        conn.close()
        flash("Bitte einen Titel für die Aufgabe angeben.", "fehler")
        return redirect(url_for("aufgaben.index"))
    wdh = request.form.get("wiederholung", "einmalig")
    if wdh not in [k for k, _ in WIEDERHOLUNGEN]:
        wdh = "einmalig"
    vorlauf = request.form.get("vorlauf_min", type=int)
    vorlauf = 60 if vorlauf is None else max(0, vorlauf)
    faellig_am = request.form.get("faellig_am", "") or a["faellig_am"]
    faellig_zeit = request.form.get("faellig_zeit", "")
    # Bei geänderter Fälligkeit darf die Erinnerung erneut ausgelöst werden
    benachrichtigt = a["benachrichtigt"]
    if faellig_am != a["faellig_am"] or faellig_zeit != (a["faellig_zeit"] or ""):
        benachrichtigt = 0
    mid, zielgruppe = _zuweisung()
    conn.execute(
        "UPDATE aufgaben SET titel=?, beschreibung=?, faellig_am=?, faellig_zeit=?, wiederholung=?, "
        "vorlauf_min=?, mitglied_id=?, zielgruppe=?, benachrichtigt=? WHERE id=?",
        (titel, request.form.get("beschreibung", "").strip(), faellig_am, faellig_zeit, wdh,
         vorlauf, mid, zielgruppe, benachrichtigt, aid))
    conn.commit()
    try:
        erwaehnungen.pushe_erwaehnungen(conn, vid, request.form.get("beschreibung", ""),
                                        "✅ Aufgabe: " + titel, url_for("aufgaben.index"))
    except Exception:  # noqa: BLE001
        pass
    conn.close()
    flash("Aufgabe aktualisiert.", "erfolg")
    return redirect(url_for("aufgaben.index"))


@bp.route("/<int:aid>/erledigt", methods=["POST"])
def erledigt(aid):
    """Als erledigt markieren. Bei wiederkehrenden Aufgaben entsteht automatisch
    die nächste Aufgabe; die erledigte bleibt als Verlauf stehen."""
    conn = get_db()
    vid = aktiver_verein_id()
    a = conn.execute("SELECT * FROM aufgaben WHERE id=? AND verein_id=?", (aid, vid)).fetchone()
    if a:
        if a["wiederholung"] and a["wiederholung"] != "einmalig" and not a["erledigt"]:
            neu_datum = wiederholung_fortschreiben(conn, a)
            try:
                schoen = date.fromisoformat(neu_datum).strftime("%d.%m.%Y")
            except ValueError:
                schoen = neu_datum
            flash("Erledigt – die nächste Aufgabe steht automatisch für den " + schoen + " in der Liste.", "erfolg")
            session["aufgabe_feier"] = 1
        else:
            neu = 0 if a["erledigt"] else 1
            conn.execute("UPDATE aufgaben SET erledigt=?, erledigt_am=? WHERE id=?",
                         (neu, now_iso() if neu else None, aid))
            if neu:
                session["aufgabe_feier"] = 1
        conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("aufgaben.index"))


@bp.route("/<int:aid>/kopieren", methods=["POST"])
def kopieren(aid):
    """Aufgabe duplizieren – als Vorlage für ähnliche Aufgaben, auch aus dem Verlauf."""
    conn = get_db()
    vid = aktiver_verein_id()
    a = conn.execute("SELECT * FROM aufgaben WHERE id=? AND verein_id=?", (aid, vid)).fetchone()
    if not a:
        conn.close()
        flash("Aufgabe nicht gefunden.", "fehler")
        return redirect(url_for("aufgaben.index"))
    conn.execute(
        "INSERT INTO aufgaben (verein_id, titel, beschreibung, faellig_am, faellig_zeit, wiederholung, "
        "vorlauf_min, mitglied_id, zielgruppe, erledigt, benachrichtigt, erstellt_am, erstellt_von) VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?)",
        (vid, a["titel"] + " (Kopie)", a["beschreibung"], a["faellig_am"], a["faellig_zeit"],
         a["wiederholung"], a["vorlauf_min"], a["mitglied_id"], _zielgruppe(a), now_iso(), wer()))
    conn.commit()
    conn.close()
    flash("Aufgabe kopiert – die Kopie kann jetzt über ✏️ angepasst werden.", "erfolg")
    return redirect(url_for("aufgaben.index"))


@bp.route("/<int:aid>/loeschen", methods=["POST"])
def loeschen(aid):
    conn = get_db()
    conn.execute("DELETE FROM aufgaben WHERE id=? AND verein_id=?", (aid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Aufgabe gelöscht.", "erfolg")
    return redirect(url_for("aufgaben.index"))
