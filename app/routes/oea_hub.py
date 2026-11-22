"""ÖA-Cockpit → Content-Hub (Bereich /oea/hub). Teil des Blueprints „oea“.

Rechte: Rolle `oeffentlichkeit` darf über das Präfix `oea.` alles Lesende und abstimmen;
Kanäle schalten, Einstellungen, Wochenblick-Vorschläge und das Social-Häkchen an Fotos
nur der Vorstand (admin). Der Hub prüft Person und Rolle zusätzlich selbst.
Alles hier läuft nur mit eingeschaltetem Modul `modul_contenthub`.
"""
import json
import re
from datetime import date

from flask import render_template, request, redirect, url_for, flash, abort, make_response, current_app
from flask_login import current_user

from .. import db, hub_client
from ..db import get_db, aktiver_verein_id, now_iso
from .oea import bp, _ist_admin, _wer, _beitrag

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ENTSCHEIDUNGEN = ("freigeben", "aendern", "ablehnen")


def _modul_an():
    einst = db.get_einstellungen()
    return bool(einst and einst["modul_contenthub"]), einst


def _ich():
    """(Kennung, Rolle, Name) der angemeldeten Person für den Hub."""
    uid = getattr(current_user, "id", None)
    rolle = getattr(current_user, "rolle", "") or ""
    if not current_app.config.get("PRODUKTION"):
        rolle = rolle or "admin"
    return (f"app:{uid}" if uid else "app:0"), ("admin" if rolle == "admin" else "oeffentlichkeit"), _wer()


def _gate():
    an, einst = _modul_an()
    if not an:
        flash("Der Content-Hub ist für diesen Verein nicht eingeschaltet (Einstellungen → Module).", "fehler")
        return None
    return einst


def _nur_vorstand():
    if not _ist_admin():
        flash("Das kann nur der Vorstand.", "fehler")
        return False
    return True


def _gruppen(text):
    out = []
    for zeile in (text or "").splitlines():
        name, _, link = zeile.partition("|")
        name, link = name.strip(), link.strip()
        if name and (not link or link.startswith("https://")):
            out.append({"name": name, "link": link})
    return out


@bp.route("/hub")
def hub():
    einst = _gate()
    if einst is None:
        return redirect(url_for("oea.index"))
    vid = aktiver_verein_id()
    person, rolle, _ = _ich()
    stand, hinweis = None, ""
    try:
        stand = hub_client.anfrage(person, rolle, "lesen")
        if not stand.get("ok"):
            hinweis = stand.get("grund") or "Der Hub hat die Anfrage nicht angenommen."
            stand = None
    except hub_client.HubFehler as e:
        hinweis = str(e)
    conn = get_db()
    zugriffe = {r["pfad"]: r["zuletzt"] for r in conn.execute(
        "SELECT pfad, zuletzt FROM hub_zugriffe WHERE verein_id=?", (vid,))}
    rueck = conn.execute("SELECT * FROM hub_beitraege WHERE verein_id=? ORDER BY aktualisiert DESC LIMIT 20",
                         (vid,)).fetchall()
    wbs = conn.execute("SELECT woche, daten FROM hub_wochenblicke WHERE verein_id=? ORDER BY woche DESC LIMIT 12",
                       (vid,)).fetchall()
    offen_auto = conn.execute("SELECT COUNT(*) n FROM oea_beitraege WHERE verein_id=? AND auto_posten=1 "
                              "AND kanal='instagram' AND COALESCE(ig_media_id,'')=''", (vid,)).fetchone()["n"]
    conn.close()
    wochenblick = None
    gewaehlt = next((w for w in wbs if w["woche"] == request.args.get("woche")), wbs[0] if wbs else None)
    if gewaehlt:
        try:
            wochenblick = json.loads(gewaehlt["daten"])
        except ValueError:
            wochenblick = None
    # Übernehmen/Verwerfen-Stand der Vorschläge kommt live vom Hub (gleiche learnings-Tabelle wie dort)
    if wochenblick and stand:
        entschieden = {v.get("id"): v for v in ((stand.get("wochenblick") or {}).get("vorschlaege") or [])}
        for v in wochenblick.get("vorschlaege") or []:
            if isinstance(v, dict) and v.get("id") in entschieden:
                v["entschieden"] = entschieden[v["id"]].get("entschieden")
    s = db.get_system_db()
    ich_id = getattr(current_user, "id", None)
    ich_row = s.execute("SELECT abwesend_bis, vertretung_id FROM benutzer WHERE id=?", (ich_id,)).fetchone() if ich_id else None
    kollegium = s.execute("SELECT id, anzeigename, email FROM benutzer WHERE verein_id=? AND aktiv=1 "
                          "AND rolle IN ('admin','oeffentlichkeit') AND id!=? ORDER BY anzeigename",
                          (vid, ich_id or 0)).fetchall()
    s.close()
    letzte = max(zugriffe.values()) if zugriffe else ""
    return render_template("oea/hub.html", aktiv="oea", stand=stand or {}, hinweis=hinweis,
                           ist_admin=_ist_admin(), einst=einst, rueck=rueck, wochenblick=wochenblick,
                           wochen=[w["woche"] for w in wbs], letzte_sichtung=letzte,
                           fb_gruppen=_gruppen(einst["hub_fb_gruppen"]), offen_auto=offen_auto,
                           ich=ich_row, kollegium=kollegium, heute=date.today().isoformat(),
                           person=person)


