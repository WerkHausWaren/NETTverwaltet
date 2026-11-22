# Sichern und aktualisieren

## Warum sichern?

Ein Festplattenschaden, ein versehentliches Löschen oder ein Problem beim Server-Anbieter – ohne Sicherung wären
die Daten eures Vereins weg. **Sichert regelmäßig und bewahrt mindestens eine Kopie an einem anderen Ort auf.**

## Auf dem eigenen Rechner

- Die App legt beim Start automatisch eine Sicherung an, wenn die letzte älter als eine Woche ist. Die letzten
  12 Sicherungen liegen im Ordner `data/backups` (auf dem Mac in OneDrive, falls vorhanden).
- Unter **Einstellungen → 💾 Datensicherung** könnt ihr jederzeit **jetzt sichern** und eine Sicherung
  **wiederherstellen**.
- Kopiert zusätzlich ab und zu den ganzen Ordner `data` auf einen USB-Stick oder in euren Cloud-Speicher.

## Auf dem Server

Alle Daten liegen im Ordner `/opt/NETTverwaltet/data`. Dazu kommt die Datei `.env` mit den Geheimschlüsseln –
**ohne sie lassen sich gesicherte Zugangsdaten nicht wieder öffnen.**

Eine tägliche Sicherung um 3 Uhr nachts richtet ihr so ein (auf dem Server eintippen):

```bash
mkdir -p /root/sicherungen
```

```bash
echo '0 3 * * * root tar -czf /root/sicherungen/verein-$(date +\%F).tar.gz -C /opt/NETTverwaltet data .env && find /root/sicherungen -name "verein-*.tar.gz" -mtime +30 -delete' > /etc/cron.d/nettverwaltet-sicherung
```

Das legt jede Nacht eine gepackte Kopie an und löscht Kopien, die älter als 30 Tage sind.

**Wichtig:** Diese Sicherungen liegen noch auf demselben Server. Holt sie regelmäßig auf einen anderen Rechner
oder nutzt den Sicherungsdienst eures Server-Anbieters (oft „Backup“ oder „Snapshots“ für wenige Euro im Monat).
Eine Kopie auf euren Rechner holt ihr so (auf eurem Rechner eintippen):

```bash
scp -r root@DEINE-SERVER-IP:/root/sicherungen ./sicherungen-verein
```

### Wiederherstellen auf dem Server

```bash
cd /opt/NETTverwaltet
```

```bash
docker compose down
```

```bash
tar -xzf /root/sicherungen/verein-DATUM.tar.gz -C /opt/NETTverwaltet
```

```bash
docker compose up -d
```

(`DATUM` durch das Datum der gewünschten Sicherung ersetzen, z. B. `2026-12-03`.)

## Aktualisieren

Neue Versionen stehen auf GitHub unter **„Releases“**; was sich ändert, steht in `CHANGELOG.md`.
**Vor jedem Update eine Sicherung anlegen.**

### Server

```bash
cd /opt/NETTverwaltet
```

```bash
git pull
```

```bash
docker compose up -d --build
```

Eure Daten, die `.env` und das `Caddyfile` bleiben dabei erhalten.

### Eigener Rechner

Neue Fassung als ZIP herunterladen, entpacken und den Ordner `data` aus der alten Fassung in die neue kopieren.
Danach wie gewohnt per Doppelklick starten – die App passt ihre Datenbank beim Start selbst an.
