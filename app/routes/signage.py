"""Signage (21.11.2026, Stufe 1): Geräte-Seiten und Verwaltung.

Geräte (ohne Login, über den Gerätezugang im Cookie):
  /signage/            – EINE Adresse für jedes Gerät: Kopplungscode, danach Aufsteller
                         bzw. Schaufenster (Rolle legt die Verwaltung fest)
  /signage/folien      – Folien des Schaufensters (JSON, zugleich „lebt noch“)
  /signage/bild/…      – Bilder öffentlicher Termine/Kategorien, Vereinslogo

Verwaltung (Vorstand + Öffentlichkeitsarbeit, Modul „Signage“ muss an sein):
  /signage/verwaltung  – Geräte koppeln, einstellen, Vorschau, neu laden, entfernen
"""
import json
import os
from datetime import datetime

from flask import (Blueprint, abort, flash, g, jsonify, make_response, redirect, render_template,
                   request, send_from_directory, session, url_for)
from flask_login import current_user

from .. import db, seo, signage, termin_maske
from ..db import aktiver_verein_id, get_db, get_einstellungen, now_iso

bp = Blueprint("signage", __name__, url_prefix="/signage")

COOKIE = "vv_signage"
COOKIE_KOPPLUNG = "vv_signage_k"
GERAETE_SEITEN = {"signage.start", "signage.folien", "signage.bild", "signage.logo"}


# ------------------------------------------------------------ Hilfen ---
def _modul_an(vid):
    if vid is None:
        return False
    conn = db.get_db_fuer(vid)
    try:
        r = conn.execute("SELECT modul_signage FROM vereine WHERE id=?", (vid,)).fetchone()
    finally:
        conn.close()
    return bool(r and r["modul_signage"])


def _ip():
    return (request.access_route[0] if request.access_route else request.remote_addr) or ""


def _cookie_setzen(antwort, name, wert, tage):
    antwort.set_cookie(name, wert, max_age=tage * 86400, httponly=True, secure=request.is_secure,
                       samesite="Lax", path="/signage")


def _geraet():
    """Gerät dieses Aufrufs (Cookie); setzt den Verein für die Anfrage."""
    gr = signage.geraet_per_token(request.cookies.get(COOKIE))
    if gr:
        g.verein_id = gr["verein_id"]
    return gr


def _ohne_index(antwort):
    antwort.headers["X-Robots-Tag"] = "noindex, nofollow"
    antwort.headers["Cache-Control"] = "no-store"
    return antwort


def _marke(vid, einst=None):
    return signage.marke(vid, dunkel=bool(einst and einst.get("erscheinungsbild") == "dunkel"))


def _static_dir():
    from flask import current_app
    return current_app.static_folder


def _url_fuer(art, **kw):
    if art == "anmelden":
        return url_for("veranstaltungen.oeffentlich", token=kw["token"], _external=True)
    if art == "termin":
        return seo.termin_url(kw["t"], extern=True)
    if art == "bild":
        return url_for("signage.bild", art=kw["bildart"], bid=kw["id"], v=kw.get("v") or "")
    if art == "mitglied":
        return url_for("mitglieder.antrag_public", _external=True)
    if art == "ueber":
        return url_for("portal.ueber", _external=True)
    if art == "sprache":
        from .. import sprache as _sprache
        if kw.get("code") in _sprache.SPRACHEN:
            return url_for("sprache_setzen", code=kw["code"], _external=True)
        return url_for("portal.termine", _external=True)
    if art == "lak_logo":
        datei = os.path.basename(kw.get("datei") or "")
        return url_for("static", filename="signage/" + datei) \
            if datei and os.path.exists(os.path.join(_static_dir(), "signage", datei)) else ""
    if art == "lak":
        return url_for("adventskalender_kurz", _external=True)
    return url_for("portal.termine", _external=True)


def _inhalt(gr, vorschau=False):
    """Seite für ein gekoppeltes Gerät (bzw. die Vorschau in der Verwaltung)."""
    einst = signage.einstellungen(gr)
    vid = gr["verein_id"]
    m = _marke(vid, einst)
    if not vorschau and not _modul_an(vid):
        return render_template("signage/aus.html", grund="modul", m=m)
    if gr["rolle"] == "checkin":
        if vorschau:
            urls = {"stand_url": url_for("signage.vorschau_checkin_stand", gid=gr["id"]),
                    "pruefen_url": url_for("signage.vorschau_checkin_pruefen", gid=gr["id"])}
        else:
            urls = {"stand_url": url_for("signage.checkin_stand"), "pruefen_url": url_for("signage.checkin_pruefen")}
        antwort = make_response(render_template("signage/checkin.html", einst=einst, vorschau=vorschau, m=m,
                                                jetzt=datetime.now(), **urls))
        # Kamera nur hier freigeben (app/__init__.py sperrt sie sonst überall – Chromium verweigert dann den Scanner)
        antwort.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=(self)"
        return antwort
    if gr["rolle"] == "tuer":
        an = vorschau or signage.im_zeitfenster(einst)
        conn = db.get_db_fuer(vid)
        try:
            schild = signage.tuerschild(conn, vid, einst)
        finally:
            conn.close()
        return render_template("signage/tuer.html", schild=schild, einst=einst, an=an, vorschau=vorschau,
                               m=m, jetzt=datetime.now())
    folien_url = url_for("signage.vorschau_folien", gid=gr["id"]) if vorschau else url_for("signage.folien")
    return render_template("signage/schaufenster.html", einst=einst, vorschau=vorschau, folien_url=folien_url,
                           m=m, geraet_name=gr["name"], jetzt=datetime.now())


