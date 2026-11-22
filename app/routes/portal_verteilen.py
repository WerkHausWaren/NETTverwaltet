"""Mitglieder-App: Verteilaktionen (Modul „Verteilen") für Helfer:innen.

Die Routen hängen am Portal-Blueprint (Endpunkte `portal.verteilen_*`), damit
der zentrale Zugriffsschutz für Mitglieder und Freund:innen greift. Die
Fachregeln stehen in app/verteilen.py.

Standort: Die App sperrt Geolocation sonst überall (Permissions-Policy). Nur
die Verteil-Seiten erlauben sie für die eigene Herkunft – der Standort bleibt
im Browser, gespeichert wird höchstens der Startpunkt einer Route. Fußwege
fragt der Server bei FOSSGIS an (nur Koordinaten, keine IP der Geräte).
"""
from flask import abort, jsonify, make_response, redirect, render_template, request, url_for
from flask_login import current_user

from .. import speicher
from .. import verteilen as vt
from .. import verteilen_zustellung as vz
from ..db import aktiver_verein_id, get_db, get_einstellungen, now_iso
from .portal import FREUND_ERLAUBT, bp

# Freund:innen dürfen mitverteilen und Plakate aufhängen (14./16.11.2026) –
# sie sehen bei anderen nur den Vornamen.
FREUND_ERLAUBT.update({
    "verteilen_start", "verteilen_aktion", "verteilen_laufen", "verteilen_runde", "verteilen_state",
    "verteilen_status", "verteilen_route", "verteilen_route_spaeter", "verteilen_route_raus",
    "verteilen_route_beenden", "verteilen_foto_hoch", "verteilen_foto", "verteilen_flyer_bild",
    "verteilen_weg", "verteilen_plakat_neu", "verteilen_plakat_melden", "verteilen_plakat_bearbeiten",
    "verteilen_plakat_loeschen", "verteilen_zustellen", "verteilen_zust_uebernehmen", "verteilen_zust_setzen",
})

STANDORT_ERLAUBT = "geolocation=(self), microphone=(), camera=()"


def _pruefen():
    """Modul an + angemeldet, sonst 404 bzw. zum Login."""
    e = get_einstellungen()
    if not (e and e.get("modul_verteilen")):
        abort(404)
    if not getattr(current_user, "is_authenticated", False):
        return None
    return e


def _rolle():
    return getattr(current_user, "rolle", "") or ""


def _uid():
    return int(current_user.id)


def _aktion(conn, aid):
    a = vt.aktion_laden(conn, aktiver_verein_id(), aid)
    if not a:
        conn.close()
        abort(404)
    return a


def _json_fehler(text, status=409, belegt=None):
    return jsonify({"ok": False, "fehler": text, "belegt": belegt or []}), status


def _mit_standort(html):
    antwort = make_response(html)
    antwort.headers["Permissions-Policy"] = STANDORT_ERLAUBT
    return antwort


def _body():
    return request.get_json(silent=True) or {}


@bp.route("/verteilen")
def verteilen_start():
    if not _pruefen():
        return redirect(url_for("auth.login"))
    conn = get_db()
    vid = aktiver_verein_id()
    vt.loeschfrist_pruefen(conn, vid)
    karte = vt.startkarte(conn, vid, _uid())
    conn.close()
    if len(karte) == 1:
        return redirect(url_for("portal.verteilen_aktion", aid=karte[0]["id"]))
    return render_template("portal/verteilen_start.html", aktiv="home", aktionen=karte)


@bp.route("/verteilen/<int:aid>")
def verteilen_aktion(aid):
    if not _pruefen():
        return redirect(url_for("auth.login"))
    conn = get_db()
    a = _aktion(conn, aid)
    mats = vt.materialien(conn, a) if vt.hat_flyer(a) else []
    conn.close()
    return _mit_standort(render_template("portal/verteilen.html", aktiv="home", a=a, mats=mats,
                                         nur_vorname=_rolle() == "extern"))


@bp.route("/verteilen/<int:aid>/laufen")
def verteilen_laufen(aid):
    if not _pruefen():
        return redirect(url_for("auth.login"))
    conn = get_db()
    a = _aktion(conn, aid)
    conn.close()
    return _mit_standort(render_template("portal/verteilen_laufen.html", a=a, ansicht="laufen"))


@bp.route("/verteilen/<int:aid>/zustellen")
def verteilen_zustellen(aid):
    """Lauf-Ansicht für die eigenen übernommenen Zustellungen."""
    if not _pruefen():
        return redirect(url_for("auth.login"))
    conn = get_db()
    a = _aktion(conn, aid)
    conn.close()
    if not vz.hat_zustellungen(a):
        abort(404)
    return _mit_standort(render_template("portal/verteilen_laufen.html", a=a, ansicht="zustellen"))