@bp.route("/hub/stimme", methods=["POST"])
def hub_stimme():
    if _gate() is None:
        return redirect(url_for("oea.index"))
    fid, ent = request.form.get("freigabe_id", ""), request.form.get("entscheidung", "")
    notiz = (request.form.get("notiz") or "").strip()[:4000]
    if not _UUID.match(fid) or ent not in ENTSCHEIDUNGEN:
        abort(400)
    if ent == "aendern" and not notiz:
        flash("Bitte kurz beschreiben, was geändert werden soll.", "fehler")
        return redirect(url_for("oea.hub") + "#freigaben")
    person, rolle, _ = _ich()
    try:
        r = hub_client.anfrage(person, rolle, "stimme", freigabe_id=fid, entscheidung=ent, notiz=notiz)
        flash(r.get("meldung") or ("Gespeichert. ✅" if r.get("ok") else r.get("grund") or "Nicht angenommen."),
              "erfolg" if r.get("ok") else "fehler")
    except hub_client.HubFehler as e:
        flash(str(e), "fehler")
    return redirect(url_for("oea.hub") + "#freigaben")


@bp.route("/hub/kanal", methods=["POST"])
def hub_kanal():
    if _gate() is None or not _nur_vorstand():
        return redirect(url_for("oea.hub"))
    kanal, status = request.form.get("kanal", ""), request.form.get("status", "")
    if not re.match(r"^[a-z]{2,12}$", kanal) or status not in ("aktiv", "trockenlauf"):
        abort(400)
    person, rolle, _ = _ich()
    try:
        r = hub_client.anfrage(person, rolle, "kanal", kanal=kanal, status=status)
        flash(r.get("meldung") or ("Kanal umgestellt." if r.get("ok") else r.get("grund") or "Nicht angenommen."),
              "erfolg" if r.get("ok") else "fehler")
    except hub_client.HubFehler as e:
        flash(str(e), "fehler")
    return redirect(url_for("oea.hub") + "#kanaele")


