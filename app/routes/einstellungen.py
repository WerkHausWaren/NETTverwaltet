"""Einstellungen des aktiven Vereins: Stammdaten, Bank, SMTP, Design, Module."""
import os
import time
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, send_from_directory, abort)
from ..db import get_db, get_einstellungen, aktiver_verein_id, DATA_DIR
from .. import mail, farben, krypto
from ..design import _HEX

# Felder, die verschlüsselt in der DB liegen (PIN/Passwörter)
GEHEIM_FELDER = {"smtp_passwort", "fints_pin", "ncc_pass"}

bp = Blueprint("einstellungen", __name__, url_prefix="/einstellungen")

LOGO_DIR = os.path.join(DATA_DIR, "logos")

# Textfelder, die direkt übernommen werden
TEXTFELDER = [
    "vereinsname", "strasse", "plz", "ort", "email", "telefon", "website", "instagram",
    "whatsapp_kanal",
    "ueber_text", "app_url", "event_import_url",
    "mail_schriftart", "signatur_text",
    "datenschutz_url", "datenschutz_verantwortlich", "rechtliches_zusatz",
    "registergericht", "registernummer", "steuernummer", "vorstand",
    "finanzamt", "freistellung_datum", "freistellung_zeitraum", "foerderzwecke", "wj_beginn",
    "werkhaus_name", "ausweis_platzhalter", "ausweis_symbol", "kino_name", "feier_symbol",
    "beitrag_normal", "beitrag_ermaessigt", "beitrag_intervall", "beitrag_hinweis",
    "satzung_url", "beitragsordnung_url", "antwort_stunden", "angebote",
    "werkhaus_adresse",
    "bank_kontoinhaber", "bank_iban", "bank_bic", "glaeubiger_id", "paypal_me",
    "smtp_host", "smtp_security", "smtp_user", "smtp_passwort",
    "smtp_absender_name", "smtp_absender_email", "presse_kopie_email",
    "pinnwand_kategorien", "pinnwand_mail_an",
    "schriftart", "farbe_primaer", "farbe_sidebar",
    # Bankdaten des VEREINSKONTOS. Anmeldename, PIN und Produkt-ID sind
    # persönlich und stehen unter „Mein Bankzugang" (benutzer.bank_fints_*).
    "fints_blz", "fints_url", "konto_bezeichnung",
    "ncc_server", "ncc_user", "ncc_pass",
    "nc_gruppe_admin", "nc_gruppe_mitglied", "nc_gruppe_extern",
    "nc_gruppe_schatz", "nc_gruppe_oeffentlichkeit",
]
# Modul-Schalter (Checkboxen -> 0/1)
MODULE = ["modul_aktuelles", "modul_geburtstage", "modul_jubilaeen", "modul_spenden", "modul_rechnungen",
          "modul_veranstaltungen", "modul_werkhaus", "modul_schluessel", "modul_dms", "modul_schichten",
          "modul_kinder", "modul_adventskalender", "modul_contenthub", "modul_verteilen", "modul_signage",
          "signatur_aktiv", "nc_externe_erlaubt", "pinnwand_gast", "portal_sprachen", "pinnwand_pruefung", "sprachen_labels"]

SCHRIFTARTEN = ["System", "Serif", "Klassisch (Georgia)", "Modern (Verdana)", "Schreibmaschine"]


