# Aufbau der Software und Mitmachen

Für alle, die in den Code schauen oder mitentwickeln möchten.

## Technik in Kürze

- **Python 3** mit **Flask**; Oberfläche als HTML-Vorlagen (Jinja) mit wenig eigenem JavaScript, ohne Build-Schritt.
- **SQLite**: eine Datenbank `system.db` (Konten, Geräte, Vereinsliste) und je Verein eine eigene `verein_<id>.db`.
  Die Datenbanken passen sich beim Start selbst an (fehlende Tabellen und Spalten werden ergänzt).
- **Zwei Betriebsarten** über die Umgebungsvariable `VV_PRODUKTION`:
  ohne = Einzelplatz auf `127.0.0.1:5001` ohne Anmeldung (`run.py`), mit `1` = Server-Betrieb mit Anmeldung,
  sicheren Cookies und HTTPS hinter Caddy (`wsgi.py`, gunicorn, `Dockerfile`, `docker-compose.yml`).
- **Mitglieder-App**: dieselbe Anwendung unter `/portal/`, als Progressive Web App installierbar (Service Worker,
  Web-Push).
- Wichtige Umgebungsvariablen (siehe `app/config.py`): `SECRET_KEY`, `VV_KRYPTO_KEY` (verschlüsselt gespeicherte
  Zugangsdaten), `VV_BASIS_URL`, `VV_DATA_DIR`, optional `NEXTCLOUD_URL`.

## Ordner

| Ordner/Datei | Inhalt |
|---|---|
| `app/` | die Anwendung: `routes/` (Seiten je Bereich), `templates/`, `static/`, Fachlogik in `app/*.py` |
| `app/db.py` | Datenbankzugriff und automatische Anpassung des Schemas |
| `app/neuigkeiten.py` | Einträge für „Was ist neu“ in der App |
| `tests/` | eigenständige Testskripte, je Bereich eines |
| `beispieldaten/` | erfundene Mitgliederliste zum Üben |
| `einrichten.sh`, `docker-compose.yml`, `Caddyfile.vorlage` | Server-Betrieb |

## Lokal entwickeln

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python run.py
```

## Tests

Jede Datei in `tests/` ist ein eigenständiges Skript mit eigener Wegwerf-Datenbank; es geht nichts nach draußen.

```bash
for t in tests/test_*.py; do .venv/bin/python "$t" > /dev/null || echo "FEHLER: $t"; done
```

GitHub führt alle Tests bei jeder Änderung automatisch aus (`.github/workflows/tests.yml`).

## Grundsätze

- **Öffentlich nur, was öffentlich sein soll**: Termine „nur für Mitglieder“ oder „nicht-öffentlich“ werden an jeder
  öffentlichen Stelle zentral maskiert (`app/termin_maske.py`).
- **Module** sind abschaltbar (`modul_*` in den Vereinseinstellungen); eine neue Funktion prüft ihren Schalter an jeder
  Stelle (Seiten, Formulare, zeitgesteuerte Aufgaben).
- **Neue Funktion = neuer Eintrag in `app/neuigkeiten.py`** und ein Test.
- Oberfläche in einfachem, gegendertem Deutsch.

Mehr zum Mitmachen: [CONTRIBUTING.md](../CONTRIBUTING.md).