def _folien_json(gr, vorschau=False):
    einst = signage.einstellungen(gr)
    vid = gr["verein_id"]
    conn = db.get_db_fuer(vid)
    try:
        folien = signage.schaufenster_folien(conn, vid, einst, _url_fuer, geraet_id=gr["id"])
        for fo in folien:
            if fo.get("typ") == "feiertag" and fo.get("foto"):
                fo["foto"]["url"] = url_for("static", filename="signage/feiertage/" + fo["foto"]["datei"])
    finally:
        conn.close()
    if not folien:
        folien = [{"typ": "willkommen", "titel": "Herzlich willkommen", "text": "Schön, dass du da bist!", "dauer": 20}]
    return {"folien": folien, "an": vorschau or signage.im_zeitfenster(einst),
            "modul": vorschau or _modul_an(vid), "stand": datetime.now().strftime("%H:%M")}


# --------------------------------------------------------- Geräte ---
def _kennung():
    """Browser-Kennung des Geräts; die Geräteseiten setzen vv_touch=1 bei Touchscreen (iPad ≙ „Mac“)."""
    ua = request.headers.get("User-Agent", "")[:150]
    return ua + (" [touch]" if request.cookies.get("vv_touch") == "1" else "")


@bp.route("/", strict_slashes=False)   # auch „/signage“ ohne Schrägstrich (sonst Umweg übers Portal)
def start():
    """Die eine Adresse für alle Geräte."""
    gr = _geraet()
    if gr:
        signage.kontakt_merken(gr, _kennung())
        return _ohne_index(make_response(_inhalt(gr)))
    geheim = request.cookies.get(COOKIE_KOPPLUNG)
    k = signage.kopplung_laden(geheim)
    if k and k["geraet_id"]:
        token = signage.kopplung_einloesen(geheim)
        if token:
            antwort = redirect(url_for("signage.start"))
            _cookie_setzen(antwort, COOKIE, token, 3650)
            antwort.delete_cookie(COOKIE_KOPPLUNG, path="/signage")
            return _ohne_index(antwort)
        k = None
    neu = None
    if not k:
        neu = signage.kopplung_anlegen(_ip())
        if not neu:
            return _ohne_index(make_response(render_template("signage/aus.html", grund="zu_viele", m=signage.marke_leer()), 429))
        code, geheim = neu
    else:
        code = k["code"]
    vid = aktiver_verein_id()
    koppel_url = url_for("signage.verwaltung", code=code, _external=True, _anchor="koppeln")
    antwort = make_response(render_template(
        "signage/start.html", gruppen=signage.code_gruppen(code), koppel_qr=signage._qr(koppel_url),
        verwaltung_url=url_for("signage.verwaltung", _external=True),
        m=_marke(vid) if vid is not None else signage.marke_leer()))
    if neu:
        _cookie_setzen(antwort, COOKIE_KOPPLUNG, geheim, 1)
    return _ohne_index(antwort)


def _checkin_stand_json(gr):
    from flask_wtf.csrf import generate_csrf
    conn = db.get_db_fuer(gr["verein_id"])
    try:
        daten = signage.checkin_stand(conn, gr["verein_id"], signage.einstellungen(gr))
    finally:
        conn.close()
    daten["csrf"] = generate_csrf()      # frisches Token – das Gerät läuft stundenlang ohne Neuladen
    return daten


def _checkin_pruefen_json(gr, nur_pruefen):
    code = ((request.get_json(silent=True) or {}).get("code") or "")[:500]
    if not signage.checkin_takt_ok(gr["id"]):
        return make_response(jsonify({"status": "gelb", "titel": "Kurz durchatmen …",
                                      "text": "Bitte in ein paar Sekunden noch einmal."}), 429)
    conn = db.get_db_fuer(gr["verein_id"])
    try:
        erg = signage.checkin_pruefen(conn, gr["verein_id"], signage.einstellungen(gr), code,
                                      geraet_name=gr["name"] or "", nur_pruefen=nur_pruefen)
    finally:
        conn.close()
    return jsonify(erg)


