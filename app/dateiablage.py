"""
Dateiablage (DMS) – einfache, browserbasierte Dokumentenablage je Verein.

Zwei Rechtestufen:
  * Vorstand (Admin): voller Zugriff (lesen, hochladen, Ordner anlegen, löschen).
  * Mitglieder: nur lesen/herunterladen; der Ordner „Vorstand" ist für sie unsichtbar.

Alle Pfade werden gegen Path-Traversal abgesichert (Ablage bleibt im Vereins-Wurzelordner).
"""
import os
import shutil
from datetime import datetime

from .db import DATA_DIR

VORSTAND_ORDNER = "Vorstand"
STANDARD_ORDNER = [
    "Vorstand",
    "Protokolle",
    "Finanzen",
    "Mitglieder",
    "Veranstaltungen",
    "Öffentlichkeitsarbeit",
    "Satzung & Dokumente",
]


def root(vid):
    p = os.path.join(DATA_DIR, "dms", f"verein_{vid}")
    os.makedirs(p, exist_ok=True)
    return p


def struktur_sicherstellen(vid):
    """Legt die Standard-Ordnerstruktur an, wenn die Ablage noch leer ist."""
    r = root(vid)
    if not os.listdir(r):
        for name in STANDARD_ORDNER:
            os.makedirs(os.path.join(r, name), exist_ok=True)


def aufloesen(vid, relpfad):
    """Sicherer absoluter Pfad innerhalb der Ablage – oder None bei Traversal-Versuch."""
    r = root(vid)
    rel = (relpfad or "").replace("\\", "/").strip().lstrip("/")
    ziel = os.path.normpath(os.path.join(r, rel))
    if ziel != r and not ziel.startswith(r + os.sep):
        return None
    return ziel


def ist_vorstand(relpfad):
    teile = [t for t in (relpfad or "").replace("\\", "/").strip("/").split("/") if t]
    return bool(teile) and teile[0] == VORSTAND_ORDNER


# ------------------- Ordner-Sichtbarkeit je Gruppe ------------------------
def zugriffsregeln(vid):
    """{ordnerpfad: set(gruppe_ids)} – nur Ordner MIT Einschränkung tauchen auf."""
    from .db import get_db
    conn = get_db()
    regeln = {}
    for r in conn.execute("SELECT pfad, gruppe_id FROM dms_zugriff WHERE verein_id=?", (vid,)).fetchall():
        regeln.setdefault(r["pfad"], set()).add(r["gruppe_id"])
    conn.close()
    return regeln


def darf_sehen(relpfad, regeln, gruppen_ids):
    """True, wenn jede eingeschränkte Ebene des Pfads eine Gruppe des Mitglieds erlaubt."""
    if not regeln:
        return True
    gruppen = set(gruppen_ids or [])
    akk = ""
    for teil in [t for t in (relpfad or "").replace("\\", "/").strip("/").split("/") if t]:
        akk = (akk + "/" + teil).strip("/")
        erlaubt = regeln.get(akk)
        if erlaubt and not (gruppen & erlaubt):
            return False
    return True


def zugriff_pfad_umziehen(vid, alt, neu):
    """Zieht Sichtbarkeitsregeln nach Umbenennen/Verschieben auf den neuen Pfad um."""
    from .db import get_db
    alt = (alt or "").strip("/")
    neu = (neu or "").strip("/")
    if not alt or alt == neu:
        return
    conn = get_db()
    conn.execute("UPDATE dms_zugriff SET pfad=? WHERE verein_id=? AND pfad=?", (neu, vid, alt))
    for r in conn.execute("SELECT id, pfad FROM dms_zugriff WHERE verein_id=? AND pfad LIKE ?",
                          (vid, alt + "/%")).fetchall():
        conn.execute("UPDATE dms_zugriff SET pfad=? WHERE id=?", (neu + r["pfad"][len(alt):], r["id"]))
    conn.commit()
    conn.close()


def zugriff_pfad_loeschen(vid, relpfad):
    """Entfernt Sichtbarkeitsregeln eines gelöschten Ordners (inkl. Unterordner)."""
    from .db import get_db
    relpfad = (relpfad or "").strip("/")
    if not relpfad:
        return
    conn = get_db()
    conn.execute("DELETE FROM dms_zugriff WHERE verein_id=? AND (pfad=? OR pfad LIKE ?)",
                 (vid, relpfad, relpfad + "/%"))
    conn.commit()
    conn.close()


