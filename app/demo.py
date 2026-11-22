"""Schutz für die öffentliche Demo (nur aktiv mit VV_DEMO_LOGIN=1).

In der Demo (verein.nettverwaltet.de) wird JEDE:R per Knopf Admin. Hier steht,
was dort trotzdem nicht geht – damit niemand die Demo kaputt macht, anderen
Besucher:innen schadet oder unseren Server für Spam, Betrug oder Angriffe nutzt:

- SPERRLISTE: Vereine anlegen/löschen, Sicherungen, Sync, Zugänge ändern, 2FA,
  Nextcloud-Login, Kalender-Abos/Event-Import, Bankabruf, Hub/Instagram …
- EINGEFROREN: SMTP, Nextcloud, Bank/IBAN/PayPal, eigene Links und Quota werden
  beim Speichern der Einstellungen auf dem bisherigen Wert gehalten.
- Mails und Push gehen nicht raus (mail.einzelmail / push.senden fragen ist_demo()).
- Uploads höchstens 8 MB, jede HTML-Seite trägt ein Demo-Band und „noindex“.

Doppelter Boden zur Server-Abschottung (internes Docker-Netz ohne Internet,
siehe deploy/konfig-generieren.sh DEMO=1). Demo-Schutz-Audit 16.11.2026.
"""
from flask import current_app, flash, g, has_request_context, jsonify, redirect, request, url_for
from werkzeug.datastructures import ImmutableMultiDict

UPLOAD_GRENZE = 8 * 1024 * 1024

# Endpunkte, die in der Demo gar nicht gehen (alle Methoden)
GESPERRT_IMMER = {
    "sync.index", "sync.status", "sync.paket", "sync.vorschau", "sync.anwenden",
    "auth.login_nextcloud", "auth.login_nextcloud_neu", "auth.passwort_reset_anfordern",
}
# Endpunkte, deren POST (Ändern/Auslösen) in der Demo nicht geht – Ansehen bleibt erlaubt
GESPERRT_POST = {
    "vereine.neu", "vereine.loeschen", "vereine.uebernehmen", "vereine.wechseln",
    "sicherung.jetzt", "sicherung.wiederherstellen",
    "zugaenge.neu", "zugaenge.bearbeiten", "zugaenge.aktiv", "zugaenge.loeschen", "zugaenge.passwort_link",
    "auth.zwei_fa_einrichten",
    "einstellungen.absender", "einstellungen.bankzugang", "einstellungen.nuki",
    "unterlagen.speicherort",
    "kalender.neu", "kalender.bearbeiten", "kalender.loeschen", "kalender.ics_import",
    "kalender.import_ausfuehren", "kalender.import_veranstaltungen",
    "kalender.nextcloud_abo", "kalender.nextcloud_suchen", "kalender.nextcloud_sync", "kalender.nextcloud_waehlen",
    "bank.index",
    "oea.an_hub", "oea.hub_aktion", "oea.hub_einstellungen", "oea.hub_kanal", "oea.hub_stimme",
    "oea.hub_abwesenheit", "oea.instagram", "oea.instagram_posten", "oea.kommentare_holen",
    "oea.kommentar_antwort",
}
# Vereins-Einstellungen, die beim Speichern unverändert bleiben (Zugänge, Geld, Links)
EINGEFROREN = (
    "smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_passwort", "smtp_absender_name",
    "smtp_absender_email", "presse_kopie_email", "pinnwand_mail_an",
    "ncc_server", "ncc_user", "ncc_pass", "nc_gruppe_admin", "nc_gruppe_mitglied", "nc_gruppe_extern",
    "nc_gruppe_schatz", "nc_gruppe_oeffentlichkeit",
    "fints_blz", "fints_url", "bank_kontoinhaber", "bank_iban", "bank_bic", "glaeubiger_id", "paypal_me",
    "satzung_url", "beitragsordnung_url", "datenschutz_url", "tuer_url", "app_url", "event_import_url",
    "whatsapp_kanal", "hub_whatsapp_kanal", "dms_quota_mb", "website",
)
# Schalter (Checkboxen), die in der Demo ihren Wert behalten
EINGEFROREN_SCHALTER = ("freunde_registrierung", "seo_index", "nc_externe_erlaubt")

