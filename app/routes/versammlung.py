"""Mitgliederversammlung: Einladung mit Frist, Anwesenheit, Beschlüsse, Protokoll.

Deckt den gesamten rechtlich relevanten Ablauf ab:
  1. Versammlung anlegen (Datum, Ort, Tagesordnung, Einladungsfrist)
  2. Einladung an alle Mitglieder senden – die App prüft die Satzungsfrist
  3. Anwesenheit abhaken (inkl. Gäste und Vollmachten) → Beschlussfähigkeit
  4. Beschlüsse und Wahlen mit Stimmen erfassen
  5. Protokoll schreiben und als PDF ablegen
"""
from datetime import date, datetime, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort, send_file)
from flask_login import current_user

from ..db import get_db, get_einstellungen, now_iso, aktiver_verein_id
from .. import mail

bp = Blueprint("versammlung", __name__, url_prefix="/versammlungen")

ARTEN = [("mv", "Mitgliederversammlung"), ("ausserordentlich", "Außerordentliche MV"),
         ("vorstand", "Vorstandssitzung")]
ARTEN_NAMEN = dict(ARTEN)
BESCHLUSS_ARTEN = [("beschluss", "Beschluss"), ("wahl", "Wahl"), ("satzung", "Satzungsänderung")]


def _v(conn, vid_verein, vsid):
    v = conn.execute("SELECT * FROM versammlungen WHERE id=? AND verein_id=?",
                     (vsid, vid_verein)).fetchone()
    if not v:
        conn.close()
        abort(404)
    return v


def _stimmberechtigte(conn, vid):
    """Aktive Mitglieder = potenziell stimmberechtigt (Grundlage der Beschlussfähigkeit)."""
    return conn.execute("SELECT COUNT(*) c FROM mitglieder WHERE verein_id=? AND status='aktiv'",
                        (vid,)).fetchone()["c"]


@bp.route("/")
def index():
    conn = get_db()
    vid = aktiver_verein_id()
    heute = date.today().isoformat()
    liste = conn.execute(
        "SELECT v.*, (SELECT COUNT(*) FROM versammlung_anwesend a WHERE a.versammlung_id=v.id) anwesend, "
        "(SELECT COUNT(*) FROM versammlung_beschluesse b WHERE b.versammlung_id=v.id) beschluesse "
        "FROM versammlungen v WHERE v.verein_id=? "
        "ORDER BY (v.datum < ?), CASE WHEN v.datum >= ? THEN v.datum ELSE '' END, v.datum DESC",
        (vid, heute, heute)).fetchall()
    conn.close()
    return render_template("versammlung/index.html", aktiv="versammlungen", liste=liste,
                           arten=ARTEN_NAMEN, heute=heute)