@bp.route("/checkin/stand")
def checkin_stand():
    gr = _geraet()
    if not gr:
        return _ohne_index(jsonify({"neu_laden": True})), 401
    neu_laden = signage.kontakt_merken(gr, _kennung())
    if gr["rolle"] != "checkin" or not _modul_an(gr["verein_id"]):
        return _ohne_index(jsonify({"neu_laden": True}))
    daten = _checkin_stand_json(gr)
    daten["neu_laden"] = neu_laden
    return _ohne_index(jsonify(daten))


@bp.route("/checkin/pruefen", methods=["POST"])
def checkin_pruefen():
    gr = _geraet()
    if not gr or gr["rolle"] != "checkin" or not _modul_an(gr["verein_id"]):
        return jsonify({"status": "rot", "titel": "Gerät nicht bereit", "text": "Bitte am Einlass melden."}), 401
    return _ohne_index(_checkin_pruefen_json(gr, nur_pruefen=False))


@bp.route("/<int:gid>/vorschau/checkin/stand")
def vorschau_checkin_stand(gid):
    gr = signage.geraet_laden(gid, aktiver_verein_id())
    if not gr or gr["rolle"] != "checkin":
        abort(404)
    return jsonify(_checkin_stand_json(gr))


@bp.route("/<int:gid>/vorschau/checkin/pruefen", methods=["POST"])
def vorschau_checkin_pruefen(gid):
    """Vorschau in der Verwaltung: prüft echte Tickets, checkt aber niemanden ein."""
    gr = signage.geraet_laden(gid, aktiver_verein_id())
    if not gr or gr["rolle"] != "checkin":
        abort(404)
    return _checkin_pruefen_json(gr, nur_pruefen=True)


@bp.route("/folien")
def folien():
    gr = _geraet()
    if not gr:
        return _ohne_index(jsonify({"neu_laden": True})), 401
    neu_laden = signage.kontakt_merken(gr, _kennung())
    if gr["rolle"] != "schaufenster":
        return _ohne_index(jsonify({"neu_laden": True}))
    daten = _folien_json(gr)
    daten["neu_laden"] = neu_laden
    return _ohne_index(jsonify(daten))


def _vid_fuer_bilder():
    gr = _geraet()
    if gr:
        return gr["verein_id"]
    if getattr(current_user, "is_authenticated", False):
        return session.get("verein_id") or getattr(current_user, "verein_id", None) or aktiver_verein_id()
    return None


@bp.route("/bild/<art>/<int:bid>")
def bild(art, bid):
    """Nur Bilder ÖFFENTLICHER Termine bzw. von Kategorien mit öffentlichen Terminen."""
    vid = _vid_fuer_bilder()
    if vid is None:
        abort(404)
    g.verein_id = vid
    from .termine import _event_bild_dir
    conn = db.get_db_fuer(vid)
    try:
        if art == "termin":
            t = conn.execute("SELECT t.bild, t.nur_mitglieder, k.name AS kategorie FROM termine t "
                             "LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
                             "WHERE t.id=? AND t.verein_id=? AND t.aktiv=1", (bid, vid)).fetchone()
            datei = t["bild"] if t and not (termin_maske.ist_intern(t) or termin_maske.ist_nicht_oeffentlich(t)) else None
        elif art == "kategorie":
            k = conn.execute("SELECT name, bild FROM termin_kategorien WHERE id=? AND verein_id=?", (bid, vid)).fetchone()
            offen = bool(k and not termin_maske.ist_nicht_oeffentlich({"kategorie": k["name"]})
                         and conn.execute("SELECT 1 FROM termine WHERE verein_id=? AND kategorie_id=? AND aktiv=1 "
                                          "AND COALESCE(nur_mitglieder,0)=0 LIMIT 1", (vid, bid)).fetchone())
            datei = k["bild"] if offen else None
        elif art == "foto":
            from .. import termin_entdecken
            from .portal import _galerie_dir
            fo = termin_entdecken.foto_oeffentlich_erlaubt(conn, bid, vid)
            if fo:
                return send_from_directory(_galerie_dir(), fo["dateiname"], max_age=86400)
            datei = None
        elif art == "mieten":
            r = conn.execute("SELECT signage_mieten_bild FROM vereine WHERE id=?", (vid,)).fetchone()
            if r and r["signage_mieten_bild"]:
                return send_from_directory(_folien_dir(), r["signage_mieten_bild"], max_age=86400)
            datei = None
        elif art == "folie":
            r = conn.execute("SELECT bild FROM signage_folien WHERE id=? AND verein_id=? AND typ='bild'",
                             (bid, vid)).fetchone()
            if r and r["bild"]:
                return send_from_directory(_folien_dir(), r["bild"], max_age=86400)
            datei = None
        elif art == "sponsor":
            t = conn.execute("SELECT t.sponsor_logo, t.nur_mitglieder, k.name AS kategorie FROM termine t "
                             "LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
                             "WHERE t.id=? AND t.verein_id=? AND t.aktiv=1", (bid, vid)).fetchone()
            datei = t["sponsor_logo"] if t and not (termin_maske.ist_intern(t) or termin_maske.ist_nicht_oeffentlich(t)) else None
            if datei:
                from .einstellungen import _bild_ohne_skript
                from .termine import _sponsor_dir
                return _bild_ohne_skript(send_from_directory(_sponsor_dir(), datei, max_age=86400))
        else:
            datei = None
    finally:
        conn.close()
    if not datei:
        abort(404)
    return send_from_directory(_event_bild_dir(), datei, max_age=86400)


