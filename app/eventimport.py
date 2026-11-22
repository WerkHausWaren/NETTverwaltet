"""
Import von Veranstaltungen von auf-nach-mv.de (Toubiz/et4-Portal) per Volltextsuche.

Die Suchseite listet Event-Treffer mit „Weiterlesen"-Links auf die Detailseiten.
Jede Detailseite enthält schema.org-JSON-LD vom Typ „Event" – bei Serienterminen
ein Event-Objekt je Datum. Daraus lesen wir Titel, Datum, Uhrzeit, Ort,
Beschreibung und Bild-URL. Reine Standardbibliothek (urllib) – keine Zusatzpakete.
"""
import html as _html
import json
import re
import ssl
import urllib.request

from . import netz

_UA = {"User-Agent": "Mozilla/5.0 (Vereinsverwaltung Event-Import)"}
_BASIS = "https://www.auf-nach-mv.de"


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers=_UA)
    ctx = ssl.create_default_context()
    with netz.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode("utf-8", errors="replace")


def _detail_links(such_html):
    """Detailseiten-Links der Event-Treffer (readmore) – nur /veranstaltungssuche/."""
    roh = re.findall(
        r'<a href="(https?://[^"]*?/veranstaltungssuche/[^"]+)"[^>]*class="[^"]*list__element__readmore',
        such_html)
    return list(dict.fromkeys(roh))  # eindeutig, Reihenfolge erhalten


def _finde_events(o):
    if isinstance(o, dict):
        if o.get("@type") == "Event":
            yield o
        for v in o.values():
            yield from _finde_events(v)
    elif isinstance(o, list):
        for v in o:
            yield from _finde_events(v)


def _bild_url(img):
    if isinstance(img, list) and img:
        img = img[0]
    if isinstance(img, dict):
        return img.get("url") or img.get("contentUrl") or ""
    return img if isinstance(img, str) else ""


def _ort(loc):
    if isinstance(loc, str):
        return loc
    if isinstance(loc, dict):
        name = loc.get("name") or ""
        adr = loc.get("address")
        if isinstance(adr, dict):
            teile = [adr.get("streetAddress"), adr.get("postalCode"), adr.get("addressLocality")]
            teile = [t for t in teile if t]
            if name and teile:
                return f"{name}, " + " ".join(teile)
            return name or " ".join(teile)
        return name
    return ""


