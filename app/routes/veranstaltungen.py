"""
Modul „Veranstaltungen & Anmeldungen" (ein-/ausschaltbar über modul_veranstaltungen).

- Veranstaltungen mit Min-/Max-Teilnehmerzahl, Preis, Anmeldeschluss
- Öffentliche Selbst-Anmeldung über einen teilbaren Link (+ Admin-Erfassung)
- Externe Teilnehmende in eigenen Tabellen (Datenschutz), 1 Anmelder:in → mehrere Teilnehmende
- Kommunikation (Bestätigung/Voucher, AGB, Foto, Mitglied werden, Quittung …)
- Teilnehmendenlisten (Druck/Excel/Unterschriften), Online-Feedback, Auswertung
"""
import io
import csv
import secrets
from datetime import date, datetime, timedelta

from flask import (Blueprint, jsonify, render_template, request, redirect, url_for,
                   flash, abort, Response, session, make_response)

from flask_login import current_user

from .. import mail, xlsx
from ..db import (get_db, get_einstellungen, aktiver_verein_id, now_iso,
                  oeffentliche_suche)

bp = Blueprint("veranstaltungen", __name__, url_prefix="/veranstaltungen")

BEZAHLSTATUS = [("offen", "offen"), ("bezahlt", "bezahlt"), ("erlassen", "erlassen/frei")]

# Vorformulierte Textbausteine für die Teilnehmenden-Kommunikation (frei editierbar).
VORLAGEN = {
    "bestaetigung": ("Anmeldebestätigung – {veranstaltung}",
        "Hallo {anmelder},\n\nvielen Dank für deine Anmeldung zu „{veranstaltung}“ am {datum}{uhrzeit_teil} in {ort}.\n"
        "Angemeldete Teilnehmende: {teilnehmer}.\n{betrag_teil}\n{spende_teil}{bezahl_teil}{zahlcode_teil}"
        "Deinen Voucher / deine Teilnahmebestätigung findest du hier:\n{voucher_link}\n"
        "Termin in deinen Kalender übernehmen:\n{kalender_link}\n\n"
        "Du kannst doch nicht kommen? Hier kannst du deine Reservierung stornieren:\n{storno_link}\n\n"
        "Wir freuen uns auf dich!\n{vereinsname}"),
    "voucher": ("Dein Voucher – {veranstaltung}",
        "Hallo {anmelder},\n\nanbei dein Voucher für „{veranstaltung}“ am {datum}:\n{voucher_link}\n\n"
        "Bitte bring ihn (ausgedruckt oder auf dem Handy) zur Veranstaltung mit.\n\n{vereinsname}"),
    "agb": ("Teilnahmebedingungen – {veranstaltung}",
        "Hallo {anmelder},\n\nhier findest du die Teilnahmebedingungen für „{veranstaltung}“.\n\n"
        "[Bitte Teilnahmebedingungen hier einfügen]\n\n{vereinsname}"),
    "foto": ("Dürfen wir fotografieren? – {veranstaltung}",
        "Hallo {anmelder},\n\nbei „{veranstaltung}“ entstehen ggf. Fotos für unsere Öffentlichkeitsarbeit.\n"
        "Bitte gib uns kurz Bescheid, ob du/ihr damit einverstanden bist.\n\n{vereinsname}"),
    "mitglied": ("Lust, Mitglied zu werden? – {vereinsname}",
        "Hallo {anmelder},\n\nschön, dass du bei „{veranstaltung}“ dabei warst!\n"
        "Wenn dir gefällt, was wir tun: Als Mitglied unterstützt du uns dauerhaft und bist immer dabei.\n"
        "Melde dich einfach bei uns – wir freuen uns.\n\n{vereinsname}"),
    "infos": ("Dürfen wir dich auf dem Laufenden halten?",
        "Hallo {anmelder},\n\ndürfen wir dir künftig Infos zu Veranstaltungen und Neuigkeiten schicken?\n"
        "Antworte uns einfach kurz. Du kannst dem jederzeit widersprechen.\n\n{vereinsname}"),
    "quittung": ("Quittung – {veranstaltung}",
        "Hallo {anmelder},\n\nhiermit bestätigen wir den Erhalt von {betrag} € für „{veranstaltung}“.\n"
        "Eine ausführliche Quittung findest du hier:\n{voucher_link}\n\n{vereinsname}"),
    "zahlung": ("Zahlungserinnerung – {veranstaltung}",
        "Hallo {anmelder},\n\nfür deine Anmeldung zu „{veranstaltung}“ am {datum} ist noch ein Betrag von "
        "{betrag} € offen.\n\nBitte überweise ihn auf unser Konto:\n{kontoinhaber}\nIBAN: {iban}\n"
        "Verwendungszweck: {zahlcode} ({anmelder})\n\n{bezahl_teil}"
        "Schon überwiesen? Dann hat sich diese Nachricht überschnitten – danke!\n\n{vereinsname}"),
    "erinnerung": ("Erinnerung – {veranstaltung} am {datum}",
        "Hallo {anmelder},\n\nkurze Erinnerung: „{veranstaltung}“ findet am {datum}{uhrzeit_teil} in {ort} statt.\n"
        "Wir freuen uns auf dich!\n\n{vereinsname}"),
    "nachrueck_angebot": ("Platz frei – bitte bestätigen – {veranstaltung}",
        "Hallo {anmelder},\n\nfür „{veranstaltung}“ am {datum}{uhrzeit_teil} ist ein Platz frei "
        "geworden – und du bist als Nächste:r dran!\n"
        "Bitte bestätige deine Teilnahme bis {nachrueck_frist}:\n{nachrueck_link}\n\n"
        "Ohne Bestätigung geht der Platz danach an die nächste Person auf der Warteliste.\n\n"
        "{vereinsname}"),
    "nachrueck": ("Platz frei – du bist dabei! – {veranstaltung}",
        "Hallo {anmelder},\n\ngute Nachricht: Für „{veranstaltung}“ am {datum}{uhrzeit_teil} ist ein Platz "
        "frei geworden – du rückst von der Warteliste nach und bist jetzt fest dabei!\n"
        "{betrag_teil}\n{bezahl_teil}{zahlcode_teil}"
        "Deinen Voucher findest du hier:\n{voucher_link}\n\n"
        "Du kannst doch nicht? Hier kannst du stornieren:\n{storno_link}\n\n"
        "Bis bald!\n{vereinsname}"),
    "feedback": ("Wie war's? – Dein Feedback zu {veranstaltung}",
        "Hallo {anmelder},\n\ndanke, dass du bei „{veranstaltung}“ dabei warst!\n"
        "Mit zwei Klicks hilfst du uns sehr – dein Feedback:\n{feedback_link}\n\n{vereinsname}"),
    "absage": ("Absage – {veranstaltung}",
        "Hallo {anmelder},\n\nleider müssen wir „{veranstaltung}“ am {datum} absagen "
        "(die Mindestteilnehmerzahl wurde nicht erreicht). Bereits gezahlte Beiträge erstatten wir zurück.\n"
        "Entschuldige die Umstände.\n\n{vereinsname}"),
}

# Sprechende Namen für die Vorlagen-Übersicht
VORLAGEN_NAMEN = {
    "bestaetigung": "Anmeldebestätigung (wird automatisch versendet)",
    "voucher": "Voucher zusenden",
    "agb": "Teilnahmebedingungen",
    "foto": "Foto-Einverständnis",
    "mitglied": "Mitglieder-Werbung",
    "infos": "Info-Einwilligung",
    "quittung": "Quittung",
    "zahlung": "Zahlungserinnerung (offener Betrag)",
    "erinnerung": "Erinnerung vor der Veranstaltung (automatisch am Vortag)",
    "nachrueck_angebot": "Nachrück-Angebot mit Frist (automatisch, wenn eingestellt)",
    "nachrueck": "Wartelisten-Nachrücker (automatisch bei freiem Platz)",
    "feedback": "Feedback-Bitte",
    "absage": "Absage",
}

VORLAGEN_PLATZHALTER = ["anmelder", "veranstaltung", "datum", "uhrzeit_teil", "ort",
                        "kalender_link", "nachrueck_link", "nachrueck_frist", "warteliste_platz",
                        "teilnehmer", "betrag", "betrag_teil", "voucher_link",
                        "feedback_link", "storno_link", "iban", "bic", "kontoinhaber",
                        "bezahl_link", "bezahl_teil", "spende", "spende_teil", "zahlcode", "zahlcode_teil", "vereinsname"]


def vorlagen_laden(conn, vid):
    """Eingebaute Vorlagen (je Verein überlagert) plus selbst erstellte Vorlagen.

    Anpassungen liegen seit dem Umzug in der zentralen Vorlagen-Tabelle
    (Spalte schluessel, gepflegt unter Kommunikation → Vorlagen). Die
    eingebauten Standards bleiben hier im Code – so funktioniert der Versand
    auch ganz ohne Datenbank-Zeile und ohne Modul-Seed."""
    vorlagen = dict(VORLAGEN)
    try:
        for r in conn.execute("SELECT schluessel, betreff, inhalt FROM vorlagen "
                              "WHERE verein_id=? AND COALESCE(schluessel,'')!='' "
                              "ORDER BY id", (vid,)).fetchall():
            if r["betreff"] or r["inhalt"]:
                vorlagen[r["schluessel"]] = (r["betreff"], r["inhalt"])
    except Exception:  # noqa: BLE001
        pass
    return vorlagen


def vorlagen_namen_laden(conn, vid):
    """Anzeigenamen: eingebaute plus die Namen selbst erstellter Vorlagen."""
    namen = dict(VORLAGEN_NAMEN)
    try:
        for r in conn.execute("SELECT schluessel, name, betreff FROM vorlagen "
                              "WHERE verein_id=? AND COALESCE(schluessel,'')!=''",
                              (vid,)).fetchall():
            if r["schluessel"] not in VORLAGEN_NAMEN:
                namen[r["schluessel"]] = r["name"] or r["betreff"] or r["schluessel"]
    except Exception:  # noqa: BLE001
        pass
    return namen


def mail_gestalten(einst, empfaenger, betreff, text, signatur=True, anhaenge=None):
    """Versendet eine Veranstaltungs-Mail im Vereins-Design: Schriftart aus den
    Einstellungen, Vereins-Signatur mit Logo. Der Klartext bleibt als
    Alternative für Mailprogramme ohne HTML erhalten."""
    import html as _h
    from .kommunikation import mail_html_rahmen, _signatur_html
    sig = ""
    if signatur:
        try:
            sig = _signatur_html(einst)
        except Exception:  # noqa: BLE001 – Signatur darf den Versand nie stoppen
            pass
    text = (text or "").rstrip()
    if sig:
        # Die eingebauten Vorlagen schließen mit dem Vereinsnamen – der steht
        # gleich darunter fett im Signaturblock. Einmal reicht.
        zeilen = text.split("\n")
        if zeilen and zeilen[-1].strip() == (einst["vereinsname"] or "").strip() != "":
            text = "\n".join(zeilen[:-1]).rstrip()
    html = mail_html_rahmen(einst, _h.escape(text).replace("\n", "<br>") + sig)
    # Klartext-Alternative aus dem fertigen HTML – so trägt sie die Signatur mit
    return mail.einzelmail(einst, empfaenger, betreff, mail._html_zu_text(html),
                           html=html, anhaenge=anhaenge)


def _frist_de(iso):
    """'2026-08-31 18:30:00' → '31.08.2026, 18:30 Uhr' (für Mail-Platzhalter)."""
    try:
        return datetime.strptime(iso[:16].replace("T", " "), "%Y-%m-%d %H:%M") \
            .strftime("%d.%m.%Y, %H:%M Uhr")
    except (ValueError, TypeError):
        return iso or ""


def warteliste_platz(conn, a):
    """1-basierte Position auf der Warteliste – in der Reihenfolge, in der auch
    nachgerückt wird: laufende Angebote zählen nicht mit (die sind quasi durch),
    Verpasste stehen hinten."""
    mein_v = int(a["nachrueck_verpasst"] or 0) if "nachrueck_verpasst" in a.keys() else 0
    z = conn.execute(
        "SELECT COUNT(*) c FROM veranstaltung_anmeldung WHERE termin_id=? AND storniert=0 "
        "AND warteliste=1 AND COALESCE(nachrueck_bis,'') <= ? "
        "AND (COALESCE(nachrueck_verpasst,0) < ? OR (COALESCE(nachrueck_verpasst,0) = ? "
        "AND (erstellt_am < ? OR (erstellt_am = ? AND id <= ?))))",
        (a["termin_id"], now_iso(), mein_v, mein_v,
         a["erstellt_am"], a["erstellt_am"], a["id"])).fetchone()
    return int(z["c"] or 0)


