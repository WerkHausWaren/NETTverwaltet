# Hilfe bei Problemen

## Auf dem eigenen Rechner

| Problem | Lösung |
|---|---|
| „Python wurde nicht gefunden“ | Python installieren ([Anleitung](01-ausprobieren.md)). Unter Windows beim Installieren „Add Python to PATH“ anhaken und danach den Rechner neu starten. |
| Mac: „kann nicht geöffnet werden“ / „Apple konnte nicht überprüfen …“ | Warnung mit „Fertig“ schließen → Systemeinstellungen → Datenschutz & Sicherheit → ganz unten „Dennoch öffnen“. Bei älteren Macs: Rechtsklick auf die Startdatei → Öffnen → Öffnen. |
| Windows: „Der Computer wurde durch Windows geschützt“ | „Weitere Informationen“ → „Trotzdem ausführen“. |
| Mac fragt nach „Befehlszeilen-Entwicklerwerkzeugen“ | Python fehlt noch. Entweder „Installieren“ wählen (dauert einige Minuten) oder Python wie in der [Anleitung](01-ausprobieren.md) installieren. Danach die Startdatei erneut doppelklicken. |
| Der Browser zeigt „Seite nicht erreichbar“ | Läuft das kleine Programmfenster noch? Sonst neu starten. Adresse: `http://127.0.0.1:5001` |
| Beim ersten Start bleibt es lange stehen | Beim ersten Mal werden Bausteine aus dem Internet geladen. Internetverbindung prüfen und ein paar Minuten warten. |
| Das Programm startet, aber ein zweites Mal nicht | Vermutlich läuft es schon. Alle Programmfenster schließen und erneut starten. |

## Auf dem Server

Diese Befehle helfen bei der Fehlersuche (auf dem Server im Ordner `/opt/NETTverwaltet` eintippen):

| Frage | Befehl |
|---|---|
| Läuft alles? | `docker compose ps` |
| Was ist zuletzt passiert? | `docker compose logs --tail 100` |
| Neu starten | `docker compose restart` |

| Problem | Lösung |
|---|---|
| Die Adresse ist im Browser nicht erreichbar | Zeigt die Adresse schon auf den Server? `ping eure-adresse.de` muss die Server-IP zeigen. Nach einer Änderung kann es bis zu einer Stunde dauern. |
| Warnung „unsichere Verbindung“ | Das Zertifikat wird beim ersten Start geholt (1–2 Minuten). Dauert es länger: Prüfen, ob beim Server-Anbieter eine Firewall die Anschlüsse 80 und 443 sperrt. |
| Es kommen keine E-Mails an | Einstellungen → E-Mail-Versand prüfen und die Testmail schicken. Manche Anbieter verlangen ein eigenes App-Passwort. Landen Mails im Spam, fehlen oft Absender-Einträge bei eurer Domain (SPF, DKIM) – euer E-Mail-Anbieter hilft weiter. |
| Passwort vergessen | Auf der Anmeldeseite „Passwort vergessen“ nutzen (dafür muss der E-Mail-Versand eingerichtet sein). Kommt niemand mehr hinein – kein E-Mail-Versand, Handy mit der Zwei-Faktor-App verloren –, siehe unten „Niemand kommt mehr hinein“. |

## Niemand kommt mehr hinein

Wer Zugriff auf den Server hat, kann jedes Verwaltungskonto wieder freischalten. Auf dem Server eintippen:

```bash
cd /opt/NETTverwaltet
```

```bash
docker compose exec app python konto_zuruecksetzen.py
```

Das zeigt alle Konten. Dann für das gewünschte Konto (E-Mail anpassen):

```bash
docker compose exec app python konto_zuruecksetzen.py vorstand@euer-verein.de
```

Das Programm fragt nach einem neuen Passwort und – falls eingeschaltet – ob die Zwei-Faktor-Anmeldung
abgeschaltet werden soll. Danach könnt ihr euch sofort anmelden. Schaltet die Zwei-Faktor-Anmeldung
anschließend gleich wieder ein. Gibt es die E-Mail-Adresse noch nicht, bietet das Programm an, ein neues
Verwaltungskonto anzulegen.

## Weiterhin ratlos?

Stellt eure Frage im Reiter **„Discussions“** hier auf GitHub. Beschreibt, was ihr getan habt und was passiert ist –
und **entfernt vorher alle Personendaten** aus Bildschirmfotos und Fehlermeldungen.
