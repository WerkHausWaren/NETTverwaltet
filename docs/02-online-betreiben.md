# Online betreiben – auf einem eigenen Server

Mit dieser Anleitung läuft NETTverwaltet unter einer eigenen Adresse wie `verwaltung.euer-verein.de`:
mit Anmeldung, verschlüsselter Verbindung (HTTPS), Mitglieder-App und Online-Formularen.

**Zeitbedarf:** 30–60 Minuten beim ersten Mal. **Vorkenntnisse:** keine – ihr tippt die Befehle genau so ab, wie sie
hier stehen. Unbekannte Wörter erklärt das [Technik-Wörterbuch](08-glossar.md).

## Was ihr braucht

1. **Einen kleinen Mietserver** (VPS) mit **Ubuntu 22.04 oder neuer** und mindestens **2 GB Arbeitsspeicher**,
   z. B. bei Hetzner, IONOS oder netcup – ab etwa 4–5 € im Monat. Wählt bei der Bestellung Ubuntu und richtet den
   Zugang für „root“ ein (Passwort oder SSH-Schlüssel – der Anbieter erklärt das).
2. **Eine Internetadresse** (Domain oder Subdomain), z. B. `verwaltung.euer-verein.de`. Habt ihr schon eine Website,
   könnt ihr dort eine Subdomain anlegen.
3. **Einen Rechner mit Terminal:** Mac („Terminal“), Windows („PowerShell“) oder Linux.

## Schritt 1: Die Adresse auf den Server zeigen lassen

Meldet euch beim Anbieter eurer Domain an und legt in der DNS-Verwaltung einen **A-Eintrag** an:

| Typ | Name | Wert |
|---|---|---|
| A | `verwaltung` (der Teil vor eurer Domain) | die IP-Adresse eures Servers (steht in der Übersicht des Server-Anbieters) |

Das kann bis zu einer Stunde dauern. Ob es geklappt hat, seht ihr im Terminal mit
`ping verwaltung.euer-verein.de` – dort muss die IP-Adresse eures Servers erscheinen (beenden mit Strg + C).

## Schritt 2: Am Server anmelden

Im Terminal eures Rechners (statt `DEINE-SERVER-IP` die echte IP-Adresse):

```bash
ssh root@DEINE-SERVER-IP
```

Beim ersten Mal kommt eine Rückfrage, ob ihr dem Server vertraut: `yes` eintippen und Enter drücken.
Alle weiteren Befehle tippt ihr jetzt **auf dem Server** ein.

## Schritt 3: Docker und Git installieren (einmalig)

```bash
curl -fsSL https://get.docker.com | sh
```

```bash
apt-get install -y git
```

## Schritt 4: NETTverwaltet herunterladen

```bash
cd /opt
```

```bash
git clone https://github.com/WerkHausWaren/NETTverwaltet.git
```

```bash
cd NETTverwaltet
```

## Schritt 5: Einrichten

```bash
./einrichten.sh
```

Das Skript fragt nach eurer Adresse (z. B. `verwaltung.euer-verein.de`). Es legt dann die Datei `.env` mit
zufällig erzeugten Geheimschlüsseln an. **Diese Datei nie weitergeben** und gut sichern – ohne sie lassen sich
gespeicherte Zugangsdaten (z. B. für die Bank) nicht mehr entschlüsseln.

## Schritt 6: Starten

```bash
docker compose up -d --build
```

Beim ersten Mal dauert das einige Minuten. Danach öffnet ihr im Browser `https://verwaltung.euer-verein.de`.
Das Sicherheitszertifikat für HTTPS holt sich der Server automatisch und kostenlos (Let's Encrypt); das kann nach
dem ersten Start ein bis zwei Minuten dauern.

## Schritt 7: Erst-Einrichtung im Browser

Die Seite fragt nach **Vereinsname, Ort, Vereins-E-Mail** und legt euer **erstes Verwaltungskonto** an
(E-Mail und Passwort mit mindestens 8 Zeichen). Danach empfiehlt die App, die Zwei-Faktor-Anmeldung einzuschalten –
macht das bitte.

Anschließend unter **Einstellungen**:

- **E-Mail-Versand (SMTP)** eintragen – sonst kann die App keine E-Mails schicken. Die Zugangsdaten bekommt ihr bei
  eurem E-Mail-Anbieter.
- Logo, Farben, Anschrift, Impressum und Datenschutzerklärung,
- Bankverbindung und Beiträge.

Weiter mit den [ersten Schritten](03-erste-schritte.md).

## Ganz wichtig: Sicherung einrichten

Alle Daten liegen auf dem Server im Ordner `/opt/NETTverwaltet/data`. Richtet **sofort** eine Sicherung ein –
wie, steht in [Sichern und aktualisieren](06-sichern-und-aktualisieren.md).

## Wenn etwas nicht klappt

Siehe [Hilfe bei Problemen](07-hilfe.md). Die häufigsten Ursachen: Die Adresse zeigt noch nicht auf den Server
(Schritt 1 braucht Zeit), oder beim Anbieter ist eine Firewall aktiv, die die Anschlüsse 80 und 443 sperrt.
