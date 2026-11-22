"""
Automatische Datensicherung in die Nextcloud – STILLGELEGT seit 17.11.2026.

Die Kopien waren UNVERSCHLÜSSELT (Mitgliederdaten mit IBAN im Klartext in der
Nextcloud). Off-site sichert jetzt /opt/backup-server.sh age-verschlüsselt.
``sichere_alle`` lädt deshalb nichts mehr hoch; nur mit der Umgebungsvariable
``VV_KLARTEXT_SICHERUNG_ERLAUBT=1`` ließe sie sich bewusst wieder einschalten.

Erstellt von der Registry (system.db) und JEDER Vereins-Datenbank eine
konsistente SQLite-Kopie (Online-Backup-API, auch bei laufender App sicher)
und lädt sie in die Nextcloud unter „Vereinsverwaltung/Sicherungen". Je
Datenbank werden die letzten ``MAX_JE_DB`` Sicherungen behalten, ältere
werden entfernt.

Aufruf auf dem Server per Cron (täglich):
    docker compose exec -T app python -c \
      "from app import create_app; create_app().app_context().push(); \
       from app.sicherung_auto import sichere_alle; print(sichere_alle())"
"""
import os
import sqlite3
import tempfile
from datetime import datetime

from . import db, webdav, krypto, speicher

MAX_JE_DB = 30  # letzte 30 Sicherungen je Datenbank behalten


def _einst_fuer(vid):
    """Einstellungen eines Vereins mit entschlüsseltem Nextcloud-Passwort."""
    conn = db.get_db_fuer(vid)
    try:
        row = conn.execute("SELECT * FROM vereine WHERE id=?", (vid,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    e = dict(row)
    if "ncc_pass" in e:
        e["ncc_pass"] = krypto.entschluesseln(e["ncc_pass"])
    return e


def _kopie_bytes(quelle_pfad):
    """Konsistente SQLite-Sicherung einer DB als Bytes (Online-Backup-API)."""
    fd, ziel = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src = sqlite3.connect(quelle_pfad)
        dst = sqlite3.connect(ziel)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with open(ziel, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(ziel)
        except OSError:
            pass


def _aufraeumen(einst, ordner, praefix):
    """Behält je Präfix nur die letzten MAX_JE_DB Sicherungen (nach Name = Zeit)."""
    namen = sorted(n for n in webdav.liste(einst, ordner)
                   if n.startswith(praefix) and n.endswith(".db"))
    for alt in namen[:-MAX_JE_DB] if len(namen) > MAX_JE_DB else []:
        webdav.delete(einst, f"{ordner}/{alt}")


def sichere_alle():
    """Sichert Registry + alle Vereins-DBs nach Nextcloud. Gibt (anzahl, meldungen) zurück."""
    if os.environ.get("VV_KLARTEXT_SICHERUNG_ERLAUBT") != "1":
        return 0, ["stillgelegt: unverschlüsselte Nextcloud-Sicherung ist abgeschaltet "
                   "(verschlüsselte Sicherung über /opt/backup-server.sh)"]
    zeit = datetime.now().strftime("%Y%m%d-%H%M%S")
    ordner = f"{speicher.UNTERORDNER}/Sicherungen"
    sys_conn = db.get_system_db()
    try:
        vids = [r["id"] for r in sys_conn.execute(
            "SELECT id FROM vereine_index ORDER BY id").fetchall()]
    finally:
        sys_conn.close()

    anzahl, meldungen, system_gesichert = 0, [], False
    for vid in vids:
        einst = _einst_fuer(vid)
        if not einst or not webdav.konfiguriert(einst):
            meldungen.append(f"Verein {vid}: keine Nextcloud konfiguriert – übersprungen")
            continue
        try:
            webdav.ordner_sicherstellen(einst, ordner)
            daten = _kopie_bytes(db.verein_db_pfad(vid))
            if webdav.put(einst, f"{ordner}/verein_{vid}_{zeit}.db", daten):
                anzahl += 1
                _aufraeumen(einst, ordner, f"verein_{vid}_")
                meldungen.append(f"Verein {vid}: gesichert ({len(daten) // 1024} KB)")
            else:
                meldungen.append(f"Verein {vid}: Upload fehlgeschlagen")
            # Registry (system.db) einmal mitsichern – bei der ersten Nextcloud
            if not system_gesichert:
                sdaten = _kopie_bytes(db.SYSTEM_DB_PATH)
                if webdav.put(einst, f"{ordner}/system_{zeit}.db", sdaten):
                    _aufraeumen(einst, ordner, "system_")
                    system_gesichert = True
        except Exception as e:  # noqa: BLE001 – eine kaputte Nextcloud darf die anderen nicht stoppen
            meldungen.append(f"Verein {vid}: FEHLER {e}")
    return anzahl, meldungen
