"""Dateiablage (DMS) – Vorstands-/Admin-Ansicht mit vollem Zugriff + Komfortfunktionen."""
import io
import os
import zipfile

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, send_file, abort)

from .. import dateiablage as dms
from .. import speicher
from ..db import get_db, get_einstellungen, aktiver_verein_id

bp = Blueprint("dms", __name__, url_prefix="/dms")


@bp.before_request
def _gate():
    e = get_einstellungen()
    if not (e and "modul_dms" in e.keys() and e["modul_dms"]):
        flash("Das Modul „Dateiablage“ ist nicht aktiviert (Einstellungen → Module).", "info")
        return redirect(url_for("dashboard.index"))


def breadcrumbs(relpfad):
    crumbs, akk = [], ""
    for teil in [t for t in (relpfad or "").strip("/").split("/") if t]:
        akk = (akk + "/" + teil).strip("/")
        crumbs.append({"name": teil, "pfad": akk})
    return crumbs


def _eltern(relpfad):
    return "/".join(relpfad.strip("/").split("/")[:-1])


@bp.route("/")
def index():
    vid = aktiver_verein_id()
    dms.struktur_sicherstellen(vid)
    relpfad = (request.args.get("pfad") or "").strip()
    sort = request.args.get("sort") if request.args.get("sort") in ("name", "datum", "groesse") else "name"
    inhalt = dms.liste(vid, relpfad, mitglied=False, sort=sort)
    if inhalt is None:
        flash("Ordner nicht gefunden.", "fehler")
        return redirect(url_for("dms.index"))
    conn = get_db()
    gruppen = conn.execute("SELECT id, name FROM gruppen WHERE verein_id=? ORDER BY name", (vid,)).fetchall()
    conn.close()
    regeln = dms.zugriffsregeln(vid)
    gruppen_namen = {g["id"]: g["name"] for g in gruppen}
    zugriff_text = {}  # pfad -> "Gruppe A, Gruppe B" (nur eingeschränkte Ordner)
    for pfad, ids in regeln.items():
        zugriff_text[pfad] = ", ".join(sorted(gruppen_namen.get(i, "?") for i in ids))
    return render_template("dms/index.html", aktiv="dms", relpfad=relpfad, inhalt=inhalt,
                           crumbs=breadcrumbs(relpfad), sort=sort,
                           ordnerliste=dms.alle_ordner(vid, mitglied=False),
                           gesamt=dms.groesse_gesamt(vid), quota=dms.quota_bytes(vid),
                           gruppen=gruppen, regeln=regeln, zugriff_text=zugriff_text)


@bp.route("/upload", methods=["POST"])
def upload():
    vid = aktiver_verein_id()
    relpfad = (request.form.get("pfad") or "").strip()
    ziel = dms.aufloesen(vid, relpfad)
    if not ziel or not os.path.isdir(ziel):
        abort(400)
    quota = dms.quota_bytes(vid)
    belegt = dms.groesse_gesamt(vid)
    n = 0
    abgelehnt = 0
    for datei in request.files.getlist("dateien"):
        if datei and datei.filename:
            daten = datei.read()
            if quota and belegt + len(daten) > quota:
                abgelehnt += 1
                continue
            with open(os.path.join(ziel, dms.sicherer_name(datei.filename)), "wb") as f:
                f.write(daten)
            belegt += len(daten)
            n += 1
    if request.form.get("ajax"):
        return {"ok": n > 0, "n": n, "abgelehnt": abgelehnt}
    if abgelehnt:
        flash(f"{n} Datei(en) hochgeladen, {abgelehnt} abgelehnt – Speicherlimit erreicht.", "fehler")
    else:
        flash(f"{n} Datei(en) hochgeladen.", "erfolg" if n else "fehler")
    return redirect(url_for("dms.index", pfad=relpfad))


@bp.route("/zugriff", methods=["POST"])
def zugriff():
    """Sichtbarkeit eines Ordners für Mitglieder festlegen (keine Auswahl = alle Mitglieder)."""
    vid = aktiver_verein_id()
    relpfad = (request.form.get("pfad") or "").strip().strip("/")
    ziel = dms.aufloesen(vid, relpfad)
    if not relpfad or not ziel or not os.path.isdir(ziel):
        abort(400)
    gruppen_ids = request.form.getlist("gruppen", type=int)
    conn = get_db()
    gueltig = {g["id"] for g in conn.execute("SELECT id FROM gruppen WHERE verein_id=?", (vid,)).fetchall()}
    conn.execute("DELETE FROM dms_zugriff WHERE verein_id=? AND pfad=?", (vid, relpfad))
    for gid in gruppen_ids:
        if gid in gueltig:
            conn.execute("INSERT INTO dms_zugriff (verein_id, pfad, gruppe_id) VALUES (?,?,?)",
                         (vid, relpfad, gid))
    conn.commit()
    conn.close()
    if gruppen_ids:
        flash("Zugriff eingeschränkt – der Ordner ist nur noch für die gewählten Gruppen sichtbar.", "erfolg")
    else:
        flash("Zugriff zurückgesetzt – der Ordner ist wieder für alle Mitglieder sichtbar.", "erfolg")
    return redirect(url_for("dms.index", pfad=_eltern(relpfad)))


