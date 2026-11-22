"""
Zugangsverwaltung (nur Admin): Login-Konten anlegen, Mitgliedern zuordnen,
Passwort-Setzen-Links erzeugen, aktiv schalten und löschen.
"""
import secrets
from datetime import datetime, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session)
from flask_login import current_user

from .. import db
from ..db import get_db, aktiver_verein_id
from ..auth import hash_passwort

bp = Blueprint("zugaenge", __name__, url_prefix="/zugaenge")

ROLLEN = [("admin", "Admin (volle Verwaltung)"),
          ("schatz", "Schatzmeister:in (nur Finanzbereich)"),
          ("oeffentlichkeit", "Öffentlichkeitsarbeit (Presse, Social Media, Fotos)"),
          ("mitglied", "Mitglied (nur Mitglieder-App)"),
          ("extern", "Freund (Gast: nur Termine & Anmeldungen)")]


def _mitglieder(vid):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, vorname, nachname, email FROM mitglieder WHERE verein_id=? "
        "ORDER BY nachname COLLATE NOCASE, vorname COLLATE NOCASE", (vid,)).fetchall()
    conn.close()
    return rows


def _link_erzeugen(uid, stunden=48):
    token = secrets.token_urlsafe(32)
    ablauf = (datetime.now() + timedelta(hours=stunden)).strftime("%Y-%m-%d %H:%M:%S")
    db.benutzer_aktualisieren(uid, reset_token=token, reset_ablauf=ablauf)
    return url_for("auth.passwort_neu", token=token, _external=True)



def _ich_id():
    """Eigene Konto-Nr. als Zahl (current_user.id ist ein Text – der Vergleich
    mit der int-Route-ID war vorher nie wahr, Selbstschutz griff nicht)."""
    try:
        return int(getattr(current_user, "id", None))
    except (TypeError, ValueError):
        return None

@bp.route("/")
def index():
    vid = aktiver_verein_id()
    benutzer = db.alle_benutzer(vid)
    mitglieder = _mitglieder(vid)
    mit_namen = {m["id"]: f"{m['vorname']} {m['nachname']}".strip() for m in mitglieder}
    return render_template("zugaenge/index.html", aktiv="zugaenge",
                           benutzer=benutzer, mitglieder=mitglieder, mit_namen=mit_namen,
                           geraete=db.passkeys_fuer_benutzer([b["id"] for b in benutzer]),
                           rollen=ROLLEN, ich_id=_ich_id(),
                           vorbelegt_mitglied=request.args.get("mitglied", type=int),
                           neuer_link=session.pop("zugang_link", None))


@bp.route("/neu", methods=["POST"])
def neu():
    vid = aktiver_verein_id()
    email = (request.form.get("email") or "").strip().lower()
    name = (request.form.get("anzeigename") or "").strip()
    rolle = request.form.get("rolle") if request.form.get("rolle") in ("admin", "schatz", "oeffentlichkeit", "mitglied", "extern") else "mitglied"
    mid = request.form.get("mitglied_id", type=int)
    pw = request.form.get("passwort") or ""

    if not email:
        flash("Bitte eine E-Mail-Adresse angeben.", "fehler")
        return redirect(url_for("zugaenge.index"))
    if db.benutzer_per_email(email):
        flash("Für diese E-Mail gibt es bereits einen Zugang.", "fehler")
        return redirect(url_for("zugaenge.index"))
    if pw and len(pw) < 8:
        flash("Das Passwort muss mindestens 8 Zeichen haben (oder leer lassen für einen Passwort-Link).", "fehler")
        return redirect(url_for("zugaenge.index"))

    if not name and mid:
        m = next((x for x in _mitglieder(vid) if x["id"] == mid), None)
        if m:
            name = f"{m['vorname']} {m['nachname']}".strip()

    pwhash = hash_passwort(pw) if pw else ""
    uid = db.benutzer_anlegen(email, name or email, rolle, vid, mid or None, pwhash)

    if pw:
        flash(f"Zugang für {email} angelegt – Anmeldung mit dem gesetzten Passwort möglich.", "erfolg")
    else:
        # Kein Passwort gesetzt → Setzen-Link erzeugen, den der Admin weitergeben kann.
        link = _link_erzeugen(uid)
        session["zugang_link"] = {"email": email, "link": link}
        flash(f"Zugang für {email} angelegt. Passwort-Link unten weitergeben.", "erfolg")
    return redirect(url_for("zugaenge.index"))


def _laden_geschuetzt(uid):
    """Benutzer laden und sicherstellen, dass er zum aktiven Verein (oder übergreifend) gehört."""
    b = db.benutzer_laden(uid)
    if not b:
        return None
    if b["verein_id"] not in (aktiver_verein_id(), None):
        return None
    return b


