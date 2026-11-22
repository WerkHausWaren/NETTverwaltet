"""Verwaltung des Moduls „Verteilen" (Leitung = Rolle admin).

Verteilaktionen anlegen und abschließen, den Straßenstamm importieren und
Haushalte korrigieren, Fortschritt je Person sehen, fremde Straßen
zurücksetzen und den Stand als CSV exportieren. Alle Routen liegen hinter dem
Admin-Login (zentraler Zugriffsschutz). Die Helfer:innen-Ansicht für das
Handy lebt im Portal (routes/portal_verteilen.py).
"""
import csv
import io
import json

from flask import (Blueprint, Response, abort, flash, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user

from .. import speicher
from .. import verteilen as vt
from .. import verteilen_zustellung as vz
from ..db import aktiver_verein_id, get_db, get_einstellungen, now_iso
from ..kontakte_import import ImportFehler

bp = Blueprint("verteilen", __name__, url_prefix="/verteilen")


def _modul_fehlt():
    e = get_einstellungen()
    return not (e and e.get("modul_verteilen"))


@bp.before_request
def _modul_gate():
    if _modul_fehlt():
        if request.method == "POST":
            abort(404)
        flash("Das Modul „Verteilaktionen“ ist für diesen Verein nicht eingeschaltet "
              "(Einstellungen › Module).", "fehler")
        return redirect(url_for("dashboard.index"))


def _uid():
    try:
        return int(current_user.id)
    except (AttributeError, TypeError, ValueError):
        return None


def _aktion_oder_404(conn, vid, aid):
    a = vt.aktion_laden(conn, vid, aid)
    if not a:
        conn.close()
        abort(404)
    return a


# --------------------------------------------------------------- Aktionen ---
@bp.route("/")
def index():
    vid = aktiver_verein_id()
    conn = get_db()
    vt.loeschfrist_pruefen(conn, vid)
    aktionen = []
    for a in conn.execute("SELECT * FROM verteil_aktionen WHERE verein_id=? "
                          "ORDER BY (COALESCE(archiviert_am,'')!=''), COALESCE(von,'') DESC, id DESC",
                          (vid,)).fetchall():
        x = {"a": a, "f": None, "z": None, "laeuft": 0, "loeschfrist": vt.loeschfrist_datum(a)}
        if vt.hat_flyer(a):
            x["f"] = vt.fortschritt(conn, vid, a)
            x["laeuft"] = conn.execute("SELECT COUNT(DISTINCT strasse_id) FROM verteil_status WHERE aktion_id=? "
                                       "AND status='laeuft'", (a["id"],)).fetchone()[0]
        if vt.hat_plakate(a):
            x["z"] = vt.plakat_zaehler(conn, a)
        x["zu"] = vz.zaehler(conn, a) if vz.hat_zustellungen(a) else None
        aktionen.append(x)
    orte = [r["ort"] for r in conn.execute(
        "SELECT ort, COUNT(*) n FROM verteil_strassen WHERE verein_id=? GROUP BY ort ORDER BY ort", (vid,))]
    stamm = conn.execute("SELECT COUNT(*) FROM verteil_strassen WHERE verein_id=?", (vid,)).fetchone()[0]
    conn.close()
    return render_template("verteilen/index.html", aktiv="verteilen", aktionen=aktionen,
                           orte=orte, stamm=stamm)


def _aktion_aus_formular():
    titel = request.form.get("titel", "").strip()[:120]
    if not titel:
        raise ValueError("Bitte einen Titel angeben, z. B. „Programmheft Frühjahr“.")
    von, bis = request.form.get("von", "").strip()[:10], request.form.get("bis", "").strip()[:10]
    if von and bis and bis < von:
        raise ValueError("Das Ende liegt vor dem Beginn.")
    orte = [o for o in request.form.getlist("orte") if o.strip()]
    mit_flyern = 1 if request.form.get("mit_flyern") else 0
    mit_plakaten = 1 if request.form.get("mit_plakaten") else 0
    mit_zustellungen = 1 if request.form.get("mit_zustellungen") else 0
    if not (mit_flyern or mit_plakaten or mit_zustellungen):
        raise ValueError("Bitte ankreuzen, was dazugehört: Flyer verteilen, Plakate aufhängen "
                         "und/oder Zustellungen an Kontakte.")
    kuerzel = "".join(c for c in request.form.get("plakat_kuerzel", "") if c.isalnum())[:6].upper()
    try:
        plakat_max = max(0, min(100000, int(request.form.get("plakat_max") or 0)))
    except ValueError:
        raise ValueError("Die Höchstzahl der Plakate muss eine ganze Zahl sein.") from None
    abhaengen_bis = request.form.get("abhaengen_bis", "").strip()[:10]
    return {"titel": titel, "beschreibung": request.form.get("beschreibung", "").strip()[:2000],
            "von": von, "bis": bis, "orte": json.dumps(orte, ensure_ascii=False),
            "mit_flyern": mit_flyern, "mit_plakaten": mit_plakaten, "plakat_kuerzel": kuerzel or "P",
            "plakat_max": plakat_max, "abhaengen_bis": abhaengen_bis, "mit_zustellungen": mit_zustellungen,
            "zustellung_was": request.form.get("zustellung_was", "").strip()[:120]}


def _flyer_bild_speichern(conn, a, mid, datei):
    """Bild eines Flyers prüfen, verkleinern (ohne Metadaten) und ablegen."""
    einst = get_einstellungen()
    jpeg = vt.foto_aufbereiten(datei.read())
    name = vt.material_bild_name(a["id"], mid)
    if speicher.speichern(einst, "verteilen", name, jpeg) is False:
        raise vt.VerteilFehler("Das Bild konnte nicht gespeichert werden – bitte später noch einmal.")
    alt = conn.execute("SELECT bild FROM verteil_material WHERE id=?", (mid,)).fetchone()
    conn.execute("UPDATE verteil_material SET bild=? WHERE id=?", (name, mid))
    conn.commit()
    if alt and alt["bild"]:
        try:
            speicher.loeschen(einst, "verteilen", alt["bild"])
        except Exception:  # noqa: BLE001 – altes Bild bleibt dann eben liegen
            pass


@bp.route("/neu", methods=["POST"])
def neu():
    vid = aktiver_verein_id()
    try:
        d = _aktion_aus_formular()
    except ValueError as e:
        flash(str(e), "fehler")
        return redirect(url_for("verteilen.index"))
    conn = get_db()
    aid = conn.execute("INSERT INTO verteil_aktionen (verein_id, titel, beschreibung, von, bis, orte, aktiv, "
                       "mit_flyern, mit_plakaten, plakat_kuerzel, plakat_max, abhaengen_bis, "
                       "mit_zustellungen, zustellung_was, erstellt_von, erstellt_am) "
                       "VALUES (?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)",
                       (vid, d["titel"], d["beschreibung"], d["von"], d["bis"], d["orte"], d["mit_flyern"],
                        d["mit_plakaten"], d["plakat_kuerzel"], d["plakat_max"], d["abhaengen_bis"],
                        d["mit_zustellungen"], d["zustellung_was"], _uid(), now_iso())).lastrowid
    conn.commit()
    a = vt.aktion_laden(conn, vid, aid)
    titel = d["titel"]
    if d["mit_flyern"]:
        mats = vt.materialien(conn, a)
        bild = request.files.get("flyer_bild")
        if bild and bild.filename:
            try:
                _flyer_bild_speichern(conn, a, mats[0]["id"], bild)
            except vt.VerteilFehler as e:
                flash(e.text, "fehler")
    conn.close()
    flash(f"Verteilaktion „{titel}“ angelegt – Helfer:innen finden sie jetzt in der App. 🗺️", "erfolg")
    return redirect(url_for("verteilen.detail", aid=aid))


@bp.route("/<int:aid>")
def detail(aid):
    vid = aktiver_verein_id()
    conn = get_db()
    vt.loeschfrist_pruefen(conn, vid)
    a = _aktion_oder_404(conn, vid, aid)
    st = vt.state(conn, vid, a, _uid(), "admin")
    personen = {}
    hh = {s["id"]: s["haushalte"] for s in st["strassen"]}
    for r in conn.execute("SELECT benutzer_id, strasse_id, status FROM verteil_status WHERE aktion_id=?", (aid,)):
        p = personen.setdefault(r["benutzer_id"], {"laeuft": set(), "verteilt": set(), "plakate": 0})
        p[r["status"]].add(r["strasse_id"])
    for r in conn.execute("SELECT benutzer_id, COUNT(*) n FROM verteil_plakat_verlauf WHERE aktion_id=? "
                          "AND was IN ('hängt','Ersatz hängt') GROUP BY benutzer_id", (aid,)):
        personen.setdefault(r["benutzer_id"], {"laeuft": set(), "verteilt": set(), "plakate": 0})["plakate"] = r["n"]
    nk = vt.namen(personen.keys())
    personen_liste = sorted(({"name": vt.anzeige(nk, b, False), "verteilt": len(w["verteilt"]),
                              "laeuft": len(w["laeuft"] - w["verteilt"]),
                              "hh": sum(hh.get(x, 0) for x in w["verteilt"]), "plakate": w["plakate"]}
                             for b, w in personen.items()),
                            key=lambda x: (-x["verteilt"], -x["plakate"], x["name"]))
    fotos = conn.execute("SELECT COUNT(*) FROM verteil_fotos WHERE aktion_id=?", (aid,)).fetchone()[0]
    orte = [r["ort"] for r in conn.execute(
        "SELECT DISTINCT ort FROM verteil_strassen WHERE verein_id=? ORDER BY ort", (vid,))]
    mats = vt.materialien(conn, a) if vt.hat_flyer(a) else []
    loeschbar = {m["id"]: vt.material_loeschbar(conn, a, m["id"])[0] for m in mats}
    zust = None
    if vz.hat_zustellungen(a):
        liste = vz.leitung_liste(conn, vid, a)
        verortung = vz.verortung_stand(conn, aid)
        if verortung["fertig"] < verortung["gesamt"]:
            vz.verortung_starten(vid, aid)
            verortung = vz.verortung_stand(conn, aid)
        zust = {"liste": liste, "zaehler": vz.zaehler(conn, a), "verortung": verortung,
                "rueck": [z for z in liste if z["status"] == "nicht_zustellbar"]}
        for r in conn.execute("SELECT benutzer_id, COUNT(*) n FROM verteil_zustellungen WHERE aktion_id=? "
                              "AND status='zugestellt' AND benutzer_id IS NOT NULL GROUP BY benutzer_id", (aid,)):
            name = vt.anzeige(vt.namen([r["benutzer_id"]]), r["benutzer_id"], False)
            treffer = [p for p in personen_liste if p["name"] == name]
            if treffer:
                treffer[0]["zugestellt"] = r["n"]
            else:
                personen_liste.append({"name": name, "verteilt": 0, "laeuft": 0, "hh": 0, "plakate": 0,
                                       "zugestellt": r["n"]})
    conn.close()
    return render_template("verteilen/detail.html", aktiv="verteilen", a=a, st=st, mats=mats, zust=zust,
                           loeschbar=loeschbar, personen=personen_liste, fotos=fotos, orte=orte,
                           gewaehlte_orte=vt.orte_der_aktion(a), loeschfrist=vt.loeschfrist_datum(a))


@bp.route("/<int:aid>/bearbeiten", methods=["POST"])
def bearbeiten(aid):
    vid = aktiver_verein_id()
    try:
        d = _aktion_aus_formular()
    except ValueError as e:
        flash(str(e), "fehler")
        return redirect(url_for("verteilen.detail", aid=aid))
    conn = get_db()
    _aktion_oder_404(conn, vid, aid)
    conn.execute("UPDATE verteil_aktionen SET titel=?, beschreibung=?, von=?, bis=?, orte=?, mit_flyern=?, "
                 "mit_plakaten=?, plakat_kuerzel=?, plakat_max=?, abhaengen_bis=?, mit_zustellungen=?, "
                 "zustellung_was=? WHERE id=? AND verein_id=?",
                 (d["titel"], d["beschreibung"], d["von"], d["bis"], d["orte"], d["mit_flyern"],
                  d["mit_plakaten"], d["plakat_kuerzel"], d["plakat_max"], d["abhaengen_bis"],
                  d["mit_zustellungen"], d["zustellung_was"], aid, vid))
    conn.commit()
    conn.close()
    flash("Verteilaktion gespeichert.", "erfolg")
    return redirect(url_for("verteilen.detail", aid=aid))


@bp.route("/<int:aid>/archivieren", methods=["POST"])
def archivieren(aid):
    vid = aktiver_verein_id()
    conn = get_db()
    a = _aktion_oder_404(conn, vid, aid)
    if a["archiviert_am"]:
        conn.execute("UPDATE verteil_aktionen SET archiviert_am='', aktiv=1 WHERE id=?", (aid,))
        flash("Verteilaktion wieder aktiv – Helfer:innen können weitermachen.", "erfolg")
    else:
        conn.execute("UPDATE verteil_aktionen SET archiviert_am=?, aktiv=0 WHERE id=?",
                     (now_iso()[:10], aid))
        flash("Verteilaktion abgeschlossen. Die Zahlen bleiben erhalten; Namen und Routen werden "
              "nach Ablauf der Löschfrist automatisch entfernt.", "erfolg")
    conn.commit()
    conn.close()
    return redirect(url_for("verteilen.detail", aid=aid))


@bp.route("/<int:aid>/zuruecksetzen", methods=["POST"])
def zuruecksetzen(aid):
    """Leitung setzt eine (auch fremde) Straße auf offen oder verteilt."""
    vid = aktiver_verein_id()
    conn = get_db()
    a = _aktion_oder_404(conn, vid, aid)
    try:
        mid = request.form.get("material_id", type=int)
        vt.status_setzen(conn, vid, a, request.form.get("strasse_id", type=int) or 0,
                         request.form.get("status", "offen"), _uid(), leitung=True,
                         materialien_ids=[mid] if mid else None)
        flash("Status geändert.", "erfolg")
    except vt.VerteilFehler as e:
        flash(e.text, "fehler")
    conn.close()
    return redirect(url_for("verteilen.detail", aid=aid) + "#strassen")


def _csv_antwort(zeilen, name):
    puffer = io.StringIO()
    w = csv.writer(puffer, delimiter=";")
    for werte in zeilen:
        # Formel-Schutz für Tabellenprogramme (=, +, -, @ am Zellenanfang)
        w.writerow([("'" + v) if isinstance(v, str) and v[:1] in "=+-@" else v for v in werte])
    name = "".join(c if c.isalnum() else "-" for c in name)[:50] or "verteilaktion"
    return Response("\ufeff" + puffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@bp.route("/<int:aid>/export.csv")
def export(aid):
    vid = aktiver_verein_id()
    conn = get_db()
    a = _aktion_oder_404(conn, vid, aid)
    st = vt.state(conn, vid, a, _uid(), "admin")
    conn.close()
    zeilen = [["Ort", "Straße", "Haushalte", "Flyer", "Status", "Person", "Geändert"]]
    for s in st["strassen"]:
        je = st["status"].get(str(s["id"])) or {}
        for m in st["materialien"]:
            x = je.get(str(m["id"])) or {}
            zeilen.append([s["ort"], s["name"], s["haushalte"], m["titel"], x.get("status") or "offen",
                           x.get("wer") or "", x.get("am") or ""])
    return _csv_antwort(zeilen, a["titel"])


@bp.route("/<int:aid>/plakate.csv")
def plakate_export(aid):
    """Plakat-Standorte (für die nächste Aktion wiederverwendbar)."""
    vid = aktiver_verein_id()
    conn = get_db()
    a = _aktion_oder_404(conn, vid, aid)
    zeilen = [["Nr", "Status", "Straße", "Hinweis", "lat", "lon", "Geändert"]]
    for p in conn.execute("SELECT * FROM verteil_plakate WHERE aktion_id=? ORDER BY laufnr", (aid,)):
        zeilen.append([p["nr"], vt.PLAKAT_TEXT.get(p["status"], p["status"]), p["strasse"] or "",
                       p["hinweis"] or "", p["lat"], p["lon"], (p["geaendert_am"] or "")[:16]])
    conn.close()
    return _csv_antwort(zeilen, "Plakat-Standorte-" + a["titel"])


@bp.route("/<int:aid>/plakat/<int:pid>/loeschen", methods=["POST"])
def plakat_loeschen(aid, pid):
    vid = aktiver_verein_id()
    conn = get_db()
    a = _aktion_oder_404(conn, vid, aid)
    try:
        vt.plakat_loeschen(conn, a, pid, _uid(), leitung=True)
        flash("Geplantes Plakat gelöscht.", "erfolg")
    except vt.VerteilFehler as e:
        flash(e.text, "fehler")
    conn.close()
    return redirect(url_for("verteilen.detail", aid=aid) + "#plakate")



# ------------------------------------------------------------ Zustellungen ---
def _zust_aktion(conn, vid, aid):
    a = _aktion_oder_404(conn, vid, aid)
    if not vz.hat_zustellungen(a):
        conn.close()
        abort(404)
    return a


@bp.route("/<int:aid>/zustellungen/auswahl")
def zustellungen_auswahl(aid):
    vid = aktiver_verein_id()
    conn = get_db()
    a = _zust_aktion(conn, vid, aid)
    kat = request.args.get("kategorie", "")
    q = request.args.get("q", "").strip()[:80]
    nur_anschrift = request.args.get("nur_anschrift", "1") == "1"
    nur_ohne_mail = request.args.get("nur_ohne_mail") == "1"
    liste = vz.auswahl(conn, vid, a, kat, q, nur_anschrift, nur_ohne_mail)
    kategorien = [r[0] for r in conn.execute("SELECT DISTINCT COALESCE(kategorie,'') FROM kontakte WHERE verein_id=? "
                                            "ORDER BY 1", (vid,)) if r[0]]
    ohne = conn.execute("SELECT COUNT(*) FROM kontakte WHERE verein_id=? AND COALESCE(TRIM(strasse),'')=''",
                        (vid,)).fetchone()[0]
    conn.close()
    return render_template("verteilen/zustellungen_auswahl.html", aktiv="verteilen", a=a, liste=liste,
                           kategorien=kategorien, kat=kat, q=q, nur_anschrift=nur_anschrift,
                           nur_ohne_mail=nur_ohne_mail, ohne_anschrift=ohne)


@bp.route("/<int:aid>/zustellungen/hinzufuegen", methods=["POST"])
def zustellungen_hinzufuegen(aid):
    vid = aktiver_verein_id()
    conn = get_db()
    a = _zust_aktion(conn, vid, aid)
    neu, ohne, schon = vz.hinzufuegen(conn, vid, a, request.form.getlist("kontakt"))
    conn.close()
    if neu:
        vz.verortung_starten(vid, aid)
    text = f"{neu} Empfänger:in{'nen' if neu != 1 else ''} hinzugefügt."
    if schon:
        text += f" {schon} war{'en' if schon != 1 else ''} schon dabei."
    if ohne:
        text += f" {ohne} ohne Anschrift übersprungen – bitte erst im Kontakt ergänzen."
    flash(text + (" Die Kartenpunkte werden jetzt im Hintergrund ermittelt." if neu else ""),
          "erfolg" if neu else "info")
    return redirect(url_for("verteilen.detail", aid=aid) + "#zustellungen")


@bp.route("/<int:aid>/zustellungen/<int:zid>/entfernen", methods=["POST"])
def zustellung_entfernen(aid, zid):
    vid = aktiver_verein_id()
    conn = get_db()
    a = _zust_aktion(conn, vid, aid)
    try:
        vz.entfernen(conn, a, zid)
        flash("Empfänger:in entfernt.", "erfolg")
    except vt.VerteilFehler as e:
        flash(e.text, "fehler")
    conn.close()
    return redirect(url_for("verteilen.detail", aid=aid) + "#zustellungen")


@bp.route("/<int:aid>/zustellungen/<int:zid>/setzen", methods=["POST"])
def zustellung_setzen(aid, zid):
    """Leitung: Rückläufer wieder öffnen, Stand korrigieren."""
    vid = aktiver_verein_id()
    conn = get_db()
    a = _zust_aktion(conn, vid, aid)
    try:
        vz.setzen(conn, a, zid, request.form.get("status", "offen"), _uid(), leitung=True,
                  grund=request.form.get("grund", ""))
        flash("Stand geändert.", "erfolg")
    except vt.VerteilFehler as e:
        flash(e.text, "fehler")
    conn.close()
    return redirect(url_for("verteilen.detail", aid=aid) + "#zustellungen")


@bp.route("/<int:aid>/zustellungen/verorten", methods=["POST"])
def zustellungen_verorten(aid):
    vid = aktiver_verein_id()
    conn = get_db()
    _zust_aktion(conn, vid, aid)
    conn.close()
    gestartet = vz.verortung_starten(vid, aid)
    flash("Die Kartenpunkte werden ermittelt – das dauert etwa eine Sekunde je Anschrift."
          if gestartet else "Die Kartenpunkte werden gerade schon ermittelt.", "info")
    return redirect(url_for("verteilen.detail", aid=aid) + "#zustellungen")


@bp.route("/<int:aid>/laufzettel")
def laufzettel(aid):
    vid = aktiver_verein_id()
    conn = get_db()
    a = _zust_aktion(conn, vid, aid)
    liste = vz.leitung_liste(conn, vid, a)
    conn.close()
    return render_template("verteilen/laufzettel.html", a=a, liste=liste, heute=now_iso()[:10],
                           gruende=vz.GRUENDE, einst=get_einstellungen())


@bp.route("/<int:aid>/laufzettel.csv")
def laufzettel_csv(aid):
    vid = aktiver_verein_id()
    conn = get_db()
    a = _zust_aktion(conn, vid, aid)
    liste = vz.leitung_liste(conn, vid, a)
    conn.close()
    zeilen = [["Straße", "Hausnummer", "PLZ", "Ort", "Kontakt", "Ansprechpartner:in", "Stand", "Grund",
               "Person", "Geändert"]]
    stand = {"offen": "offen", "laeuft": "in Arbeit", "zugestellt": "zugestellt",
             "nicht_zustellbar": "nicht zustellbar"}
    for z in liste:
        zeilen.append([z["strasse"], z["hausnr"], z["plz"], z["ort"], z["name"], z["ansprechpartner"],
                       stand.get(z["status"], z["status"]), z["grund"], z["wer"], z["am"]])
    return _csv_antwort(zeilen, "Laufzettel-" + a["titel"])


# ------------------------------------------------------------------ Flyer ---
@bp.route("/<int:aid>/flyer/neu", methods=["POST"])
def flyer_neu(aid):
    vid = aktiver_verein_id()
    conn = get_db()
    a = _aktion_oder_404(conn, vid, aid)
    try:
        mid = vt.material_anlegen(conn, a, request.form.get("titel", ""))
        bild = request.files.get("bild")
        if bild and bild.filename:
            _flyer_bild_speichern(conn, a, mid, bild)
        flash("Flyer hinzugefügt – Helfer:innen wählen ihn ab jetzt beim Übernehmen einer Route mit aus.", "erfolg")
    except vt.VerteilFehler as e:
        flash(e.text, "fehler")
    conn.close()
    return redirect(url_for("verteilen.detail", aid=aid) + "#flyer")


@bp.route("/<int:aid>/flyer/<int:mid>/bearbeiten", methods=["POST"])
def flyer_bearbeiten(aid, mid):
    vid = aktiver_verein_id()
    conn = get_db()
    a = _aktion_oder_404(conn, vid, aid)
    m = conn.execute("SELECT * FROM verteil_material WHERE id=? AND aktion_id=?", (mid, aid)).fetchone()
    if not m:
        conn.close()
        abort(404)
    try:
        titel = request.form.get("titel", "").strip()[:80]
        if titel:
            conn.execute("UPDATE verteil_material SET titel=? WHERE id=?", (titel, mid))
            conn.commit()
        bild = request.files.get("bild")
        if bild and bild.filename:
            _flyer_bild_speichern(conn, a, mid, bild)
        flash("Flyer gespeichert.", "erfolg")
    except vt.VerteilFehler as e:
        flash(e.text, "fehler")
    conn.close()
    return redirect(url_for("verteilen.detail", aid=aid) + "#flyer")


@bp.route("/<int:aid>/flyer/<int:mid>/loeschen", methods=["POST"])
def flyer_loeschen(aid, mid):
    vid = aktiver_verein_id()
    conn = get_db()
    a = _aktion_oder_404(conn, vid, aid)
    m = conn.execute("SELECT * FROM verteil_material WHERE id=? AND aktion_id=?", (mid, aid)).fetchone()
    if not m:
        conn.close()
        abort(404)
    darf, grund = vt.material_loeschbar(conn, a, mid)
    if not darf:
        flash(grund, "fehler")
    else:
        conn.execute("DELETE FROM verteil_material WHERE id=?", (mid,))
        conn.commit()
        if m["bild"]:
            try:
                speicher.loeschen(get_einstellungen(), "verteilen", m["bild"])
            except Exception:  # noqa: BLE001
                pass
        flash(f"Flyer „{m['titel']}“ entfernt.", "erfolg")
    conn.close()
    return redirect(url_for("verteilen.detail", aid=aid) + "#flyer")


# ---------------------------------------------------------------- Straßen ---
@bp.route("/strassen")
def strassen():
    vid = aktiver_verein_id()
    conn = get_db()
    liste = conn.execute("SELECT * FROM verteil_strassen WHERE verein_id=? "
                         "ORDER BY ort COLLATE NOCASE, name COLLATE NOCASE", (vid,)).fetchall()
    conn.close()
    orte = {}
    for s in liste:
        orte.setdefault(s["ort"], []).append(s)
    return render_template("verteilen/strassen.html", aktiv="verteilen", orte=orte,
                           vorschau=session.get("verteil_import"))


@bp.route("/strassen/import", methods=["POST"])
def strassen_import():
    """Zwei Schritte: Datei hochladen → Orte zur Auswahl; dann übernehmen."""
    vid = aktiver_verein_id()
    if request.form.get("schritt") == "uebernehmen":
        vorschau = session.pop("verteil_import", None)
        if not vorschau:
            flash("Die Vorschau ist abgelaufen – bitte die Datei noch einmal hochladen.", "fehler")
            return redirect(url_for("verteilen.strassen"))
        from ..db import DATA_DIR
        import os
        pfad = os.path.join(DATA_DIR, "verteilen_import", f"{vid}-{vorschau['token']}.json")
        try:
            with open(pfad, encoding="utf-8") as f:
                liste = json.load(f)
            os.remove(pfad)
        except (OSError, ValueError):
            flash("Die Vorschau ist abgelaufen – bitte die Datei noch einmal hochladen.", "fehler")
            return redirect(url_for("verteilen.strassen"))
        orte = request.form.getlist("orte")
        if not orte:
            flash("Bitte mindestens einen Ort auswählen.", "fehler")
            return redirect(url_for("verteilen.strassen"))
        conn = get_db()
        neu, akt, geschuetzt = vt.strassen_importieren(conn, vid, liste, orte,
                                                       vorschau.get("stand", ""), vorschau.get("quelle", ""))
        conn.close()
        text = f"{neu} Straße(n) neu, {akt} aktualisiert."
        if geschuetzt:
            text += f" Bei {geschuetzt} blieben die von Hand korrigierten Haushalte erhalten."
        flash(text, "erfolg")
        return redirect(url_for("verteilen.strassen"))
    datei = request.files.get("datei")
    if not datei or not datei.filename:
        flash("Bitte eine CSV- oder JSON-Datei auswählen.", "fehler")
        return redirect(url_for("verteilen.strassen"))
    try:
        liste, stand, quelle = vt.strassen_lesen(datei.filename, datei.read())
    except ImportFehler as e:
        flash(str(e), "fehler")
        return redirect(url_for("verteilen.strassen"))
    import os
    import secrets
    from ..db import DATA_DIR
    ordner = os.path.join(DATA_DIR, "verteilen_import")
    os.makedirs(ordner, exist_ok=True)
    token = secrets.token_hex(8)
    with open(os.path.join(ordner, f"{vid}-{token}.json"), "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False)
    zaehler = {}
    for d in liste:
        zaehler[d["ort"]] = zaehler.get(d["ort"], 0) + 1
    session["verteil_import"] = {"token": token, "datei": datei.filename[:80], "stand": stand,
                                 "quelle": quelle, "orte": sorted(zaehler.items())}
    return redirect(url_for("verteilen.strassen") + "#import")


@bp.route("/strassen/import/abbrechen", methods=["POST"])
def strassen_import_abbrechen():
    vorschau = session.pop("verteil_import", None)
    if vorschau and str(vorschau.get("token", "")).isalnum():
        import os
        from ..db import DATA_DIR
        try:
            os.remove(os.path.join(DATA_DIR, "verteilen_import", f"{aktiver_verein_id()}-{vorschau['token']}.json"))
        except OSError:
            pass
    return redirect(url_for("verteilen.strassen"))


@bp.route("/strassen/<int:sid>/haushalte", methods=["POST"])
def haushalte(sid):
    vid = aktiver_verein_id()
    try:
        n = max(0, min(100000, int(request.form.get("haushalte", ""))))
    except ValueError:
        flash("Bitte eine ganze Zahl eintragen.", "fehler")
        return redirect(url_for("verteilen.strassen"))
    conn = get_db()
    cur = conn.execute("UPDATE verteil_strassen SET haushalte=?, haushalte_von_hand=1 "
                       "WHERE id=? AND verein_id=?", (n, sid, vid))
    conn.commit()
    conn.close()
    if cur.rowcount:
        flash("Haushalte korrigiert – die Zahl bleibt auch bei einem neuen Import erhalten.", "erfolg")
    return redirect(url_for("verteilen.strassen") + f"#s{sid}")


@bp.route("/strassen/<int:sid>/loeschen", methods=["POST"])
def strasse_loeschen(sid):
    vid = aktiver_verein_id()
    conn = get_db()
    benutzt = conn.execute("SELECT COUNT(*) FROM verteil_status WHERE strasse_id=?", (sid,)).fetchone()[0]
    if benutzt:
        flash("Diese Straße hat in einer Verteilaktion schon einen Stand und bleibt deshalb erhalten.", "fehler")
    else:
        conn.execute("DELETE FROM verteil_route_strassen WHERE strasse_id=?", (sid,))
        conn.execute("DELETE FROM verteil_strassen WHERE id=? AND verein_id=?", (sid, vid))
        conn.commit()
        flash("Straße entfernt.", "erfolg")
    conn.close()
    return redirect(url_for("verteilen.strassen"))