@bp.route("/logo")
def logo():
    vid = _vid_fuer_bilder()
    if vid is None:
        vid = aktiver_verein_id()      # Kopplungsseite: Logo ist eine öffentliche Grafik (wie portal.logo)
    if vid is None:
        abort(404)
    g.verein_id = vid
    einst = get_einstellungen()
    if not einst or not einst["logo_dateiname"]:
        abort(404)
    from .einstellungen import LOGO_DIR, _bild_ohne_skript
    return _bild_ohne_skript(send_from_directory(LOGO_DIR, einst["logo_dateiname"], max_age=86400))


# ------------------------------------------------------ Verwaltung ---
@bp.before_request
def _modul_gate():
    if request.endpoint in GERAETE_SEITEN:
        return None
    if not _modul_an(aktiver_verein_id()):
        if request.method == "POST":
            abort(404)
        flash("Das Modul „Signage“ ist für diesen Verein nicht eingeschaltet (Einstellungen › Module).", "fehler")
        return redirect(url_for("dashboard.index"))
    return None


def _orte(vid):
    conn = get_db()
    try:
        rows = conn.execute("SELECT ort, COUNT(*) n FROM termine WHERE verein_id=? AND COALESCE(ort,'')<>'' "
                            "GROUP BY ort ORDER BY n DESC LIMIT 40", (vid,)).fetchall()
    finally:
        conn.close()
    return [r["ort"] for r in rows]


@bp.route("/verwaltung")
def verwaltung():
    vid = aktiver_verein_id()
    geraete = []
    for gr in signage.geraete_liste(vid):
        laeuft, text = signage.status_text(gr)
        typ, typ_name = signage.geraet_typ(gr["kontakt_info"])
        geraete.append({"g": gr, "einst": signage.einstellungen(gr), "laeuft": laeuft, "status": text,
                        "typ": typ, "typ_name": typ_name})
    start_url = url_for("signage.start", _external=True)
    conn = get_db()
    try:
        folien = conn.execute("SELECT * FROM signage_folien WHERE verein_id=? ORDER BY sortierung, id", (vid,)).fetchall()
        verein = dict(conn.execute("SELECT * FROM vereine WHERE id=?", (vid,)).fetchone() or {})
        plan = signage.plan_laden(conn, vid, verein)
    finally:
        conn.close()
    namen = {e["g"]["id"]: e["g"]["name"] or "Gerät" for e in geraete}
    heute_iso = datetime.now().date().isoformat()
    vorbelegt = signage.code_normalisieren(request.args.get("code")) or ""
    return render_template("signage/verwaltung.html", aktiv="oea", geraete=geraete, rollen=signage.ROLLEN,
                           erscheinungsbilder=signage.ERSCHEINUNGSBILDER, folien=folien, geraete_namen=namen,
                           heute_iso=heute_iso, hintergruende=dict(signage.HINTERGRUENDE),
                           code_vorbelegt=" ".join(signage.code_gruppen(vorbelegt)).strip() if vorbelegt else "",
                           name_vorbelegt=request.args.get("name", "")[:60],
                           plan=plan, ablaeufe=signage.ABLAEUFE, ablauf=verein.get("signage_ablauf") or "streuen",
                           rollen_bald=signage.ROLLEN_BALD, rollen_erklaert=signage.ROLLEN_ERKLAERT,
                           folien_je_id={f["id"]: f for f in folien},
                           orte=_orte(vid), start_url=start_url,
                           start_qr=signage._qr(start_url))


