"""
Kontenrahmen für gemeinnützige Vereine.

Gemeinnützige Vereine unterscheiden vier steuerliche „Sphären":
  - ideell          : Ideeller Bereich (steuerfrei) – Beiträge, Spenden, Zuschüsse
  - vermoegen       : Vermögensverwaltung – Zinsen, Miete/Pacht
  - zweckbetrieb    : Zweckbetrieb (steuerbegünstigt) – z.B. Eintritt Kulturveranstaltung/Kino
  - wirtschaftlich  : Wirtschaftlicher Geschäftsbetrieb (steuerpflichtig) – z.B. Getränkeverkauf

Die Standardkonten sind an den DATEV-SKR-49 (Vereine) angelehnt und bewusst
auf das Wesentliche eingedampft. Eigene Konten lassen sich ergänzen.
"""

SPHAEREN = {
    "ideell": "Ideeller Bereich (steuerfrei)",
    "vermoegen": "Vermögensverwaltung",
    "zweckbetrieb": "Zweckbetrieb (steuerbegünstigt)",
    "wirtschaftlich": "Wirtschaftl. Geschäftsbetrieb (steuerpflichtig)",
}
SPHAEREN_REIHENFOLGE = ["ideell", "vermoegen", "zweckbetrieb", "wirtschaftlich"]

# (nummer, name, typ, sphaere)
STANDARD_KONTEN = [
    # Ideeller Bereich
    ("0100", "Mitgliedsbeiträge", "einnahme", "ideell"),
    ("0110", "Aufnahmegebühren", "einnahme", "ideell"),
    ("0120", "Spenden", "einnahme", "ideell"),
    ("0130", "Öffentliche Zuschüsse / Fördermittel", "einnahme", "ideell"),
    ("0140", "Sonstige ideelle Einnahmen", "einnahme", "ideell"),
    ("0200", "Verwaltungskosten", "ausgabe", "ideell"),
    ("0210", "Porto, Büromaterial", "ausgabe", "ideell"),
    ("0220", "Versicherungen, Beiträge", "ausgabe", "ideell"),
    ("0230", "Miete / Raumkosten", "ausgabe", "ideell"),
    ("0240", "Öffentlichkeitsarbeit", "ausgabe", "ideell"),
    ("0250", "Aus- und Fortbildung", "ausgabe", "ideell"),
    ("0290", "Sonstige ideelle Ausgaben", "ausgabe", "ideell"),
    # Vermögensverwaltung
    ("3100", "Zinserträge", "einnahme", "vermoegen"),
    ("3110", "Miet- / Pachteinnahmen", "einnahme", "vermoegen"),
    ("3200", "Kosten der Vermögensverwaltung", "ausgabe", "vermoegen"),
    # Zweckbetrieb
    ("4100", "Eintrittsgelder Veranstaltungen", "einnahme", "zweckbetrieb"),
    ("4110", "Teilnahme- / Kursgebühren", "einnahme", "zweckbetrieb"),
    ("4120", "Zuschüsse Zweckbetrieb", "einnahme", "zweckbetrieb"),
    ("4200", "Veranstaltungskosten", "ausgabe", "zweckbetrieb"),
    ("4210", "Honorare / Künstlergagen", "ausgabe", "zweckbetrieb"),
    ("4220", "GEMA / Lizenzen", "ausgabe", "zweckbetrieb"),
    ("4230", "Filmmiete / Leihgebühren", "ausgabe", "zweckbetrieb"),
    # Wirtschaftlicher Geschäftsbetrieb
    ("5100", "Verkauf Speisen / Getränke", "einnahme", "wirtschaftlich"),
    ("5110", "Werbung / Sponsoring", "einnahme", "wirtschaftlich"),
    ("5120", "Sonstige wirtschaftliche Einnahmen", "einnahme", "wirtschaftlich"),
    ("5200", "Wareneinkauf", "ausgabe", "wirtschaftlich"),
    ("5210", "Kosten wirtschaftl. Geschäftsbetrieb", "ausgabe", "wirtschaftlich"),
]