@bp.route("/", methods=["GET", "POST"])
def index():
    conn = get_db()
    vid = aktiver_verein_id()
    if request.method == "POST":
        werte, spalten = [], []
        for f in TEXTFELDER:
            wert = request.form.get(f, "").strip()
            # Farben landen roh in <style>-Blöcken – nur echte Hex-Werte zulassen
            if f in ("farbe_primaer", "farbe_sidebar") and wert and not _HEX.fullmatch(wert):
                wert = ""
            if f in GEHEIM_FELDER:
                # Geheimnisse stehen nicht mehr im Formular (sonst wären sie im
                # Seitenquelltext lesbar). Ein leeres Feld heißt deshalb
                # „bisherigen Wert behalten" – wie bei „Mein Absender".
                if not wert:
                    continue
                wert = krypto.verschluesseln(wert)
            spalten.append(f)
            werte.append(wert)
        # SMTP-Port
        port = request.form.get("smtp_port", "").strip()
        spalten.append("smtp_port"); werte.append(int(port) if port.isdigit() else 587)
        # Dashboard-Vorlaufzeiten (in Tagen)
        for feld, standard in (("dash_geburtstage_tage", 30), ("dash_jubilaeen_tage", 365)):
            v = request.form.get(feld, "").strip()
            spalten.append(feld)
            werte.append(int(v) if v.isdigit() and int(v) > 0 else standard)
        # Dateiablage-Speicherlimit (MB, 0 = unbegrenzt)
        q = request.form.get("dms_quota_mb", "").strip()
        spalten.append("dms_quota_mb"); werte.append(int(q) if q.isdigit() else 500)
        # Module (Checkbox an = 1)
        for m in MODULE:
            spalten.append(m); werte.append(1 if request.form.get(m) else 0)
        # Gruppen-Trennung: zählende Mitglieder-Gruppen + schichtberechtigte Gruppen
        # (Checkbox-Listen mit Gruppen-IDs; leer = alle, wie bisher)
        gueltig = {str(r["id"]) for r in conn.execute(
            "SELECT id FROM gruppen WHERE verein_id=?", (vid,)).fetchall()}
        for feld in ("mitglied_gruppen", "schicht_gruppen"):
            ausgewaehlt = [g for g in request.form.getlist(feld) if g in gueltig]
            spalten.append(feld); werte.append(",".join(ausgewaehlt))

        setzt = ", ".join(f"{s}=?" for s in spalten)
        conn.execute(f"UPDATE vereine SET {setzt} WHERE id=?", werte + [vid])
        conn.commit()

        # Aus der IBAN alles Weitere ableiten: Bankleitzahl, BIC, Bankname und
        # die FinTS-Adresse für den Umsatzabruf. Nur leere Felder werden
        # gefüllt – wer etwas von Hand eingetragen hat, behält es.
        from .. import fints_adressen
        zeile = conn.execute("SELECT bank_iban, bank_bic, fints_blz, fints_url, "
                             "konto_bezeichnung FROM vereine WHERE id=?", (vid,)).fetchone()
        bank = fints_adressen.aus_iban(zeile["bank_iban"]) if zeile["bank_iban"] else None
        if bank:
            if not bank["gueltig"]:
                flash("⚠️ Die IBAN sieht nach einem Tippfehler aus – die Prüfsumme "
                      "stimmt nicht. Bitte noch einmal vergleichen.", "fehler")
            ergaenzt = []
            for feld, wert, anzeige in (("fints_blz", bank["blz"], "Bankleitzahl"),
                                        ("bank_bic", bank["bic"], "BIC"),
                                        ("fints_url", bank["url"], "FinTS-Adresse"),
                                        ("konto_bezeichnung", bank["bank"], "Bank")):
                if wert and not (zeile[feld] or "").strip():
                    conn.execute(f"UPDATE vereine SET {feld}=? WHERE id=?", (wert, vid))
                    ergaenzt.append(anzeige)
            if ergaenzt:
                conn.commit()
                flash("🏦 Aus der IBAN ergänzt: " + ", ".join(ergaenzt)
                      + (f" ({bank['bank']})" if bank["bank"] else "") + ".", "erfolg")
            if bank["gueltig"] and not bank["url"] and not (zeile["fints_url"] or "").strip():
                flash("Für diese Bank kennt das Verzeichnis keine FinTS-Adresse – "
                      "für den Umsatzabruf bitte bei der Bank erfragen.", "info")

        # Logo-Upload verarbeiten
        logo = request.files.get("logo")
        if logo and logo.filename:
            os.makedirs(LOGO_DIR, exist_ok=True)
            endung = os.path.splitext(logo.filename)[1].lower()
            if endung in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
                dateiname = f"verein_{vid}{endung}"
                logo.save(os.path.join(LOGO_DIR, dateiname))
                conn.execute("UPDATE vereine SET logo_dateiname=? WHERE id=?", (dateiname, vid))
                conn.commit()
            else:
                flash("Logo muss ein Bild sein (PNG, JPG, GIF, SVG oder WEBP).", "fehler")

        conn.close()
        aktion = request.form.get("aktion")
        if aktion == "testen":
            ok, meldung = mail.testverbindung(get_einstellungen())
            flash(("✅ " if ok else "❌ ") + meldung, "erfolg" if ok else "fehler")
        elif aktion == "nextcloud_test":
            from .. import caldav
            ok, meldung = caldav.testverbindung(get_einstellungen())
            flash(("✅ Nextcloud: " if ok else "❌ Nextcloud: ") + meldung, "erfolg" if ok else "fehler")
        elif aktion == "test_mail":
            einst = get_einstellungen()
            empf = (request.form.get("test_empfaenger") or "").strip() or (einst["smtp_absender_email"] or "")
            if not empf:
                flash("❌ Bitte eine Ziel-Adresse für die Test-E-Mail angeben.", "fehler")
            else:
                betreff = "Test-E-Mail – " + (einst["vereinsname"] or "Vereinsverwaltung")
                inhalt = ("Dies ist eine Test-E-Mail aus der Vereinsverwaltung.\n\n"
                          "Wenn du diese Nachricht liest, funktioniert der E-Mail-Versand (SMTP) korrekt.")
                ok, meldung = mail.einzelmail(einst, empf, betreff, inhalt)
                if ok:
                    flash(f"✅ Test-E-Mail an {empf} versendet. Bitte im Postfach (ggf. Spam) nachsehen.", "erfolg")
                else:
                    flash("❌ Versand fehlgeschlagen: " + (meldung or "Unbekannter Fehler"), "fehler")
        else:
            flash("Einstellungen gespeichert.", "erfolg")
        return redirect(url_for("einstellungen.index"))

    e = get_einstellungen()
    gruppen = conn.execute("SELECT id, name FROM gruppen WHERE verein_id=? ORDER BY rang, name",
                           (vid,)).fetchall()
    conn.close()
    from ..db import id_liste
    from .portal import PORTAL_BLOECKE
    bloecke_aus = {k.strip() for k in (e.get("portal_bloecke_aus") or "").split(",") if k.strip()}
    return render_template("einstellungen.html", aktiv="einstellungen", e=e,
                           schriftarten=SCHRIFTARTEN, gruppen=gruppen,
                           mitglied_gruppen=id_liste(e.get("mitglied_gruppen")),
                           schicht_gruppen=id_liste(e.get("schicht_gruppen")),
                           portal_bloecke=PORTAL_BLOECKE, portal_bloecke_aus=bloecke_aus)