@bp.route("/verteilen/<int:aid>/plakate/runde")
def verteilen_runde(aid):
    """Plakat-Runde: Kontrollieren/Abhängen, zu Fuß oder im Auto (Mitfahr-Modus)."""
    if not _pruefen():
        return redirect(url_for("auth.login"))
    conn = get_db()
    a = _aktion(conn, aid)
    conn.close()
    if not vt.hat_plakate(a):
        abort(404)
    return _mit_standort(render_template("portal/verteilen_laufen.html", a=a, ansicht="runde"))


@bp.route("/verteilen/flyer/<int:mid>/bild")
def verteilen_flyer_bild(mid):
    einst = _pruefen()
    if not einst:
        return redirect(url_for("auth.login"))
    conn = get_db()
    m = conn.execute("SELECT * FROM verteil_material WHERE id=? AND verein_id=?",
                     (mid, aktiver_verein_id())).fetchone()
    conn.close()
    if not m or not m["bild"]:
        abort(404)
    return speicher.sende(einst, "verteilen", m["bild"])


# ------------------------------------------------------------------ API ---
@bp.route("/verteilen/api/<int:aid>/state")
def verteilen_state(aid):
    if not _pruefen():
        return _json_fehler("Bitte zuerst anmelden.", 401)
    conn = get_db()
    vid = aktiver_verein_id()
    vt.loeschfrist_pruefen(conn, vid)
    a = _aktion(conn, aid)
    daten = vt.state(conn, vid, a, _uid(), _rolle())
    conn.close()
    antwort = jsonify(daten)
    antwort.headers["Cache-Control"] = "no-store"
    return antwort


@bp.route("/verteilen/api/<int:aid>/status", methods=["POST"])
def verteilen_status(aid):
    if not _pruefen():
        return _json_fehler("Bitte zuerst anmelden.", 401)
    b = _body()
    conn = get_db()
    a = _aktion(conn, aid)
    try:
        n = vt.status_setzen(conn, aktiver_verein_id(), a, int(b.get("strasse") or 0), str(b.get("status") or ""),
                             _uid(), leitung=_rolle() == "admin", nur_vorname=_rolle() == "extern",
                             materialien_ids=b.get("materialien") or None, nur_eigene=bool(b.get("nur_eigene")))
    except (vt.VerteilFehler, ValueError) as e:
        conn.close()
        return _json_fehler(getattr(e, "text", "Ungültige Angabe."))
    conn.close()
    return jsonify({"ok": True, "geaendert": n})


@bp.route("/verteilen/api/<int:aid>/route", methods=["POST"])
def verteilen_route(aid):
    if not _pruefen():
        return _json_fehler("Bitte zuerst anmelden.", 401)
    b = _body()
    conn = get_db()
    a = _aktion(conn, aid)
    try:
        erg = vt.route_setzen(conn, aktiver_verein_id(), a, _uid(), b.get("strassen") or [],
                              b.get("start"), nur_vorname=_rolle() == "extern",
                              materialien_ids=b.get("materialien") or None)
    except vt.VerteilFehler as e:
        conn.close()
        return _json_fehler(e.text, belegt=e.belegt)
    conn.close()
    return jsonify({"ok": True, **erg})


def _route_einzeln(aid, aktion_fn):
    if not _pruefen():
        return _json_fehler("Bitte zuerst anmelden.", 401)
    b = _body()
    conn = get_db()
    a = _aktion(conn, aid)
    try:
        aktion_fn(conn, a, _uid(), int(b.get("strasse") or 0))
    except (vt.VerteilFehler, ValueError) as e:
        conn.close()
        return _json_fehler(getattr(e, "text", "Ungültige Angabe."))
    conn.close()
    return jsonify({"ok": True})


@bp.route("/verteilen/api/<int:aid>/route/spaeter", methods=["POST"])
def verteilen_route_spaeter(aid):
    return _route_einzeln(aid, vt.route_spaeter)


@bp.route("/verteilen/api/<int:aid>/route/raus", methods=["POST"])
def verteilen_route_raus(aid):
    return _route_einzeln(aid, vt.route_raus)


@bp.route("/verteilen/api/<int:aid>/route/beenden", methods=["POST"])
def verteilen_route_beenden(aid):
    if not _pruefen():
        return _json_fehler("Bitte zuerst anmelden.", 401)
    b = _body()
    conn = get_db()
    a = _aktion(conn, aid)
    frei = vt.route_beenden(conn, a, _uid(), bool(b.get("freigeben")))
    conn.close()
    return jsonify({"ok": True, "freigegeben": frei})