@bp.route("/koppeln", methods=["POST"])
def koppeln():
    gid, fehler = signage.geraet_koppeln(request.form.get("code"), aktiver_verein_id(),
                                         request.form.get("name"), request.form.get("rolle"))
    if fehler:
        flash(fehler, "fehler")
        return redirect(url_for("signage.verwaltung", code=request.form.get("code", "")[:20],
                                name=request.form.get("name", "")[:60], _anchor="koppeln"))
    else:
        flash("Gerät gekoppelt – es zeigt in wenigen Sekunden seine Inhalte. Anpassen kannst du es über ⚙️ Einstellungen.",
              "erfolg")
    return redirect(url_for("signage.verwaltung") + (f"#geraet-{gid}" if gid else ""))


def _zeit(wert):
    wert = (wert or "").strip()
    try:
        return datetime.strptime(wert, "%H:%M").strftime("%H:%M")
    except ValueError:
        return ""


def _zahl(wert, standard, lo, hi):
    try:
        return max(lo, min(hi, int(wert)))
    except (TypeError, ValueError):
        return standard


@bp.route("/<int:gid>/speichern", methods=["POST"])
def speichern(gid):
    vid = aktiver_verein_id()
    gr = signage.geraet_laden(gid, vid)
    if not gr:
        abort(404)
    rolle = request.form.get("rolle") if request.form.get("rolle") in signage.ROLLEN else gr["rolle"]
    f = request.form
    bild = "dunkel" if f.get("erscheinungsbild") == "dunkel" else "hell"
    if rolle == "tuer":
        einst = {"ort": f.get("ort", "").strip()[:80], "raumname": f.get("raumname", "").strip()[:60],
                 "von": _zeit(f.get("von")), "bis": _zeit(f.get("bis")), "erscheinungsbild": bild}
    elif rolle == "checkin":
        einst = {"ort": f.get("ort", "").strip()[:80], "von": _zeit(f.get("von")), "bis": _zeit(f.get("bis")),
                 "erscheinungsbild": bild, "kamera": "environment" if f.get("kamera") == "environment" else "user"}
    else:
        # Welche Folien in welcher Reihenfolge laufen, steht im Folienplan (für alle Schaufenster gleich)
        einst = {"tage": _zahl(f.get("tage"), 14, 1, 60), "max_termine": _zahl(f.get("max_termine"), 10, 1, 30),
                 "von": _zeit(f.get("von")), "bis": _zeit(f.get("bis")), "erscheinungsbild": bild}
    signage.geraet_aendern(gid, vid, name=(f.get("name") or gr["name"]).strip()[:60], rolle=rolle,
                           einstellungen=json.dumps(einst), neu_laden=1)
    flash("Gespeichert – das Gerät übernimmt die Änderung innerhalb einer Minute.", "erfolg")
    return redirect(url_for("signage.verwaltung") + f"#geraet-{gid}")


@bp.route("/<int:gid>/neu-laden", methods=["POST"])
def neu_laden(gid):
    if not signage.geraet_aendern(gid, aktiver_verein_id(), neu_laden=1):
        abort(404)
    flash("Das Gerät lädt sich beim nächsten Kontakt neu (spätestens in einer Minute).", "erfolg")
    return redirect(url_for("signage.verwaltung") + f"#geraet-{gid}")


@bp.route("/<int:gid>/entfernen", methods=["POST"])
def entfernen(gid):
    if not signage.geraet_entfernen(gid, aktiver_verein_id()):
        abort(404)
    flash("Gerät entfernt. Es zeigt jetzt wieder einen Kopplungscode.", "erfolg")
    return redirect(url_for("signage.verwaltung"))


@bp.route("/<int:gid>/vorschau")
def vorschau(gid):
    gr = signage.geraet_laden(gid, aktiver_verein_id())
    if not gr:
        abort(404)
    return _ohne_index(make_response(_inhalt(gr, vorschau=True)))


@bp.route("/<int:gid>/vorschau/folien")
def vorschau_folien(gid):
    gr = signage.geraet_laden(gid, aktiver_verein_id())
    if not gr:
        abort(404)
    daten = _folien_json(gr, vorschau=True)
    daten["neu_laden"] = False
    return _ohne_index(jsonify(daten))


# --------------------------------------------------- eigene Folien ---
def _folien_dir():
    from .einstellungen import LOGO_DIR
    p = os.path.join(os.path.dirname(LOGO_DIR), "signage_folien")
    os.makedirs(p, exist_ok=True)
    return p


