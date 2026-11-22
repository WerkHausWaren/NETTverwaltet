# Mitmachen

Schön, dass du NETTverwaltet besser machen möchtest! Du musst nicht programmieren können, um zu helfen.

## Ohne Programmieren

- **Fehler melden:** Reiter „Issues“ → „New issue“ → „Fehler melden“. Beschreib, was du gemacht hast, was passiert ist
  und was du erwartet hättest. Ein Bildschirmfoto hilft – aber bitte **ohne echte Mitgliederdaten**.
- **Ideen vorschlagen:** „New issue“ → „Idee vorschlagen“. Erzähl, welches Problem in deinem Verein gelöst werden soll.
- **Anleitungen verbessern:** Wo bist du hängen geblieben? Jeder Hinweis macht die Anleitungen für den nächsten Verein
  leichter.
- **Fragen stellen und beantworten:** im Reiter „Discussions“.

## Mit Programmieren

1. Projekt herunterladen und lokal starten (siehe [docs/01-ausprobieren.md](docs/01-ausprobieren.md)).
2. Einen eigenen Zweig (Branch) anlegen und die Änderung machen.
3. Tests laufen lassen – alle müssen grün sein:
   ```bash
   for t in tests/test_*.py; do python "$t" || echo "FEHLER: $t"; done
   ```
4. Für neue Funktionen bitte einen Test ergänzen (Vorbild: die Dateien in `tests/`).
5. Pull Request stellen und kurz beschreiben, was sich für die Nutzer:innen ändert.

Mehr zum Aufbau: [docs/entwicklung.md](docs/entwicklung.md).

## Ein paar Regeln

- **Sprache:** Oberfläche und Texte auf Deutsch, in einfacher Sprache und gegendert (z. B. „Helfer:innen“,
  „Kassenwart:in“).
- **Datenschutz zuerst:** Neue Funktionen zeigen öffentlich nur, was ausdrücklich öffentlich sein soll.
- **Nie echte Daten** in Tests, Beispielen oder Bildschirmfotos – nur erfundene (Rufnummern für Beispiele:
  030 23125 xxx, IBANs mit Bankleitzahl 10000000).
- Mit deinem Beitrag stimmst du zu, dass er unter der Lizenz des Projekts (AGPL-3.0) steht.