@bp.route("/logo-entfernen", methods=["POST"])
def logo_entfernen():
    conn = get_db()
    conn.execute("UPDATE vereine SET logo_dateiname='' WHERE id=?", (aktiver_verein_id(),))
    conn.commit()
    conn.close()
    flash("Logo entfernt.", "erfolg")
    return redirect(url_for("einstellungen.index"))


@bp.route("/mitglieder-app", methods=["GET", "POST"])
def mitglieder_app():
    """Eigene Seite für alles rund um die Mitglieder-App: Inhalte der Startseite,
    Türlink, Dateien-Quelle, Freunde-Zugang – und das Aussehen, das der Verein
    für die App vorgibt."""
    from .portal import PORTAL_BLOECKE, PORTAL_BLOCK_KEYS
    from .. import design as design_modul

    if request.method == "POST":
        # Nicht angehakte Startseiten-Blöcke sind für ALLE Mitglieder dieses Vereins aus
        aus = [k for k in PORTAL_BLOCK_KEYS if not request.form.get("block_" + k)]
        quelle = request.form.get("dateien_quelle", "")
        if quelle not in ("", "nextcloud", "server", "beide"):
            quelle = ""
        conn = get_db()
        conn.execute("UPDATE vereine SET tuer_url=?, portal_willkommen=?, portal_bloecke_aus=?, "
                     "dateien_quelle=?, freunde_registrierung=?, seo_index=?, website_stil=? WHERE id=?",
                     (request.form.get("tuer_url", "").strip(),
                      request.form.get("portal_willkommen", "").strip(),
                      ",".join(aus), quelle,
                      1 if request.form.get("freunde_registrierung") else 0,
                      1 if request.form.get("seo_index") else 0,
                      1 if request.form.get("website_stil") else 0,
                      aktiver_verein_id()))
        # Website-Stil: Überschrift + Titelbild der Terminseite (nur Gäste am Computer)
        vid = aktiver_verein_id()
        conn.execute("UPDATE vereine SET website_titel=? WHERE id=?",
                     ((request.form.get("website_titel") or "").strip()[:80], vid))
        if request.form.get("website_titelbild_weg"):
            weg = conn.execute("SELECT website_titelbild FROM vereine WHERE id=?", (vid,)).fetchone()[0] or ""
            conn.execute("UPDATE vereine SET website_titelbild='' WHERE id=?", (vid,))
            if weg.startswith(f"titelbild_v{vid}_"):
                try:
                    os.remove(os.path.join(LOGO_DIR, weg))
                except OSError:
                    pass
        bild = request.files.get("website_titelbild")
        if bild and bild.filename:
            endung = os.path.splitext(bild.filename)[1].lower()
            if endung in (".jpg", ".jpeg", ".png", ".webp"):   # bewusst kein SVG
                os.makedirs(LOGO_DIR, exist_ok=True)
                # Neuer Name je Fassung: Browser speichern das Bild einen Tag – sonst sähen
                # Besucher:innen nach dem Austausch weiter das alte.
                name = f"titelbild_v{vid}_{int(time.time())}{endung}"
                bild.save(os.path.join(LOGO_DIR, name))
                alt = conn.execute("SELECT website_titelbild FROM vereine WHERE id=?", (vid,)).fetchone()[0] or ""
                conn.execute("UPDATE vereine SET website_titelbild=? WHERE id=?", (name, vid))
                if alt.startswith(f"titelbild_v{vid}") and alt != name:
                    try:
                        os.remove(os.path.join(LOGO_DIR, alt))
                    except OSError:
                        pass
            else:
                flash("Das Titelbild muss ein Foto sein (JPG, PNG oder WEBP).", "fehler")
        # Aussehen der App als Vereinsvorgabe – liegt im Unterobjekt "app"
        _bereich_speichern(conn, "app", request.form)
        conn.commit()
        conn.close()
        flash("Einstellungen der Mitglieder-App gespeichert. 📱", "erfolg")
        return redirect(url_for("einstellungen.mitglieder_app"))

    e = get_einstellungen()
    bloecke_aus = [b for b in (e["portal_bloecke_aus"] or "").split(",") if b]
    return render_template("einstellungen/mitglieder_app.html", aktiv="einstellungen_app",
                           e=e, portal_bloecke=PORTAL_BLOECKE, portal_bloecke_aus=bloecke_aus,
                           vorgabe=design_modul.zusammenfuehren(e, None, "app"),
                           schemata=design_modul.SCHEMATA, modi=design_modul.MODI,
                           groessen=design_modul.SCHRIFTGROESSEN,
                           ecken=design_modul.ECKEN,
                           schriftarten=sorted(design_modul.SCHRIFTARTEN))


