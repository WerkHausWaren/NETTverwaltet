"""Bausteine für die Terminliste (19.11.2026): schnell verstehen, schnell aktiv werden.

- ZIELGRUPPEN / EINTRITT_ARTEN: feste Auswahl für Formular, Etiketten und Filter
- teaser(): Kurztext ohne angehängte Textbausteine („- - -“, „7 EUR“ …)
- buendeln(): wiederkehrende Termine (gleicher Titel, ≥ 3×) zu einer Karte zusammenfassen
- schnellfilter(): Datumsbereiche für „Heute“, „Wochenende“, „Nächste 7 Tage“
- hinweise(): „Heute“/„Morgen“, „Beginnt in 3 Std.“, „Anmeldung nur noch bis …“

Alles arbeitet auf dicts (bzw. sqlite3.Row über dict()) und ist ohne Flask testbar.
"""
import math
import re
from datetime import date, datetime, timedelta

# (Schlüssel, Etikett) – Reihenfolge = Anzeige-Reihenfolge
ZIELGRUPPEN = [
    ("familien", "Familien"),
    ("kinder", "Kinder willkommen"),
    ("jugend", "Jugendliche"),
    ("senioren", "Senior:innen"),
    ("barrierefrei", "barrierefrei"),
    ("englisch", "auch auf Englisch"),
]
ZIELGRUPPEN_KEYS = {k for k, _ in ZIELGRUPPEN}
ZIELGRUPPEN_NAMEN = dict(ZIELGRUPPEN)

# '' = keine Angabe (der Preis-Standard 0 heißt oft „nicht eingetragen“ – daher eigenes Feld)
EINTRITT_ARTEN = [("", "keine Angabe"), ("frei", "Eintritt frei"), ("spende", "Spende erwünscht")]
EINTRITT_KEYS = {k for k, _ in EINTRITT_ARTEN}

WT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MONATE_KURZ = ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."]

_TRENNER = re.compile(r"^\s*([-–—_*=~·.]\s*){3,}$")
_NUR_PREIS = re.compile(r"^\s*(eintritt:?\s*)?\d+([.,]\d{1,2})?\s*(€|eur|euro)\s*$", re.I)


def zielgruppen_liste(roh):
    """'familien,kinder' → [('familien', '👨‍👩‍👧 Familien'), …] (unbekannte Schlüssel fallen weg)."""
    keys = [k.strip() for k in str(roh or "").split(",") if k.strip() in ZIELGRUPPEN_KEYS]
    return [(k, ZIELGRUPPEN_NAMEN[k]) for k, _ in ZIELGRUPPEN if k in keys]


def zielgruppen_aus_formular(werte):
    """Liste der angehakten Formularwerte → gespeicherter Text (nur bekannte Schlüssel)."""
    return ",".join(k for k, _ in ZIELGRUPPEN if k in set(werte or []))


def teaser(text, laenge=170):
    """Kurztext für Karten: schneidet alles ab der ersten Trennlinie ab und lässt
    Zeilen weg, die nur einen Preis nennen; danach eine Zeile, sinnvoll gekürzt."""
    zeilen = []
    for z in str(text or "").replace("\r", "").split("\n"):
        if _TRENNER.match(z):
            break
        if _NUR_PREIS.match(z):
            continue
        zeilen.append(z.strip())
    t = re.sub(r"\s+", " ", " ".join(z for z in zeilen if z)).strip()
    if len(t) <= laenge:
        return t
    schnitt = t[:laenge].rsplit(" ", 1)[0].rstrip(",;:–- ")
    return schnitt + " …"


def _hm(wert):
    """'14:00' → '14', '09:30' → '9:30' (für kurze Zeitangaben)."""
    w = (wert or "")[:5]
    if not re.fullmatch(r"\d{2}:\d{2}", w):
        return w
    std, minute = w.split(":")
    std = str(int(std))
    return std if minute == "00" else f"{std}:{minute}"


def _zeitspanne(von, bis):
    if not von:
        return ""
    return f"{_hm(von)}–{_hm(bis)} Uhr" if bis else f"{_hm(von)} Uhr"


def muster(termine):
    """Regel aus mehreren Terminen: 'Mo 14–18 Uhr · Mi 14–16 Uhr' (nach Wochentag sortiert)."""
    gesehen = []
    for t in termine:
        try:
            wt = date.fromisoformat(t["datum"]).weekday()
        except (ValueError, TypeError):
            continue
        eintrag = (wt, (t.get("uhrzeit") or "")[:5], (t.get("uhrzeit_bis") or "")[:5])
        if eintrag not in gesehen:
            gesehen.append(eintrag)
    gesehen.sort()
    return " · ".join(f"{WT[wt]} {_zeitspanne(v, b)}".strip() for wt, v, b in gesehen[:4])


