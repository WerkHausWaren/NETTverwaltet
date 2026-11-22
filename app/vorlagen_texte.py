"""Eingebaute Standard-Mailvorlagen für die Kommunikation (alle Vereine).

Werden einmalig je Verein in die Tabelle `vorlagen` gesät (db.init_db,
Versions-Flag vereine.vorlagen_seed) und sind danach normale, frei
bearbeitbare Vorlagen. Platzhalter wie {vorname}/{vereinsname} ersetzt der
Versand je Mitglied; eckige [Klammern] sind bewusst zum Ausfüllen gedacht.

Das Logo wird mit RELATIVER Adresse eingebettet (im Editor sofort sichtbar);
der Versand macht `src="/…"` automatisch absolut (kommunikation.neu).
"""

SEED_VERSION = 1

# Einordnung der Standard-Vorlagen: name -> (art, kategorie).
# art: "intern" (an Mitglieder/Vorstand) oder "extern" (nach draußen).
# Wird beim Säen mitgeschrieben und einmalig auf Bestandsvorlagen übertragen.
# Die Namen MÜSSEN exakt denen in alle() entsprechen (tests/test_vorlagen_zuordnung.py).
STANDARD_ZUORDNUNG = {
    "🎉 Willkommen im Verein (neues Mitglied)": ("intern", "Mitgliedschaft"),
    "🛠️ Willkommen in der Verwaltung (Vorstand)": ("intern", "Mitgliedschaft"),
    "📩 Bestätigung der Aufnahme": ("intern", "Mitgliedschaft"),
    "👋 Kündigungsbestätigung (Austritt)": ("intern", "Mitgliedschaft"),
    "🗳️ Einladung zur Mitgliederversammlung": ("intern", "Sitzungen & Versammlung"),
    "📋 Einladung zur Vorstandssitzung": ("intern", "Sitzungen & Versammlung"),
    "📄 Protokoll-Versand": ("intern", "Sitzungen & Versammlung"),
    "🎪 Veranstaltungshinweis": ("extern", "Veranstaltungen"),
}


def _logo(vid, hat_logo):
    if not hat_logo:
        return ""
    return (f'<p><img src="/einstellungen/logo/{vid}" alt="{{vereinsname}}" '
            'style="max-height:72px"></p>')


def kurzanleitung_html(app_url):
    """Kurzanleitung Mitglieder-App – gemeinsamer Baustein für Vorlage + Automatik."""
    ziel = f'<a href="{app_url}">{app_url.replace("https://", "")}</a>' if app_url else "die Vereins-App"
    return (
        "<p><strong>So nutzt du unsere Vereins-App – in 5 Schritten:</strong></p>"
        f"<ol><li><strong>Anmelden:</strong> Öffne {ziel} und melde dich mit dieser "
        "E-Mail-Adresse an.</li>"
        "<li><strong>Aufs Handy holen:</strong> Ganz unten auf der Seite steht „App installieren“ – "
        "antippen und den Schritten folgen. Danach liegt die App wie gewohnt auf deinem "
        "Startbildschirm.</li>"
        "<li><strong>Termine &amp; Anmeldungen:</strong> Unter „Termine“ findest du alles "
        "Kommende und kannst dich direkt anmelden – deine Eintrittskarte (Voucher) kommt "
        "aufs Handy.</li>"
        "<li><strong>Dein Profil:</strong> Halte deine Kontaktdaten selbst aktuell – das "
        "erspart dem Vorstand viel Arbeit.</li>"
        "<li><strong>Nichts verpassen:</strong> Erlaube Mitteilungen, dann erfährst du von "
        "neuen Terminen und Ankündigungen zuerst.</li></ol>")