@bp.route("/neu", methods=["GET", "POST"])
@bp.route("/<int:vsid>/bearbeiten", methods=["GET", "POST"], endpoint="bearbeiten")
def neu(vsid=None):
    conn = get_db()
    vid = aktiver_verein_id()
    v = _v(conn, vid, vsid) if vsid else None
    if request.method == "POST":
        f = request.form
        daten = (f.get("titel", "").strip() or "Mitgliederversammlung",
                 f.get("art", "mv"), f.get("datum", ""), f.get("uhrzeit", ""),
                 f.get("ort", "").strip(), f.get("tagesordnung", "").strip(),
                 f.get("frist_tage", type=int) or 14, f.get("leitung", "").strip(),
                 f.get("protokoll_fuehrung", "").strip())
        if v is None:
            cur = conn.execute(
                "INSERT INTO versammlungen (verein_id, titel, art, datum, uhrzeit, ort, tagesordnung, "
                "frist_tage, leitung, protokoll_fuehrung, erstellt_am) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (vid, *daten, now_iso()))
            vsid = cur.lastrowid
        else:
            conn.execute("UPDATE versammlungen SET titel=?, art=?, datum=?, uhrzeit=?, ort=?, "
                         "tagesordnung=?, frist_tage=?, leitung=?, protokoll_fuehrung=? "
                         "WHERE id=? AND verein_id=?", (*daten, vsid, vid))
        conn.commit()
        conn.close()
        flash("Versammlung gespeichert. 🗳️", "erfolg")
        return redirect(url_for("versammlung.detail", vsid=vsid))
    einst = get_einstellungen()
    conn.close()
    return render_template("versammlung/formular.html", aktiv="versammlungen", v=v, arten=ARTEN,
                           heute=date.today().isoformat(), einst=einst)


@bp.route("/<int:vsid>")
def detail(vsid):
    conn = get_db()
    vid = aktiver_verein_id()
    v = _v(conn, vid, vsid)
    anwesend = conn.execute(
        "SELECT a.*, m.vorname, m.nachname FROM versammlung_anwesend a "
        "LEFT JOIN mitglieder m ON m.id=a.mitglied_id WHERE a.versammlung_id=? "
        "ORDER BY a.gast, m.nachname COLLATE NOCASE, a.name", (vsid,)).fetchall()
    beschluesse = conn.execute(
        "SELECT * FROM versammlung_beschluesse WHERE versammlung_id=? ORDER BY nummer, id", (vsid,)).fetchall()
    mitglieder = conn.execute(
        "SELECT id, vorname, nachname FROM mitglieder WHERE verein_id=? AND status='aktiv' "
        "ORDER BY nachname COLLATE NOCASE, vorname", (vid,)).fetchall()
    conn.close()
    berechtigt = v["stimmberechtigt"] or 0
    if not berechtigt:
        conn2 = get_db()
        berechtigt = _stimmberechtigte(conn2, vid)
        conn2.close()
    # Anwesende Mitglieder (keine Gäste) + Vollmachten = Stimmen
    mit_stimme = [a for a in anwesend if not a["gast"]]
    vollmachten = sum(1 for a in anwesend if a["vollmacht_von"])
    stimmen = len(mit_stimme)
    quote = round(stimmen / berechtigt * 100) if berechtigt else 0
    # Einladungsfrist prüfen
    frist_ok, frist_text = None, ""
    if v["datum"]:
        frist_datum = (date.fromisoformat(v["datum"]) - timedelta(days=v["frist_tage"] or 14))
        if v["einladung_am"]:
            frist_ok = v["einladung_am"] <= frist_datum.isoformat()
            frist_text = (f"Eingeladen am {v['einladung_am'][8:10]}.{v['einladung_am'][5:7]}."
                          f"{v['einladung_am'][:4]} – Frist war der "
                          f"{frist_datum.strftime('%d.%m.%Y')}.")
        else:
            frist_text = f"Einladung muss bis zum {frist_datum.strftime('%d.%m.%Y')} raus sein."
    # Persönlicher Absender für die Einladungs-Schaltfläche
    mein_profil, meine_wahl = None, "verein"
    if getattr(current_user, "is_authenticated", False):
        mein_profil = mail.profil_laden(int(current_user.id))
        try:
            from ..db import get_system_db
            sys_conn = get_system_db()
            z = sys_conn.execute("SELECT absender_wahl FROM benutzer WHERE id=?",
                                 (int(current_user.id),)).fetchone()
            sys_conn.close()
            if z and z["absender_wahl"] in ("verein", "selbst"):
                meine_wahl = z["absender_wahl"]
        except Exception:  # noqa: BLE001
            pass
    return render_template("versammlung/detail.html", aktiv="versammlungen", v=v, anwesend=anwesend,
                           beschluesse=beschluesse, mitglieder=mitglieder, berechtigt=berechtigt,
                           stimmen=stimmen, vollmachten=vollmachten, quote=quote,
                           frist_ok=frist_ok, frist_text=frist_text, arten=ARTEN_NAMEN,
                           mein_profil=mein_profil, meine_wahl=meine_wahl,
                           beschluss_arten=BESCHLUSS_ARTEN, heute=date.today().isoformat())


@bp.route("/<int:vsid>/einladen", methods=["POST"])
def einladen(vsid):
    """Einladung per E-Mail an alle aktiven Mitglieder – mit Fristprüfung."""
    conn = get_db()
    vid = aktiver_verein_id()
    v = _v(conn, vid, vsid)
    einst = get_einstellungen()
    if not (einst["smtp_host"] and (einst["smtp_absender_email"] or einst["smtp_user"])):
        conn.close()
        flash("Bitte zuerst den E-Mail-Versand in den Einstellungen einrichten.", "fehler")
        return redirect(url_for("versammlung.detail", vsid=vsid))
    # Absender: Verein oder – wenn angehakt – das persönliche Profil
    profil = None
    if request.form.get("absender_selbst") and getattr(current_user, "is_authenticated", False):
        profil = mail.profil_laden(int(current_user.id))
        if profil is None:
            conn.close()
            flash("Kein persönlicher Absender hinterlegt – bitte zuerst unter "
                  "Einstellungen → Mein Absender eintragen.", "fehler")
            return redirect(url_for("versammlung.detail", vsid=vsid))

    empfaenger = conn.execute(
        "SELECT id, vorname, nachname, email FROM mitglieder WHERE verein_id=? AND status='aktiv' "
        "AND email!=''", (vid,)).fetchall()
    ohne_mail = conn.execute(
        "SELECT COUNT(*) c FROM mitglieder WHERE verein_id=? AND status='aktiv' AND email=''",
        (vid,)).fetchone()["c"]
    d = v["datum"]
    datum_de = f"{d[8:10]}.{d[5:7]}.{d[:4]}" if d else "(Datum offen)"
    betreff = f"Einladung: {v['titel']} am {datum_de}"
    n = 0
    for m in empfaenger:
        text = (f"Hallo {m['vorname']},\n\n"
                f"hiermit laden wir dich herzlich zur {ARTEN_NAMEN.get(v['art'], 'Versammlung')} "
                f"von {einst['vereinsname']} ein.\n\n"
                f"📅 {datum_de}{(', ' + v['uhrzeit'] + ' Uhr') if v['uhrzeit'] else ''}\n"
                f"📍 {v['ort'] or 'Ort wird noch bekannt gegeben'}\n\n"
                f"Tagesordnung:\n{v['tagesordnung'] or '(wird noch bekannt gegeben)'}\n\n"
                "Anträge zur Tagesordnung richtest du bitte rechtzeitig an den Vorstand.\n\n"
                "Deine Stimme zählt – wir freuen uns auf dich!\n\n"
                f"Freundliche Grüße\n{einst['vereinsname']}")
        try:
            ok, _f = mail.einzelmail(einst, m["email"], betreff, text, profil=profil)
            n += 1 if ok else 0
        except Exception:  # noqa: BLE001
            pass
    conn.execute("UPDATE versammlungen SET einladung_am=?, stimmberechtigt=? WHERE id=?",
                 (now_iso()[:10], _stimmberechtigte(conn, vid), vsid))
    conn.commit()
    conn.close()
    msg = f"Einladung an {n} Mitglied(er) verschickt. ✉️"
    if ohne_mail:
        msg += f" {ohne_mail} Mitglied(er) ohne E-Mail – bitte per Post einladen (Serienbrief)."
    flash(msg, "erfolg")
    return redirect(url_for("versammlung.detail", vsid=vsid))


@bp.route("/<int:vsid>/anwesenheit", methods=["POST"])
def anwesenheit(vsid):
    conn = get_db()
    vid = aktiver_verein_id()
    _v(conn, vid, vsid)
    aktion = request.form.get("aktion")
    if aktion == "entfernen":
        conn.execute("DELETE FROM versammlung_anwesend WHERE id=? AND versammlung_id=?",
                     (request.form.get("id", type=int), vsid))
    elif aktion == "gast":
        name = (request.form.get("name") or "").strip()
        if name:
            conn.execute("INSERT INTO versammlung_anwesend (versammlung_id, name, gast, erfasst_am) "
                         "VALUES (?,?,1,?)", (vsid, name, now_iso()))
    else:
        mid = request.form.get("mitglied_id", type=int)
        if mid and not conn.execute("SELECT 1 FROM versammlung_anwesend WHERE versammlung_id=? "
                                    "AND mitglied_id=?", (vsid, mid)).fetchone():
            conn.execute("INSERT INTO versammlung_anwesend (versammlung_id, mitglied_id, "
                         "vollmacht_von, erfasst_am) VALUES (?,?,?,?)",
                         (vsid, mid, request.form.get("vollmacht_von", type=int) or None, now_iso()))
    conn.commit()
    conn.close()
    return redirect(url_for("versammlung.detail", vsid=vsid) + "#anwesenheit")


@bp.route("/<int:vsid>/beschluss", methods=["POST"])
def beschluss(vsid):
    conn = get_db()
    vid = aktiver_verein_id()
    _v(conn, vid, vsid)
    if request.form.get("aktion") == "loeschen":
        conn.execute("DELETE FROM versammlung_beschluesse WHERE id=? AND versammlung_id=?",
                     (request.form.get("id", type=int), vsid))
    else:
        ja = request.form.get("ja", type=int) or 0
        nein = request.form.get("nein", type=int) or 0
        enth = request.form.get("enthaltung", type=int) or 0
        ergebnis = "angenommen" if ja > nein else ("abgelehnt" if nein > ja else "Stimmengleichheit")
        nr = conn.execute("SELECT COALESCE(MAX(nummer),0)+1 n FROM versammlung_beschluesse "
                          "WHERE versammlung_id=?", (vsid,)).fetchone()["n"]
        conn.execute(
            "INSERT INTO versammlung_beschluesse (versammlung_id, nummer, art, titel, text, ja, nein, "
            "enthaltung, ergebnis, notiz) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (vsid, nr, request.form.get("art", "beschluss"),
             (request.form.get("titel") or "").strip(), (request.form.get("text") or "").strip(),
             ja, nein, enth, ergebnis, (request.form.get("notiz") or "").strip()))
    conn.commit()
    conn.close()
    return redirect(url_for("versammlung.detail", vsid=vsid) + "#beschluesse")


