"""
Interne Termine für fremde Augen unkenntlich machen.

Zwei Wege führen zur Maskierung – beide lassen Datum, Uhrzeit und Ort stehen
(man soll erkennen, dass das Haus belegt ist), ersetzen aber den Titel und
leeren alles Verräterische:

1. Der Haken „Nur für Mitglieder" am Termin → Titel „Mitgliederveranstaltung".
2. Die Termin-Kategorie „nicht-öffentlich" → Titel „Veranstaltung".
   Erkannt wird der Kategoriename unabhängig von Groß-/Kleinschreibung,
   Leerzeichen oder Bindestrich („Nicht öffentlich" trifft genauso).

Genutzt von der Mitglieder-App (Gäste und Freunde, app/routes/portal.py) und
vom Kalender-Abo, das ohne Anmeldung per Token abgerufen wird
(app/routes/kalender.py). WICHTIG für Aufrufer: Damit Weg 2 greifen kann,
muss die Abfrage den Kategorienamen als Spalte ``kategorie`` mitliefern
(LEFT JOIN termin_kategorien … k.name AS kategorie).
"""
import re

GAST_TITEL = "Mitgliederveranstaltung"
OEFFENTLICH_TITEL = "Veranstaltung"

# Felder, die bei maskierten Terminen nichts nach draußen tragen dürfen.
# Die Kategorie steht bewusst mit in der Liste: „Vorstand / Sitzung" wäre
# sonst genau der Fingerzeig, den der Haken verhindern soll.
# Auch Nicht-Text-Felder stehen hier: "" ist in Jinja falsch – damit
# verschwinden Anmelde-Knopf, Sponsor-Block, Sterne, Auslastung und
# Serien-Symbol von selbst aus allen Vorlagen.
_LEEREN = ("beschreibung", "ort_hinweise", "mitwirkende", "highlight", "highlight_text", "eintritt_art", "zielgruppen", "gut_zu_wissen", "teaser",
           "kat_bild", "trailer_url", "sponsor_name",
           "sponsor_logo", "sponsor_url", "langbeschreibung", "stornobedingung",
           "bild", "kategorie", "farbe", "anmeldung_token", "anmeldung_aktiv",
           "bewertung", "belegt", "serie_id", "max_teilnehmer")


def _normalisiert(name) -> str:
    return re.sub(r"[\s\-_]+", "", str(name or "")).casefold()


def ist_intern(termin) -> bool:
    """True, wenn der Termin den Haken „Nur für Mitglieder" trägt."""
    try:
        return bool(termin["nur_mitglieder"])
    except (KeyError, IndexError, TypeError):
        return False


def ist_nicht_oeffentlich(termin) -> bool:
    """True, wenn der Termin die Kategorie „nicht-öffentlich" trägt."""
    try:
        return _normalisiert(termin["kategorie"]) == "nichtöffentlich"
    except (KeyError, IndexError, TypeError):
        return False


def maskiere(termin) -> dict:
    """Zu maskierende Termine anonymisieren; alles andere unverändert durchreichen.

    Nimmt eine sqlite3.Row oder ein dict und liefert immer ein dict.
    Der Haken „Nur für Mitglieder" hat Vorrang vor der Kategorie.
    """
    d = dict(termin)
    if ist_intern(d):
        d["titel"] = GAST_TITEL
        d["verborgen_grund"] = "mitglieder"
    elif ist_nicht_oeffentlich(d):
        d["titel"] = OEFFENTLICH_TITEL
        d["verborgen_grund"] = "nicht_oeffentlich"
    else:
        return d
    for feld in _LEEREN:
        if feld in d:
            # Das Etikett „nicht-öffentlich" selbst verrät nichts – es erklärt
            # sogar, warum hier kein Name steht. Es darf samt seiner Farbe
            # stehen bleiben; bei „nur Mitglieder" wäre die Kategoriefarbe
            # dagegen ein Fingerzeig auf die verborgene Kategorie.
            if feld in ("kategorie", "farbe") and d["verborgen_grund"] == "nicht_oeffentlich":
                continue
            d[feld] = ""
    d["verborgen"] = True
    return d


def maskiere_liste(termine) -> list:
    return [maskiere(t) for t in termine]