def verwaltung_anleitung_html(app_url):
    """Rundgang durch die Verwaltung – Baustein für die Vorstands-Willkommensmail."""
    ziel = f'<a href="{app_url}">{app_url.replace("https://", "")}</a>' if app_url else "der Vereins-App"
    return (
        "<p><strong>Dein Rundgang durch die Verwaltung:</strong></p>"
        f"<p>Nach der Anmeldung in {ziel} erreichst du die Verwaltung über das 🛠️-Symbol "
        "ganz unten in der App.</p>"
        "<ul>"
        "<li><strong>🏠 Dashboard</strong> – der Überblick: Zahlen, anstehende Aufgaben, Geburtstage.</li>"
        "<li><strong>👥 Mitglieder</strong> – Stammdaten, Gruppen, Beitrittsanträge, Zugänge "
        "(wer darf was) und die Kommunikation mit Vorlagen für Rund-Mails.</li>"
        "<li><strong>📆 Kalender &amp; Veranstaltungen</strong> – Termine anlegen, "
        "Teilnehmenden-Anmeldung mit Eintrittskarten, Schichtplanung.</li>"
        "<li><strong>📊 Finanzen</strong> – Kassenbuch, Bankkonten mit automatischem Abgleich, "
        "Beiträge/SEPA, Rechnungen und Spenden.</li>"
        "<li><strong>📣 Öffentlichkeitsarbeit</strong> – Redaktionsplan, Pressemitteilungen, "
        "Fotoarchiv und der Presseverteiler.</li>"
        "<li><strong>⚙️ Einstellungen</strong> – Vereinsdaten, Logo und Farben, E-Mail-Versand; "
        "die Datensicherung läuft automatisch.</li></ul>"
        "<p>Wichtig: Jede Änderung wirkt sofort für alle – im Zweifel lieber einmal mehr "
        "nachfragen. Bei Fragen hilft die 💡-Hilfe unten in der App oder der restliche Vorstand.</p>")