@bp.route("/ordner", methods=["POST"])
def ordner_neu():
    vid = aktiver_verein_id()
    relpfad = (request.form.get("pfad") or "").strip()
    name = dms.sicherer_name(request.form.get("name") or "")
    ziel = dms.aufloesen(vid, (relpfad.strip("/") + "/" + name).strip("/"))
    if name and ziel:
        os.makedirs(ziel, exist_ok=True)
        flash(f"Ordner „{name}“ angelegt.", "erfolg")
    return redirect(url_for("dms.index", pfad=relpfad))


@bp.route("/rename", methods=["POST"])
def rename():
    vid = aktiver_verein_id()
    relpfad = (request.form.get("pfad") or "").strip()
    ok = dms.rename(vid, relpfad, request.form.get("name") or "")
    if ok:
        neu = (_eltern(relpfad) + "/" + dms.sicherer_name(request.form.get("name") or "")).strip("/")
        dms.zugriff_pfad_umziehen(vid, relpfad, neu)
    flash("Umbenannt." if ok else "Umbenennen nicht möglich (Name schon vergeben?).", "erfolg" if ok else "fehler")
    return redirect(url_for("dms.index", pfad=_eltern(relpfad)))


@bp.route("/verschieben", methods=["POST"])
def verschieben():
    vid = aktiver_verein_id()
    relpfad = (request.form.get("pfad") or "").strip()
    ziel = (request.form.get("ziel") or "").strip()
    ok = dms.verschieben(vid, relpfad, ziel)
    if ok:
        neu = (ziel.strip("/") + "/" + os.path.basename(relpfad.strip("/"))).strip("/")
        dms.zugriff_pfad_umziehen(vid, relpfad, neu)
    flash("Verschoben." if ok else "Verschieben nicht möglich.", "erfolg" if ok else "fehler")
    return redirect(url_for("dms.index", pfad=ziel if ok else _eltern(relpfad)))


@bp.route("/download")
def download():
    vid = aktiver_verein_id()
    ziel = dms.aufloesen(vid, request.args.get("pfad"))
    if not ziel or not os.path.isfile(ziel):
        abort(404)
    return send_file(ziel, as_attachment=True, download_name=os.path.basename(ziel))


@bp.route("/vorschau")
def vorschau():
    vid = aktiver_verein_id()
    ziel = dms.aufloesen(vid, request.args.get("pfad"))
    if not ziel or not os.path.isfile(ziel):
        abort(404)
    # Nur harmlose Dateitypen direkt anzeigen; HTML/SVG aus der Ablage würden
    # sonst als Seite unserer eigenen Adresse laufen (Cross-Site-Scripting).
    import mimetypes as _mt
    typ = _mt.guess_type(ziel)[0] or ""
    if typ not in speicher.ANZEIGBAR:
        return send_file(ziel, as_attachment=True, download_name=os.path.basename(ziel),
                         mimetype="application/octet-stream")
    # Passive Typen dürfen in die Vorschau – Kopfzeilen zentral (speicher.inline_schutz):
    # die alte Sandbox-CSP schaltete hier den PDF-Viewer ab (behoben 05.10.).
    return speicher.inline_schutz(send_file(ziel, mimetype=typ))


@bp.route("/zip")
def download_zip():
    vid = aktiver_verein_id()
    relpfad = (request.args.get("pfad") or "").strip()
    ordner = dms.aufloesen(vid, relpfad)
    if not ordner or not os.path.isdir(ordner):
        abort(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, _, fns in os.walk(ordner):
            for f in fns:
                if f.startswith("."):
                    continue
                voll = os.path.join(dp, f)
                z.write(voll, os.path.relpath(voll, ordner))
    buf.seek(0)
    name = (os.path.basename(ordner) or "dateien") + ".zip"
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name=name)


@bp.route("/suche")
def suche():
    vid = aktiver_verein_id()
    begriff = (request.args.get("q") or "").strip()
    treffer = dms.suche(vid, begriff, mitglied=False) if begriff else []
    return render_template("dms/suche.html", aktiv="dms", begriff=begriff, treffer=treffer)


@bp.route("/loeschen", methods=["POST"])
def loeschen():
    vid = aktiver_verein_id()
    relpfad = (request.form.get("pfad") or "").strip()
    if dms.loeschen(vid, relpfad):
        dms.zugriff_pfad_loeschen(vid, relpfad)
    flash("Gelöscht.", "erfolg")
    return redirect(url_for("dms.index", pfad=_eltern(relpfad)))
