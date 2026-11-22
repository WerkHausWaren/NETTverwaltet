"""
Wächter: keine unverschlüsselten Datenbank-Kopien mehr in die Nextcloud (Vorfall 17.11.2026).

Hintergrund: Die App lud täglich (Cron werkhaus-backup → app/sicherung_auto.py) und beim
Knopf „Jetzt sichern“ Klartext-Kopien der Vereinsdatenbank (mit IBAN, Geburtsdaten) in die
Nextcloud. Die lag auf einem Webspace, der kompromittiert wurde. Off-site wird seitdem nur
noch age-verschlüsselt über /opt/backup-server.sh gesichert.

Start (aus dem Projektordner):  .venv/bin/python tests/test_keine_klartext_sicherung.py
"""
import os
import pathlib
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-klartext-")
os.environ.update(VV_DATA_DIR=TMP, VV_PRODUKTION="1", SECRET_KEY="klartexttest")
os.environ.pop("VV_KLARTEXT_SICHERUNG_ERLAUBT", None)
WURZEL = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL))

from app import sicherung_auto, webdav  # noqa: E402

FEHLER = []


def pruefe(bedingung, text):
    print(("✔ " if bedingung else "✘ ") + text)
    if not bedingung:
        FEHLER.append(text)


# 1) Die nächtliche Sicherung lädt nichts mehr hoch
aufrufe = []
_put, _ordner = webdav.put, webdav.ordner_sicherstellen
webdav.put = lambda *a, **k: aufrufe.append(a) or True
webdav.ordner_sicherstellen = lambda *a, **k: aufrufe.append(a)
try:
    anzahl, meldungen = sicherung_auto.sichere_alle()
finally:
    webdav.put, webdav.ordner_sicherstellen = _put, _ordner
pruefe(anzahl == 0 and aufrufe == [], "sichere_alle lädt nichts in die Nextcloud")
pruefe(bool(meldungen) and "stillgelegt" in meldungen[0], "sichere_alle meldet „stillgelegt“")

# 2) Der Knopf „Jetzt sichern“ benutzt kein WebDAV mehr
quelle = (WURZEL / "app" / "routes" / "sicherung.py").read_text(encoding="utf-8")
pruefe("webdav" not in quelle, "Sicherungs-Seite lädt nichts per WebDAV hoch")

# 3) Kein aktiver Cron mit sichere_alle im Repo
aktiv = []
for pfad in (WURZEL / "deploy").rglob("*"):
    if pfad.is_file():
        try:
            text = pfad.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for zeile in text.splitlines():
            if "sichere_alle" in zeile and not zeile.lstrip().startswith("#"):
                aktiv.append(str(pfad.relative_to(WURZEL)))
pruefe(not aktiv, f"kein aktiver Klartext-Sicherungs-Cron in deploy/ {aktiv or ''}")

shutil.rmtree(TMP, ignore_errors=True)
print()
print(f"{len(FEHLER)} Fehler" if FEHLER else "Alles grün ✔")
sys.exit(1 if FEHLER else 0)