def alle(vid, hat_logo, app_url):
    """Liste (name, betreff, inhalt_html) aller Standard-Vorlagen."""
    logo = _logo(vid, hat_logo)
    gruss = "<p>Herzliche Grüße<br>dein Vorstand von {vereinsname}</p>"
    return [
        ("🎉 Willkommen im Verein (neues Mitglied)",
         "Willkommen bei {vereinsname}, {vorname}! 🎉",
         logo +
         "<p style=\"font-size:18px\"><strong>Herzlich willkommen, {vorname}!</strong></p>"
         "<p>wir freuen uns riesig, dich als neues Mitglied bei <strong>{vereinsname}</strong> "
         "zu begrüßen. Schön, dass du dabei bist!</p>" +
         kurzanleitung_html(app_url) +
         "<p>Wenn etwas nicht klappt oder du Fragen hast: Antworte einfach auf diese "
         "E-Mail – wir helfen gern.</p>" + gruss),

        ("🛠️ Willkommen in der Verwaltung (Vorstand)",
         "Dein Verwaltungszugang bei {vereinsname}",
         logo +
         "<p><strong>Hallo {vorname},</strong></p>"
         "<p>du hast ab sofort Zugang zur Verwaltung von <strong>{vereinsname}</strong> – "
         "danke, dass du Verantwortung übernimmst! Diese Mail ist dein Wegweiser für den Start.</p>" +
         kurzanleitung_html(app_url) + verwaltung_anleitung_html(app_url) + gruss),

        ("📩 Bestätigung der Aufnahme",
         "Deine Aufnahme bei {vereinsname}",
         logo +
         "<p>Hallo {vorname},</p>"
         "<p>hiermit bestätigen wir deine Aufnahme in den Verein <strong>{vereinsname}</strong>. "
         "Deine Mitgliedsnummer lautet <strong>{mitgliedsnummer}</strong>, der Mitgliedsbeitrag "
         "beträgt <strong>{beitrag} €</strong>.</p>"
         "<p>Satzung und Beitragsordnung findest du in der Vereins-App unter „Über uns“ – "
         "oder frag uns einfach danach.</p>" + gruss),

        ("🗳️ Einladung zur Mitgliederversammlung",
         "Einladung zur Mitgliederversammlung am [Datum]",
         logo +
         "<p>Hallo {vorname},</p>"
         "<p>hiermit laden wir dich herzlich zur <strong>Mitgliederversammlung</strong> von "
         "{vereinsname} ein:</p>"
         "<p>📅 <strong>[Datum]</strong>, 🕒 <strong>[Uhrzeit]</strong> Uhr<br>"
         "📍 <strong>[Ort]</strong></p>"
         "<p><strong>Vorläufige Tagesordnung:</strong></p>"
         "<ol><li>Begrüßung und Feststellung der Beschlussfähigkeit</li>"
         "<li>Genehmigung des Protokolls der letzten Versammlung</li>"
         "<li>Bericht des Vorstands</li>"
         "<li>Kassenbericht und Bericht der Kassenprüfung</li>"
         "<li>Entlastung des Vorstands</li>"
         "<li>[Wahlen / Anträge / Satzungsänderungen]</li>"
         "<li>Verschiedenes</li></ol>"
         "<p>Anträge zur Tagesordnung reiche bitte bis zum <strong>[Datum]</strong> "
         "schriftlich beim Vorstand ein. Die Einladung ergeht fristgerecht gemäß Satzung.</p>"
         "<p>Deine Stimme zählt – wir freuen uns auf dich!</p>" + gruss),

        ("📋 Einladung zur Vorstandssitzung",
         "Vorstandssitzung am [Datum] – Einladung & Tagesordnung",
         "<p>Hallo {vorname},</p>"
         "<p>hiermit lade ich dich zur nächsten <strong>Vorstandssitzung</strong> ein:</p>"
         "<p>📅 <strong>[Datum]</strong>, 🕒 <strong>[Uhrzeit]</strong> Uhr, "
         "📍 <strong>[Ort / online]</strong></p>"
         "<p><strong>Tagesordnung:</strong></p>"
         "<ol><li>[Punkt 1]</li><li>[Punkt 2]</li><li>Finanzen – aktueller Stand</li>"
         "<li>Kommende Veranstaltungen</li><li>Verschiedenes</li></ol>"
         "<p>Weitere Punkte gern vorab an mich. Bitte gib kurz Bescheid, ob du dabei bist.</p>"
         + gruss),

        ("👋 Kündigungsbestätigung (Austritt)",
         "Bestätigung deines Austritts aus {vereinsname}",
         logo +
         "<p>Hallo {vorname},</p>"
         "<p>hiermit bestätigen wir deinen Austritt aus <strong>{vereinsname}</strong> zum "
         "<strong>[Datum]</strong>. Ein eventuell erteiltes SEPA-Mandat endet damit; bis zum "
         "Austrittstermin fällige Beiträge bleiben davon unberührt.</p>"
         "<p>Wir danken dir herzlich für deine Zeit und deine Unterstützung im Verein. "
         "Solltest du irgendwann zurückkommen wollen: Die Tür steht dir immer offen.</p>"
         "<p>Alles Gute für dich!</p>" + gruss),

        ("📄 Protokoll-Versand",
         "Protokoll der [Sitzung/Versammlung] vom [Datum]",
         "<p>Hallo {vorname},</p>"
         "<p>im Anhang findest du das Protokoll der <strong>[Sitzung/Versammlung]</strong> "
         "vom <strong>[Datum]</strong>.</p>"
         "<p>Anmerkungen oder Einwände sende bitte bis zum <strong>[Datum]</strong> an "
         "[E-Mail/Person] – danach gilt das Protokoll als genehmigt.</p>"
         "<p><em>💡 Datei über die Büroklammer unten anhängen, bevor du sendest.</em></p>" + gruss),

        ("🎪 Veranstaltungshinweis",
         "Nicht verpassen: [Veranstaltung] am [Datum]",
         logo +
         "<p>Hallo {vorname},</p>"
         "<p>wir möchten dich herzlich einladen:</p>"
         "<p style=\"font-size:17px\"><strong>[Veranstaltung]</strong><br>"
         "📅 [Datum], 🕒 [Uhrzeit] Uhr · 📍 [Ort]</p>"
         "<p>[Zwei, drei Sätze, worum es geht und warum sich das Kommen lohnt.]</p>"
         "<p>👉 Anmeldung direkt in der Vereins-App unter „Termine“ – oder über "
         "[Anmeldelink].</p>"
         "<p>Wir freuen uns auf dich!</p>" + gruss),
    ]
