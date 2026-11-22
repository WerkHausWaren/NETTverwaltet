"""
E-Mail-Versand über das SMTP-Konto des Vereins – oder, wo eine Person bewusst
schreibt (Kommunikation, Versammlungs-Einladung), über ihr persönliches
Absenderprofil (`benutzer.absender_*`).

Nutzt ausschließlich die Python-Standardbibliothek (smtplib, email) –
keine Zusatzinstallation nötig.
"""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr


def _verbindung(einst, profil=None):
    """Baut eine SMTP-Verbindung auf – über die Vereins-Einstellungen oder,
    wenn ein vollständiges persönliches Profil vorliegt, über dessen Konto."""
    if profil and profil.get("eigen_smtp"):
        host = profil["host"]
        port = int(profil["port"] or 587)
        security = (profil["security"] or "starttls").lower()
        user, passwort = profil["user"], profil["passwort"]
    else:
        host = einst["smtp_host"]
        port = int(einst["smtp_port"] or 587)
        security = (einst["smtp_security"] or "starttls").lower()
        user, passwort = einst["smtp_user"], einst["smtp_passwort"]

    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        if security == "starttls":
            server.starttls(context=ssl.create_default_context())
    if user:
        server.login(user, passwort)
    return server


def absender(einst):
    """Ermittelt die Absenderadresse des Vereins (Fallback auf SMTP-Benutzer)."""
    name = einst["smtp_absender_name"] or einst["vereinsname"] or ""
    adresse = einst["smtp_absender_email"] or einst["smtp_user"] or ""
    return name, adresse


def profil_laden(benutzer_id):
    """Persönliches Absenderprofil eines Kontos; None, wenn keine Adresse gesetzt.

    `eigen_smtp` sagt, ob die Person über ihr eigenes Postfach einliefern kann
    (Host + Benutzer + Passwort vorhanden). Ohne eigenes SMTP sendet der Verein
    mit ihrem Namen und ihrer Adresse als Antwort-Ziel – siehe einzelmail().
    """
    from .db import get_system_db
    from . import krypto
    conn = get_system_db()
    z = conn.execute("SELECT * FROM benutzer WHERE id=?", (int(benutzer_id),)).fetchone()
    conn.close()
    if not z or "absender_email" not in z.keys():
        return None
    adresse = (z["absender_email"] or "").strip()
    if not adresse:
        return None
    passwort = krypto.entschluesseln(z["absender_smtp_passwort"]) \
        if z["absender_smtp_passwort"] else ""
    profil = {
        "name": (z["absender_name"] or z["anzeigename"] or "").strip(),
        "email": adresse,
        "host": (z["absender_smtp_host"] or "").strip(),
        "port": (z["absender_smtp_port"] or "").strip() or "587",
        "security": (z["absender_smtp_security"] or "starttls").strip(),
        "user": (z["absender_smtp_user"] or "").strip(),
        "passwort": passwort,
        "signatur": (z["absender_signatur"] or "").strip()
        if "absender_signatur" in z.keys() else "",
    }
    profil["eigen_smtp"] = bool(profil["host"] and profil["user"] and profil["passwort"])
    return profil


