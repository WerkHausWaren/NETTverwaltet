"""
Flask-App-Fabrik: erzeugt die Web-Anwendung und bindet alle Bereiche ein.
"""
from flask import Flask, request, redirect, url_for, session, g, render_template
from flask_login import current_user
from flask_wtf.csrf import CSRFProtect

from . import db
from .auth import darf_verein as _darf_verein
from .auth import init_auth

csrf = CSRFProtect()
from .routes import (mitglieder, kommunikation, einstellungen, gruppen,
                     beitraege, finanzen, termine, dokumente,
                     dashboard, vereine, unterlagen, sicherung,
                     schluessel, schichten, kalender, bank, auswertungen, auth, portal, aufgaben, sync,
                     zugaenge, veranstaltungen, dms, spenden, rechnungen, kontakte, oea, versammlung, fristen,
                     foerderung, lastschrift, adventskalender)
# eigener Name: `app.verteilen` ist die Fachlogik (Cron: from app.verteilen import …)
from .routes import verteilen as verteilen_routes
from .routes import signage as signage_routes


# NETTverwaltet: Projektseite und Spendenlink des Software-Projekts (WerkHausWaren e.V.)
PROJEKT_URL = "https://github.com/WerkHausWaren/NETTverwaltet"
PROJEKT_SPENDE = "https://paypal.me/werkhauswaren"

