"""
Schulungsunterlagen im Hilfe-Bereich: Handbuch und Kurzanleitungen.

Die Dateien liegen je Verein unter DATA_DIR/hilfe/ (sie kommen per Deploy dorthin,
NICHT ins Docker-Image – das Handbuch wiegt ~50 MB). Jeder Verein bekommt genau
seine eigene Fassung (Kapitel nach Modulen gefiltert, eigenes Logo).

Je Kurzanleitung gibt es zwei Formen:
- das PDF (zum Teilen und Ausdrucken)
- die Web-Textfassung anleitung-<rolle>.html (mobiltauglich, wird in der App
  in einer Lese-Ansicht mit Zurück/Weiter/Teilen angezeigt)

Hochladen vom Mac: deploy/unterlagen-hochladen.sh (nach jedem Unterlagen-Neubau).
"""

import os

from .db import DATA_DIR

ORDNER = os.path.join(DATA_DIR, "hilfe")

HANDBUCH = "NETTverwaltet-Handbuch.pdf"

# (schluessel, pdf, web, Anzeige, Symbol, Rollen, für die diese Unterlage „passend" ist)
KURZANLEITUNGEN = [
    ("vorstand", "Kurzanleitung-Vorstand.pdf", "anleitung-vorstand.html",
     "Kurzanleitung Vorstand", "🏛️", ("admin",)),
    ("kasse", "Kurzanleitung-Schatzmeisterin.pdf", "anleitung-kasse.html",
     "Kurzanleitung Kasse", "💶", ("schatz",)),
    ("oeffentlichkeit", "Kurzanleitung-Öffentlichkeitsarbeit.pdf", "anleitung-oeffentlichkeit.html",
     "Kurzanleitung Öffentlichkeitsarbeit", "📣", ("oeffentlichkeit",)),
    ("mitglied", "Kurzanleitung-Mitglied.pdf", "anleitung-mitglied.html",
     "Kurzanleitung Mitglied", "👥", ("mitglied",)),
    ("freund", "Kurzanleitung-Freund-und-Gast.pdf", "anleitung-freund.html",
     "Kurzanleitung Freund und Gast", "🤝", ("extern",)),
]

_ERLAUBT = ({HANDBUCH}
            | {k[1] for k in KURZANLEITUNGEN}
            | {k[2] for k in KURZANLEITUNGEN})


def _groesse(pfad):
    mb = os.path.getsize(pfad) / (1024 * 1024)
    return f"{mb:.0f} MB" if mb >= 1 else f"{mb * 1024:.0f} KB"


def liste(rolle=""):
    """Vorhandene Unterlagen, rollenpassende Kurzanleitung zuoberst.

    Reihenfolge: passende Kurzanleitung(en) → Handbuch → übrige Kurzanleitungen.
    Fehlende Dateien werden weggelassen (z. B. bevor sie hochgeladen sind)."""
    gefunden = []
    hb = os.path.join(ORDNER, HANDBUCH)
    if os.path.isfile(hb):
        gefunden.append({"schluessel": "", "datei": HANDBUCH, "web": "",
                         "name": "Das Handbuch – alles ausführlich", "symbol": "📖",
                         "passend": False, "groesse": _groesse(hb)})
    for schluessel, pdf, web, name, symbol, rollen in KURZANLEITUNGEN:
        pfad = os.path.join(ORDNER, pdf)
        if not os.path.isfile(pfad):
            continue
        gefunden.append({"schluessel": schluessel, "datei": pdf,
                         "web": web if os.path.isfile(os.path.join(ORDNER, web)) else "",
                         "name": name, "symbol": symbol, "passend": rolle in rollen,
                         "groesse": _groesse(pfad)})
    return sorted(gefunden, key=lambda u: (0 if u["passend"] else (1 if u["datei"] == HANDBUCH else 2)))


def lesen(schluessel, rolle=""):
    """(Eintrag, nächster lesbarer Eintrag) für die Lese-Ansicht – oder (None, None)."""
    lesbar = [u for u in liste(rolle) if u["web"]]
    for i, u in enumerate(lesbar):
        if u["schluessel"] == schluessel:
            return u, lesbar[(i + 1) % len(lesbar)] if len(lesbar) > 1 else None
    return None, None


def senden(dateiname, anhang=False):
    """Liefert eine Unterlage aus – nur Katalog-Dateien, nichts anderes."""
    from flask import abort, send_from_directory
    if dateiname not in _ERLAUBT or not os.path.isfile(os.path.join(ORDNER, dateiname)):
        abort(404)
    antwort = send_from_directory(ORDNER, dateiname, as_attachment=bool(anhang))
    if dateiname.endswith(".html"):
        # Die Web-Textfassungen werden in der Lese-Ansicht als iframe eingebettet.
        # Die globalen Sicherheits-Header (X-Frame-Options: DENY, frame-ancestors
        # 'none') würden genau das blockieren („Verbindung abgelehnt") – für diese
        # eigenen, statischen Seiten gilt deshalb: Einbetten nur im eigenen Haus.
        antwort.headers["X-Frame-Options"] = "SAMEORIGIN"
        antwort.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; frame-ancestors 'self'")
    return antwort
