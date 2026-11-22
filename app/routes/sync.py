"""Daten-Abgleich Online → Offline für WerkHaus. Die Online-Verwaltung hat Priorität.

Zwei Rollen (derselbe Code, unterschiedliche Konfiguration):
- SERVER (VV_SYNC_TOKEN gesetzt): STELLT die WerkHaus-Daten bereit – /sync/status und
  /sync/paket, geschützt über den Token (kein Login, da Maschine-zu-Maschine).
- LOKAL (data/sync.json vorhanden): HOLT die Daten – Vorschau + Übernahme. Dabei wird
  die lokale verein_1.db (+ avatare, Logo) durch die Online-Version ERSETZT. system.db
  wird NIE angefasst (lokal angelegte Vereine bleiben erhalten). Vorher: Sicherung.
"""
import os
import io
import json
import hmac
import shutil
import zipfile
import sqlite3
import tempfile
from datetime import datetime

import requests
from flask import (Blueprint, request, jsonify, render_template, redirect,
                   url_for, flash, current_app, abort, send_file)

from ..db import DATA_DIR, verein_db_pfad

bp = Blueprint("sync", __name__, url_prefix="/sync")

WERKHAUS_VID = 1                                   # nur WerkHaus wird abgeglichen
SYNC_CONF = os.path.join(DATA_DIR, "sync.json")    # lokale Client-Konfiguration


# --------------------------------------------------------------------------
def _server_token():
    return (current_app.config.get("VV_SYNC_TOKEN") or "").strip()


def _token_ok():
    tok = _server_token()
    return bool(tok) and hmac.compare_digest(request.args.get("token", ""), tok)


def _client_conf():
    """Lokale Sync-Konfiguration (Quelle + Token) aus data/sync.json, sonst None."""
    try:
        with open(SYNC_CONF, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("quelle") and c.get("token"):
            return c
    except Exception:  # noqa: BLE001
        pass
    return None


def _db_pfad():
    return verein_db_pfad(WERKHAUS_VID)


def _counts(pfad):
    """Zeilenzahl je Tabelle der WerkHaus-Datenbank (für die Vorschau)."""
    con = sqlite3.connect(pfad)
    try:
        tabs = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: con.execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()[0] for t in tabs}
    finally:
        con.close()


# ============================ SERVER: bereitstellen ========================
@bp.route("/status")
def status():
    if not _token_ok():
        abort(403)
    return jsonify({"counts": _counts(_db_pfad()),
                    "erzeugt": datetime.now().strftime("%d.%m.%Y %H:%M"),
                    "verein_id": WERKHAUS_VID})


@bp.route("/paket")
def paket():
    if not _token_ok():
        abort(403)
    quelle_db = _db_pfad()
    # Konsistente Kopie der DB (SQLite-Backup, auch bei parallelen Schreibzugriffen sicher)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db", dir=DATA_DIR)
    tmp.close()
    src = sqlite3.connect(quelle_db)
    dst = sqlite3.connect(tmp.name)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp.name, "verein_1.db")
        avdir = os.path.join(DATA_DIR, "avatare")
        if os.path.isdir(avdir):
            for fn in os.listdir(avdir):
                fp = os.path.join(avdir, fn)
                if os.path.isfile(fp):
                    z.write(fp, f"avatare/{fn}")
        lodir = os.path.join(DATA_DIR, "logos")
        if os.path.isdir(lodir):
            for fn in os.listdir(lodir):
                if fn.startswith(f"verein_{WERKHAUS_VID}."):
                    z.write(os.path.join(lodir, fn), f"logos/{fn}")
        z.writestr("manifest.json", json.dumps({
            "counts": _counts(quelle_db),
            "erzeugt": datetime.now().strftime("%d.%m.%Y %H:%M")}))
    os.unlink(tmp.name)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="werkhaus-sync.zip")


# ============================ LOKAL: abrufen ===============================
@bp.route("/")
def index():
    conf = _client_conf()
    return render_template("sync/index.html", aktiv="sync", conf=conf, diff=None,
                           quelle=(conf or {}).get("quelle"), online_stand=None)


