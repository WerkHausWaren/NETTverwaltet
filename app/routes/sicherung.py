"""
Datensicherung: Status anzeigen, manuell sichern, aus einer Sicherung wiederherstellen.

Die eigentliche Sicherungslogik liegt im eigenständigen Skript backup.py
(Projektwurzel), damit sie auch per Wochen-Zeitplan ohne die App laufen kann.
"""
import os
import sys
import sqlite3
from flask import Blueprint, render_template, redirect, url_for, flash, request
from ..db import BASE_DIR, verein_db_pfad, aktiver_verein_id, get_einstellungen

bp = Blueprint("sicherung", __name__, url_prefix="/sicherung")


def _backup_modul():
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)
    import backup
    return backup


@bp.route("/")
def index():
    b = _backup_modul()
    return render_template("sicherung/index.html", aktiv="sicherung",
                           letztes=b.letztes_backup(),
                           ordner=b.backup_ordner(),
                           in_onedrive=b.onedrive_basis() is not None,
                           backups=b.liste_backups())


@bp.route("/jetzt", methods=["POST"])
def jetzt():
    b = _backup_modul()
    ziel, in_onedrive = b.erstelle_backup()
    if ziel:
        # Seit 17.11.2026 KEIN Upload mehr in die Nextcloud: Das waren unverschlüsselte
        # Kopien der Vereinsdatenbank. Off-site sichert /opt/backup-server.sh verschlüsselt.
        wohin = "OneDrive" if in_onedrive else "lokal"
        flash(f"Sicherung erstellt ({wohin}): {os.path.basename(ziel)}", "erfolg")
    else:
        flash("Es gibt noch keine Daten zum Sichern.", "info")
    return redirect(url_for("sicherung.index"))


@bp.route("/wiederherstellen", methods=["POST"])
def wiederherstellen():
    b = _backup_modul()
    name = request.form.get("name", "")
    # Nur Dateien aus dem Backup-Ordner mit erwartetem Namensmuster zulassen
    if not name.startswith("verein-backup_") or not name.endswith(".db") or "/" in name:
        flash("Ungültige Auswahl.", "fehler")
        return redirect(url_for("sicherung.index"))
    quelle = os.path.join(b.backup_ordner(), name)
    if not os.path.exists(quelle):
        flash("Sicherungsdatei nicht gefunden.", "fehler")
        return redirect(url_for("sicherung.index"))

    # Vor dem Zurückspielen den aktuellen Stand sichern (Schutz)
    try:
        b.erstelle_backup()
    except Exception:
        pass

    # Sicherung in die aktive Vereins-Datenbank zurückspielen (konsistent über SQLite-API)
    try:
        src = sqlite3.connect(quelle)
        dst = sqlite3.connect(verein_db_pfad(aktiver_verein_id()))
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        flash("Daten wurden aus der Sicherung wiederhergestellt.", "erfolg")
    except Exception as e:
        flash(f"Wiederherstellung fehlgeschlagen: {e}", "fehler")
    return redirect(url_for("sicherung.index"))
