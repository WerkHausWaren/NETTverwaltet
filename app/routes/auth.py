"""Anmelde-Routen: Nextcloud-SSO, Notfall-Admin, 2FA, Passwort-Reset."""
import io
import secrets
from datetime import datetime, timedelta

import pyotp
import segno
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session, current_app, abort)
from flask_login import login_user, logout_user, login_required, current_user

from .. import db
from ..auth import (Nutzer, hash_passwort, pruefe_passwort, nc_flow_start,
                    nc_flow_poll, nc_benutzerdaten, nc_app_passwort_widerrufen,
                    rolle_aus_gruppen)

bp = Blueprint("auth", __name__)


def _einloggen(row, dauer=None):
    """Meldet einen Benutzer an. Mitglieder/Externe bekommen ein langlebiges
    Remember-Cookie (Portal = App-Gefühl, 180 Tage), Admins seit 07.11.2026
    ein KURZES von einem Monat – jeweils nur, wenn beim Anmelden „Eingeloggt
    bleiben" angehakt war (Wunsch aus dem Verein: Vorstand nicht mehr alle 12 h neu).
    Flows ohne Formular-Häkchen (Demo, Registrierungs-Auto-Login) merken sich
    das Gerät wie bisher."""
    merken = session.pop("login_merken", None)
    merken = True if merken is None else bool(merken)
    if dauer is not None:
        # Face ID (21.11.2026): „Eingeloggt bleiben“ höchstens bis zum Ende der Face-ID-Frist
        login_user(Nutzer(row), remember=merken, duration=dauer)
    elif row["rolle"] == "admin":
        login_user(Nutzer(row), remember=merken, duration=timedelta(days=31))
    else:
        login_user(Nutzer(row), remember=merken)
    session.permanent = True
    # Zeitpunkt der echten Anmeldung (nicht per Merk-Cookie) – neue Face-ID-Geräte
    # dürfen nur kurz danach eingerichtet werden (Sicherheitsprüfung 21.11.2026)
    session["auth_zeit"] = datetime.now().timestamp()
    # Verein dieser Sitzung festlegen: gewählter Verein, sonst der des Kontos.
    vid = session.pop("login_verein_id", None)
    if vid is None and "verein_id" in row.keys():
        vid = row["verein_id"]
    if vid is not None:
        session["verein_id"] = vid
    db.letzter_login_setzen(row["id"])


def _gewaehlter_verein():
    """Vom Anmeldeformular gewählte Vereins-ID (oder der einzige Verein)."""
    vid = request.form.get("verein_id", type=int)
    vereine = db.alle_vereine()
    gueltige = {v["id"] for v in vereine}
    if vid in gueltige:
        return vid
    if len(vereine) == 1:
        return vereine[0]["id"]
    return None


@bp.route("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    # Offene Nextcloud-Anmeldung? Dann hier zu Ende bringen (App-Neustart!)
    ziel = nc_offen_abschliessen()
    if ziel:
        return redirect(ziel)
    e = db.get_einstellungen()
    nc_konfiguriert = bool(e and "ncc_server" in e.keys() and e["ncc_server"])
    return render_template("auth/login.html", nc_url=current_app.config.get("NEXTCLOUD_URL"),
                           vereine=db.alle_vereine(), nc_konfiguriert=nc_konfiguriert)


def _nc_flow_beginnen():
    """Holt einen frischen Anmelde-Token von Nextcloud und merkt ihn samt
    Anmelde-Adresse in der Sitzung. Die Adresse liegt BEWUSST in der Session:
    So zeigen Warteseite und Öffnen-Knopf immer auf denselben Token – zwei
    konkurrierende Tokens waren die Haupt-Hängeursache (Audit 07.11.)."""
    daten = nc_flow_start()
    session["nc_flow"] = {"token": daten["poll"]["token"],
                          "endpoint": daten["poll"]["endpoint"],
                          "login_url": daten["login"]}
    session.pop("nc_poll_ts", None)
    return daten


@bp.route("/login/nextcloud", methods=["POST"])
def login_nextcloud():
    session["login_verein_id"] = _gewaehlter_verein()
    if not session["login_verein_id"]:
        # Ohne Vereinswahl würde am Ende ein Konto ohne verein_id entstehen
        # (NULL ist den Notfall-Admins vorbehalten) – gar nicht erst starten.
        flash("Bitte wähle zuerst deinen Verein aus.", "fehler")
        return redirect(url_for("auth.login"))
    session["login_merken"] = 1 if request.form.get("merken") else 0
    try:
        _nc_flow_beginnen()
    except Exception as e:  # noqa: BLE001
        flash(f"Nextcloud ist gerade nicht erreichbar: {e}", "fehler")
        return redirect(url_for("auth.login"))
    # Post/Redirect/Get: Die Warteseite ist eine eigene GET-Adresse – ein
    # Neuladen (iOS wirft die App gern aus dem Speicher) landet wieder HIER
    # statt auf einem 405er der POST-Adresse (Audit 07.11.).
    return redirect(url_for("auth.login_nextcloud_warten"))


@bp.route("/login/nextcloud/warten")
def login_nextcloud_warten():
    """Die Warteseite des Nextcloud-Logins – neu-ladbar und App-Neustart-fest."""
    flow = session.get("nc_flow")
    if not flow or not session.get("login_verein_id"):
        return redirect(url_for("auth.login"))
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index") if current_user.ist_admin
                        else url_for("portal.home"))
    return render_template("auth/warten.html", login_url=flow.get("login_url", ""))