HINWEIS = "🧪 In der Demo gesperrt – in deinem eigenen Verein geht das."

_BAND = ('<div role="note" style="position:sticky;top:0;z-index:99999;background:#fef3c7;color:#713f12;'
         'border-bottom:1px solid #f59e0b;font:600 13px/1.4 system-ui,sans-serif;padding:6px 12px;'
         'text-align:center">🧪 Demo-Verein – alle Personen und Zahlen sind erfunden. Bitte keine echten '
         'Bank- oder Personendaten eingeben; nachts wird alles zurückgesetzt.</div>')


def ist_demo():
    try:
        return bool(current_app.config.get("DEMO_LOGIN"))
    except RuntimeError:          # außerhalb einer App (Skripte)
        return False


def mail_unterdrueckt():
    """Für mail.einzelmail: True = nicht senden (Demo). Meldet es einmal je Anfrage."""
    if not ist_demo():
        return False
    if has_request_context() and not getattr(g, "_demo_mail_gemeldet", False):
        g._demo_mail_gemeldet = True
        try:
            flash("📭 In der Demo werden keine E-Mails verschickt – im echten Verein ginge sie jetzt raus.", "info")
        except Exception:  # noqa: BLE001
            pass
    return True


def _gesperrt_antwort():
    if request.is_json or request.accept_mimetypes.best == "application/json" or request.path.startswith("/api"):
        return jsonify({"ok": False, "fehler": HINWEIS}), 403
    flash(HINWEIS, "fehler")
    ziel = request.referrer or ""
    if not ziel.startswith(request.host_url):
        ziel = url_for("dashboard.index")
    return redirect(ziel)


def _einstellungen_einfrieren():
    from . import db
    e = db.get_einstellungen()
    if not e:
        return
    form = request.form.to_dict(flat=False)
    for k in EINGEFROREN:
        if k in form and k in e.keys():
            alt = e[k]
            # Geheimfelder: leer = „bisherigen Wert behalten“ (siehe einstellungen.index)
            form[k] = [""] if k in ("smtp_passwort", "ncc_pass") else ["" if alt is None else str(alt)]
    if request.endpoint in ("einstellungen.index", "einstellungen.mitglieder_app"):
        for k in EINGEFROREN_SCHALTER:
            if k in e.keys():
                if e[k]:
                    form[k] = ["1"]
                else:
                    form.pop(k, None)
    request.form = ImmutableMultiDict([(k, v) for k, werte in form.items() for v in werte])


def einrichten(app):
    """Hängt den Demo-Schutz an die App (nur wenn DEMO_LOGIN gesetzt ist)."""
    if not app.config.get("DEMO_LOGIN"):
        return
    app.config["MAX_CONTENT_LENGTH"] = min(app.config.get("MAX_CONTENT_LENGTH") or UPLOAD_GRENZE, UPLOAD_GRENZE)

    @app.before_request
    def _demo_schutz():
        ep = request.endpoint or ""
        if ep in GESPERRT_IMMER or (request.method == "POST" and ep in GESPERRT_POST):
            return _gesperrt_antwort()
        if request.method == "POST" and ep.startswith("einstellungen."):
            _einstellungen_einfrieren()

    @app.after_request
    def _demo_kennzeichnen(antwort):
        antwort.headers["X-Robots-Tag"] = "noindex, nofollow"
        if (antwort.status_code == 200 and antwort.mimetype == "text/html"
                and not antwort.direct_passthrough and not antwort.is_streamed):
            html = antwort.get_data(as_text=True)
            i = html.find("<body")
            if i != -1:
                j = html.find(">", i)
                if j != -1:
                    antwort.set_data(html[:j + 1] + _BAND + html[j + 1:])
        return antwort