def mitglied_gruppen_ids(mitglied_id):
    """Gruppen-IDs eines Mitglieds (für die Sichtbarkeitsprüfung im Portal)."""
    from .db import get_db
    conn = get_db()
    ids = [r["gruppe_id"] for r in conn.execute(
        "SELECT gruppe_id FROM mitglied_gruppe WHERE mitglied_id=?", (mitglied_id,)).fetchall()]
    conn.close()
    return ids


def sicherer_name(name):
    """Dateiname/Ordnername ohne Pfadtrenner und gefährliche Zeichen (Umlaute bleiben)."""
    name = os.path.basename((name or "").replace("\\", "/"))
    name = name.replace("/", "").strip().strip(".")
    for c in '<>:"|?*\0':
        name = name.replace(c, "")
    return name[:120] or "unbenannt"


def _geaendert(ts):
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")
    except (ValueError, OSError):
        return ""


def liste(vid, relpfad, mitglied=False, sort="name", gruppen_ids=None, regeln=None):
    """Inhalt eines Ordners: {ordner:[...], dateien:[...]} – oder None (nicht gefunden/kein Zugriff).

    Für Mitglieder werden Ordner ausgeblendet, deren Sichtbarkeit auf Gruppen
    eingeschränkt ist, denen das Mitglied nicht angehört (regeln=zugriffsregeln(vid))."""
    ziel = aufloesen(vid, relpfad)
    if not ziel or not os.path.isdir(ziel):
        return None
    if mitglied and ist_vorstand(relpfad):
        return None
    if mitglied and regeln is None:
        regeln = zugriffsregeln(vid)
    if mitglied and not darf_sehen(relpfad, regeln, gruppen_ids):
        return None
    ordner, dateien = [], []
    for name in os.listdir(ziel):
        if name.startswith("."):
            continue
        voll = os.path.join(ziel, name)
        rel = (relpfad.strip("/") + "/" + name).strip("/")
        try:
            mtime = os.path.getmtime(voll)
        except OSError:
            mtime = 0
        if os.path.isdir(voll):
            if mitglied and not relpfad.strip("/") and name == VORSTAND_ORDNER:
                continue  # Vorstand-Ordner für Mitglieder ausblenden
            if mitglied and not darf_sehen(rel, regeln, gruppen_ids):
                continue  # Ordner ist auf andere Gruppen eingeschränkt
            anzahl = 0
            try:
                anzahl = len([x for x in os.listdir(voll) if not x.startswith(".")])
            except OSError:
                pass
            ordner.append({"name": name, "pfad": rel, "anzahl": anzahl, "mtime": mtime, "geaendert": _geaendert(mtime)})
        else:
            dateien.append({"name": name, "pfad": rel, "groesse": os.path.getsize(voll),
                            "mtime": mtime, "geaendert": _geaendert(mtime)})
    if sort == "datum":
        ordner.sort(key=lambda x: -x["mtime"])
        dateien.sort(key=lambda x: -x["mtime"])
    elif sort == "groesse":
        ordner.sort(key=lambda x: x["name"].lower())
        dateien.sort(key=lambda x: -x["groesse"])
    else:
        ordner.sort(key=lambda x: x["name"].lower())
        dateien.sort(key=lambda x: x["name"].lower())
    return {"ordner": ordner, "dateien": dateien}


IKONEN = {
    ".pdf": "📕", ".doc": "📘", ".docx": "📘", ".odt": "📘", ".rtf": "📘",
    ".xls": "📗", ".xlsx": "📗", ".ods": "📗", ".csv": "📗",
    ".ppt": "📙", ".pptx": "📙", ".odp": "📙",
    ".jpg": "🖼️", ".jpeg": "🖼️", ".png": "🖼️", ".gif": "🖼️", ".webp": "🖼️", ".bmp": "🖼️", ".svg": "🖼️", ".heic": "🖼️",
    ".zip": "🗜️", ".rar": "🗜️", ".7z": "🗜️", ".tar": "🗜️", ".gz": "🗜️",
    ".mp3": "🎵", ".wav": "🎵", ".m4a": "🎵", ".ogg": "🎵",
    ".mp4": "🎬", ".mov": "🎬", ".avi": "🎬", ".mkv": "🎬", ".webm": "🎬",
    ".txt": "📄", ".md": "📄",
}


def icon(name):
    return IKONEN.get(os.path.splitext(name or "")[1].lower(), "📄")


def vorschaubar(name):
    # Kein .svg: SVG kann Skripte tragen und wird darum nie inline ausgeliefert
    # (speicher.ANZEIGBAR) – ein Vorschau-Versprechen wäre gelogen.
    return os.path.splitext(name or "")[1].lower() in (
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".pdf")


