"""Kontakte aus Fremdformaten einlesen.

Liest die Dateien, die Vereine typischerweise in der Hand haben:

  • CSV   – aus Excel, Outlook, Google Kontakte, Nextcloud (Trennzeichen und
            Zeichenkodierung werden erkannt, auch Windows-Exporte)
  • XLSX  – Excel-Arbeitsmappen (ohne Zusatzpaket, nur Standardbibliothek)
  • VCF   – vCard, wie Handy, Apple Kontakte und Nextcloud sie ausgeben

Ergebnis ist immer dasselbe: eine Kopfzeile und Datenzeilen. Die Zuordnung der
fremden Spaltennamen auf unsere Felder übernimmt `spalten_raten()`; was nicht
erkannt wird, ordnet der Mensch in der Oberfläche selbst zu.

Bewusst ohne neue Abhängigkeit – die App soll auf jedem Rechner ohne
Installationsakrobatik laufen.
"""
import csv
import io
import re
import zipfile
from xml.etree import ElementTree as ET

# Unsere Zielfelder (identisch mit kontakte.FELDER)
ZIELFELDER = ("name", "ansprechpartner", "strasse", "plz", "ort", "email",
              "telefon", "notizen", "kategorie")

ZIELFELD_NAMEN = {
    "name": "Name / Firma", "ansprechpartner": "Ansprechpartner:in",
    "strasse": "Straße", "plz": "PLZ", "ort": "Ort", "email": "E-Mail",
    "telefon": "Telefon", "notizen": "Notizen", "kategorie": "Kategorie",
}

MAX_BYTES = 8 * 1024 * 1024     # 8 MB reichen für zehntausende Kontakte
MAX_ZEILEN = 5000               # Schutz vor versehentlich riesigen Dateien


class ImportFehler(RuntimeError):
    """Fehler mit einer Meldung, die man einem Menschen zeigen kann."""


# ── Spaltennamen fremder Programme auf unsere Felder abbilden ──────────────
# Kleinschreibung, ohne Satzzeichen – der Vergleich normalisiert beide Seiten.
SYNONYME = {
    "name": [
        "name", "firma", "firmenname", "organisation", "organisationsname",
        "company", "company name", "organization", "organization name", "org",
        "unternehmen", "institution", "redaktion", "medium", "anzeigename",
        "display name", "vollstandiger name", "full name", "fn", "kontakt",
        "empfanger", "empfangername", "verein", "verband", "trager",
        "einrichtung", "verlag", "agentur", "buro", "praxis", "betrieb",
        "geschaft", "laden", "lieferant", "sponsor", "partner",
    ],
    "ansprechpartner": [
        "ansprechpartner", "ansprechpartnerin", "ansprechpartner in",
        "kontaktperson", "person", "contact", "contact person", "contact name",
        "vorname nachname", "nachname vorname", "given name", "vorname",
        "first name", "last name", "nachname", "familienname", "surname",
        "redakteur", "redakteurin", "journalist", "journalistin", "betreuer",
    ],
    "strasse": [
        "strasse", "strasse nr", "strasse hausnummer", "street", "address",
        "adresse", "anschrift", "street address", "business street",
        "strasse geschaftlich", "strasse privat", "home street", "adresse 1",
        "address 1", "street 1", "hausanschrift",
    ],
    "plz": [
        "plz", "postleitzahl", "zip", "zip code", "postal code", "postcode",
        "plz geschaftlich", "business postal code", "home postal code",
        "postleitzahl geschaftlich",
    ],
    "ort": [
        "ort", "stadt", "city", "wohnort", "business city", "home city",
        "ort geschaftlich", "gemeinde", "town",
    ],
    "email": [
        "email", "e mail", "e mail adresse", "email adresse", "mail",
        "email address", "e mail address", "e mail 1 adresse",
        "e mail adresse 1", "email 1 value", "primary email", "mailadresse",
        "email1", "e mail1",
    ],
    "telefon": [
        "telefon", "tel", "telefonnummer", "phone", "phone number",
        "telefon geschaftlich", "business phone", "home phone", "mobil",
        "mobiltelefon", "handy", "mobile", "mobile phone", "cell phone",
        "phone 1 value", "telefon privat", "festnetz", "durchwahl",
    ],
    "notizen": [
        "notizen", "notiz", "bemerkung", "bemerkungen", "notes", "note",
        "kommentar", "comment", "beschreibung", "description", "info",
        "hinweis", "hinweise", "anmerkung", "anmerkungen",
    ],
    "kategorie": [
        "kategorie", "kategorien", "gruppe", "gruppen", "category",
        "categories", "label", "labels", "group", "group membership",
        "tags", "tag", "art", "typ", "type", "rubrik", "bereich",
    ],
}


