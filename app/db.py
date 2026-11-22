"""
Datenbank-Schicht (SQLite).

Alles wird in einer einzigen Datei gespeichert: data/verein.db
Keine externe Datenbank nötig, nichts in der Cloud.

Die App ist mandantenfähig: Es können mehrere Vereine verwaltet werden.
Jeder Datensatz gehört über die Spalte verein_id genau zu einem Verein.
Der aktuell ausgewählte Verein steht in der Tabelle system (aktiver_verein_id).
"""
import logging
import sqlite3
import os
import shutil
from datetime import datetime, timedelta
from flask import g

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Datenverzeichnis ist per Umgebungsvariable überschreibbar (z. B. gemountetes
# Volume auf dem Server); lokal bleibt es standardmäßig BASE_DIR/data.
DATA_DIR = os.environ.get("VV_DATA_DIR") or os.path.join(BASE_DIR, "data")

# Registry-Datenbank: kennt nur die Vereinsliste + den aktiven Verein.
SYSTEM_DB_PATH = os.path.join(DATA_DIR, "system.db")
# Alte gemeinsame Datenbank (vor der Aufteilung in eine DB je Verein) –
# wird nur noch für die einmalige Migration gelesen.
LEGACY_DB_PATH = os.path.join(DATA_DIR, "verein.db")
DB_PATH = LEGACY_DB_PATH  # Rückwärtskompatibilität (Altimporte)


def verein_db_pfad(vid):
    """Dateipfad der EIGENEN Datenbank eines Vereins."""
    return os.path.join(DATA_DIR, f"verein_{vid}.db")


def _connect(pfad):
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(pfad)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_system_db():
    """Verbindung zur Registry (Vereinsliste + aktiver Verein)."""
    return _connect(SYSTEM_DB_PATH)


def get_db():
    """Verbindung zur Datenbank des AKTIVEN Vereins."""
    return _connect(verein_db_pfad(aktiver_verein_id()))


def get_db_fuer(vid):
    """Verbindung zur Datenbank eines bestimmten Vereins."""
    return _connect(verein_db_pfad(vid))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SCHEMA = """
-- Vereine (Mandanten): je Verein eigene Stammdaten, Bank, SMTP, Design, Module
CREATE TABLE IF NOT EXISTS vereine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vereinsname TEXT DEFAULT '',
    strasse TEXT DEFAULT '',
    plz TEXT DEFAULT '',
    ort TEXT DEFAULT '',
    email TEXT DEFAULT '',
    telefon TEXT DEFAULT '',
    website TEXT DEFAULT '',
    registergericht TEXT DEFAULT '',
    registernummer TEXT DEFAULT '',
    steuernummer TEXT DEFAULT '',
    vorstand TEXT DEFAULT '',
    -- Bankverbindung des Vereins (für SEPA-Einzug / Rechnungen)
    bank_kontoinhaber TEXT DEFAULT '',
    bank_iban TEXT DEFAULT '',
    bank_bic TEXT DEFAULT '',
    glaeubiger_id TEXT DEFAULT '',
    -- SMTP / E-Mail-Versand
    smtp_host TEXT DEFAULT '',
    smtp_port INTEGER DEFAULT 587,
    smtp_security TEXT DEFAULT 'starttls',
    smtp_user TEXT DEFAULT '',
    smtp_passwort TEXT DEFAULT '',
    smtp_absender_name TEXT DEFAULT '',
    smtp_absender_email TEXT DEFAULT '',
    -- Design
    logo_dateiname TEXT DEFAULT '',
    schriftart TEXT DEFAULT 'System',
    farbe_primaer TEXT DEFAULT '#2563eb',
    farbe_sidebar TEXT DEFAULT '#0f172a',
    -- Dashboard-Module (1 = aktiv)
    modul_aktuelles INTEGER DEFAULT 1,
    modul_geburtstage INTEGER DEFAULT 1,
    modul_spenden INTEGER DEFAULT 1,
    modul_rechnungen INTEGER DEFAULT 1,
    modul_jubilaeen INTEGER DEFAULT 1,
    dash_geburtstage_tage INTEGER DEFAULT 30,
    dash_jubilaeen_tage INTEGER DEFAULT 365,
    -- Dokumentenablage: 'lokal' oder 'onedrive'
    dok_speicherort TEXT DEFAULT 'lokal',
    erstellt_am TEXT
);

-- Merkt sich den aktuell ausgewählten Verein (genau eine Zeile, id=1)
CREATE TABLE IF NOT EXISTS system (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    aktiver_verein_id INTEGER
);

CREATE TABLE IF NOT EXISTS mitglieder (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    mitgliedsnummer TEXT,
    anrede TEXT DEFAULT '',
    titel TEXT DEFAULT '',
    vorname TEXT DEFAULT '',
    nachname TEXT DEFAULT '',
    firma TEXT DEFAULT '',
    strasse TEXT DEFAULT '',
    plz TEXT DEFAULT '',
    ort TEXT DEFAULT '',
    land TEXT DEFAULT 'Deutschland',
    email TEXT DEFAULT '',
    telefon TEXT DEFAULT '',
    mobil TEXT DEFAULT '',
    geburtsdatum TEXT DEFAULT '',
    eintrittsdatum TEXT DEFAULT '',
    austrittsdatum TEXT DEFAULT '',
    status TEXT DEFAULT 'aktiv',
    beitragsgruppe TEXT DEFAULT '',
    beitrag REAL DEFAULT 0,
    zahlungsweise TEXT DEFAULT 'jährlich',
    zahlungsart TEXT DEFAULT 'Überweisung',
    iban TEXT DEFAULT '',
    bic TEXT DEFAULT '',
    kontoinhaber TEXT DEFAULT '',
    mandatsreferenz TEXT DEFAULT '',
    mandatsdatum TEXT DEFAULT '',
    notizen TEXT DEFAULT '',
    erstellt_am TEXT,
    geaendert_am TEXT
);

CREATE TABLE IF NOT EXISTS gruppen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    name TEXT NOT NULL,
    beschreibung TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS mitglied_gruppe (
    mitglied_id INTEGER NOT NULL REFERENCES mitglieder(id) ON DELETE CASCADE,
    gruppe_id INTEGER NOT NULL REFERENCES gruppen(id) ON DELETE CASCADE,
    PRIMARY KEY (mitglied_id, gruppe_id)
);

-- Eigene Felder je Verein (z. B. Kita: Kind, Kita-Ende) + Werte je Mitglied
CREATE TABLE IF NOT EXISTS mitglied_felder (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    name TEXT DEFAULT '',                -- Beschriftung
    typ TEXT DEFAULT 'text',             -- text | mehrzeilig | datum | zahl | ja_nein | auswahl
    optionen TEXT DEFAULT '',            -- bei 'auswahl': Optionen (eine pro Zeile)
    reihenfolge INTEGER DEFAULT 0,
    erstellt_am TEXT
);
CREATE TABLE IF NOT EXISTS mitglied_feldwerte (
    mitglied_id INTEGER NOT NULL REFERENCES mitglieder(id) ON DELETE CASCADE,
    feld_id INTEGER NOT NULL REFERENCES mitglied_felder(id) ON DELETE CASCADE,
    wert TEXT DEFAULT '',
    PRIMARY KEY (mitglied_id, feld_id)
);
CREATE TABLE IF NOT EXISTS mitglied_kinder (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    mitglied_id INTEGER NOT NULL REFERENCES mitglieder(id) ON DELETE CASCADE,
    name TEXT DEFAULT '',
    geburtsdatum TEXT DEFAULT '',
    austritt_datum TEXT DEFAULT '',      -- z. B. wann das Kind die Kita verlässt
    notiz TEXT DEFAULT '',
    erinnert INTEGER DEFAULT 0,          -- Vorab-Erinnerung an den Vorstand schon gesendet?
    erstellt_am TEXT
);

CREATE TABLE IF NOT EXISTS vorlagen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    name TEXT NOT NULL,
    betreff TEXT DEFAULT '',
    inhalt TEXT DEFAULT '',
    erstellt_am TEXT
);

CREATE TABLE IF NOT EXISTS versand (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    betreff TEXT DEFAULT '',
    inhalt TEXT DEFAULT '',
    anzahl_empfaenger INTEGER DEFAULT 0,
    anzahl_erfolg INTEGER DEFAULT 0,
    anzahl_fehler INTEGER DEFAULT 0,
    details TEXT DEFAULT '',
    gesendet_am TEXT
);

CREATE TABLE IF NOT EXISTS beitraege (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    mitglied_id INTEGER REFERENCES mitglieder(id) ON DELETE CASCADE,
    jahr INTEGER,
    bezeichnung TEXT DEFAULT '',
    betrag REAL DEFAULT 0,
    faellig_am TEXT DEFAULT '',
    bezahlt INTEGER DEFAULT 0,
    bezahlt_am TEXT DEFAULT '',
    zahlungsart TEXT DEFAULT '',
    erstellt_am TEXT
);

-- Lastschriftlauf: ein Einzug als nachvollziehbarer Vorgang statt als einmaliger
-- Dateidownload. Der Lauf hält fest, wer mit welchem Betrag und welcher
-- Mandatsreferenz drin war – auch wenn sich die Mitgliedsdaten später ändern.
CREATE TABLE IF NOT EXISTS sepa_laeufe (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    quelle TEXT DEFAULT 'beitrag',      -- beitrag | rechnung | veranstaltung
    jahr INTEGER,
    bezeichnung TEXT DEFAULT '',
    faellig_am TEXT DEFAULT '',         -- gewünschter Einzugstag
    status TEXT DEFAULT 'entwurf',      -- entwurf | eingereicht | verbucht | verworfen
    anzahl INTEGER DEFAULT 0,
    summe REAL DEFAULT 0,
    msg_id TEXT DEFAULT '',             -- Kennung der erzeugten Datei
    datei_am TEXT DEFAULT '',           -- wann die Datei zuletzt erzeugt wurde
    eingereicht_am TEXT DEFAULT '',
    verbucht_am TEXT DEFAULT '',
    erstellt_am TEXT,
    erstellt_von INTEGER,               -- benutzer.id
    notiz TEXT DEFAULT '',
    buchung_id INTEGER                  -- die Sammelbuchung im Kassenbuch
);

-- Ein Posten je Forderung, eingefroren beim Anlegen des Laufs.
CREATE TABLE IF NOT EXISTS sepa_lauf_posten (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    lauf_id INTEGER REFERENCES sepa_laeufe(id) ON DELETE CASCADE,
    mitglied_id INTEGER,
    beitrag_id INTEGER,                 -- Herkunft der Forderung
    name TEXT DEFAULT '',
    iban TEXT DEFAULT '',
    bic TEXT DEFAULT '',
    betrag REAL DEFAULT 0,
    mandatsreferenz TEXT DEFAULT '',
    mandatsdatum TEXT DEFAULT '',
    verwendungszweck TEXT DEFAULT '',
    referenz TEXT DEFAULT '',           -- Ende-zu-Ende-Kennung
    status TEXT DEFAULT 'dabei',        -- dabei | ausgelassen | ruecklaeufer
    grund TEXT DEFAULT '',              -- warum ausgelassen bzw. zurückgekommen
    angekuendigt_am TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS buchungen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    datum TEXT DEFAULT '',
    typ TEXT DEFAULT 'einnahme',
    kategorie TEXT DEFAULT '',
    beschreibung TEXT DEFAULT '',
    betrag REAL DEFAULT 0,
    mitglied_id INTEGER REFERENCES mitglieder(id) ON DELETE SET NULL,
    konto_id INTEGER,
    sphaere TEXT DEFAULT '',
    beleg_dateiname TEXT DEFAULT '',
    erstellt_am TEXT
);

-- Kontenrahmen für gemeinnützige Vereine (mit steuerlichen Sphären)
CREATE TABLE IF NOT EXISTS konten (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    nummer TEXT DEFAULT '',
    name TEXT DEFAULT '',
    typ TEXT DEFAULT 'einnahme',        -- einnahme | ausgabe
    sphaere TEXT DEFAULT 'ideell',      -- ideell | vermoegen | zweckbetrieb | wirtschaftlich
    aktiv INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS termine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    titel TEXT DEFAULT '',
    beschreibung TEXT DEFAULT '',
    datum TEXT DEFAULT '',
    uhrzeit TEXT DEFAULT '',
    ort TEXT DEFAULT '',
    kategorie_id INTEGER,
    aktiv INTEGER DEFAULT 1,
    erstellt_am TEXT
);

-- Termin-Kategorien mit Farbcodierung
CREATE TABLE IF NOT EXISTS termin_kategorien (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    name TEXT DEFAULT '',
    farbe TEXT DEFAULT '#2563eb'
);

CREATE TABLE IF NOT EXISTS teilnahmen (
    termin_id INTEGER NOT NULL REFERENCES termine(id) ON DELETE CASCADE,
    mitglied_id INTEGER NOT NULL REFERENCES mitglieder(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'eingeladen',
    PRIMARY KEY (termin_id, mitglied_id)
);

-- Veranstaltungs-Anmeldungen (externe Teilnehmende, getrennt von den Mitgliedern)
CREATE TABLE IF NOT EXISTS veranstaltung_anmeldung (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    termin_id INTEGER NOT NULL REFERENCES termine(id) ON DELETE CASCADE,
    anmelder_name TEXT DEFAULT '',
    strasse TEXT DEFAULT '',
    plz TEXT DEFAULT '',
    ort TEXT DEFAULT '',
    telefon TEXT DEFAULT '',
    email TEXT DEFAULT '',
    quelle TEXT DEFAULT '',              -- woher der Kontakt kommt
    foto_ok INTEGER DEFAULT 0,           -- Einwilligung Fotografieren
    infos_ok INTEGER DEFAULT 0,          -- darf weitere Infos erhalten
    agb_ok INTEGER DEFAULT 0,            -- Teilnahmebedingungen akzeptiert
    mitglied_interesse INTEGER DEFAULT 0,
    bezahlstatus TEXT DEFAULT 'offen',   -- offen | bezahlt | erlassen
    betrag REAL DEFAULT 0,
    zahlart TEXT DEFAULT '',
    bezahlt_am TEXT DEFAULT '',
    spende REAL DEFAULT 0,
    notizen TEXT DEFAULT '',
    storniert INTEGER DEFAULT 0,
    warteliste INTEGER DEFAULT 0,
    token TEXT DEFAULT '',               -- für Voucher-/Feedback-Link
    herkunft TEXT DEFAULT 'admin',       -- admin | online
    erstellt_am TEXT
);
CREATE TABLE IF NOT EXISTS veranstaltung_teilnehmer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    anmeldung_id INTEGER NOT NULL REFERENCES veranstaltung_anmeldung(id) ON DELETE CASCADE,
    name TEXT DEFAULT '',
    geburtsdatum TEXT DEFAULT '',
    notiz TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS veranstaltung_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    termin_id INTEGER NOT NULL REFERENCES termine(id) ON DELETE CASCADE,
    anmeldung_id INTEGER,
    gesamt INTEGER DEFAULT 0,            -- Gesamtbewertung 1..5
    organisation INTEGER DEFAULT 0,     -- 1..5
    inhalt INTEGER DEFAULT 0,           -- 1..5
    weiterempfehlung INTEGER DEFAULT 0, -- 0..10 (NPS)
    text TEXT DEFAULT '',
    erstellt_am TEXT
);
-- Angepasste E-Mail-Vorlagen für die Teilnehmenden-Kommunikation (je Verein; fehlt ein
-- Schlüssel, gilt die eingebaute Standard-Vorlage)
CREATE TABLE IF NOT EXISTS veranstaltung_mailvorlagen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    schluessel TEXT,
    name TEXT DEFAULT '',                -- Anzeigename (für selbst erstellte Vorlagen)
    betreff TEXT DEFAULT '',
    text TEXT DEFAULT '',
    UNIQUE (verein_id, schluessel)
);
-- Protokoll: welche Mail ging an welche Anmeldung (inkl. automatischer Bestätigung)
CREATE TABLE IF NOT EXISTS veranstaltung_mailprotokoll (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    termin_id INTEGER,
    anmeldung_id INTEGER,
    email TEXT DEFAULT '',
    betreff TEXT DEFAULT '',
    anhaenge INTEGER DEFAULT 0,
    automatisch INTEGER DEFAULT 0,
    ok INTEGER DEFAULT 1,
    gesendet_am TEXT
);

-- Jubiläen-Regeln (flexibel definierbar): runde Geburtstage, Mitgliedschaftsjubiläen
CREATE TABLE IF NOT EXISTS jubilaeen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    name TEXT DEFAULT '',
    typ TEXT DEFAULT 'geburtstag',   -- geburtstag | mitgliedschaft
    jahre TEXT DEFAULT '',            -- z.B. "50,60,70,80" bzw. "10,25,40"
    aktiv INTEGER DEFAULT 1
);

-- Schlüsselbuch: Ausgabe physischer Schlüssel und Zugangscodes
CREATE TABLE IF NOT EXISTS schluesselbuch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    bezeichnung TEXT DEFAULT '',
    typ TEXT DEFAULT 'Schlüssel',        -- Schlüssel | Zugangscode
    code_wert TEXT DEFAULT '',            -- optionaler Codewert
    inhaber_mitglied_id INTEGER REFERENCES mitglieder(id) ON DELETE SET NULL,
    inhaber_name TEXT DEFAULT '',         -- falls kein Mitglied
    ausgegeben_am TEXT DEFAULT '',
    befristet_bis TEXT DEFAULT '',        -- leer = dauerhaft
    zurueckgegeben_am TEXT DEFAULT '',    -- leer = noch ausgegeben
    notiz TEXT DEFAULT '',
    erstellt_am TEXT
);

-- Schichtplan: Rollen
CREATE TABLE IF NOT EXISTS rollen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    name TEXT NOT NULL,
    farbe TEXT DEFAULT '#2563eb',
    beschreibung TEXT DEFAULT ''
);

-- Medien je Rolle: Dokumente (Datei) oder Videos (Link)
CREATE TABLE IF NOT EXISTS rolle_medien (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rolle_id INTEGER REFERENCES rollen(id) ON DELETE CASCADE,
    typ TEXT DEFAULT 'dokument',       -- dokument | video
    titel TEXT DEFAULT '',
    dateiname TEXT DEFAULT '',          -- bei Datei-Upload
    original_name TEXT DEFAULT '',
    url TEXT DEFAULT '',                -- bei Video-Link
    erstellt_am TEXT
);

-- Schichtplan: Schichten (Einsätze bei Veranstaltungen)
CREATE TABLE IF NOT EXISTS schichten (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    termin_id INTEGER REFERENCES termine(id) ON DELETE SET NULL,
    titel TEXT DEFAULT '',
    datum TEXT DEFAULT '',
    zeit_von TEXT DEFAULT '',
    zeit_bis TEXT DEFAULT '',
    ort TEXT DEFAULT '',
    notiz TEXT DEFAULT '',
    erstellt_am TEXT
);

-- Schichtplan: einzelne Positionen (Slot = eine benötigte Rolle in einer Schicht)
CREATE TABLE IF NOT EXISTS schicht_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schicht_id INTEGER REFERENCES schichten(id) ON DELETE CASCADE,
    rolle_id INTEGER REFERENCES rollen(id) ON DELETE SET NULL,
    mitglied_id INTEGER REFERENCES mitglieder(id) ON DELETE SET NULL,
    notiz TEXT DEFAULT ''
);

-- Kalenderquellen je Verein (ICS-Abo oder Weblink)
CREATE TABLE IF NOT EXISTS kalender (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    name TEXT DEFAULT '',
    typ TEXT DEFAULT 'ics',              -- ics | weblink
    url TEXT DEFAULT '',
    erstellt_am TEXT
);

-- Geldkonten (echte Geldtöpfe: Barkasse, Bankkonto, PayPal ...)
CREATE TABLE IF NOT EXISTS geldkonten (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    name TEXT DEFAULT '',
    typ TEXT DEFAULT 'bank',            -- bar | bank | paypal | sonstig
    anfangsbestand REAL DEFAULT 0,
    anfangsdatum TEXT DEFAULT '',
    aktiv INTEGER DEFAULT 1,
    sortierung INTEGER DEFAULT 0
);

-- Kontoauszüge: hochgeladene Bank-/PayPal-Auszüge je Geldkonto und Jahr
-- (Kassenprüfung; Beleg für den Anfangsbestand des Wirtschaftsjahres)
CREATE TABLE IF NOT EXISTS kontoauszuege (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    geldkonto_id INTEGER,
    jahr INTEGER,
    bezeichnung TEXT DEFAULT '',
    dateiname TEXT DEFAULT '',
    hochgeladen_am TEXT
);

-- Wiederkehrende (Dauer-)Buchungen: Miete, Versicherung, Beiträge ...
CREATE TABLE IF NOT EXISTS dauerbuchungen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    name TEXT DEFAULT '',
    typ TEXT DEFAULT 'ausgabe',        -- einnahme | ausgabe
    betrag REAL DEFAULT 0,
    konto_id INTEGER,
    geldkonto_id INTEGER,
    intervall TEXT DEFAULT 'monatlich', -- monatlich | quartal | halbjahr | jaehrlich
    naechste_faellig TEXT DEFAULT '',
    beschreibung TEXT DEFAULT '',
    aktiv INTEGER DEFAULT 1,
    erstellt_am TEXT
);

-- Vereinsunterlagen (Freistellungsbescheid, Satzung, Registerauszug ...)
CREATE TABLE IF NOT EXISTS vereinsdokumente (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    kategorie TEXT DEFAULT 'Sonstiges',
    titel TEXT DEFAULT '',
    dateiname TEXT DEFAULT '',        -- Dateiname im Ablageordner
    original_name TEXT DEFAULT '',
    notiz TEXT DEFAULT '',
    hochgeladen_am TEXT
);

-- Dateiablage: Ordner-Sichtbarkeit je Gruppe (kein Eintrag für einen Pfad = alle Mitglieder)
CREATE TABLE IF NOT EXISTS dms_zugriff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    pfad TEXT DEFAULT '',
    gruppe_id INTEGER
);

-- Wiederkehrende Spenden (Dauerspenden-Definition)
CREATE TABLE IF NOT EXISTS dauerspenden (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    betrag REAL DEFAULT 0,
    intervall TEXT DEFAULT 'monatlich',  -- monatlich | quartal | halbjahr | jaehrlich
    spender_name TEXT DEFAULT '',
    spender_strasse TEXT DEFAULT '',
    spender_plz TEXT DEFAULT '',
    spender_ort TEXT DEFAULT '',
    spender_email TEXT DEFAULT '',
    mitglied_id INTEGER,
    zweck TEXT DEFAULT '',
    start_datum TEXT DEFAULT '',
    aktiv INTEGER DEFAULT 1,
    erstellt_am TEXT
);

-- Einzelne (erfolgte) Spenden – Grundlage jeder Spendenbescheinigung
CREATE TABLE IF NOT EXISTS spenden (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER,
    datum TEXT DEFAULT '',
    betrag REAL DEFAULT 0,
    spender_name TEXT DEFAULT '',
    spender_strasse TEXT DEFAULT '',
    spender_plz TEXT DEFAULT '',
    spender_ort TEXT DEFAULT '',
    spender_email TEXT DEFAULT '',
    mitglied_id INTEGER,
    zweck TEXT DEFAULT '',
    art TEXT DEFAULT 'geld',             -- geld | sach
    dauerspende_id INTEGER,              -- gesetzt, wenn aus einer Dauerspende erfasst
    erstellt_am TEXT
);
"""


# Registry-Schema: Vereinsliste + aktiver Verein + Benutzerkonten (global)
SYSTEM_SCHEMA = """
CREATE TABLE IF NOT EXISTS system (
    id INTEGER PRIMARY KEY,
    aktiver_verein_id INTEGER
);
CREATE TABLE IF NOT EXISTS vereine_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vereinsname TEXT DEFAULT '',
    erstellt_am TEXT
);
CREATE TABLE IF NOT EXISTS benutzer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nc_benutzer TEXT DEFAULT '',        -- Nextcloud-Loginname (leer beim Notfall-Konto)
    email TEXT DEFAULT '',
    anzeigename TEXT DEFAULT '',
    rolle TEXT DEFAULT 'mitglied',      -- admin | mitglied | extern
    verein_id INTEGER,                  -- Konto gehört zu diesem Verein (Mehr-Mandanten); NULL = übergreifend (Notfall-Admin)
    mitglied_id INTEGER,                -- Verknüpfung zum Mitglieder-Datensatz (Portal)
    passwort_hash TEXT DEFAULT '',      -- nur Notfall-Konto
    totp_secret TEXT DEFAULT '',        -- nur Notfall-Konto (2FA)
    twofa_aktiv INTEGER DEFAULT 0,
    nc_token TEXT DEFAULT '',           -- App-Passwort aus dem Nextcloud-Login-Flow
    reset_token TEXT DEFAULT '',
    reset_ablauf TEXT DEFAULT '',
    aktiv INTEGER DEFAULT 1,
    letzter_login TEXT,
    erstellt_am TEXT
);
CREATE TABLE IF NOT EXISTS push_abos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    benutzer_id INTEGER,
    endpoint TEXT,
    p256dh TEXT,
    auth TEXT,
    erstellt_am TEXT
);
-- Herzchenliste: Veranstaltungen, die sich ein Konto gemerkt hat (19.11.2026).
-- Liegt in der Registry, weil Freunde-Konten keinen Mitglieder-Datensatz haben;
-- wird beim Löschen des Kontos bzw. Vereins mit entfernt.
CREATE TABLE IF NOT EXISTS termin_merkliste (
    benutzer_id INTEGER NOT NULL,
    verein_id INTEGER NOT NULL,
    termin_id INTEGER NOT NULL,
    erstellt_am TEXT,
    PRIMARY KEY (benutzer_id, verein_id, termin_id)
);
-- Face ID / Touch ID / Fingerabdruck (WebAuthn-Passkeys, 20.11.2026).
-- Je Gerät eine Zeile; der öffentliche Schlüssel liegt hier, der private
-- verlässt das Gerät nie. credential_id = base64url.
CREATE TABLE IF NOT EXISTS passkeys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    benutzer_id INTEGER NOT NULL,
    credential_id TEXT NOT NULL UNIQUE,
    public_key BLOB NOT NULL,
    sign_count INTEGER DEFAULT 0,
    transports TEXT DEFAULT '',
    name TEXT DEFAULT '',
    rp_id TEXT DEFAULT '',
    erstellt_am TEXT,
    zuletzt_am TEXT
);
CREATE INDEX IF NOT EXISTS ix_passkeys_benutzer ON passkeys(benutzer_id);
-- Face-ID-Challenges serverseitig: einmalig einlösbar (auch ein mitgeschnittenes
-- altes Sitzungs-Cookie hilft nicht mehr), höchstens 5 Minuten gültig (21.11.2026)
-- Signage (21.11.2026): Bildschirme/iPads, die Inhalte der App anzeigen. Global
-- (system.db), weil ein Gerät sich VOR der Zuordnung zu einem Verein koppelt.
CREATE TABLE IF NOT EXISTS signage_geraete (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verein_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    rolle TEXT DEFAULT 'schaufenster',      -- schaufenster | tuer
    einstellungen TEXT DEFAULT '{}',        -- JSON je Rolle
    token_hash TEXT UNIQUE,                 -- SHA-256 des Gerätezugangs (Cookie)
    erstellt_am TEXT,
    letzter_kontakt TEXT,
    kontakt_info TEXT DEFAULT '',           -- Browser-Kennung (gekürzt)
    neu_laden INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS signage_kopplungen (
    code TEXT PRIMARY KEY,                  -- angezeigter Kopplungscode (ohne Bindestrich)
    geheim_hash TEXT NOT NULL UNIQUE,       -- SHA-256 des Kopplungs-Cookies
    ip TEXT DEFAULT '',
    erstellt REAL NOT NULL,
    ablauf REAL NOT NULL,
    geraet_id INTEGER
);
CREATE TABLE IF NOT EXISTS passkey_anfragen (
    id TEXT PRIMARY KEY,
    zweck TEXT NOT NULL,
    challenge TEXT NOT NULL,
    benutzer_id INTEGER,
    ablauf REAL NOT NULL
);
"""

# Kind-/Join-Tabellen ohne eigene verein_id (nur für Mehr-Vereine-Migration relevant)
_KIND_TABELLEN = {
    "mitglied_gruppe": ("mitglied_id", "mitglieder"),
    "teilnahmen": ("mitglied_id", "mitglieder"),
    "schicht_slots": ("schicht_id", "schichten"),
    "rolle_medien": ("rolle_id", "rollen"),
}


def init_db():
    """Registry sicherstellen, Alt-DB einmalig migrieren, jede Vereins-DB aktuell halten."""
    sys_conn = get_system_db()
    sys_conn.executescript(SYSTEM_SCHEMA)
    # Sanfte Migration der Registry (neue Benutzer-Spalten)
    _spalte_sicherstellen(sys_conn, "benutzer", "mitglied_id", "INTEGER")
    _spalte_sicherstellen(sys_conn, "benutzer", "verein_id", "INTEGER")
    _spalte_sicherstellen(sys_conn, "benutzer", "dashboard_config", "TEXT")
    # Konto hat die App schon installiert → „App verfügbar"-Hinweis überall ausblenden
    _spalte_sicherstellen(sys_conn, "benutzer", "pwa_installiert", "INTEGER DEFAULT 0")
    # Herzchenliste: Erinnerung am Vortag (nur wer es selbst einschaltet) + Merker „schon erinnert“
    _spalte_sicherstellen(sys_conn, "benutzer", "merk_erinnern", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(sys_conn, "termin_merkliste", "erinnert", "INTEGER DEFAULT 0")
    # Zahlen an den App-Symbolen: wann Termine bzw. Schwarzes Brett zuletzt geöffnet wurden
    _spalte_sicherstellen(sys_conn, "benutzer", "termine_gesehen_am", "TEXT")
    _spalte_sicherstellen(sys_conn, "benutzer", "pinnwand_gesehen_am", "TEXT")
    # Eigenes Aussehen je Nutzer (JSON: Farbschema, Modus, Schrift, Größe …)
    _spalte_sicherstellen(sys_conn, "benutzer", "design_config", "TEXT")
    # Persönlicher Mail-Absender je Konto (Kommunikation + Versammlung).
    # Das SMTP-Passwort liegt verschlüsselt (Fernet, app/krypto.py).
    for _sp in ("absender_name", "absender_email", "absender_smtp_host",
                "absender_smtp_port", "absender_smtp_security",
                "absender_smtp_user", "absender_smtp_passwort", "absender_wahl"):
        _spalte_sicherstellen(sys_conn, "benutzer", _sp, "TEXT")
    # Persönlicher Bankzugang je Konto: Jede Person im Vorstand meldet sich mit
    # ihren EIGENEN Bankdaten an. PIN und Gerätebindung liegen verschlüsselt
    # (Fernet, app/krypto.py). Das Konto selbst (IBAN/BIC/BLZ/FinTS-Adresse)
    # bleibt beim Verein – es gehört dem Verein, nicht der Person.
    for _sp in ("bank_fints_benutzer", "bank_fints_pin", "bank_fints_produkt_id",
                "bank_fints_client_data"):
        _spalte_sicherstellen(sys_conn, "benutzer", _sp, "TEXT")
    _spalte_sicherstellen(sys_conn, "benutzer", "badge_gesehen_am", "TEXT")
    # Freunde-Selbstregistrierung: Double-Opt-in-Bestätigung
    _spalte_sicherstellen(sys_conn, "benutzer", "reg_token", "TEXT")
    _spalte_sicherstellen(sys_conn, "benutzer", "reg_ablauf", "TEXT")
    # Inaktivitäts-Erinnerung: wann zuletzt "Wir vermissen dich" verschickt wurde
    _spalte_sicherstellen(sys_conn, "benutzer", "inaktiv_erinnert_am", "TEXT")
    # Mitteilungs-Themen: kommagetrennt die ABBESTELLTEN Themen (leer = alle an)
    _spalte_sicherstellen(sys_conn, "benutzer", "push_aus", "TEXT")
    # Persönliche Mail-Signatur – erscheint zusätzlich zur Vereins-Signatur,
    # wenn jemand mit der persönlichen Adresse sendet
    _spalte_sicherstellen(sys_conn, "benutzer", "absender_signatur", "TEXT")
    # „Was ist neu": wann diese Person die Neuigkeiten zuletzt geöffnet hat
    _spalte_sicherstellen(sys_conn, "benutzer", "neuigkeiten_gesehen_am", "TEXT")
    # Content-Hub: Urlaub/Vertretung der Freigebenden (Vorstand/ÖA); die App ist die Quelle
    _spalte_sicherstellen(sys_conn, "benutzer", "abwesend_bis", "TEXT DEFAULT ''")
    _spalte_sicherstellen(sys_conn, "benutzer", "vertretung_id", "INTEGER")
    # Face ID (20.11.2026): Konten mit Nextcloud-Login dürfen Face ID nur bis
    # 90 Tage nach der letzten Nextcloud-Anmeldung nutzen (Rollen bleiben aktuell).
    if "nc_login_am" not in [r["name"] for r in sys_conn.execute("PRAGMA table_info(benutzer)")]:
        sys_conn.execute("ALTER TABLE benutzer ADD COLUMN nc_login_am TEXT")
        sys_conn.execute("UPDATE benutzer SET nc_login_am=letzter_login "
                         "WHERE IFNULL(nc_benutzer,'')!='' AND IFNULL(letzter_login,'')!=''")

    # Doppelte Zugänge verhindern: pro Verein höchstens EIN Konto je E-Mail.
    # Schlägt fehl, falls noch Altlasten-Duplikate existieren – dann greift bis zur
    # Bereinigung weiterhin die E-Mail-Prüfung in nc_benutzer_upsert/benutzer_anlegen.
    try:
        sys_conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_benutzer_email_verein "
                         "ON benutzer (lower(email), IFNULL(verein_id,0)) WHERE email != ''")
    except Exception:  # noqa: BLE001 – Duplikate zuerst bereinigen, dann greift der Index
        pass
    # Login-Bremse: fehlgeschlagene Passwort-Logins (E-Mail + IP, mit Zeitstempel)
    sys_conn.execute("""CREATE TABLE IF NOT EXISTS login_versuche (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT DEFAULT '',
        ip TEXT DEFAULT '',
        zeitpunkt TEXT)""")
    if sys_conn.execute("SELECT id FROM system WHERE id=1").fetchone() is None:
        sys_conn.execute("INSERT INTO system (id, aktiver_verein_id) VALUES (1, NULL)")
    # Nextcloud-Token verschlüsseln (einmalige, idempotente Migration)
    from . import krypto
    krypto.migriere_klartext(sys_conn, "benutzer", ["nc_token"])
    sys_conn.commit()

    anzahl = sys_conn.execute("SELECT COUNT(*) c FROM vereine_index").fetchone()["c"]
    # Einmalige Migration aus der alten gemeinsamen Datenbank
    if anzahl == 0 and os.path.exists(LEGACY_DB_PATH):
        _migriere_legacy(sys_conn)
        anzahl = sys_conn.execute("SELECT COUNT(*) c FROM vereine_index").fetchone()["c"]

    # Falls noch gar kein Verein existiert: Start-Verein anlegen
    if anzahl == 0:
        vid = _neuer_verein_db(sys_conn, "Mein Verein e.V.")
        sys_conn.execute("UPDATE system SET aktiver_verein_id=? WHERE id=1", (vid,))
        sys_conn.commit()

    # Aktiven Verein prüfen und ggf. auf den ersten setzen
    aktiv = sys_conn.execute("SELECT aktiver_verein_id FROM system WHERE id=1").fetchone()["aktiver_verein_id"]
    gueltig = sys_conn.execute("SELECT id FROM vereine_index WHERE id=?", (aktiv,)).fetchone() if aktiv else None
    if not gueltig:
        erster = sys_conn.execute("SELECT id FROM vereine_index ORDER BY id LIMIT 1").fetchone()
        sys_conn.execute("UPDATE system SET aktiver_verein_id=? WHERE id=1",
                         (erster["id"] if erster else None,))
        sys_conn.commit()

    # Schema + Spalten in JEDER Vereins-DB aktuell halten
    for r in sys_conn.execute("SELECT id FROM vereine_index").fetchall():
        conn = _connect(verein_db_pfad(r["id"]))
        try:
            _club_schema_sicherstellen(conn, r["id"])
        finally:
            conn.close()
    sys_conn.close()


def _club_schema_sicherstellen(conn, vid):
    """Schema + fehlende Spalten in EINER Vereins-DB; garantiert die vereine-Zeile."""
    conn.executescript(SCHEMA)
    _alle_spalten_migrationen(conn)
    if conn.execute("SELECT id FROM vereine WHERE id=?", (vid,)).fetchone() is None:
        conn.execute("INSERT INTO vereine (id, vereinsname, erstellt_am) VALUES (?,?,?)",
                     (vid, "", now_iso()))
    conn.commit()


def _neuer_verein_db(sys_conn, name):
    """Registriert einen neuen Verein und legt seine eigene DB an; gibt die id zurück."""
    cur = sys_conn.execute("INSERT INTO vereine_index (vereinsname, erstellt_am) VALUES (?, ?)",
                           (name, now_iso()))
    vid = cur.lastrowid
    sys_conn.commit()
    conn = _connect(verein_db_pfad(vid))
    _club_schema_sicherstellen(conn, vid)
    conn.execute("UPDATE vereine SET vereinsname=? WHERE id=?", (name, vid))
    conn.commit()
    conn.close()
    return vid


def _fremde_vereine_entfernen(conn, vid):
    """Löscht in einer aus der Legacy-DB kopierten Vereins-DB die Daten anderer Vereine."""
    tabellen = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for t in tabellen:
        spalten = [r["name"] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()]
        if "verein_id" in spalten and t != "vereine":
            conn.execute(f"DELETE FROM {t} WHERE verein_id!=?", (vid,))
    conn.execute("DELETE FROM vereine WHERE id!=?", (vid,))
    for kind, (fk, eltern) in _KIND_TABELLEN.items():
        if kind in tabellen and eltern in tabellen:
            conn.execute(f"DELETE FROM {kind} WHERE {fk} NOT IN (SELECT id FROM {eltern})")
    conn.commit()


def _migriere_legacy(sys_conn):
    """Teilt die alte gemeinsame verein.db in je eine DB pro Verein auf."""
    legacy = _connect(LEGACY_DB_PATH)
    try:
        clubs = legacy.execute("SELECT id, vereinsname, erstellt_am FROM vereine ORDER BY id").fetchall()
        aktiv_row = legacy.execute("SELECT aktiver_verein_id FROM system WHERE id=1").fetchone()
    finally:
        legacy.close()
    mehrere = len(clubs) > 1
    for c in clubs:
        vid = c["id"]
        sys_conn.execute("INSERT INTO vereine_index (id, vereinsname, erstellt_am) VALUES (?,?,?)",
                         (vid, c["vereinsname"] or "", c["erstellt_am"] or now_iso()))
        shutil.copyfile(LEGACY_DB_PATH, verein_db_pfad(vid))
        conn = _connect(verein_db_pfad(vid))
        try:
            if mehrere:
                _fremde_vereine_entfernen(conn, vid)
            _club_schema_sicherstellen(conn, vid)
        finally:
            conn.close()
    if aktiv_row and aktiv_row["aktiver_verein_id"]:
        sys_conn.execute("UPDATE system SET aktiver_verein_id=? WHERE id=1",
                         (aktiv_row["aktiver_verein_id"],))
    sys_conn.commit()
    # Alte gemeinsame DB als migriert markieren, damit die Migration nicht erneut läuft
    try:
        os.rename(LEGACY_DB_PATH, LEGACY_DB_PATH + ".migriert")
    except OSError:
        pass


def _alle_spalten_migrationen(conn):
    """Ergänzt in einer Vereins-DB fehlende Spalten (sanfte Migration)."""
    _spalte_sicherstellen(conn, "mitglieder", "verein_id", "INTEGER")
    _spalte_sicherstellen(conn, "gruppen", "verein_id", "INTEGER")
    _spalte_sicherstellen(conn, "vorlagen", "verein_id", "INTEGER")
    _spalte_sicherstellen(conn, "versand", "verein_id", "INTEGER")
    _spalte_sicherstellen(conn, "beitraege", "verein_id", "INTEGER")
    _spalte_sicherstellen(conn, "geldkonten", "bank_saldo", "REAL")
    _spalte_sicherstellen(conn, "geldkonten", "bank_saldo_datum", "TEXT")
    _spalte_sicherstellen(conn, "buchungen", "verein_id", "INTEGER")
    _spalte_sicherstellen(conn, "termine", "verein_id", "INTEGER")
    _spalte_sicherstellen(conn, "buchungen", "beleg_dateiname", "TEXT")
    # Kommunikationswege bei Mitgliedern
    _spalte_sicherstellen(conn, "mitglieder", "komm_genutzt", "TEXT")
    _spalte_sicherstellen(conn, "mitglieder", "komm_erlaubt", "TEXT")
    # Weitere E-Mail-Adressen (eine pro Zeile) – zusätzlich zur Haupt-E-Mail
    _spalte_sicherstellen(conn, "mitglieder", "email_weitere", "TEXT")
    # Wer hat es angelegt? Anzeigename, denormalisiert (benutzer liegt in system.db,
    # ein JOIN wäre unmöglich); Automatiken schreiben ein festes Etikett.
    for _tab in ("mitglieder", "versand", "buchungen", "termine", "aufgaben",
                 "ankuendigungen", "umfragen", "spenden", "schluesselbuch", "pinnwand"):
        _spalte_sicherstellen(conn, _tab, "erstellt_von", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereinsdokumente", "hochgeladen_von", "TEXT DEFAULT ''")
    # Serien-Veranstaltungen: eine Serie erzeugt echte Einzeltermine (voll
    # bearbeitbar, eigene Anmeldung/Schichten), verknüpft über termine.serie_id
    conn.execute("""CREATE TABLE IF NOT EXISTS termin_serien (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER,
        titel TEXT DEFAULT '',
        regel TEXT DEFAULT 'woechentlich',   -- woechentlich | n_wochen | monat_tag | monat_wochentag
        intervall INTEGER DEFAULT 1,         -- alle N Wochen (bei n_wochen)
        ende_am TEXT DEFAULT '',             -- letztes Datum (oder leer)
        anzahl INTEGER DEFAULT 0,            -- ODER: feste Anzahl Termine
        erstellt_am TEXT,
        erstellt_von TEXT DEFAULT ''
    )""")
    _spalte_sicherstellen(conn, "termine", "serie_id", "INTEGER")
    # Nextcloud-Kontakte-Abgleich (CardDAV): Verknüpfung + Änderungserkennung
    _spalte_sicherstellen(conn, "mitglieder", "nc_uid", "TEXT")
    _spalte_sicherstellen(conn, "mitglieder", "nc_etag", "TEXT")
    _spalte_sicherstellen(conn, "mitglieder", "nc_sync_am", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "ncc_adressbuch", "TEXT")
    # FinTS-Bankzugang je Verein
    for spalte in ["fints_blz", "fints_url", "fints_benutzer", "fints_pin",
                   "fints_produkt_id", "konto_bezeichnung",
                   "ncc_server", "ncc_url", "ncc_user", "ncc_pass"]:
        _spalte_sicherstellen(conn, "vereine", spalte, "TEXT")
    _spalte_sicherstellen(conn, "rollen", "beschreibung", "TEXT")
    _spalte_sicherstellen(conn, "schichten", "termin_id", "INTEGER")
    _spalte_sicherstellen(conn, "buchungen", "konto_id", "INTEGER")
    _spalte_sicherstellen(conn, "buchungen", "sphaere", "TEXT")
    _spalte_sicherstellen(conn, "termine", "kategorie_id", "INTEGER")
    _spalte_sicherstellen(conn, "termine", "aktiv", "INTEGER DEFAULT 1")
    _spalte_sicherstellen(conn, "termine", "uhrzeit_bis", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "modul_jubilaeen", "INTEGER DEFAULT 1")
    _spalte_sicherstellen(conn, "vereine", "dash_geburtstage_tage", "INTEGER DEFAULT 30")
    _spalte_sicherstellen(conn, "vereine", "dash_jubilaeen_tage", "INTEGER DEFAULT 365")
    # SEPA-Lastschriftmandat je Mitglied
    _spalte_sicherstellen(conn, "mitglieder", "mandat_vorhanden", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "mitglieder", "mandat_dateiname", "TEXT")
    # Logo-Kreis (Farbe + Ausschnitt/Zoom/Position)
    _spalte_sicherstellen(conn, "vereine", "logo_kreis_farbe", "TEXT DEFAULT '#ffffff'")
    _spalte_sicherstellen(conn, "vereine", "logo_zoom", "INTEGER DEFAULT 100")
    _spalte_sicherstellen(conn, "vereine", "logo_pos_x", "INTEGER DEFAULT 50")
    _spalte_sicherstellen(conn, "vereine", "logo_pos_y", "INTEGER DEFAULT 50")
    # Anpassbares Auswertungs-Layout (JSON: Reihenfolge + versteckte Karten)
    _spalte_sicherstellen(conn, "vereine", "auswertung_config", "TEXT")
    # Anpassbares Dashboard-Layout (JSON: Reihenfolge + versteckt + Größe)
    _spalte_sicherstellen(conn, "vereine", "dashboard_config", "TEXT")
    # Aussehen, das der Verein vorgibt – getrennt für Verwaltung und App
    # (JSON: {"modus":…, "verwaltung":{…}, "app":{…}}). Nutzer dürfen abweichen.
    _spalte_sicherstellen(conn, "vereine", "design_config", "TEXT")
    # Geldkonten (Barkasse/Bank/PayPal) + fortlaufende Belegnummer je Buchung
    _spalte_sicherstellen(conn, "buchungen", "geldkonto_id", "INTEGER")
    # Storno (Gegenbuchung mit Verweis) und Aufteilen (gemeinsame Gruppe)
    _spalte_sicherstellen(conn, "buchungen", "storno_von", "INTEGER")
    _spalte_sicherstellen(conn, "buchungen", "split_gruppe", "TEXT")
    # PayPal-Direktabruf (Transaction Search API)
    _spalte_sicherstellen(conn, "vereine", "paypal_client_id", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "paypal_secret", "TEXT")
    # Wirtschaftsjahr: Stichtag des Beginns (TT.MM., Standard 01.01. = Kalenderjahr)
    _spalte_sicherstellen(conn, "vereine", "wj_beginn", "TEXT")
    # Rechnungsmodul (freie Rechnungen im WISO-Stil)
    conn.execute("""CREATE TABLE IF NOT EXISTS rechnungen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER,
        nummer TEXT DEFAULT '',
        datum TEXT DEFAULT '',
        faellig_am TEXT DEFAULT '',
        empfaenger_name TEXT DEFAULT '',
        empfaenger_strasse TEXT DEFAULT '',
        empfaenger_plz TEXT DEFAULT '',
        empfaenger_ort TEXT DEFAULT '',
        empfaenger_email TEXT DEFAULT '',
        mitglied_id INTEGER,
        betreff TEXT DEFAULT '',
        einleitung TEXT DEFAULT '',
        schluss TEXT DEFAULT '',
        ust_satz REAL DEFAULT 0,
        status TEXT DEFAULT 'offen',        -- offen | bezahlt | storniert
        bezahlt_am TEXT DEFAULT '',
        zahlcode TEXT DEFAULT '',
        token TEXT DEFAULT '',
        buchung_id INTEGER,
        erstellt_am TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS rechnung_positionen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rechnung_id INTEGER NOT NULL REFERENCES rechnungen(id) ON DELETE CASCADE,
        beschreibung TEXT DEFAULT '',
        menge REAL DEFAULT 1,
        einzelpreis REAL DEFAULT 0
    )""")
    # Online-Beitrittsanträge (Mitglied werden)
    conn.execute("""CREATE TABLE IF NOT EXISTS beitrittsantraege (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER,
        anrede TEXT DEFAULT '',
        vorname TEXT DEFAULT '',
        nachname TEXT DEFAULT '',
        geburtsdatum TEXT DEFAULT '',
        strasse TEXT DEFAULT '',
        plz TEXT DEFAULT '',
        ort TEXT DEFAULT '',
        email TEXT DEFAULT '',
        telefon TEXT DEFAULT '',
        mitgliedsart TEXT DEFAULT '',
        iban TEXT DEFAULT '',
        sepa_ok INTEGER DEFAULT 0,
        bemerkung TEXT DEFAULT '',
        status TEXT DEFAULT 'offen',
        erstellt_am TEXT
    )""")
    # Kontaktdatenbank (Mini-CRM): Externe wie Firmen, Sponsoren, Saalmieter
    conn.execute("""CREATE TABLE IF NOT EXISTS kontakte (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER,
        name TEXT DEFAULT '',
        ansprechpartner TEXT DEFAULT '',
        strasse TEXT DEFAULT '',
        plz TEXT DEFAULT '',
        ort TEXT DEFAULT '',
        email TEXT DEFAULT '',
        telefon TEXT DEFAULT '',
        notizen TEXT DEFAULT '',
        kategorie TEXT DEFAULT '',
        erstellt_am TEXT
    )""")
    _spalte_sicherstellen(conn, "rechnungen", "kontakt_id", "INTEGER")
    # Öffentlichkeitsarbeit: Kontakt-Kategorie (z. B. "presse" für den Presseverteiler)
    _spalte_sicherstellen(conn, "kontakte", "kategorie", "TEXT DEFAULT ''")
    # Presseverteiler: Redaktionsschluss je Medium (z. B. "15. des Vormonats") –
    # der Monats-Cron trägt daraus Erinnerungen in den Redaktionsplan ein
    _spalte_sicherstellen(conn, "kontakte", "redaktionsschluss", "TEXT DEFAULT ''")
    # Zugangsdaten für Selbst-Eintrag-Portale (MeinWittich, Lokalportale) –
    # Fernet-verschlüsselt (enc::), Entschlüsselung nur im Bearbeiten-Dialog
    _spalte_sicherstellen(conn, "kontakte", "logindaten", "TEXT DEFAULT ''")
    # Kennzeichen des Import-Laufs – damit sich genau dieser Import wieder
    # zurücknehmen lässt, ohne von Hand angelegte Kontakte zu treffen
    _spalte_sicherstellen(conn, "kontakte", "import_lauf", "TEXT DEFAULT ''")
    # Beitrittsformular: Angaben aus Satzung und Beitragsordnung, damit das
    # öffentliche Formular echte Zahlen nennt statt „der Vorstand meldet sich"
    for _sp, _typ in (("beitrag_normal", "TEXT DEFAULT ''"),
                      ("beitrag_ermaessigt", "TEXT DEFAULT ''"),
                      ("beitrag_intervall", "TEXT DEFAULT 'jährlich'"),
                      ("beitrag_hinweis", "TEXT DEFAULT ''"),
                      ("satzung_url", "TEXT DEFAULT ''"),
                      ("beitragsordnung_url", "TEXT DEFAULT ''"),
                      ("antwort_stunden", "TEXT DEFAULT '48'"),
                      ("angebote", "TEXT DEFAULT ''"),
                      ("werkhaus_adresse", "TEXT DEFAULT ''")):
        _spalte_sicherstellen(conn, "vereine", _sp, _typ)
    # Beitrittsanträge: Felder der amtlichen Beitrittserklärung ergänzen
    for _sp in ("organisation", "beitragsart", "kontoinhaber", "bic",
                "zahlungsweg", "vertreter"):
        _spalte_sicherstellen(conn, "beitrittsantraege", _sp, "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "beitrittsantraege", "satzung_ok", "INTEGER DEFAULT 0")
    # Mitgliedsausweis: Was steht auf dem Ausweis, wenn kein Foto hinterlegt ist?
    # 'initialen' | 'symbol' | 'logo' – jeder Verein entscheidet das selbst.
    _spalte_sicherstellen(conn, "vereine", "ausweis_platzhalter", "TEXT DEFAULT 'initialen'")
    _spalte_sicherstellen(conn, "vereine", "ausweis_symbol", "TEXT DEFAULT ''")
    # Öffentlichkeitsarbeit: Redaktionsplan (Social-Media-Beiträge + Pressemitteilungen)
    conn.execute("""CREATE TABLE IF NOT EXISTS oea_beitraege (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER,
        typ TEXT DEFAULT 'social',
        kanal TEXT DEFAULT '',
        titel TEXT DEFAULT '',
        text TEXT DEFAULT '',
        termin_id INTEGER,
        geplant_am TEXT DEFAULT '',
        status TEXT DEFAULT 'entwurf',
        notiz TEXT DEFAULT '',
        erstellt_von TEXT DEFAULT '',
        erstellt_am TEXT,
        freigegeben_von TEXT DEFAULT '',
        freigegeben_am TEXT DEFAULT '',
        veroeffentlicht_am TEXT DEFAULT ''
    )""")
    # Veranstaltung: "Pressemitteilung gewünscht"-Haken beim Anlegen
    _spalte_sicherstellen(conn, "termine", "presse_wunsch", "INTEGER DEFAULT 0")
    # Instagram-Anbindung (Meta Graph API): Seiten-Token verschlüsselt
    for feld in ("ig_token", "ig_app_id", "ig_app_secret", "ig_user_id", "ig_konto"):
        _spalte_sicherstellen(conn, "vereine", feld, "TEXT DEFAULT ''")
    # Veröffentlichte Instagram-Beiträge: Kennung für Kommentar-Abruf
    _spalte_sicherstellen(conn, "oea_beitraege", "ig_media_id", "TEXT DEFAULT ''")
    # Instagram braucht ein Bild: gewaehltes Galerie-Foto (0 = Veranstaltungsbild)
    _spalte_sicherstellen(conn, "oea_beitraege", "bild_foto_id", "INTEGER")
    # Stufe 3 – Zeitsteuerung: Uhrzeit + Schalter fuer automatisches Veroeffentlichen
    _spalte_sicherstellen(conn, "oea_beitraege", "geplant_um", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "oea_beitraege", "auto_posten", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "oea_beitraege", "auto_fehler", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "oea_beitraege", "auto_versuche", "INTEGER DEFAULT 0")
    # Stufe 3 – Auswertung: Kennzahlen des veroeffentlichten Instagram-Beitrags
    for feld in ("ig_reichweite", "ig_likes", "ig_kommentare_n", "ig_gemerkt"):
        _spalte_sicherstellen(conn, "oea_beitraege", feld, "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "oea_beitraege", "ig_stats_am", "TEXT DEFAULT ''")
    # Einlass läuft: Startzeit des Check-ins am Termin (leer = nicht eröffnet);
    # wird beim ersten Check-in automatisch gesetzt und ist öffentlich sichtbar
    _spalte_sicherstellen(conn, "termine", "einlass_start", "TEXT DEFAULT ''")
    # Veranstaltungs-Komfort (Vorbild Eventbrite): Mitgliederpreis, Storno-Frist,
    # Nachrück-Modus je Termin; Nachrück-Angebote und Gutscheine je Anmeldung
    _spalte_sicherstellen(conn, "termine", "preis_mitglied", "REAL")
    _spalte_sicherstellen(conn, "termine", "storno_frist_stunden", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "termine", "nachrueck_modus", "TEXT DEFAULT 'auto'")
    _spalte_sicherstellen(conn, "veranstaltung_anmeldung", "nachrueck_bis", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "veranstaltung_anmeldung", "nachrueck_verpasst", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "veranstaltung_anmeldung", "gutschein_code", "TEXT DEFAULT ''")
    # Der reguläre Einzelpreis zum Anmeldezeitpunkt (Basis für Nachmeldungen –
    # NIE aus dem rabattierten Betrag zurückrechnen)
    _spalte_sicherstellen(conn, "veranstaltung_anmeldung", "einzelpreis", "REAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS veranstaltung_gutscheine (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER,
        code TEXT NOT NULL,                  -- Eingabe am Anmeldeformular
        termin_id INTEGER,                   -- leer = gilt für alle Veranstaltungen
        art TEXT DEFAULT 'frei',             -- 'frei' | 'betrag' | 'prozent'
        wert REAL DEFAULT 0,
        max_nutzung INTEGER DEFAULT 0,       -- 0 = unbegrenzt
        genutzt INTEGER DEFAULT 0,
        aktiv INTEGER DEFAULT 1,
        erstellt_am TEXT
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_va_gutschein "
                 "ON veranstaltung_gutscheine(verein_id, code)")
    # Belegungs-Zählungen (Anmeldeseite, Listen, Adventskalender) laufen ständig
    conn.execute("CREATE INDEX IF NOT EXISTS ix_va_anmeldung_termin ON veranstaltung_anmeldung(termin_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_va_teilnehmer_anmeldung ON veranstaltung_teilnehmer(anmeldung_id)")
    # Einlass-Check-in: wann und von wem eine Person eingecheckt wurde
    _spalte_sicherstellen(conn, "veranstaltung_teilnehmer", "checkin_am", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "veranstaltung_teilnehmer", "checkin_von", "TEXT DEFAULT ''")
    # Presse-PDF: Bildunterschrift + Bildnachweis je Beitrag (leer = Automatik)
    _spalte_sicherstellen(conn, "oea_beitraege", "bild_unterschrift", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "oea_beitraege", "bild_copyright", "TEXT DEFAULT ''")
    # Instagram-Kommentare (Postfach); UNIQUE verhindert Doppel-Import
    conn.execute("""CREATE TABLE IF NOT EXISTS oea_kommentare (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, beitrag_id INTEGER,
        ig_comment_id TEXT, ig_media_id TEXT,
        von TEXT DEFAULT '', text TEXT DEFAULT '',
        erstellt_am TEXT, geholt_am TEXT,
        antwort_text TEXT DEFAULT '', beantwortet_am TEXT DEFAULT '',
        erledigt INTEGER DEFAULT 0,
        UNIQUE (ig_comment_id)
    )""")
    # Medienecho: gefundene Online-Veröffentlichungen zu Pressemitteilungen
    # (Cron sucht alle 12 h; UNIQUE verhindert doppelte Fundstellen)
    conn.execute("""CREATE TABLE IF NOT EXISTS oea_fundstellen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, beitrag_id INTEGER,
        url TEXT, titel TEXT DEFAULT '', gefunden_am TEXT,
        UNIQUE (beitrag_id, url)
    )""")
    # Pressefach: Stammtext ("Über den Verein") + Pressekontakt für Mitteilungen
    _spalte_sicherstellen(conn, "vereine", "presse_boilerplate", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "presse_kontakt", "TEXT DEFAULT ''")
    # Interne Kopie jedes Presseversands an diese Adresse (leer = keine Kopie)
    _spalte_sicherstellen(conn, "vereine", "presse_kopie_email", "TEXT DEFAULT ''")
    # Fördermittel: Fördergeber-Datenbank (Recherche) + eigene Anträge/Projekte
    conn.execute("""CREATE TABLE IF NOT EXISTS foerdergeber (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, name TEXT DEFAULT '', traeger TEXT DEFAULT '',
        url TEXT DEFAULT '', was TEXT DEFAULT '', bereiche TEXT DEFAULT '',
        summe_von REAL DEFAULT 0, summe_bis REAL DEFAULT 0, eigenanteil TEXT DEFAULT '',
        fristen TEXT DEFAULT '', zielgruppe TEXT DEFAULT '', region TEXT DEFAULT '',
        kontakt TEXT DEFAULT '', notiz TEXT DEFAULT '', favorit INTEGER DEFAULT 0,
        aktiv INTEGER DEFAULT 1, quelle TEXT DEFAULT '', erstellt_am TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS foerderprojekte (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, titel TEXT DEFAULT '', foerdergeber_id INTEGER,
        geber_name TEXT DEFAULT '', status TEXT DEFAULT 'idee',
        beschreibung TEXT DEFAULT '', aktenzeichen TEXT DEFAULT '',
        antragssumme REAL DEFAULT 0, bewilligt REAL DEFAULT 0, eigenanteil REAL DEFAULT 0,
        gesamtkosten REAL DEFAULT 0,
        antragsfrist TEXT DEFAULT '', eingereicht_am TEXT DEFAULT '', bescheid_am TEXT DEFAULT '',
        projekt_von TEXT DEFAULT '', projekt_bis TEXT DEFAULT '',
        nachweis_bis TEXT DEFAULT '', nachweis_am TEXT DEFAULT '',
        ansprechpartner TEXT DEFAULT '', sachbericht TEXT DEFAULT '', notiz TEXT DEFAULT '',
        erinnert_am TEXT DEFAULT '', erstellt_am TEXT
    )""")
    # Kassenbuch-Buchungen einem Förderprojekt zuordnen (Grundlage Verwendungsnachweis)
    _spalte_sicherstellen(conn, "buchungen", "foerderprojekt_id", "INTEGER")
    # Versions-Flag für die einmalige Startbestückung der Fördertopf-Liste
    _spalte_sicherstellen(conn, "vereine", "foerder_seed", "INTEGER DEFAULT 0")
    # Mehrstufige Fristerinnerung (30/14/3 Tage vorher, danach wöchentlich)
    _spalte_sicherstellen(conn, "foerderprojekte", "erinnert_stufe", "INTEGER DEFAULT 99")
    # Finanzierungsart bestimmt, was bei Minderausgaben passiert (Anteil-, Fehlbedarfs-,
    # Festbetrags-, Vollfinanzierung) – gehört in jeden Verwendungsnachweis
    _spalte_sicherstellen(conn, "foerderprojekte", "finanzierungsart", "TEXT DEFAULT ''")
    # Datum der schriftlichen Zustimmung zum vorzeitigen Maßnahmenbeginn (falls erteilt)
    _spalte_sicherstellen(conn, "foerderprojekte", "vmb_am", "TEXT DEFAULT ''")
    # Crowdfunding: Spendenkampagnen mit Ziel und öffentlicher Seite
    conn.execute("""CREATE TABLE IF NOT EXISTS kampagnen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, titel TEXT DEFAULT '', kurztext TEXT DEFAULT '',
        text TEXT DEFAULT '', ziel REAL DEFAULT 0, start_datum TEXT DEFAULT '',
        ende_datum TEXT DEFAULT '', bild TEXT DEFAULT '', token TEXT DEFAULT '',
        aktiv INTEGER DEFAULT 1, konto_id INTEGER, erstellt_am TEXT
    )""")
    _spalte_sicherstellen(conn, "spenden", "kampagne_id", "INTEGER")
    _spalte_sicherstellen(conn, "dauerspenden", "kampagne_id", "INTEGER")
    # Ablage unterschriebener Zuwendungsbestätigungen (je Spender:in und Jahr)
    conn.execute("""CREATE TABLE IF NOT EXISTS spenden_dokumente (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, jahr TEXT DEFAULT '',
        mitglied_id INTEGER, spender_name TEXT DEFAULT '',
        dateiname TEXT DEFAULT '', original_name TEXT DEFAULT '',
        hochgeladen_am TEXT
    )""")
    # Mitgliederversammlung: Versammlung, Anwesenheit, Beschlüsse/Wahlen
    conn.execute("""CREATE TABLE IF NOT EXISTS versammlungen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, titel TEXT DEFAULT '', art TEXT DEFAULT 'mv',
        datum TEXT DEFAULT '', uhrzeit TEXT DEFAULT '', ort TEXT DEFAULT '',
        tagesordnung TEXT DEFAULT '', einladung_am TEXT DEFAULT '', frist_tage INTEGER DEFAULT 14,
        leitung TEXT DEFAULT '', protokoll TEXT DEFAULT '', protokoll_fuehrung TEXT DEFAULT '',
        stimmberechtigt INTEGER DEFAULT 0, status TEXT DEFAULT 'geplant',
        termin_id INTEGER, erstellt_am TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS versammlung_anwesend (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        versammlung_id INTEGER REFERENCES versammlungen(id) ON DELETE CASCADE,
        mitglied_id INTEGER, name TEXT DEFAULT '', gast INTEGER DEFAULT 0,
        vollmacht_von INTEGER, erfasst_am TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS versammlung_beschluesse (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        versammlung_id INTEGER REFERENCES versammlungen(id) ON DELETE CASCADE,
        nummer INTEGER DEFAULT 1, art TEXT DEFAULT 'beschluss', titel TEXT DEFAULT '',
        text TEXT DEFAULT '', ja INTEGER DEFAULT 0, nein INTEGER DEFAULT 0,
        enthaltung INTEGER DEFAULT 0, ergebnis TEXT DEFAULT '', notiz TEXT DEFAULT ''
    )""")
    # Fristen-Radar: Verträge, Bescheide, Mandate mit Ablauf-/Kündigungsfristen
    conn.execute("""CREATE TABLE IF NOT EXISTS fristen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, art TEXT DEFAULT 'vertrag', titel TEXT DEFAULT '',
        partner TEXT DEFAULT '', ablauf_am TEXT DEFAULT '', kuendigungsfrist_tage INTEGER DEFAULT 0,
        vorwarnung_tage INTEGER DEFAULT 30, betrag REAL DEFAULT 0, intervall TEXT DEFAULT '',
        notiz TEXT DEFAULT '', dokument TEXT DEFAULT '', erledigt INTEGER DEFAULT 0,
        erinnert_am TEXT DEFAULT '', erstellt_am TEXT
    )""")
    # Austritt: Kündigungsdaten je Mitglied + Satzungsfrist je Verein
    _spalte_sicherstellen(conn, "mitglieder", "kuendigung_am", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "mitglieder", "kuendigung_grund", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "austritt_frist_tage", "INTEGER DEFAULT 90")
    _spalte_sicherstellen(conn, "vereine", "austritt_zum", "TEXT DEFAULT 'jahresende'")
    # Mahnwesen: Stufe (0 = noch nichts, 1 = Erinnerung, 2 = 1. Mahnung, 3 = 2. Mahnung)
    _spalte_sicherstellen(conn, "rechnungen", "mahnstufe", "INTEGER DEFAULT 0")
    # Mahnautomatik je Verein: Opt-in + Tage nach Fälligkeit je Stufe
    _spalte_sicherstellen(conn, "vereine", "mahn_auto", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "vereine", "mahn_tage1", "INTEGER DEFAULT 7")
    _spalte_sicherstellen(conn, "vereine", "mahn_tage2", "INTEGER DEFAULT 21")
    _spalte_sicherstellen(conn, "vereine", "mahn_tage3", "INTEGER DEFAULT 35")
    # Haushaltsplan: geplanter Betrag je Konto und Wirtschaftsjahr (Plan-Ist-Vergleich)
    conn.execute("""CREATE TABLE IF NOT EXISTS budget (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, wj_jahr INTEGER, konto_id INTEGER,
        plan_betrag REAL DEFAULT 0, notiz TEXT DEFAULT '', erstellt_am TEXT,
        UNIQUE (verein_id, wj_jahr, konto_id)
    )""")
    # Rücklagen nach § 62 AO (freie und zweckgebundene Rücklagen dokumentieren)
    conn.execute("""CREATE TABLE IF NOT EXISTS ruecklagen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, art TEXT DEFAULT 'zweck', bezeichnung TEXT DEFAULT '',
        betrag REAL DEFAULT 0, gebildet_am TEXT DEFAULT '', wj_jahr INTEGER,
        verwendung_bis TEXT DEFAULT '', aufgeloest_am TEXT DEFAULT '',
        aufgeloest_betrag REAL DEFAULT 0, zweck TEXT DEFAULT '', notiz TEXT DEFAULT '',
        erstellt_am TEXT
    )""")
    # Schicht-Positionen können eine eigene Uhrzeit haben (z. B. Catering 18–20 Uhr,
    # Aufbau 16–18 Uhr innerhalb derselben Veranstaltung); leer = Zeit der Schicht
    _spalte_sicherstellen(conn, "schicht_slots", "zeit_von", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "schicht_slots", "zeit_bis", "TEXT DEFAULT ''")
    # Voucher: editierbare Textbausteine (statt hartcodiert) – Einlasshinweis + Fußzeile
    _spalte_sicherstellen(conn, "vereine", "voucher_einlass_text", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "voucher_fusszeile", "TEXT DEFAULT ''")
    # Vorlagen-Ordnung: Art (intern/extern) + frei benennbare Kategorie je Vorlage;
    # die Kategorienlisten selbst pflegt der Verein (vereine.vorlagen_kat_*)
    _spalte_sicherstellen(conn, "vorlagen", "art", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vorlagen", "kategorie", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "vorlagen_kat_intern", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "vorlagen_kat_extern", "TEXT DEFAULT ''")
    try:  # Bestands-Vorlagen einmalig einordnen (nur solange art leer ist)
        from . import vorlagen_texte as _vtz
        for _nm, (_art, _kat) in _vtz.STANDARD_ZUORDNUNG.items():
            conn.execute("UPDATE vorlagen SET art=?, kategorie=? "
                         "WHERE name=? AND COALESCE(art,'')=''", (_art, _kat, _nm))
    except Exception:  # noqa: BLE001
        pass
    # Eingebaute Standard-Mailvorlagen einmalig je Verein säen (Versions-Flag,
    # damit vom Verein gelöschte Vorlagen NICHT wieder auftauchen)
    _spalte_sicherstellen(conn, "vereine", "vorlagen_seed", "INTEGER DEFAULT 0")
    try:
        from . import vorlagen_texte as _vt
        _app_url = os.environ.get("VV_BASIS_URL", "").rstrip("/")
        for _v in conn.execute("SELECT id, vorlagen_seed, logo_dateiname FROM vereine").fetchall():
            if (_v["vorlagen_seed"] or 0) >= _vt.SEED_VERSION:
                continue
            for _name, _betreff, _inhalt in _vt.alle(_v["id"], bool(_v["logo_dateiname"]), _app_url):
                _da = conn.execute("SELECT 1 FROM vorlagen WHERE verein_id=? AND name=?",
                                   (_v["id"], _name)).fetchone()
                if not _da:
                    _art, _kat = _vt.STANDARD_ZUORDNUNG.get(_name, ("", ""))
                    conn.execute("INSERT INTO vorlagen (verein_id, name, betreff, inhalt, art, kategorie, erstellt_am) "
                                 "VALUES (?,?,?,?,?,?,?)",
                                 (_v["id"], _name, _betreff, _inhalt, _art, _kat, now_iso()))
            conn.execute("UPDATE vereine SET vorlagen_seed=? WHERE id=?", (_vt.SEED_VERSION, _v["id"]))
    except Exception:  # noqa: BLE001 – Seed darf den App-Start nie blockieren
        logging.getLogger("db").exception("Vorlagen-Seed fehlgeschlagen")
    # Rechnungen: Leistungsdatum (§ 14 UStG) + Mahnwesen
    _spalte_sicherstellen(conn, "rechnungen", "leistung_datum", "TEXT")
    _spalte_sicherstellen(conn, "rechnungen", "gemahnt_am", "TEXT")
    # Spenden-Zentrale: Lastschrift-Daten je Dauerspende + Verknüpfung zum Kassenbuch
    _spalte_sicherstellen(conn, "dauerspenden", "zahlungsart", "TEXT DEFAULT 'ueberweisung'")
    for feld in ("iban", "bic", "kontoinhaber", "mandatsreferenz", "mandatsdatum"):
        _spalte_sicherstellen(conn, "dauerspenden", feld, "TEXT")
    _spalte_sicherstellen(conn, "spenden", "buchung_id", "INTEGER")
    # Nächtlicher automatischer Bankabruf (Opt-in je Verein)
    _spalte_sicherstellen(conn, "vereine", "bank_auto", "INTEGER DEFAULT 0")
    # Wessen persönlicher Bankzugang den Nachtlauf trägt (benutzer.id in der System-DB).
    # Ohne Eintrag läuft nichts – der Nachtlauf hat keine angemeldete Person.
    _spalte_sicherstellen(conn, "vereine", "bank_auto_benutzer_id", "INTEGER")
    # FinTS: gespeicherter Client-Zustand (Kundensystem-ID), damit die Bank
    # das "Gerät" wiedererkennt statt bei jedem Abruf ein neues anzulegen
    _spalte_sicherstellen(conn, "vereine", "fints_client_data", "TEXT")
    _spalte_sicherstellen(conn, "buchungen", "belegnr", "INTEGER")
    # Import-Kennung (Dedup für Bank-/PayPal-Umsätze)
    _spalte_sicherstellen(conn, "buchungen", "import_ref", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_buchungen_import_ref "
                 "ON buchungen(verein_id, import_ref)")
    # Mitglieder-App: Nuki-Türöffner:in-Link + optionaler Willkommenstext
    _spalte_sicherstellen(conn, "vereine", "tuer_url", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "portal_willkommen", "TEXT")
    # Nuki Web API: verschlüsselter Token + ausgewählte Tür (Smartlock)
    _spalte_sicherstellen(conn, "vereine", "nuki_token", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "nuki_smartlock_id", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "nuki_smartlock_name", "TEXT DEFAULT ''")
    # Protokoll aller Türaktionen (wer, wann, Erfolg) – Vorbild veranstaltung_mailprotokoll
    conn.execute("""CREATE TABLE IF NOT EXISTS tuer_protokoll (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, benutzer_id INTEGER, mitglied_id INTEGER,
        name TEXT DEFAULT '', aktion TEXT DEFAULT '',
        ok INTEGER DEFAULT 1, meldung TEXT DEFAULT '', zeitpunkt TEXT
    )""")
    # Öffentliche Adresse der Mitglieder-App (für QR-Code auf dem Ausweis)
    _spalte_sicherstellen(conn, "vereine", "app_url", "TEXT")
    # Abonnier-Token für Kalender-/Aufgaben-Feeds (ICS)
    _spalte_sicherstellen(conn, "vereine", "feed_token", "TEXT")
    # E-Mail-Ausbau: Standardschrift + zentrale Signatur
    _spalte_sicherstellen(conn, "vereine", "mail_schriftart", "TEXT DEFAULT 'Arial'")
    _spalte_sicherstellen(conn, "vereine", "signatur_aktiv", "INTEGER DEFAULT 1")
    _spalte_sicherstellen(conn, "vereine", "signatur_text", "TEXT")
    # Impressum & Datenschutz (eigene Seite je Verein; externer Link optional)
    _spalte_sicherstellen(conn, "vereine", "datenschutz_url", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "datenschutz_verantwortlich", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "rechtliches_zusatz", "TEXT")
    # Dokument für Mitglieder in der App sichtbar?
    _spalte_sicherstellen(conn, "vereinsdokumente", "fuer_mitglieder", "INTEGER DEFAULT 0")
    # Mitglieder-App Paket 2/3: Schicht-Tausch/-Abgabe + Erinnerung
    _spalte_sicherstellen(conn, "schicht_slots", "tausch_gesucht", "INTEGER DEFAULT 0")
    # erinnerung = Vorlaufzeit in Stunden (0 = aus); erinnerung_gesendet = Push schon raus?
    _spalte_sicherstellen(conn, "schicht_slots", "erinnerung", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "schicht_slots", "erinnerung_gesendet", "INTEGER DEFAULT 0")
    # Profil: Avatar + Einwilligungen (Lastschrift/DSGVO/Kontakt)
    _spalte_sicherstellen(conn, "mitglieder", "avatar_dateiname", "TEXT")
    _spalte_sicherstellen(conn, "mitglieder", "sepa_ok", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "mitglieder", "dsgvo_ok", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "mitglieder", "kontakt_ok", "INTEGER DEFAULT 0")
    # Mitglieder-Startseite anpassbar (JSON: Reihenfolge + versteckte Kacheln)
    _spalte_sicherstellen(conn, "mitglieder", "portal_config", "TEXT")
    # Abwesenheitsnotiz je Mitglied (selbst oder vom Vorstand gesetzt)
    _spalte_sicherstellen(conn, "mitglieder", "abwesend", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "mitglieder", "abwesend_von", "TEXT")
    _spalte_sicherstellen(conn, "mitglieder", "abwesend_bis", "TEXT")
    _spalte_sicherstellen(conn, "mitglieder", "abwesend_notiz", "TEXT")
    # Schicht-Nachbereitung (Besucher:innen/Einnahmen/Fragen) je Termin
    conn.execute("""CREATE TABLE IF NOT EXISTS rueckmeldungen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, termin_id INTEGER, schicht_id INTEGER, mitglied_id INTEGER,
        besucher INTEGER, einnahmen TEXT, fragen TEXT, nachricht TEXT, erstellt_am TEXT
    )""")
    _spalte_sicherstellen(conn, "rueckmeldungen", "gelesen", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "rueckmeldungen", "antwort", "TEXT")
    _spalte_sicherstellen(conn, "rueckmeldungen", "antwort_am", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "rueck_besucher_tage", "INTEGER DEFAULT 30")
    # Aufgaben (einmalig/wiederkehrend, an Mitglieder delegierbar, Vorab-Benachrichtigung)
    conn.execute("""CREATE TABLE IF NOT EXISTS aufgaben (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, titel TEXT, beschreibung TEXT,
        faellig_am TEXT, faellig_zeit TEXT,
        wiederholung TEXT DEFAULT 'einmalig',
        vorlauf_min INTEGER DEFAULT 60,
        mitglied_id INTEGER,
        erledigt INTEGER DEFAULT 0, erledigt_am TEXT,
        benachrichtigt INTEGER DEFAULT 0, erstellt_am TEXT
    )""")
    # '' = Vorstand bzw. einzelne Person (mitglied_id); 'mitglieder' = alle Mitglieder
    _spalte_sicherstellen(conn, "aufgaben", "zielgruppe", "TEXT DEFAULT ''")
    # Die Sammelbuchung eines verbuchten Lastschriftlaufs (nachträglich ergänzt)
    _spalte_sicherstellen(conn, "sepa_laeufe", "buchung_id", "INTEGER")
    # Umfragen / Terminfindung / Abstimmungen
    conn.execute("""CREATE TABLE IF NOT EXISTS umfragen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, titel TEXT, beschreibung TEXT,
        typ TEXT DEFAULT 'auswahl',        -- 'termin' oder 'auswahl'
        mehrfach INTEGER DEFAULT 0, anonym INTEGER DEFAULT 0, aktiv INTEGER DEFAULT 1,
        ende_am TEXT, erstellt_am TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS umfrage_optionen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        umfrage_id INTEGER, text TEXT, sortierung INTEGER DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS umfrage_stimmen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        umfrage_id INTEGER, option_id INTEGER, mitglied_id INTEGER, wert TEXT, erstellt_am TEXT
    )""")
    # RSVP „Ich komme" zu Veranstaltungen
    conn.execute("""CREATE TABLE IF NOT EXISTS termin_rsvp (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, termin_id INTEGER, mitglied_id INTEGER,
        status TEXT, gaeste INTEGER DEFAULT 0, erstellt_am TEXT
    )""")
    # Abzeichen (bereits verliehene, als JSON) für Push-Glückwünsche
    _spalte_sicherstellen(conn, "mitglieder", "abzeichen", "TEXT")
    # Schwarzes Brett / Helfer-Gesuche
    conn.execute("""CREATE TABLE IF NOT EXISTS pinnwand (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, titel TEXT, text TEXT, typ TEXT DEFAULT 'gesuch',
        mitglied_id INTEGER, aktiv INTEGER DEFAULT 1, erstellt_am TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pinnwand_antworten (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pinnwand_id INTEGER, mitglied_id INTEGER, erstellt_am TEXT
    )""")
    # Ankündigungen / News
    conn.execute("""CREATE TABLE IF NOT EXISTS ankuendigungen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, titel TEXT, text TEXT, aktiv INTEGER DEFAULT 1, erstellt_am TEXT,
        oeffentlich INTEGER DEFAULT 1
    )""")
    # Ankündigungen: öffentlich (auch Gäste/Freunde) oder nur intern (Mitglieder/Vorstand).
    # Bestehende Einträge bleiben öffentlich (bisheriges Verhalten), neue wählt das Formular.
    _spalte_sicherstellen(conn, "ankuendigungen", "oeffentlich", "INTEGER DEFAULT 1")
    # Planbare Ankündigungen: Anzeigefenster von/bis (leer = sofort/unbegrenzt);
    # push_gesendet=1 sobald die Push-Benachrichtigung raus ist (geplante pusht der Cron).
    _spalte_sicherstellen(conn, "ankuendigungen", "sichtbar_von", "TEXT")
    _spalte_sicherstellen(conn, "ankuendigungen", "sichtbar_bis", "TEXT")
    _spalte_sicherstellen(conn, "ankuendigungen", "push_gesendet", "INTEGER DEFAULT 1")
    # Signage (21.11.2026): eigene Folien des Vereins – Bild (hochgeladen) oder Text (in der Verwaltung geschrieben).
    # geraete leer = alle Schaufenster, sonst Komma-Liste von signage_geraete.id (system.db); dauer 0 = wie das Gerät.
    conn.execute("""CREATE TABLE IF NOT EXISTS signage_folien (
        id INTEGER PRIMARY KEY AUTOINCREMENT, verein_id INTEGER NOT NULL,
        typ TEXT DEFAULT 'text', titel TEXT DEFAULT '', text TEXT DEFAULT '', bild TEXT DEFAULT '',
        link TEXT DEFAULT '', hintergrund TEXT DEFAULT 'hell', einpassen TEXT DEFAULT 'ganz',
        dauer INTEGER DEFAULT 0, von TEXT DEFAULT '', bis TEXT DEFAULT '', geraete TEXT DEFAULT '',
        aktiv INTEGER DEFAULT 1, sortierung INTEGER DEFAULT 0, erstellt_am TEXT, geaendert_am TEXT
    )""")
    # Signage-Folienplan (21.11.2026): Reihenfolge, Sichtbarkeit (Auge) und Dauer JEDER Folienart
    # je Verein – Schlüssel wie „gruss“, „termine“, „haus:social“, „folie:12“ (eigene Folien)
    conn.execute("""CREATE TABLE IF NOT EXISTS signage_plan (
        verein_id INTEGER NOT NULL, schluessel TEXT NOT NULL, sortierung INTEGER DEFAULT 0,
        sichtbar INTEGER DEFAULT 1, dauer INTEGER DEFAULT 0, PRIMARY KEY (verein_id, schluessel)
    )""")
    # FAQ / Infos
    conn.execute("""CREATE TABLE IF NOT EXISTS faq (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, frage TEXT, antwort TEXT, sortierung INTEGER DEFAULT 0, aktiv INTEGER DEFAULT 1
    )""")
    # Geburtstag anderen Mitgliedern zeigen (Opt-in)
    _spalte_sicherstellen(conn, "mitglieder", "geb_sichtbar", "INTEGER DEFAULT 0")
    # Kino-Programm: Trailer-Link je Termin
    _spalte_sicherstellen(conn, "termine", "trailer_url", "TEXT")
    # Hinweise zum Veranstaltungsort (Anfahrt, Eingang, Parken) – auf Anmeldung & Voucher
    _spalte_sicherstellen(conn, "termine", "ort_hinweise", "TEXT")
    # Mitwirkende (Referent:in, Band, Ensemble …) – öffentlich und als schema.org-performer
    _spalte_sicherstellen(conn, "termine", "mitwirkende", "TEXT DEFAULT ''")
    # Highlights (19.11.2026): oben in der Terminliste, auf der App-Startseite, auf der Website
    _spalte_sicherstellen(conn, "termine", "highlight", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "termine", "highlight_text", "TEXT DEFAULT ''")
    # Terminliste „schnell verstehen“ (19.11.2026): Eintritt-Art, Zielgruppen, Standardbild je Kategorie
    _spalte_sicherstellen(conn, "termine", "eintritt_art", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "termine", "zielgruppen", "TEXT DEFAULT ''")
    # Faktenkasten der Terminseite: „Einlass ab“ (HH:MM, optional) – auch als doorTime an Google
    _spalte_sicherstellen(conn, "termine", "einlass_ab", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "termine", "gut_zu_wissen", "TEXT DEFAULT ''")
    # Terminseite (21.11.2026): Draußen-Termine bekommen eine Wettervorhersage
    _spalte_sicherstellen(conn, "termine", "draussen", "INTEGER DEFAULT 0")
    # Kurztext/Teaser (21.11.2026): ein Satz, was beim Termin passiert – Signage-Bildschirme; leer = aus der Beschreibung
    _spalte_sicherstellen(conn, "termine", "teaser", "TEXT DEFAULT ''")
    # Signage: eigenes Foto für die Folie „Räume mieten“ (leer = Folie ohne Foto)
    _spalte_sicherstellen(conn, "vereine", "signage_mieten_bild", "TEXT DEFAULT ''")
    # Hausfolien als Einstellung statt Erkennung über den Vereinsnamen (22.11.2026)
    _spalte_sicherstellen(conn, "vereine", "signage_mieten", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "vereine", "kino_name", "TEXT DEFAULT ''")      # z. B. „Stadtkino“ (leer = „Kino-Programm“)
    _spalte_sicherstellen(conn, "vereine", "feier_symbol", "TEXT DEFAULT ''")   # Erledigt-Animation: stern|biene|funkeln
    _spalte_sicherstellen(conn, "vereine", "signage_mieten_titel", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "signage_spenden_url", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "signage_spenden_titel", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "signage_spenden_text", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "signage_werte", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "signage_verein_fuss", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "signage_lak_motiv", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "lak_erste_saison", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "termin_kategorien", "bild", "TEXT DEFAULT ''")
    # Gruppe für die Filterknöpfe der Terminliste (z. B. Doppelkopf + Skat + SpieleZeit = „Spiele“)
    _spalte_sicherstellen(conn, "termin_kategorien", "gruppe", "TEXT DEFAULT ''")
    # Anmeldung: Interesse an regelmäßiger Unterstützung (WerkHausStern/Förderung)
    _spalte_sicherstellen(conn, "veranstaltung_anmeldung", "stern_interesse", "INTEGER DEFAULT 0")
    # Bezahlen Stufe 2: eindeutiger Zahlcode je Anmeldung (Verwendungszweck-Abgleich)
    _spalte_sicherstellen(conn, "veranstaltung_anmeldung", "zahlcode", "TEXT")
    # Fotoerlaubnis mit drei Zuständen: '' = nie gefragt/nicht geantwortet, 'ja', 'nein'.
    # Das alte foto_ok (0/1) konnte „abgelehnt" nicht von „nie geantwortet" unterscheiden –
    # beim Fotografieren ein wichtiger Unterschied. foto_ok bleibt als Spiegel erhalten.
    # foto_social: eigene Einwilligung für Facebook/Instagram (weltweit abrufbar,
    # Drittlandübermittlung) – die deckt eine allgemeine Foto-Zustimmung nicht ab.
    _spalte_sicherstellen(conn, "veranstaltung_anmeldung", "foto_status", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "veranstaltung_anmeldung", "foto_social", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "veranstaltung_anmeldung", "foto_antwort_am", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "veranstaltung_anmeldung", "foto_angefragt_am", "TEXT DEFAULT ''")
    # Unterschriebenes Papierformular als Nachweis (Dateiname in der Ablage)
    _spalte_sicherstellen(conn, "veranstaltung_anmeldung", "foto_nachweis", "TEXT DEFAULT ''")
    # Altbestand: gesetztes Häkchen gilt als Ja (idempotent; '' bleibt „nicht geantwortet")
    conn.execute("UPDATE veranstaltung_anmeldung SET foto_status='ja' "
                 "WHERE COALESCE(foto_ok,0)=1 AND COALESCE(foto_status,'')=''")
    try:
        from . import zahlungsabgleich as _za
        fehlend = conn.execute("SELECT id, verein_id FROM veranstaltung_anmeldung "
                               "WHERE zahlcode IS NULL OR zahlcode=''").fetchall()
        for r in fehlend:
            conn.execute("UPDATE veranstaltung_anmeldung SET zahlcode=? WHERE id=?",
                         (_za.neuer_code(conn, r["verein_id"]), r["id"]))
    except Exception:  # noqa: BLE001
        pass
    # Modul „Veranstaltungen & Anmeldungen"
    _spalte_sicherstellen(conn, "vereine", "modul_veranstaltungen", "INTEGER DEFAULT 0")
    # Ein-/ausblendbare Bereiche: WerkHaus-Dashboard, Schlüsselbuch, Dateiablage (DMS)
    _spalte_sicherstellen(conn, "vereine", "modul_werkhaus", "INTEGER DEFAULT 1")
    _spalte_sicherstellen(conn, "vereine", "modul_schluessel", "INTEGER DEFAULT 1")
    _spalte_sicherstellen(conn, "vereine", "modul_dms", "INTEGER DEFAULT 0")
    # Modul „Lebendiger Adventskalender": öffentliche Türchen-Seite + Verwaltung
    # (bewusst AUS als Standard – nur Vereine mit eigenem Kalender schalten ein)
    _spalte_sicherstellen(conn, "vereine", "modul_adventskalender", "INTEGER DEFAULT 0")
    # Eigener Einleitungstext der Adventskalender-Seite (leer = eingebauter Text)
    _spalte_sicherstellen(conn, "vereine", "lak_einleitung", "TEXT DEFAULT ''")
    # Modul „Verteilen": Flyer/Programmhefte straßenweise verteilen (ab Werk AUS)
    _spalte_sicherstellen(conn, "vereine", "modul_verteilen", "INTEGER DEFAULT 0")
    # Signage (21.11.2026): Bildschirme, Aufsteller, Info-Terminal – standardmäßig aus
    _spalte_sicherstellen(conn, "vereine", "modul_signage", "INTEGER DEFAULT 0")
    # Ablauf der Schaufenster-Folien: reihe | streuen (Folien unter „Termine“ zwischen die Termine) | mischen
    _spalte_sicherstellen(conn, "vereine", "signage_ablauf", "TEXT DEFAULT 'streuen'")
    _spalte_sicherstellen(conn, "vereine", "dms_quota_mb", "INTEGER DEFAULT 500")
    _spalte_sicherstellen(conn, "termine", "anmeldung_aktiv", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "termine", "min_teilnehmer", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "termine", "max_teilnehmer", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "termine", "preis", "REAL DEFAULT 0")
    _spalte_sicherstellen(conn, "termine", "anmeldeschluss", "TEXT")
    _spalte_sicherstellen(conn, "termine", "anmeldung_token", "TEXT")
    _spalte_sicherstellen(conn, "termine", "feedback_aktiv", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "termine", "langbeschreibung", "TEXT")
    # Stornierungsbedingungen je Veranstaltung (Auswahltext)
    _spalte_sicherstellen(conn, "termine", "stornobedingung", "TEXT")
    # Sponsor / Förderer je Veranstaltung (optional)
    _spalte_sicherstellen(conn, "termine", "sponsor_name", "TEXT")
    _spalte_sicherstellen(conn, "termine", "sponsor_logo", "TEXT")
    _spalte_sicherstellen(conn, "termine", "sponsor_url", "TEXT")
    # Veranstaltungs-Import (auf-nach-mv.de): Bild je Termin + Quelle je Verein
    _spalte_sicherstellen(conn, "termine", "bild", "TEXT")
    _spalte_sicherstellen(conn, "termine", "quelle_url", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "event_import_url", "TEXT")
    _spalte_sicherstellen(conn, "veranstaltung_mailvorlagen", "name", "TEXT")
    # Portal: Schichten-Bereich abschaltbar (Standard: AUS – Vereine schalten ihn
    # bewusst in den Einstellungen ein); Gesundheitszeugnis je Mitglied
    _spalte_sicherstellen(conn, "vereine", "modul_schichten", "INTEGER DEFAULT 0")
    # Anzeigename des „Vereinsraum"-Dashboards (früher fest „WerkHaus")
    _spalte_sicherstellen(conn, "vereine", "werkhaus_name", "TEXT")
    # Nextcloud-Login: Gruppen->Rollen-Zuordnung je Verein (leer = Server-Standard)
    _spalte_sicherstellen(conn, "vereine", "nc_gruppe_admin", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "nc_gruppe_mitglied", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "nc_gruppe_extern", "TEXT")
    # Sonderrollen optional auch per Nextcloud-Gruppe (leer = nur von Hand in „Zugänge")
    _spalte_sicherstellen(conn, "vereine", "nc_gruppe_schatz", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "nc_gruppe_oeffentlichkeit", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "nc_externe_erlaubt", "INTEGER DEFAULT 0")
    # Mitglieder-App: Startseiten-Blöcke je Verein abschaltbar; Quelle des Dateien-Tabs
    _spalte_sicherstellen(conn, "vereine", "portal_bloecke_aus", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "dateien_quelle", "TEXT")  # ''=auto|nextcloud|server|beide
    # Freunde (Gäste): Selbstregistrierung je Verein zuschaltbar
    _spalte_sicherstellen(conn, "vereine", "freunde_registrierung", "INTEGER DEFAULT 0")
    # Termine für Suchmaschinen (robots.txt, Sitemap, schema.org/Event) – wirkt nur
    # zusammen mit der Gastansicht, in der Demo nie (app/seo.py). Standard an.
    _spalte_sicherstellen(conn, "vereine", "seo_index", "INTEGER DEFAULT 1")
    # Computeransicht der App für Gäste im Stil der Vereinswebsite (19.11.2026)
    _spalte_sicherstellen(conn, "vereine", "website_stil", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "vereine", "website_titel", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "website_titelbild", "TEXT DEFAULT ''")
    # Bezahllink (Stufe 1): PayPal.me-Adresse des Vereins für Anmeldungs-Zahlungen
    _spalte_sicherstellen(conn, "vereine", "paypal_me", "TEXT")
    # Social Media + Visions-Text (für die Kennenlern-Seite in der App)
    _spalte_sicherstellen(conn, "vereine", "instagram", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "whatsapp_kanal", "TEXT")  # Kanal-Link, z. B. Adventskalender-Seite
    _spalte_sicherstellen(conn, "vereine", "ueber_text", "TEXT")
    # Automatische Erinnerungs-Mail an Angemeldete (1 Tag vor der Veranstaltung, 1x)
    _spalte_sicherstellen(conn, "termine", "va_erinnerung_gesendet", "INTEGER DEFAULT 0")
    # Veranstaltung kann abgesagt werden (bleibt sichtbar, aber ohne Anmeldung)
    _spalte_sicherstellen(conn, "termine", "abgesagt", "INTEGER DEFAULT 0")
    # Interne Veranstaltungen (z. B. Vorstandssitzung, Mitgliederversammlung)
    _spalte_sicherstellen(conn, "termine", "nur_mitglieder", "INTEGER DEFAULT 0")
    # Gruppen-Trennung: welche Gruppen zählen als Mitglieder / dürfen Schichten übernehmen
    # (Komma-Listen von Gruppen-IDs; leer = alle, wie bisher)
    _spalte_sicherstellen(conn, "vereine", "mitglied_gruppen", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "schicht_gruppen", "TEXT")
    # Kinder/Angehörige-Bereich (Kita-Funktion) je Verein zuschaltbar; beim ersten
    # Anlegen der Spalte für Kita-Vereine automatisch an, sonst aus.
    if "modul_kinder" not in [r["name"] for r in conn.execute("PRAGMA table_info(vereine)").fetchall()]:
        conn.execute("ALTER TABLE vereine ADD COLUMN modul_kinder INTEGER DEFAULT 0")
        conn.execute("UPDATE vereine SET modul_kinder=1 WHERE lower(vereinsname) LIKE '%kita%'")
    # Gruppen-Rang: Reihenfolge in der Mitglieder-App (kleiner = weiter oben).
    # Beim ersten Anlegen der Spalte bestehende Gruppen sinnvoll vorsortieren.
    if "rang" not in [r["name"] for r in conn.execute("PRAGMA table_info(gruppen)").fetchall()]:
        conn.execute("ALTER TABLE gruppen ADD COLUMN rang INTEGER DEFAULT 50")
        for gid, name in conn.execute("SELECT id, name FROM gruppen").fetchall():
            n = (name or "").lower()
            if "vorstand" in n:
                rang = 10
            elif "förder" in n or "foerder" in n:
                rang = 30
            elif "mitglied" in n:
                rang = 20
            elif "freund" in n:
                rang = 80
            elif "extern" in n:
                rang = 90
            else:
                rang = 50
            conn.execute("UPDATE gruppen SET rang=? WHERE id=?", (rang, gid))
    _spalte_sicherstellen(conn, "mitglieder", "gz_dateiname", "TEXT")
    _spalte_sicherstellen(conn, "mitglieder", "gz_hochgeladen_am", "TEXT")
    # Galerie: dokumentierte Freigabe-Bestätigung beim Foto-Upload
    _spalte_sicherstellen(conn, "galerie_fotos", "freigabe_ok", "INTEGER DEFAULT 0")
    # Abzeichen je Verein definierbar (leer = eingebaute Standard-Abzeichen)
    conn.execute("""CREATE TABLE IF NOT EXISTS abzeichen_defs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER,
        schluessel TEXT DEFAULT '',
        symbol TEXT DEFAULT '🏅',
        titel TEXT DEFAULT '',
        kriterium TEXT DEFAULT 'schichten',
        schwelle REAL DEFAULT 1,
        sortierung INTEGER DEFAULT 0)""")
    # Angaben für die amtliche Zuwendungsbestätigung (Spendenbescheinigung)
    _spalte_sicherstellen(conn, "vereine", "finanzamt", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "freistellung_datum", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "freistellung_zeitraum", "TEXT")
    _spalte_sicherstellen(conn, "vereine", "foerderzwecke", "TEXT")
    # Schwarzes Brett: optionale Kategorien (kommagetrennt, leer = Feature aus)
    # und optionale Mail-Benachrichtigung bei neuen Einträgen (leer = aus)
    _spalte_sicherstellen(conn, "pinnwand", "kategorie", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "pinnwand_kategorien", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "pinnwand_mail_an", "TEXT DEFAULT ''")
    # Schwarzes Brett für Gäste (öffentliches Formular mit Moderation):
    # Gast-Einträge haben kein Mitglied, dafür Name/Kontakt/Sprachen und
    # warten mit moderation=1 auf Freigabe durch die Verwaltung.
    _spalte_sicherstellen(conn, "pinnwand", "gast_name", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "pinnwand", "gast_kontakt", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "pinnwand", "sprachen", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "pinnwand", "moderation", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "pinnwand", "gast_token", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "pinnwand", "bestaetigt_am", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "pinnwand", "pruef_token", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "pinnwand", "pruef_gesendet_am", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "pinnwand_pruefung", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "mitglieder", "sprachen", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "sprachen_labels", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "vereine", "pinnwand_gast", "INTEGER DEFAULT 0")
    # Mehrsprachige Mitglieder-App (Opt-in; übersetzt Navigation, Schwarzes
    # Brett und Gast-Formular – siehe app/sprache.py)
    _spalte_sicherstellen(conn, "vereine", "portal_sprachen", "INTEGER DEFAULT 0")
    # Bewertungen zu Veranstaltungen (Sterne + Kommentar)
    conn.execute("""CREATE TABLE IF NOT EXISTS bewertungen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, termin_id INTEGER, mitglied_id INTEGER,
        sterne INTEGER, kommentar TEXT, erstellt_am TEXT
    )""")
    # Foto-Galerie zu Veranstaltungen (Mitglieder laden Fotos hoch)
    conn.execute("""CREATE TABLE IF NOT EXISTS galerie_fotos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER, termin_id INTEGER, mitglied_id INTEGER,
        dateiname TEXT, beschreibung TEXT, erstellt_am TEXT,
        freigabe_ok INTEGER DEFAULT 0
    )""")
    # Archiv vergangener Adventskalender: je Saison ein Bild, ein Rückblickstext
    # und die Kennzahlen (Teilnehmer:innen, Türchen) – Grundlage des PDF-Exports
    conn.execute("""CREATE TABLE IF NOT EXISTS adventskalender_archiv (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER NOT NULL,
        saison INTEGER NOT NULL,
        text TEXT DEFAULT '',
        bild TEXT DEFAULT '',
        teilnehmerzahl INTEGER,
        tuerchen INTEGER,
        erstellt_am TEXT,
        UNIQUE(verein_id, saison)
    )""")
    # Modul „Verteilen" (Flyer-Verteilaktionen, nachgebaut aus der SPD-
    # Wahlkampf-App 14.11.2026): Straßenstamm je Verein, Aktionen mit einem
    # oder mehreren Flyern (verteil_material, 16.11.2026), Status je Straße
    # UND Flyer (fehlende Zeile = offen), eigene Route je Person, Fotos.
    conn.execute("""CREATE TABLE IF NOT EXISTS verteil_strassen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER NOT NULL,
        ort TEXT NOT NULL, name TEXT NOT NULL,
        adressen INTEGER DEFAULT 0, haushalte INTEGER DEFAULT 0,
        haushalte_von_hand INTEGER DEFAULT 0,
        lat REAL, lon REAL, quelle TEXT DEFAULT '', stand TEXT DEFAULT '',
        UNIQUE(verein_id, ort, name)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS verteil_aktionen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER NOT NULL,
        titel TEXT NOT NULL, beschreibung TEXT DEFAULT '',
        von TEXT DEFAULT '', bis TEXT DEFAULT '',
        orte TEXT DEFAULT '',
        aktiv INTEGER DEFAULT 1, archiviert_am TEXT DEFAULT '',
        anonymisiert_am TEXT DEFAULT '',
        erstellt_von INTEGER, erstellt_am TEXT
    )""")
    # Was eine Aktion umfasst (Flyer und/oder Plakate) + Plakat-Regeln (16.11.2026)
    _spalte_sicherstellen(conn, "verteil_aktionen", "mit_flyern", "INTEGER DEFAULT 1")
    _spalte_sicherstellen(conn, "verteil_aktionen", "mit_plakaten", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "verteil_aktionen", "plakat_kuerzel", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "verteil_aktionen", "plakat_max", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "verteil_aktionen", "abhaengen_bis", "TEXT DEFAULT ''")
    # höchste je vergebene Plakat-Nummer – gelöschte Nummern werden nie neu vergeben
    _spalte_sicherstellen(conn, "verteil_aktionen", "plakat_letzte_nr", "INTEGER DEFAULT 0")
    # Zustellungen an Kontakte (Mini-CRM), 16.11.2026: Schalter, „Was?“ und
    # Zähler, die nach der Löschfrist (Zeilen weg) stehen bleiben
    _spalte_sicherstellen(conn, "verteil_aktionen", "mit_zustellungen", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "verteil_aktionen", "zustellung_was", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "verteil_aktionen", "zust_gesamt", "INTEGER DEFAULT 0")
    _spalte_sicherstellen(conn, "verteil_aktionen", "zust_zugestellt", "INTEGER DEFAULT 0")
    conn.execute("""CREATE TABLE IF NOT EXISTS verteil_zustellungen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER NOT NULL,
        aktion_id INTEGER NOT NULL, kontakt_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'offen'
            CHECK (status IN ('offen','laeuft','zugestellt','nicht_zustellbar')),
        grund TEXT DEFAULT '', benutzer_id INTEGER, geaendert_am TEXT,
        UNIQUE(aktion_id, kontakt_id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_verteil_zust_aktion ON verteil_zustellungen(aktion_id)")
    # Kartenpunkt am Kontakt (hausgenau über Nominatim, Rückfall Straßenpunkt,
    # oder von der Leitung gesetzt); geo_schluessel = Prüfsumme der Anschrift
    for _sp, _typ in (("lat", "REAL"), ("lon", "REAL"), ("geo_quelle", "TEXT DEFAULT ''"),
                      ("geo_schluessel", "TEXT DEFAULT ''")):
        _spalte_sicherstellen(conn, "kontakte", _sp, _typ)
    conn.execute("""CREATE TABLE IF NOT EXISTS verteil_plakate (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER NOT NULL,
        aktion_id INTEGER NOT NULL,
        laufnr INTEGER NOT NULL, nr TEXT NOT NULL,
        lat REAL NOT NULL, lon REAL NOT NULL,
        strasse TEXT DEFAULT '', hinweis TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'geplant'
            CHECK (status IN ('geplant','haengt','beschaedigt','weg','abgehaengt')),
        erstellt_von INTEGER, erstellt_am TEXT, geaendert_am TEXT,
        UNIQUE(aktion_id, laufnr)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS verteil_plakat_verlauf (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plakat_id INTEGER NOT NULL, aktion_id INTEGER NOT NULL,
        benutzer_id INTEGER, was TEXT NOT NULL, am TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS verteil_material (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER NOT NULL,
        aktion_id INTEGER NOT NULL,
        titel TEXT NOT NULL, bild TEXT DEFAULT '',
        pos INTEGER DEFAULT 0, erstellt_am TEXT
    )""")
    # Status je Straße + Flyer. Frühe Testfassung (nur je Straße, UNIQUE ohne
    # material_id) wird umgebaut – material_id 0 ordnet verteilen.py beim
    # ersten Zugriff dem ersten Flyer der Aktion zu.
    _vs = conn.execute("SELECT sql FROM sqlite_master WHERE name='verteil_status'").fetchone()
    if _vs and "material_id" not in (_vs[0] or ""):
        conn.execute("ALTER TABLE verteil_status RENAME TO verteil_status_alt")
    conn.execute("""CREATE TABLE IF NOT EXISTS verteil_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER NOT NULL,
        aktion_id INTEGER NOT NULL, strasse_id INTEGER NOT NULL,
        material_id INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL CHECK (status IN ('offen','laeuft','verteilt')),
        benutzer_id INTEGER, geaendert_am TEXT,
        UNIQUE(aktion_id, strasse_id, material_id)
    )""")
    if _vs and "material_id" not in (_vs[0] or ""):
        conn.execute("INSERT INTO verteil_status (verein_id, aktion_id, strasse_id, material_id, status, "
                     "benutzer_id, geaendert_am) SELECT verein_id, aktion_id, strasse_id, 0, status, "
                     "benutzer_id, geaendert_am FROM verteil_status_alt")
        conn.execute("DROP TABLE verteil_status_alt")
    conn.execute("""CREATE TABLE IF NOT EXISTS verteil_routen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER NOT NULL,
        aktion_id INTEGER NOT NULL, benutzer_id INTEGER NOT NULL,
        start_lat REAL, start_lon REAL, angelegt TEXT, geaendert TEXT,
        UNIQUE(aktion_id, benutzer_id)
    )""")
    # gewählte Flyer der Route (JSON-Liste von verteil_material.id)
    _spalte_sicherstellen(conn, "verteil_routen", "materialien", "TEXT DEFAULT ''")
    conn.execute("""CREATE TABLE IF NOT EXISTS verteil_route_strassen (
        route_id INTEGER NOT NULL, pos INTEGER NOT NULL, strasse_id INTEGER NOT NULL,
        PRIMARY KEY (route_id, strasse_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS verteil_fotos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verein_id INTEGER NOT NULL,
        aktion_id INTEGER NOT NULL, strasse_id INTEGER NOT NULL,
        benutzer_id INTEGER, dateiname TEXT NOT NULL, erstellt_am TEXT
    )""")
    # Fotos zu Plakaten (strasse_id dann 0)
    _spalte_sicherstellen(conn, "verteil_fotos", "plakat_id", "INTEGER DEFAULT 0")
    for _ix, _tab in (("ix_verteil_status_aktion", "verteil_status"),
                      ("ix_verteil_fotos_aktion", "verteil_fotos"),
                      ("ix_verteil_routen_aktion", "verteil_routen"),
                      ("ix_verteil_material_aktion", "verteil_material"),
                      ("ix_verteil_plakate_aktion", "verteil_plakate"),
                      ("ix_verteil_plakat_verlauf_aktion", "verteil_plakat_verlauf")):
        conn.execute(f"CREATE INDEX IF NOT EXISTS {_ix} ON {_tab}(aktion_id)")
    # Veranstaltungs-Mailvorlagen wandern in die zentrale Vorlagen-Tabelle
    # (Spalte schluessel). Es ziehen NUR Anpassungen um – die eingebauten
    # Standards bleiben im Code (routes/veranstaltungen.VORLAGEN) als Rückgriff,
    # damit Vereine ohne Veranstaltungs-Modul keine Datensätze bekommen und
    # nachträgliches Aktivieren ohne Seed funktioniert. Idempotent: Nach dem
    # Umzug ist die alte Tabelle leer und bleibt es.
    _spalte_sicherstellen(conn, "vorlagen", "schluessel", "TEXT DEFAULT ''")
    try:
        _alte = conn.execute("SELECT * FROM veranstaltung_mailvorlagen").fetchall()
        for _z in _alte:
            _da = conn.execute(
                "SELECT 1 FROM vorlagen WHERE verein_id=? AND schluessel=?",
                (_z["verein_id"], _z["schluessel"])).fetchone()
            if not _da:
                conn.execute(
                    "INSERT INTO vorlagen (verein_id, name, betreff, inhalt, art, "
                    "kategorie, schluessel, erstellt_am) VALUES (?,?,?,?,?,?,?,?)",
                    (_z["verein_id"], _z["name"] or "", _z["betreff"] or "",
                     _z["text"] or "", "extern", "Veranstaltungen",
                     _z["schluessel"], now_iso()))
        if _alte:
            conn.execute("DELETE FROM veranstaltung_mailvorlagen")
        # Sicherheitsnetz gegen Doppel-Submit: je Verein und Schlüssel nur eine
        # Zeile (vorher etwaige Duplikate bereinigen – die jüngste gewinnt, wie
        # beim Laden). Normale Vorlagen (schluessel='') bleiben unbegrenzt.
        conn.execute("DELETE FROM vorlagen WHERE COALESCE(schluessel,'')!='' AND id NOT IN "
                     "(SELECT MAX(id) FROM vorlagen WHERE COALESCE(schluessel,'')!='' "
                     "GROUP BY verein_id, schluessel)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_vorlagen_schluessel "
                     "ON vorlagen(verein_id, schluessel) WHERE COALESCE(schluessel,'')!=''")
    except Exception:  # noqa: BLE001 – Umzug darf den App-Start nie blockieren
        logging.getLogger("db").exception("Vorlagen-Umzug fehlgeschlagen")
    # Content-Hub-Anbindung (Modul, Feed-Zeitstempel, Social-Häkchen, Rückmeldungen)
    _contenthub_migrationen(conn)
    # Sensible Zugangsdaten verschlüsseln (einmalige, idempotente Migration)
    from . import krypto
    krypto.migriere_klartext(conn, "vereine", ["fints_pin", "smtp_passwort", "ncc_pass"])
    conn.commit()


def _tabelle_da(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


# Spalten, deren Änderung einen Termin für den Content-Hub „neu“ macht (Feed: updated_at).
# Bewusst NICHT dabei: Zähler/Merker wie va_erinnerung_gesendet – sonst sähe der Hub
# jede Stunde „geänderte“ Termine.
_HUB_TERMIN_SPALTEN = ("titel", "beschreibung", "datum", "uhrzeit", "uhrzeit_bis", "ort", "kategorie_id",
                       "aktiv", "nur_mitglieder", "abgesagt", "bild", "langbeschreibung",
                       "anmeldung_aktiv", "anmeldung_token")


def _contenthub_migrationen(conn):
    """Content-Hub (Plan Fassung 2, 09.11.2026): idempotent, läuft in JEDER Vereins-DB.

    Das Modul ist ab Werk AUS; ohne Modul + Token antworten alle Endpunkte mit 404.
    Zeiten in Ortszeit des Prozesses – wie now_iso() (SQLite: 'localtime')."""
    _spalte_sicherstellen(conn, "vereine", "modul_contenthub", "INTEGER DEFAULT 0")
    # 1 = Instagram läuft über den Hub: das App-Direktposten (oea_auto.plan_lauf,
    # „Jetzt posten“) ist dann für diesen Verein gesperrt – keine Dubletten.
    _spalte_sicherstellen(conn, "vereine", "hub_instagram", "INTEGER DEFAULT 0")
    # Teilen-Helfer: Facebook-Gruppen (je Zeile „Name | Link“) und Link zum WhatsApp-Kanal
    _spalte_sicherstellen(conn, "vereine", "hub_fb_gruppen", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "vereine", "hub_whatsapp_kanal", "TEXT DEFAULT ''")
    if _tabelle_da(conn, "termine"):
        _spalte_sicherstellen(conn, "termine", "geaendert_am", "TEXT")
        conn.execute("UPDATE termine SET geaendert_am=COALESCE(NULLIF(erstellt_am,''), "
                     "NULLIF(datum,''), strftime('%Y-%m-%d %H:%M:%S','now','localtime')) "
                     "WHERE geaendert_am IS NULL OR geaendert_am=''")
        da = {r["name"] for r in conn.execute("PRAGMA table_info(termine)")}
        wann = " OR ".join(f"NEW.{s} IS NOT OLD.{s}" for s in _HUB_TERMIN_SPALTEN if s in da)
        conn.execute("DROP TRIGGER IF EXISTS hub_termin_geaendert")
        conn.execute(f"CREATE TRIGGER hub_termin_geaendert AFTER UPDATE ON termine "
                     f"WHEN ({wann}) BEGIN UPDATE termine SET geaendert_am="
                     f"strftime('%Y-%m-%d %H:%M:%S','now','localtime') WHERE id=NEW.id; END")
        conn.execute("CREATE TRIGGER IF NOT EXISTS hub_termin_neu AFTER INSERT ON termine "
                     "WHEN NEW.geaendert_am IS NULL BEGIN UPDATE termine SET geaendert_am="
                     "strftime('%Y-%m-%d %H:%M:%S','now','localtime') WHERE id=NEW.id; END")
    if _tabelle_da(conn, "galerie_fotos"):
        # Social-Häkchen: nur der Vorstand setzt es; 0 heißt NICHT freigegeben
        _spalte_sicherstellen(conn, "galerie_fotos", "social_ok", "INTEGER DEFAULT 0")
        _spalte_sicherstellen(conn, "galerie_fotos", "social_ok_von", "TEXT DEFAULT ''")
        _spalte_sicherstellen(conn, "galerie_fotos", "social_ok_am", "TEXT DEFAULT ''")
        if _tabelle_da(conn, "veranstaltung_anmeldung"):
            # Ein Nein/Widerruf einer teilnehmenden Person (egal über welchen Weg) nimmt das
            # Social-Häkchen ALLER Fotos dieser Veranstaltung zurück – der Vorstand prüft neu.
            for art, bedingung in (("UPDATE", "NEW.foto_social='nein' AND COALESCE(OLD.foto_social,'')!='nein'"),
                                   ("INSERT", "NEW.foto_social='nein'")):
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS hub_foto_widerruf_{art.lower()} AFTER {art} "
                    f"ON veranstaltung_anmeldung WHEN {bedingung} BEGIN UPDATE galerie_fotos SET social_ok=0, "
                    f"social_ok_von='Widerruf', social_ok_am=strftime('%Y-%m-%d %H:%M:%S','now','localtime') "
                    f"WHERE termin_id=NEW.termin_id AND social_ok=1; END")
    _spalte_sicherstellen(conn, "ankuendigungen", "hub_id", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "oea_beitraege", "hub_thema", "TEXT DEFAULT ''")
    _spalte_sicherstellen(conn, "oea_beitraege", "hub_status", "TEXT DEFAULT ''")
    # Was der Hub schon bekommen hat – Grundlage für Grabsteine (zurückgezogene Items)
    conn.execute("""CREATE TABLE IF NOT EXISTS hub_export (
        verein_id INTEGER NOT NULL, item_id TEXT NOT NULL, art TEXT NOT NULL,
        zuletzt TEXT NOT NULL, zurueckgezogen INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (verein_id, item_id))""")
    # Spiegel der Hub-Veröffentlichungen (je Kanal eine Zeile), gefüllt per Rückmeldung
    conn.execute("""CREATE TABLE IF NOT EXISTS hub_beitraege (
        id INTEGER PRIMARY KEY AUTOINCREMENT, verein_id INTEGER NOT NULL,
        veroeff_id TEXT NOT NULL UNIQUE, beitrag_id TEXT, thema_id TEXT, kanal TEXT,
        status TEXT, titel TEXT DEFAULT '', geplant_fuer TEXT, veroeffentlicht TEXT,
        url TEXT DEFAULT '', fehler TEXT DEFAULT '', zahlen TEXT DEFAULT '',
        version INTEGER NOT NULL DEFAULT 0, termin TEXT DEFAULT '', oea_beitrag_id INTEGER,
        aktualisiert TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS hub_wochenblicke (
        verein_id INTEGER NOT NULL, woche TEXT NOT NULL, daten TEXT NOT NULL,
        erstellt TEXT NOT NULL, PRIMARY KEY (verein_id, woche))""")
    # Letzter Zugriff des Hubs je Pfad (Gesundheitsanzeige im Cockpit)
    conn.execute("""CREATE TABLE IF NOT EXISTS hub_zugriffe (
        verein_id INTEGER NOT NULL, pfad TEXT NOT NULL, zuletzt TEXT NOT NULL,
        PRIMARY KEY (verein_id, pfad))""")


def _spalte_sicherstellen(conn, tabelle, spalte, typ):
    """Fügt eine Spalte hinzu, falls sie noch nicht existiert (einfache Migration).

    Existiert die TABELLE (noch) nicht – z. B. bei einer ganz frischen Vereins-DB,
    deren CREATE-Anweisungen weiter unten folgen – wird still übersprungen; die
    Spalte muss dann in der CREATE-Definition enthalten sein."""
    vorhanden = [r["name"] for r in conn.execute(f"PRAGMA table_info({tabelle})").fetchall()]
    if not vorhanden:
        return
    if spalte not in vorhanden:
        conn.execute(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {typ}")


# ---------------------------------------------------------------------------
# Aktiver Verein (Mandant)
# ---------------------------------------------------------------------------
def aktiver_verein_id():
    """ID des aktuell ausgewählten Vereins (pro Request zwischengespeichert).
    Liest aus der Registry – NICHT aus einer Vereins-DB (sonst Endlosschleife)."""
    if "verein_id" in g:
        return g.verein_id
    conn = get_system_db()
    row = conn.execute("SELECT aktiver_verein_id FROM system WHERE id=1").fetchone()
    conn.close()
    g.verein_id = row["aktiver_verein_id"] if row else None
    return g.verein_id


def setze_aktiven_verein(verein_id, registry_erzwingen=False):
    """Merkt den gewählten Verein.

    Der Registry-Zeiger system.aktiver_verein_id ist GLOBAL: Er bestimmt, welchen
    Verein anonyme Gäste auf öffentlichen Seiten sehen und welcher Verein ohne
    Request gilt (Skripte, Backups). Im Server-Betrieb (PRODUKTION) entscheidet
    für angemeldete Personen allein die Sitzung – der Wechsel einer Person darf
    dort den Gäste-Standard nicht umstellen. Nur registry_erzwingen=True (etwa
    wenn der bisherige Standard-Verein gelöscht wurde) oder ein Aufruf außerhalb
    eines Requests (Einrichtung, Skripte, Lokale Verwaltung) schreibt den Zeiger."""
    from flask import current_app, has_app_context, has_request_context, session
    nur_sitzung = has_request_context() and bool(current_app.config.get("PRODUKTION"))
    if registry_erzwingen or not nur_sitzung:
        conn = get_system_db()
        conn.execute("UPDATE system SET aktiver_verein_id=? WHERE id=1", (verein_id,))
        conn.commit()
        conn.close()
    if has_app_context():
        g.pop("verein_id", None)
    # Im Server-Betrieb entscheidet die Sitzung über den Verein (Mehr-Mandanten):
    # den Wechsel auch dort hinterlegen, sofern wir in einem Request sind.
    if has_request_context():
        session["verein_id"] = verein_id
        # Für den Rest DIESES Requests gilt der neue Verein ebenfalls –
        # ohne g-Eintrag fiele aktiver_verein_id() auf die Registry zurück.
        g.verein_id = verein_id


_GEHEIM_VEREINE = ("fints_pin", "smtp_passwort", "ncc_pass", "paypal_secret", "fints_client_data",
                   "ig_token", "ig_app_secret", "nuki_token")


def einstellungen_fuer(vid):
    """Wie get_einstellungen(), aber für einen bestimmten Verein.

    Nötig überall dort, wo es keine Anmeldung und keinen aktiven Verein gibt –
    also in den nächtlichen Läufen (app/bankauto.py, app/erinnerungen.py)."""
    conn = get_db_fuer(vid)
    row = conn.execute("SELECT * FROM vereine WHERE id=?", (vid,)).fetchone()
    conn.close()
    return _einstellungen_aufbereiten(row)


def get_einstellungen():
    """Stammdaten/Einstellungen des aktiven Vereins (aus dessen eigener DB).

    Sensible Felder (PIN/Passwörter) liegen in der DB verschlüsselt und werden
    hier für den App-Betrieb wieder in Klartext gewandelt. Rückgabe ist ein
    dict (bracket-Zugriff wie bei einer Row bleibt kompatibel)."""
    conn = get_db()
    row = conn.execute("SELECT * FROM vereine WHERE id=?", (aktiver_verein_id(),)).fetchone()
    conn.close()
    return _einstellungen_aufbereiten(row)


def _einstellungen_aufbereiten(row):
    """Row → dict mit entschlüsselten Geheimfeldern (oder None)."""
    if row is None:
        return None
    from . import krypto
    e = dict(row)
    for feld in _GEHEIM_VEREINE:
        if feld in e:
            e[feld] = krypto.entschluesseln(e[feld])
    return e


def feed_token(vid):
    """Abonnier-Token des Vereins (erzeugt einen, falls noch keiner existiert)."""
    import secrets
    conn = get_db_fuer(vid)
    r = conn.execute("SELECT feed_token FROM vereine WHERE id=?", (vid,)).fetchone()
    tok = (r["feed_token"] if r else "") or ""
    if not tok:
        tok = secrets.token_urlsafe(16)
        conn.execute("UPDATE vereine SET feed_token=? WHERE id=?", (tok, vid))
        conn.commit()
    conn.close()
    return tok


def oeffentliche_suche(abfrage, *params):
    """Token-Abfrage für öffentliche Links: zuerst in der DB des aktiven Vereins,
    danach bei allen übrigen Vereinen – die Lokale Verwaltung hält je Verein eine
    eigene Datenbank, ein geteilter Link soll aber unabhängig vom gerade
    gewählten Verein funktionieren. params füllt die ?-Platzhalter; ein leerer
    Wert bedeutet „kein Treffer". Liefert (conn, row); ohne Treffer zeigt conn
    auf den aktiven Verein. conn ist in jedem Fall zu schließen."""
    conn = get_db()
    if not params or any(p in (None, "") for p in params):
        return conn, None
    row = conn.execute(abfrage, params).fetchone()
    if row:
        return conn, row
    aktiv = aktiver_verein_id()
    for v in alle_vereine():
        if v["id"] == aktiv:
            continue
        andere = get_db_fuer(v["id"])
        try:
            row = andere.execute(abfrage, params).fetchone()
        except Exception:  # noqa: BLE001 – fremde DB kann leer/älter sein (Tabelle fehlt)
            row = None
        if row:
            conn.close()
            return andere, row
        andere.close()
    return conn, None


def verein_per_feed_token(token):
    """Findet die Vereins-id zu einem Feed-Token (über alle Vereine)."""
    if not token:
        return None
    for v in alle_vereine():
        conn = get_db_fuer(v["id"])
        r = conn.execute("SELECT 1 FROM vereine WHERE id=? AND feed_token=? AND feed_token!=''",
                         (v["id"], token)).fetchone()
        conn.close()
        if r:
            return v["id"]
    return None


def alle_vereine():
    """Vereinsliste aus der Registry (id + Name) – für den Verein-Wechsler."""
    conn = get_system_db()
    rows = conn.execute(
        "SELECT id, vereinsname, erstellt_am FROM vereine_index ORDER BY vereinsname").fetchall()
    conn.close()
    return rows


def erstelle_verein(name):
    """Legt einen neuen Verein an (Registry-Eintrag + eigene DB); gibt die id zurück."""
    conn = get_system_db()
    vid = _neuer_verein_db(conn, name)
    conn.close()
    return vid


def loesche_verein(vid):
    """Entfernt einen Verein aus der Registry und löscht seine Datenbank-Datei."""
    conn = get_system_db()
    conn.execute("DELETE FROM vereine_index WHERE id=?", (vid,))
    conn.execute("DELETE FROM termin_merkliste WHERE verein_id=?", (vid,))
    # Der globale Standard-Verein (Gäste, Skripte) darf nie auf einen gelöschten
    # Verein zeigen – sonst liefen öffentliche Seiten bis zum nächsten App-Start
    # ins Leere. Dann auf den ersten verbleibenden Verein umstellen.
    aktiv = conn.execute("SELECT aktiver_verein_id FROM system WHERE id=1").fetchone()
    if aktiv and aktiv["aktiver_verein_id"] == vid:
        # Gleiche Sortierung wie alle_vereine(), damit Gäste und die löschende
        # Person (vereine.loeschen nimmt alle_vereine()[0]) im SELBEN Verein landen.
        erster = conn.execute("SELECT id FROM vereine_index ORDER BY vereinsname LIMIT 1").fetchone()
        conn.execute("UPDATE system SET aktiver_verein_id=? WHERE id=1",
                     (erster["id"] if erster else None,))
    conn.commit()
    conn.close()
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = verein_db_pfad(vid) + suffix
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    # Titelbild (Website-Stil) und Kategorie-Standardbilder dieses Vereins (19.11.2026);
    # das Unterstrich-Ende im Muster trennt Verein 1 sicher von Verein 10.
    import glob
    for muster in (os.path.join(DATA_DIR, "logos", f"titelbild_v{int(vid)}_*"),
                   os.path.join(DATA_DIR, "event_bilder", f"kategorie_v{int(vid)}_*")):
        for p in glob.glob(muster):
            try:
                os.remove(p)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Benutzer / Anmeldung (in der Registry gespeichert – global über alle Vereine)
# ---------------------------------------------------------------------------
def benutzer_anzahl():
    conn = get_system_db()
    n = conn.execute("SELECT COUNT(*) c FROM benutzer").fetchone()["c"]
    conn.close()
    return n


def benutzer_laden(uid):
    conn = get_system_db()
    r = conn.execute("SELECT * FROM benutzer WHERE id=?", (uid,)).fetchone()
    conn.close()
    return r


def benutzer_per_email(email):
    conn = get_system_db()
    r = conn.execute("SELECT * FROM benutzer WHERE email=? COLLATE NOCASE AND email!=''", (email,)).fetchone()
    conn.close()
    return r


def benutzer_per_reset_token(token):
    if not token:
        return None
    conn = get_system_db()
    r = conn.execute("SELECT * FROM benutzer WHERE reset_token=? AND reset_token!=''", (token,)).fetchone()
    conn.close()
    return r


def notfall_admin_anlegen(email, passwort_hash):
    conn = get_system_db()
    cur = conn.execute(
        "INSERT INTO benutzer (email, anzeigename, rolle, passwort_hash, aktiv, erstellt_am) "
        "VALUES (?,?,?,?,1,?)", (email, email, "admin", passwort_hash, now_iso()))
    uid = cur.lastrowid
    conn.commit()
    conn.close()
    return uid


def verein_stammdaten_setzen(vereinsname, ort="", email=""):
    """Erst-Einrichtung: Grunddaten in die vereine-Zeile des aktiven Vereins schreiben.

    Aktualisiert AUCH die Registry (vereine_index) – sonst hieße der Verein im
    Wechsler und in alle_vereine() weiterhin "Mein Verein e.V."."""
    vid = aktiver_verein_id()
    conn = get_db()
    conn.execute("UPDATE vereine SET vereinsname=?, ort=?, email=? WHERE id=?",
                 (vereinsname, ort or "", email or "", vid))
    conn.commit()
    conn.close()
    sys_conn = get_system_db()
    sys_conn.execute("UPDATE vereine_index SET vereinsname=? WHERE id=?", (vereinsname, vid))
    sys_conn.commit()
    sys_conn.close()


# Rollen, die es in Nextcloud normalerweise NICHT als Gruppe gibt: sie werden in
# „Zugänge" von Hand vergeben. Ohne diesen Schutz würde die Gruppen-Zuordnung
# (auth.rolle_aus_gruppen kennt nur admin/mitglied/extern) sie bei der nächsten
# Nextcloud-Anmeldung wieder überschreiben – die Rollen wären unbenutzbar.
MANUELLE_ROLLEN = ("schatz", "oeffentlichkeit")


def rolle_beim_login(alte_rolle, gruppen_rolle):
    """Welche Rolle nach einer Nextcloud-Anmeldung gilt.

    Normalfall: die aus den Nextcloud-Gruppen abgeleitete Rolle. Ausnahme: eine
    von Hand gesetzte Sonderrolle (Schatzmeister:in, Öffentlichkeitsarbeit)
    bleibt bestehen – es sei denn, die Gruppen vergeben selbst eine Sonderrolle
    (dann ist die Zuordnung in den Einstellungen gepflegt und hat Vorrang).
    Zurücknehmen lässt sich eine Sonderrolle jederzeit unter „Zugänge"."""
    alt = (alte_rolle or "").strip()
    if alt in MANUELLE_ROLLEN and gruppen_rolle not in MANUELLE_ROLLEN:
        return alt
    return gruppen_rolle


def nc_alte_tokens(nc_benutzer, verein_id):
    """Entschlüsselte, noch gespeicherte Nextcloud-App-Passwörter dieses Loginnamens
    (Altbestand: bis 20.11.2026 wurde das Login-Flow-Passwort aufbewahrt, obwohl
    es nie wieder gebraucht wird). Dient nur zum Widerrufen beim nächsten Login."""
    from . import krypto
    conn = get_system_db()
    rows = conn.execute(
        "SELECT nc_token FROM benutzer WHERE nc_benutzer=? COLLATE NOCASE AND nc_benutzer!='' "
        "AND IFNULL(verein_id,0)=IFNULL(?,0) AND IFNULL(nc_token,'')!=''", (nc_benutzer, verein_id)).fetchall()
    conn.close()
    return [t for t in (krypto.entschluesseln(r["nc_token"]) for r in rows) if t]


def nc_benutzer_upsert(nc_benutzer, email, anzeigename, rolle, token, verein_id=None):
    """Legt einen Nextcloud-Benutzer FÜR EINEN VEREIN an oder aktualisiert ihn; gibt die id zurück.
    Dieselbe Person kann in mehreren Vereinen ein Konto haben (je verein_id eine Zeile).

    Von Hand vergebene Sonderrollen (MANUELLE_ROLLEN) bleiben erhalten – sonst
    würde die Gruppen-Zuordnung sie bei jeder Anmeldung überschreiben."""
    if verein_id is None:
        # Fail closed: ohne Verein entstünde ein NULL-Konto, das (früher) in
        # jedem Verein mitzählte. NULL ist den Notfall-Admins vorbehalten,
        # die nie über den Nextcloud-Login angelegt werden.
        raise ValueError("nc_benutzer_upsert braucht eine verein_id (Vereinswahl beim Login)")
    from . import krypto
    token = krypto.verschluesseln(token)  # Nextcloud-Token verschlüsselt ablegen
    conn = get_system_db()
    # 1) Treffer über den Nextcloud-Loginnamen – Groß-/Kleinschreibung egal
    #    ("Andreas" vs. "andreas" sind dieselbe Person).
    r = conn.execute(
        "SELECT id, rolle FROM benutzer WHERE nc_benutzer=? COLLATE NOCASE AND nc_benutzer!='' "
        "AND IFNULL(verein_id,0)=IFNULL(?,0)", (nc_benutzer, verein_id)).fetchone()
    # 2) Sonst über die E-Mail: Nextcloud erlaubt die Anmeldung per Loginname ODER
    #    E-Mail, und vorab per E-Mail angelegte Konten (Admin/Selbstregistrierung)
    #    haben noch keinen NC-Namen. Ohne diese Prüfung entstünde ein Duplikat.
    if not r and (email or "").strip():
        r = conn.execute(
            "SELECT id, rolle FROM benutzer WHERE email=? COLLATE NOCASE AND email!='' "
            "AND IFNULL(verein_id,0)=IFNULL(?,0) ORDER BY id LIMIT 1", (email, verein_id)).fetchone()
    if r:
        # Vorhandenes Konto übernehmen und den zuletzt genutzten NC-Namen festhalten.
        conn.execute("UPDATE benutzer SET nc_benutzer=?, email=?, anzeigename=?, rolle=?, nc_token=?, "
                     "aktiv=1, letzter_login=?, nc_login_am=? WHERE id=?",
                     (nc_benutzer, email, anzeigename, rolle_beim_login(r["rolle"], rolle),
                      token, now_iso(), now_iso(), r["id"]))
        uid = r["id"]
    else:
        cur = conn.execute(
            "INSERT INTO benutzer (nc_benutzer, email, anzeigename, rolle, verein_id, nc_token, aktiv, letzter_login, "
            "nc_login_am, erstellt_am) VALUES (?,?,?,?,?,?,1,?,?,?)",
            (nc_benutzer, email, anzeigename, rolle, verein_id, token, now_iso(), now_iso(), now_iso()))
        uid = cur.lastrowid
    conn.commit()
    conn.close()
    return uid


def benutzer_fuer_mitglied_anlegen(nc_benutzer, email, anzeigename, rolle, mitglied_id, verein_id=None):
    """Legt – falls noch nicht vorhanden – einen WerkHausApp-Zugang für ein Mitglied an.

    Ohne Token/Passwort: die Person meldet sich später selbst per Nextcloud an; dabei wird
    genau diese Zeile wiederverwendet (Treffer über den Nextcloud-Loginnamen). Ein bereits
    vorhandenes Konto wird nur (falls offen) mit dem Mitglied verknüpft, nie überschrieben.
    Rückgabe: True, wenn neu angelegt."""
    if verein_id is None:
        # Fail closed wie in nc_benutzer_upsert: NULL ist den Notfall-Admins vorbehalten.
        raise ValueError("benutzer_fuer_mitglied_anlegen braucht eine verein_id")
    conn = get_system_db()
    treffer = conn.execute(
        "SELECT id, mitglied_id FROM benutzer WHERE IFNULL(verein_id,0)=IFNULL(?,0) AND ("
        "(nc_benutzer=? AND nc_benutzer!='') OR (email=? AND email!=''))",
        (verein_id, nc_benutzer or "", email or "")).fetchone()
    if treffer:
        if not treffer["mitglied_id"] and mitglied_id:
            conn.execute("UPDATE benutzer SET mitglied_id=? WHERE id=?", (mitglied_id, treffer["id"]))
            conn.commit()
        conn.close()
        return False
    conn.execute(
        "INSERT INTO benutzer (nc_benutzer, email, anzeigename, rolle, verein_id, mitglied_id, aktiv, erstellt_am) "
        "VALUES (?,?,?,?,?,?,1,?)",
        (nc_benutzer or "", email or "", anzeigename or "", rolle, verein_id, mitglied_id, now_iso()))
    conn.commit()
    conn.close()
    return True


def benutzer_aktualisieren(uid, **felder):
    if not felder:
        return
    setz = ", ".join(f"{k}=?" for k in felder)
    conn = get_system_db()
    conn.execute(f"UPDATE benutzer SET {setz} WHERE id=?", list(felder.values()) + [uid])
    conn.commit()
    conn.close()


def letzter_login_setzen(uid):
    benutzer_aktualisieren(uid, letzter_login=now_iso())


def benutzer_ids_mit_rolle(rollen, verein_id):
    """IDs der aktiven Benutzer dieses Vereins mit einer der angegebenen Rollen.

    Konten ohne verein_id zählen nur mit, wenn sie Notfall-Admins sind
    (rolle='admin'). Ein NULL-Konto mit anderer Rolle wäre ein Fehleintrag
    (z. B. aus einem Nextcloud-Login ohne Vereinswahl) und darf nicht in
    jedem Verein Mitteilungen bekommen."""
    conn = get_system_db()
    frag = ",".join("?" * len(rollen))
    rows = conn.execute(
        f"SELECT id FROM benutzer WHERE aktiv=1 AND rolle IN ({frag}) "
        "AND (verein_id=? OR (verein_id IS NULL AND rolle='admin'))",
        list(rollen) + [verein_id]).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def alle_benutzer(verein_id):
    """Alle Zugänge dieses Vereins; ohne verein_id nur Notfall-Admins
    (rolle='admin') – NULL-Konten anderer Rollen gehören keinem Verein und
    haben in der Zugänge-Liste jedes Vereins nichts zu suchen."""
    conn = get_system_db()
    rows = conn.execute(
        "SELECT * FROM benutzer WHERE verein_id=? OR (verein_id IS NULL AND rolle='admin') "
        "ORDER BY (rolle!='admin'), anzeigename COLLATE NOCASE, email COLLATE NOCASE",
        (verein_id,)).fetchall()
    conn.close()
    return rows


def benutzer_anlegen(email, anzeigename, rolle, verein_id, mitglied_id=None, passwort_hash=""):
    """Legt einen neuen (Passwort-)Zugang an und gibt die id zurück."""
    conn = get_system_db()
    cur = conn.execute(
        "INSERT INTO benutzer (email, anzeigename, rolle, verein_id, mitglied_id, passwort_hash, aktiv, erstellt_am) "
        "VALUES (?,?,?,?,?,?,1,?)",
        (email, anzeigename, rolle, verein_id, mitglied_id, passwort_hash, now_iso()))
    uid = cur.lastrowid
    conn.commit()
    conn.close()
    return uid


def merkliste_ids(benutzer_id, verein_id):
    """Termin-IDs auf der Herzchenliste eines Kontos (leere Menge ohne Konto)."""
    if not benutzer_id:
        return set()
    conn = get_system_db()
    ids = {r[0] for r in conn.execute(
        "SELECT termin_id FROM termin_merkliste WHERE benutzer_id=? AND verein_id=?",
        (int(benutzer_id), int(verein_id)))}
    conn.close()
    return ids


def merken_umschalten(benutzer_id, verein_id, termin_id):
    """Setzt bzw. entfernt das Herz; liefert True, wenn der Termin danach gemerkt ist."""
    conn = get_system_db()
    weg = conn.execute("DELETE FROM termin_merkliste WHERE benutzer_id=? AND verein_id=? AND termin_id=?",
                       (int(benutzer_id), int(verein_id), int(termin_id))).rowcount
    if not weg:
        conn.execute("INSERT INTO termin_merkliste (benutzer_id, verein_id, termin_id, erstellt_am) "
                     "VALUES (?,?,?,?)", (int(benutzer_id), int(verein_id), int(termin_id), now_iso()))
    conn.commit()
    conn.close()
    return not weg


def benutzer_loeschen(uid):
    conn = get_system_db()
    conn.execute("DELETE FROM termin_merkliste WHERE benutzer_id=?", (uid,))
    conn.execute("DELETE FROM passkeys WHERE benutzer_id=?", (uid,))
    conn.execute("DELETE FROM benutzer WHERE id=?", (uid,))
    conn.commit()
    conn.close()


def aktive_admins_anzahl(verein_id):
    """Anzahl aktiver Admin-Konten (Verein + übergreifend) – für den Aussperr-Schutz."""
    conn = get_system_db()
    n = conn.execute(
        "SELECT COUNT(*) c FROM benutzer WHERE rolle='admin' AND aktiv=1 "
        "AND (verein_id=? OR verein_id IS NULL)", (verein_id,)).fetchone()["c"]
    conn.close()
    return n


# ------------------------- Push-Benachrichtigungen -----------------------
def push_abo_speichern(benutzer_id, endpoint, p256dh, auth):
    conn = get_system_db()
    conn.execute("DELETE FROM push_abos WHERE endpoint=?", (endpoint,))  # doppelte vermeiden
    conn.execute("INSERT INTO push_abos (benutzer_id, endpoint, p256dh, auth, erstellt_am) VALUES (?,?,?,?,?)",
                 (benutzer_id, endpoint, p256dh, auth, now_iso()))
    conn.commit()
    conn.close()


def push_abos_alle(benutzer_ids=None):
    """Abos der angegebenen Benutzer; None = alle. Eine LEERE Liste heißt
    „niemand" – nicht „alle", sonst gingen Mitteilungen am Vereinsfilter vorbei."""
    if benutzer_ids is not None and not benutzer_ids:
        return []
    conn = get_system_db()
    if benutzer_ids:
        frag = ",".join("?" * len(benutzer_ids))
        rows = conn.execute(f"SELECT * FROM push_abos WHERE benutzer_id IN ({frag})", list(benutzer_ids)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM push_abos").fetchall()
    conn.close()
    return rows


def push_aus_karte(benutzer_ids):
    """Abbestellte Mitteilungs-Themen je Benutzer: {benutzer_id: {"thema", …}}.
    Leeres Feld (Standard) heißt: alle Themen an."""
    ids = [int(i) for i in benutzer_ids or []]
    if not ids:
        return {}
    conn = get_system_db()
    frag = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT id, COALESCE(push_aus,'') AS push_aus FROM benutzer "
                        f"WHERE id IN ({frag})", ids).fetchall()
    conn.close()
    return {r["id"]: {t.strip() for t in r["push_aus"].split(",") if t.strip()} for r in rows}


def push_abo_loeschen(endpoint):
    conn = get_system_db()
    conn.execute("DELETE FROM push_abos WHERE endpoint=?", (endpoint,))
    conn.commit()
    conn.close()


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Gruppen-Trennung (Mitglieder vs. Freunde/Externe, Schicht-Berechtigung)
# ---------------------------------------------------------------------------
def id_liste(wert):
    """Komma-Liste wie '3,5' robust in [3, 5] wandeln."""
    return [int(x) for x in (wert or "").split(",") if x.strip().isdigit()]


def mitglied_gruppen_filter(einst, alias="m"):
    """SQL-Zusatz (+Parameter), der auf die als „Mitglieder" zählenden Gruppen
    einschränkt. Leere Einstellung = keine Einschränkung (alle zählen wie bisher)."""
    ids = id_liste(einst.get("mitglied_gruppen") if einst else "")
    if not ids:
        return "", []
    ph = ",".join("?" * len(ids))
    return (f" AND EXISTS (SELECT 1 FROM mitglied_gruppe _mgf "
            f"WHERE _mgf.mitglied_id={alias}.id AND _mgf.gruppe_id IN ({ph}))", ids)


# ----------------------------------------------------- Face ID (Passkeys) ---
PASSKEYS_JE_KONTO = 10
NC_FACEID_TAGE = 90
# Vorstandsrollen: Rechteentzug in der Nextcloud soll schneller greifen (Sicherheitsprüfung 21.11.2026)
NC_FACEID_TAGE_VORSTAND = 14
VORSTANDSROLLEN = ("admin", "schatz", "oeffentlichkeit")


def passkey_anfrage_speichern(zweck, challenge_b64, benutzer_id=None, gueltig_s=300):
    """Challenge serverseitig ablegen; liefert die Kennung für die Sitzung."""
    import secrets as _secrets
    import time as _time
    kennung = _secrets.token_urlsafe(24)
    conn = get_system_db()
    conn.execute("DELETE FROM passkey_anfragen WHERE ablauf < ?", (_time.time(),))
    conn.execute("INSERT INTO passkey_anfragen (id, zweck, challenge, benutzer_id, ablauf) VALUES (?,?,?,?,?)",
                 (kennung, zweck, challenge_b64, benutzer_id, _time.time() + gueltig_s))
    conn.commit()
    conn.close()
    return kennung


def passkey_anfrage_einloesen(kennung, zweck, benutzer_id=None):
    """Challenge (base64url) holen und sofort löschen – nur einmal, nur innerhalb der Frist."""
    import time as _time
    if not kennung:
        return None
    conn = get_system_db()
    r = conn.execute("SELECT * FROM passkey_anfragen WHERE id=?", (str(kennung),)).fetchone()
    conn.execute("DELETE FROM passkey_anfragen WHERE id=?", (str(kennung),))
    conn.commit()
    conn.close()
    if not r or r["zweck"] != zweck or r["ablauf"] < _time.time() or r["benutzer_id"] != benutzer_id:
        return None
    return r["challenge"]


def passkeys_fuer_benutzer(benutzer_ids):
    """{benutzer_id: [Geräte]} – für die Zugangsverwaltung des Vorstands."""
    ids = [int(i) for i in benutzer_ids]
    if not ids:
        return {}
    conn = get_system_db()
    rows = conn.execute(f"SELECT id, benutzer_id, name, erstellt_am, zuletzt_am FROM passkeys "
                        f"WHERE benutzer_id IN ({','.join('?' * len(ids))}) ORDER BY id", ids).fetchall()
    conn.close()
    aus = {}
    for r in rows:
        aus.setdefault(r["benutzer_id"], []).append(r)
    return aus


def passkey_loeschen_verwaltung(pid, erlaubte_benutzer_ids):
    """Vorstand entfernt ein Gerät eines Kontos SEINES Vereins; liefert die benutzer_id oder None."""
    conn = get_system_db()
    r = conn.execute("SELECT benutzer_id FROM passkeys WHERE id=?", (pid,)).fetchone()
    if not r or r["benutzer_id"] not in set(erlaubte_benutzer_ids):
        conn.close()
        return None
    conn.execute("DELETE FROM passkeys WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return r["benutzer_id"]


def passkeys_alle_loeschen(benutzer_id):
    """Alle Geräte eines Kontos entfernen (z. B. nach einem Passwort-Reset); liefert die Anzahl."""
    conn = get_system_db()
    n = conn.execute("DELETE FROM passkeys WHERE benutzer_id=?", (benutzer_id,)).rowcount
    conn.commit()
    conn.close()
    return n


def passkeys_liste(benutzer_id):
    conn = get_system_db()
    rows = conn.execute("SELECT id, name, erstellt_am, zuletzt_am, credential_id FROM passkeys "
                        "WHERE benutzer_id=? ORDER BY id", (benutzer_id,)).fetchall()
    conn.close()
    return rows


def passkey_anlegen(benutzer_id, credential_id, public_key, sign_count, transports, name, rp_id):
    conn = get_system_db()
    conn.execute("INSERT INTO passkeys (benutzer_id, credential_id, public_key, sign_count, transports, "
                 "name, rp_id, erstellt_am) VALUES (?,?,?,?,?,?,?,?)",
                 (benutzer_id, credential_id, public_key, sign_count, transports, name, rp_id, now_iso()))
    conn.commit()
    conn.close()


def passkey_per_credential(credential_id):
    conn = get_system_db()
    r = conn.execute("SELECT * FROM passkeys WHERE credential_id=?", (credential_id,)).fetchone()
    conn.close()
    return r


def passkey_benutzt(pid, sign_count):
    conn = get_system_db()
    conn.execute("UPDATE passkeys SET sign_count=?, zuletzt_am=? WHERE id=?", (sign_count, now_iso(), pid))
    conn.commit()
    conn.close()


def passkey_loeschen(pid, benutzer_id):
    """Löscht nur ein EIGENES Gerät; liefert True, wenn etwas gelöscht wurde."""
    conn = get_system_db()
    n = conn.execute("DELETE FROM passkeys WHERE id=? AND benutzer_id=?", (pid, benutzer_id)).rowcount
    conn.commit()
    conn.close()
    return n > 0


def faceid_nc_rest(row):
    """Restzeit, in der Face ID für dieses Konto noch gilt (timedelta), oder None,
    wenn das Konto nicht an Nextcloud hängt (dann ohne Frist). Frist: 90 Tage nach
    der letzten Nextcloud-Anmeldung, für Vorstandsrollen 14 Tage (21.11.2026)."""
    if not (row["nc_benutzer"] or "").strip():
        return None
    am = row["nc_login_am"] if "nc_login_am" in row.keys() else None
    if not am:
        return timedelta(0)
    try:
        zeit = datetime.strptime(str(am)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return timedelta(0)
    tage = NC_FACEID_TAGE_VORSTAND if (row["rolle"] or "") in VORSTANDSROLLEN else NC_FACEID_TAGE
    return zeit + timedelta(days=tage) - datetime.now()


def faceid_nc_abgelaufen(row):
    """True, wenn die Face-ID-Frist abgelaufen ist (siehe faceid_nc_rest) – dann
    ist Face ID bis zur nächsten Nextcloud-Anmeldung gesperrt."""
    rest = faceid_nc_rest(row)
    return rest is not None and rest <= timedelta(0)


def login_gesperrt(email, ip, minuten=15, limit=10):
    """Login-Bremse: True, wenn E-Mail oder IP zu viele Fehlversuche hatte."""
    grenze = (datetime.now() - timedelta(minutes=minuten)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_system_db()
    conn.execute("DELETE FROM login_versuche WHERE zeitpunkt < ?", (grenze,))
    n = conn.execute(
        "SELECT COUNT(*) c FROM login_versuche WHERE zeitpunkt >= ? AND "
        "(email = ? COLLATE NOCASE OR ip = ?)", (grenze, email or "-", ip or "-")).fetchone()["c"]
    conn.commit()
    conn.close()
    return n >= limit


def login_fehlversuch(email, ip):
    conn = get_system_db()
    conn.execute("INSERT INTO login_versuche (email, ip, zeitpunkt) VALUES (?,?,?)",
                 (email or "", ip or "", now_iso()))
    conn.commit()
    conn.close()


def login_erfolg(email):
    """Nach erfolgreichem Login die Fehlversuche dieser E-Mail vergessen."""
    conn = get_system_db()
    conn.execute("DELETE FROM login_versuche WHERE email = ? COLLATE NOCASE", (email or "",))
    conn.commit()
    conn.close()


def tuer_zu_haeufig(benutzer_id, sekunden=5):
    """Missbrauchsschutz: True, wenn dieser Zugang gerade eben schon eine
    Türaktion ausgelöst hat (verhindert Doppelklicks/versehentliches Spammen)."""
    grenze = (datetime.now() - timedelta(seconds=sekunden)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) c FROM tuer_protokoll WHERE benutzer_id=? AND zeitpunkt >= ?",
                     (benutzer_id, grenze)).fetchone()["c"]
    conn.close()
    return n > 0


def tuer_protokoll_schreiben(vid, benutzer_id, mitglied_id, name, aktion, ok, meldung=""):
    conn = get_db()
    conn.execute("INSERT INTO tuer_protokoll (verein_id, benutzer_id, mitglied_id, name, aktion, "
                 "ok, meldung, zeitpunkt) VALUES (?,?,?,?,?,?,?,?)",
                 (vid, benutzer_id, mitglied_id, name, aktion, 1 if ok else 0, meldung, now_iso()))
    conn.commit()
    conn.close()


def schicht_berechtigt(conn, einst, mitglied_id):
    """Darf dieses Mitglied Schichten übernehmen? Leer = alle dürfen (wie bisher)."""
    ids = id_liste(einst.get("schicht_gruppen") if einst else "")
    if not ids:
        return True
    ph = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT 1 FROM mitglied_gruppe WHERE mitglied_id=? AND gruppe_id IN ({ph})",
        [mitglied_id] + ids).fetchone() is not None