def sichere_konten(conn, verein_id):
    """Legt die Standardkonten an, wenn der Verein noch keine hat."""
    anzahl = conn.execute("SELECT COUNT(*) c FROM konten WHERE verein_id=?", (verein_id,)).fetchone()["c"]
    if anzahl == 0:
        for nummer, name, typ, sphaere in STANDARD_KONTEN:
            conn.execute("INSERT INTO konten (verein_id, nummer, name, typ, sphaere, aktiv) VALUES (?,?,?,?,?,1)",
                         (verein_id, nummer, name, typ, sphaere))
        conn.commit()


# ---------------------------------------------------------------------------
# Geldkonten (echte Geldtöpfe – anders als der Kontenrahmen oben!)
# Der Kontenrahmen sagt WOFÜR Geld floss (Kategorie/Sphäre),
# das Geldkonto sagt WO das Geld liegt (Barkasse, Bankkonto, PayPal).
# ---------------------------------------------------------------------------
GELDKONTO_TYPEN = {
    "bar": "Barkasse",
    "bank": "Bankkonto",
    "paypal": "PayPal",
    "sonstig": "Sonstiges",
}
GELDKONTO_ICON = {"bar": "💶", "bank": "🏦", "paypal": "🅿️", "sonstig": "💳"}

# (name, typ) – beim ersten Aufruf angelegt
STANDARD_GELDKONTEN = [
    ("Barkasse", "bar"),
    ("Bankkonto (Sparkasse)", "bank"),
    ("PayPal", "paypal"),
]


def sichere_geldkonten(conn, verein_id):
    """Legt die Standard-Geldkonten an, wenn der Verein noch keine hat."""
    anzahl = conn.execute("SELECT COUNT(*) c FROM geldkonten WHERE verein_id=?", (verein_id,)).fetchone()["c"]
    if anzahl == 0:
        for i, (name, typ) in enumerate(STANDARD_GELDKONTEN):
            conn.execute("INSERT INTO geldkonten (verein_id, name, typ, anfangsbestand, aktiv, sortierung) "
                         "VALUES (?,?,?,0,1,?)", (verein_id, name, typ, i))
        conn.commit()


# ---------------------------------------------------------------------------
# Einfacher Modus: Alltags-Kategorien in Klartext, gemappt auf Standardkonten.
# (label, emoji, konto_nummer) – die Kontonummer bestimmt Typ + Sphäre.
# ---------------------------------------------------------------------------
EINFACH_EINNAHMEN = [
    ("Mitgliedsbeitrag", "👤", "0100"),
    ("Spende", "🎁", "0120"),
    ("Zuschuss / Fördermittel", "🏛️", "0130"),
    ("Eintritt Veranstaltung", "🎟️", "4100"),
    ("Kurs- / Teilnahmegebühr", "📚", "4110"),
    ("Getränke- / Speisenverkauf", "🥤", "5100"),
]
EINFACH_AUSGABEN = [
    ("Miete / Raumkosten", "🏠", "0230"),
    ("Porto / Büromaterial", "✉️", "0210"),
    ("Versicherung / Beiträge", "🛡️", "0220"),
    ("Öffentlichkeitsarbeit", "📣", "0240"),
    ("Veranstaltungskosten", "🎪", "4200"),
    ("Honorar / Künstlergage", "🎤", "4210"),
    ("GEMA / Lizenzen", "🎵", "4220"),
    ("Wareneinkauf", "🛒", "5200"),
]


def bank_saldo_merken(conn, verein_id, saldo):
    """Merkt den von der Bank gemeldeten Kontostand am ERSTEN aktiven
    Bank-Geldkonto (gleiche Konvention wie beim Verbuchen) – Grundlage für
    die Abgleich-Anzeige im Kassenbuch. saldo=None tut nichts."""
    if saldo is None:
        return
    from datetime import datetime
    conn.execute(
        "UPDATE geldkonten SET bank_saldo=?, bank_saldo_datum=? "
        "WHERE id=(SELECT id FROM geldkonten WHERE verein_id=? AND typ='bank' "
        "AND aktiv=1 ORDER BY sortierung, id LIMIT 1)",
        (float(saldo), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), verein_id))