def _normal(text):
    """Vergleichsform: klein, ohne Umlaute, ohne Satz- und Sonderzeichen."""
    t = (text or "").strip().lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss"), ("é", "e")):
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


# Wie aussagekräftig ist ein Treffer? „city" oder „PLZ" sind eindeutig,
# „name" und „address" sind Allerweltswörter – deshalb dürfen die eindeutigen
# gewinnen, wenn eine Spalte auf mehrere Felder passt.
# Beispiel: „Address 1 - City" enthält „address" UND „city" – gemeint ist der Ort.
_RANG = {"plz": 0, "ort": 1, "email": 2, "telefon": 3, "kategorie": 4,
         "notizen": 5, "strasse": 6, "ansprechpartner": 7, "name": 8}

# Felder, die mehrere Spalten aufnehmen dürfen (Vorname + Nachname → eine Person)
_MEHRFACH = {"ansprechpartner"}

_SYN_INDEX = {}
for _ziel, _namen in SYNONYME.items():
    for _n in _namen:
        _SYN_INDEX.setdefault(_normal(_n), _ziel)


_VORNAME_SPALTEN = {"vorname", "first name", "given name", "rufname", "vornamen"}


def ist_vornamen_spalte(ueberschrift):
    """Damit aus „Nachname;Vorname" nicht „Groß Jörg", sondern „Jörg Groß" wird."""
    return _normal(ueberschrift) in {_normal(x) for x in _VORNAME_SPALTEN}


def _enthaelt_woerter(text_woerter, muster_woerter):
    """Kommt die Wortfolge als Ganzes vor? (Wortweise, damit „ort" nicht in
    „Sortierung" trifft.)"""
    n, m = len(text_woerter), len(muster_woerter)
    return any(text_woerter[i:i + m] == muster_woerter for i in range(n - m + 1))


def spalten_raten(kopf):
    """Ordnet jede Spalte der Datei einem Zielfeld zu ('' = nicht zuordnen).

    Bewertet erst alle denkbaren Paarungen und vergibt dann die besten zuerst.
    Dadurch schlägt „Organization Name" die generische Spalte „Name" beim
    Firmenfeld, und „Address 1 - City" landet beim Ort statt bei der Straße.
    """
    kandidaten = []
    for i, spalte in enumerate(kopf):
        woerter = _normal(spalte).split()
        if not woerter:
            continue
        for muster, feld in _SYN_INDEX.items():
            m_woerter = muster.split()
            if woerter == m_woerter:
                guete = 2                                   # exakte Überschrift
            elif _enthaelt_woerter(woerter, m_woerter):
                guete = 1                                   # z. B. „E-Mail (privat)"
            else:
                continue
            punkte = guete * 10000 + (100 - _RANG[feld] * 10) + len(muster)
            kandidaten.append((punkte, i, feld))

    zuordnung = [""] * len(kopf)
    belegt_spalte, belegt_feld = set(), set()
    for _punkte, i, feld in sorted(kandidaten, reverse=True):
        if i in belegt_spalte or (feld in belegt_feld and feld not in _MEHRFACH):
            continue
        zuordnung[i] = feld
        belegt_spalte.add(i)
        belegt_feld.add(feld)
    return zuordnung


# ── CSV ───────────────────────────────────────────────────────────────────
def _text_dekodieren(rohdaten):
    """Zeichenkodierung erkennen. Windows-Programme liefern selten UTF-8."""
    import unicodedata
    if rohdaten.startswith(b"\xef\xbb\xbf"):
        text = rohdaten.decode("utf-8-sig")
    elif rohdaten.startswith((b"\xff\xfe", b"\xfe\xff")):      # Excel „Unicode Text"
        text = rohdaten.decode("utf-16")
    else:
        for kodierung in ("utf-8", "cp1252", "latin-1"):
            try:
                text = rohdaten.decode(kodierung)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = rohdaten.decode("utf-8", errors="replace")
    # macOS speichert „ä" gern als a + Trema. Ohne Vereinheitlichung findet die
    # Suche solche Namen später nicht und Dubletten werden nicht erkannt.
    return unicodedata.normalize("NFC", text.replace("\x00", ""))