def _bereich_speichern(conn, bereich, formular):
    """Vereinsvorgabe für einen Bereich ("verwaltung" oder "app") ablegen.

    Alles liegt in vereine.design_config als JSON; die beiden Bereiche stehen
    als eigene Unterobjekte darin, damit Verwaltung und App sich unterscheiden
    dürfen.
    """
    import json
    from .. import design as design_modul
    vid = aktiver_verein_id()
    zeile = conn.execute("SELECT design_config FROM vereine WHERE id=?", (vid,)).fetchone()
    try:
        daten = json.loads((zeile["design_config"] if zeile else "") or "{}")
    except (ValueError, TypeError):
        daten = {}
    if not isinstance(daten, dict):
        daten = {}
    daten[bereich] = design_modul.saeubern(formular.to_dict())
    conn.execute("UPDATE vereine SET design_config=? WHERE id=?",
                 (json.dumps(daten, ensure_ascii=False), vid))


@bp.route("/absender", methods=["GET", "POST"])
def absender():
    """„Mein Absender" – persönliche Mailadresse der angemeldeten Person.

    Wirkt in Kommunikation und Versammlungs-Einladung, wenn dort „Meine
    Adresse" gewählt wird. Automatische Mails sendet weiterhin der Verein.
    """
    from flask_login import current_user
    from .. import krypto
    from ..db import get_system_db

    if not getattr(current_user, "is_authenticated", False):
        flash("Der persönliche Absender braucht ein eigenes Konto – in der "
              "Lokalen Verwaltung gibt es nur die Vereinsadresse.", "info")
        return redirect(url_for("einstellungen.index"))

    sys_conn = get_system_db()
    if request.method == "POST":
        zeile = sys_conn.execute("SELECT absender_smtp_passwort FROM benutzer WHERE id=?",
                                 (int(current_user.id),)).fetchone()
        if request.form.get("loeschen"):
            sys_conn.execute(
                "UPDATE benutzer SET absender_name='', absender_email='', "
                "absender_smtp_host='', absender_smtp_port='', absender_smtp_security='', "
                "absender_smtp_user='', absender_smtp_passwort='', absender_wahl='', "
                "absender_signatur='' WHERE id=?", (int(current_user.id),))
            sys_conn.commit()
            sys_conn.close()
            flash("Persönlicher Absender entfernt – es gilt wieder die Vereinsadresse.", "erfolg")
            return redirect(url_for("einstellungen.absender"))

        # Leeres Passwortfeld = altes Passwort behalten (Muster der Vereins-SMTP)
        neues_pw = request.form.get("smtp_passwort", "")
        pw = krypto.verschluesseln(neues_pw) if neues_pw else \
            (zeile["absender_smtp_passwort"] if zeile else "")
        sys_conn.execute(
            "UPDATE benutzer SET absender_name=?, absender_email=?, absender_smtp_host=?, "
            "absender_smtp_port=?, absender_smtp_security=?, absender_smtp_user=?, "
            "absender_smtp_passwort=?, absender_signatur=? WHERE id=?",
            ((request.form.get("name") or "").strip(),
             (request.form.get("email") or "").strip(),
             (request.form.get("smtp_host") or "").strip(),
             (request.form.get("smtp_port") or "").strip(),
             (request.form.get("smtp_security") or "starttls").strip(),
             (request.form.get("smtp_user") or "").strip(),
             pw, (request.form.get("signatur") or "").strip(),
             int(current_user.id)))
        sys_conn.commit()
        sys_conn.close()

        profil = mail.profil_laden(int(current_user.id))
        if request.form.get("aktion") == "testen":
            ok, meldung = mail.testverbindung_profil(profil)
            flash(("✅ " if ok else "⚠️ ") + meldung, "erfolg" if ok else "fehler")
            if ok and profil:
                e = get_einstellungen()
                ok2, m2 = mail.einzelmail(e, profil["email"], "Testmail deines Absenders",
                                          "Wenn du das liest, funktioniert dein "
                                          "persönlicher Absender.", profil=profil)
                flash("📬 Testmail an dich selbst ist unterwegs." if ok2
                      else f"Testmail scheiterte: {m2}", "erfolg" if ok2 else "fehler")
        else:
            flash("Dein Absender ist gespeichert. ✉️", "erfolg")
        return redirect(url_for("einstellungen.absender"))

    zeile = sys_conn.execute("SELECT * FROM benutzer WHERE id=?",
                             (int(current_user.id),)).fetchone()
    sys_conn.close()
    profil = mail.profil_laden(int(current_user.id))
    e = get_einstellungen()
    return render_template("einstellungen/absender.html", aktiv="einstellungen_absender",
                           z=zeile, profil=profil, e=e,
                           verein_name=e["smtp_absender_name"] or e["vereinsname"],
                           verein_adresse=e["smtp_absender_email"] or e["smtp_user"] or "")


