# Hinweise zu Bausteinen und Bildern anderer

NETTverwaltet selbst steht unter der GNU Affero General Public License 3.0 (siehe [LICENSE](LICENSE)).
Darin stecken außerdem Bausteine und Bilder anderer, die unter ihren eigenen Lizenzen stehen.

## Mitgelieferte Dateien

| Datei | Herkunft | Lizenz |
|---|---|---|
| `app/static/jsqr.min.js` | jsQR 1.4.0 (QR-Code-Erkennung im Browser), https://github.com/cozmo/jsQR | Apache License 2.0 |
| `app/data/blz.properties`, daraus abgeleitet `app/daten/fints_adressen.csv` | Bankverzeichnis des Projekts hbci4java, https://github.com/hbci4j/hbci4java | GNU Lesser General Public License 2.1 |

## Bilder der Feiertagsfolien (`app/static/signage/feiertage/`)

Alle Fotos stammen von Wikimedia Commons und wurden für die Bildschirmfolien verkleinert
(höchstens 600 Pixel); einzelne sind zusätzlich zugeschnitten. Die Folien zeigen den Bildnachweis jeweils unten rechts.

| Datei | Motiv | Urheber:in | Lizenz | Quelle |
|---|---|---|---|---|
| `brinckman.jpg` | John Brinckman | unbekannt (vor 1870) | gemeinfrei | https://commons.wikimedia.org/wiki/File:John_Frédéric_Brinckman_(Brinckmann).jpg |
| `reuter.jpg` | Fritz Reuter | unbekannt (vor 1874) | gemeinfrei | https://commons.wikimedia.org/wiki/File:Fritz_Reuter_Porträt_und_faksimilierte_Unterschrift.jpg |
| `otto-peters.jpg` | Louise Otto-Peters | unbekannt (1879) | gemeinfrei | https://commons.wikimedia.org/wiki/File:Porträt_Louise_Otto-Peters_sozialkritische_Schriftstellerin_und_Demokratin.png |
| `stadthafen.jpg` | Stadthafen Waren (Müritz), 2024 | Romzig | CC0 1.0 | https://commons.wikimedia.org/wiki/File:Waren_(Müritz)_(September_2024)_1.jpg |
| `marienkirche.jpg` | St. Marienkirche Waren, 2010 (auf den Turm zugeschnitten) | Ruchhöft-Plau | gemeinfrei | https://commons.wikimedia.org/wiki/File:Waren_(Müritz)_St._Marienkirche_2010.jpg |
| `waren-nacht.jpg` | Waren (Müritz) bei Nacht (verkleinert) | ErRu | CC BY-SA 3.0, https://creativecommons.org/licenses/by-sa/3.0/ | https://commons.wikimedia.org/wiki/File:Waren_-_Müritz_bei_Nacht.jpg |
| `nelke.svg` | Rote Nelke | eigene Zeichnung des Projekts | wie das Projekt (AGPL-3.0) | – |

Die verkleinerte Fassung von `waren-nacht.jpg` steht – wie das Original – unter CC BY-SA 3.0.

## Zitate auf den Feiertagsfolien

Die Zitate stammen von gemeinfreien Autor:innen (u. a. John Brinckman, Fritz Reuter, Johann Heinrich Voß,
Martin Luther, Louise Otto-Peters). Kurze Bibelverse folgen der Lutherbibel 2017
(© Deutsche Bibelgesellschaft); ein kurzes Zitat von Joachim Gauck stammt aus seiner Rede zum 3. Oktober 2015.

## Schulferien

Die Ferientermine für Mecklenburg-Vorpommern stammen aus den Ferienkalendern der Kultusministerkonferenz,
https://www.kmk.org/service/ferienregelung.html (Stand 09.10.2025).

## Bibliotheken, die bei der Installation geladen werden

Diese Pakete werden nicht mitgeliefert, sondern bei der Installation aus dem Python-Paketverzeichnis geholt
(`requirements.txt`). Ihre Lizenzen zum Zeitpunkt der Veröffentlichung:

| Paket | Lizenz |
|---|---|
| Flask, Flask-WTF, segno, webauthn, pypdf | BSD |
| Flask-Login, gunicorn, pyotp, argon2-cffi | MIT |
| Pillow | MIT-CMU |
| requests | Apache 2.0 |
| cryptography | Apache 2.0 oder BSD |
| pywebpush | Mozilla Public License 2.0 |
| fints (python-fints), fpdf2 | GNU LGPL 3.0 oder später |