def _text_aus_html(schnipsel):
    """HTML-Ausschnitt in lesbaren Fließtext verwandeln.

    Absätze und Zeilenumbrüche bleiben erhalten, Sonderzeichen (&auml;, &nbsp;)
    werden aufgelöst, Tags fliegen raus.
    """
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", schnipsel or "")
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n\n", t)
    t = re.sub(r"(?i)<li[^>]*>", "• ", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = _html.unescape(t).replace("\xa0", " ")
    zeilen = [re.sub(r"[ \t]+", " ", z).strip() for z in t.split("\n")]
    text = "\n".join(zeilen)
    text = re.sub(r"\n{3,}", "\n\n", text)          # höchstens eine Leerzeile
    return text.strip()


def volltext(detail_html):
    """Die ausführliche Beschreibung der Detailseite.

    Das JSON-LD der Portalseiten enthält unter „description" nur den
    Anreißer (eine Zeile). Der eigentliche Text steht im Seitenkörper in
    <article class="organism-bodycopy">: darin der Anreißer als
    <p class="article__intro"> und darunter <div class="text"> mit den
    Absätzen. Beides zusammen ergibt die vollständige Beschreibung.
    """
    teile = []
    for block in re.findall(r'(?is)<article[^>]*class="[^"]*organism-bodycopy[^"]*".*?</article>',
                            detail_html or ""):
        stueck = _text_aus_html(block)
        if stueck:
            teile.append(stueck)
    ganz = "\n\n".join(teile).strip()
    # Doppelte Absätze (z. B. Anreißer, den auch das JSON-LD liefert) entfernen
    gesehen, sauber = set(), []
    for absatz in ganz.split("\n\n"):
        schluessel = absatz.strip().lower()
        if schluessel and schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        sauber.append(absatz)
    return "\n\n".join(sauber).strip()


def _organisation(o):
    """Name einer Organisation (Veranstalter/Anbieter) aus dem JSON-LD."""
    if isinstance(o, list) and o:
        o = o[0]
    if isinstance(o, dict):
        return (o.get("name") or "").strip()
    return o.strip() if isinstance(o, str) else ""


def _eintritt(ev):
    """Eintritt als kurzer Text: Preis aus den Angeboten oder „Eintritt frei"."""
    angebote = ev.get("offers")
    if isinstance(angebote, dict):
        angebote = [angebote]
    if isinstance(angebote, list):
        for a in angebote:
            if not isinstance(a, dict):
                continue
            preis = str(a.get("price") or "").strip()
            if preis and preis not in ("0", "0.0", "0.00"):
                waehrung = (a.get("priceCurrency") or "EUR").strip()
                return f"{preis} {waehrung}".replace(".", ",")
    frei = str(ev.get("isAccessibleForFree") or "")
    if frei.lower().endswith("true"):
        return "Eintritt frei"
    return ""


def _mit_zusatz(text, veranstalter, eintritt):
    """Eintritt und Veranstalter unten anfügen – aber nur, wenn sie nicht
    ohnehin schon im Text stehen. Dadurch bleibt das Ergebnis auch bei
    wiederholtem Import gleich (kein Anwachsen der Beschreibung)."""
    text = (text or "").strip()
    klein = text.lower()
    anhang = []
    if eintritt and "eintritt" not in klein and eintritt.lower() not in klein:
        anhang.append(eintritt)
    if veranstalter and veranstalter.lower() not in klein:
        anhang.append("Veranstalter: " + veranstalter)
    if not anhang:
        return text
    return (text + "\n\n" + " · ".join(anhang)).strip()


def _events_aus_detail(detail_html, quelle_url):
    aus = []
    # Der ausführliche Text steht einmal je Seite und gilt für alle Termine
    # der Reihe – deshalb nur einmal aus dem Seitenkörper lesen.
    langtext = volltext(detail_html)
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', detail_html, re.S):
        try:
            data = json.loads(block)
        except Exception:  # noqa: BLE001
            continue
        for ev in _finde_events(data):
            start = str(ev.get("startDate") or "")
            if len(start) < 10:
                continue
            ende = str(ev.get("endDate") or "")
            anreisser = (ev.get("description") or "").strip()
            veranstalter = _organisation(ev.get("organizer")) or _organisation(ev.get("contributor"))
            eintritt = _eintritt(ev)
            aus.append({
                "titel": (ev.get("name") or "").strip(),
                "datum": start[:10],
                "uhrzeit": start[11:16] if len(start) >= 16 else "",
                "uhrzeit_bis": ende[11:16] if len(ende) >= 16 else "",
                "ort": _ort(ev.get("location")).strip(),
                # Voller Text, wenn die Seite ihn hergibt – sonst der Anreißer
                "beschreibung": _mit_zusatz(langtext or anreisser, veranstalter, eintritt),
                "anreisser": anreisser,
                "veranstalter": veranstalter,
                "eintritt": eintritt,
                "bild_url": _bild_url(ev.get("image")),
                "quelle_url": (ev.get("url") or "").strip() or quelle_url,
            })
    return aus


def _norm(s):
    s = (s or "").lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return re.sub(r"[^a-z0-9]", "", s)


def kategorie_finden(titel, kategorien):
    """Sucht die am besten passende Kategorie für einen Titel.

    kategorien: Liste von Objekten mit ['id'] und ['name'] (z. B. sqlite-Rows).
    Bewertung = Summe der Längen der Kategorie-Wörter (>=4 Zeichen), die im Titel
    vorkommen. So gewinnt bei „SprachCafé Deutsch" die Deutsch- vor der Englisch-
    Kategorie. Gibt die kategorie_id zurück oder None."""
    tnorm = _norm(titel)
    if not tnorm:
        return None
    best_score, best_id = 0, None
    for k in kategorien:
        score = 0
        for wort in re.split(r"[\s/&,+.\-]+", k["name"] or ""):
            w = _norm(wort)
            if len(w) >= 4 and w in tnorm:
                score += len(w)
        if score > best_score:
            best_score, best_id = score, k["id"]
    return best_id


def hole_events(such_url, ab_datum=None, grenze=500):
    """Liefert alle Termin-Vorkommen (dicts) von der Suchseite.

    ab_datum (ISO 'YYYY-MM-DD'): nur Termine ab diesem Tag. Gibt (liste, fehler) zurück."""
    try:
        such_html = _get(such_url)
    except Exception as e:  # noqa: BLE001
        return [], f"Suchseite nicht erreichbar: {e}"
    links = _detail_links(such_html)
    if not links:
        return [], "Keine Veranstaltungs-Treffer gefunden (Quelle/Format geändert?)."
    ergebnis, gesehen = [], set()
    for link in links:
        try:
            dhtml = _get(link)
        except Exception:  # noqa: BLE001
            continue
        for e in _events_aus_detail(dhtml, link):
            if not e["titel"] or not e["datum"]:
                continue
            if ab_datum and e["datum"] < ab_datum:
                continue
            schluessel = (e["titel"].lower(), e["datum"])
            if schluessel in gesehen:
                continue
            gesehen.add(schluessel)
            ergebnis.append(e)
            if len(ergebnis) >= grenze:
                return ergebnis, ""
    ergebnis.sort(key=lambda x: (x["datum"], x["uhrzeit"]))
    return ergebnis, ""