@bp.route("/login/nextcloud/neu")
def login_nextcloud_neu():
    """Startet den Nextcloud-Anmeldefluss NEU und springt direkt zur Anmeldeseite.

    Der Login-Token von Nextcloud ist ein Einweg-Token: Nach einem Fehlversuch
    (falsches Passwort, Tab geschlossen) ist er verbraucht, und derselbe Link
    endet bei Nextcloud auf „Zugriff verboten". Dieser Weg holt deshalb bei
    jedem Klick einen frischen Token – die Warteseite pollt automatisch gegen
    den neuen Stand weiter."""
    if not session.get("login_verein_id"):
        return redirect(url_for("auth.login"))
    try:
        daten = _nc_flow_beginnen()
    except Exception as e:  # noqa: BLE001
        flash(f"Nextcloud ist gerade nicht erreichbar: {e}", "fehler")
        return redirect(url_for("auth.login"))
    return redirect(daten["login"])


def _nc_anmeldung_abschliessen(res):
    """Gemeinsamer Abschluss nach bestätigtem Nextcloud-Poll: Nutzerdaten
    holen, Rolle bestimmen, Konto anlegen/aktualisieren, einloggen.
    Liefert (status, wert): ("ok", ziel-URL) oder ("kein_zugang"/"fehler", Text)."""
    log = current_app.logger
    login_name = res.get("loginName") or ""
    app_pw = res.get("appPassword") or ""
    log.info("[NCLOGIN] poll OK, loginName=%r, appPassword vorhanden=%s", login_name, bool(app_pw))
    try:
        gruppen, email, anzeigename = nc_benutzerdaten(login_name, app_pw)
    except Exception:  # noqa: BLE001
        log.exception("[NCLOGIN] nc_benutzerdaten fehlgeschlagen")
        session.pop("nc_flow", None)
        return "fehler", "Konnte Nutzerdaten nicht laden."
    finally:
        # Das App-Passwort wird nur für diese eine Abfrage gebraucht → sofort
        # bei Nextcloud widerrufen (sonst bleibt je Anmeldung ein gültiger
        # Zugang der App im Konto liegen) und nicht speichern.
        widerrufen = nc_app_passwort_widerrufen(login_name, app_pw)
        log.info("[NCLOGIN] App-Passwort widerrufen=%s", widerrufen)
        app_pw = ""
    # Einstellungen des gewählten Vereins laden (Gruppen-Zuordnung je Verein)
    einst = None
    try:
        from flask import g as _g
        if session.get("login_verein_id"):
            _g.verein_id = session["login_verein_id"]
        einst = db.get_einstellungen()
    except Exception:  # noqa: BLE001
        einst = None
    rolle = rolle_aus_gruppen(gruppen, einst)
    log.info("[NCLOGIN] gruppen=%r -> rolle=%r", gruppen, rolle)
    if not rolle:
        session.pop("nc_flow", None)
        return "kein_zugang", None
    if not session.get("login_verein_id"):
        # Sicherheitsnetz: ohne Vereinswahl kein Konto anlegen (sonst entstünde
        # ein NULL-Konto, das wie ein Notfall-Admin vereinsübergreifend wäre).
        session.pop("nc_flow", None)
        log.warning("[NCLOGIN] kein login_verein_id in der Sitzung -> Abbruch")
        return "fehler", "Bitte zuerst den Verein wählen und neu anmelden."
    # Früher gespeichertes App-Passwort (Altbestand bis 20.11.2026) ebenfalls widerrufen.
    for alt in db.nc_alte_tokens(login_name, session["login_verein_id"]):
        log.info("[NCLOGIN] altes App-Passwort widerrufen=%s", nc_app_passwort_widerrufen(login_name, alt))
    uid = db.nc_benutzer_upsert(login_name, email, anzeigename, rolle, app_pw,
                                verein_id=session["login_verein_id"])
    session.pop("nc_flow", None)
    nutzer = db.benutzer_laden(uid)
    _einloggen(nutzer)
    # Die gespeicherte Rolle kann von der Gruppen-Rolle abweichen: von Hand
    # vergebene Sonderrollen (Schatzmeister:in, Öffentlichkeitsarbeit) bleiben
    # bestehen. Für das Ziel zählt deshalb die Rolle aus der Datenbank.
    if nutzer is not None and nutzer["rolle"]:
        rolle = nutzer["rolle"]
    ziel = (url_for("dashboard.index") if rolle in ("admin", "schatz", "oeffentlichkeit")
            else url_for("portal.home"))
    log.info("[NCLOGIN] Login OK uid=%s rolle=%s -> %s", uid, rolle, ziel)
    return "ok", ziel


