"""
Wächter: Sicherungen landen nur auf ausdrücklichen Wunsch in OneDrive (Vorfall 22.11.2026).

Hintergrund: backup.py bevorzugte früher jeden gefundenen OneDrive-Ordner. Jeder Testlauf
und jeder Export startete die App mit frischem Datenordner, legte dabei eine (leere)
Sicherung in OneDrive ab – und die Aufbewahrungsregel (12 Dateien) löschte die echten.
In fremden Installationen hätte das Mitgliederdaten ungefragt in eine Cloud kopiert.

Der Test baut ein Schein-Zuhause mit OneDrive-Ordner und prüft:
  1. ohne Schalter: App-Start und „Jetzt sichern“ schreiben NICHTS nach OneDrive,
  2. mit Schalter-Datei: Sicherung landet in OneDrive,
  3. die Aufbewahrungsregel löscht nur im eigenen Ordner.

Start (aus dem Projektordner):  .venv/bin/python tests/test_sicherung_ort.py
"""
import importlib
import os
import pathlib
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-sicherung-ort-")
HEIM = os.path.join(TMP, "heim")
WOLKE = os.path.join(HEIM, "Library", "CloudStorage", "OneDrive-Personal")
os.makedirs(WOLKE)
DATEN = os.path.join(TMP, "daten")
os.environ.update(HOME=HEIM, VV_DATA_DIR=DATEN, SECRET_KEY="sicherungtest")
os.environ.pop("VV_SICHERUNG_ONEDRIVE", None)
os.environ.pop("VV_PRODUKTION", None)
WURZEL = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL))

FEHLER = []


def pruefe(bedingung, text):
    print(("✔ " if bedingung else "✘ ") + text)
    if not bedingung:
        FEHLER.append(text)


def dateien_in_wolke():
    return [os.path.join(w, d) for w, _, ds in os.walk(WOLKE) for d in ds]


try:
    from app import create_app  # noqa: E402
    app = create_app()
    import backup  # noqa: E402  (erst nach create_app – DATA_DIR kommt aus der Umgebung)

    pruefe(backup.onedrive_basis() == WOLKE, "Schein-OneDrive wird erkannt")
    pruefe(not dateien_in_wolke(), "App-Start legt ohne Schalter nichts in OneDrive ab")
    pruefe(not backup.in_onedrive(), "ohne Schalter: in_onedrive() ist falsch")
    pruefe(backup.backup_ordner().startswith(DATEN), "ohne Schalter: Ziel liegt im Datenordner")

    with app.test_client() as c:
        antwort = c.get("/sicherung/")
        pruefe(antwort.status_code in (200, 302), f"Sicherungsseite lädt ({antwort.status_code})")
        if antwort.status_code == 200:
            pruefe("OneDrive" not in antwort.get_data(as_text=True).split("Speicherort:")[-1][:80],
                   "Sicherungsseite zeigt ohne Schalter „Lokal“ an")

    ziel, wolke = backup.erstelle_backup()
    pruefe(ziel and ziel.startswith(DATEN) and not wolke, "„Jetzt sichern“ ohne Schalter: lokal")
    pruefe(not dateien_in_wolke(), "auch danach liegt nichts in OneDrive")

    # Fremde Sicherungen in OneDrive dürfen von der Aufbewahrungsregel nie angefasst werden
    fremd = os.path.join(WOLKE, "Vereinsverwaltung", "Sicherungen")
    os.makedirs(fremd)
    for i in range(3):
        open(os.path.join(fremd, f"verein-backup_2026-01-0{i + 1}_1200.db"), "w").close()
    for _ in range(14):
        backup._alte_entfernen(backup.backup_ordner())
        backup.erstelle_backup()
    pruefe(len(os.listdir(fremd)) == 3, "lokale Aufbewahrungsregel lässt OneDrive-Sicherungen in Ruhe")

    # Mit Schalter-Datei: bewusst eingeschaltet → OneDrive
    open(backup.ONEDRIVE_SCHALTER, "w").close()
    importlib.reload(backup)
    pruefe(backup.in_onedrive(), "mit Schalter-Datei: in_onedrive() ist wahr")
    ziel, wolke = backup.erstelle_backup()
    pruefe(ziel and ziel.startswith(WOLKE) and wolke, "mit Schalter-Datei: Sicherung landet in OneDrive")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
if FEHLER:
    print(f"✘ {len(FEHLER)} Fehler")
    sys.exit(1)
print("✔ Sicherungsort in Ordnung")