def create_app():
    app = Flask(__name__)
    from .config import Config
    app.config.from_object(Config)
    # Hinter dem Caddy-Proxy: X-Forwarded-Proto/-Host auswerten, sonst baut
    # url_for(..., _external=True) http://-Adressen (Gunicorn vertraut dem
    # Docker-Netz-Peer nicht). Ohne diese Kopfzeilen ist das ein No-op –
    # Lokalbetrieb und Tests bleiben unverändert. (Review Termin-Teilen 27.10.)
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    db.init_db()
    init_auth(app)
    csrf.init_app(app)
    # Push-Abo-Erneuerung durch den Service Worker (ohne offene Seite -> kein Token)
    csrf.exempt(portal.push_abo)

    # Nextcloud-Basis-URL in Templates verfügbar (z. B. Dateien-Link in der App-Navigation)
    from .auth import nc_basis
    app.jinja_env.globals["nc_basis"] = nc_basis
    # Projektseite und freiwillige Spende an das Software-Projekt (Fußzeile jeder Installation, 22.11.2026)
    app.jinja_env.globals["PROJEKT_URL"] = PROJEKT_URL
    app.jinja_env.globals["PROJEKT_SPENDE"] = PROJEKT_SPENDE

    # Mehrsprachige Mitglieder-App (Opt-in je Verein, siehe app/sprache.py)
    from . import sprache as _sprache
    app.jinja_env.globals["t"] = _sprache.t
    app.jinja_env.globals["sprache_aktuell"] = _sprache.aktuelle_sprache
    app.jinja_env.globals["sprache_richtung"] = _sprache.richtung
    app.jinja_env.globals["tk"] = _sprache.tk
    app.jinja_env.globals["sprach_kuerzel"] = _sprache.sprach_kuerzel
    app.jinja_env.globals["sprachen_liste"] = lambda: [
        (code, _sprache.SPRACH_NAMEN[code]) for code in _sprache.SPRACHEN]

    @app.route("/sprache/<code>")
    def sprache_setzen(code):
        from flask import session as _session, redirect as _redirect, request as _request
        if code in _sprache.SPRACHEN:
            _session["sprache"] = code
        from .html_sicher import zurueck as _zurueck
        ziel = _zurueck(url_for("portal.home"))
        return _redirect(ziel)

    # Wöchentliche Datensicherung: beim Start prüfen, ob eine fällig ist
    try:
        import sys
        if db.BASE_DIR not in sys.path:
            sys.path.insert(0, db.BASE_DIR)
        import backup as _backup
        _backup.backup_wenn_faellig(tage=7)
    except Exception:
        pass  # Sicherung darf den App-Start niemals blockieren

    # Terminbilder: Alt-Dateinamen ohne Vereinsbezug einmalig umstellen
    # (alle Vereine teilen sich die Bild-Ordner – Kollisionsgefahr bei gleicher Termin-Nummer)
    try:
        from .routes.termine import bilddateien_migrieren
        bilddateien_migrieren()
    except Exception:
        pass  # Migration darf den App-Start niemals blockieren

    app.register_blueprint(rechnungen.bp)
    app.register_blueprint(kontakte.bp)
    app.register_blueprint(oea.bp)
    app.register_blueprint(versammlung.bp)
    app.register_blueprint(fristen.bp)
    app.register_blueprint(foerderung.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(portal.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(aufgaben.bp)
    app.register_blueprint(mitglieder.bp)
    app.register_blueprint(gruppen.bp)
    app.register_blueprint(kommunikation.bp)
    app.register_blueprint(beitraege.bp)
    app.register_blueprint(lastschrift.bp)
    app.register_blueprint(finanzen.bp)
    app.register_blueprint(auswertungen.bp)
    app.register_blueprint(termine.bp)
    app.register_blueprint(schichten.bp)
    app.register_blueprint(kalender.bp)
    app.register_blueprint(bank.bp)
    app.register_blueprint(dokumente.bp)
    app.register_blueprint(unterlagen.bp)
    app.register_blueprint(schluessel.bp)
    app.register_blueprint(vereine.bp)
    app.register_blueprint(sicherung.bp)
    app.register_blueprint(einstellungen.bp)
    app.register_blueprint(zugaenge.bp)
    app.register_blueprint(veranstaltungen.bp)
    app.register_blueprint(dms.bp)
    app.register_blueprint(spenden.bp)
    app.register_blueprint(sync.bp)
    app.register_blueprint(adventskalender.bp)
    app.register_blueprint(verteilen_routes.bp)
    app.register_blueprint(signage_routes.bp)
    # Content-Hub: Maschinen-Schnittstelle (Token je Richtung, ohne Modul/Token → 404).
    # Die POST-Routen kommen vom Hub ohne Sitzung – CSRF-Ausnahme NUR für genau diese drei.
    from . import content_hub as _content_hub
    app.register_blueprint(_content_hub.bp)
    for _name in _content_hub.POST_SICHTEN:
        csrf.exempt(getattr(_content_hub, _name))

    # Zugriffsschutz: alles hinter Login (Ausnahmen: öffentliche Seiten + statische Dateien)
    OEFFENTLICH = {
        "willkommen", "favicon", "robots_txt", "sitemap_xml",
        "termine_json",                       # öffentlicher Termin-Feed für die Vereins-Website (prüft Gastansicht selbst)
        "auth.login", "auth.login_nextcloud", "auth.login_nextcloud_status", "auth.login_nextcloud_neu",
        "auth.login_notfall", "auth.zwei_fa", "auth.logout", "auth.einrichten",
        "auth.passkey_login_optionen", "auth.passkey_login",   # Face ID / Fingerabdruck (20.11.2026)
        "auth.demo_login",                    # Ein-Klick-Demo (nur mit VV_DEMO_LOGIN aktiv)
        "auth.freund_registrieren", "auth.freund_bestaetigen",  # Freunde-Selbstregistrierung
        "auth.passwort_vergessen", "auth.passwort_reset_anfordern", "auth.passwort_neu",
        "pwa_manifest", "pwa_sw", "app_icon", "offline",
        # Öffentliche Veranstaltungs-Anmeldung/Feedback/Voucher (per Token geschützt)
        "veranstaltungen.oeffentlich", "veranstaltungen.anmelden_public",
        "veranstaltungen.danke", "veranstaltungen.feedback_public", "veranstaltungen.voucher",
        "veranstaltungen.storno_public",      # Reservierung per persönlichem Link stornieren
        "veranstaltungen.kalender_ics",       # „Zum Kalender hinzufügen" (per Token)
        "veranstaltungen.nachrueck_bestaetigen",  # Nachrück-Angebot bestätigen (per Token)
        "veranstaltungen.voucher_personen",   # Personen am Voucher selbst ändern (per Token)
        "veranstaltungen.foto_public",        # Fotoerlaubnis per persönlichem Link erteilen/widerrufen
        "veranstaltungen.teilnahmebedingungen",  # öffentliche Teilnahmebedingungen
        "veranstaltungen.stand_public",       # anonymisierter Anmeldestand (Kooperation/Sponsoring)
        "rechnungen.oeffentlich",             # Rechnung per persönlichem Link ansehen
        "mitglieder.antrag_public",           # öffentliches Mitglied-werden-Formular
        "portal.brett_gast", "portal.brett_gast_absenden",  # öffentliches Schwarzes Brett (opt-in, moderiert)
        "portal.pinnwand",                    # Brett-Leseansicht für Gäste (Route prüft pinnwand_gast selbst)
        "portal.brett_gast_bearbeiten", "portal.brett_gast_speichern", "portal.brett_aktuell",  # Gast-Eintrag per persönlichem Link ändern
        "sprache_setzen",                     # Sprachwahl der mehrsprachigen Mitglieder-App
        "veranstaltungen.wallet_pass",        # Apple-Wallet-Ticket (per Token geschützt)
        "oea.bild_oeffentlich",               # Bild für Instagram (nur freigegeben, per Signatur geschützt)
        "portal.ausweis_pruefen",             # Ausweis-Prüfseite (per Signatur geschützt)
        "spenden.kampagne_oeffentlich",       # öffentliche Spendenseite (per Token)
        "auth.login_nextcloud_warten",        # Warteseite des Nextcloud-Logins (neu-ladbar)
        "portal.adventskalender", "adventskalender_kurz",  # Lebendiger Adventskalender (öffentliche Programmseite)
        "portal.adventskalender_ics",         # Türchen als Kalenderdatei (öffentlicher Export)
        "einstellungen.logo", "portal.logo",  # Vereinslogo (öffentliche Branding-Grafik)
        "portal.titelbild",                   # Titelbild der Terminseite im Website-Stil
        "einstellungen.rechtliches",          # öffentliches Impressum & Datenschutz
        "termine.sponsor_logo", "termine.bild",  # Sponsor-Logo + Veranstaltungsbild (öffentliche Grafiken)
        "kalender.abo", "aufgaben.abo",       # abonnierbare ICS-Feeds (per Token geschützt)
        "sync.status", "sync.paket",          # Maschine-zu-Maschine, per Token geschützt
        # Content-Hub (n8n): per Token bzw. Bild-Signatur geschützt, Caddy lässt nur die Hub-IP durch
        "content_hub.feed", "content_hub.medien", "content_hub.personen", "content_hub.bild",
        "content_hub.inbox", "content_hub.rueckmeldung", "content_hub.wochenblick",
        # Signage-Geräte (21.11.2026): per Gerätezugang im Cookie, zeigen nur Öffentliches
        "signage.start", "signage.folien", "signage.bild", "signage.logo",
        "signage.checkin_stand", "signage.checkin_pruefen",   # Selbst-Check-in-Gerät (Gerätezugang im Cookie)
    }

    # Gast-Ansicht: diese Portal-Seiten dürfen auch OHNE Login besucht werden,
    # sofern der Verein seinen Freunde-Zugang eingeschaltet hat (freunde_registrierung).
    # Buchen/Reservieren läuft über die öffentliche Token-Anmeldung oder ein Freunde-Konto.
    PORTAL_GAESTE = {"portal.home", "portal.termine", "portal.termin", "portal.kalender",
                     "portal.kino", "portal.termin_ics",
                     "portal.ueber", "portal.hilfe", "portal.app_installieren", "portal.logo",
                     "portal.titelbild", "portal.merkliste", "portal.termin_merken", "portal.kategorie_bild",
                     "portal.termin_foto_oeffentlich"}

    @app.before_request
    def _zugriffsschutz():
        # Lokaler Offline-Betrieb (kein VV_PRODUKTION): kein Login-Zwang,
        # direkt aufs Dashboard – wie die ursprüngliche Offline-App.
        if not app.config.get("PRODUKTION"):
            return
        ep = request.endpoint or ""
        if ep == "static" or ep in OEFFENTLICH:
            return
        # Erst-Einrichtung, solange es noch kein Konto gibt
        if db.benutzer_anzahl() == 0:
            return redirect(url_for("auth.einrichten"))
        if not current_user.is_authenticated:
            # Hängt in der Sitzung eine offene Nextcloud-Anmeldung, wird sie
            # hier serverseitig abgeschlossen – so meldet ein App-Neustart
            # nach „Zugriff gewähren" automatisch an, statt die Anmeldung zu
            # verlieren (Login-Audit 07.11.2026).
            ziel = auth.nc_offen_abschliessen()
            if ziel:
                return redirect(ziel)
            # Gast-Ansicht: reduzierte Portal-Seiten ohne Login, wenn der Verein
            # den Freunde-Zugang aktiviert hat.
            e = db.get_einstellungen()
            gast_ok = bool(e and e["freunde_registrierung"])
            if gast_ok and ep in PORTAL_GAESTE:
                return
            if gast_ok:
                # Standard für Unangemeldete: direkt die öffentliche App-Ansicht
                # (Termine & Co.); die Info-Seite /willkommen bleibt per Link erreichbar.
                return redirect(url_for("portal.home"))
            # Ohne Gast-Ansicht: die öffentliche Info-Startseite (mit „Mitglied-Login").
            return redirect(url_for("willkommen"))
        # App-Nutzung festhalten: letzter_login höchstens 1x pro Tag fortschreiben
        # (wichtig, weil das Remember-Cookie monatelang ohne echten Login anmeldet)
        from datetime import date as _date
        _heute = _date.today().isoformat()
        if session.get("_aktiv_am") != _heute:
            try:
                db.letzter_login_setzen(int(current_user.id))
            except Exception:  # noqa: BLE001
                pass
            session["_aktiv_am"] = _heute
        # Mehr-Mandanten: die Sitzung bestimmt den Verein dieses Nutzers (pro Anfrage).
        # Konten mit eigenem Verein können nie in einem anderen arbeiten (Absicherung 22.11.2026).
        _konto_vid = getattr(current_user, "verein_id", None)
        if _konto_vid is not None and session.get("verein_id") != _konto_vid:
            session["verein_id"] = _konto_vid
        vid = session.get("verein_id")
        if vid is None:
            # Nach einem Remember-Cookie-Re-Login ist die Session leer -> den
            # Verein aus dem Benutzerkonto wiederherstellen (Mandanten-Schutz).
            vid = getattr(current_user, "verein_id", None)
            if vid is not None:
                session["verein_id"] = vid
        if vid is not None:
            g.verein_id = vid
        # „Als Gast ansehen“ (Vorstand, 19.11.2026): Portal-Seiten so, wie ein nicht angemeldeter
        # Gast sie sieht – im eigenen Verein (g.verein_id steht schon). Nimmt nur Rechte weg:
        # Für diese Anfrage gilt die Person als anonym, Nicht-Gäste-Seiten führen zur Startseite.
        if session.get("gast_vorschau") and ep.startswith("portal.") and ep != "portal.gast_vorschau_ende":
            if not getattr(current_user, "ist_admin", False):
                session.pop("gast_vorschau", None)
            else:
                if ep not in PORTAL_GAESTE:
                    return redirect(url_for("portal.home"))
                g._login_user = app.login_manager.anonymous_user()
                return
        # Verwaltung nur für Admins; Schatzmeister:in sieht den Finanzbereich;
        # Mitglieder/Externe kommen in den Portal-Bereich
        if not getattr(current_user, "ist_admin", False):
            # „Mein Aussehen" und „Mein Absender" gehören jeder Person, die sich
            # anmeldet – Lesbarkeit und der eigene Briefkopf sind keine Frage
            # der Berechtigung.
            erlaubt = ep in ("auth.zwei_fa_einrichten", "auth.logout", "auth.portal_bald",
                             "einstellungen.aussehen", "einstellungen.absender",
                             "einstellungen.bankzugang",
                             # Face ID einrichten/entfernen gehört jeder Person (20.11.2026)
                             "auth.passkey_registrieren_optionen", "auth.passkey_registrieren",
                             "auth.passkey_entfernen") or ep.startswith("portal.")
            if getattr(current_user, "rolle", "") == "schatz":
                FINANZ = ("finanzen.", "bank.", "beitraege.", "lastschrift.", "spenden.", "rechnungen.",
                          "kontakte.", "auswertungen.", "dashboard.", "foerderung.",
                          "termine.bild")
                # Schmale Freigabe auf Mitglieder: Die Kasse muss fehlende Bankangaben
                # nachtragen können (sonst scheitert jeder Einzug an fremder Hilfe),
                # sieht aber weder Geburtsdaten noch Notizen noch Austritte.
                MITGLIED_KASSE = ("mitglieder.detail", "mitglieder.zahlungsdaten",
                                  "mitglieder.mandat")
                erlaubt = (erlaubt or any(ep.startswith(p) for p in FINANZ)
                           or ep in MITGLIED_KASSE)
            if getattr(current_user, "rolle", "") == "oeffentlichkeit":
                # Öffentlichkeitsarbeit: eigenes Cockpit + Ankündigungen + Veranstaltungsbilder.
                # Das Dashboard NUR als Startseite – die Spenden- und Rechnungsseiten
                # darunter zeigen Spendernamen mit Beträgen bzw. verschicken
                # Beitragsrechnungen an alle Mitglieder und bleiben der Kasse vorbehalten.
                OEA = ("oea.", "kommunikation.ankuendigung", "termine.bild", "signage.")
                OEA_EINZELN = ("dashboard.index", "dashboard.layout_speichern",
                               "dashboard.besucher_zeitraum")
                erlaubt = (erlaubt or ep in OEA_EINZELN
                           or any(ep.startswith(p) for p in OEA))
            if not erlaubt:
                return redirect(url_for("portal.home"))

    # Übersetzungen für die öffentlichen Seiten (ue('…'), Sprachumschalter)
    from . import uebersetzung as _uebersetzung

    @app.context_processor
    def inject_uebersetzung():
        try:
            return {"ue": _uebersetzung.ue, "sprache_aktiv": _uebersetzung.sprache(),
                    "SPRACHEN": _uebersetzung.SPRACHEN}
        except Exception:  # noqa: BLE001
            return {"ue": lambda s, lang=None: s, "sprache_aktiv": "de",
                    "SPRACHEN": _uebersetzung.SPRACHEN}

    # Vereinsname, Design und Vereinsliste in jedem Template verfügbar machen
    @app.context_processor
    def inject_kontext():
        import os.path
        e = db.get_einstellungen()
        # Erledigt-Animation: einmalig nach dem Abhaken einer Aufgabe (Verein bestimmt das Symbol)
        feier = None
        if session.pop("aufgabe_feier", None):
            name = (e["vereinsname"] or "").lower() if e else ""
            gewaehlt = (e["feier_symbol"] if e and "feier_symbol" in e.keys() else "") or ""
            if gewaehlt in ("stern", "biene", "funkeln"):
                feier = gewaehlt
            elif "kita" in name:
                feier = "biene"
            else:
                feier = "funkeln"
        # Aussehen: Vereinsvorgabe und Nutzerwunsch zusammenführen. Ohne
        # Anmeldung (Lokale Verwaltung) gelten allein die Vereinsfarben.
        from . import design as _design
        eigen = None
        if getattr(current_user, "is_authenticated", False):
            try:
                sys_conn = db.get_system_db()
                zeile = sys_conn.execute(
                    "SELECT design_config FROM benutzer WHERE id=?",
                    (int(current_user.id),)).fetchone()
                sys_conn.close()
                if zeile and "design_config" in zeile.keys():
                    eigen = zeile["design_config"]
            except Exception:  # noqa: BLE001 – Aussehen darf nie eine Seite kippen
                eigen = None
        aussehen = _design.zusammenfuehren(e, eigen, "verwaltung")
        aussehen_app = _design.zusammenfuehren(e, eigen, "app")

        return {
            "verein_name": e["vereinsname"] if e else "",
            "design": e,
            "aussehen": aussehen,
            "aussehen_css": _design.css(aussehen),
            "aussehen_modus": _design.modus_attribut(aussehen),
            "aussehen_app_css": _design.css(aussehen_app),
            "aussehen_app_modus": _design.modus_attribut(aussehen_app),
            # im Server-Betrieb nur die Vereine, die die Person nutzen darf (Absicherung 22.11.2026)
            "alle_vereine": [v for v in db.alle_vereine() if _darf_verein(v["id"])],
            "aktiver_verein_id": db.aktiver_verein_id(),
            "sync_verfuegbar": os.path.exists(os.path.join(db.DATA_DIR, "sync.json")),
            "funktionen_index": __import__("app.funktionen", fromlist=["fuer_verein"]).fuer_verein(e),
            "aufgabe_feier": feier,
        }

    # PWA: Manifest + Service-Worker (im Wurzelpfad, damit der SW die ganze Seite abdeckt)
    from flask import send_from_directory, Response
    import os as _os

    @app.route("/favicon.ico", endpoint="favicon")
    def favicon():
        return send_from_directory(_os.path.join(app.root_path, "static"),
                                   "favicon.png", mimetype="image/png")

    @app.route("/willkommen", endpoint="willkommen")
    def willkommen():
        # Öffentliche Startseite (nur im Server-Betrieb als Landing genutzt);
        # eigenständige Datei mit „Mitglied-Login" oben rechts.
        # Die Seite ist vereinseigen (Impressum, Kontakt) und liegt nicht jeder Installation bei –
        # ohne sie geht es direkt zur Anmeldung.
        if not _os.path.exists(_os.path.join(app.root_path, "static", "willkommen.html")):
            return redirect(url_for("auth.login"))
        return send_from_directory(_os.path.join(app.root_path, "static"),
                                   "willkommen.html", mimetype="text/html")

    @app.route("/adventskalender", endpoint="adventskalender_kurz")
    def adventskalender_kurz():
        # Kurze, merkbare Adresse für Aushänge/WhatsApp – die Seite lebt im Portal.
        # Parameter wie ?tuer=5 (Story-Sprungmarken) müssen mitwandern.
        return redirect(url_for("portal.adventskalender", **request.args.to_dict()))

    @app.route("/manifest.webmanifest", endpoint="pwa_manifest")
    def pwa_manifest():
        import json as _json
        e = db.get_einstellungen()
        name = (e["vereinsname"] if e and e["vereinsname"] else "Mitglieder")
        theme = (e["farbe_primaer"] if e and e["farbe_primaer"] else "#174290")
        daten = {
            "id": "/portal/",
            "name": f"{name}-App",
            "short_name": name,
            "description": f"Mitglieder-App – {name}",
            "lang": "de", "dir": "ltr",
            "categories": ["social", "productivity"],
            "start_url": "/portal/", "scope": "/",
            "display": "standalone", "orientation": "portrait",
            "launch_handler": {"client_mode": "navigate-existing"},
            "background_color": "#ffffff", "theme_color": theme,
            "icons": [
                {"src": "/app-icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/app-icon-512.png", "sizes": "512x512", "type": "image/png"},
                {"src": "/app-icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
            "shortcuts": [
                {"name": "Termine", "url": "/portal/termine",
                 "icons": [{"src": "/app-icon-96.png", "sizes": "96x96", "type": "image/png"}]},
                {"name": "Schichten", "url": "/portal/schichten",
                 "icons": [{"src": "/app-icon-96.png", "sizes": "96x96", "type": "image/png"}]},
                {"name": "Chat", "url": "/portal/chat",
                 "icons": [{"src": "/app-icon-96.png", "sizes": "96x96", "type": "image/png"}]},
            ],
        }
        return Response(_json.dumps(daten), mimetype="application/manifest+json")

    @app.route("/app-icon-<int:groesse>.png", endpoint="app_icon")
    def app_icon(groesse):
        from . import pwa_icon
        e = db.get_einstellungen()
        try:
            daten = pwa_icon.erzeuge(e, groesse)
        except Exception:  # noqa: BLE001 – zur Not das statische Standard-Icon
            return send_from_directory(_os.path.join(app.root_path, "static"),
                                       "icon-512.png", mimetype="image/png")
        resp = Response(daten, mimetype="image/png")
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp

    # Cache-Version des Service Workers: Hash über Statik UND alle Vorlagen.
    # Vorher hingen nur drei statische Dateien im Hash – HTML-Deploys erneuerten
    # die Seiten-Caches nie, und der Offline-Rückgriff zeigte wochenalte Seiten
    # (z. B. die Hilfe ohne den Unterlagen-Bereich). Jetzt ändert JEDER Deploy
    # mit Vorlagen-Änderungen die Version -> Update-Toast + frische Caches.
    import hashlib as _hashlib
    _sw_version = "dev"
    try:
        _h = _hashlib.md5()
        for _f in ("sw.js", "style.css", "erwaehnungen.js", "jsqr.min.js",
                   "verteilen.js", "leaflet/leaflet.js", "leaflet/leaflet.css"):
            with open(_os.path.join(app.root_path, "static", _f), "rb") as _fh:
                _h.update(_fh.read())
        _tpl = _os.path.join(app.root_path, "templates")
        for _wurzel, _dirs, _dateien in sorted(_os.walk(_tpl)):
            for _f in sorted(_dateien):
                _pfad = _os.path.join(_wurzel, _f)
                _st = _os.stat(_pfad)
                _h.update(f"{_os.path.relpath(_pfad, _tpl)}:{_st.st_size}:{int(_st.st_mtime)}".encode())
        _sw_version = _h.hexdigest()[:12]
    except OSError:
        pass

    @app.route("/sw.js", endpoint="pwa_sw")
    def pwa_sw():
        with open(_os.path.join(app.root_path, "static", "sw.js"), encoding="utf-8") as fh:
            quell = fh.read().replace("__VERSION__", _sw_version)
        resp = Response(quell, mimetype="application/javascript")
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.route("/offline", endpoint="offline")
    def offline():
        # Offline-Fallback-Seite (öffentlich, damit der Service Worker sie cachen kann)
        return render_template("offline.html", e=db.get_einstellungen())

    # Links aus gespeicherten Feldern: kein javascript:/data: (Demo-Schutz-Audit 16.11.2026)
    from .html_sicher import sichere_url as _sichere_url
    app.add_template_filter(_sichere_url, "sichere_url")
    from .html_sicher import html_saeubern as _html_saeubern
    app.add_template_filter(_html_saeubern, "html_saeubern")
    from .seo import termin_link as _termin_link
    app.add_template_global(_termin_link, "termin_link")

    @app.template_filter("euro")
    def euro(wert):
        try:
            return f"{float(wert):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
        except (TypeError, ValueError):
            return "0,00 €"

    # Goldener Lorbeerkranz mit Jubiläumszahl (als SVG, in Templates nutzbar)
    import math
    from markupsafe import Markup

    def lorbeer(zahl, groesse=76):
        cx, cy, R = 50, 54, 33
        winkel = [24, 48, 72, 96, 120, 144, 165]
        blaetter = []
        for seite in (1, -1):
            for w in winkel:
                rad = math.radians(w)
                x = cx + seite * R * math.sin(rad)
                y = cy - R * math.cos(rad)
                blaetter.append(
                    '<ellipse cx="%.1f" cy="%.1f" rx="6.4" ry="2.7" transform="rotate(%.1f %.1f %.1f)" '
                    'fill="url(#gold)" stroke="#9a7b12" stroke-width="0.4"/>' % (x, y, seite * w, x, y))
        # kleine Schleife/Basis unten
        blaetter.append('<circle cx="50" cy="88" r="3" fill="url(#gold)" stroke="#9a7b12" stroke-width="0.4"/>')
        svg = (
            '<svg viewBox="0 0 100 100" width="%d" height="%d" xmlns="http://www.w3.org/2000/svg">'
            '<defs><linearGradient id="gold" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#f7e08a"/><stop offset="0.5" stop-color="#d4af37"/>'
            '<stop offset="1" stop-color="#a9801a"/></linearGradient></defs>'
            '%s'
            '<text x="50" y="63" text-anchor="middle" font-size="30" font-weight="800" '
            'fill="#8a6d1a" font-family="Georgia,\'Times New Roman\',serif">%s</text>'
            '</svg>' % (groesse, groesse, "".join(blaetter), zahl))
        return Markup(svg)

    app.jinja_env.globals["lorbeer"] = lorbeer

    from . import dateiablage as _dateiablage
    app.jinja_env.globals["dms_icon"] = _dateiablage.icon
    app.jinja_env.globals["dms_vorschau"] = _dateiablage.vorschaubar

    from .abwesenheit import ist_abwesend as _ist_abwesend, badge as _abwesend_badge
    app.jinja_env.globals["ist_abwesend"] = _ist_abwesend
    app.jinja_env.globals["abwesend_badge"] = _abwesend_badge

    @app.template_filter("hexfarbe")
    def hexfarbe_filter(wert, standard="#174290"):
        """Lässt nur echte Hex-Farben in <style>-Blöcke – alles andere (auch leer)
        wird zum Standard. Schutz vor CSS-Injection über gespeicherte Farben."""
        import re as _re
        wert = str(wert or "").strip()
        return wert if _re.fullmatch(r"#[0-9a-fA-F]{3,8}", wert) else standard

    @app.template_filter("kontrast")
    def kontrast(hexfarbe):
        """Gibt je nach Helligkeit des Hintergrunds die besser lesbare Textfarbe zurück
        (dunkel auf hell, weiß auf dunkel). Eine Formel für alles: design.kontrastfarbe."""
        from . import design as _design
        return _design.kontrastfarbe(hexfarbe)

    @app.template_filter("wa_nummer")
    def wa_nummer(nr):
        """Wandelt eine (deutsche) Telefonnummer ins internationale Format für wa.me um."""
        d = "".join(c for c in str(nr or "") if c.isdigit())
        if not d:
            return ""
        if d.startswith("00"):
            d = d[2:]
        elif d.startswith("0"):
            d = "49" + d[1:]
        return d

    @app.template_filter("datum_de")
    def datum_de(wert):
        if not wert:
            return ""
        try:
            teile = str(wert)[:10].split("-")
            if len(teile) == 3:
                return f"{teile[2]}.{teile[1]}.{teile[0]}"
        except Exception:
            pass
        return wert

    # Wochentags-Kürzel NUR für Termin-Anzeigen (Verwaltung + App, 07.11.).
    # datum_de bleibt bewusst ohne Wochentag – es steht auch auf Rechnungen,
    # Spendenbescheinigungen und Druckseiten, wo ein Wochentag fehl am Platz wäre.
    WOCHENTAGE_KURZ = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

    @app.template_filter("teaser")
    def teaser_filter(text, laenge=170):
        """Kurztext für Terminkarten ohne angehängte Textbausteine (app/termin_extras.py)."""
        from . import termin_extras as _te
        return _te.teaser(text, laenge)

    @app.context_processor
    def _termin_extras_kontext():
        from . import termin_extras as _te
        from . import seo as _seo
        return {"zielgruppen": _te.ZIELGRUPPEN, "eintritt_arten": _te.EINTRITT_ARTEN,
                "zielgruppen_liste": _te.zielgruppen_liste, "termin_bild_url": _seo.bild_url}

    @app.template_filter("wt")
    def wt(wert):
        """ISO-Datum → Wochentags-Kürzel („Fr"); leer/ungültig → ""."""
        from datetime import date as _date
        try:
            return WOCHENTAGE_KURZ[_date.fromisoformat(str(wert)[:10]).weekday()]
        except (ValueError, TypeError):
            return ""

    @app.template_filter("datum_wt")
    def datum_wt(wert):
        """ISO-Datum → „Fr, 06.11.2026"; ohne lesbaren Wochentag wie datum_de."""
        kuerzel = wt(wert)
        d = datum_de(wert)
        return f"{kuerzel}, {d}" if kuerzel else d

    @app.after_request
    def _schutz_kopfzeilen(antwort):
        """Schutzangaben für den Browser – die zweite Verteidigungslinie.

        Die Vorlagen der App nutzen durchgängig Inline-Stile und onclick-Aufrufe,
        deshalb erlaubt die Richtlinie 'unsafe-inline' für eigene Skripte/Stile.
        Fremde Server sind damit trotzdem ausgesperrt: kein Nachladen von Skripten
        aus dem Netz, kein Einbetten der Seite in fremde Rahmen, kein MIME-Raten.
        Dateidownloads setzen ihre eigene, strengere Richtlinie (app/speicher.py).
        """
        if "Content-Security-Policy" not in antwort.headers:
            antwort.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; "
                "font-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'")
        antwort.headers.setdefault("X-Content-Type-Options", "nosniff")
        antwort.headers.setdefault("X-Frame-Options", "DENY")
        antwort.headers.setdefault("Referrer-Policy", "same-origin")
        antwort.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        if app.config.get("PRODUKTION"):
            antwort.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return antwort

    # Öffentliche Demo (VV_DEMO_LOGIN): Sperrliste, eingefrorene Einstellungen,
    # 8-MB-Uploads, Demo-Band + noindex. Zuletzt registriert → läuft nach dem
    # Zugriffsschutz (Login/Rollen gelten weiter).
    from . import demo as _demo
    _demo.einrichten(app)
    # Suchmaschinen: robots.txt, sitemap.xml, noindex außerhalb der Termin-Seiten
    from . import seo as _seo
    _seo.einrichten(app)

    return app