@bp.route("/login/nextcloud/status")
def login_nextcloud_status():
    log = current_app.logger
    flow = session.get("nc_flow")
    if not flow:
        log.warning("[NCLOGIN] keine nc_flow-Sitzung vorhanden")
        return {"status": "fehler", "text": "Sitzung abgelaufen. Bitte neu starten."}
    try:
        res = nc_flow_poll(flow["token"], flow.get("endpoint"))
    except Exception:  # noqa: BLE001
        log.exception("[NCLOGIN] Fehler beim Poll")
        return {"status": "warten"}
    if not res:
        log.info("[NCLOGIN] poll -> noch keine Bestätigung (warten)")
        return {"status": "warten"}
    status, wert = _nc_anmeldung_abschliessen(res)
    if status == "ok":
        return {"status": "ok", "redirect": wert}
    if status == "kein_zugang":
        return {"status": "kein_zugang"}
    return {"status": "fehler", "text": wert or "Fehler."}


def nc_offen_abschliessen():
    """App-Neustart-Retter (Audit 07.11.): Hängt in der Sitzung noch eine
    offene Nextcloud-Anmeldung (nc_flow), wird sie beim nächsten Seitenaufruf
    serverseitig zu Ende gebracht – auch wenn iOS die Warteseite längst aus
    dem Speicher geworfen hat. Liefert die Ziel-URL oder None.

    Gedrosselt auf alle 3 Sekunden und mit kurzem Zeitlimit, damit normale
    Seitenaufrufe nie spürbar warten."""
    import time
    flow = session.get("nc_flow")
    if not flow or current_user.is_authenticated or not session.get("login_verein_id"):
        return None
    jetzt = time.time()
    if jetzt - float(session.get("nc_poll_ts") or 0) < 3:
        return None
    session["nc_poll_ts"] = jetzt
    try:
        res = nc_flow_poll(flow["token"], flow.get("endpoint"), timeout=6)
    except Exception:  # noqa: BLE001
        return None
    if not res:
        return None
    status, wert = _nc_anmeldung_abschliessen(res)
    return wert if status == "ok" else None


def _weiter_ziel():
    """Rücksprung nach der Anmeldung (z. B. vom Merkzettel) – nur interne Portal-Pfade."""
    weiter = (request.form.get("weiter") or "").strip()
    if weiter.startswith("/portal/") and not weiter.startswith("//") and "\\" not in weiter:
        return weiter
    return None


@bp.route("/login/notfall", methods=["POST"])
def login_notfall():
    email = request.form.get("email", "").strip()
    pw = request.form.get("passwort", "")
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    # Login-Bremse: nach zu vielen Fehlversuchen (E-Mail oder IP) 15 Minuten Pause
    if db.login_gesperrt(email, ip):
        flash("Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen.", "fehler")
        return redirect(_weiter_ziel() or url_for("auth.login"))
    session["login_verein_id"] = _gewaehlter_verein()
    session["login_merken"] = 1 if request.form.get("merken") else 0
    row = db.benutzer_per_email(email)
    # Ein Login für alle: Nextcloud-Konten (ohne lokales Passwort) landen hier,
    # wenn jemand das E-Mail-Formular nimmt -> direkt in den Vereins-Konto-Flow
    # weiterleiten statt "Passwort falsch" zu melden.
    e = db.get_einstellungen()
    if (row and row["aktiv"] and not row["passwort_hash"]
            and e and "ncc_server" in e.keys() and e["ncc_server"]):
        if not session["login_verein_id"]:
            # Der Nextcloud-Umweg braucht die Vereinswahl (siehe login_nextcloud).
            flash("Bitte wähle zuerst deinen Verein aus.", "fehler")
            return redirect(url_for("auth.login"))
        try:
            _nc_flow_beginnen()
        except Exception as exc:  # noqa: BLE001
            flash(f"Dein Konto meldet sich über das Vereins-Konto an, aber Nextcloud "
                  f"ist gerade nicht erreichbar: {exc}", "fehler")
            return redirect(url_for("auth.login"))
        flash("Dein Konto nutzt den Vereins-Konto-Login – wir haben dich direkt weitergeleitet.", "erfolg")
        return redirect(url_for("auth.login_nextcloud_warten"))
    if not row or not row["aktiv"] or not pruefe_passwort(row["passwort_hash"], pw):
        db.login_fehlversuch(email, ip)
        flash("E-Mail oder Passwort ist falsch.", "fehler")
        return redirect(_weiter_ziel() or url_for("auth.login"))
    db.login_erfolg(email)
    if row["twofa_aktiv"]:
        session["2fa_uid"] = row["id"]
        return redirect(url_for("auth.zwei_fa"))
    _einloggen(row)
    # Anmeldung direkt vom Merkzettel (19.11.2026): zurück dorthin – nur interne Pfade
    return redirect(_weiter_ziel() or url_for("dashboard.index"))


@bp.route("/2fa", methods=["GET", "POST"])
def zwei_fa():
    uid = session.get("2fa_uid")
    if not uid:
        return redirect(url_for("auth.login"))
    row = db.benutzer_laden(uid)
    if request.method == "POST":
        # Auch der zweite Faktor braucht eine Bremse: sonst ließe sich der
        # sechsstellige Code nach einem geklauten Passwort einfach durchprobieren.
        kennung = f"2fa:{uid}"
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
        if db.login_gesperrt(kennung, ip):
            flash("Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen.", "fehler")
            return redirect(url_for("auth.login"))
        code = request.form.get("code", "").strip().replace(" ", "")
        if row and row["totp_secret"] and pyotp.TOTP(row["totp_secret"]).verify(code, valid_window=1):
            db.login_erfolg(kennung)
            session.pop("2fa_uid", None)
            _einloggen(row)
            return redirect(url_for("dashboard.index"))
        db.login_fehlversuch(kennung, ip)
        flash("Der Code stimmt nicht. Bitte erneut versuchen.", "fehler")
    return render_template("auth/2fa.html")