def buendeln(liste, mindestens=3):
    """Fasst Termine mit gleichem Titel (≥ mindestens Vorkommen) zu EINER Karte zusammen:
    der nächste Termin bleibt an seiner Stelle und bekommt 'wiederkehrend' =
    {'muster', 'anzahl', 'suche'}; die übrigen fallen aus der Liste.
    Nicht gebündelt werden verborgene, abgesagte und Termine mit Anmeldung
    (dort zählt jeder einzelne Termin), ebenso Highlights."""
    def schluessel(t):
        if t.get("verborgen") or t.get("abgesagt") or t.get("anmeldung_aktiv") or t.get("highlight"):
            return None
        return re.sub(r"\s+", " ", str(t.get("titel") or "")).strip().casefold() or None

    gruppen = {}
    for t in liste:
        s = schluessel(t)
        if s:
            gruppen.setdefault(s, []).append(t)
    ergebnis, erledigt = [], set()
    for t in liste:
        s = schluessel(t)
        gruppe = gruppen.get(s) if s else None
        if not gruppe or len(gruppe) < mindestens:
            ergebnis.append(t)
            continue
        if s in erledigt:
            continue
        erledigt.add(s)
        erster = dict(gruppe[0])
        erster["wiederkehrend"] = {"muster": muster(gruppe), "anzahl": len(gruppe),
                                   "suche": str(erster.get("titel") or "")}
        ergebnis.append(erster)
    return ergebnis


def highlights_einmal(liste, hoechstens=3):
    """Highlights entdoppeln: von einer Terminreihe (serie_id bzw. gleicher Titel) nur das
    erste (= nächste) Vorkommen – sonst stünde eine Reihe mit Haken mehrfach oben."""
    gesehen, erg = set(), []
    for t in liste:
        schluessel = t.get("serie_id") or re.sub(r"\s+", " ", str(t.get("titel") or "")).strip().casefold()
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        erg.append(t)
        if len(erg) >= hoechstens:
            break
    return erg


def uhrzeit_oder_leer(wert):
    """'18:30' bleibt, alles andere (leer, Unsinn) wird ''."""
    w = str(wert or "").strip()[:5]
    if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", w):
        return w
    return ""


def relativ(datum, heute=None):
    """'Heute' / 'Morgen' / 'Übermorgen' / 'in 5 Tagen' (bis 30) / 'vorbei' / ''."""
    try:
        d = date.fromisoformat(str(datum or "")[:10])
    except ValueError:
        return ""
    tage = (d - (heute or date.today())).days
    if tage < 0:
        return "vorbei"
    return {0: "Heute", 1: "Morgen", 2: "Übermorgen"}.get(tage, f"in {tage} Tagen" if tage <= 30 else "")


def schnellfilter(heute=None):
    """{'heute': (von, bis), 'wochenende': …, 'woche': …} als ISO-Datumspaare."""
    h = heute or date.today()
    sa = h + timedelta(days=(5 - h.weekday()) % 7) if h.weekday() < 5 else h
    so = sa + timedelta(days=6 - sa.weekday())
    return {"heute": (h.isoformat(), h.isoformat()),
            "wochenende": (sa.isoformat(), so.isoformat()),
            "woche": (h.isoformat(), (h + timedelta(days=6)).isoformat())}


def hinweise(t, jetzt=None, anmeldbar=False):
    """Kurze Status-Texte für eine Karte: {'tag': 'Heute'|'Morgen'|'', 'eile': '…'}."""
    jetzt = jetzt or datetime.now()
    heute = jetzt.date()
    erg = {"tag": "", "eile": ""}
    try:
        d = date.fromisoformat(str(t.get("datum") or ""))
    except ValueError:
        return erg
    if d == heute:
        erg["tag"] = "Heute"
        zeit = (t.get("uhrzeit") or "")[:5]
        if re.fullmatch(r"\d{2}:\d{2}", zeit) and not t.get("abgesagt"):
            start = datetime.combine(heute, datetime.strptime(zeit, "%H:%M").time())
            rest = (start - jetzt).total_seconds() / 60
            if 0 < rest <= 60:
                erg["eile"] = f"Beginnt in {max(1, math.ceil(rest))} Min."
            elif 60 < rest <= 6 * 60:
                erg["eile"] = f"Beginnt in {round(rest / 60)} Std."
    elif d == heute + timedelta(days=1):
        erg["tag"] = "Morgen"
    if anmeldbar and not erg["eile"] and t.get("anmeldeschluss"):
        try:
            schluss = date.fromisoformat(str(t["anmeldeschluss"])[:10])
        except ValueError:
            schluss = None
        if schluss and 0 <= (schluss - heute).days <= 3:
            erg["eile"] = ("Anmeldung nur noch heute" if schluss == heute
                           else f"Anmeldung nur noch bis {WT[schluss.weekday()]}")
    return erg