def _trennzeichen(text):
    """Trennzeichen anhand der Kopfzeile bestimmen (außerhalb von Anführungszeichen)."""
    kopf = text.split("\n", 1)[0]
    ohne_zitate = re.sub(r'"[^"]*"', "", kopf)
    zaehler = {z: ohne_zitate.count(z) for z in (";", ",", "\t", "|")}
    bestes = max(zaehler, key=zaehler.get)
    return bestes if zaehler[bestes] else ";"


def _csv_lesen(rohdaten):
    text = _text_dekodieren(rohdaten)
    if not text.strip():
        raise ImportFehler("Die Datei ist leer.")
    leser = csv.reader(io.StringIO(text, newline=""), delimiter=_trennzeichen(text))
    zeilen = [z for z in leser if any((f or "").strip() for f in z)]
    if not zeilen:
        raise ImportFehler("In der Datei stehen keine Daten.")
    kopf = [(f or "").strip() for f in zeilen[0]]
    return kopf, [z for z in zeilen[1:]]


# ── XLSX ──────────────────────────────────────────────────────────────────
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _spalten_index(zellbezug):
    """'B7' -> 1 (0-basiert). Nötig, weil Excel leere Zellen einfach weglässt –
    ohne diese Umrechnung verrutschen ganze Zeilen."""
    buchstaben = re.match(r"([A-Z]+)", (zellbezug or "").upper())
    if not buchstaben:
        return None
    n = 0
    for z in buchstaben.group(1):
        n = n * 26 + (ord(z) - 64)
    return n - 1


def _erstes_blatt(zipdatei):
    """Name des ersten Arbeitsblatts laut Arbeitsmappe – nicht nach Dateiname
    raten, denn sheet1.xml ist nicht zwingend das erste Blatt."""
    try:
        mappe = ET.fromstring(zipdatei.read("xl/workbook.xml"))
        rels = ET.fromstring(zipdatei.read("xl/_rels/workbook.xml.rels"))
        ziel = {r.get("Id"): r.get("Target") for r in rels}
        blatt = mappe.find("m:sheets/m:sheet", _NS)
        if blatt is not None:
            rid = blatt.get("{%s}id" % _NS["r"])
            pfad = (ziel.get(rid) or "").lstrip("/")
            if pfad:
                return pfad if pfad.startswith("xl/") else "xl/" + pfad
    except (KeyError, ET.ParseError):
        pass
    kandidaten = sorted(n for n in zipdatei.namelist()
                        if n.startswith("xl/worksheets/") and n.endswith(".xml"))
    if not kandidaten:
        raise ImportFehler("In der Excel-Datei ist kein Arbeitsblatt zu finden.")
    return kandidaten[0]


def _xlsx_lesen(rohdaten):
    try:
        z = zipfile.ZipFile(io.BytesIO(rohdaten))
    except zipfile.BadZipFile:
        raise ImportFehler(
            "Diese Datei ist keine gültige Excel-Datei (.xlsx). Falls es eine alte "
            ".xls-Datei ist: In Excel über „Speichern unter“ als .xlsx oder CSV sichern.")
    # Gemeinsame Zeichenketten – Excel speichert Texte zentral und verweist nur darauf
    texte = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
            texte.append("".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"])))
    blattpfad = _erstes_blatt(z)
    entpackt = sum(i.file_size for i in z.infolist())
    if entpackt > 20 * MAX_BYTES:      # komprimiert klein, entpackt riesig
        raise ImportFehler(
            "Diese Excel-Datei ist entpackt zu umfangreich. Bitte nur das Tabellenblatt "
            "mit den Kontakten in eine neue Datei kopieren und diese hochladen.")
    blatt = ET.fromstring(z.read(blattpfad))
    zeilen = []
    for zeile in blatt.iter("{%s}row" % _NS["m"]):
        werte = []
        for zelle in zeile:
            idx = _spalten_index(zelle.get("r"))
            if idx is None:
                idx = len(werte)
            while len(werte) < idx:                 # ausgelassene leere Zellen auffüllen
                werte.append("")
            typ = zelle.get("t")
            if typ == "inlineStr":
                wert = "".join(t.text or "" for t in zelle.iter("{%s}t" % _NS["m"]))
            else:
                v = zelle.find("m:v", _NS)
                wert = v.text if v is not None and v.text is not None else ""
                if typ == "s":
                    try:
                        wert = texte[int(wert)]
                    except (ValueError, IndexError):
                        wert = ""
                elif typ == "b":
                    wert = "ja" if wert == "1" else "nein"
                elif wert and re.fullmatch(r"-?\d+\.0+", wert):
                    wert = wert.split(".")[0]       # 5.0 -> 5 (Excel speichert alles als Fließkomma)
            werte.append(wert)
        if any((w or "").strip() for w in werte):
            zeilen.append(werte)
        if len(zeilen) > MAX_ZEILEN + 1:
            break
    if not zeilen:
        raise ImportFehler("Das erste Arbeitsblatt der Excel-Datei ist leer.")
    kopf = [(w or "").strip() for w in zeilen[0]]
    return kopf, zeilen[1:]


# ── vCard ─────────────────────────────────────────────────────────────────
# Eigener Leser statt carddav.vcard_parsen: der ist auf den Personen-Abgleich
# per CardDAV zugeschnitten und kennt weder FN/NOTE/CATEGORIES noch die
# Eigenheiten exportierter Dateien (Apple-Präfixe, Quoted-Printable, Fax).

def _vcard_entfalten(text):
    """Zeilenfaltung aufheben – erst die Quoted-Printable-Fortsetzung (Zeile
    endet auf „="), dann die normale vCard-Faltung (Folgezeile beginnt mit
    Leerzeichen)."""
    text = re.sub(r"=\r?\n(?=[^\r\n])", "", text)
    return re.sub(r"\r?\n[ \t]", "", text)


def _qp_dekodieren(wert, kodierung):
    """=C3=BC → ü. Ältere Handys und Outlook 2003 exportieren so."""
    import quopri
    try:
        roh = quopri.decodestring(wert.encode("ascii", errors="ignore"))
        return roh.decode(kodierung or "utf-8", errors="replace")
    except (ValueError, LookupError):
        return wert


def _vcard_unescape(wert):
    """\\n \\, \\; \\\\ auflösen – in einem Durchgang, damit aus \\\\n kein Umbruch wird."""
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "N": "\n"}.get(m.group(1), m.group(1)), wert or "")