@bp.route("/logout")
def logout():
    logout_user()
    # session.clear() würde auch die "_remember=clear"-Merkung von logout_user()
    # wegwerfen -> das Langzeit-Cookie bliebe gültig und meldet sofort wieder an.
    merker = {k: session[k] for k in ("_remember", "_remember_seconds") if k in session}
    session.clear()
    session.update(merker)
    flash("Du wurdest abgemeldet.", "info")
    return redirect(url_for("auth.login"))


# ------------------------- Erst-Einrichtung ------------------------------
@bp.route("/einrichten", methods=["GET", "POST"])
def einrichten():
    if db.benutzer_anzahl() > 0:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        vereinsname = request.form.get("vereinsname", "").strip()
        ort = request.form.get("ort", "").strip()
        vereins_email = request.form.get("vereins_email", "").strip()
        email = request.form.get("email", "").strip()
        pw = request.form.get("passwort", "")
        pw2 = request.form.get("passwort2", "")
        if not vereinsname:
            flash("Bitte den Vereinsnamen angeben.", "fehler")
        elif not email or len(pw) < 8 or pw != pw2:
            flash("Bitte eine gültige E-Mail und zweimal dasselbe Passwort (mind. 8 Zeichen).", "fehler")
        else:
            uid = db.notfall_admin_anlegen(email, hash_passwort(pw))
            # Vereinsname/Ort/Kontakt gleich mitschreiben – sonst hieße der
            # Verein bis zum ersten Einstellungen-Besuch "Mein Verein e.V.".
            db.verein_stammdaten_setzen(vereinsname, ort, vereins_email)
            _einloggen(db.benutzer_laden(uid))
            flash("Verein eingerichtet. Richte jetzt die Zwei-Faktor-Sicherung ein (empfohlen).", "erfolg")
            return redirect(url_for("auth.zwei_fa_einrichten"))
    return render_template("auth/einrichten.html")


# ----------------------- 2FA einrichten (Notfall) ------------------------
@bp.route("/2fa/einrichten", methods=["GET", "POST"])
@login_required
def zwei_fa_einrichten():
    row = db.benutzer_laden(int(current_user.id))
    if not row["passwort_hash"]:
        flash("Für Nextcloud-Konten wird die Zwei-Faktor-Sicherung in Nextcloud verwaltet.", "info")
        return redirect(url_for("dashboard.index"))
    secret = session.get("neu_totp")
    if not secret:
        secret = pyotp.random_base32()
        session["neu_totp"] = secret
    if request.method == "POST":
        code = request.form.get("code", "").strip().replace(" ", "")
        if pyotp.TOTP(secret).verify(code, valid_window=1):
            db.benutzer_aktualisieren(row["id"], totp_secret=secret, twofa_aktiv=1)
            session.pop("neu_totp", None)
            flash("Zwei-Faktor-Sicherung ist aktiv.", "erfolg")
            return redirect(url_for("dashboard.index"))
        flash("Der Code stimmt nicht – bitte erneut versuchen.", "fehler")
    einst = db.get_einstellungen()
    aussteller = (einst["vereinsname"] if einst and einst.get("vereinsname") else "Vereinsverwaltung")
    otpauth = pyotp.TOTP(secret).provisioning_uri(name=row["email"], issuer_name=aussteller)
    buf = io.BytesIO()
    segno.make(otpauth).save(buf, kind="svg", scale=4)
    return render_template("auth/2fa_einrichten.html", qr_svg=buf.getvalue().decode("utf-8"), secret=secret)


# ----------------------- Freunde-Selbstregistrierung ----------------------
def _freunde_registrierung_an():
    e = db.get_einstellungen()
    return bool(e and e.get("freunde_registrierung"))


