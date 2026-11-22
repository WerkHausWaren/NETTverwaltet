"""
Test: Standard-Vorlagen landen beim Säen in der richtigen Einordnung.

Hintergrund: In app/vorlagen_texte.py stimmten zwei Schlüssel in
STANDARD_ZUORDNUNG nicht mit den Vorlagen-Namen aus alle() überein
(„Bestätigung deiner Aufnahme" statt „Bestätigung der Aufnahme",
„Bestätigung deiner Kündigung" statt „Kündigungsbestätigung (Austritt)").
Folge: Der Seed (db._alle_spalten_migrationen) fand über .get keine
Einordnung und säte beide mit art=''/kategorie='' – sie lagen bei allen
Vereinen unter „Ohne Einordnung".

Dieser Test verhindert das dauerhaft:
1) Die Schlüssel von STANDARD_ZUORDNUNG sind exakt die Namen aus alle().
2) Ein frisch gesäter Verein hat KEINE Standard-Vorlage ohne Einordnung.
3) Bestandszeilen mit art='' repariert der vorhandene Backfill beim
   nächsten init_db() von selbst (läuft bei jedem App-Start).

Start (aus dem Projektordner):  python3 tests/test_vorlagen_zuordnung.py
Läuft ausschließlich auf einer Wegwerf-Datenbank in einem temporären Ordner;
die echten Daten unter data/ werden nicht angefasst.
"""
import os
import shutil
import sys
import tempfile

# Wegwerf-Datenverzeichnis MUSS vor dem Import der App stehen (DATA_DIR wird
# beim Import ausgewertet). Ohne VV_PRODUKTION ist der Zugriffsschutz aus.
TMP = tempfile.mkdtemp(prefix="vv-test-vorlagen-")
os.environ["VV_DATA_DIR"] = TMP
os.environ["SECRET_KEY"] = "test-nur-fuer-diesen-lauf"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app import db  # noqa: E402
from app import vorlagen_texte  # noqa: E402

flask_app = create_app()

ok, fehler = 0, []


def pruefe(bedingung, text):
    global ok
    if bedingung:
        ok += 1
        print(f"  ✅ {text}")
    else:
        fehler.append(text)
        print(f"  ❌ {text}")


# --------------------------------------------------------------------------
print("\n1) STANDARD_ZUORDNUNG und alle() nennen dieselben Vorlagen")
namen_alle = {name for name, _betreff, _inhalt in vorlagen_texte.alle(1, False, "")}
namen_zuordnung = set(vorlagen_texte.STANDARD_ZUORDNUNG)

nur_zuordnung = sorted(namen_zuordnung - namen_alle)
nur_alle = sorted(namen_alle - namen_zuordnung)
pruefe(not nur_zuordnung,
       f"kein Schlüssel ohne passende Vorlage in alle() {nur_zuordnung or ''}")
pruefe(not nur_alle,
       f"keine Vorlage aus alle() ohne Einordnung {nur_alle or ''}")

for name, (art, kat) in vorlagen_texte.STANDARD_ZUORDNUNG.items():
    pruefe(art in ("intern", "extern") and bool(kat),
           f"„{name}“ hat gültige Einordnung ({art}/{kat})")

# --------------------------------------------------------------------------
print("\n2) Frisch gesäter Verein: keine Standard-Vorlage ohne Einordnung")
with flask_app.app_context():
    vid = db.alle_vereine()[0]["id"]
    conn = db.get_db_fuer(vid)
    zeilen = conn.execute(
        "SELECT name, COALESCE(art,'') art, COALESCE(kategorie,'') kategorie "
        "FROM vorlagen WHERE verein_id=?", (vid,)).fetchall()
    conn.close()

standard = {z["name"]: z for z in zeilen if z["name"] in namen_alle}
pruefe(len(standard) == len(namen_alle),
       f"alle {len(namen_alle)} Standard-Vorlagen wurden gesät ({len(standard)})")
ohne = sorted(n for n, z in standard.items() if not z["art"] or not z["kategorie"])
pruefe(not ohne, f"keine davon liegt unter „Ohne Einordnung“ {ohne or ''}")

# --------------------------------------------------------------------------
print("\n3) Backfill: leere Einordnung wird beim nächsten Start repariert")
BETROFFEN = ["📩 Bestätigung der Aufnahme", "👋 Kündigungsbestätigung (Austritt)"]
with flask_app.app_context():
    conn = db.get_db_fuer(vid)
    for name in BETROFFEN:  # Bestand nachstellen: so lagen die Zeilen live da
        conn.execute("UPDATE vorlagen SET art='', kategorie='' "
                     "WHERE verein_id=? AND name=?", (vid, name))
    conn.commit()
    leer = conn.execute(
        "SELECT COUNT(*) n FROM vorlagen WHERE verein_id=? AND art=''",
        (vid,)).fetchone()["n"]
    conn.close()
pruefe(leer == len(BETROFFEN), f"Ausgangslage nachgestellt: {leer} Zeilen mit art=''")

with flask_app.app_context():
    db.init_db()  # läuft bei jedem App-Start – enthält den Backfill
    conn = db.get_db_fuer(vid)
    zeilen = conn.execute(
        "SELECT name, COALESCE(art,'') art, COALESCE(kategorie,'') kategorie "
        "FROM vorlagen WHERE verein_id=?", (vid,)).fetchall()
    conn.close()

standard = {z["name"]: z for z in zeilen}
for name in BETROFFEN:
    soll_art, soll_kat = vorlagen_texte.STANDARD_ZUORDNUNG[name]
    z = standard.get(name)
    pruefe(z is not None and z["art"] == soll_art and z["kategorie"] == soll_kat,
           f"„{name}“ wieder eingeordnet als {soll_art}/{soll_kat}")

anzahl = len([z for z in zeilen if z["name"] in namen_alle])
pruefe(anzahl == len(namen_alle),
       f"Backfill hat keine Dubletten gesät ({anzahl} Standard-Vorlagen)")

shutil.rmtree(TMP, ignore_errors=True)

# --------------------------------------------------------------------------
print(f"\nErgebnis: {ok}/{ok + len(fehler)} Prüfungen bestanden")
for f in fehler:
    print(f"  FEHLER: {f}")
sys.exit(1 if fehler else 0)
