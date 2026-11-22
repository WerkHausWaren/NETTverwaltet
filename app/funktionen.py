"""Funktions-Suche der Verwaltung: kuratierter Index aller Verwaltungsfunktionen.

Jeder Eintrag: t = Titel (mit Emoji), p = Pfad, b = Bereich,
s = zusätzliche Stichwörter (kleingeschrieben), m = Modul-Schalter aus den
Einstellungen (Eintrag erscheint nur, wenn das Modul an ist; None = immer).

Die Suche selbst läuft im Browser (base.html): Tippen filtert über Titel,
Bereich und Stichwörter. Der Test tests/test_funktionssuche.py prüft, dass
jeder Pfad wirklich auf eine Route zeigt – tote Einträge fliegen dort auf.
"""

FUNKTIONEN = [
    # Start
    {"t": "🏠 Dashboard", "p": "/", "b": "Start", "s": "start übersicht handlungsbedarf todo", "m": None},
    {"t": "✅ Aufgaben", "p": "/aufgaben/", "b": "Start", "s": "todo erledigen zuständig wiederkehrend", "m": None},
    {"t": "📱 Mitglieder-App öffnen", "p": "/portal/", "b": "Start", "s": "portal app ansicht mitgliederansicht", "m": None},

    # Mitglieder
    {"t": "👥 Mitgliederliste", "p": "/mitglieder/", "b": "Mitglieder", "s": "personen suchen filtern status", "m": None},
    {"t": "➕ Mitglied anlegen", "p": "/mitglieder/neu", "b": "Mitglieder", "s": "neu aufnehmen person hinzufügen", "m": None},
    {"t": "📥 Mitglieder importieren", "p": "/mitglieder/import", "b": "Mitglieder", "s": "csv excel einlesen übernehmen", "m": None},
    {"t": "📤 Export als CSV", "p": "/mitglieder/export.csv", "b": "Mitglieder", "s": "herunterladen tabelle", "m": None},
    {"t": "📤 Export als Excel", "p": "/mitglieder/export.xlsx", "b": "Mitglieder", "s": "herunterladen xlsx tabelle", "m": None},
    {"t": "🧩 Eigene Felder", "p": "/mitglieder/felder", "b": "Mitglieder", "s": "zusatzfelder benutzerdefiniert", "m": None},
    {"t": "🤝 Mitgliedschaft & Anträge", "p": "/mitglieder/antraege", "b": "Mitglieder", "s": "beitritt aufnahme austritt beitrittsformular", "m": None},
    {"t": "🔄 Nextcloud-Abgleich", "p": "/mitglieder/nextcloud-abgleich", "b": "Mitglieder", "s": "kontakte carddav synchronisieren", "m": None},
    {"t": "🏷️ Gruppen", "p": "/gruppen/", "b": "Mitglieder", "s": "vorstand zählung rollen", "m": None},
    {"t": "📇 Kontakte", "p": "/kontakte/", "b": "Mitglieder", "s": "adressbuch externe partner vcard", "m": None},
    {"t": "🔐 Zugänge", "p": "/zugaenge/", "b": "Mitglieder", "s": "konten benutzer login passwort einladen", "m": None},
    {"t": "🔑 Schlüsselbuch", "p": "/schluessel/", "b": "Mitglieder", "s": "zugangscode ausgabe rückgabe", "m": "modul_schluessel"},

    # Kommunikation
    {"t": "✉️ E-Mail schreiben", "p": "/kommunikation/", "b": "Kommunikation", "s": "rundmail serienmail versand newsletter", "m": None},
    {"t": "🕘 Mail-Verlauf", "p": "/kommunikation/verlauf", "b": "Kommunikation", "s": "gesendet historie", "m": None},
    {"t": "📝 Vorlagen", "p": "/kommunikation/vorlagen", "b": "Kommunikation", "s": "textbausteine muster", "m": None},
    {"t": "📣 Ankündigungen", "p": "/kommunikation/ankuendigungen", "b": "Kommunikation", "s": "news aktuelles app-meldung", "m": None},
    {"t": "📌 Schwarzes Brett", "p": "/kommunikation/schwarzes-brett", "b": "Kommunikation", "s": "gesuch angebot matchen verbinden pinnwand freigeben", "m": None},
    {"t": "🖨️ Listen & Serienbriefe", "p": "/dokumente/", "b": "Kommunikation", "s": "pdf drucken brief", "m": None},
    {"t": "📄 Mitgliederliste (Druck)", "p": "/dokumente/mitgliederliste", "b": "Kommunikation", "s": "pdf ausdrucken", "m": None},
    {"t": "🏷️ Adress-Etiketten", "p": "/dokumente/etiketten", "b": "Kommunikation", "s": "aufkleber post versand", "m": None},
    {"t": "🪪 Mitgliedsausweise", "p": "/dokumente/ausweise", "b": "Kommunikation", "s": "karte ausweis drucken", "m": None},

    # Finanzen
    {"t": "📊 Kassenbuch", "p": "/finanzen/", "b": "Finanzen", "s": "buchungen einnahmen ausgaben beleg", "m": None},
    {"t": "🧾 Steuer & Jahresbericht", "p": "/finanzen/steuer", "b": "Finanzen", "s": "finanzamt gemeinnützigkeit körperschaftsteuer jahresabschluss", "m": None},
    {"t": "📑 Kassenbericht", "p": "/finanzen/kassenbericht", "b": "Finanzen", "s": "kassenprüfung bericht jahresbericht", "m": None},
    {"t": "💶 Beiträge & SEPA-Lauf", "p": "/beitraege/", "b": "Finanzen", "s": "lastschrift einzug mitgliedsbeitrag mahnung sepa.xml", "m": None},
    {"t": "💳 Konten & Bankabruf", "p": "/bank/", "b": "Finanzen", "s": "fints hbci umsätze iban zuordnen", "m": None},
    {"t": "🧾 Rechnungen", "p": "/rechnungen/", "b": "Finanzen", "s": "forderungen offene posten", "m": "modul_rechnungen"},
    {"t": "💝 Spenden", "p": "/finanzen/spenden/", "b": "Finanzen", "s": "zuwendung bescheinigung spendenquittung zuwendungsbestätigung spendenbescheinigung", "m": "modul_spenden"},
    {"t": "💝 Sammelbestätigungen", "p": "/finanzen/spenden/sammelbestaetigung", "b": "Finanzen", "s": "jahresbescheinigung spendenbescheinigung sammelspendenbescheinigung sammelbescheinigung zuwendungsbestätigung", "m": "modul_spenden"},
    {"t": "🎯 Fördermittel", "p": "/foerderung/", "b": "Finanzen", "s": "anträge zuschuss projekt verwendungsnachweis", "m": None},
    {"t": "🗂️ Fördertöpfe", "p": "/foerderung/toepfe", "b": "Finanzen", "s": "fördergeber recherche stiftung", "m": None},
    {"t": "📈 Auswertungen", "p": "/auswertungen/", "b": "Finanzen", "s": "statistik entwicklung diagramm mitgliederzahlen", "m": None},
    {"t": "🖨️ Vorlagen für die Kasse", "p": "/finanzen/vorlagen", "b": "Finanzen", "s": "einzahlungsbeleg zählprotokoll auslagenerstattung quittung formular druckvorlage ausfüllbar", "m": None},

    # Öffentlichkeit
    {"t": "📣 Öffentlichkeitsarbeit", "p": "/oea/", "b": "Öffentlichkeit", "s": "presse social media", "m": None},
    {"t": "📋 Redaktionsplan", "p": "/oea/plan", "b": "Öffentlichkeit", "s": "beiträge planen posten", "m": None},
    {"t": "🪄 Beitrags-Werkstatt", "p": "/oea/werkstatt", "b": "Öffentlichkeit", "s": "texte entwerfen schreiben", "m": None},
    {"t": "📇 Presseverteiler", "p": "/oea/verteiler", "b": "Öffentlichkeit", "s": "redaktionen journalisten", "m": None},
    {"t": "📸 Fotoarchiv", "p": "/oea/fotos", "b": "Öffentlichkeit", "s": "bilder medien", "m": None},
    {"t": "📺 Signage", "p": "/signage/verwaltung", "b": "Öffentlichkeit", "s": "monitor bildschirm fernseher ipad tablet türschild raum schaufenster foyer digital signage werbung anzeige", "m": "modul_signage"},
    {"t": "💬 Kommentare", "p": "/oea/kommentare", "b": "Öffentlichkeit", "s": "instagram antworten", "m": None},
    {"t": "🔗 Instagram-Verbindung", "p": "/oea/instagram", "b": "Öffentlichkeit", "s": "verknüpfen konto", "m": None},

    # Termine
    {"t": "📆 Kalender", "p": "/kalender/", "b": "Termine", "s": "monatsansicht übersicht ics abo", "m": None},
    {"t": "🗓️ Terminliste", "p": "/termine/", "b": "Termine", "s": "veranstaltungen ereignisse", "m": None},
    {"t": "🎟️ Anmeldungen", "p": "/veranstaltungen/", "b": "Termine", "s": "tickets teilnehmer anmeldeformular", "m": "modul_veranstaltungen"},
    {"t": "🧑‍🤝‍🧑 Schichten", "p": "/schichten/", "b": "Termine", "s": "dienste helfer einteilen", "m": "modul_schichten"},
    {"t": "🗺️ Verteilaktionen", "p": "/verteilen/", "b": "Termine", "s": "flyer verteilen programmheft einladung postkarte briefkasten straßen route plakate plakatieren aufhängen abhängen zustellung zustellen briefkasten kontakte laufzettel", "m": "modul_verteilen"},
    {"t": "🏘️ Straßen für Verteilaktionen", "p": "/verteilen/strassen", "b": "Termine", "s": "straßen import haushalte csv verteilen", "m": "modul_verteilen"},

    # Vereinsführung
    {"t": "🏛️ Versammlungen", "p": "/versammlungen/", "b": "Vereinsführung", "s": "mitgliederversammlung protokoll einladung tagesordnung", "m": None},
    {"t": "⏰ Fristen-Radar", "p": "/fristen/", "b": "Vereinsführung", "s": "kündigungsfrist verträge ablauf erinnerung", "m": None},
    {"t": "📜 Vereinsunterlagen", "p": "/unterlagen/", "b": "Vereinsführung", "s": "satzung ordnungen protokolle register", "m": None},
    {"t": "🗄️ Dateiablage", "p": "/dms/", "b": "Vereinsführung", "s": "dokumente dms archiv ablage", "m": "modul_dms"},

    # Einstellungen
    {"t": "⚙️ Einstellungen", "p": "/einstellungen/", "b": "Einstellungen", "s": "stammdaten smtp module farben logo mehrsprachigkeit bankverbindung", "m": None},
    {"t": "📱 Mitglieder-App einrichten", "p": "/einstellungen/mitglieder-app", "b": "Einstellungen", "s": "portal kacheln startseite freigaben", "m": None},
    {"t": "🎨 Mein Aussehen", "p": "/einstellungen/aussehen", "b": "Einstellungen", "s": "farben dunkelmodus schrift design", "m": None},
    {"t": "✉️ Mein Absender", "p": "/einstellungen/absender", "b": "Einstellungen", "s": "persönliche mailadresse smtp", "m": None},
    {"t": "💳 Mein Bankzugang", "p": "/einstellungen/bankzugang", "b": "Einstellungen", "s": "eigener bankzugang fints pin online-banking anmeldung tan", "m": None},
    {"t": "💾 Datensicherung", "p": "/sicherung/", "b": "Einstellungen", "s": "backup wiederherstellen export", "m": None},
    {"t": "🔒 Impressum & Datenschutz", "p": "/einstellungen/rechtliches", "b": "Einstellungen", "s": "rechtliches dsgvo", "m": None},
]


def fuer_verein(einst):
    """Index gefiltert nach den Modul-Schaltern des Vereins."""
    sichtbar = []
    for f in FUNKTIONEN:
        if f["m"]:
            try:
                if not (einst and einst[f["m"]]):
                    continue
            except (KeyError, IndexError, TypeError):
                continue
        sichtbar.append({"t": f["t"], "p": f["p"], "b": f["b"], "s": f["s"]})
    return sichtbar


def wer():
    """Anzeigename der angemeldeten Person – für „erstellt von"-Spalten.

    In der Lokalen Verwaltung (ohne Anmeldung) ist das „Verwaltung"."""
    try:
        from flask_login import current_user
        return (getattr(current_user, "anzeigename", "")
                or getattr(current_user, "email", "") or "Verwaltung")
    except Exception:  # noqa: BLE001 – außerhalb eines Requests (Cron-Läufe)
        return "Verwaltung"