@bp.route("/freund-registrieren", methods=["GET", "POST"])
def freund_registrieren():
    """Selbstregistrierung als Freund/Gast (Rolle 'extern', Double-Opt-in per E-Mail)."""
    if not _freunde_registrierung_an():
        abort(404)
    einst = db.get_einstellungen()
    if request.method == "POST":
        # Honeypot: unsichtbares Feld – Bots füllen es aus
        if request.form.get("webseite"):
            return redirect(url_for("auth.login"))
        name = (request.form.get("name") or "").strip()[:80]
        email = (request.form.get("email") or "").strip()[:120]
        pw = request.form.get("passwort", "")
        if not name or "@" not in email or len(pw) < 8:
            flash("Bitte Name, gültige E-Mail und ein Passwort mit mindestens 8 Zeichen angeben.", "fehler")
            return redirect(url_for("auth.freund_registrieren"))
        if not request.form.get("datenschutz"):
            flash("Bitte bestätige die Datenschutzhinweise.", "fehler")
            return redirect(url_for("auth.freund_registrieren"))
        sys = db.get_system_db()
        # Abgelaufene, unbestätigte Registrierungen aufräumen (48 h)
        grenze = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
        sys.execute("DELETE FROM benutzer WHERE aktiv=0 AND rolle='extern' AND reg_token IS NOT NULL "
                    "AND reg_token != '' AND erstellt_am < ?", (grenze,))
        sys.commit()
        vorhanden = db.benutzer_per_email(email)
        if not vorhanden:
            token = secrets.token_urlsafe(32)
            sys.execute("INSERT INTO benutzer (nc_benutzer, email, anzeigename, rolle, passwort_hash, "
                        "twofa_aktiv, aktiv, verein_id, reg_token, reg_ablauf, erstellt_am) "
                        "VALUES ('', ?, ?, 'extern', ?, 0, 0, ?, ?, ?, ?)",
                        (email, name, hash_passwort(pw), db.aktiver_verein_id(), token,
                         (datetime.now() + timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S"), db.now_iso()))
            sys.commit()
            try:
                from .. import mail
                link = url_for("auth.freund_bestaetigen", token=token, _external=True)
                vname = (einst["vereinsname"] if einst else "") or "Vereinsverwaltung"
                mail.einzelmail(einst, email, f"Bitte bestätigen – dein Freunde-Zugang bei {vname}",
                                f"Hallo {name},\n\nschön, dass du dabei sein willst! Bitte bestätige deine "
                                f"E-Mail-Adresse mit diesem Link (48 Stunden gültig):\n{link}\n\n"
                                "Wenn du das nicht warst, ignoriere diese E-Mail einfach.\n\n"
                                f"{vname}")
            except Exception:  # noqa: BLE001
                pass
        sys.close()
        # Immer dieselbe Antwort – kein Rückschluss, ob die Adresse schon existiert
        flash("Fast geschafft! Falls die Adresse neu ist, haben wir dir einen Bestätigungslink geschickt "
              "(bitte auch im Spam-Ordner nachsehen).", "erfolg")
        return redirect(url_for("auth.login"))
    return render_template("auth/freund_registrieren.html")


@bp.route("/freund-bestaetigen/<token>")
def freund_bestaetigen(token):
    if not _freunde_registrierung_an():
        abort(404)
    sys = db.get_system_db()
    row = sys.execute("SELECT * FROM benutzer WHERE reg_token=? AND aktiv=0 AND rolle='extern'",
                      (token,)).fetchone()
    jetzt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not row or not row["reg_ablauf"] or row["reg_ablauf"] < jetzt:
        sys.close()
        flash("Der Bestätigungslink ist ungültig oder abgelaufen. Bitte registriere dich erneut.", "fehler")
        return redirect(url_for("auth.login"))
    sys.execute("UPDATE benutzer SET aktiv=1, reg_token='', reg_ablauf='' WHERE id=?", (row["id"],))
    sys.commit()
    sys.close()
    _einloggen(db.benutzer_laden(row["id"]))
    # Gestaltete Willkommensmail für den neuen Freunde-Zugang (best effort)
    try:
        from .. import mail as _mail, mailhtml
        einst = db.get_einstellungen()
        if einst and (row["email"] or "").strip():
            vorname = (row["anzeigename"] or "").split()[0] if (row["anzeigename"] or "").strip() else "du"
            betreff, html = mailhtml.willkommen_freund(einst, request.host_url.rstrip("/"), vorname)
            _mail.einzelmail(einst, row["email"], betreff, _mail._html_zu_text(html), html=html)
    except Exception:  # noqa: BLE001
        pass
    flash("Willkommen! Dein Freunde-Zugang ist bestätigt. 🎉", "erfolg")
    return redirect(url_for("portal.home"))


# ----------------------------- Demo-Zugang -------------------------------
@bp.route("/demo-login", methods=["POST"])
def demo_login():
    """Ein-Klick-Demo (nur mit VV_DEMO_LOGIN=1, d. h. nur im Demo-Verein).

    bereich=mitglied  -> Demo-Mitglied, landet in der Mitglieder-App
    bereich=admin     -> Demo-Vorstand, landet in der Verwaltung
    Die Konten werden beim ersten Mal automatisch angelegt (zufälliges,
    unbenutzbares Passwort – Anmeldung ausschließlich über diese Knöpfe)."""
    if not current_app.config.get("DEMO_LOGIN"):
        abort(404)
    admin = request.form.get("bereich") == "admin"
    email = "demo-vorstand@demo.lokal" if admin else "demo-mitglied@demo.lokal"
    sys = db.get_system_db()
    row = sys.execute("SELECT * FROM benutzer WHERE email=?", (email,)).fetchone()
    if not row:
        vid = db.aktiver_verein_id()
        mitglied_id, name = None, ("Demo-Vorstand" if admin else "Demo-Mitglied")
        if not admin:
            conn = db.get_db()
            m = conn.execute("SELECT id, vorname, nachname FROM mitglieder WHERE verein_id=? "
                             "AND status='aktiv' ORDER BY id LIMIT 1", (vid,)).fetchone()
            conn.close()
            if m:
                mitglied_id, name = m["id"], f"{m['vorname']} {m['nachname']}"
        sys.execute("INSERT INTO benutzer (nc_benutzer, email, anzeigename, rolle, passwort_hash, "
                    "twofa_aktiv, aktiv, verein_id, mitglied_id, erstellt_am) "
                    "VALUES ('', ?, ?, ?, ?, 0, 1, ?, ?, ?)",
                    (email, name, "admin" if admin else "mitglied",
                     hash_passwort(secrets.token_urlsafe(24)), vid, mitglied_id, db.now_iso()))
        sys.commit()
        row = sys.execute("SELECT * FROM benutzer WHERE email=?", (email,)).fetchone()
    sys.close()
    _einloggen(row)
    if admin:
        flash("Du bist als Demo-Vorstand in der Verwaltung – probiere alles aus!", "erfolg")
        return redirect(url_for("dashboard.index"))
    flash("Du bist als Demo-Mitglied angemeldet – schau dich in Ruhe um!", "erfolg")
    return redirect(url_for("portal.home"))


# -------------------------- Passwort vergessen ---------------------------
@bp.route("/passwort-vergessen")
def passwort_vergessen():
    from ..auth import nc_basis
    e = db.get_einstellungen()
    nc_konfiguriert = bool(e and "ncc_server" in e.keys() and e["ncc_server"])
    nc_url = nc_basis(e["ncc_server"]) if nc_konfiguriert else (current_app.config.get("NEXTCLOUD_URL") or "").rstrip("/")
    return render_template("auth/passwort_vergessen.html", nc_url=nc_url,
                           nc_konfiguriert=nc_konfiguriert)


@bp.route("/passwort-reset", methods=["POST"])
def passwort_reset_anfordern():
    email = request.form.get("email", "").strip()
    row = db.benutzer_per_email(email)
    if row and row["passwort_hash"]:  # nur Notfall-Konten haben ein App-Passwort
        token = secrets.token_urlsafe(32)
        ablauf = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        db.benutzer_aktualisieren(row["id"], reset_token=token, reset_ablauf=ablauf)
        try:
            from .. import mail
            einst = db.get_einstellungen()
            vname = (einst["vereinsname"] if einst and einst.get("vereinsname") else "Vereinsverwaltung")
            link = url_for("auth.passwort_neu", token=token, _external=True)
            mail.einzelmail(einst, email, f"Passwort zurücksetzen – {vname}",
                            f"Hallo,\n\nüber diesen Link kannst du dein Passwort neu setzen (gültig 2 Stunden):\n{link}\n\n"
                            "Wenn du das nicht warst, ignoriere diese E-Mail einfach.")
        except Exception:  # noqa: BLE001
            pass
    flash("Falls ein Notfall-Konto mit dieser E-Mail existiert, wurde ein Link gesendet.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/passwort-neu/<token>", methods=["GET", "POST"])
def passwort_neu(token):
    row = db.benutzer_per_reset_token(token)
    jetzt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not row or not row["reset_ablauf"] or row["reset_ablauf"] < jetzt:
        flash("Der Link ist ungültig oder abgelaufen.", "fehler")
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        pw = request.form.get("passwort", "")
        pw2 = request.form.get("passwort2", "")
        if len(pw) < 8 or pw != pw2:
            flash("Bitte zweimal dasselbe Passwort (mind. 8 Zeichen).", "fehler")
        else:
            db.benutzer_aktualisieren(row["id"], passwort_hash=hash_passwort(pw),
                                      reset_token="", reset_ablauf="")
            # Wer sein Passwort zurücksetzt, will ggf. einen Eindringling loswerden:
            # dann auch alle Face-ID-Geräte entfernen (neu einrichten geht jederzeit)
            n = db.passkeys_alle_loeschen(row["id"])
            flash("Passwort geändert. Bitte melde dich neu an."
                  + (f" Zur Sicherheit wurden auch deine {n} Face-ID-Gerät{'e' if n != 1 else ''} entfernt."
                     if n else ""), "erfolg")
            return redirect(url_for("auth.login"))
    return render_template("auth/passwort_neu.html")


@bp.route("/portal-bald")
@login_required
def portal_bald():
    return render_template("auth/portal_bald.html")


# ------------------------------------------- Face ID / Fingerabdruck (20.11.2026) ---
# WebAuthn-Passkeys: Das Gerät (iPhone mit Face ID, Mac mit Touch ID, Android mit
# Fingerabdruck) hält den privaten Schlüssel, hier liegt nur der öffentliche.
# Für ALLE Konten; Nextcloud-Konten nur bis 90 Tage nach der letzten
# Nextcloud-Anmeldung (Entscheidung 20.11.2026, db.faceid_nc_abgelaufen).
# Die Challenge liegt einmalig und höchstens 5 Minuten in der (signierten) Sitzung.
PASSKEY_GUELTIG_S = 300


def _rp_id():
    return request.host.split(":")[0]


def _origin():
    # ProxyFix ist aktiv → scheme/host stammen aus X-Forwarded-Proto/-Host von Caddy
    return f"{request.scheme}://{request.host}"


def _client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()


def _sperr_kennung():
    return "passkey:" + _client_ip()


def _challenge_merken(zweck, challenge, uid=None):
    """Challenge serverseitig ablegen (einmalig einlösbar), in der Sitzung nur die Kennung."""
    from webauthn.helpers import bytes_to_base64url
    session["passkey_anfrage"] = db.passkey_anfrage_speichern(zweck, bytes_to_base64url(challenge), uid,
                                                              PASSKEY_GUELTIG_S)


def _challenge_einloesen(zweck, uid=None):
    """Challenge holen und sofort verwerfen (einmalig, max. 5 Minuten)."""
    from webauthn.helpers import base64url_to_bytes
    c = db.passkey_anfrage_einloesen(session.pop("passkey_anfrage", None), zweck, uid)
    if not c:
        return None
    try:
        return base64url_to_bytes(c)
    except Exception:  # noqa: BLE001
        return None


FRISCH_S = 600   # neues Gerät nur bis 10 Minuten nach einer echten Anmeldung


def _anmeldung_frisch():
    try:
        return datetime.now().timestamp() - float(session.get("auth_zeit") or 0) <= FRISCH_S
    except (TypeError, ValueError):
        return False


NEU_ANMELDEN = ("Zur Sicherheit: Ein neues Gerät lässt sich nur kurz nach einer Anmeldung einrichten. "
                "Bitte melde dich einmal ab und wieder an und tippe dann erneut auf „Einrichten“.")


def _geraet_mail(row, name):
    """Benachrichtigung „Neues Gerät für Face ID eingerichtet“ an die Konto-Adresse."""
    if not row or not (row["email"] or "").strip():
        return
    try:
        from .. import mail
        einst = db.get_einstellungen()
        vname = (einst["vereinsname"] if einst and einst.get("vereinsname") else "Vereinsverwaltung")
        mail.einzelmail(einst, row["email"], f"Neues Gerät für Face ID eingerichtet – {vname}",
                        f"Hallo {row['anzeigename'] or ''},\n\n"
                        f"für dein Konto wurde soeben ein neues Gerät zur Anmeldung mit Face ID bzw. "
                        f"Fingerabdruck eingerichtet: {name} ({datetime.now().strftime('%d.%m.%Y, %H:%M')} Uhr).\n\n"
                        "Warst du das? Dann ist alles in Ordnung.\n"
                        "Warst du das NICHT, entferne das Gerät bitte sofort in der App unter „Ich“ → "
                        "„Face ID / Fingerabdruck“ und sag dem Vorstand Bescheid.")
    except Exception:  # noqa: BLE001 – Mail darf das Einrichten nicht verhindern
        current_app.logger.info("[FACEID] Benachrichtigung konnte nicht gesendet werden")


def _passkey_einrichten_erlaubt():
    if session.get("gast_vorschau"):
        return "In der Gastansicht nicht möglich."
    if current_app.config.get("DEMO_LOGIN"):
        return "Im Demo-Verein nicht möglich."
    return None


def _geraet_name():
    ua = (request.headers.get("User-Agent") or "")
    for merkmal, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android-Gerät"),
                          ("Macintosh", "Mac"), ("Windows", "Windows-PC")):
        if merkmal in ua:
            return name
    return "Gerät"


@bp.route("/passkey/registrieren/optionen", methods=["POST"])
@login_required
def passkey_registrieren_optionen():
    import json
    import webauthn
    from webauthn.helpers import base64url_to_bytes, options_to_json
    from webauthn.helpers.structs import (AuthenticatorAttachment, AuthenticatorSelectionCriteria,
                                          PublicKeyCredentialDescriptor, ResidentKeyRequirement,
                                          UserVerificationRequirement)
    sperre = _passkey_einrichten_erlaubt()
    if sperre:
        return {"ok": False, "fehler": sperre}, 403
    if not _anmeldung_frisch():
        return {"ok": False, "neu_anmelden": True, "fehler": NEU_ANMELDEN}, 403
    uid = int(current_user.id)
    vorhandene = [r["credential_id"] for r in db.passkeys_liste(uid)]
    if len(vorhandene) >= db.PASSKEYS_JE_KONTO:
        return {"ok": False, "fehler": "Es sind schon sehr viele Geräte eingerichtet. Bitte erst eines entfernen."}, 400
    vid = session.get("verein_id") or current_user.verein_id
    verein = next((v["vereinsname"] for v in db.alle_vereine() if v["id"] == vid), "")
    optionen = webauthn.generate_registration_options(
        rp_id=_rp_id(), rp_name=verein or "Vereinsverwaltung",
        user_id=f"vv-{uid}".encode(), user_name=current_user.email or current_user.anzeigename,
        user_display_name=current_user.anzeigename,
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,   # das Gerät selbst
            resident_key=ResidentKeyRequirement.REQUIRED,               # Anmelden ohne Namen
            user_verification=UserVerificationRequirement.REQUIRED),     # Face ID/Fingerabdruck Pflicht
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(k)) for k in vorhandene])
    _challenge_merken("registrieren", optionen.challenge, uid)
    return {"ok": True, "optionen": json.loads(options_to_json(optionen))}