def storno_deadline(t):
    """Bis wann storniert/geändert werden darf – None = bis zum Termin selbst."""
    frist = int(t["storno_frist_stunden"] or 0) if "storno_frist_stunden" in t.keys() else 0
    if not frist or not (t["uhrzeit"] or "").strip():
        return None
    try:
        beginn = datetime.strptime(f"{t['datum']} {t['uhrzeit'][:5]}", "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None
    return beginn - timedelta(hours=frist)


def storno_erlaubt(t):
    """Storno/Änderung noch möglich? (Frist + nicht vergangen)"""
    if (t["datum"] or "") < date.today().isoformat():
        return False
    deadline = storno_deadline(t)
    return deadline is None or datetime.now() <= deadline


def belegte_plaetze_je_termin(conn, tids):
    """Belegte Plätze (feste Teilnehmende + laufende Nachrück-Reservierungen)
    für VIELE Termine in zwei Abfragen – semantisch identisch zu
    _stand()["teilnehmer"] + reservierte_plaetze() je Termin, aber ohne N+1
    (gebaut für die öffentliche Adventskalender-Seite)."""
    tids = [t for t in tids]
    if not tids:
        return {}
    ph = ",".join("?" * len(tids))
    belegt = {}
    for extra, params in (
            ("a.warteliste=0", ()),
            ("a.warteliste=1 AND COALESCE(a.nachrueck_bis,'') > ?", (now_iso(),))):
        for z in conn.execute(
                f"SELECT a.termin_id, COUNT(t.id) n FROM veranstaltung_anmeldung a "
                f"JOIN veranstaltung_teilnehmer t ON t.anmeldung_id=a.id "
                f"WHERE a.termin_id IN ({ph}) AND a.storniert=0 AND {extra} "
                f"GROUP BY a.termin_id", (*tids, *params)).fetchall():
            belegt[z["termin_id"]] = belegt.get(z["termin_id"], 0) + int(z["n"] or 0)
    return belegt


def reservierte_plaetze(conn, tid):
    """Plätze, die gerade per Nachrück-Angebot (mit Frist) reserviert sind."""
    z = conn.execute(
        "SELECT COALESCE(SUM((SELECT COUNT(*) FROM veranstaltung_teilnehmer tn "
        "WHERE tn.anmeldung_id=a.id)), 0) n "
        "FROM veranstaltung_anmeldung a WHERE a.termin_id=? AND a.storniert=0 "
        "AND a.warteliste=1 AND COALESCE(a.nachrueck_bis,'') > ?",
        (tid, now_iso())).fetchone()
    return int(z["n"] or 0)


def gutschein_pruefen(conn, vid, tid, code):
    """(gutschein_row, fehlertext) – prüft Gültigkeit eines Codes fürs Formular."""
    code = (code or "").strip()
    if not code:
        return None, ""
    g = conn.execute(
        "SELECT * FROM veranstaltung_gutscheine WHERE verein_id=? AND code=? COLLATE NOCASE "
        "AND aktiv=1", (vid, code)).fetchone()
    if not g:
        return None, "Dieser Gutscheincode ist nicht gültig."
    if g["termin_id"] and g["termin_id"] != tid:
        return None, "Dieser Gutscheincode gilt für eine andere Veranstaltung."
    if g["max_nutzung"] and g["genutzt"] >= g["max_nutzung"]:
        return None, "Dieser Gutscheincode ist bereits aufgebraucht."
    return g, ""


def gutschein_anwenden(betrag, g):
    """Rabatt auf den Preisanteil (nie auf die Spende); nie unter 0."""
    if not g:
        return betrag
    if g["art"] == "frei":
        return 0.0
    if g["art"] == "prozent":
        return round(max(0.0, betrag * (1 - (g["wert"] or 0) / 100.0)), 2)
    return round(max(0.0, betrag - (g["wert"] or 0)), 2)


def _mail_protokollieren(conn, vid, tid, aid, email, betreff, anhaenge=0, automatisch=0, ok=1):
    """Versand im Protokoll festhalten (best effort, stört den Versand nie)."""
    try:
        conn.execute("INSERT INTO veranstaltung_mailprotokoll (verein_id, termin_id, anmeldung_id, "
                     "email, betreff, anhaenge, automatisch, ok, gesendet_am) VALUES (?,?,?,?,?,?,?,?,?)",
                     (vid, tid, aid, email, betreff, anhaenge, automatisch, 1 if ok else 0, now_iso()))
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
#  Hilfen
# --------------------------------------------------------------------------- #
def _modul_an():
    e = get_einstellungen()
    return bool(e and e["modul_veranstaltungen"])


def _basis_url():
    host = request.host
    schema = "http" if host.startswith(("localhost", "127.")) else "https"
    return f"{schema}://{host}"


def _termin(conn, tid, nur_veranstaltung=True):
    t = conn.execute("SELECT * FROM termine WHERE id=? AND verein_id=?",
                     (tid, aktiver_verein_id())).fetchone()
    if not t:
        return None
    if nur_veranstaltung and not t["anmeldung_aktiv"]:
        return None
    return t


def _termin_conn_per_token(token):
    """(conn, Termin) zum Anmelde-Token – vereinsübergreifend, siehe db.oeffentliche_suche."""
    return oeffentliche_suche(
        "SELECT * FROM termine WHERE anmeldung_token=? AND anmeldung_token!='' AND anmeldung_aktiv=1",
        token)


def _anmeldung_conn_per_token(token, extras=""):
    """(conn, Anmeldung) zum persönlichen Token – vereinsübergreifend; extras =
    zusätzliche Termin-Spalten (t.…) für die jeweilige Seite."""
    return oeffentliche_suche(
        "SELECT a.*" + (", " + extras if extras else "") +
        " FROM veranstaltung_anmeldung a JOIN termine t ON t.id=a.termin_id "
        "WHERE a.token=? AND a.token!=''", token)


def _stand(conn, tid):
    def z(sql, *p):
        return conn.execute(sql, p).fetchone()[0]
    teil = z("SELECT COUNT(*) FROM veranstaltung_teilnehmer t JOIN veranstaltung_anmeldung a ON a.id=t.anmeldung_id "
             "WHERE a.termin_id=? AND a.storniert=0 AND a.warteliste=0", tid)
    warte = z("SELECT COUNT(*) FROM veranstaltung_teilnehmer t JOIN veranstaltung_anmeldung a ON a.id=t.anmeldung_id "
              "WHERE a.termin_id=? AND a.storniert=0 AND a.warteliste=1", tid)
    anm = z("SELECT COUNT(*) FROM veranstaltung_anmeldung WHERE termin_id=? AND storniert=0", tid)
    bezahlt = z("SELECT COALESCE(SUM(betrag),0) FROM veranstaltung_anmeldung WHERE termin_id=? AND storniert=0 AND bezahlstatus='bezahlt'", tid)
    offen = z("SELECT COALESCE(SUM(betrag),0) FROM veranstaltung_anmeldung WHERE termin_id=? AND storniert=0 AND bezahlstatus='offen'", tid)
    spenden = z("SELECT COALESCE(SUM(spende),0) FROM veranstaltung_anmeldung WHERE termin_id=? AND storniert=0", tid)
    return {"teilnehmer": teil, "warteliste": warte, "anmeldungen": anm,
            "bezahlt": bezahlt, "offen": offen, "spenden": spenden}


def _anmeldung(conn, aid):
    return conn.execute(
        "SELECT a.*, t.titel, t.datum, t.uhrzeit, t.ort, t.preis FROM veranstaltung_anmeldung a "
        "JOIN termine t ON t.id=a.termin_id WHERE a.id=? AND a.verein_id=?",
        (aid, aktiver_verein_id())).fetchone()


def _teilnehmer(conn, aid):
    return conn.execute("SELECT * FROM veranstaltung_teilnehmer WHERE anmeldung_id=? ORDER BY id", (aid,)).fetchall()


def bezahl_link(einst, betrag):
    """PayPal.me-Link mit vorbefülltem Betrag; '' wenn nicht konfiguriert oder Betrag 0.

    Akzeptiert in den Einstellungen 'paypal.me/name', 'https://paypal.me/name' oder nur 'name'."""
    try:
        handle = (einst.get("paypal_me") if hasattr(einst, "get") else einst["paypal_me"]) or ""
    except (KeyError, IndexError):
        handle = ""
    handle = handle.strip().rstrip("/")
    if not handle or not betrag or betrag <= 0:
        return ""
    handle = handle.replace("https://", "").replace("http://", "").lstrip("@")
    if not handle.lower().startswith("paypal.me/"):
        handle = "paypal.me/" + handle
    return f"https://{handle}/{betrag:.2f}EUR"


def _platzhalter(einst, t, a, teilnehmer_namen, conn=None):
    betrag = a["betrag"] if a else (t["preis"] or 0)
    return {
        "{vereinsname}": einst["vereinsname"] or "",
        "{veranstaltung}": t["titel"] or "",
        "{datum}": _datum_de(t["datum"]),
        "{uhrzeit_teil}": (f" um {t['uhrzeit']} Uhr" if t["uhrzeit"] else ""),
        "{uhrzeit}": t["uhrzeit"] or "",
        "{ort}": t["ort"] or "",
        "{anmelder}": (a["anmelder_name"] if a else "") or "",
        "{teilnehmer}": teilnehmer_namen,
        "{betrag}": f"{betrag:.2f}".replace(".", ","),
        "{betrag_teil}": (f"Beitrag: {betrag:.2f} € ({a['bezahlstatus']}).".replace(".", ",", 1)
                          if a and betrag else ""),
        "{voucher_link}": (_basis_url() + url_for("veranstaltungen.voucher", token=a["token"]) if a and a["token"] else ""),
        "{feedback_link}": (_basis_url() + url_for("veranstaltungen.feedback_public", token=a["token"]) if a and a["token"] else ""),
        "{storno_link}": (_basis_url() + url_for("veranstaltungen.storno_public", token=a["token"]) if a and a["token"] else ""),
        "{kalender_link}": (_basis_url() + url_for("veranstaltungen.kalender_ics", token=a["token"]) if a and a["token"] else ""),
        "{nachrueck_link}": (_basis_url() + url_for("veranstaltungen.nachrueck_bestaetigen", token=a["token"]) if a and a["token"] else ""),
        "{nachrueck_frist}": (_frist_de(a["nachrueck_bis"]) if a and "nachrueck_bis" in a.keys() and a["nachrueck_bis"] else ""),
        "{warteliste_platz}": (str(warteliste_platz(conn, a))
                               if conn is not None and a and a["warteliste"] else ""),
        "{iban}": einst["bank_iban"] or "",
        "{bic}": einst["bank_bic"] or "",
        "{kontoinhaber}": einst["bank_kontoinhaber"] or einst["vereinsname"] or "",
        "{spende}": (f"{a['spende']:.2f}".replace(".", ",") if a and a["spende"] else ""),
        "{spende_teil}": (f"Darin enthalten: {a['spende']:.2f} € freiwillige Spende – herzlichen Dank! 💛\n".replace(".", ",", 1)
                          if a and (a["spende"] or 0) > 0 else ""),
        "{bezahl_link}": (bezahl_link(einst, betrag)
                          if a and a["bezahlstatus"] == "offen" else ""),
        "{bezahl_teil}": (f"Am einfachsten zahlst du online per PayPal: {bezahl_link(einst, betrag)}\n"
                          if a and a["bezahlstatus"] == "offen" and bezahl_link(einst, betrag) else ""),
        "{zahlcode}": ((a["zahlcode"] if "zahlcode" in a.keys() else "") or "") if a else "",
        "{zahlcode_teil}": (f"Bei Überweisung bitte als Verwendungszweck angeben: {a['zahlcode']}\n"
                            if a and a["bezahlstatus"] == "offen" and betrag
                            and "zahlcode" in a.keys() and (a["zahlcode"] or "") else ""),
    }


def _ersetze(text, ph):
    for k, v in ph.items():
        text = text.replace(k, str(v))
    return text


def _datum_de(w):
    if not w:
        return ""
    teile = str(w)[:10].split("-")
    return f"{teile[2]}.{teile[1]}.{teile[0]}" if len(teile) == 3 else w


def _qr(text):
    from ..qr import data_uri
    return data_uri(text)


# --------------------------------------------------------------------------- #
#  Modul-Schutz
# --------------------------------------------------------------------------- #
@bp.before_request
def _gate():
    # Öffentliche Endpunkte prüfen das Modul selbst (brauchen keinen aktiven Verein-Kontext im Menü)
    oeffentlich = request.endpoint in (
        "veranstaltungen.oeffentlich", "veranstaltungen.anmelden_public",
        "veranstaltungen.danke", "veranstaltungen.feedback_public", "veranstaltungen.voucher",
        "veranstaltungen.storno_public", "veranstaltungen.foto_public",
        "veranstaltungen.wallet_pass", "veranstaltungen.teilnahmebedingungen",
        "veranstaltungen.kalender_ics", "veranstaltungen.nachrueck_bestaetigen",
        "veranstaltungen.voucher_personen", "veranstaltungen.stand_public")
    if oeffentlich:
        return
    if not _modul_an():
        flash("Das Modul „Veranstaltungen“ ist nicht aktiviert (Einstellungen → Module).", "info")
        return redirect(url_for("dashboard.index"))


# --------------------------------------------------------------------------- #
#  Admin: Übersicht + Anlegen/Bearbeiten
# --------------------------------------------------------------------------- #
@bp.route("/")
def index():
    """Übersicht „Anmeldungen": alle Veranstaltungen mit aktiver Teilnehmenden-Anmeldung."""
    conn = get_db()
    vid = aktiver_verein_id()
    # Kommende zuerst (chronologisch), danach Vergangenes (jüngste zuerst)
    heute = date.today().isoformat()
    termine = conn.execute(
        "SELECT * FROM termine WHERE verein_id=? AND anmeldung_aktiv=1 "
        "ORDER BY (datum < ?), CASE WHEN datum >= ? THEN datum ELSE '' END, datum DESC, uhrzeit",
        (vid, heute, heute)).fetchall()
    liste = [{"t": t, "stand": _stand(conn, t["id"])} for t in termine]
    # Alle Anmelder:in- und Namen der Teilnehmenden je Veranstaltung – für die Namenssuche in der Übersicht
    namen = {}
    for r in conn.execute(
            "SELECT a.termin_id, a.anmelder_name AS n FROM veranstaltung_anmeldung a WHERE a.verein_id=? "
            "UNION SELECT a.termin_id, tn.name FROM veranstaltung_teilnehmer tn "
            "JOIN veranstaltung_anmeldung a ON a.id=tn.anmeldung_id WHERE a.verein_id=?",
            (vid, vid)).fetchall():
        if (r["n"] or "").strip():
            namen.setdefault(r["termin_id"], set()).add(r["n"].strip())
    namen = {tid: sorted(werte) for tid, werte in namen.items()}
    conn.close()
    return render_template("veranstaltungen/index.html", aktiv="veranstaltungen", liste=liste, namen=namen)


@bp.route("/neu", methods=["GET", "POST"])
def neu():
    # Anlegen erfolgt im einheitlichen Editor (Kalender → Neue Veranstaltung).
    return redirect(url_for("termine.neu"))


@bp.route("/<int:tid>/bearbeiten", methods=["GET", "POST"])
def bearbeiten(tid):
    # Bearbeiten erfolgt im einheitlichen Termin-Editor.
    return redirect(url_for("termine.bearbeiten", tid=tid))


# --------------------------------------------------------------------------- #
#  Teilnehmenden-Verwaltung (Detail-Hub; Event-Editor liegt in termine/kalender)
# --------------------------------------------------------------------------- #
@bp.route("/<int:tid>")
def detail(tid):
    conn = get_db()
    t = _termin(conn, tid)
    if not t:
        conn.close()
        flash("Veranstaltung nicht gefunden.", "fehler")
        return redirect(url_for("veranstaltungen.index"))
    anmeldungen = conn.execute(
        "SELECT * FROM veranstaltung_anmeldung WHERE termin_id=? ORDER BY storniert, warteliste, erstellt_am", (tid,)).fetchall()
    einlass_da, einlass_gesamt = _einlass_zaehler(conn, tid)
    teilnehmer = {}
    for a in anmeldungen:
        teilnehmer[a["id"]] = _teilnehmer(conn, a["id"])
    stand = _stand(conn, tid)
    vorlagen = vorlagen_laden(conn, aktiver_verein_id())
    # Wem wurde schon etwas geschickt? (Anzahl + letzter Betreff je Anmeldung)
    mails = {}
    try:
        for r in conn.execute(
                "SELECT anmeldung_id, COUNT(*) n, MAX(gesendet_am) wann FROM veranstaltung_mailprotokoll "
                "WHERE termin_id=? GROUP BY anmeldung_id", (tid,)).fetchall():
            mails[r["anmeldung_id"]] = {"n": r["n"], "wann": r["wann"]}
        for r in conn.execute(
                "SELECT p.anmeldung_id, p.betreff FROM veranstaltung_mailprotokoll p "
                "WHERE p.termin_id=? ORDER BY p.gesendet_am", (tid,)).fetchall():
            if r["anmeldung_id"] in mails:
                mails[r["anmeldung_id"]]["letzte"] = r["betreff"]
    except Exception:  # noqa: BLE001
        pass
    conn.close()
    # Zahlungs-Mahnliste: offene Beträge, deren Anmeldung schon >= 7 Tage zurückliegt
    ueberfaellig = {}
    for a in anmeldungen:
        if (not a["storniert"] and a["bezahlstatus"] == "offen"
                and (a["betrag"] or 0) > 0 and (a["erstellt_am"] or "")):
            try:
                alter = (date.today() - datetime.strptime(a["erstellt_am"][:10], "%Y-%m-%d").date()).days
                if alter >= 7:
                    ueberfaellig[a["id"]] = alter
            except ValueError:
                pass
    link = _basis_url() + url_for("veranstaltungen.oeffentlich", token=t["anmeldung_token"]) if t["anmeldung_token"] else ""
    return render_template("veranstaltungen/detail.html", aktiv="veranstaltungen",
                           t=t, anmeldungen=anmeldungen, teilnehmer=teilnehmer, stand=stand,
                           link=link, bezahlstatus=BEZAHLSTATUS, vorlagen=vorlagen, mails=mails,
                           ueberfaellig=ueberfaellig,
                           einlass_da=einlass_da, einlass_gesamt=einlass_gesamt)


def _anmeldung_aus_form(conn, tid, aid=None, herkunft="admin"):
    """Legt eine Anmeldung + Teilnehmende aus dem Formular an oder aktualisiert sie."""
    vid = aktiver_verein_id()
    f = request.form
    felder = dict(
        anmelder_name=(f.get("anmelder_name") or "").strip(),
        strasse=(f.get("strasse") or "").strip(),
        plz=(f.get("plz") or "").strip(),
        ort=(f.get("ort") or "").strip(),
        telefon=(f.get("telefon") or "").strip(),
        email=(f.get("email") or "").strip(),
        quelle=(f.get("quelle") or "").strip(),
        foto_status=f.get("foto_status") if f.get("foto_status") in ("ja", "nein") else "",
        foto_social=f.get("foto_social") if f.get("foto_social") in ("ja", "nein") else "",
        infos_ok=1 if f.get("infos_ok") else 0,
        agb_ok=1 if f.get("agb_ok") else 0,
        mitglied_interesse=1 if f.get("mitglied_interesse") else 0,
        notizen=(f.get("notizen") or "").strip(),
    )
    felder["foto_ok"] = 1 if felder["foto_status"] == "ja" else 0
    if felder["foto_status"]:
        felder["foto_antwort_am"] = now_iso()
    # Unterschriebenes Papierformular als Nachweis mit ablegen (optional)
    nachweis = request.files.get("foto_nachweis_datei")
    if nachweis and nachweis.filename:
        import os as _os
        import time as _time
        from .. import speicher
        basis = _os.path.basename(nachweis.filename).replace("/", "_")
        dateiname = f"{_time.strftime('%Y%m%d-%H%M%S')}_{basis}"
        speicher.speichern(get_einstellungen(), "fotoerlaubnis", dateiname, nachweis.read())
        felder["foto_nachweis"] = dateiname
    if herkunft == "admin":
        felder["bezahlstatus"] = f.get("bezahlstatus") if f.get("bezahlstatus") in ("offen", "bezahlt", "erlassen") else "offen"
        felder["betrag"] = _float(f.get("betrag"))
        felder["spende"] = _float(f.get("spende"))
    # Namen der Teilnehmenden (mehrzeilig, einer pro Zeile) ODER Einzelfelder teilnehmer[]
    namen = [n.strip() for n in f.getlist("teilnehmer") if n.strip()]
    if not namen:
        namen = [z.strip() for z in (f.get("teilnehmer_liste") or "").splitlines() if z.strip()]
    if not namen and felder["anmelder_name"]:
        # Wie im öffentlichen Formular: ohne Namensliste zählt die anmeldende
        # Person selbst – sonst wäre die Gruppe am Einlass nie abhakbar.
        namen = [felder["anmelder_name"]]
    alte_teilnehmer = {}
    if aid is None:
        token = secrets.token_urlsafe(9)
        spalten = list(felder) + ["verein_id", "termin_id", "token", "herkunft", "erstellt_am"]
        werte = list(felder.values()) + [vid, tid, token, herkunft, now_iso()]
        cur = conn.execute(
            f"INSERT INTO veranstaltung_anmeldung ({','.join(spalten)}) VALUES ({','.join(['?']*len(spalten))})", werte)
        aid = cur.lastrowid
        from .. import zahlungsabgleich as za
        za.code_setzen(conn, aid, vid)
    else:
        setz = ", ".join(f"{k}=?" for k in felder)
        conn.execute(f"UPDATE veranstaltung_anmeldung SET {setz} WHERE id=? AND verein_id=?",
                     list(felder.values()) + [aid, vid])
        # Check-in-Stand merken: Beim Neuschreiben der Namensliste dürfen die
        # Einlass-Zeiten nicht verloren gehen (Namensabgleich)
        alte_teilnehmer = {r["name"]: r for r in conn.execute(
            "SELECT * FROM veranstaltung_teilnehmer WHERE anmeldung_id=?", (aid,)).fetchall()}
        conn.execute("DELETE FROM veranstaltung_teilnehmer WHERE anmeldung_id=?", (aid,))
    for i, name in enumerate(namen):
        gd = f.getlist("teilnehmer_geb")
        geb = gd[i].strip() if i < len(gd) else ""
        alt_z = alte_teilnehmer.get(name)
        if alt_z and not geb:
            geb = alt_z["geburtsdatum"] or ""
        conn.execute("INSERT INTO veranstaltung_teilnehmer (anmeldung_id, name, geburtsdatum, "
                     "checkin_am, checkin_von) VALUES (?,?,?,?,?)",
                     (aid, name, geb,
                      (alt_z["checkin_am"] if alt_z else "") or "",
                      (alt_z["checkin_von"] if alt_z else "") or ""))
    return aid


def _float(w):
    try:
        return float((w or "0").replace(",", ".").strip() or 0)
    except ValueError:
        return 0.0


@bp.route("/<int:tid>/anmeldung/neu", methods=["GET", "POST"])
def anmeldung_neu(tid):
    conn = get_db()
    t = _termin(conn, tid)
    if not t:
        conn.close()
        return redirect(url_for("veranstaltungen.index"))
    if request.method == "POST":
        _anmeldung_aus_form(conn, tid, herkunft="admin")
        conn.commit()
        conn.close()
        flash("Anmeldung erfasst.", "erfolg")
        return redirect(url_for("veranstaltungen.detail", tid=tid))
    conn.close()
    return render_template("veranstaltungen/anmeldung_formular.html", aktiv="veranstaltungen", t=t, a=None, teilnehmer=[])


@bp.route("/anmeldung/<int:aid>/bearbeiten", methods=["GET", "POST"])
def anmeldung_bearbeiten(aid):
    conn = get_db()
    a = _anmeldung(conn, aid)
    if not a:
        conn.close()
        return redirect(url_for("veranstaltungen.index"))
    if request.method == "POST":
        _anmeldung_aus_form(conn, a["termin_id"], aid=aid, herkunft="admin")
        conn.commit()
        conn.close()
        flash("Anmeldung aktualisiert.", "erfolg")
        return redirect(url_for("veranstaltungen.detail", tid=a["termin_id"]))
    teilnehmer = _teilnehmer(conn, aid)
    t = conn.execute("SELECT * FROM termine WHERE id=?", (a["termin_id"],)).fetchone()
    conn.close()
    return render_template("veranstaltungen/anmeldung_formular.html", aktiv="veranstaltungen", t=t, a=a, teilnehmer=teilnehmer)


@bp.route("/anmeldung/<int:aid>/zahlung", methods=["POST"])
def anmeldung_zahlung(aid):
    conn = get_db()
    a = _anmeldung(conn, aid)
    if a:
        status = request.form.get("bezahlstatus") if request.form.get("bezahlstatus") in ("offen", "bezahlt", "erlassen") else a["bezahlstatus"]
        bezahlt_am = now_iso()[:10] if status == "bezahlt" and not a["bezahlt_am"] else (a["bezahlt_am"] if status == "bezahlt" else "")
        conn.execute("UPDATE veranstaltung_anmeldung SET bezahlstatus=?, betrag=?, zahlart=?, bezahlt_am=? WHERE id=?",
                     (status, _float(request.form.get("betrag")), (request.form.get("zahlart") or "").strip(), bezahlt_am, aid))
        conn.commit()
        flash("Zahlung aktualisiert.", "erfolg")
    tid = a["termin_id"] if a else None
    conn.close()
    return redirect(url_for("veranstaltungen.detail", tid=tid))


@bp.route("/anmeldung/<int:aid>/storno", methods=["POST"])
def anmeldung_storno(aid):
    conn = get_db()
    a = _anmeldung(conn, aid)
    if a:
        neu_storniert = 0 if a["storniert"] else 1
        conn.execute("UPDATE veranstaltung_anmeldung SET storniert=? WHERE id=?", (neu_storniert, aid))
        if neu_storniert:
            n = nachruecken(conn, a["termin_id"])
            if n:
                flash(f"⤴️ {n} Anmeldung(en) von der Warteliste nachgerückt – Info-Mail ist raus.", "info")
        conn.commit()
    tid = a["termin_id"] if a else None
    conn.close()
    return redirect(url_for("veranstaltungen.detail", tid=tid))


def nachruecken(conn, tid):
    """Rückt nach einem Storno Wartelisten-Anmeldungen nach, solange Platz ist.

    Der Reihe nach (älteste zuerst); jede nachgerückte Person bekommt eine Mail
    (Vorlage „nachrueck"). Gibt die Anzahl nachgerückter Anmeldungen zurück."""
    t = conn.execute("SELECT * FROM termine WHERE id=?", (tid,)).fetchone()
    if not t or not t["max_teilnehmer"]:
        return 0
    if (t["datum"] or "") < date.today().isoformat():
        return 0        # vorbei – niemand rückt mehr nach, keine Angebote mehr
    einst = _verein_einst(conn, t["verein_id"])
    vorlagen = vorlagen_laden(conn, t["verein_id"])
    modus = (t["nachrueck_modus"] or "auto") if "nachrueck_modus" in t.keys() else "auto"
    nachgerueckt = 0
    while True:
        stand = _stand(conn, tid)
        frei = t["max_teilnehmer"] - stand["teilnehmer"] - reservierte_plaetze(conn, tid)
        if frei <= 0:
            break
        kandidat = None
        # Im Angebots-Modus werden Personen, die ein Angebot verstreichen ließen,
        # übersprungen – sie bleiben aber auf der Liste (Vorstand sieht sie weiter)
        verpasst_filter = "AND COALESCE(a.nachrueck_verpasst,0)=0 " if modus == "bestaetigen" else ""
        for w in conn.execute(
                "SELECT a.*, (SELECT COUNT(*) FROM veranstaltung_teilnehmer tn WHERE tn.anmeldung_id=a.id) plaetze "
                "FROM veranstaltung_anmeldung a WHERE a.termin_id=? AND a.storniert=0 AND a.warteliste=1 "
                "AND COALESCE(a.nachrueck_bis,'') <= ? " + verpasst_filter +
                "ORDER BY COALESCE(a.nachrueck_verpasst,0), a.erstellt_am, a.id",
                (tid, now_iso())).fetchall():
            if (w["plaetze"] or 1) <= frei:
                kandidat = w
                break
        if not kandidat:
            break
        if modus == "bestaetigen" and (kandidat["email"] or "").strip():
            # Angebot mit 24-Stunden-Frist: Platz wird reserviert, nicht fest vergeben
            frist = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE veranstaltung_anmeldung SET nachrueck_bis=? WHERE id=?",
                         (frist, kandidat["id"]))
            conn.commit()          # erst festschreiben, dann Mail – kein Doppelversand im Cron
            kandidat = _anmeldung_neu_laden(conn, kandidat["id"])
            nachgerueckt += 1
            try:
                namen = ", ".join(tn["name"] for tn in _teilnehmer(conn, kandidat["id"]))
                ph = _platzhalter(einst, t, kandidat, namen, conn=conn)
                betreff, text = vorlagen["nachrueck_angebot"]
                ok, _f = mail_gestalten(einst, kandidat["email"], _ersetze(betreff, ph), _ersetze(text, ph))
                _mail_protokollieren(conn, t["verein_id"], tid, kandidat["id"], kandidat["email"],
                                     _ersetze(betreff, ph), automatisch=1, ok=ok)
            except Exception:  # noqa: BLE001 – Mail darf das Nachrücken nie blockieren
                pass
            continue
        conn.execute("UPDATE veranstaltung_anmeldung SET warteliste=0, nachrueck_bis='' "
                     "WHERE id=?", (kandidat["id"],))
        if (kandidat["gutschein_code"] or "").strip():
            # Der Gutschein wird erst beim festen Platz verbraucht (Warteliste zählt nicht)
            conn.execute("UPDATE veranstaltung_gutscheine SET genutzt=genutzt+1 "
                         "WHERE verein_id=? AND code=? COLLATE NOCASE",
                         (t["verein_id"], kandidat["gutschein_code"]))
        conn.commit()          # erst festschreiben, dann Mail
        nachgerueckt += 1
        if kandidat["email"] and einst:
            try:
                namen = ", ".join(tn["name"] for tn in _teilnehmer(conn, kandidat["id"]))
                ph = _platzhalter(einst, t, kandidat, namen)
                betreff, text = vorlagen["nachrueck"]
                ok, _f = mail_gestalten(einst, kandidat["email"], _ersetze(betreff, ph), _ersetze(text, ph))
                _mail_protokollieren(conn, t["verein_id"], tid, kandidat["id"], kandidat["email"],
                                     _ersetze(betreff, ph), automatisch=1, ok=ok)
            except Exception:  # noqa: BLE001 – Mail darf das Nachrücken nie blockieren
                pass
    return nachgerueckt