@bp.route("/verteilen/api/<int:aid>/weg", methods=["POST"])
def verteilen_weg(aid):
    """Fußweg/Autoweg für die Linie auf der Karte (Server fragt FOSSGIS-OSRM)."""
    if not _pruefen():
        return _json_fehler("Bitte zuerst anmelden.", 401)
    b = _body()
    conn = get_db()
    _aktion(conn, aid)
    conn.close()
    try:
        erg = vt.weg_berechnen(b.get("punkte"), "auto" if b.get("profil") == "auto" else "fuss")
    except vt.VerteilFehler as e:
        return _json_fehler(e.text, 503)
    antwort = jsonify({"ok": True, **erg})
    antwort.headers["Cache-Control"] = "no-store"
    return antwort


@bp.route("/verteilen/api/<int:aid>/foto", methods=["POST"])
def verteilen_foto_hoch(aid):
    einst = _pruefen()
    if not einst:
        return _json_fehler("Bitte zuerst anmelden.", 401)
    conn = get_db()
    vid = aktiver_verein_id()
    a = _aktion(conn, aid)
    try:
        if not vt.aktion_offen(a):
            raise vt.VerteilFehler("Diese Verteilaktion ist abgeschlossen.")
        sid = request.form.get("strasse", type=int) or 0
        pid = request.form.get("plakat", type=int) or 0
        if pid:
            p = vt.plakat_laden(conn, a, pid)
            if not p:
                raise vt.VerteilFehler("Dieses Plakat gehört nicht zur Aktion.")
            name = f"aktion{aid}-plakat-{p['nr']}-{now_iso()[:19].replace(':', '').replace(' ', '-')}.jpg"
            sid = 0
        else:
            strasse = vt._strasse_der_aktion(conn, vid, a, sid)
            if not strasse:
                raise vt.VerteilFehler("Diese Straße gehört nicht zur Verteilaktion.")
            name = vt.foto_dateiname(aid, strasse)
        datei = request.files.get("foto")
        jpeg = vt.foto_aufbereiten(datei.read() if datei else b"")
        if speicher.speichern(einst, "verteilen", name, jpeg) is False:
            raise vt.VerteilFehler("Das Foto konnte nicht gespeichert werden – bitte später noch einmal.")
        fid = conn.execute("INSERT INTO verteil_fotos (verein_id, aktion_id, strasse_id, plakat_id, benutzer_id, "
                           "dateiname, erstellt_am) VALUES (?,?,?,?,?,?,?)",
                           (vid, aid, sid, pid, _uid(), name, now_iso())).lastrowid
        if pid:
            vt._verlauf(conn, p, _uid(), "Foto hochgeladen")
        conn.commit()
    except vt.VerteilFehler as e:
        conn.close()
        return _json_fehler(e.text, 400)
    conn.close()
    return jsonify({"ok": True, "foto": fid})


@bp.route("/verteilen/foto/<int:fid>")
def verteilen_foto(fid):
    einst = _pruefen()
    if not einst:
        return redirect(url_for("auth.login"))
    conn = get_db()
    f = conn.execute("SELECT * FROM verteil_fotos WHERE id=? AND verein_id=?",
                     (fid, aktiver_verein_id())).fetchone()
    conn.close()
    if not f:
        abort(404)
    return speicher.sende(einst, "verteilen", f["dateiname"])


# --------------------------------------------------------------- Plakate ---
@bp.route("/verteilen/api/<int:aid>/plakat", methods=["POST"])
def verteilen_plakat_neu(aid):
    if not _pruefen():
        return _json_fehler("Bitte zuerst anmelden.", 401)
    b = _body()
    conn = get_db()
    a = _aktion(conn, aid)
    try:
        erg = vt.plakat_anlegen(conn, aktiver_verein_id(), a, _uid(), b.get("lat"), b.get("lon"),
                                str(b.get("strasse") or ""), str(b.get("hinweis") or ""),
                                haengt=b.get("status") == "haengt")
    except vt.VerteilFehler as e:
        conn.close()
        return _json_fehler(e.text)
    conn.close()
    return jsonify({"ok": True, **erg})