def alle_ordner(vid, mitglied=False):
    """Alle Ordner (rekursiv) als Liste {name(mit Einrückung), pfad} – für „Verschieben nach"."""
    r = root(vid)
    ergebnis = [{"name": "🏠 Hauptordner", "pfad": ""}]
    paare = []
    for dirpath, dirnames, _ in os.walk(r):
        dirnames[:] = sorted([d for d in dirnames if not d.startswith(".")], key=str.lower)
        for d in dirnames:
            rel = os.path.relpath(os.path.join(dirpath, d), r).replace("\\", "/")
            if mitglied and ist_vorstand(rel):
                continue
            paare.append(rel)
    for rel in sorted(paare, key=str.lower):
        tiefe = rel.count("/")
        ergebnis.append({"name": "   " * tiefe + "📁 " + rel.split("/")[-1], "pfad": rel})
    return ergebnis


def rename(vid, relpfad, neuer_name):
    quelle = aufloesen(vid, relpfad)
    neuer_name = sicherer_name(neuer_name)
    if not quelle or quelle == root(vid) or not os.path.exists(quelle) or not neuer_name:
        return False
    ziel = os.path.join(os.path.dirname(quelle), neuer_name)
    if os.path.exists(ziel):
        return False
    os.rename(quelle, ziel)
    return True


def verschieben(vid, relpfad, ziel_ordner_rel):
    quelle = aufloesen(vid, relpfad)
    zielordner = aufloesen(vid, ziel_ordner_rel)
    if not quelle or not zielordner or not os.path.isdir(zielordner) or quelle == root(vid):
        return False
    if os.path.isdir(quelle) and (zielordner == quelle or zielordner.startswith(quelle + os.sep)):
        return False  # nicht in sich selbst verschieben
    ziel = os.path.join(zielordner, os.path.basename(quelle))
    if os.path.exists(ziel):
        return False
    shutil.move(quelle, ziel)
    return True


def suche(vid, begriff, mitglied=False, grenze=200, gruppen_ids=None):
    r = root(vid)
    begriff = (begriff or "").lower().strip()
    if not begriff:
        return []
    regeln = zugriffsregeln(vid) if mitglied else None
    treffer = []
    for dirpath, dirnames, filenames in os.walk(r):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel_dir = "" if dirpath == r else os.path.relpath(dirpath, r).replace("\\", "/")
        if mitglied and (ist_vorstand(rel_dir) or not darf_sehen(rel_dir, regeln, gruppen_ids)):
            dirnames[:] = []
            continue
        for f in filenames:
            if f.startswith(".") or begriff not in f.lower():
                continue
            voll = os.path.join(dirpath, f)
            treffer.append({"name": f, "pfad": (rel_dir + "/" + f).strip("/"),
                            "ordner": rel_dir or "Hauptordner",
                            "groesse": os.path.getsize(voll), "geaendert": _geaendert(os.path.getmtime(voll))})
            if len(treffer) >= grenze:
                return treffer
    return treffer


def alle_dateien(vid, mitglied=False):
    """Alle Dateien (rekursiv) als Liste {name, pfad, ordner} – z. B. für Mail-Anhänge."""
    r = root(vid)
    ergebnis = []
    for dirpath, dirnames, filenames in os.walk(r):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel_dir = "" if dirpath == r else os.path.relpath(dirpath, r).replace("\\", "/")
        if mitglied and ist_vorstand(rel_dir):
            dirnames[:] = []
            continue
        for f in sorted([x for x in filenames if not x.startswith(".")], key=str.lower):
            ergebnis.append({"name": f, "pfad": (rel_dir + "/" + f).strip("/"),
                             "ordner": rel_dir or "Hauptordner"})
    return ergebnis


def groesse_gesamt(vid):
    total = 0
    for dirpath, _, filenames in os.walk(root(vid)):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def quota_bytes(vid):
    """Speicherlimit der Dateiablage in Bytes (0 = unbegrenzt)."""
    from .db import get_db
    row = get_db().execute("SELECT dms_quota_mb FROM vereine WHERE id=?", (vid,)).fetchone()
    mb = row["dms_quota_mb"] if row and row["dms_quota_mb"] is not None else 500
    try:
        mb = int(mb)
    except (TypeError, ValueError):
        mb = 500
    return max(mb, 0) * 1024 * 1024


def loeschen(vid, relpfad):
    ziel = aufloesen(vid, relpfad)
    if not ziel or ziel == root(vid) or not os.path.exists(ziel):
        return False
    if os.path.isdir(ziel):
        shutil.rmtree(ziel)
    else:
        os.remove(ziel)
    return True