def _anmeldung_neu_laden(conn, aid):
    return conn.execute("SELECT * FROM veranstaltung_anmeldung WHERE id=?", (aid,)).fetchone()


def nachrueck_verfall(conn, tid=None):
    """Abgelaufene Nachrück-Angebote einsammeln und die nächste Person anbieten.

    Läuft im stündlichen Cron (alle Vereine) und nach Bedarf je Termin."""
    frage = ("SELECT DISTINCT a.termin_id FROM veranstaltung_anmeldung a "
             "WHERE a.warteliste=1 AND a.storniert=0 AND COALESCE(a.nachrueck_bis,'')!='' "
             "AND a.nachrueck_bis <= ?")
    params = [now_iso()]
    if tid:
        frage += " AND a.termin_id=?"
        params.append(tid)
    betroffen = [z["termin_id"] for z in conn.execute(frage, params).fetchall()]
    for t_id in betroffen:
        conn.execute("UPDATE veranstaltung_anmeldung SET nachrueck_bis='', nachrueck_verpasst=1 "
                     "WHERE termin_id=? AND warteliste=1 AND storniert=0 "
                     "AND COALESCE(nachrueck_bis,'')!='' AND nachrueck_bis <= ?",
                     (t_id, now_iso()))
        nachruecken(conn, t_id)
    return len(betroffen)


@bp.route("/anmeldung/<int:aid>/zahlungserinnerung", methods=["POST"])
def anmeldung_zahlungserinnerung(aid):
    """Personalisierte Zahlungserinnerung an eine einzelne Anmeldung schicken."""
    conn = get_db()
    a = _anmeldung(conn, aid)
    if not a:
        conn.close()
        flash("Anmeldung nicht gefunden.", "fehler")
        return redirect(url_for("veranstaltungen.index"))
    if not a["email"]:
        conn.close()
        flash("Diese Anmeldung hat keine E-Mail-Adresse – bitte anders erinnern.", "fehler")
        return redirect(url_for("veranstaltungen.detail", tid=a["termin_id"]))
    t = conn.execute("SELECT * FROM termine WHERE id=?", (a["termin_id"],)).fetchone()
    einst = _verein_einst(conn, a["verein_id"])
    vorlagen = vorlagen_laden(conn, a["verein_id"])
    namen = ", ".join(tn["name"] for tn in _teilnehmer(conn, a["id"]))
    ph = _platzhalter(einst, t, a, namen)
    betreff, text = vorlagen["zahlung"]
    ok, meldung = mail_gestalten(einst, a["email"], _ersetze(betreff, ph), _ersetze(text, ph))
    _mail_protokollieren(conn, a["verein_id"], t["id"], aid, a["email"], _ersetze(betreff, ph), ok=ok)
    conn.commit()
    conn.close()
    if ok:
        flash(f"💶 Zahlungserinnerung an {a['anmelder_name']} ({a['email']}) versendet.", "erfolg")
    else:
        flash("Versand fehlgeschlagen: " + (meldung or "Unbekannter Fehler"), "fehler")
    return redirect(url_for("veranstaltungen.detail", tid=a["termin_id"]))