@bp.route("/hub/aktion", methods=["POST"])
def hub_aktion():
    """Kleine Aktionen: sichten (alle), lernen (Vorstand), geteilt (alle)."""
    if _gate() is None:
        return redirect(url_for("oea.index"))
    aktion = request.form.get("aktion", "")
    person, rolle, _ = _ich()
    felder = {}
    if aktion == "sichten":
        pass
    elif aktion == "lernen":
        if not _nur_vorstand():
            return redirect(url_for("oea.hub"))
        lid = request.form.get("learning_id", "")
        if not _UUID.match(lid):
            abort(400)
        felder = {"learning_id": lid, "ok": request.form.get("ok") == "1",
                  "text": (request.form.get("text") or "").strip()[:600]}
    elif aktion == "geteilt":
        bid, ziel = request.form.get("beitrag_id", ""), request.form.get("ziel", "")
        if not _UUID.match(bid) or ziel not in ("whatsapp", "facebook_gruppe"):
            abort(400)
        felder = {"beitrag_id": bid, "ziel": ziel}
    else:
        abort(400)
    try:
        r = hub_client.anfrage(person, rolle, aktion, **felder)
        flash(r.get("meldung") or ("Erledigt. ✅" if r.get("ok") else r.get("grund") or "Nicht angenommen."),
              "erfolg" if r.get("ok") else "fehler")
    except hub_client.HubFehler as e:
        flash(str(e), "fehler")
    return redirect(url_for("oea.hub") + {"lernen": "#wochenblick", "geteilt": "#teilen"}.get(aktion, ""))


@bp.route("/hub/bild/<beitrag_id>/<int:nr>")
def hub_bild(beitrag_id, nr):
    """Vorschaubild eines noch nicht veröffentlichten Beitrags – über den App-Server geholt (privat)."""
    if _gate() is None or not _UUID.match(beitrag_id) or not 0 <= nr < 20:
        abort(404)
    person, rolle, _ = _ich()
    try:
        daten, art = hub_client.bild(person, rolle, beitrag_id, nr)
    except hub_client.HubFehler:
        abort(404)
    if not art.startswith("image/"):
        abort(404)
    antwort = make_response(daten)
    antwort.headers["Content-Type"] = art
    antwort.headers["Cache-Control"] = "private, max-age=300"
    return antwort


@bp.route("/hub/oeffnen")
def hub_oeffnen():
    """Sprung ins Hub-Cockpit ohne zweites Passwort: selbst abschickendes POST-Formular mit Einmal-Code."""
    if _gate() is None:
        return redirect(url_for("oea.index"))
    person, rolle, name = _ich()
    try:
        code = hub_client.sso_code(person, name, rolle)
    except hub_client.HubFehler as e:
        flash(str(e), "fehler")
        return redirect(url_for("oea.hub"))
    ziel = hub_client.sso_url()
    antwort = make_response(render_template("oea/hub_sprung.html", ziel=ziel, code=code))
    herkunft = re.match(r"^https://[^/]+", ziel)
    antwort.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        f"form-action {herkunft.group(0) if herkunft else 'none'}; frame-ancestors 'none'; base-uri 'none'")
    antwort.headers["Referrer-Policy"] = "no-referrer"
    antwort.headers["Cache-Control"] = "no-store"
    return antwort


@bp.route("/hub/abwesenheit", methods=["POST"])
def hub_abwesenheit():
    """Eigene Abwesenheit + Vertretung (Quelle für den Hub: keine Freigabe-Anfragen in der Zeit)."""
    if _gate() is None:
        return redirect(url_for("oea.index"))
    ich_id = getattr(current_user, "id", None)
    if not ich_id:
        abort(403)
    bis = (request.form.get("abwesend_bis") or "").strip()
    if bis and (not re.match(r"^\d{4}-\d{2}-\d{2}$", bis) or bis < date.today().isoformat()):
        flash("Bitte ein Datum ab heute angeben.", "fehler")
        return redirect(url_for("oea.hub") + "#abwesenheit")
    vt = request.form.get("vertretung_id", type=int)
    s = db.get_system_db()
    vid = aktiver_verein_id()
    if vt and not s.execute("SELECT 1 FROM benutzer WHERE id=? AND id!=? AND verein_id=? AND aktiv=1 "
                            "AND rolle IN ('admin','oeffentlichkeit')", (vt, ich_id, vid)).fetchone():
        vt = None
    s.execute("UPDATE benutzer SET abwesend_bis=?, vertretung_id=? WHERE id=?",
              (bis, vt if bis else None, ich_id))
    s.commit()
    s.close()
    flash("Abwesenheit gespeichert – der Hub übernimmt sie beim nächsten Abgleich (bis 15 Minuten). 🌴" if bis
          else "Abwesenheit gelöscht – du bekommst wieder Freigabe-Anfragen.", "erfolg")
    return redirect(url_for("oea.hub") + "#abwesenheit")


