"""
Datensicherung der Vereinsverwaltung.

Erstellt eine konsistente Kopie der Datenbank (data/verein.db).
Ziel: OneDrive-Ordner (automatische Cloud-Sicherung), sonst lokal in data/backups.

Dieses Skript nutzt NUR die Python-Standardbibliothek und ist unabhängig von Flask.
Dadurch kann es auch über einen wöchentlichen macOS-Zeitplan (launchd) laufen,
selbst wenn die App gerade nicht geöffnet ist.

Direktaufruf:  python3 backup.py
"""
import os
import glob
import sqlite3
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("VV_DATA_DIR") or os.path.join(BASE_DIR, "data")
SYSTEM_DB_PATH = os.path.join(DATA_DIR, "system.db")
LEGACY_DB_PATH = os.path.join(DATA_DIR, "verein.db")
MARKER = os.path.join(DATA_DIR, "letztes_backup.txt")


def _aktive_db_pfad():
    """Datenbank-Datei des aktiven Vereins (aus der Registry); Fallback: Alt-DB."""
    if os.path.exists(SYSTEM_DB_PATH):
        try:
            conn = sqlite3.connect(SYSTEM_DB_PATH)
            row = conn.execute("SELECT aktiver_verein_id FROM system WHERE id=1").fetchone()
            conn.close()
            if row and row[0]:
                pfad = os.path.join(DATA_DIR, f"verein_{row[0]}.db")
                if os.path.exists(pfad):
                    return pfad
        except sqlite3.Error:
            pass
    return LEGACY_DB_PATH

MAX_BACKUPS = 12            # so viele Sicherungen aufbewahren
ONEDRIVE_UNTERORDNER = "Vereinsverwaltung"

_ONEDRIVE_KANDIDATEN = [
    os.path.expanduser("~/Library/CloudStorage/OneDrive-Persönlich"),
    os.path.expanduser("~/Library/CloudStorage/OneDrive-Personal"),
    os.path.expanduser("~/OneDrive"),
]


def onedrive_basis():
    for p in _ONEDRIVE_KANDIDATEN:
        if os.path.isdir(p):
            return p
    treffer = glob.glob(os.path.expanduser("~/Library/CloudStorage/OneDrive-*"))
    return treffer[0] if treffer else None


def backup_ordner():
    """Zielordner für Sicherungen (OneDrive bevorzugt, sonst lokal)."""
    basis = onedrive_basis()
    if basis:
        ordner = os.path.join(basis, ONEDRIVE_UNTERORDNER, "Sicherungen")
    else:
        ordner = os.path.join(DATA_DIR, "backups")
    os.makedirs(ordner, exist_ok=True)
    return ordner


def erstelle_backup():
    """Erstellt eine Sicherung der aktiven Vereins-DB und gibt (pfad, onedrive?) zurück."""
    db_pfad = _aktive_db_pfad()
    if not os.path.exists(db_pfad):
        return None, False
    ordner = backup_ordner()
    zeit = time.strftime("%Y-%m-%d_%H%M")
    ziel = os.path.join(ordner, f"verein-backup_{zeit}.db")

    # Konsistente Kopie über die SQLite-Backup-API (auch bei geöffneter DB sicher)
    quelle = sqlite3.connect(db_pfad)
    kopie = sqlite3.connect(ziel)
    with kopie:
        quelle.backup(kopie)
    kopie.close()
    quelle.close()

    _alte_entfernen(ordner)
    with open(MARKER, "w") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    return ziel, onedrive_basis() is not None


def _alte_entfernen(ordner):
    dateien = sorted(glob.glob(os.path.join(ordner, "verein-backup_*.db")))
    while len(dateien) > MAX_BACKUPS:
        try:
            os.remove(dateien.pop(0))
        except OSError:
            break


def letztes_backup():
    """Zeitpunkt der letzten Sicherung als Text oder None."""
    if os.path.exists(MARKER):
        try:
            with open(MARKER) as f:
                return f.read().strip()
        except OSError:
            return None
    return None


def _tage_seit_backup():
    stand = letztes_backup()
    if not stand:
        return None
    try:
        t = time.mktime(time.strptime(stand, "%Y-%m-%d %H:%M:%S"))
        return (time.time() - t) / 86400.0
    except ValueError:
        return None


def backup_wenn_faellig(tage=7):
    """Erstellt eine Sicherung, wenn die letzte länger als `tage` her ist."""
    seit = _tage_seit_backup()
    if seit is None or seit >= tage:
        try:
            return erstelle_backup()
        except Exception:
            return None, False
    return None, False


def liste_backups():
    """Vorhandene Sicherungen (neueste zuerst) mit Größe und Zeit."""
    ordner = backup_ordner()
    eintraege = []
    for pfad in sorted(glob.glob(os.path.join(ordner, "verein-backup_*.db")), reverse=True):
        eintraege.append({
            "name": os.path.basename(pfad),
            "pfad": pfad,
            "groesse_kb": round(os.path.getsize(pfad) / 1024, 1),
            "zeit": time.strftime("%d.%m.%Y %H:%M", time.localtime(os.path.getmtime(pfad))),
        })
    return eintraege


if __name__ == "__main__":
    ziel, in_onedrive = erstelle_backup()
    if ziel:
        print(f"Sicherung erstellt: {ziel}" + (" (OneDrive)" if in_onedrive else " (lokal)"))
    else:
        print("Keine Datenbank gefunden – nichts gesichert.")