@bp.route("/anmeldung/<int:aid>/warteliste", methods=["POST"])
def anmeldung_warteliste(aid):
    conn = get_db()
    a = _anmeldung(conn, aid)
    if a:
        conn.execute("UPDATE veranstaltung_anmeldung SET warteliste=? WHERE id=?", (0 if a["warteliste"] else 1, aid))
        conn.commit()
    tid = a["termin_id"] if a else None
    conn.close()
    return redirect(url_for("veranstaltungen.detail", tid=tid))


@bp.route("/anmeldung/<int:aid>/loeschen", methods=["POST"])
def anmeldung_loeschen(aid):
    conn = get_db()
    a = _anmeldung(conn, aid)
    if a:
        conn.execute("DELETE FROM veranstaltung_anmeldung WHERE id=? AND verein_id=?", (aid, aktiver_verein_id()))
        n = nachruecken(conn, a["termin_id"])
        if n:
            flash(f"⤴️ {n} Anmeldung(en) von der Warteliste nachgerückt – Info-Mail ist raus.", "info")
        conn.commit()
        flash("Anmeldung gelöscht.", "erfolg")
    tid = a["termin_id"] if a else None
    conn.close()
    return redirect(url_for("veranstaltungen.detail", tid=tid))


# --------------------------------------------------------------------------- #
#  Öffentliche Anmeldung (ohne Login)
# --------------------------------------------------------------------------- #
@bp.route("/anmelden/<token>")
def oeffentlich(token):
    from flask_login import current_user
    conn, t = _termin_conn_per_token(token)
    modul = t and _verein_modul_an(conn, t["verein_id"])
    stand = _stand(conn, t["id"]) if t else None
    einst = _verein_einst(conn, t["verein_id"]) if t else None
    # Eingeloggt? Dann die bekannten Angaben vorbefüllen (Mitglied oder Freund:in).
    vb = {}
    if t and getattr(current_user, "is_authenticated", False):
        vb = {"anmelder_name": current_user.anzeigename or "", "email": current_user.email or ""}
        # Mitgliedsdaten nur aus dem eigenen Verein ziehen – conn kann bei einem
        # geteilten Link zur Datenbank eines anderen Vereins zeigen.
        if getattr(current_user, "mitglied_id", None) and \
                getattr(current_user, "verein_id", None) in (None, t["verein_id"]):
            m = conn.execute("SELECT vorname, nachname, email, telefon, mobil, strasse, plz, ort "
                             "FROM mitglieder WHERE id=?", (current_user.mitglied_id,)).fetchone()
            if m:
                vb = {"anmelder_name": (f"{m['vorname'] or ''} {m['nachname'] or ''}").strip() or vb["anmelder_name"],
                      "email": m["email"] or vb["email"],
                      "telefon": m["mobil"] or m["telefon"] or "",
                      "strasse": m["strasse"] or "", "plz": m["plz"] or "", "ort": m["ort"] or ""}
        vb["teilnehmer_liste"] = vb.get("anmelder_name", "")
    ist_mitglied = bool(t and getattr(current_user, "is_authenticated", False)
                        and getattr(current_user, "mitglied_id", None)
                        and getattr(current_user, "rolle", "") != "extern"
                        and getattr(current_user, "verein_id", None) in (None, t["verein_id"]))
    einlass_laeuft = einlass_auto_schliessen(conn, t) if t else ""
    reserviert = reservierte_plaetze(conn, t["id"]) if t else 0
    schwelle = einlass_schwelle(t) if t else None
    einlass_zu = bool(t and not einlass_laeuft and schwelle
                      and t["datum"] == date.today().isoformat()
                      and datetime.now() >= schwelle)
    conn.close()
    if not t or not modul:
        return render_template("veranstaltungen/public_fehler.html", meldung="Diese Anmeldeseite ist nicht verfügbar."), 404
    geschlossen = _anmeldung_geschlossen(t, stand)
    return render_template("veranstaltungen/public_anmelden.html", t=t, stand=stand, einst=einst,
                           geschlossen=geschlossen, vb=vb, einlass_laeuft=einlass_laeuft,
                           einlass_zu=einlass_zu, ist_mitglied=ist_mitglied,
                           reserviert=reserviert)


@bp.route("/teilnahmebedingungen")
def teilnahmebedingungen():
    """Öffentliche Teilnahmebedingungen für Veranstaltungen (aus Vereinsdaten erzeugt).

    Mit ?token= (Anmelde-Token der Veranstaltung) kommen die Angaben vom Verein
    der Veranstaltung – ohne Token vom aktiven Verein (Session/Systemstandard)."""
    einst = None
    conn, t = _termin_conn_per_token(request.args.get("token"))
    if t and _verein_modul_an(conn, t["verein_id"]):
        einst = _verein_einst(conn, t["verein_id"])
    conn.close()
    return render_template("veranstaltungen/public_bedingungen.html", einst=einst or get_einstellungen())


@bp.route("/stand/<token>")
def stand_public(token):
    """Anonymisierter Anmeldestand für Kooperationspartner:innen und Sponsor:innen.

    Öffentlich über den Anmelde-Token erreichbar, aber nur bei Veranstaltungen
    mit eingetragener Unterstützung (sponsor_name/sponsor_logo) – dort steht der
    Link auch auf der Anmeldeseite. Zeigt ausschließlich Zahlen: keine Namen,
    keine Kontaktdaten und bewusst auch keine Beträge. Lebt und stirbt mit der
    öffentlichen Anmeldung (anmeldung_aktiv), wie alle Token-Seiten."""
    conn, t = _termin_conn_per_token(token)
    if not t or not _verein_modul_an(conn, t["verein_id"]) \
            or not (t["sponsor_name"] or t["sponsor_logo"]):
        conn.close()
        return render_template("veranstaltungen/public_fehler.html",
                               meldung="Diese Seite ist nicht verfügbar."), 404
    stand = _stand(conn, t["id"])
    reserviert = reservierte_plaetze(conn, t["id"])
    einst = _verein_einst(conn, t["verein_id"])
    conn.close()
    # Nur die vier benötigten Felder ins Template – der volle vereine-Datensatz
    # (SMTP-Zugang & Co.) hat auf einer öffentlichen Seite nichts verloren.
    einst = {k: einst[k] for k in ("id", "vereinsname", "logo_dateiname", "farbe_primaer")}
    belegt = stand["teilnehmer"] + reserviert       # wie die Restplatz-Logik der Anmeldeseite
    frei = max(t["max_teilnehmer"] - belegt, 0) if t["max_teilnehmer"] else None
    antwort = make_response(render_template(
        "veranstaltungen/public_stand.html", t=t, einst=einst, belegt=belegt, frei=frei,
        # Nachrück-Reservierte stecken schon in „Angemeldet" – nicht doppelt zählen
        warteliste=max(stand["warteliste"] - reserviert, 0),
        geschlossen=_anmeldung_geschlossen(t, stand),
        jetzt=datetime.now().strftime("%d.%m.%Y, %H:%M")))
    # Sprachfassungen (Accept-Language) dürfen sich im geteilten Cache nie mischen
    antwort.headers.add("Vary", "Accept-Language")
    # Öffentlich kurz cachen – aber NUR eine cookielose, sprach-neutrale Antwort,
    # deren Rendern die Session nicht angefasst hat (sonst steckt ein frisches
    # Set-Cookie in einer public-Antwort: Session-Leck über geteilte Caches).
    if not request.cookies and "lang" not in request.args and not session.modified:
        antwort.headers["Cache-Control"] = "public, max-age=60"
    else:
        antwort.headers["Cache-Control"] = "no-store"
    return antwort


@bp.route("/anmelden/<token>", methods=["POST"], endpoint="anmelden_public")
def anmelden_public(token):
    conn, t = _termin_conn_per_token(token)
    if not t or not _verein_modul_an(conn, t["verein_id"]):
        conn.close()
        abort(404)
    stand = _stand(conn, t["id"])
    if _anmeldung_geschlossen(t, stand):
        conn.close()
        from ..uebersetzung import ue as _ue
        flash(_ue("Die Anmeldung ist leider geschlossen."), "fehler")
        return redirect(url_for("veranstaltungen.oeffentlich", token=token))
    # Honeypot gegen Bots
    if (request.form.get("website") or "").strip():
        conn.close()
        abort(400)
    f = request.form
    namen = [z.strip() for z in (f.get("teilnehmer_liste") or "").splitlines() if z.strip()]
    if not namen and (f.get("anmelder_name") or "").strip():
        namen = [f.get("anmelder_name").strip()]
    if not (f.get("anmelder_name") or "").strip() or not namen:
        conn.close()
        from ..uebersetzung import ue as _ue
        flash(_ue("Bitte Name und mindestens eine teilnehmende Person angeben."), "fehler")
        return redirect(url_for("veranstaltungen.oeffentlich", token=token))
    # Kapazität: passt die Gruppe noch rein? sonst Warteliste.
    # Plätze mit laufendem Nachrück-Angebot zählen als (noch) belegt.
    # Schreibsperre: Zwei gleichzeitige Anmeldungen dürfen den letzten Platz
    # nicht doppelt vergeben (Muster wie mailvorlage_speichern).
    conn.execute("BEGIN IMMEDIATE")
    stand = _stand(conn, t["id"])
    warte = 0
    if t["max_teilnehmer"] and stand["teilnehmer"] + reservierte_plaetze(conn, t["id"]) \
            + len(namen) > t["max_teilnehmer"]:
        warte = 1
    # Freiwillige Zusatzspende: nur volle 5er-Schritte, 0–500 € (Formular-Stepper + Server-Klammer)
    spende = max(0.0, min(500.0, _float(f.get("spende"))))
    spende = round(spende / 5) * 5
    # Mitgliederpreis: gilt automatisch für angemeldete Mitglieder des Vereins
    ist_mitglied = bool(getattr(current_user, "is_authenticated", False)
                        and getattr(current_user, "mitglied_id", None)
                        and getattr(current_user, "rolle", "") != "extern"
                        and getattr(current_user, "verein_id", None) in (None, t["verein_id"]))
    einzelpreis = t["preis"] or 0
    if ist_mitglied and "preis_mitglied" in t.keys() and t["preis_mitglied"] is not None:
        einzelpreis = t["preis_mitglied"]
    preis_gesamt = einzelpreis * len(namen)
    # Gutschein-/Freikartencode (nur auf den Preisanteil, nie auf die Spende)
    gutschein, g_fehler = gutschein_pruefen(conn, t["verein_id"], t["id"],
                                            f.get("gutschein_code"))
    if g_fehler:
        conn.close()
        flash(g_fehler, "fehler")
        return redirect(url_for("veranstaltungen.oeffentlich", token=token))
    preis_gesamt = gutschein_anwenden(preis_gesamt, gutschein)
    token_a = secrets.token_urlsafe(9)
    cur = conn.execute(
        "INSERT INTO veranstaltung_anmeldung (verein_id, termin_id, anmelder_name, strasse, plz, ort, telefon, email, "
        "quelle, foto_ok, foto_status, foto_social, foto_antwort_am, "
        "infos_ok, agb_ok, mitglied_interesse, stern_interesse, betrag, einzelpreis, spende, bezahlstatus, warteliste, token, herkunft, erstellt_am) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (t["verein_id"], t["id"], f.get("anmelder_name").strip(), (f.get("strasse") or "").strip(),
         (f.get("plz") or "").strip(), (f.get("ort") or "").strip(), (f.get("telefon") or "").strip(),
         (f.get("email") or "").strip(), (f.get("quelle") or "").strip(),
         1 if f.get("foto_ok") else 0,
         # Gesetztes Häkchen = Ja; KEIN Häkchen = nicht beantwortet (nicht: Nein)
         "ja" if f.get("foto_ok") else "",
         "ja" if (f.get("foto_ok") and f.get("foto_social")) else "",
         now_iso() if f.get("foto_ok") else "",
         1 if f.get("infos_ok") else 0, 1 if f.get("agb_ok") else 0,
         1 if f.get("mitglied_interesse") else 0, 1 if f.get("stern_interesse") else 0,
         preis_gesamt + spende, einzelpreis, spende,
         "bezahlt" if preis_gesamt + spende == 0 else "offen",
         warte, token_a, "online", now_iso()))
    aid = cur.lastrowid
    from .. import zahlungsabgleich as za
    za.code_setzen(conn, aid, t["verein_id"])
    if gutschein:
        conn.execute("UPDATE veranstaltung_anmeldung SET gutschein_code=? WHERE id=?",
                     (gutschein["code"], aid))
        if not warte:
            # Atomar: das Kontingent kann durch die Sperre oben nicht überbucht
            # werden; auf der Warteliste wird erst beim Nachrücken verbraucht
            conn.execute("UPDATE veranstaltung_gutscheine SET genutzt=genutzt+1 WHERE id=?",
                         (gutschein["id"],))
    for name in namen:
        conn.execute("INSERT INTO veranstaltung_teilnehmer (anmeldung_id, name) VALUES (?,?)", (aid, name))
    conn.commit()
    # Bestätigungs-E-Mail (best effort)
    einst = _verein_einst(conn, t["verein_id"])
    a = conn.execute("SELECT * FROM veranstaltung_anmeldung WHERE id=?", (aid,)).fetchone()
    vorlagen = vorlagen_laden(conn, t["verein_id"])
    if a["email"] and einst:
        ph = _platzhalter(einst, t, a, ", ".join(namen), conn=conn)
        betreff, text = vorlagen["bestaetigung"]
        try:
            erfolg, _f = mail_gestalten(einst, a["email"], _ersetze(betreff, ph), _ersetze(text, ph))
            _mail_protokollieren(conn, t["verein_id"], t["id"], aid, a["email"],
                                 _ersetze(betreff, ph), automatisch=1, ok=erfolg)
            conn.commit()
        except Exception:  # noqa: BLE001
            pass
    conn.close()
    return redirect(url_for("veranstaltungen.danke", token=token, a=token_a, w=warte))


@bp.route("/anmelden/<token>/danke")
def danke(token):
    conn, t = _termin_conn_per_token(token)
    einst = _verein_einst(conn, t["verein_id"]) if t else None
    a = None
    platz = 0
    at = request.args.get("a")
    if t and at:
        a = conn.execute("SELECT * FROM veranstaltung_anmeldung WHERE termin_id=? AND token=?", (t["id"], at)).fetchone()
        if a is not None and a["warteliste"] and not a["storniert"]:
            platz = warteliste_platz(conn, a)
    conn.close()
    if not t:
        abort(404)
    zahl_link = bezahl_link(einst, a["betrag"]) if (a and a["bezahlstatus"] == "offen") else ""
    return render_template("veranstaltungen/public_danke.html", t=t, einst=einst, a=a, platz=platz,
                           zahl_link=zahl_link, warte=request.args.get("w") == "1")


def _anmeldung_geschlossen(t, stand):
    try:
        if t["abgesagt"] or t["nur_mitglieder"]:
            return True
    except (KeyError, IndexError):
        pass
    if t["anmeldeschluss"]:
        if t["anmeldeschluss"] < date.today().isoformat():
            return True
    return False