def _vcard_teilen(wert):
    """An unmaskierten Semikola trennen (ADR, N, ORG)."""
    return [_vcard_unescape(t) for t in re.split(r"(?<!\\);", wert)]


def _vcard_zeilen(block):
    """(FELDNAME, Parameter-Text, entschlüsselter Wert) je Zeile."""
    for zeile in re.split(r"\r\n|\r|\n", _vcard_entfalten(block)):
        if ":" not in zeile:
            continue
        kopf, wert = zeile.split(":", 1)
        teile = kopf.split(";")
        # Apple schreibt „item1.EMAIL" – ohne Abschneiden geht die Adresse verloren
        name = teile[0].split(".")[-1].strip().upper()
        params = ";".join(teile[1:]).upper()
        if "QUOTED-PRINTABLE" in params:
            kodierung = "utf-8"
            m = re.search(r"CHARSET=([A-Z0-9\-]+)", params)
            if m:
                kodierung = m.group(1)
            wert = _qp_dekodieren(wert, kodierung)
        yield name, params, wert.strip()


def _vcard_kontakt(block):
    """Eine vCard → unsere Felder. Firmenname hat Vorrang vor dem Personennamen."""
    fn = org = abteilung = vorname = nachname = note = kategorie = ""
    strasse = plz = ort = ""
    email = telefon = fax = ""
    for name, params, wert in _vcard_zeilen(block):
        if not wert:
            continue
        if name == "FN":
            fn = fn or _vcard_unescape(wert)
        elif name == "ORG":
            t = _vcard_teilen(wert)
            org = org or (t[0] if t else "")
            abteilung = abteilung or (t[1] if len(t) > 1 else "")
        elif name == "N":
            t = _vcard_teilen(wert)
            nachname = nachname or (t[0] if t else "")
            vorname = vorname or (t[1] if len(t) > 1 else "")
        elif name == "NOTE":
            note = note or _vcard_unescape(wert)
        elif name == "CATEGORIES":
            if not kategorie:
                kategorie = _vcard_unescape(re.split(r"(?<!\\),", wert)[0]).strip()
        elif name == "EMAIL":
            if not email:
                email = re.sub(r"^mailto:", "", wert, flags=re.I).strip()
        elif name == "TEL":
            nummer = re.sub(r"^tel:", "", wert, flags=re.I).strip()
            if "FAX" in params:
                fax = fax or nummer
            elif not telefon or ("WORK" in params and "CELL" not in params):
                telefon = telefon or nummer
        elif name == "ADR":
            t = _vcard_teilen(wert)          # PObox;Zusatz;Straße;Ort;Region;PLZ;Land
            if not (strasse or ort or plz):
                strasse = t[2] if len(t) > 2 else ""
                ort = t[3] if len(t) > 3 else ""
                plz = t[5] if len(t) > 5 else ""
    person = " ".join(x for x in (vorname, nachname) if x).strip()
    name = org or fn or person
    if not name:
        return None
    ansprechpartner = person if person and person != name else (fn if fn and fn != name else "")
    if abteilung and abteilung not in ansprechpartner:
        ansprechpartner = (ansprechpartner + " · " + abteilung).strip(" ·")
    return [name, ansprechpartner, strasse, plz, ort, email,
            telefon or fax, note.replace("\n", " ").strip(), kategorie]