@bp.route("/bankzugang", methods=["GET", "POST"])
def bankzugang():
    """„Mein Bankzugang" – die eigenen Zugangsdaten der angemeldeten Person.

    Jedes Vorstandsmitglied meldet sich mit seinen eigenen Bankdaten an. Das
    Konto des Vereins (IBAN, Bank, FinTS-Adresse) steht weiterhin in den
    allgemeinen Einstellungen – hier geht es nur um die persönliche Anmeldung.
    """
    from flask_login import current_user
    from .. import krypto, fints_bank
    from ..db import get_system_db

    if not getattr(current_user, "is_authenticated", False):
        flash("Der persönliche Bankzugang braucht ein eigenes Konto – in der "
              "Lokalen Verwaltung gibt es keine Anmeldung.", "info")
        return redirect(url_for("einstellungen.index"))

    sys_conn = get_system_db()
    if request.method == "POST":
        zeile = sys_conn.execute("SELECT bank_fints_pin FROM benutzer WHERE id=?",
                                 (int(current_user.id),)).fetchone()
        if request.form.get("loeschen"):
            sys_conn.execute(
                "UPDATE benutzer SET bank_fints_benutzer='', bank_fints_pin='', "
                "bank_fints_produkt_id='', bank_fints_client_data='' WHERE id=?",
                (int(current_user.id),))
            sys_conn.commit()
            sys_conn.close()
            flash("Dein Bankzugang wurde entfernt.", "erfolg")
            return redirect(url_for("einstellungen.bankzugang"))

        # Leeres PIN-Feld = bisherige PIN behalten (Muster von „Mein Absender")
        neue_pin = request.form.get("pin", "")
        pin = krypto.verschluesseln(neue_pin) if neue_pin else \
            (zeile["bank_fints_pin"] if zeile else "")
        anmeldename = (request.form.get("benutzer") or "").strip()
        # Bei geändertem Anmeldenamen ist die alte Gerätebindung wertlos
        alt = sys_conn.execute("SELECT bank_fints_benutzer FROM benutzer WHERE id=?",
                               (int(current_user.id),)).fetchone()
        bindung_behalten = bool(alt and (alt["bank_fints_benutzer"] or "").strip() == anmeldename)
        if bindung_behalten:
            sys_conn.execute(
                "UPDATE benutzer SET bank_fints_benutzer=?, bank_fints_pin=?, "
                "bank_fints_produkt_id=? WHERE id=?",
                (anmeldename, pin, (request.form.get("produkt_id") or "").strip(),
                 int(current_user.id)))
        else:
            sys_conn.execute(
                "UPDATE benutzer SET bank_fints_benutzer=?, bank_fints_pin=?, "
                "bank_fints_produkt_id=?, bank_fints_client_data='' WHERE id=?",
                (anmeldename, pin, (request.form.get("produkt_id") or "").strip(),
                 int(current_user.id)))
        sys_conn.commit()
        sys_conn.close()
        flash("Dein Bankzugang ist gespeichert. 💳 Beim ersten Abruf fragt die Bank "
              "einmal nach einer TAN.", "erfolg")
        return redirect(url_for("einstellungen.bankzugang"))

    zeile = sys_conn.execute("SELECT * FROM benutzer WHERE id=?",
                             (int(current_user.id),)).fetchone()
    sys_conn.close()
    e = get_einstellungen()
    zugang = fints_bank.zugang_laden(int(current_user.id), e)
    fehlt = fints_bank.zugang_vollstaendig(zugang) if zugang else []
    return render_template("einstellungen/bankzugang.html", aktiv="einstellungen_bankzugang",
                           z=zeile, e=e, zugang=zugang, fehlt=fehlt,
                           bank_verfuegbar=fints_bank.verfuegbar())