@bp.route("/<int:vsid>/protokoll", methods=["POST"])
def protokoll(vsid):
    conn = get_db()
    vid = aktiver_verein_id()
    _v(conn, vid, vsid)
    conn.execute("UPDATE versammlungen SET protokoll=?, status=? WHERE id=? AND verein_id=?",
                 ((request.form.get("protokoll") or "").strip(),
                  request.form.get("status", "geplant"), vsid, vid))
    conn.commit()
    conn.close()
    flash("Protokoll gespeichert.", "erfolg")
    return redirect(url_for("versammlung.detail", vsid=vsid) + "#protokoll")


@bp.route("/<int:vsid>/druck")
def druck(vsid):
    """Druckansicht: Einladung, Anwesenheitsliste oder Protokoll."""
    conn = get_db()
    vid = aktiver_verein_id()
    v = _v(conn, vid, vsid)
    was = request.args.get("was", "protokoll")
    anwesend = conn.execute(
        "SELECT a.*, m.vorname, m.nachname FROM versammlung_anwesend a "
        "LEFT JOIN mitglieder m ON m.id=a.mitglied_id WHERE a.versammlung_id=? "
        "ORDER BY a.gast, m.nachname COLLATE NOCASE, a.name", (vsid,)).fetchall()
    beschluesse = conn.execute("SELECT * FROM versammlung_beschluesse WHERE versammlung_id=? "
                               "ORDER BY nummer, id", (vsid,)).fetchall()
    mitglieder = conn.execute(
        "SELECT id, vorname, nachname FROM mitglieder WHERE verein_id=? AND status='aktiv' "
        "ORDER BY nachname COLLATE NOCASE, vorname", (vid,)).fetchall() if was == "liste" else []
    conn.close()
    return render_template("versammlung/druck.html", v=v, anwesend=anwesend, beschluesse=beschluesse,
                           mitglieder=mitglieder, was=was, einst=get_einstellungen(),
                           arten=ARTEN_NAMEN, heute=date.today().isoformat())


@bp.route("/<int:vsid>/loeschen", methods=["POST"])
def loeschen(vsid):
    conn = get_db()
    vid = aktiver_verein_id()
    _v(conn, vid, vsid)
    conn.execute("DELETE FROM versammlung_anwesend WHERE versammlung_id=?", (vsid,))
    conn.execute("DELETE FROM versammlung_beschluesse WHERE versammlung_id=?", (vsid,))
    conn.execute("DELETE FROM versammlungen WHERE id=? AND verein_id=?", (vsid, vid))
    conn.commit()
    conn.close()
    flash("Versammlung gelöscht.", "erfolg")
    return redirect(url_for("versammlung.index"))