def _verein_modul_an(conn, vid):
    r = conn.execute("SELECT modul_veranstaltungen FROM vereine WHERE id=?", (vid,)).fetchone()
    return bool(r and r["modul_veranstaltungen"])


def _verein_einst(conn, vid):
    from . import einstellungen as _e  # nur für LOGO/Absender nicht nötig; get_einstellungen ist aktiv-bezogen
    r = conn.execute("SELECT * FROM vereine WHERE id=?", (vid,)).fetchone()
    if not r:
        return None
    from .. import krypto
    e = dict(r)
    for feld in ("smtp_passwort",):
        if feld in e and e[feld]:
            try:
                e[feld] = krypto.entschluesseln(e[feld])
            except Exception:  # noqa: BLE001
                pass
    return e


# --------------------------------------------------------------------------- #
#  Kommunikation an Teilnehmende
# --------------------------------------------------------------------------- #
@bp.route("/<int:tid>/mail", methods=["GET", "POST"])
def mailen(tid):
    conn = get_db()
    t = _termin(conn, tid)
    if not t:
        conn.close()
        return redirect(url_for("veranstaltungen.index"))
    einst = get_einstellungen()
    vid = aktiver_verein_id()
    anmeldungen = conn.execute(
        "SELECT * FROM veranstaltung_anmeldung WHERE termin_id=? AND storniert=0 AND email!='' "
        "ORDER BY anmelder_name", (tid,)).fetchall()
    if request.method == "POST":
        betreff = request.form.get("betreff", "")
        inhalt = request.form.get("inhalt", "")
        mit_signatur = bool(request.form.get("signatur"))
        # Anhänge: Upload + Vereinsunterlagen + Dateiablage (wie beim Rundmail-Versand)
        from .kommunikation import _anhaenge_sammeln
        anhaenge, _bytes = _anhaenge_sammeln(conn, einst, vid)
        ids = request.form.getlist("empfaenger")
        ok, fehler = 0, 0
        for a in anmeldungen:
            if str(a["id"]) not in ids:
                continue
            namen = ", ".join(x["name"] for x in _teilnehmer(conn, a["id"]))
            ph = _platzhalter(einst, t, a, namen, conn=conn)
            erfolg, _ = mail_gestalten(einst, a["email"], _ersetze(betreff, ph), _ersetze(inhalt, ph),
                                       signatur=mit_signatur, anhaenge=anhaenge)
            _mail_protokollieren(conn, vid, tid, a["id"], a["email"], _ersetze(betreff, ph),
                                 anhaenge=len(anhaenge), ok=erfolg)
            ok += 1 if erfolg else 0
            fehler += 0 if erfolg else 1
        conn.commit()
        conn.close()
        zusatz = f" · 📎 {len(anhaenge)} Anhang/Anhänge" if anhaenge else ""
        flash(f"{ok} E-Mail(s) gesendet{', ' + str(fehler) + ' fehlgeschlagen' if fehler else ''}{zusatz}.",
              "erfolg" if not fehler else "fehler")
        return redirect(url_for("veranstaltungen.mailen", tid=tid))
    vorlagen = vorlagen_laden(conn, vid)
    namen = vorlagen_namen_laden(conn, vid)
    dokumente = conn.execute("SELECT id, titel, original_name FROM vereinsdokumente WHERE verein_id=? "
                             "ORDER BY titel", (vid,)).fetchall()
    # Versand-Protokoll: was ging schon an wen?
    protokoll = conn.execute(
        "SELECT p.*, a.anmelder_name FROM veranstaltung_mailprotokoll p "
        "LEFT JOIN veranstaltung_anmeldung a ON a.id=p.anmeldung_id "
        "WHERE p.termin_id=? ORDER BY p.gesendet_am DESC, p.id DESC LIMIT 200", (tid,)).fetchall()
    gesendet = {}   # anmeldung_id -> {"n": Anzahl, "letzte": Betreff, "wann": Datum}
    for p in protokoll:
        e = gesendet.setdefault(p["anmeldung_id"], {"n": 0, "letzte": p["betreff"], "wann": p["gesendet_am"]})
        e["n"] += 1
    conn.close()
    from .. import dateiablage
    dms_an = bool(einst and "modul_dms" in einst.keys() and einst["modul_dms"])
    dms_dateien = dateiablage.alle_dateien(vid) if dms_an else []
    return render_template("veranstaltungen/mail.html", aktiv="veranstaltungen", t=t,
                           anmeldungen=anmeldungen, vorlagen=vorlagen, namen=namen,
                           signatur_text=einst["signatur_text"] or "",
                           signatur_aktiv=bool(einst["signatur_aktiv"]),
                           dokumente=dokumente, dms_dateien=dms_dateien,
                           protokoll=protokoll, gesendet=gesendet)


# --------------------------------------------------------------------------- #
#  E-Mail-Vorlagen: Übersicht + Bearbeiten (je Verein gespeichert)
# --------------------------------------------------------------------------- #
@bp.route("/mailvorlagen")
def mailvorlagen():
    """Alter Ort – die Vorlagen werden jetzt zentral unter Kommunikation gepflegt."""
    return redirect(url_for("kommunikation.vorlagen_veranstaltungen"))


@bp.route("/mailvorlagen/voucher", methods=["POST"])
def voucher_texte_speichern():
    """Editierbare Textbausteine des Vouchers (Einlasshinweis + Fußzeile)."""
    conn = get_db()
    conn.execute("UPDATE vereine SET voucher_einlass_text=?, voucher_fusszeile=? WHERE id=?",
                 ((request.form.get("voucher_einlass_text") or "").strip(),
                  (request.form.get("voucher_fusszeile") or "").strip(), aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Voucher-Texte gespeichert. 🎟️", "erfolg")
    return redirect(url_for("kommunikation.vorlagen_veranstaltungen") + "#voucher")


@bp.route("/mailvorlagen/neu", methods=["POST"])
def mailvorlage_neu():
    """Eigene, zusätzliche Vorlage anlegen."""
    conn = get_db()
    vid = aktiver_verein_id()
    name = (request.form.get("name") or "").strip()
    betreff = (request.form.get("betreff") or "").strip()
    text = (request.form.get("text") or "").strip()
    if not name or not betreff or not text:
        conn.close()
        flash("Bitte Name, Betreff und Text für die neue Vorlage angeben.", "fehler")
        return redirect(url_for("kommunikation.vorlagen_veranstaltungen"))
    schluessel = "eigen-" + secrets.token_hex(4)
    conn.execute("INSERT INTO vorlagen (verein_id, schluessel, name, betreff, inhalt, art, kategorie, "
                 "erstellt_am) VALUES (?,?,?,?,?,?,?,?)",
                 (vid, schluessel, name, betreff, text, "extern", "Veranstaltungen", now_iso()))
    conn.commit()
    conn.close()
    flash(f"Vorlage „{name}“ angelegt – sie steht ab sofort beim Versand zur Auswahl.", "erfolg")
    return redirect(url_for("kommunikation.vorlagen_veranstaltungen"))


@bp.route("/mailvorlagen/<schluessel>", methods=["POST"])
def mailvorlage_speichern(schluessel):
    conn = get_db()
    vid = aktiver_verein_id()
    # Schreibsperre VOR dem Nachschauen: Zwei gleichzeitige Speichern-Klicks
    # dürfen keine zweite Zeile desselben Schlüssels anlegen (kein UNIQUE mehr).
    conn.execute("BEGIN IMMEDIATE")
    vorhanden = conn.execute("SELECT id FROM vorlagen WHERE verein_id=? AND schluessel=?",
                             (vid, schluessel)).fetchone()
    if schluessel not in VORLAGEN and not vorhanden:
        conn.close()
        abort(404)
    namen = vorlagen_namen_laden(conn, vid)
    if request.form.get("aktion") == "reset":
        # Eingebaute Vorlage: Zeile weg = Standard aus dem Code gilt wieder.
        # Eigene Vorlage: Zeile weg = Vorlage gelöscht.
        conn.execute("DELETE FROM vorlagen WHERE verein_id=? AND schluessel=?",
                     (vid, schluessel))
        if schluessel in VORLAGEN:
            flash(f"„{namen.get(schluessel, schluessel)}“ auf Standard zurückgesetzt.", "erfolg")
        else:
            flash(f"Eigene Vorlage „{namen.get(schluessel, schluessel)}“ gelöscht.", "erfolg")
    else:
        betreff = (request.form.get("betreff") or "").strip()
        text = (request.form.get("text") or "").strip()
        if not betreff or not text:
            conn.close()
            flash("Bitte Betreff und Text angeben (oder zurücksetzen/löschen nutzen).", "fehler")
            return redirect(url_for("kommunikation.vorlagen_veranstaltungen"))
        name = (request.form.get("name") or "").strip()
        if vorhanden:
            conn.execute("UPDATE vorlagen SET betreff=?, inhalt=?, "
                         "name=CASE WHEN ?!='' THEN ? ELSE name END WHERE id=?",
                         (betreff, text, name, name, vorhanden["id"]))
        else:
            conn.execute("INSERT INTO vorlagen (verein_id, schluessel, name, betreff, inhalt, "
                         "art, kategorie, erstellt_am) VALUES (?,?,?,?,?,?,?,?)",
                         (vid, schluessel, name, betreff, text,
                          "extern", "Veranstaltungen", now_iso()))
        flash(f"Vorlage „{name or namen.get(schluessel, schluessel)}“ gespeichert.", "erfolg")
    conn.commit()
    conn.close()
    return redirect(url_for("kommunikation.vorlagen_veranstaltungen"))


# --------------------------------------------------------------------------- #
#  Belege: Voucher (öffentlich per Token) + Quittung
# --------------------------------------------------------------------------- #
@bp.route("/voucher/<token>")
def voucher(token):
    conn, a = _anmeldung_conn_per_token(
        token, "t.titel, t.datum, t.uhrzeit, t.uhrzeit_bis, t.ort AS veranstaltungsort, t.bild, "
               "t.ort_hinweise, t.sponsor_name, t.sponsor_logo, t.sponsor_url, "
               "t.storno_frist_stunden, t.max_teilnehmer")
    # Modulprüfung wie bei allen öffentlichen Veranstaltungs-Seiten (wallet_pass,
    # anmelden_public …): Ohne Veranstaltungs-Modul gibt es auch keinen Voucher.
    if not a or not _verein_modul_an(conn, a["verein_id"]):
        conn.close()
        abort(404)
    einst = _verein_einst(conn, a["verein_id"])
    teilnehmer = _teilnehmer(conn, a["id"])
    platz = warteliste_platz(conn, a) if (a["warteliste"] and not a["storniert"]) else 0
    conn.close()
    qr = _qr(_basis_url() + url_for("veranstaltungen.voucher", token=token))
    zahl_link = bezahl_link(einst, a["betrag"]) if (not a["storniert"] and a["bezahlstatus"] == "offen") else ""
    from .. import wallet_pass as wp
    angebot = ((a["nachrueck_bis"] or "") if "nachrueck_bis" in a.keys() else "")
    return render_template("veranstaltungen/voucher.html", a=a, einst=einst, teilnehmer=teilnehmer,
                           qr=qr, zahl_link=zahl_link, wallet_da=wp.verfuegbar(),
                           platz=platz, aenderbar=(not a["storniert"] and storno_erlaubt(a)),
                           angebot_frist=_frist_de(angebot) if angebot and angebot > now_iso() else "")


@bp.route("/wallet/<token>")
def wallet_pass(token):
    """Voucher als Apple-Wallet-Karte (.pkpass) – nur mit hinterlegten Zertifikaten."""
    from .. import wallet_pass as wp
    if not wp.verfuegbar():
        abort(404)
    conn, a = _anmeldung_conn_per_token(token, "t.titel, t.datum, t.uhrzeit, t.ort AS veranstaltungsort")
    if not a or not _verein_modul_an(conn, a["verein_id"]):
        conn.close()
        abort(404)
    einst = _verein_einst(conn, a["verein_id"])
    conn.close()
    url = _basis_url() + url_for("veranstaltungen.voucher", token=token)
    try:
        daten = wp.erzeuge(einst, a, url)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("wallet").exception("Wallet-Pass fehlgeschlagen")
        flash("Die Wallet-Karte konnte nicht erzeugt werden.", "fehler")
        return redirect(url_for("veranstaltungen.voucher", token=token))
    return Response(daten, mimetype="application/vnd.apple.pkpass",
                    headers={"Content-Disposition": f"attachment; filename=Ticket-{a['id']}.pkpass"})


@bp.route("/storno/<token>", methods=["GET", "POST"])
def storno_public(token):
    """Öffentliches Stornieren einer Anmeldung über den persönlichen Link (ohne Konto)."""
    conn, a = _anmeldung_conn_per_token(token, "t.titel, t.datum, t.uhrzeit, t.ort AS veranstaltungsort")
    if not a or not _verein_modul_an(conn, a["verein_id"]):
        conn.close()
        abort(404)
    einst = _verein_einst(conn, a["verein_id"])
    vergangen = (a["datum"] or "") < date.today().isoformat()
    t_frist = conn.execute("SELECT * FROM termine WHERE id=?", (a["termin_id"],)).fetchone()
    frist_vorbei = t_frist is not None and not storno_erlaubt(t_frist)
    if request.method == "POST" and not a["storniert"] and not frist_vorbei:
        conn.execute("UPDATE veranstaltung_anmeldung SET storniert=1 WHERE id=?", (a["id"],))
        if (a["gutschein_code"] or "").strip() and not a["warteliste"]:
            conn.execute("UPDATE veranstaltung_gutscheine SET genutzt=MAX(0, genutzt-1) "
                         "WHERE verein_id=? AND code=? COLLATE NOCASE",
                         (a["verein_id"], a["gutschein_code"]))
        nachruecken(conn, a["termin_id"])
        conn.commit()
        # Verein per Mail informieren (best effort), damit die Planung aktuell bleibt
        try:
            empf = (einst["email"] or einst["smtp_absender_email"] or "") if einst else ""
            if empf:
                mail.einzelmail(einst, empf,
                                f"Storno: {a['anmelder_name']} – {a['titel']} am {_datum_de(a['datum'])}",
                                f"{a['anmelder_name']} hat die Reservierung für „{a['titel']}“ "
                                f"am {_datum_de(a['datum'])} soeben online storniert.\n"
                                f"E-Mail: {a['email'] or '–'}")
        except Exception:  # noqa: BLE001
            pass
        a = conn.execute(
            "SELECT a.*, t.titel, t.datum, t.uhrzeit, t.ort AS veranstaltungsort FROM veranstaltung_anmeldung a "
            "JOIN termine t ON t.id=a.termin_id WHERE a.id=?", (a["id"],)).fetchone()
    teilnehmer = _teilnehmer(conn, a["id"])
    conn.close()
    return render_template("veranstaltungen/public_storno.html", a=a, einst=einst,
                           teilnehmer=teilnehmer, vergangen=vergangen,
                           frist_vorbei=frist_vorbei and not vergangen,
                           frist_bis=_frist_de(storno_deadline(t_frist).strftime("%Y-%m-%d %H:%M"))
                           if t_frist is not None and storno_deadline(t_frist) else "")


@bp.route("/anmeldung/<int:aid>/quittung")
def quittung(aid):
    conn = get_db()
    a = _anmeldung(conn, aid)
    if not a:
        conn.close()
        abort(404)
    einst = get_einstellungen()
    teilnehmer = _teilnehmer(conn, aid)
    conn.close()
    return render_template("veranstaltungen/quittung.html", a=a, einst=einst, teilnehmer=teilnehmer, heute=_datum_de(now_iso()))


# --------------------------------------------------------------------------- #
#  Teilnehmendenlisten (Druck / Unterschriften / Excel)
# --------------------------------------------------------------------------- #
def _teilnehmer_zeilen(conn, tid):
    """Flache Liste: pro Person eine Zeile mit Angaben der anmeldenden Person."""
    rows = conn.execute(
        "SELECT a.*, tn.name AS teilnehmer_name, tn.geburtsdatum, tn.checkin_am "
        "FROM veranstaltung_anmeldung a "
        "JOIN veranstaltung_teilnehmer tn ON tn.anmeldung_id=a.id "
        "WHERE a.termin_id=? AND a.storniert=0 ORDER BY a.warteliste, a.anmelder_name, tn.id", (tid,)).fetchall()
    return rows


@bp.route("/<int:tid>/liste")
def liste_druck(tid):
    conn = get_db()
    t = _termin(conn, tid)
    if not t:
        conn.close()
        abort(404)
    zeilen = _teilnehmer_zeilen(conn, tid)
    einst = get_einstellungen()
    conn.close()
    return render_template("veranstaltungen/liste_druck.html", t=t, zeilen=zeilen, einst=einst,
                           unterschriften=False)


@bp.route("/<int:tid>/unterschriften")
def unterschriften(tid):
    conn = get_db()
    t = _termin(conn, tid)
    if not t:
        conn.close()
        abort(404)
    zeilen = _teilnehmer_zeilen(conn, tid)
    einst = get_einstellungen()
    conn.close()
    return render_template("veranstaltungen/liste_druck.html", t=t, zeilen=zeilen, einst=einst,
                           unterschriften=True)


@bp.route("/<int:tid>/export.xlsx")
def export_xlsx(tid):
    conn = get_db()
    t = _termin(conn, tid)
    if not t:
        conn.close()
        abort(404)
    zeilen = _teilnehmer_zeilen(conn, tid)
    conn.close()
    kopf = ["Teilnehmende", "Geburtsdatum", "Anmelder:in", "Straße", "PLZ", "Ort", "Telefon", "E-Mail",
            "Quelle", "Fotos (Verein)", "Fotos (Social Media)", "Infos ok", "Mitglied-Interesse",
            "Bezahlstatus", "Betrag (€)", "Warteliste", "Erschienen", "Notizen"]
    daten = []
    for r in zeilen:
        daten.append([r["teilnehmer_name"], r["geburtsdatum"], r["anmelder_name"], r["strasse"], r["plz"], r["ort"],
                      r["telefon"], r["email"], r["quelle"],
                      r["foto_status"] or "unbeantwortet", r["foto_social"] or "unbeantwortet",
                      "ja" if r["infos_ok"] else "",
                      "ja" if r["mitglied_interesse"] else "", r["bezahlstatus"], float(r["betrag"] or 0),
                      "ja" if r["warteliste"] else "",
                      (r["checkin_am"] or "")[:16].replace("T", " "), r["notizen"]])
    inhalt = xlsx.schreibe_xlsx(kopf, daten, blattname="Teilnehmende")
    return Response(inhalt, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=teilnehmer_{tid}.xlsx"})


# --------------------------------------------------------------------------- #
#  Feedback (öffentlich per Token) + Auswertung
# --------------------------------------------------------------------------- #
@bp.route("/feedback/<token>", methods=["GET", "POST"])
def feedback_public(token):
    conn, a = _anmeldung_conn_per_token(token, "t.titel, t.datum, t.feedback_aktiv")
    if not a or not a["feedback_aktiv"] or not _verein_modul_an(conn, a["verein_id"]):
        conn.close()
        return render_template("veranstaltungen/public_fehler.html", meldung="Kein Feedback möglich."), 404
    einst = _verein_einst(conn, a["verein_id"])
    if request.method == "POST":
        conn.execute(
            "INSERT INTO veranstaltung_feedback (verein_id, termin_id, anmeldung_id, gesamt, organisation, inhalt, weiterempfehlung, text, erstellt_am) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (a["verein_id"], a["termin_id"], a["id"], request.form.get("gesamt", type=int) or 0,
             request.form.get("organisation", type=int) or 0, request.form.get("inhalt", type=int) or 0,
             request.form.get("weiterempfehlung", type=int) or 0, (request.form.get("text") or "").strip(), now_iso()))
        conn.commit()
        conn.close()
        return render_template("veranstaltungen/public_danke.html", t={"titel": a["titel"]}, einst=einst, feedback=True)
    conn.close()
    return render_template("veranstaltungen/public_feedback.html", a=a, einst=einst)


@bp.route("/<int:tid>/feedback")
def feedback_auswertung(tid):
    conn = get_db()
    t = _termin(conn, tid)
    if not t:
        conn.close()
        abort(404)
    fb = conn.execute("SELECT * FROM veranstaltung_feedback WHERE termin_id=? ORDER BY erstellt_am DESC", (tid,)).fetchall()
    conn.close()

    def schnitt(feld):
        werte = [r[feld] for r in fb if r[feld]]
        return round(sum(werte) / len(werte), 1) if werte else None
    stats = {"anzahl": len(fb), "gesamt": schnitt("gesamt"), "organisation": schnitt("organisation"),
             "inhalt": schnitt("inhalt"), "nps": schnitt("weiterempfehlung")}
    return render_template("veranstaltungen/feedback.html", aktiv="veranstaltungen", t=t, fb=fb, stats=stats)


# --------------------------------------------------------------------------- #
#  Auswertung (übergreifend)
# --------------------------------------------------------------------------- #
@bp.route("/auswertung")
def auswertung():
    conn = get_db()
    vid = aktiver_verein_id()
    termine = conn.execute(
        "SELECT * FROM termine WHERE verein_id=? AND anmeldung_aktiv=1 ORDER BY datum DESC", (vid,)).fetchall()
    zeilen = []
    for t in termine:
        s = _stand(conn, t["id"])
        auslastung = round(s["teilnehmer"] / t["max_teilnehmer"] * 100) if t["max_teilnehmer"] else None
        zeilen.append({"t": t, "stand": s, "auslastung": auslastung,
                       "min_erreicht": (s["teilnehmer"] >= t["min_teilnehmer"]) if t["min_teilnehmer"] else None})
    # Quellen-Verteilung (woher kommen die Kontakte)
    quellen = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(quelle),''),'(ohne Angabe)') q, COUNT(*) c FROM veranstaltung_anmeldung "
        "WHERE verein_id=? AND storniert=0 GROUP BY q ORDER BY c DESC", (vid,)).fetchall()
    summen = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN bezahlstatus='bezahlt' THEN betrag ELSE 0 END),0) bezahlt, "
        "COALESCE(SUM(CASE WHEN bezahlstatus='offen' THEN betrag ELSE 0 END),0) offen, "
        "COALESCE(SUM(spende),0) spenden, "
        "COALESCE(SUM(CASE WHEN mitglied_interesse=1 THEN 1 ELSE 0 END),0) interesse, "
        "COALESCE(SUM(CASE WHEN foto_status='ja' THEN 1 ELSE 0 END),0) foto, "
        "COALESCE(SUM(CASE WHEN foto_status='nein' THEN 1 ELSE 0 END),0) foto_nein, "
        "COALESCE(SUM(CASE WHEN COALESCE(foto_status,'')='' THEN 1 ELSE 0 END),0) foto_offen, "
        "COUNT(*) gesamt "
        "FROM veranstaltung_anmeldung WHERE verein_id=? AND storniert=0", (vid,)).fetchone()
    conn.close()
    return render_template("veranstaltungen/auswertung.html", aktiv="veranstaltungen",
                           zeilen=zeilen, quellen=quellen, summen=summen)


