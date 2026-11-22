# Erste Schritte nach der Einrichtung

Diese Reihenfolge hat sich bewährt. Ihr müsst nicht alles am ersten Tag erledigen – Schritt 1 bis 4 reichen für den
Anfang.

## 1. Den Verein eintragen

**Einstellungen** (links im Menü, ganz unten):

- Vereinsname, Anschrift, Vereins-E-Mail, Website,
- Registergericht, Registernummer, Steuernummer und Freistellungsbescheid (für Spendenbescheinigungen),
- Logo hochladen – mit **„🎨 Farben aus Logo“** schlägt die App passende Farben vor,
- **Dashboard-Module**: Schaltet nur ein, was ihr braucht. Alles andere bleibt unsichtbar und lässt sich später
  einschalten.

## 2. E-Mail-Versand einrichten (nur Server-Betrieb)

Unter **Einstellungen** die Zugangsdaten eures E-Mail-Postfachs eintragen (SMTP-Server, Anschluss, Benutzer,
Passwort) und mit **Testmail** prüfen. Ohne diesen Schritt kann die App keine Einladungen, Bestätigungen oder
Rundmails verschicken.

## 3. Mitglieder übernehmen

Habt ihr schon eine Mitgliederliste? **Mitglieder → ⬆️ Import** liest **CSV-Dateien** ein:

1. Die erste Zeile der Liste muss die **Spaltennamen** enthalten, genau so geschrieben wie auf der Import-Seite
   angegeben – z. B. `vorname`, `nachname`, `strasse`, `plz`, `ort`, `email`, `geburtsdatum`, `eintrittsdatum`,
   `iban`. Am einfachsten: die Beispielliste aus dem Ordner `beispieldaten` als Vorlage nehmen.
2. **Excel-Liste?** In Excel **Datei → Speichern unter → „CSV UTF-8 (durch Trennzeichen getrennt)“** wählen
   (bei LibreOffice: „Text CSV“, Zeichensatz UTF-8).
3. Datei auf der Import-Seite auswählen und **Importieren** klicken. Datumsangaben am besten als `2015-03-01`.

Danach könnt ihr unter **🏷️ Gruppen** Mitglieder einteilen, etwa in Vorstand, Aktive oder Förderer.

> Tipp: Probiert den Import zuerst mit wenigen Zeilen oder mit der Beispielliste aus `beispieldaten/`.

## 4. Beiträge festlegen

**💶 Beiträge / SEPA**: Beitragssätze und Zahlungsweise anlegen, Gläubiger-ID für Lastschriften eintragen und je
Mitglied das Lastschriftmandat hinterlegen. Lastschriften gehen nur an Mitglieder mit vorhandenem Mandat.

## 5. Weitere Personen einladen (Server-Betrieb)

Unter **🔐 Zugänge** bekommen Vorstandsmitglieder eigene Konten mit passenden Rechten, z. B. Verwaltung,
Kasse oder Öffentlichkeitsarbeit. Jede Person meldet sich mit eigenem Passwort an und kann Zwei-Faktor oder
Face ID einschalten. Teilt Konten nie.

## 6. Die Mitglieder-App anbieten (Server-Betrieb)

Unter **📱 Mitglieder-App** legt ihr fest, was Mitglieder in der App sehen. Mitglieder öffnen einfach eure Adresse
auf dem Handy und tippen auf „Zum Home-Bildschirm“ bzw. „App installieren“ – ganz ohne App-Store.

## 7. Datensicherung prüfen

Bevor echte Daten im System stehen: [Sicherung einrichten](06-sichern-und-aktualisieren.md).

## Wo finde ich was?

Alle Bereiche im Überblick: [Was die Software alles kann](04-funktionen.md).
In der App selbst zeigt **„Was ist neu“** die neuesten Funktionen.