@bp.route("/passkey/registrieren", methods=["POST"])
@login_required
def passkey_registrieren():
    import webauthn
    from webauthn.helpers import bytes_to_base64url
    sperre = _passkey_einrichten_erlaubt()
    if sperre:
        return {"ok": False, "fehler": sperre}, 403
    if not _anmeldung_frisch():
        return {"ok": False, "neu_anmelden": True, "fehler": NEU_ANMELDEN}, 403
    uid = int(current_user.id)
    d = request.get_json(silent=True) or {}
    challenge = _challenge_einloesen("registrieren", uid)
    if challenge is None:
        return {"ok": False, "fehler": "Die Einrichtung ist abgelaufen. Bitte noch einmal starten."}, 400
    credential = d.get("credential")
    try:
        erg = webauthn.verify_registration_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=_rp_id(),
            expected_origin=_origin(), require_user_verification=True)
    except Exception as err:  # noqa: BLE001 – die Bibliothek wirft unterschiedliche Typen
        current_app.logger.info("[FACEID] Einrichtung abgelehnt: %s", err)
        return {"ok": False, "fehler": "Das Gerät konnte nicht eingerichtet werden. Bitte noch einmal versuchen."}, 400
    cred_id = bytes_to_base64url(erg.credential_id)
    if db.passkey_per_credential(cred_id):
        return {"ok": False, "fehler": "Dieses Gerät ist schon eingerichtet."}, 400
    transports = ""
    if isinstance(credential, dict):
        transports = ",".join(str(t)[:20] for t in ((credential.get("response") or {}).get("transports") or [])
                              if isinstance(t, str))[:100]
    name = str(d.get("name") or "").strip()[:60] or _geraet_name()
    db.passkey_anlegen(uid, cred_id, erg.credential_public_key, erg.sign_count, transports, name, _rp_id())
    current_app.logger.info("[FACEID] Gerät eingerichtet uid=%s name=%r", uid, name)
    _geraet_mail(db.benutzer_laden(uid), name)
    return {"ok": True, "name": name}