BILD_ENDUNGEN = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _folie_bild_ablegen(datei, vid, fid):
    """Hochgeladenes Bild prüfen, drehen (Handyfotos), auf max. 1920 px verkleinern, als JPEG ablegen.
    Liefert (dateiname, fehler)."""
    import io
    import time
    from PIL import Image, ImageOps
    if not datei or not datei.filename:
        return None, None
    if not datei.filename.lower().endswith(BILD_ENDUNGEN):
        return None, "Bitte ein Bild als JPG, PNG, WEBP oder GIF hochladen (z. B. aus Canva als PNG exportiert)."
    try:
        im = Image.open(io.BytesIO(datei.read()))
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            grund = Image.new("RGB", im.size, (255, 255, 255))
            grund.paste(im, mask=im.split()[-1])
            im = grund
        else:
            im = im.convert("RGB")
        im.thumbnail((1920, 1920))
    except Exception:  # noqa: BLE001
        return None, "Das Bild ließ sich nicht öffnen. Bitte als JPG oder PNG speichern und erneut hochladen."
    ordner = _folien_dir()
    for alt in os.listdir(ordner):
        if alt.startswith(f"folie_v{vid}_{fid}_"):
            try:
                os.remove(os.path.join(ordner, alt))
            except OSError:
                pass
    name = f"folie_v{vid}_{fid}_{int(time.time())}.jpg"
    im.save(os.path.join(ordner, name), "JPEG", quality=88)
    return name, None


def _datum(wert):
    try:
        return datetime.strptime((wert or "").strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _folie_laden(conn, fid, vid):
    r = conn.execute("SELECT * FROM signage_folien WHERE id=? AND verein_id=?", (fid, vid)).fetchone()
    if not r:
        abort(404)
    return r


@bp.route("/folie/neu", methods=["GET", "POST"])
@bp.route("/folie/<int:fid>", methods=["GET", "POST"])
def folie(fid=None):
    vid = aktiver_verein_id()
    conn = get_db()
    try:
        r = _folie_laden(conn, fid, vid) if fid else None
        if r:
            typ = r["typ"]
        else:
            typ = request.values.get("typ") if request.values.get("typ") in ("bild", "text") else "text"
        geraete = [g for g in signage.geraete_liste(vid) if g["rolle"] == "schaufenster"]
        if request.method == "POST":
            f = request.form
            werte = {
                "titel": f.get("titel", "").strip()[:120], "text": f.get("text", "").strip()[:600],
                "link": signage.sichere_link(f.get("link")),
                "hintergrund": f.get("hintergrund") if f.get("hintergrund") in ("hell", "farbe", "dunkel") else "hell",
                "einpassen": "fuellen" if f.get("einpassen") == "fuellen" else "ganz",
                "dauer": _zahl(f.get("dauer"), 0, 0, 120), "von": _datum(f.get("von")), "bis": _datum(f.get("bis")),
                "geraete": ",".join(str(g["id"]) for g in geraete if str(g["id"]) in f.getlist("geraete")),
                "aktiv": 1 if f.get("aktiv") else 0,
            }
            vorher_bild = r["bild"] if r else ""
            fehler = None
            if f.get("link", "").strip() and not werte["link"]:
                fehler = "Der Link muss mit https:// beginnen."
            if typ == "text" and not (werte["titel"] or werte["text"]):
                fehler = "Bitte eine Überschrift oder einen Text eingeben."
            if not fehler and not r:
                n = conn.execute("SELECT COALESCE(MAX(sortierung),0)+1 FROM signage_folien WHERE verein_id=?", (vid,)).fetchone()[0]
                fid = conn.execute("INSERT INTO signage_folien (verein_id, typ, sortierung, erstellt_am) VALUES (?,?,?,?)",
                                   (vid, typ, n, now_iso())).lastrowid
            if not fehler and typ == "bild":
                name, bfehler = _folie_bild_ablegen(request.files.get("bild"), vid, fid)
                if name:
                    werte["bild"] = name
                if typ == "bild" and not name and not (r and r["bild"]):
                    fehler = bfehler or "Bitte ein Bild auswählen."
                    if not r:
                        conn.execute("DELETE FROM signage_folien WHERE id=?", (fid,))
                        fid = None
                elif bfehler:
                    fehler = bfehler
            if fehler:
                conn.commit()
                flash(fehler, "fehler")
                return render_template("signage/folie.html", aktiv="oea", r=r, typ=typ,
                                       werte=dict(werte, bild=vorher_bild), geraete=geraete,
                                       hintergruende=signage.HINTERGRUENDE)
            werte["geaendert_am"] = now_iso()
            conn.execute(f"UPDATE signage_folien SET {', '.join(k + '=?' for k in werte)} WHERE id=? AND verein_id=?",
                         list(werte.values()) + [fid, vid])
            conn.commit()
            flash("Folie gespeichert – die Bildschirme zeigen sie innerhalb einer Minute.", "erfolg")
            return redirect(url_for("signage.verwaltung") + "#folien")
        werte = dict(r) if r else {"titel": "", "text": "", "link": "", "hintergrund": "hell", "einpassen": "ganz",
                                   "dauer": 0, "von": "", "bis": "", "geraete": "", "aktiv": 1, "bild": ""}
        return render_template("signage/folie.html", aktiv="oea", r=r, typ=typ, werte=werte, geraete=geraete,
                               hintergruende=signage.HINTERGRUENDE)
    finally:
        conn.close()


@bp.route("/plan", methods=["POST"])
def plan_speichern():
    """Folienplan: Reihenfolge (Ziehen/Pfeile), Auge je Folie, Dauer je Folie, Ablauf."""
    vid = aktiver_verein_id()
    f = request.form
    reihe = [k.strip() for k in (f.get("reihenfolge") or "").split(",") if k.strip()]
    dauern = {k[len("dauer__"):]: f.get(k) for k in f if k.startswith("dauer__")}
    conn = get_db()
    try:
        signage.plan_speichern(conn, vid, reihe, set(f.getlist("an")), dauern)
        ablauf = f.get("ablauf")
        if ablauf in {a for a, _ in signage.ABLAEUFE}:
            conn.execute("UPDATE vereine SET signage_ablauf=? WHERE id=?", (ablauf, vid))
            conn.commit()
    finally:
        conn.close()
    flash("Folienplan gespeichert – die Schaufenster übernehmen ihn innerhalb einer Minute.", "erfolg")
    return redirect(url_for("signage.verwaltung") + "#folien")


@bp.route("/folie/<int:fid>/<aktion>", methods=["POST"])
def folie_aktion(fid, aktion):
    vid = aktiver_verein_id()
    conn = get_db()
    try:
        r = _folie_laden(conn, fid, vid)
        if aktion == "loeschen":
            conn.execute("DELETE FROM signage_folien WHERE id=?", (fid,))
            conn.execute("DELETE FROM signage_plan WHERE verein_id=? AND schluessel=?", (vid, f"folie:{fid}"))
            if r["bild"]:
                try:
                    os.remove(os.path.join(_folien_dir(), r["bild"]))
                except OSError:
                    pass
            flash("Folie gelöscht.", "erfolg")
        elif aktion == "umschalten":
            conn.execute("UPDATE signage_folien SET aktiv=? WHERE id=?", (0 if r["aktiv"] else 1, fid))
        elif aktion in ("hoch", "runter"):
            alle = [x["id"] for x in conn.execute("SELECT id FROM signage_folien WHERE verein_id=? ORDER BY sortierung, id",
                                                   (vid,)).fetchall()]
            i = alle.index(fid)
            j = i - 1 if aktion == "hoch" else i + 1
            if 0 <= j < len(alle):
                alle[i], alle[j] = alle[j], alle[i]
            for pos, x in enumerate(alle, 1):
                conn.execute("UPDATE signage_folien SET sortierung=? WHERE id=?", (pos, x))
        else:
            abort(404)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("signage.verwaltung") + "#folien")