@bp.route("/hub/einstellungen", methods=["POST"])
def hub_einstellungen():
    if _gate() is None or not _nur_vorstand():
        return redirect(url_for("oea.hub"))
    vid = aktiver_verein_id()
    ig = 1 if request.form.get("hub_instagram") else 0
    gruppen = "\n".join(f"{g['name']} | {g['link']}".rstrip(" |")
                        for g in _gruppen(request.form.get("hub_fb_gruppen", "")))[:4000]
    wa = (request.form.get("hub_whatsapp_kanal") or "").strip()
    if wa and not wa.startswith("https://"):
        wa = ""
    conn = get_db()
    conn.execute("UPDATE vereine SET hub_instagram=?, hub_fb_gruppen=?, hub_whatsapp_kanal=? WHERE id=?",
                 (ig, gruppen, wa[:300], vid))
    abgeschaltet = 0
    if ig:
        # Im selben Schritt das App-Direktposten anhalten – sonst könnten beide Wege posten
        abgeschaltet = conn.execute(
            "UPDATE oea_beitraege SET auto_posten=0, auto_fehler='Instagram läuft jetzt über den Content-Hub – "
            "bitte über „An den Hub geben“ einplanen.' WHERE verein_id=? AND auto_posten=1 AND kanal='instagram' "
            "AND COALESCE(ig_media_id,'')=''", (vid,)).rowcount
    conn.commit()
    conn.close()
    flash("Gespeichert." + (f" {abgeschaltet} automatisch geplante Instagram-Beiträge wurden angehalten – "
                            "bitte an den Hub geben." if abgeschaltet else ""), "erfolg")
    return redirect(url_for("oea.hub") + "#einstellungen")


@bp.route("/<int:bid>/an-hub", methods=["POST"])
def an_hub(bid):
    """Einen Social-Beitrag an den Hub geben: der baut die Kanal-Fassungen, freigegeben wird im Hub."""
    if _gate() is None:
        return redirect(url_for("oea.bearbeiten", bid=bid))
    conn = get_db()
    vid = aktiver_verein_id()
    b = _beitrag(conn, bid, vid)
    if b["typ"] != "social":
        conn.close()
        flash("Nur Social-Media-Beiträge lassen sich an den Hub geben.", "fehler")
        return redirect(url_for("oea.bearbeiten", bid=bid))
    foto = None
    if b["bild_foto_id"]:
        f = conn.execute("SELECT freigabe_ok, social_ok FROM galerie_fotos WHERE id=? AND verein_id=?",
                         (b["bild_foto_id"], vid)).fetchone()
        if not f or not f["freigabe_ok"] or not f["social_ok"]:
            conn.close()
            flash("Das gewählte Foto hat (noch) kein Social-Media-Häkchen des Vorstands – "
                  "bitte im Fotoarchiv prüfen oder ein anderes Foto wählen.", "fehler")
            return redirect(url_for("oea.bearbeiten", bid=bid))
        foto = f"foto-{b['bild_foto_id']}"
    person, rolle, _ = _ich()
    try:
        r = hub_client.anfrage(person, rolle, "an_hub", titel=b["titel"] or "", text=b["text"] or "",
                               foto=foto, termin=f"termin-{b['termin_id']}" if b["termin_id"] else None,
                               oea_beitrag=bid, wunschtermin=(b["geplant_am"] or "") + (" " + b["geplant_um"] if b["geplant_um"] else ""))
    except hub_client.HubFehler as e:
        conn.close()
        flash(str(e), "fehler")
        return redirect(url_for("oea.bearbeiten", bid=bid))
    if r.get("ok"):
        conn.execute("UPDATE oea_beitraege SET hub_thema=?, hub_status='an_hub', auto_posten=0 WHERE id=? AND verein_id=?",
                     (str(r.get("thema_id") or "")[:64], bid, vid))
        conn.commit()
        flash("An den Hub gegeben – die Kanal-Fassungen kommen gleich zur Freigabe ins Cockpit. 🚀", "erfolg")
    else:
        flash(r.get("grund") or "Der Hub hat den Beitrag nicht angenommen.", "fehler")
    conn.close()
    return redirect(url_for("oea.bearbeiten", bid=bid))