@bp.route("/passkey/<int:pid>/loeschen", methods=["POST"])
@login_required
def passkey_entfernen(pid):
    if not db.passkey_loeschen(pid, int(current_user.id)):
        flash("Dieses Gerät wurde nicht gefunden.", "fehler")
    else:
        flash("Das Gerät wurde entfernt.", "erfolg")
    return redirect(url_for("portal.profil") + "#faceid")


@bp.route("/passkey/login/optionen", methods=["POST"])
def passkey_login_optionen():
    import json
    import webauthn
    from webauthn.helpers import options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement
    if db.login_gesperrt(_sperr_kennung(), ""):
        return {"ok": False, "fehler": "Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen."}, 429
    optionen = webauthn.generate_authentication_options(
        rp_id=_rp_id(), user_verification=UserVerificationRequirement.REQUIRED)
    _challenge_merken("anmelden", optionen.challenge)
    return {"ok": True, "optionen": json.loads(options_to_json(optionen))}


@bp.route("/passkey/login", methods=["POST"])
def passkey_login():
    import webauthn
    fehler = "Die Anmeldung mit Face ID hat nicht geklappt. Bitte noch einmal versuchen oder anders anmelden."
    if db.login_gesperrt(_sperr_kennung(), ""):
        return {"ok": False, "fehler": "Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen."}, 429
    d = request.get_json(silent=True) or {}
    credential = d.get("credential")
    challenge = _challenge_einloesen("anmelden")
    cred_id = str(credential.get("id", ""))[:1024] if isinstance(credential, dict) else ""
    pk = db.passkey_per_credential(cred_id) if cred_id else None
    row = db.benutzer_laden(pk["benutzer_id"]) if pk else None
    erg = None
    if challenge is not None and pk is not None and row is not None and row["aktiv"]:
        try:
            erg = webauthn.verify_authentication_response(
                credential=credential, expected_challenge=challenge, expected_rp_id=_rp_id(),
                expected_origin=_origin(), credential_public_key=bytes(pk["public_key"]),
                credential_current_sign_count=pk["sign_count"] or 0, require_user_verification=True)
        except Exception as err:  # noqa: BLE001
            current_app.logger.info("[FACEID] Anmeldung abgelehnt: %s", err)
    if erg is None:
        # Nur echte Fehlversuche zählen (bekanntes Gerät, gültige Challenge, Prüfung gescheitert) –
        # „Gerät unbekannt“/„abgelaufen“ sperrt sonst ganze WLANs aus. Eigener Zähler je IP,
        # damit der Passwort-Login davon unberührt bleibt (Sicherheitsprüfung 21.11.2026).
        if challenge is not None and pk is not None:
            db.login_fehlversuch(_sperr_kennung(), "")
        return {"ok": False, "fehler": fehler}, 400
    if db.faceid_nc_abgelaufen(row):
        return {"ok": False, "nextcloud": True,
                "fehler": "Deine letzte Anmeldung über Nextcloud ist über 90 Tage her. Bitte melde dich "
                          "einmal über „Mit Nextcloud anmelden“ an – danach geht Face ID wieder."}, 403
    db.passkey_benutzt(pk["id"], erg.new_sign_count)
    session.pop("2fa_uid", None)          # Face ID mit Nutzerprüfung ersetzt Passwort + 2FA
    session["login_verein_id"] = row["verein_id"]
    session["login_merken"] = 0 if d.get("merken") in (False, 0, "0", "") else 1
    rest = db.faceid_nc_rest(row)
    _einloggen(row, dauer=(None if rest is None else max(rest, timedelta(minutes=5))))
    current_app.logger.info("[FACEID] Login OK uid=%s", row["id"])
    ziel = (url_for("dashboard.index") if row["rolle"] in ("admin", "schatz", "oeffentlichkeit")
            else url_for("portal.home"))
    weiter = str(d.get("weiter") or "")
    if weiter.startswith("/portal/") and not weiter.startswith("//") and "\\" not in weiter:
        ziel = weiter
    return {"ok": True, "weiter": ziel}