def _html_zu_text(html):
    """Einfacher HTML→Text-Fallback für Mail-Clients ohne HTML-Anzeige."""
    import re
    t = re.sub(r"(?i)<br\s*/?>", "\n", html or "")
    t = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", t)
    t = re.sub(r"<[^<>]+>", "", t)   # [^<>] statt [^>]: sonst quadratische Laufzeit bei vielen „<“
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
         .replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def einzelmail(einst, empfaenger, betreff, inhalt, html=None, anhaenge=None, profil=None,
               cc=None, bcc=None):
    """Sendet eine E-Mail (optional HTML + Anhänge). Gibt (erfolg, fehlermeldung) zurück.

    anhaenge: Liste von {"name": str, "daten": bytes, "mime": "typ/subtyp"}.
    profil:   persönliches Absenderprofil (profil_laden). Mit eigenem SMTP wird
              wirklich VON der eigenen Adresse gesendet; ohne eigenes SMTP sendet
              der Verein mit persönlichem Namen und der eigenen Adresse als
              Antwort-Ziel."""
    from .demo import mail_unterdrueckt
    if mail_unterdrueckt():
        # Öffentliche Demo: nichts verlassen lassen (Spam/Phishing-Schutz). Für die
        # Oberfläche gilt der Versand als erledigt – sie zeigt den Demo-Hinweis.
        return True, ""
    reply_to = ""
    if profil and profil.get("eigen_smtp"):
        name, adresse = profil["name"], profil["email"]
    elif profil:
        v_name, adresse = absender(einst)
        name = f"{profil['name']} · {v_name}" if profil["name"] else v_name
        # Nur setzen, wenn die eigene Adresse wirklich abweicht: Ein Antwort-Ziel
        # gleich dem Absender bringt nichts – und manitus Ausgangsfilter weist
        # solche Mails als Spam ab (geprüft am 29.07.2026).
        if profil["email"].strip().lower() != (adresse or "").strip().lower():
            reply_to = profil["email"]
    else:
        name, adresse = absender(einst)
    if not adresse:
        return False, "Keine Absenderadresse in den Einstellungen hinterlegt."
    msg = EmailMessage()
    msg["Subject"] = betreff
    msg["From"] = formataddr((name, adresse))
    msg["To"] = empfaenger
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        # send_message entfernt den Bcc-Kopf vor dem Senden, stellt aber zu
        msg["Bcc"] = ", ".join(bcc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(inhalt or (_html_zu_text(html) if html else ""))
    if html:
        msg.add_alternative(html, subtype="html")
    for an in (anhaenge or []):
        try:
            maintype, subtype = (an.get("mime") or "application/octet-stream").split("/", 1)
        except ValueError:
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(an["daten"], maintype=maintype, subtype=subtype, filename=an["name"])
    try:
        server = _verbindung(einst, profil)
        server.send_message(msg)
        server.quit()
        return True, ""
    except Exception as e:
        return False, str(e)


def testverbindung(einst):
    """Prüft, ob Login und Verbindung funktionieren. Gibt (ok, meldung) zurück."""
    if not einst["smtp_host"]:
        return False, "Kein SMTP-Server hinterlegt."
    try:
        server = _verbindung(einst)
        server.noop()
        server.quit()
        return True, "Verbindung erfolgreich – Login hat funktioniert."
    except Exception as e:
        return False, str(e)


def _fehler_erklaeren(e):
    """Übersetzt bekannte SMTP-Fehler in verständliches Deutsch."""
    text = str(e)
    if "basic authentication is disabled" in text.lower() or "5.7.139" in text:
        return ("Microsoft (outlook, hotmail, live.de) erlaubt keinen "
                "Passwort-Login für Mailprogramme mehr – das ist bei Microsoft "
                "abgeschaltet, kein Fehler deiner Eingaben. Lösung: Host, "
                "Benutzer und Passwort hier leer lassen und nur Name und "
                "Adresse speichern – dann sendet der Verein für dich und "
                "Antworten gehen an deine Adresse. Echtes Selbst-Senden geht "
                "nur mit einem Postfach, das SMTP-Login erlaubt (z. B. eine "
                "Vereins- oder Anbieteradresse wie manitu, GMX, web.de).")
    if "535" in text:
        return ("Anmeldung abgelehnt (535): Benutzername oder Passwort passen "
                "nicht. Bei vielen Anbietern ist der Benutzername die "
                "vollständige E-Mail-Adresse; manche verlangen ein eigenes "
                "App-Passwort statt des normalen. Original: " + text)
    return text


def testverbindung_profil(profil):
    """Prüft die persönlichen SMTP-Zugangsdaten. Gibt (ok, meldung) zurück."""
    if not profil:
        return False, "Noch keine eigene Absenderadresse eingetragen."
    if not profil.get("eigen_smtp"):
        return False, ("Ohne eigene SMTP-Zugangsdaten sendet der Verein für dich – "
                       "mit deinem Namen und deiner Adresse als Antwort-Ziel. "
                       "Zum echten Selbst-Versand Host, Benutzer und Passwort eintragen.")
    try:
        server = _verbindung(None, profil)
        server.noop()
        server.quit()
        return True, f"Verbindung erfolgreich – {profil['email']} kann selbst senden."
    except Exception as e:
        return False, _fehler_erklaeren(e)