@bp.route("/foto/<int:fid>/social", methods=["POST"])
def foto_social(fid):
    """Social-Media-Häkchen (Instagram, Facebook, WhatsApp-Kanal, Signal-Gruppen) – nur Vorstand.

    Bewusste Entscheidung je Foto: In der App gibt es keine Verbindung Foto ↔ abgebildete Personen.
    Ein Nein/Widerruf einer teilnehmenden Person nimmt das Häkchen automatisch zurück (DB-Trigger)."""
    conn = get_db()
    vid = aktiver_verein_id()
    f = conn.execute("SELECT * FROM galerie_fotos WHERE id=? AND verein_id=?", (fid, vid)).fetchone()
    if not f:
        conn.close()
        abort(404)
    if not _ist_admin():
        conn.close()
        flash("Das Social-Media-Häkchen kann nur der Vorstand setzen.", "fehler")
        return redirect(url_for("oea.fotos", album=f["termin_id"]))
    neu = 0 if f["social_ok"] else 1
    if neu and not f["freigabe_ok"]:
        conn.close()
        flash("Erst das Foto selbst freigeben, dann für Social Media.", "fehler")
        return redirect(url_for("oea.fotos", album=f["termin_id"]))
    if neu:
        nein = conn.execute("SELECT COUNT(*) n FROM veranstaltung_anmeldung WHERE termin_id=? AND verein_id=? "
                            "AND foto_social='nein' AND COALESCE(storniert,0)=0", (f["termin_id"], vid)).fetchone()["n"]
        if nein and request.form.get("trotzdem") != "1":
            conn.close()
            flash(f"Achtung: {nein} Teilnehmende dieser Veranstaltung haben Social Media abgelehnt. "
                  "Nur setzen, wenn sicher niemand von ihnen zu erkennen ist – dann bitte bestätigen.", "fehler")
            return redirect(url_for("oea.fotos", album=f["termin_id"], pruefen=fid))
    conn.execute("UPDATE galerie_fotos SET social_ok=?, social_ok_von=?, social_ok_am=? WHERE id=? AND verein_id=?",
                 (neu, _wer() if neu else "", now_iso(), fid, vid))
    conn.commit()
    conn.close()
    flash("Foto für Social Media freigegeben. 📱" if neu else "Social-Media-Freigabe zurückgenommen.", "erfolg")
    return redirect(url_for("oea.fotos", album=f["termin_id"]))


def social_lage(conn, vid, termin_id):
    """Einwilligungslage einer Veranstaltung für den Prüf-Dialog im Fotoarchiv."""
    rows = conn.execute("SELECT COALESCE(foto_social,'') s, COALESCE(foto_antwort_am,'') am FROM veranstaltung_anmeldung "
                        "WHERE termin_id=? AND verein_id=? AND COALESCE(storniert,0)=0", (termin_id, vid)).fetchall()
    return {"ja": sum(1 for r in rows if r["s"] == "ja"),
            "ja_alt": sum(1 for r in rows if r["s"] == "ja" and r["am"] and r["am"] < "2026-11-04"),
            "nein": sum(1 for r in rows if r["s"] == "nein"),
            "offen": sum(1 for r in rows if r["s"] == "")}