@bp.route("/verteilen/api/<int:aid>/plakat/<int:pid>", methods=["POST"])
def verteilen_plakat_melden(aid, pid):
    einst = _pruefen()
    if not einst:
        return _json_fehler("Bitte zuerst anmelden.", 401)
    b = _body()
    conn = get_db()
    vid = aktiver_verein_id()
    a = _aktion(conn, aid)
    try:
        p, melden = vt.plakat_melden(conn, a, pid, str(b.get("meldung") or ""), _uid(),
                                     schaden=str(b.get("schaden") or ""), notiz=str(b.get("notiz") or ""))
    except vt.VerteilFehler as e:
        conn.close()
        return _json_fehler(e.text)
    conn.close()
    gemeldet = 0
    if melden:
        try:
            link = url_for("portal.verteilen_aktion", aid=aid, _external=True) + "#plakate"
            gemeldet = vt.schaden_melden(einst, vid, a, p, _uid(), link)
        except Exception:  # noqa: BLE001 – die Meldung selbst ist gespeichert
            vt.log.exception("Schadens-Mail konnte nicht vorbereitet werden")
    return jsonify({"ok": True, "status": p["status"], "nr": p["nr"], "vorstand_informiert": gemeldet})


@bp.route("/verteilen/api/<int:aid>/plakat/<int:pid>/bearbeiten", methods=["POST"])
def verteilen_plakat_bearbeiten(aid, pid):
    if not _pruefen():
        return _json_fehler("Bitte zuerst anmelden.", 401)
    b = _body()
    conn = get_db()
    a = _aktion(conn, aid)
    try:
        vt.plakat_bearbeiten(conn, a, pid, _uid(), b.get("strasse"), b.get("hinweis"))
    except vt.VerteilFehler as e:
        conn.close()
        return _json_fehler(e.text)
    conn.close()
    return jsonify({"ok": True})


@bp.route("/verteilen/api/<int:aid>/plakat/<int:pid>/loeschen", methods=["POST"])
def verteilen_plakat_loeschen(aid, pid):
    if not _pruefen():
        return _json_fehler("Bitte zuerst anmelden.", 401)
    conn = get_db()
    a = _aktion(conn, aid)
    try:
        vt.plakat_loeschen(conn, a, pid, _uid(), _rolle() == "admin")
    except vt.VerteilFehler as e:
        conn.close()
        return _json_fehler(e.text)
    conn.close()
    return jsonify({"ok": True})


# ---------------------------------------------------------- Zustellungen ---
def _zust_aktion(conn, aid):
    a = _aktion(conn, aid)
    if not vz.hat_zustellungen(a):
        conn.close()
        abort(404)
    return a


@bp.route("/verteilen/api/<int:aid>/zustellungen/uebernehmen", methods=["POST"])
def verteilen_zust_uebernehmen(aid):
    if not _pruefen():
        return _json_fehler("Bitte zuerst anmelden.", 401)
    b = _body()
    conn = get_db()
    a = _zust_aktion(conn, aid)
    try:
        n = vz.uebernehmen(conn, aktiver_verein_id(), a, _uid(), b.get("gruppen") or [])
    except vt.VerteilFehler as e:
        conn.close()
        return _json_fehler(e.text)
    conn.close()
    return jsonify({"ok": True, "uebernommen": n})


@bp.route("/verteilen/api/<int:aid>/zustellung/<int:zid>", methods=["POST"])
def verteilen_zust_setzen(aid, zid):
    if not _pruefen():
        return _json_fehler("Bitte zuerst anmelden.", 401)
    b = _body()
    conn = get_db()
    a = _zust_aktion(conn, aid)
    try:
        vz.setzen(conn, a, zid, str(b.get("status") or ""), _uid(), leitung=_rolle() == "admin",
                  grund=str(b.get("grund") or ""), grund_text=str(b.get("grund_text") or ""),
                  nur_vorname=_rolle() == "extern")
    except vt.VerteilFehler as e:
        conn.close()
        return _json_fehler(e.text)
    conn.close()
    return jsonify({"ok": True})


@bp.route("/verteilen/api/<int:aid>/zustellung/<int:zid>/punkt", methods=["POST"])
def verteilen_zust_punkt(aid, zid):
    """Nur die Leitung setzt einen fehlenden Kartenpunkt von Hand (nicht in FREUND_ERLAUBT)."""
    if not _pruefen():
        return _json_fehler("Bitte zuerst anmelden.", 401)
    if _rolle() != "admin":
        return _json_fehler("Den Kartenpunkt setzt nur die Leitung der Aktion.", 403)
    b = _body()
    conn = get_db()
    a = _zust_aktion(conn, aid)
    try:
        vz.punkt_setzen(conn, aktiver_verein_id(), a, zid, b.get("lat"), b.get("lon"))
    except vt.VerteilFehler as e:
        conn.close()
        return _json_fehler(e.text)
    conn.close()
    return jsonify({"ok": True})