@bp.route("/folie/<int:fid>/vorschau")
def folie_vorschau(fid):
    """Eine einzelne eigene Folie so, wie sie auf dem Schaufenster erscheint."""
    vid = aktiver_verein_id()
    conn = get_db()
    try:
        _folie_laden(conn, fid, vid)
    finally:
        conn.close()
    m = _marke(vid, {"erscheinungsbild": request.args.get("bild", "hell")})
    return _ohne_index(make_response(render_template(
        "signage/schaufenster.html", einst={}, vorschau=True, m=m, geraet_name="Vorschau", jetzt=datetime.now(),
        folien_url=url_for("signage.folie_vorschau_json", fid=fid))))


@bp.route("/folie/<int:fid>/vorschau.json")
def folie_vorschau_json(fid):
    vid = aktiver_verein_id()
    conn = get_db()
    try:
        _folie_laden(conn, fid, vid)
        folien = signage.eigene_folien(conn, vid, None, datetime.now().date(), _url_fuer, 30, nur_id=fid)
    finally:
        conn.close()
    return _ohne_index(jsonify({"folien": folien, "an": True, "modul": True, "neu_laden": False}))


def _einbettbar(antwort):
    """Vorschaubilder im Folienplan sind iFrames derselben Seite – nur dafür Einbetten im eigenen Haus erlauben."""
    antwort.headers["X-Frame-Options"] = "SAMEORIGIN"
    antwort.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'")
    return antwort


@bp.route("/plan/vorschau")
def plan_vorschau():
    """Eine Folienart des Plans so, wie sie auf dem Schaufenster erscheint (standbild=1: kleines Vorschaubild)."""
    vid = aktiver_verein_id()
    k = request.args.get("k", "")
    conn = get_db()
    try:
        if k not in {e["schluessel"] for e in signage.plan_laden(conn, vid)}:
            abort(404)
    finally:
        conn.close()
    m = _marke(vid, {"erscheinungsbild": request.args.get("bild", "hell")})
    return _einbettbar(_ohne_index(make_response(render_template(
        "signage/schaufenster.html", einst={}, vorschau=True, m=m, geraet_name="Vorschau", jetzt=datetime.now(),
        standbild=request.args.get("standbild") == "1",
        folien_url=url_for("signage.plan_vorschau_json", k=k)))))


