"""
Wächter gegen offene Weiterleitungen (CodeQL-Triage 22.11.2026).

Alle „zurück“-/„weiter“-Ziele laufen über html_sicher.lokales_ziel() bzw. zurueck():
nur Pfade dieser Seite oder absolute Links auf den eigenen Host. Typische Umgehungen
müssen auf den Standardwert zurückfallen.

Start (aus dem Projektordner):  .venv/bin/python tests/test_weiterleitung_sicher.py
"""
import os
import pathlib
import re
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="vv-weiterleitung-")
os.environ.update(VV_DATA_DIR=TMP, SECRET_KEY="weiterleitungstest")
os.environ.pop("VV_PRODUKTION", None)
WURZEL = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL))

from app import create_app  # noqa: E402
from app.html_sicher import lokales_ziel, zurueck  # noqa: E402

FEHLER = []


def pruefe(bedingung, text):
    print(("✔ " if bedingung else "✘ ") + text)
    if not bedingung:
        FEHLER.append(text)


try:
    app = create_app()
    S = "/standard"
    with app.test_request_context("/", base_url="https://verein.example"):
        for gut in ["/termine/5", "/portal/?tab=2#oben", "https://verein.example/finanzen/"]:
            pruefe(lokales_ziel(gut, S) == gut, f"erlaubt: {gut}")
        for boese in ["//fremd.de", "/\\fremd.de", "\\/fremd.de", "/\t/fremd.de", "/\n/fremd.de",
                      "https:fremd.de", "https://fremd.de/", "javascript:alert(1)", "fremd.de",
                      "https://verein.example.fremd.de/", "https://verein.example@fremd.de/", "", None]:
            pruefe(lokales_ziel(boese, S) == S, f"abgewiesen: {boese!r}")
    with app.test_request_context("/", base_url="https://verein.example",
                                  headers={"Referer": "https://fremd.de/falle"}):
        pruefe(zurueck(S) == S, "fremder Referer → Standard")
    with app.test_request_context("/", base_url="https://verein.example",
                                  headers={"Referer": "https://verein.example/mitglieder/3"}):
        pruefe(zurueck(S) == "https://verein.example/mitglieder/3", "eigener Referer bleibt")

    # Die öffentliche Sprach-Umschaltung darf nicht auf fremde Seiten zurückspringen
    with app.test_client() as c:
        r = c.get("/sprache/de", headers={"Referer": "https://fremd.de/falle"})
        pruefe(r.status_code in (301, 302) and "fremd.de" not in r.headers.get("Location", ""),
               f"/sprache leitet nicht nach fremd.de ({r.headers.get('Location')})")

    # Kein Rücksprung mehr direkt über den Referer im Code
    roh = []
    for p in list((WURZEL / "app" / "routes").glob("*.py")) + [WURZEL / "app" / "__init__.py"]:
        for nr, zeile in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"redirect\(\s*request\.(referrer|form\.get|args\.get)\(?", zeile):
                roh.append(f"{p.name}:{nr}")
    pruefe(not roh, "keine ungeprüften redirect(request.referrer/form/args) mehr" + (f": {roh}" if roh else ""))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
if FEHLER:
    print(f"✘ {len(FEHLER)} Fehler")
    sys.exit(1)
print("✔ Weiterleitungen sicher")
