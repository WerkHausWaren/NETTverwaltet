# Ausprobieren – Demo und eigener Rechner

## Weg 1: Die Demo (1 Minute)

1. Öffnet **https://verein.nettverwaltet.de** im Browser.
2. Klickt auf **„Demo ausprobieren“**.
3. Ihr seid jetzt in einem erfundenen Musterverein und könnt alles anklicken.

Alle Namen und Zahlen in der Demo sind erfunden. Jede Nacht wird die Demo auf den Ausgangsstand zurückgesetzt –
ihr könnt also nichts kaputt machen. Tragt dort bitte **keine echten Daten** ein.

## Weg 2: Auf dem eigenen Rechner (ca. 10 Minuten)

So läuft NETTverwaltet auf einem Mac-, Windows- oder Linux-Rechner – ohne Internet-Server, ohne Anmeldung.
Die Daten liegen im Ordner `data` neben dem Programm.

### Schritt 1: Python installieren (einmalig)

NETTverwaltet braucht das kostenlose Programm **Python** (Version 3.9 oder neuer).

- **Windows:** https://www.python.org/downloads/ → „Download Python“ → Installation starten.
  **Wichtig:** Im ersten Fenster unten das Häkchen bei **„Add Python to PATH“** setzen.
- **Mac:** https://www.python.org/downloads/ → „Download Python“ → Installation durchklicken.
- **Linux:** Python ist meist schon da. Sonst über die Software-Verwaltung „python3“ und „python3-venv“ installieren.

### Schritt 2: NETTverwaltet herunterladen

1. Oben auf dieser GitHub-Seite auf den grünen Knopf **„Code“** klicken → **„Download ZIP“**.
2. Die ZIP-Datei entpacken (Doppelklick bzw. Rechtsklick → „Alle extrahieren“).
3. Den entpackten Ordner an einen festen Ort legen, z. B. in „Dokumente“. Dort bleiben später auch eure Daten.

### Schritt 3: Starten

Im Ordner doppelt klicken auf:

- **Mac:** `Vereinsverwaltung starten (Mac).command`
- **Windows:** `Vereinsverwaltung starten (Windows).bat`
- **Linux:** `vereinsverwaltung-starten-linux.sh` (ggf. vorher Rechtsklick → Eigenschaften → „Als Programm ausführen“ erlauben)

Beim **ersten Start** richtet sich das Programm selbst ein. Das dauert ein bis zwei Minuten und braucht kurz Internet.
Danach öffnet sich der Browser mit dem Dashboard von „Mein Verein e.V.“.

> **Mac zeigt eine Warnung** („kann nicht geöffnet werden“)? Einmal **Rechtsklick auf die Datei → Öffnen → Öffnen**.
> Danach reicht der normale Doppelklick.

Ein kleines Fenster mit Text bleibt offen – das ist das laufende Programm. **Schließt ihr es, ist NETTverwaltet aus**;
die Daten sind gespeichert. Die Adresse im Browser lautet `http://127.0.0.1:5001` – gern als Lesezeichen speichern.

### Schritt 4: Euren Verein eintragen

Links im Menü **Einstellungen** öffnen und Vereinsname, Anschrift, Logo und Farben eintragen.
Weiter geht es mit den [ersten Schritten](03-erste-schritte.md).

### Mit Beispieldaten üben

Im Ordner `beispieldaten` liegt eine Liste mit 18 erfundenen Mitgliedern. Unter **Mitglieder → ⬆️ Import** könnt
ihr sie einlesen und alles gefahrlos ausprobieren. Danach einfach wieder löschen.

## Wie geht es weiter?

- Passt die Software? Dann [richtet sie auf einem Server ein](02-online-betreiben.md), damit mehrere Leute
  gemeinsam arbeiten und Mitglieder die App nutzen können.
- Oder bleibt auf dem eigenen Rechner und denkt an die [Datensicherung](06-sichern-und-aktualisieren.md).