@bp.route("/plan/vorschau.json")
def plan_vorschau_json():
    vid = aktiver_verein_id()
    k = request.args.get("k", "")
    conn = get_db()
    try:
        plan = {e["schluessel"]: e for e in signage.plan_laden(conn, vid)}
        if k not in plan:
            abort(404)
        if k.startswith("folie:"):
            folien = signage.eigene_folien(conn, vid, None, datetime.now().date(), _url_fuer, plan[k]["dauer"],
                                           nur_id=int(k.split(":", 1)[1]))
        else:
            folien = signage.schaufenster_folien(conn, vid, dict(signage.STANDARD["schaufenster"]), _url_fuer, nur=k)
            for fo in folien:
                if fo.get("typ") == "feiertag" and fo.get("foto"):
                    fo["foto"]["url"] = url_for("static", filename="signage/feiertage/" + fo["foto"]["datei"])
    finally:
        conn.close()
    if not folien:
        e = plan[k]
        folien = [{"typ": "text", "unter": "Zurzeit keine Folie", "titel": e["titel"],
                   "text": ((e["hinweis"] + " – ") if e["hinweis"] else "") + "Sobald es dazu etwas zu zeigen gibt, erscheint sie hier.",
                   "dauer": 12}]
    return _ohne_index(jsonify({"folien": folien, "an": True, "modul": True, "neu_laden": False}))


@bp.route("/hausfolien", methods=["POST"])
def hausfolien_speichern():
    """Vermietung, Spendenaktion, Werte und Adventskalender-Zählung der Hausfolien (statt Namenserkennung)."""
    vid = aktiver_verein_id()
    f = request.form
    spenden = (f.get("signage_spenden_url") or "").strip()[:300]
    if spenden and not signage.sichere_link(spenden):
        flash("Der Spenden-Link muss mit https:// beginnen.", "fehler")
        return redirect(url_for("signage.verwaltung") + "#hausfolien")
    try:
        erste = int(f.get("lak_erste_saison") or 0)
    except ValueError:
        erste = 0
    erste = erste if 1990 <= erste <= 2100 else 0
    werte = "\n".join(w.strip()[:60] for w in (f.get("signage_werte") or "").splitlines() if w.strip())[:600]
    conn = get_db()
    try:
        conn.execute("UPDATE vereine SET signage_mieten=?, signage_mieten_titel=?, signage_spenden_url=?, "
                     "signage_spenden_titel=?, signage_spenden_text=?, signage_werte=?, signage_verein_fuss=?, "
                     "lak_erste_saison=? WHERE id=?",
                     (1 if f.get("signage_mieten") else 0, (f.get("signage_mieten_titel") or "").strip()[:60], spenden,
                      (f.get("signage_spenden_titel") or "").strip()[:60], (f.get("signage_spenden_text") or "").strip()[:300],
                      werte, (f.get("signage_verein_fuss") or "").strip()[:140], erste, vid))
        conn.commit()
    finally:
        conn.close()
    flash("Hausfolien gespeichert – die Schaufenster übernehmen sie innerhalb einer Minute.", "erfolg")
    return redirect(url_for("signage.verwaltung") + "#hausfolien")


@bp.route("/mieten-foto", methods=["POST"])
def mieten_foto():
    """Foto für die Folie „Räume mieten“ hochladen oder entfernen (ohne Foto: Folie mit Text und QR-Code)."""
    vid = aktiver_verein_id()
    conn = get_db()
    try:
        alt = (conn.execute("SELECT signage_mieten_bild FROM vereine WHERE id=?", (vid,)).fetchone() or {"signage_mieten_bild": ""})["signage_mieten_bild"]
        if request.form.get("entfernen"):
            name, fehler = "", None
        else:
            name, fehler = _folie_bild_ablegen(request.files.get("bild"), vid, "mieten")
            if not name and not fehler:
                fehler = "Bitte ein Foto auswählen."
        if fehler:
            flash(fehler, "fehler")
        else:
            conn.execute("UPDATE vereine SET signage_mieten_bild=? WHERE id=?", (name, vid))
            conn.commit()
            if alt and alt != name:
                try:
                    os.remove(os.path.join(_folien_dir(), alt))
                except OSError:
                    pass
            flash("Foto gespeichert." if name else "Foto entfernt – die Folie zeigt jetzt Text und QR-Code.", "erfolg")
    finally:
        conn.close()
    return redirect(url_for("signage.verwaltung") + "#folien")
