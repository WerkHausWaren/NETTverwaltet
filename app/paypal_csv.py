"""
PayPal-CSV-Import (Aktivitäten-Export) – nur Standardbibliothek.

PayPal bietet unter „Aktivitäten → Herunterladen → CSV" einen Export.
Die Spaltenüberschriften und das Trennzeichen unterscheiden sich je nach
Sprache/Region, deshalb parsen wir tolerant:
  - Trennzeichen automatisch (Semikolon oder Komma)
  - Kodierung UTF-8 (mit/ohne BOM) oder Latin-1
  - deutsches Zahlenformat (1.234,56) und englisches (1,234.56)

Rückgabe: Liste von Umsätzen {datum (ISO), betrag (float, Vorzeichen), text, code}.
Der Betrag ist der Netto-Betrag (Bewegung auf dem PayPal-Guthaben).
"""
import csv
import io


def _decode(roh):
    if isinstance(roh, str):
        return roh
    for kodierung in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return roh.decode(kodierung)
        except (UnicodeDecodeError, AttributeError):
            continue
    return roh.decode("latin-1", "ignore")


def _zahl(s):
    """Wandelt PayPal-Betragstext in float (deutsch oder englisch)."""
    if s is None:
        return 0.0
    t = str(s).strip().replace("\xa0", "").replace(" ", "")
    if not t:
        return 0.0
    neg = t.startswith("-")
    t = t.lstrip("+-")
    if "," in t and "." in t:
        # letztes Trennzeichen ist das Dezimaltrennzeichen
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")      # 1.234,56
        else:
            t = t.replace(",", "")                          # 1,234.56
    elif "," in t:
        t = t.replace(".", "").replace(",", ".")            # 1234,56
    try:
        wert = float(t)
    except ValueError:
        return 0.0
    return -wert if neg else wert


def _iso_datum(s):
    t = (s or "").strip()
    for trenner in (".", "/", "-"):
        if trenner in t:
            teile = t.split(trenner)
            if len(teile) == 3:
                a, b, c = [x.strip() for x in teile]
                if len(a) == 4:                 # JJJJ-MM-TT
                    j, m, tag = a, b, c
                else:                            # TT.MM.JJJJ
                    tag, m, j = a, b, c
                if len(j) == 2:
                    j = "20" + j
                try:
                    return f"{int(j):04d}-{int(m):02d}-{int(tag):02d}"
                except ValueError:
                    return ""
    return ""


def _finde(feldnamen, *stichworte):
    for f in feldnamen:
        fl = (f or "").lower()
        if any(w in fl for w in stichworte):
            return f
    return None


def parse(roh):
    text = _decode(roh)
    # Trennzeichen erkennen (erste Zeile)
    erste = text.split("\n", 1)[0]
    trenner = ";" if erste.count(";") >= erste.count(",") else ","
    leser = csv.DictReader(io.StringIO(text), delimiter=trenner)
    felder = leser.fieldnames or []

    f_datum = _finde(felder, "datum", "date")
    f_netto = _finde(felder, "netto", "net")
    f_brutto = _finde(felder, "brutto", "gross")
    f_name = _finde(felder, "name")
    f_typ = _finde(felder, "typ", "type")
    f_code = _finde(felder, "transaktion", "transaction")
    f_waehrung = _finde(felder, "währung", "waehrung", "currency")

    umsaetze = []
    for zeile in leser:
        if not zeile:
            continue
        # nur EUR (falls Währungsspalte vorhanden)
        if f_waehrung and (zeile.get(f_waehrung) or "").strip().upper() not in ("", "EUR", "€"):
            continue
        datum = _iso_datum(zeile.get(f_datum)) if f_datum else ""
        betrag = _zahl(zeile.get(f_netto)) if f_netto else _zahl(zeile.get(f_brutto))
        if betrag == 0 and not datum:
            continue
        name = (zeile.get(f_name) or "").strip() if f_name else ""
        typ = (zeile.get(f_typ) or "").strip() if f_typ else ""
        text_teile = [x for x in (name, typ) if x]
        umsaetze.append({
            "datum": datum,
            "betrag": betrag,
            "text": " · ".join(text_teile) or "PayPal-Umsatz",
            "code": (zeile.get(f_code) or "").strip() if f_code else "",
        })
    umsaetze.sort(key=lambda x: x["datum"], reverse=True)
    return umsaetze