@bp.route("/<int:uid>/passwort-link", methods=["POST"])
def passwort_link(uid):
    b = _laden_geschuetzt(uid)
    if not b:
        flash("Zugang nicht gefunden.", "fehler")
        return redirect(url_for("zugaenge.index"))
    link = _link_erzeugen(uid)
    session["zugang_link"] = {"email": b["email"], "link": link}
    return redirect(url_for("zugaenge.index"))


@bp.route("/<int:uid>/bearbeiten", methods=["POST"])
def bearbeiten(uid):
    b = _laden_geschuetzt(uid)
    if not b:
        flash("Zugang nicht gefunden.", "fehler")
        return redirect(url_for("zugaenge.index"))
    rolle = request.form.get("rolle") if request.form.get("rolle") in ("admin", "schatz", "oeffentlichkeit", "mitglied", "extern") else b["rolle"]
    mid = request.form.get("mitglied_id", type=int)
    # Notfall-Konten (verein_id NULL) bleiben admin: mit anderer Rolle fielen sie
    # aus allen Vereins-Abfragen (Zugänge-Liste, Mitteilungen) und wären per
    # Oberfläche nicht mehr erreichbar.
    if b["verein_id"] is None and rolle != "admin":
        flash("Das übergreifende Notfall-Konto behält die Admin-Rolle.", "fehler")
        return redirect(url_for("zugaenge.index"))
    # Aussperr-Schutz: den letzten aktiven Admin nicht zum Mitglied herabstufen
    if b["rolle"] == "admin" and rolle != "admin" and db.aktive_admins_anzahl(aktiver_verein_id()) <= 1:
        flash("Das ist der letzte Admin – die Rolle kann nicht geändert werden.", "fehler")
        return redirect(url_for("zugaenge.index"))
    # E-Mail ändern (mit Duplikat-Schutz – eine Adresse = ein Zugang)
    email = (request.form.get("email") or "").strip().lower()
    if email and email != (b["email"] or "").lower():
        andere = db.benutzer_per_email(email)
        if andere and andere["id"] != uid:
            flash(f"Die Adresse {email} gehört bereits zum Zugang "
                  f"„{andere['anzeigename'] or andere['email']}“ – nicht geändert.", "fehler")
            return redirect(url_for("zugaenge.index"))
        db.benutzer_aktualisieren(uid, email=email)
    db.benutzer_aktualisieren(uid, rolle=rolle, mitglied_id=mid or None)
    flash("Zugang aktualisiert.", "erfolg")
    return redirect(url_for("zugaenge.index"))


@bp.route("/<int:uid>/aktiv", methods=["POST"])
def aktiv(uid):
    b = _laden_geschuetzt(uid)
    if not b:
        flash("Zugang nicht gefunden.", "fehler")
        return redirect(url_for("zugaenge.index"))
    neu_aktiv = 0 if b["aktiv"] else 1
    if not neu_aktiv:
        if uid == _ich_id():
            flash("Du kannst dich nicht selbst deaktivieren.", "fehler")
            return redirect(url_for("zugaenge.index"))
        if b["rolle"] == "admin" and db.aktive_admins_anzahl(aktiver_verein_id()) <= 1:
            flash("Das ist der letzte aktive Admin – nicht deaktivierbar.", "fehler")
            return redirect(url_for("zugaenge.index"))
    db.benutzer_aktualisieren(uid, aktiv=neu_aktiv)
    flash("Zugang " + ("aktiviert." if neu_aktiv else "deaktiviert."), "erfolg")
    return redirect(url_for("zugaenge.index"))


@bp.route("/<int:uid>/loeschen", methods=["POST"])
def loeschen(uid):
    b = _laden_geschuetzt(uid)
    if not b:
        flash("Zugang nicht gefunden.", "fehler")
        return redirect(url_for("zugaenge.index"))
    if uid == _ich_id():
        flash("Du kannst deinen eigenen Zugang nicht löschen.", "fehler")
        return redirect(url_for("zugaenge.index"))
    if b["rolle"] == "admin" and db.aktive_admins_anzahl(aktiver_verein_id()) <= 1:
        flash("Das ist der letzte aktive Admin – nicht löschbar.", "fehler")
        return redirect(url_for("zugaenge.index"))
    db.benutzer_loeschen(uid)
    flash("Zugang gelöscht.", "erfolg")
    return redirect(url_for("zugaenge.index"))


@bp.route("/geraet/<int:pid>/entfernen", methods=["POST"])
def geraet_entfernen(pid):
    """Face-ID-Gerät eines Kontos dieses Vereins entfernen (Sicherheitsprüfung 21.11.2026):
    z. B. bei verlorenem Handy oder einem Gerät, das die Person nicht selbst eingerichtet hat."""
    erlaubt = [b["id"] for b in db.alle_benutzer(aktiver_verein_id())]
    if db.passkey_loeschen_verwaltung(pid, erlaubt):
        flash("Face-ID-Gerät entfernt.", "erfolg")
    else:
        flash("Gerät nicht gefunden.", "fehler")
    return redirect(url_for("zugaenge.index"))