def _vcard_lesen(rohdaten):
    """vCards als Tabelle aufbereiten – eine Zeile je Kontakt."""
    text = _text_dekodieren(rohdaten)
    bloecke = re.findall(r"BEGIN\s*:\s*VCARD.*?END\s*:\s*VCARD", text, re.S | re.I)
    if not bloecke:
        raise ImportFehler("In dieser .vcf-Datei ist kein Kontakt zu finden.")
    zeilen = [z for z in (_vcard_kontakt(b) for b in bloecke[:MAX_ZEILEN]) if z]
    if not zeilen:
        raise ImportFehler("Die .vcf-Datei enthält keine Kontakte mit Namen.")
    return list(ZIELFELDER), zeilen


# ── Einstieg ──────────────────────────────────────────────────────────────
def kategorie_saeubern(wert):
    """Google trennt mehrere Labels mit „ ::: " und schleppt technische Werte
    wie „* myContacts" mit; Outlook trennt mit Semikolon. Wir nehmen das erste
    echte Label."""
    text = (wert or "").replace(" ::: ", ";").replace(",", ";")
    muell = {"* mycontacts", "mycontacts", "meine kontakte", "starred", "* starred"}
    for teil in text.split(";"):
        t = teil.strip()
        if t and t.lower().lstrip("* ") not in {m.lstrip("* ") for m in muell}:
            return t
    return ""


def plz_reparieren(wert):
    """01067 Dresden überlebt Excel nicht – vierstellige Zahlen wieder auffüllen."""
    w = (wert or "").strip()
    return w.zfill(5) if w.isdigit() and len(w) == 4 else w


def datei_lesen(dateiname, rohdaten):
    """(kopf, zeilen, format) – wirft ImportFehler mit verständlicher Meldung."""
    if not rohdaten:
        raise ImportFehler("Die Datei ist leer.")
    if len(rohdaten) > MAX_BYTES:
        raise ImportFehler(
            f"Die Datei ist zu groß ({len(rohdaten) // 1024 // 1024} MB). "
            f"Erlaubt sind {MAX_BYTES // 1024 // 1024} MB.")
    endung = (dateiname or "").rsplit(".", 1)[-1].lower()
    try:
        if endung == "xlsx" or rohdaten[:2] == b"PK":
            kopf, zeilen = _xlsx_lesen(rohdaten)
            art = "Excel"
        elif endung in ("vcf", "vcard") or rohdaten[:11].upper().startswith(b"BEGIN:VCARD"):
            kopf, zeilen = _vcard_lesen(rohdaten)
            art = "vCard"
        elif endung == "xls":
            raise ImportFehler(
                "Das alte Excel-Format (.xls) kann nicht gelesen werden. Bitte in Excel "
                "über „Speichern unter“ als .xlsx oder CSV sichern und erneut hochladen.")
        else:
            kopf, zeilen = _csv_lesen(rohdaten)
            art = "CSV"
    except ImportFehler:
        raise
    except Exception:      # noqa: BLE001 – lieber eine klare Meldung als eine Fehlerseite
        raise ImportFehler(
            "Diese Datei konnte nicht gelesen werden. Möglicherweise ist sie beschädigt "
            "oder mit einem Kennwort geschützt. Bitte in Excel über „Speichern unter“ "
            "als .xlsx oder CSV sichern und noch einmal versuchen.")
    if not any(k.strip() for k in kopf):
        raise ImportFehler(
            "In der ersten Zeile der Datei stehen offenbar keine Spaltenüberschriften. "
            "Bitte Überschriften wie Name, Straße, PLZ, Ort in die erste Zeile setzen "
            "und eine eventuelle Titelzeile darüber löschen.")
    if len(zeilen) > MAX_ZEILEN:
        raise ImportFehler(
            f"Die Datei enthält {len(zeilen)} Zeilen – das sind mehr als die erlaubten "
            f"{MAX_ZEILEN}. Bitte in kleinere Dateien aufteilen.")
    breite = len(kopf)
    zeilen = [(list(z) + [""] * breite)[:breite] for z in zeilen]
    return kopf, zeilen, art