@bp.route("/vorschau", methods=["POST"])
def vorschau():
    conf = _client_conf()
    if not conf:
        abort(404)
    try:
        r = requests.get(conf["quelle"].rstrip("/") + "/sync/status",
                         params={"token": conf["token"]}, timeout=25)
        r.raise_for_status()
        online = r.json()
    except Exception as e:  # noqa: BLE001
        flash(f"Online-Verwaltung nicht erreichbar: {e}", "fehler")
        return redirect(url_for("sync.index"))
    lokal = _counts(_db_pfad())
    diff = _diff(lokal, online["counts"])
    return render_template("sync/index.html", aktiv="sync", conf=conf, diff=diff,
                           quelle=conf["quelle"], online_stand=online.get("erzeugt"))


@bp.route("/anwenden", methods=["POST"])
def anwenden():
    conf = _client_conf()
    if not conf:
        abort(404)
    try:
        r = requests.get(conf["quelle"].rstrip("/") + "/sync/paket",
                         params={"token": conf["token"]}, timeout=180)
        r.raise_for_status()
        daten = r.content
    except Exception as e:  # noqa: BLE001
        flash(f"Download von der Online-Verwaltung fehlgeschlagen: {e}", "fehler")
        return redirect(url_for("sync.index"))
    try:
        backup = _paket_anwenden(daten)
    except Exception as e:  # noqa: BLE001
        flash(f"Übernahme fehlgeschlagen (lokale Daten unverändert): {e}", "fehler")
        return redirect(url_for("sync.index"))
    flash(f"Daten der Online-Verwaltung übernommen. Sicherung: {os.path.basename(backup)}", "erfolg")
    return redirect(url_for("sync.index"))


# --------------------------------------------------------------------------
def _diff(lokal, online):
    zeilen = []
    for t in sorted(set(lokal) | set(online)):
        l, o = lokal.get(t, 0), online.get(t, 0)
        zeilen.append({"tabelle": t, "lokal": l, "online": o, "delta": o - l})
    return zeilen


def _paket_anwenden(daten):
    """Ersetzt lokal verein_1.db + avatare + Logo durch die Online-Version.
    system.db bleibt unangetastet. Gibt den Sicherungspfad zurück."""
    z = zipfile.ZipFile(io.BytesIO(daten))
    if "verein_1.db" not in z.namelist():
        raise ValueError("Paket enthält keine verein_1.db")

    arbeit = tempfile.mkdtemp(dir=DATA_DIR)         # gleiches Dateisystem → os.replace atomar
    try:
        z.extractall(arbeit)
        neue_db = os.path.join(arbeit, "verein_1.db")

        # Heruntergeladene DB prüfen, bevor irgendetwas ersetzt wird
        con = sqlite3.connect(neue_db)
        try:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Heruntergeladene Datenbank ist beschädigt")
            con.execute("SELECT COUNT(*) FROM mitglieder").fetchone()  # Plausibilität
        finally:
            con.close()

        # Sicherung der aktuellen lokalen Daten
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(DATA_DIR, f"sync_backup_{stamp}")
        os.makedirs(backup, exist_ok=True)
        ziel_db = _db_pfad()
        if os.path.exists(ziel_db):
            shutil.copy2(ziel_db, os.path.join(backup, "verein_1.db"))
        av = os.path.join(DATA_DIR, "avatare")
        if os.path.isdir(av):
            shutil.copytree(av, os.path.join(backup, "avatare"))

        # DB atomar ersetzen
        os.replace(neue_db, ziel_db)

        # Avatare übernehmen (überschreibend)
        src_av = os.path.join(arbeit, "avatare")
        if os.path.isdir(src_av):
            os.makedirs(av, exist_ok=True)
            for fn in os.listdir(src_av):
                shutil.copy2(os.path.join(src_av, fn), os.path.join(av, fn))

        # Logo übernehmen
        src_lo = os.path.join(arbeit, "logos")
        if os.path.isdir(src_lo):
            lo = os.path.join(DATA_DIR, "logos")
            os.makedirs(lo, exist_ok=True)
            for fn in os.listdir(src_lo):
                shutil.copy2(os.path.join(src_lo, fn), os.path.join(lo, fn))
        return backup
    finally:
        shutil.rmtree(arbeit, ignore_errors=True)