@bp.route("/aussehen", methods=["GET", "POST"])
def aussehen():
    """„Mein Aussehen" – gilt nur für die angemeldete Person.

    Ohne Anmeldung (Lokale Verwaltung am Mac) gibt es niemanden, dem man etwas
    zuordnen könnte; dann wird die Einstellung als Vereinsvorgabe gespeichert.
    """
    import json
    from flask_login import current_user
    from .. import design as design_modul
    from ..db import get_system_db

    angemeldet = getattr(current_user, "is_authenticated", False)

    if request.method == "POST":
        werte = design_modul.saeubern(request.form.to_dict())
        if request.form.get("zuruecksetzen"):
            werte = None
        if angemeldet:
            sys_conn = get_system_db()
            sys_conn.execute("UPDATE benutzer SET design_config=? WHERE id=?",
                             (json.dumps(werte, ensure_ascii=False) if werte else None,
                              int(current_user.id)))
            sys_conn.commit()
            sys_conn.close()
        else:
            conn = get_db()
            _bereich_speichern(conn, "verwaltung", request.form)
            conn.commit()
            conn.close()
        flash("Auf die Vereinsfarben zurückgesetzt. 🎨" if werte is None
              else "Dein Aussehen ist gespeichert. 🎨", "erfolg")
        return redirect(url_for("einstellungen.aussehen"))

    e = get_einstellungen()
    eigen = None
    if angemeldet:
        sys_conn = get_system_db()
        zeile = sys_conn.execute("SELECT design_config FROM benutzer WHERE id=?",
                                 (int(current_user.id),)).fetchone()
        sys_conn.close()
        if zeile and "design_config" in zeile.keys():
            eigen = zeile["design_config"]
    return render_template(
        "einstellungen/aussehen.html", aktiv="einstellungen_aussehen",
        e=e, angemeldet=angemeldet,
        eigen_gesetzt=bool(eigen),
        werte=design_modul.zusammenfuehren(e, eigen, "verwaltung"),
        vereinsvorgabe=design_modul.zusammenfuehren(e, None, "verwaltung"),
        schemata=design_modul.SCHEMATA, modi=design_modul.MODI,
        groessen=design_modul.SCHRIFTGROESSEN, ecken=design_modul.ECKEN,
        schriftarten=sorted(design_modul.SCHRIFTARTEN))


@bp.route("/nuki", methods=["GET", "POST"])
def nuki():
    """Nuki-Web-Türöffnung: Token speichern, Verbindung testen, Tür auswählen."""
    from .. import nuki_api
    conn = get_db()
    vid = aktiver_verein_id()
    if request.method == "POST":
        aktion = request.form.get("aktion")
        if aktion == "token":
            # Token nur überschreiben, wenn ein neuer eingegeben wurde (leer = behalten)
            neu = (request.form.get("nuki_token") or "").strip()
            if neu:
                conn.execute("UPDATE vereine SET nuki_token=? WHERE id=?",
                             (krypto.verschluesseln(neu), vid))
                conn.commit()
                flash("Nuki-Token gespeichert. Jetzt „Verbindung testen“, um die Türen zu laden.", "erfolg")
            elif request.form.get("nuki_token_loeschen"):
                conn.execute("UPDATE vereine SET nuki_token='', nuki_smartlock_id='', "
                             "nuki_smartlock_name='' WHERE id=?", (vid,))
                conn.commit()
                flash("Nuki-Zugang entfernt.", "erfolg")
            conn.close()
            return redirect(url_for("einstellungen.nuki"))
        if aktion == "tuer":
            sid = (request.form.get("smartlock_id") or "").strip()
            name = (request.form.get("smartlock_name") or "").strip()
            conn.execute("UPDATE vereine SET nuki_smartlock_id=?, nuki_smartlock_name=? WHERE id=?",
                         (sid, name, vid))
            conn.commit()
            conn.close()
            flash(f"Tür „{name or sid}“ ausgewählt – der Öffnen-Knopf steht berechtigten Mitgliedern "
                  "jetzt zur Verfügung.", "erfolg")
            return redirect(url_for("einstellungen.nuki"))
    conn.close()
    einst = get_einstellungen()
    tueren, test_meldung, test_ok = [], "", None
    if request.method == "POST" and request.form.get("aktion") == "test":
        try:
            tueren = nuki_api.smartlocks(einst)
            test_ok = bool(tueren)
            test_meldung = (f"✅ Verbunden – {len(tueren)} Tür(en) gefunden." if tueren
                            else "⚠️ Verbunden, aber keine Tür mit dem Nuki-Konto verknüpft.")
        except nuki_api.NukiFehler as e:
            test_ok, test_meldung = False, f"⚠️ {e}"
    return render_template("einstellungen/nuki.html", aktiv="einstellungen", einst=einst,
                           tueren=tueren, test_meldung=test_meldung, test_ok=test_ok,
                           token_gesetzt=bool((einst["nuki_token"] or "").strip()))