# ------------------------- Fotoerlaubnis ----------------------------------
#
# Drei Zustände je Anmeldung: '' (nie geantwortet), 'ja', 'nein' – getrennt für
# die Öffentlichkeitsarbeit des Vereins (foto_status) und für Facebook/Instagram
# (foto_social). „Kein Häkchen" heißt eben NICHT „hat abgelehnt".

def _foto_sync(conn, aid, status, social):
    """Schreibt beide Einwilligungen und hält das alte foto_ok als Spiegel aktuell."""
    conn.execute("UPDATE veranstaltung_anmeldung SET foto_status=?, foto_social=?, "
                 "foto_ok=?, foto_antwort_am=? WHERE id=?",
                 (status, social, 1 if status == "ja" else 0, now_iso(), aid))


@bp.route("/<int:tid>/foto-anfragen", methods=["POST"])
def foto_anfragen(tid):
    """Fragt alle, die noch nicht geantwortet haben, per E-Mail nach der Fotoerlaubnis."""
    from .. import mail as _mail, mailhtml
    conn = get_db()
    vid = aktiver_verein_id()
    t = conn.execute("SELECT * FROM termine WHERE id=? AND verein_id=?", (tid, vid)).fetchone()
    if not t:
        conn.close()
        abort(404)
    einst = get_einstellungen()
    host = request.host_url.rstrip("/")
    offene = conn.execute(
        "SELECT * FROM veranstaltung_anmeldung WHERE termin_id=? AND verein_id=? AND storniert=0 "
        "AND COALESCE(foto_status,'')='' ", (tid, vid)).fetchall()
    gesendet, ohne_mail, fehlgeschlagen = 0, 0, 0
    for a in offene:
        adresse = (a["email"] or "").strip()
        if not adresse:
            ohne_mail += 1
            continue
        link = host + url_for("veranstaltungen.foto_public", token=a["token"])
        betreff, html = mailhtml.foto_anfrage(einst, host, a["anmelder_name"] or "du",
                                              t["titel"], t["datum"], link)
        try:
            erfolg, _f = _mail.einzelmail(einst, adresse, betreff,
                                          _mail._html_zu_text(html), html=html)
        except Exception:  # noqa: BLE001
            erfolg = False
        if erfolg:
            conn.execute("UPDATE veranstaltung_anmeldung SET foto_angefragt_am=? WHERE id=?",
                         (now_iso(), a["id"]))
            gesendet += 1
        else:
            fehlgeschlagen += 1
    conn.commit()
    conn.close()
    teile = []
    if gesendet:
        teile.append(f"{gesendet} Anfrage(n) verschickt")
    if fehlgeschlagen:
        teile.append(f"{fehlgeschlagen} konnten NICHT zugestellt werden")
    if ohne_mail:
        teile.append(f"{ohne_mail} ohne E-Mail-Adresse – dafür gibt es die Druckvorlage")
    flash(". ".join(teile) + "." if teile else
          "Alle Angemeldeten haben bereits geantwortet – niemand zu fragen.",
          "fehler" if fehlgeschlagen else "erfolg")
    return redirect(url_for("veranstaltungen.detail", tid=tid))


@bp.route("/foto/<token>", methods=["GET", "POST"])
def foto_public(token):
    """Öffentliche Seite: Fotoerlaubnis erteilen, ablehnen oder später widerrufen."""
    conn, a = _anmeldung_conn_per_token(token, "t.titel, t.datum, t.uhrzeit")
    if not a or not _verein_modul_an(conn, a["verein_id"]):
        conn.close()
        abort(404)
    einst = _verein_einst(conn, a["verein_id"])
    if request.method == "POST":
        status = request.form.get("foto") if request.form.get("foto") in ("ja", "nein") else ""
        social = request.form.get("social") if request.form.get("social") in ("ja", "nein") else ""
        if status:
            # Ohne Ja zu Fotos ist auch kein Ja zu Social Media möglich
            if status == "nein":
                social = "nein"
            _foto_sync(conn, a["id"], status, social or "nein")
            conn.commit()
            conn.close()
            flash("Danke! Deine Entscheidung ist gespeichert. Du kannst sie über diesen "
                  "Link jederzeit ändern.", "erfolg")
            return redirect(url_for("veranstaltungen.foto_public", token=token))
    conn.close()
    return render_template("veranstaltungen/foto_public.html", a=a, einst=einst)


@bp.route("/<int:tid>/foto-formular")
def foto_formular(tid):
    """Druckvorlage: Einwilligungserklärung für alle, die noch nicht geantwortet haben."""
    conn = get_db()
    vid = aktiver_verein_id()
    t = conn.execute("SELECT * FROM termine WHERE id=? AND verein_id=?", (tid, vid)).fetchone()
    if not t:
        conn.close()
        abort(404)
    nur_aid = request.args.get("aid", type=int)
    sql = ("SELECT * FROM veranstaltung_anmeldung WHERE termin_id=? AND verein_id=? AND storniert=0 ")
    params = [tid, vid]
    if nur_aid:
        # Einzelblatt: auch für Personen, die schon geantwortet haben (z. B. neu ausdrucken)
        sql += "AND id=? "
        params.append(nur_aid)
    else:
        sql += "AND COALESCE(foto_status,'')='' "
    offene = conn.execute(sql + "ORDER BY anmelder_name", params).fetchall()
    einst = get_einstellungen()
    conn.close()
    return render_template("veranstaltungen/foto_formular.html", t=t, offene=offene, einst=einst,
                           einzel=bool(nur_aid))


