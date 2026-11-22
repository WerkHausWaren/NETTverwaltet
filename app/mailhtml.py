"""Automatische System-Mails im Vereins-Design (Logo, Vereinsfarbe).

Wird für Mails benutzt, die die App selbst verschickt (Willkommen nach der
Aufnahme, Willkommen für Freunde, Inaktivitäts-Erinnerung). Die manuell
versendbaren Gegenstücke liegen als bearbeitbare Vorlagen unter
Mitglieder → Kommunikation → Vorlagen (siehe vorlagen_texte.py).
"""
from . import vorlagen_texte


def rahmen(einst, host, inhalt_html):
    """Gestalteter Mail-Rahmen: Farbbalken, Logo, Fußzeile mit Vereinsname."""
    farbe = einst["farbe_primaer"] or "#2563eb"
    schrift = (einst["mail_schriftart"] if "mail_schriftart" in einst.keys() else "") or "Arial"
    logo = ""
    if einst["logo_dateiname"]:
        logo = (f'<img src="{host}/einstellungen/logo/{einst["id"]}" alt="" '
                'style="max-height:64px;margin-bottom:6px">')
    fuss = einst["vereinsname"] or ""
    if einst["email"]:
        fuss += f' · <a href="mailto:{einst["email"]}" style="color:#64748b">{einst["email"]}</a>'
    return (f'<div style="font-family:{schrift},Arial,sans-serif;font-size:14.5px;'
            'color:#1e293b;line-height:1.6;max-width:640px">'
            f'<div style="border-top:4px solid {farbe};padding:16px 0 4px">{logo}</div>'
            + inhalt_html +
            '<div style="margin-top:22px;padding-top:10px;border-top:1px solid #e2e8f0;'
            f'color:#64748b;font-size:12.5px">{fuss}</div></div>')


def willkommen_mitglied(einst, host, vorname):
    """(betreff, html) – Willkommensmail nach der Aufnahme, mit App-Kurzanleitung."""
    inhalt = (f'<p style="font-size:18px"><strong>Herzlich willkommen, {vorname}!</strong></p>'
              f'<p>Wir freuen uns riesig, dich als neues Mitglied bei '
              f'<strong>{einst["vereinsname"]}</strong> zu begrüßen – schön, dass du dabei bist!</p>'
              + vorlagen_texte.kurzanleitung_html(host) +
              '<p>Wenn etwas nicht klappt oder du Fragen hast: Antworte einfach auf diese '
              'E-Mail – wir helfen gern.</p>'
              f'<p>Herzliche Grüße<br>dein Vorstand von {einst["vereinsname"]}</p>')
    return (f'Willkommen bei {einst["vereinsname"]}, {vorname}! 🎉',
            rahmen(einst, host, inhalt))


def willkommen_freund(einst, host, vorname):
    """(betreff, html) – Begrüßung nach bestätigter Freunde-Registrierung."""
    inhalt = (f'<p style="font-size:18px"><strong>Schön, dass du da bist, {vorname}!</strong></p>'
              f'<p>Dein Freunde-Zugang bei <strong>{einst["vereinsname"]}</strong> ist bestätigt. '
              'Damit siehst du unsere öffentlichen Termine und kannst dich direkt anmelden.</p>'
              f'<p><strong>So geht’s weiter:</strong> Öffne <a href="{host}">die Vereins-App</a>, '
              'melde dich mit dieser E-Mail-Adresse an und stöbere unter „Termine“. Über den '
              'Knopf „App installieren“ ganz unten holst du dir die App aufs Handy – und mit erlaubten '
              'Mitteilungen verpasst du keine Ankündigung.</p>'
              f'<p>Bis bald – wir freuen uns auf dich!<br>{einst["vereinsname"]}</p>')
    return (f'Willkommen bei {einst["vereinsname"]}! 👋',
            rahmen(einst, host, inhalt))


def inaktiv_erinnerung(einst, host, vorname, n_termine=0):
    """(betreff, html) – freundlicher Gruß an lange nicht angemeldete Mitglieder."""
    termin_satz = (f'Im Kalender warten übrigens gerade <strong>{n_termine} kommende '
                   'Termine</strong> auf dich. ' if n_termine else '')
    inhalt = (f'<p style="font-size:18px"><strong>Wir vermissen dich, {vorname}! 👋</strong></p>'
              f'<p>Bei <strong>{einst["vereinsname"]}</strong> ist einiges los, und du hast schon '
              'eine Weile nicht mehr in die Vereins-App geschaut. '
              + termin_satz +
              f'Schau doch mal wieder rein: <a href="{host}">{host.replace("https://", "")}</a> – '
              'anmelden mit dieser E-Mail-Adresse, fertig.</p>'
              '<p>Wenn dich etwas stört oder du Hilfe mit der App brauchst, antworte einfach auf '
              'diese E-Mail – wir kümmern uns.</p>'
              f'<p>Herzliche Grüße<br>dein Vorstand von {einst["vereinsname"]}</p>')
    return (f'Lange nicht gesehen, {vorname} – schau mal wieder rein! 👋',
            rahmen(einst, host, inhalt))