@bp.route("/design-vorschlag")
def design_vorschlag():
    e = get_einstellungen()
    logo_farben, vorschlaege = [], []
    gemischt = bool(request.args.get("mix"))
    if e["logo_dateiname"]:
        pfad = os.path.join(LOGO_DIR, e["logo_dateiname"])
        logo_farben = farben.logo_farben(pfad)
        vorschlaege = farben.mischung(logo_farben, 3) if gemischt else farben.vorschlaege(logo_farben)
    return render_template("einstellungen/design_vorschlag.html", aktiv="einstellungen",
                           e=e, logo_farben=logo_farben, vorschlaege=vorschlaege, gemischt=gemischt)


@bp.route("/design-anwenden", methods=["POST"])
def design_anwenden():
    primaer = request.form.get("primaer", "#2563eb").strip()
    sidebar = request.form.get("sidebar", "#0f172a").strip()
    if not _HEX.fullmatch(primaer):
        primaer = "#2563eb"
    if not _HEX.fullmatch(sidebar):
        sidebar = "#0f172a"
    conn = get_db()
    conn.execute("UPDATE vereine SET farbe_primaer=?, farbe_sidebar=? WHERE id=?",
                 (primaer, sidebar, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Design übernommen. 🎨", "erfolg")
    return redirect(url_for("einstellungen.design_vorschlag"))


@bp.route("/logo-kreis")
def logo_kreis():
    e = get_einstellungen()
    if not e["logo_dateiname"]:
        flash("Bitte zuerst ein Logo hochladen.", "fehler")
        return redirect(url_for("einstellungen.index"))
    return render_template("einstellungen/logo_kreis.html", aktiv="einstellungen", e=e)


@bp.route("/logo-kreis/speichern", methods=["POST"])
def logo_kreis_speichern():
    def zahl(feld, standard, lo, hi):
        v = request.form.get(feld, "")
        try:
            return max(lo, min(hi, int(float(v))))
        except (ValueError, TypeError):
            return standard
    conn = get_db()
    kreis = request.form.get("logo_kreis_farbe", "#ffffff").strip()
    conn.execute("UPDATE vereine SET logo_kreis_farbe=?, logo_zoom=?, logo_pos_x=?, logo_pos_y=? WHERE id=?",
                 (kreis if _HEX.fullmatch(kreis) else "#ffffff",
                  zahl("logo_zoom", 100, 20, 400),
                  zahl("logo_pos_x", 50, 0, 100),
                  zahl("logo_pos_y", 50, 0, 100),
                  aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Logo-Kreis gespeichert. 🔵", "erfolg")
    return redirect(url_for("einstellungen.logo_kreis"))


@bp.route("/rechtliches")
def rechtliches():
    """Öffentliche Impressum- & Datenschutz-Seite (aus den Vereinsdaten erzeugt)."""
    return render_template("einstellungen/rechtliches.html", e=get_einstellungen())


@bp.route("/logo/<int:verein_id>")
def logo(verein_id):
    """Liefert das Logo eines Vereins aus (für Anzeige in der Seitenleiste)."""
    conn = get_db()
    row = conn.execute("SELECT logo_dateiname FROM vereine WHERE id=?", (verein_id,)).fetchone()
    conn.close()
    if not row or not row["logo_dateiname"]:
        abort(404)
    name = row["logo_dateiname"]
    # ?klein=1: verkleinerte Fassung (max 96 px) für öffentliche Seiten –
    # das Original-Logo ist teils über 1 MB groß, angezeigt mit 46 px.
    if request.args.get("klein") and not name.lower().endswith(".svg"):
        thumb = f"thumb96_{name}.png"
        pfad = os.path.join(LOGO_DIR, thumb)
        original = os.path.join(LOGO_DIR, name)
        try:
            # mtime-Vergleich: nach einem Logo-Wechsel (gleicher Dateiname!)
            # muss das Thumb neu entstehen, sonst bliebe ewig das alte Bild.
            veraltet = os.path.getmtime(pfad) < os.path.getmtime(original)
        except OSError:
            veraltet = True
        if veraltet:
            try:
                from PIL import Image
                bild = Image.open(original)
                bild.load()
                bild.thumbnail((96, 96))
                if bild.mode not in ("RGB", "RGBA"):
                    bild = bild.convert("RGBA")
                # Farbprofil verwerfen: CMYK-Logos schleppen sonst ein
                # ~500-kB-ICC-Profil ins 96-px-Thumb (live beobachtet).
                bild.info.pop("icc_profile", None)
                # atomar schreiben: halbe Dateien dürfen nie liegen bleiben
                bild.save(pfad + ".neu", format="PNG", optimize=True)
                os.replace(pfad + ".neu", pfad)
            except Exception:  # noqa: BLE001 – defekte Datei: Original liefern
                thumb = name
        return _bild_ohne_skript(send_from_directory(LOGO_DIR, thumb, max_age=86400))
    return _bild_ohne_skript(send_from_directory(LOGO_DIR, name, max_age=86400))


def _bild_ohne_skript(antwort):
    """Logos dürfen SVG sein – dann aber ohne aktive Inhalte. Die strenge
    Richtlinie verhindert, dass ein SVG-Logo Skripte im Namen der App ausführt."""
    antwort.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    antwort.headers["X-Content-Type-Options"] = "nosniff"
    return antwort


# ------------------------ Abzeichen (Auszeichnungen) -----------------------
@bp.route("/abzeichen")
def abzeichen():
    """Abzeichen des Vereins verwalten (Standard, bis eigene gespeichert werden)."""
    from .. import abzeichen as abz
    conn = get_db()
    vid = aktiver_verein_id()
    defs = abz.liste(conn, vid)
    eigene = abz.eigene_vorhanden(conn, vid)
    conn.close()
    return render_template("einstellungen/abzeichen.html", aktiv="einstellungen",
                           defs=defs, eigene=eigene, kriterien=abz.KRITERIEN,
                           kriterium_namen=abz.KRITERIUM_NAMEN)


@bp.route("/abzeichen/speichern", methods=["POST"])
def abzeichen_speichern():
    """Neu anlegen, ändern, löschen oder auf Standard zurücksetzen."""
    from .. import abzeichen as abz
    conn = get_db()
    vid = aktiver_verein_id()
    aktion = request.form.get("aktion")
    if aktion == "reset":
        conn.execute("DELETE FROM abzeichen_defs WHERE verein_id=?", (vid,))
        conn.commit()
        conn.close()
        flash("Abzeichen auf den Standard zurückgesetzt.", "erfolg")
        return redirect(url_for("einstellungen.abzeichen"))

    # Vor jeder Änderung sicherstellen, dass die Standards als eigene Zeilen vorliegen
    abz.standards_einspielen(conn, vid)

    if aktion == "loeschen":
        did = request.form.get("id", type=int)
        conn.execute("DELETE FROM abzeichen_defs WHERE id=? AND verein_id=?", (did, vid))
        flash("Abzeichen gelöscht.", "erfolg")
    else:
        symbol = (request.form.get("symbol") or "🏅").strip()[:8] or "🏅"
        titel = (request.form.get("titel") or "").strip()
        kriterium = request.form.get("kriterium")
        if kriterium not in dict(abz.KRITERIEN):
            kriterium = "schichten"
        try:
            schwelle = float((request.form.get("schwelle") or "1").replace(",", "."))
        except ValueError:
            schwelle = 1
        if not titel:
            conn.close()
            flash("Bitte einen Titel für das Abzeichen angeben.", "fehler")
            return redirect(url_for("einstellungen.abzeichen"))
        did = request.form.get("id", type=int)
        if did:
            conn.execute("UPDATE abzeichen_defs SET symbol=?, titel=?, kriterium=?, schwelle=? "
                         "WHERE id=? AND verein_id=?", (symbol, titel, kriterium, schwelle, did, vid))
            flash("Abzeichen gespeichert.", "erfolg")
        else:
            import secrets as _secrets
            conn.execute("INSERT INTO abzeichen_defs (verein_id, schluessel, symbol, titel, kriterium, "
                         "schwelle, sortierung) VALUES (?,?,?,?,?,?,99)",
                         (vid, "eigen-" + _secrets.token_hex(4), symbol, titel, kriterium, schwelle))
            flash("Neues Abzeichen angelegt.", "erfolg")
    conn.commit()
    conn.close()
    return redirect(url_for("einstellungen.abzeichen"))