@bp.route("/anmeldung/<int:aid>/foto-formular.pdf")
def foto_formular_pdf(aid):
    """Die Einwilligungserklärung einer einzelnen Person als PDF-Datei."""
    from .. import foto_pdf
    conn = get_db()
    vid = aktiver_verein_id()
    a = conn.execute("SELECT * FROM veranstaltung_anmeldung WHERE id=? AND verein_id=?",
                     (aid, vid)).fetchone()
    t = conn.execute("SELECT * FROM termine WHERE id=? AND verein_id=?",
                     (a["termin_id"], vid)).fetchone() if a else None
    einst = get_einstellungen()
    conn.close()
    if not a or not t:
        abort(404)
    daten = foto_pdf.erzeuge(einst, t, a)
    name = f"Fotoerlaubnis-{(a['anmelder_name'] or 'Person').replace(' ', '-')}.pdf"
    return Response(daten, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.route("/anmeldung/<int:aid>/foto-nachweis", methods=["POST"])
def foto_nachweis_hochladen(aid):
    """Das unterschriebene Papierformular als Nachweis ablegen."""
    conn = get_db()
    vid = aktiver_verein_id()
    a = conn.execute("SELECT * FROM veranstaltung_anmeldung WHERE id=? AND verein_id=?",
                     (aid, vid)).fetchone()
    if not a:
        conn.close()
        abort(404)
    datei = request.files.get("foto_nachweis_datei")
    if not datei or not datei.filename:
        conn.close()
        flash("Bitte eine Datei auswählen (PDF oder Foto des unterschriebenen Formulars).", "fehler")
        return redirect(url_for("veranstaltungen.anmeldung_bearbeiten", aid=aid))
    import os as _os
    import time as _time
    basis = _os.path.basename(datei.filename).replace("/", "_")
    dateiname = f"{_time.strftime('%Y%m%d-%H%M%S')}_{basis}"
    from .. import speicher
    speicher.speichern(get_einstellungen(), "fotoerlaubnis", dateiname, datei.read())
    conn.execute("UPDATE veranstaltung_anmeldung SET foto_nachweis=? WHERE id=?", (dateiname, aid))
    conn.commit()
    conn.close()
    flash("Nachweis abgelegt. Denk daran, auch die Antwort (Ja/Nein) einzutragen, "
          "falls noch nicht geschehen.", "erfolg")
    return redirect(url_for("veranstaltungen.anmeldung_bearbeiten", aid=aid))


@bp.route("/anmeldung/<int:aid>/foto-nachweis")
def foto_nachweis(aid):
    """Den abgelegten Nachweis öffnen."""
    conn = get_db()
    a = conn.execute("SELECT foto_nachweis FROM veranstaltung_anmeldung WHERE id=? AND verein_id=?",
                     (aid, aktiver_verein_id())).fetchone()
    conn.close()
    if not a or not (a["foto_nachweis"] or "").strip():
        abort(404)
    from .. import speicher
    return speicher.sende(get_einstellungen(), "fotoerlaubnis", a["foto_nachweis"])


# --------------------------------------------------------------------------- #
#  Einlass: QR-Check-in am Eingang (Voucher scannen → Ampel → abhaken)
# --------------------------------------------------------------------------- #
def _einlass_wer():
    """Kürzel der Person am Einlass fürs Protokoll (checkin_von)."""
    from flask_login import current_user
    if getattr(current_user, "is_authenticated", False):
        return (getattr(current_user, "anzeigename", "") or
                getattr(current_user, "email", "") or "")
    return ""


def _einlass_zaehler(conn, tid):
    """(erschienen, gesamt) über alle gültigen Anmeldungen des Termins."""
    z = conn.execute(
        "SELECT SUM(CASE WHEN a.warteliste=0 THEN 1 ELSE 0 END) gesamt, "
        "SUM(CASE WHEN COALESCE(tn.checkin_am,'')!='' THEN 1 ELSE 0 END) da "
        "FROM veranstaltung_teilnehmer tn JOIN veranstaltung_anmeldung a ON a.id=tn.anmeldung_id "
        "WHERE a.termin_id=? AND a.storniert=0", (tid,)).fetchone()
    # da kann gesamt übersteigen, wenn Wartelisten-Gäste real eingelassen wurden
    return int(z["da"] or 0), int(z["gesamt"] or 0)


def _einlass_gruppe(conn, a):
    """Anmeldung + Teilnehmende als JSON-taugliches Paket fürs Einlass-Panel."""
    teilnehmer = [{"id": tn["id"], "name": tn["name"],
                   "checkin": (tn["checkin_am"] or "")[11:16]}
                  for tn in conn.execute(
                      "SELECT * FROM veranstaltung_teilnehmer WHERE anmeldung_id=? ORDER BY id",
                      (a["id"],)).fetchall()]
    status, gruende = _einlass_bewertung(a, teilnehmer)
    return {"status": status, "gruende": gruende,
            "anmeldung": {"id": a["id"], "name": a["anmelder_name"] or "",
                          "betrag": float(a["betrag"] or 0),
                          "bezahlstatus": a["bezahlstatus"] or "offen",
                          "warteliste": bool(a["warteliste"]),
                          "storniert": bool(a["storniert"]),
                          "notizen": (a["notizen"] or "").strip()},
            "teilnehmer": teilnehmer}


def _einlass_bewertung(a, teilnehmer):
    """Ampel: grün = rein, gelb = etwas beachten, rot = erst klären."""
    if a["storniert"]:
        return "rot", ["Diese Anmeldung wurde storniert."]
    gruende = []
    if a["warteliste"]:
        gruende.append("Steht auf der Warteliste – nur einlassen, wenn Platz ist.")
    if (a["betrag"] or 0) > 0 and (a["bezahlstatus"] or "offen") == "offen":
        gruende.append(f"Offener Betrag: {float(a['betrag']):.2f} €".replace(".", ",", 1))
    if not teilnehmer:
        gruende.append("Keine Teilnehmenden erfasst – bitte in der Anmeldung nachtragen.")
    da = [t for t in teilnehmer if t["checkin"]]
    if teilnehmer and len(da) == len(teilnehmer):
        gruende.append(f"Bereits eingecheckt ({da[0]['checkin']} Uhr) – Voucher doppelt?")
    elif da:
        gruende.append(f"{len(da)} von {len(teilnehmer)} bereits eingecheckt.")
    if (a["notizen"] or "").strip():
        gruende.append("Notiz: " + a["notizen"].strip())
    return ("gelb" if gruende else "gruen"), gruende


def einlass_seite_daten(conn, tid):
    """Alles, was die Einlass-Seite braucht – für Verwaltung UND Mitglieder-App."""
    da, gesamt = _einlass_zaehler(conn, tid)
    suchliste = [{"token": a["token"], "name": a["anmelder_name"] or "",
                  "storniert": bool(a["storniert"]), "warteliste": bool(a["warteliste"])}
                 for a in conn.execute(
                     "SELECT token, anmelder_name, storniert, warteliste "
                     "FROM veranstaltung_anmeldung WHERE termin_id=? AND token!='' "
                     "ORDER BY anmelder_name", (tid,)).fetchall()]
    return da, gesamt, suchliste


def einlass_schwelle(t):
    """Zeitpunkt, ab dem der Einlass als „nach Beginn" gilt (Beginn + 10 min).

    None, wenn der Termin keine Uhrzeit hat – dann gibt es keine Automatik."""
    if not (t["uhrzeit"] or "").strip():
        return None
    try:
        beginn = datetime.strptime(f"{t['datum']} {t['uhrzeit'][:5]}", "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None
    return beginn + timedelta(minutes=10)


def einlass_auto_schliessen(conn, t):
    """Schließt den Einlass ~10 Minuten nach Veranstaltungsbeginn von selbst.

    Wer danach von Hand wieder öffnet, bleibt offen (die neue Startzeit liegt
    dann hinter der Schwelle). Gibt den gültigen einlass_start zurück ('' = zu).
    Ohne Uhrzeit am Termin gibt es keine Automatik."""
    start = (t["einlass_start"] or "") if "einlass_start" in t.keys() else ""
    if not start or not (t["uhrzeit"] or "").strip():
        return start
    schwelle = einlass_schwelle(t)
    try:
        eroeffnet = datetime.strptime(start[:16].replace("T", " "), "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return start
    if schwelle and datetime.now() >= schwelle and eroeffnet < schwelle:
        conn.execute("UPDATE termine SET einlass_start='' WHERE id=?", (t["id"],))
        conn.commit()
        return ""
    return start


def einlass_stand_paket(conn, tid):
    """Zähler + Läuft-Status als JSON – fürs Mitzählen an mehreren Geräten."""
    da, gesamt = _einlass_zaehler(conn, tid)
    t = conn.execute("SELECT id, datum, uhrzeit, einlass_start FROM termine WHERE id=?",
                     (tid,)).fetchone()
    start = einlass_auto_schliessen(conn, t) if t else ""
    return {"da": da, "gesamt": gesamt, "offen": max(0, gesamt - da),
            "start": start[11:16] if start else ""}


def einlass_schalten(conn, tid, aktion):
    """Einlass eröffnen/beenden (öffentlich sichtbarer Läuft-Status)."""
    conn.execute("UPDATE termine SET einlass_start=? WHERE id=?",
                 (now_iso() if aktion == "start" else "", tid))
    conn.commit()


# Kamera am Einlass: app/__init__.py sperrt per Permissions-Policy überall
# Kamera, Mikrofon und Standort (camera=()). Chromium-Browser (Android-Chrome,
# Edge) verweigern getUserMedia dann – der QR-Scanner startete dort nicht.
# Freigabe NUR für die beiden Einlass-Bildschirme (Verwaltung + App), nur für
# die eigene Herkunft und nur bei erfolgreich ausgelieferter Seite (nicht bei
# Weiterleitung/404). Mikrofon und Standort bleiben gesperrt.
# Wächter: tests/test_einlass_kamera.py
KAMERA_ERLAUBT = "geolocation=(), microphone=(), camera=(self)"
_KAMERA_ENDPUNKTE = {"veranstaltungen.einlass", "portal.einlass"}


@bp.after_app_request
def _einlass_kamera_erlauben(antwort):
    if (request.endpoint in _KAMERA_ENDPUNKTE and antwort.status_code == 200
            and antwort.mimetype == "text/html"):
        antwort.headers["Permissions-Policy"] = KAMERA_ERLAUBT
    return antwort


@bp.route("/<int:tid>/einlass")
def einlass(tid):
    conn = get_db()
    t = _termin(conn, tid)
    if not t:
        conn.close()
        abort(404)
    da, gesamt, suchliste = einlass_seite_daten(conn, tid)
    start = einlass_auto_schliessen(conn, t)
    conn.close()
    urls = {"pruefen": url_for("veranstaltungen.einlass_pruefen", tid=tid),
            "checkin": url_for("veranstaltungen.einlass_checkin"),
            "stand": url_for("veranstaltungen.einlass_stand", tid=tid),
            "schalter": url_for("veranstaltungen.einlass_schalter", tid=tid),
            "zurueck": url_for("veranstaltungen.detail", tid=tid)}
    return render_template("veranstaltungen/einlass.html", t=t, da=da, gesamt=gesamt,
                           suchliste=suchliste, urls=urls,
                           einlass_start=start[11:16] if start else "")


@bp.route("/<int:tid>/einlass/stand")
def einlass_stand(tid):
    conn = get_db()
    t = _termin(conn, tid)
    if not t:
        conn.close()
        abort(404)
    paket = einlass_stand_paket(conn, tid)
    conn.close()
    return jsonify(paket)


@bp.route("/<int:tid>/einlass/schalter", methods=["POST"])
def einlass_schalter(tid):
    conn = get_db()
    t = _termin(conn, tid)
    if not t:
        conn.close()
        abort(404)
    einlass_schalten(conn, tid, (request.get_json(silent=True) or {}).get("aktion") or "start")
    paket = einlass_stand_paket(conn, tid)
    conn.close()
    return jsonify(paket)


def _einlass_token(code):
    """Zieht den Token aus dem Scan: volle Voucher-URL oder nackter Token."""
    code = (code or "").strip()
    if "/voucher/" in code:
        code = code.rsplit("/voucher/", 1)[1]
    return code.split("?")[0].split("#")[0].strip().strip("/")


@bp.route("/<int:tid>/einlass/pruefen", methods=["POST"])
def einlass_pruefen(tid):
    token = _einlass_token((request.get_json(silent=True) or {}).get("code"))
    conn = get_db()
    t = _termin(conn, tid)
    if not t or not token:
        conn.close()
        return jsonify({"status": "rot", "gruende": ["Kein gültiger Code."]})
    a = conn.execute("SELECT * FROM veranstaltung_anmeldung WHERE termin_id=? AND token=?",
                     (tid, token)).fetchone()
    if not a:
        # Eingetippte Tokens verzeihen Groß-/Kleinschreibung (Kollisionsrisiko ~0)
        a = conn.execute("SELECT * FROM veranstaltung_anmeldung WHERE termin_id=? "
                         "AND token=? COLLATE NOCASE", (tid, token)).fetchone()
    if not a:
        fremd = conn.execute(
            "SELECT tt.titel, tt.datum FROM veranstaltung_anmeldung aa "
            "JOIN termine tt ON tt.id=aa.termin_id WHERE aa.token=? AND aa.verein_id=?",
            (token, t["verein_id"])).fetchone()
        conn.close()
        if fremd:
            return jsonify({"status": "rot", "gruende": [
                f"Der Voucher gehört zu einer anderen Veranstaltung: "
                f"„{fremd['titel']}“ am {_datum_de(fremd['datum'])}."]})
        return jsonify({"status": "rot", "gruende": ["Unbekannter Voucher – bitte klären."]})
    paket = _einlass_gruppe(conn, a)
    paket["zaehler"] = einlass_stand_paket(conn, tid)
    schwelle = einlass_schwelle(t)
    if not paket["zaehler"]["start"] and schwelle and datetime.now() >= schwelle             and paket["status"] != "rot":
        paket["gruende"].append("Der Einlass ist geschlossen – Nachzügler:in bewusst einlassen?")
        paket["status"] = "gelb"
    conn.close()
    return jsonify(paket)


@bp.route("/einlass/checkin", methods=["POST"])
def einlass_checkin():
    """Check-in setzen/zurücknehmen (je Person oder ganze Gruppe), bar kassieren."""
    daten = request.get_json(silent=True) or {}
    aid = daten.get("anmeldung_id")
    aktion = daten.get("aktion") or "an"
    conn = get_db()
    a = _anmeldung(conn, aid) if aid else None
    if not a:
        conn.close()
        return jsonify({"status": "rot", "gruende": ["Anmeldung nicht gefunden."]}), 404
    if a["storniert"]:
        paket = _einlass_gruppe(conn, a)
        paket["zaehler"] = einlass_stand_paket(conn, a["termin_id"])
        conn.close()
        paket["gruende"].append("Storniert – am Einlass nicht änderbar.")
        return jsonify(paket)
    jetzt, wer_ = now_iso(), _einlass_wer()
    if aktion == "bezahlt":
        conn.execute("UPDATE veranstaltung_anmeldung SET bezahlstatus='bezahlt', "
                     "zahlart=COALESCE(NULLIF(zahlart,''),'bar'), bezahlt_am=? WHERE id=?",
                     (jetzt[:10], a["id"]))
    elif aktion == "alle":
        conn.execute("UPDATE veranstaltung_teilnehmer SET checkin_am=?, checkin_von=? "
                     "WHERE anmeldung_id=? AND COALESCE(checkin_am,'')=''",
                     (jetzt, wer_, a["id"]))
    else:
        tn_id = daten.get("teilnehmer_id")
        tn = conn.execute("SELECT * FROM veranstaltung_teilnehmer WHERE id=? AND anmeldung_id=?",
                          (tn_id, a["id"])).fetchone()
        if not tn:
            conn.close()
            return jsonify({"status": "rot", "gruende": ["Person nicht gefunden."]}), 404
        if aktion == "aus" or (tn["checkin_am"] and aktion != "an"):
            conn.execute("UPDATE veranstaltung_teilnehmer SET checkin_am='', checkin_von='' "
                         "WHERE id=?", (tn["id"],))
        elif not tn["checkin_am"]:
            conn.execute("UPDATE veranstaltung_teilnehmer SET checkin_am=?, checkin_von=? "
                         "WHERE id=?", (jetzt, wer_, tn["id"]))
    if aktion in ("an", "alle", "wechsel"):
        # Der erste Check-in eröffnet den Einlass automatisch – aber nur bis
        # ~10 Minuten nach Beginn. Danach (Nachzügler:innen) öffnet NUR der
        # Schalter bewusst wieder; der Scan warnt stattdessen.
        t_zeile = conn.execute("SELECT id, datum, uhrzeit, einlass_start FROM termine "
                               "WHERE id=?", (a["termin_id"],)).fetchone()
        schwelle = einlass_schwelle(t_zeile) if t_zeile else None
        if t_zeile and not (t_zeile["einlass_start"] or "") and                 (schwelle is None or datetime.now() < schwelle):
            conn.execute("UPDATE termine SET einlass_start=? WHERE id=? AND EXISTS ("
                         "SELECT 1 FROM veranstaltung_teilnehmer tn "
                         "JOIN veranstaltung_anmeldung va ON va.id=tn.anmeldung_id "
                         "WHERE va.termin_id=termine.id AND COALESCE(tn.checkin_am,'')!='')",
                         (jetzt, a["termin_id"]))
    conn.commit()
    a = _anmeldung(conn, a["id"])
    paket = _einlass_gruppe(conn, a)
    # Nach der EIGENEN Aktion ist „bereits eingecheckt" keine Warnung mehr –
    # die Doppel-Voucher-Erkennung gehört nur zum frischen Scan (pruefen)
    paket["gruende"] = [g for g in paket["gruende"] if "bereits eingecheckt" not in g.lower()]
    if not paket["gruende"] and paket["status"] == "gelb":
        paket["status"] = "gruen"
    paket["zaehler"] = einlass_stand_paket(conn, a["termin_id"])
    conn.close()
    return jsonify(paket)


# --------------------------------------------------------------------------- #
#  Kalender-Datei (ICS), Nachrück-Bestätigung, Gutscheine, Einlass-Bericht
# --------------------------------------------------------------------------- #
@bp.route("/kalender/<token>.ics")
def kalender_ics(token):
    """„Zum Kalender hinzufügen": ein einzelner Termin als ICS-Datei (öffentlich)."""
    conn, a = _anmeldung_conn_per_token(
        token, "t.titel, t.datum, t.uhrzeit, t.uhrzeit_bis, t.ort AS veranstaltungsort")
    if not a or not _verein_modul_an(conn, a["verein_id"]):
        conn.close()
        abort(404)
    einst = _verein_einst(conn, a["verein_id"])
    conn.close()

    def _dt(datum, uhrzeit):
        return datum.replace("-", "") + "T" + (uhrzeit[:5].replace(":", "") or "0000") + "00"

    if (a["uhrzeit"] or "").strip():
        start = _dt(a["datum"], a["uhrzeit"])
        if (a["uhrzeit_bis"] or "").strip():
            ende = _dt(a["datum"], a["uhrzeit_bis"])
        else:
            e = datetime.strptime(f"{a['datum']} {a['uhrzeit'][:5]}", "%Y-%m-%d %H:%M") + timedelta(hours=2)
            ende = e.strftime("%Y%m%dT%H%M00")
        zeit = f"DTSTART:{start}\r\nDTEND:{ende}"
    else:
        zeit = f"DTSTART;VALUE=DATE:{a['datum'].replace('-', '')}"

    def _ics(text):
        return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    voucher = _basis_url() + url_for("veranstaltungen.voucher", token=token)
    zeilen = ["BEGIN:VCALENDAR", "VERSION:2.0",
              f"PRODID:-//{_ics((einst or {}).get('vereinsname') or 'Verein')}//NETTverwaltet//DE",
              "BEGIN:VEVENT",
              f"UID:va-{token}@{request.host}",
              f"DTSTAMP:{datetime.now().strftime('%Y%m%dT%H%M%S')}",
              zeit,
              f"SUMMARY:{_ics(a['titel'])}",
              f"LOCATION:{_ics(a['veranstaltungsort'] or '')}",
              f"DESCRIPTION:{_ics('Dein Voucher: ' + voucher)}",
              "END:VEVENT", "END:VCALENDAR", ""]
    return Response("\r\n".join(zeilen), mimetype="text/calendar",
                    headers={"Content-Disposition":
                             f'attachment; filename="termin-{a["datum"]}.ics"'})


@bp.route("/nachruecken/<token>", methods=["GET", "POST"])
def nachrueck_bestaetigen(token):
    """Nachrück-Angebot mit Frist: Platz bestätigen (öffentlich, ohne Konto)."""
    conn, a = _anmeldung_conn_per_token(
        token, "t.titel, t.datum, t.uhrzeit, t.ort AS veranstaltungsort, t.max_teilnehmer")
    if not a or not _verein_modul_an(conn, a["verein_id"]):
        conn.close()
        abort(404)
    einst = _verein_einst(conn, a["verein_id"])
    zustand = "offen"
    if a["storniert"]:
        zustand = "storniert"
    elif not a["warteliste"]:
        zustand = "dabei"
    elif not (a["nachrueck_bis"] or "") or a["nachrueck_bis"] < now_iso():
        zustand = "abgelaufen"
    if zustand == "offen" and (a["datum"] or "") < date.today().isoformat():
        zustand = "abgelaufen"     # Veranstaltung ist vorbei
    if request.method == "POST" and zustand == "offen":
        conn.execute("BEGIN IMMEDIATE")
        stand = _stand(conn, a["termin_id"])
        eigene = len(_teilnehmer(conn, a["id"])) or 1
        # Reservierung der ANDEREN Angebote respektieren, das eigene zählt nicht
        andere = reservierte_plaetze(conn, a["termin_id"]) - eigene
        frei = (a["max_teilnehmer"] or 0) - stand["teilnehmer"] - max(0, andere)
        if not a["max_teilnehmer"] or eigene <= frei:
            conn.execute("UPDATE veranstaltung_anmeldung SET warteliste=0, nachrueck_bis='' "
                         "WHERE id=?", (a["id"],))
            if (a["gutschein_code"] or "").strip():
                conn.execute("UPDATE veranstaltung_gutscheine SET genutzt=genutzt+1 "
                             "WHERE verein_id=? AND code=? COLLATE NOCASE",
                             (a["verein_id"], a["gutschein_code"]))
            conn.commit()
            zustand = "bestaetigt"
            try:
                if a["email"] and einst:
                    vorlagen = vorlagen_laden(conn, a["verein_id"])
                    t_echt = conn.execute("SELECT * FROM termine WHERE id=?",
                                          (a["termin_id"],)).fetchone()
                    ph = _platzhalter(einst, t_echt, a, ", ".join(
                        tn["name"] for tn in _teilnehmer(conn, a["id"])), conn=conn)
                    betreff, text = vorlagen["nachrueck"]
                    ok, _f = mail_gestalten(einst, a["email"], _ersetze(betreff, ph), _ersetze(text, ph))
                    _mail_protokollieren(conn, a["verein_id"], a["termin_id"], a["id"],
                                         a["email"], _ersetze(betreff, ph), automatisch=1, ok=ok)
                    conn.commit()
            except Exception:  # noqa: BLE001
                pass
        else:
            zustand = "voll"
    conn.close()
    return render_template("veranstaltungen/nachrueck_bestaetigen.html", a=a, einst=einst,
                           zustand=zustand, frist=_frist_de(a["nachrueck_bis"] or ""))


@bp.route("/gutscheine", methods=["GET", "POST"])
def gutscheine():
    """Gutschein-/Freikartencodes verwalten (z. B. Presse, Sponsoren, Helfer:innen)."""
    conn = get_db()
    vid = aktiver_verein_id()
    if request.method == "POST":
        if request.form.get("aktion") == "loeschen":
            conn.execute("DELETE FROM veranstaltung_gutscheine WHERE id=? AND verein_id=?",
                         (request.form.get("gid", type=int), vid))
            flash("Gutschein gelöscht.", "erfolg")
        elif request.form.get("aktion") == "umschalten":
            conn.execute("UPDATE veranstaltung_gutscheine SET aktiv=1-aktiv WHERE id=? AND verein_id=?",
                         (request.form.get("gid", type=int), vid))
        else:
            code = (request.form.get("code") or "").strip() or secrets.token_urlsafe(4).upper()
            art = request.form.get("art") if request.form.get("art") in ("frei", "betrag", "prozent") else "frei"
            wert = _float(request.form.get("wert"))
            if art == "prozent":
                wert = max(0.0, min(100.0, wert))
            da = conn.execute("SELECT 1 FROM veranstaltung_gutscheine WHERE verein_id=? "
                              "AND code=? COLLATE NOCASE", (vid, code)).fetchone()
            if da:
                flash(f"Den Code „{code}“ gibt es schon.", "fehler")
            else:
                try:
                    conn.execute("INSERT INTO veranstaltung_gutscheine (verein_id, code, termin_id, "
                                 "art, wert, max_nutzung, erstellt_am) VALUES (?,?,?,?,?,?,?)",
                                 (vid, code, request.form.get("termin_id", type=int) or None,
                                  art, wert, request.form.get("max_nutzung", type=int) or 0, now_iso()))
                    flash(f"Gutschein „{code}“ angelegt.", "erfolg")
                except Exception:  # noqa: BLE001 – UNIQUE-Wettlauf
                    flash(f"Den Code „{code}“ gibt es schon.", "fehler")
        conn.commit()
    liste = conn.execute(
        "SELECT g.*, t.titel AS termin_titel FROM veranstaltung_gutscheine g "
        "LEFT JOIN termine t ON t.id=g.termin_id WHERE g.verein_id=? "
        "ORDER BY g.aktiv DESC, g.id DESC", (vid,)).fetchall()
    termine = conn.execute("SELECT id, titel, datum FROM termine WHERE verein_id=? "
                           "AND anmeldung_aktiv=1 AND datum>=? ORDER BY datum",
                           (vid, date.today().isoformat())).fetchall()
    conn.close()
    return render_template("veranstaltungen/gutscheine.html", aktiv="veranstaltungen",
                           liste=liste, termine=termine)


@bp.route("/<int:tid>/einlass-bericht")
def einlass_bericht(tid):
    """Nach der Veranstaltung: Wer kam wirklich? (No-Shows, Ankunftswelle, Barkasse)"""
    conn = get_db()
    t = _termin(conn, tid)
    if not t:
        conn.close()
        abort(404)
    zeilen = _teilnehmer_zeilen(conn, tid)
    fest = [z for z in zeilen if not z["warteliste"]]
    erschienen = [z for z in zeilen if (z["checkin_am"] or "")]
    wl_da = [z for z in erschienen if z["warteliste"]]
    quote = round(100 * len([z for z in fest if z["checkin_am"]]) / len(fest)) if fest else 0
    noshows = sorted({(z["anmelder_name"], z["teilnehmer_name"])
                      for z in fest if not (z["checkin_am"] or "")})
    # Ankunftswelle in Viertelstunden
    welle = {}
    for z in erschienen:
        hhmm = z["checkin_am"][11:16]
        if hhmm:
            eimer = hhmm[:3] + ("00" if int(hhmm[3:5]) < 15 else
                                "15" if int(hhmm[3:5]) < 30 else
                                "30" if int(hhmm[3:5]) < 45 else "45")
            welle[eimer] = welle.get(eimer, 0) + 1
    welle = sorted(welle.items())
    bar = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(betrag), 0) summe FROM veranstaltung_anmeldung "
        "WHERE termin_id=? AND storniert=0 AND zahlart='bar' AND bezahlstatus='bezahlt'",
        (tid,)).fetchone()
    # Saison-Vergleich: die letzten anmeldbaren Veranstaltungen
    saison = conn.execute(
        "SELECT t2.id, t2.titel, t2.datum, "
        "SUM(CASE WHEN a.warteliste=0 THEN 1 ELSE 0 END) angemeldet, "
        "SUM(CASE WHEN COALESCE(tn.checkin_am,'')!='' AND a.warteliste=0 THEN 1 ELSE 0 END) da "
        "FROM termine t2 "
        "JOIN veranstaltung_anmeldung a ON a.termin_id=t2.id AND a.storniert=0 "
        "JOIN veranstaltung_teilnehmer tn ON tn.anmeldung_id=a.id "
        "WHERE t2.verein_id=? AND t2.anmeldung_aktiv=1 AND (t2.datum<=? OR t2.id=?) "
        "GROUP BY t2.id ORDER BY t2.datum DESC LIMIT 10",
        (t["verein_id"], date.today().isoformat(), tid)).fetchall()
    conn.close()
    return render_template("veranstaltungen/einlass_bericht.html", aktiv="veranstaltungen",
                           t=t, fest=len(fest),
                           erschienen=len([z for z in fest if z["checkin_am"]]),
                           wl_da=len(wl_da), quote=quote, noshows=noshows, welle=welle,
                           bar=bar, saison=saison,
                           max_welle=max([n for _, n in welle], default=0))


@bp.route("/voucher/<token>/personen", methods=["POST"])
def voucher_personen(token):
    """Selbstbedienung am Voucher: Person nachmelden oder austragen (per Token)."""
    conn, a = _anmeldung_conn_per_token(
        token, "t.titel, t.datum, t.uhrzeit, t.storno_frist_stunden, t.max_teilnehmer, t.preis")
    if not a or not _verein_modul_an(conn, a["verein_id"]):
        conn.close()
        abort(404)
    from ..uebersetzung import ue as _ue
    zurueck = redirect(url_for("veranstaltungen.voucher", token=token))
    if a["storniert"] or not storno_erlaubt(a):
        conn.close()
        flash(_ue("Änderungen sind für diese Reservierung nicht mehr möglich."), "fehler")
        return zurueck
    # Schreibsperre: Kapazitätsprüfung und Änderung müssen zusammen passieren
    conn.execute("BEGIN IMMEDIATE")
    teilnehmer = _teilnehmer(conn, a["id"])
    alte_zahl = len(teilnehmer) or 1
    # Regulärer Einzelpreis: gespeichert bei der Anmeldung; Bestand ohne
    # Eintrag fällt auf den Terminpreis zurück. NIE aus dem (womöglich
    # rabattierten) Betrag zurückrechnen – sonst skalieren Freikarten mit.
    if "einzelpreis" in a.keys() and a["einzelpreis"] is not None:
        einzel = max(0.0, a["einzelpreis"])
    else:
        einzel = max(0.0, a["preis"] or 0)
    # Der bei der Anmeldung gewährte Rabatt bleibt in Euro KONSTANT – egal ob
    # Freikarte, Betrag oder Prozent. So kann keine Freikarte zur wachsenden
    # Gruppen-Freikarte werden, und Nachgemeldete zahlen den regulären Preis.
    rabatt = max(0.0, round(einzel * alte_zahl - ((a["betrag"] or 0) - (a["spende"] or 0)), 2))
    aktion = request.form.get("aktion")
    if aktion == "dazu":
        name = (request.form.get("name") or "").strip()
        if not name:
            conn.close()
            flash(_ue("Bitte einen Namen angeben."), "fehler")
            return zurueck
        stand = _stand(conn, a["termin_id"])
        voll = (a["max_teilnehmer"] and not a["warteliste"]
                and stand["teilnehmer"] + reservierte_plaetze(conn, a["termin_id"]) + 1
                > a["max_teilnehmer"])
        if voll:
            conn.close()
            flash(_ue("Leider ist kein Platz mehr frei – bitte melde dich direkt beim Verein."),
                  "fehler")
            return zurueck
        conn.execute("INSERT INTO veranstaltung_teilnehmer (anmeldung_id, name) VALUES (?,?)",
                     (a["id"], name))
        neue_zahl = alte_zahl + 1
    elif aktion == "raus":
        tn = conn.execute("SELECT * FROM veranstaltung_teilnehmer WHERE id=? AND anmeldung_id=?",
                          (request.form.get("tn_id", type=int), a["id"])).fetchone()
        if not tn or len(teilnehmer) <= 1:
            conn.close()
            flash(_ue("Die letzte Person kann nicht entfernt werden – nutze dafür das Storno."),
                  "fehler")
            return zurueck
        conn.execute("DELETE FROM veranstaltung_teilnehmer WHERE id=?", (tn["id"],))
        neue_zahl = alte_zahl - 1
    else:
        conn.close()
        abort(400)
    neuer_betrag = round(max(0.0, einzel * neue_zahl - rabatt) + (a["spende"] or 0), 2)
    status = a["bezahlstatus"]
    if neuer_betrag > (a["betrag"] or 0) and status in ("bezahlt", "erlassen"):
        status = "offen"     # Restbetrag für die nachgemeldete Person
    conn.execute("UPDATE veranstaltung_anmeldung SET betrag=?, bezahlstatus=? WHERE id=?",
                 (neuer_betrag, status, a["id"]))
    if aktion == "raus":
        nachruecken(conn, a["termin_id"])
    conn.commit()
    conn.close()
    flash(_ue("Deine Reservierung wurde angepasst.") if aktion == "dazu"
          else _ue("Die Person wurde ausgetragen."), "erfolg")
    return zurueck