def vorabankuendigung(einst, host, vorname, betrag, faellig_am, mandatsreferenz,
                      verwendungszweck=""):
    """(betreff, html) – Ankündigung eines Lastschrifteinzugs.

    Vor jedem Einzug muss die Person wissen, wann welcher Betrag von ihrem Konto
    geht (Vorabankündigung). Die Angaben unten sind genau die, mit denen sie den
    Einzug später auf ihrem Kontoauszug wiedererkennt.
    """
    from datetime import date as _date
    try:
        tag = _date.fromisoformat(str(faellig_am)[:10]).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        tag = str(faellig_am or "")
    betrag_de = f"{float(betrag or 0):.2f}".replace(".", ",")
    zeilen = [
        ("Betrag", f"<strong>{betrag_de}&nbsp;€</strong>"),
        ("Einzug am", f"<strong>{tag}</strong>"),
        ("Verwendungszweck", verwendungszweck or "Mitgliedsbeitrag"),
        ("Mandatsreferenz", mandatsreferenz or "–"),
        ("Gläubiger-Identifikationsnummer", einst["glaeubiger_id"] or "–"),
    ]
    tabelle = "".join(
        f'<tr><td style="padding:6px 12px 6px 0;color:#555">{k}</td>'
        f'<td style="padding:6px 0">{v}</td></tr>' for k, v in zeilen)
    inhalt = (
        f'<p style="font-size:18px"><strong>Hallo {vorname},</strong></p>'
        f'<p>wir kündigen dir hiermit an, dass wir deinen Beitrag wie vereinbart per '
        f'Lastschrift von deinem Konto einziehen. Du musst nichts tun – bitte sorge nur '
        f'dafür, dass das Konto zum genannten Termin gedeckt ist.</p>'
        f'<table style="border-collapse:collapse;margin:16px 0;font-size:15px">{tabelle}</table>'
        f'<p style="font-size:13px;color:#555">Der Einzug erfolgt frühestens am genannten Tag; '
        f'je nach Bank kann die Buchung ein bis zwei Tage später erscheinen. '
        f'Du kannst der Abbuchung innerhalb von acht Wochen ab Belastung ohne Angabe von '
        f'Gründen widersprechen – wende dich dafür an deine Bank.</p>'
        f'<p>Wenn etwas nicht stimmt – falscher Betrag, altes Konto, Frage zur Höhe – '
        f'antworte einfach auf diese E-Mail. Wir klären das gern vorher.</p>'
        f'<p>Herzliche Grüße<br>dein Vorstand von {einst["vereinsname"]}</p>')
    return (f'Ankündigung: Beitragseinzug am {tag}', rahmen(einst, host, inhalt))


def foto_anfrage(einst, host, vorname, termin_titel, datum, link):
    """(betreff, html) – Bitte um die Fotoerlaubnis nach einer Anmeldung.

    Geht nur an Personen, die die Frage noch nicht beantwortet haben. Der Link
    führt auf die persönliche Seite, auf der sie zustimmen, ablehnen und ihre
    Entscheidung jederzeit ändern können.
    """
    from datetime import date as _date
    try:
        tag = _date.fromisoformat(str(datum)[:10]).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        tag = str(datum or "")
    inhalt = (
        f'<p style="font-size:18px"><strong>Hallo {vorname},</strong></p>'
        f'<p>schön, dass du bei <strong>{termin_titel}</strong>'
        f'{f" am {tag}" if tag else ""} dabei bist! Eine Bitte vorab:</p>'
        f'<p>Wir machen bei unseren Veranstaltungen gern ein paar Fotos – für die '
        f'Vereinsarbeit und manchmal für unsere Kanäle bei Facebook, Instagram und WhatsApp. '
        f'<strong>Ob du dabei aufs Bild möchtest, entscheidest allein du.</strong></p>'
        f'<p style="margin:20px 0"><a href="{link}" style="background:#2563eb;color:#ffffff;'
        f'padding:12px 22px;border-radius:8px;text-decoration:none;font-weight:700">'
        f'Meine Entscheidung mitteilen</a></p>'
        f'<p style="font-size:13px;color:#555">Der Link ist dein persönlicher Zugang – du kannst '
        f'deine Entscheidung dort auch später jederzeit ändern. Wenn du nichts angibst, '
        f'achten wir darauf, dass du nicht erkennbar fotografiert wirst.</p>'
        f'<p>Herzliche Grüße<br>{einst["vereinsname"]}</p>')
    return (f'Kurze Frage zu Fotos – {termin_titel}', rahmen(einst, host, inhalt))
