"""Öffentlichkeitsarbeit: technische Format-Vorlagen je Kanal.

Jedes Format kennt seine Plattform-Grenzen (max. Zeichen), die empfohlene
Länge (ideal) und die bewährte Struktur. baue() erzeugt aus einer
Veranstaltung einen fertigen Textvorschlag im jeweiligen Format – als
Startpunkt für die Beitrags-Werkstatt, per Hand nachschärfbar.
"""
from datetime import datetime

WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

FORMATE = {
    "instagram": {
        "label": "Instagram", "symbol": "📸", "max": 2200, "ideal": 150,
        "struktur": ["1. Zeile = Aufhänger (wird vor dem „mehr“ angezeigt, ca. 125 Zeichen)",
                     "Kern: Was, wann, wo – kurz und mit Zeilenumbrüchen",
                     "Aufforderung (Link in Bio / Anmeldung)",
                     "3–5 Hashtags ans Ende"],
        "hinweis": "Bild ist Pflicht (quadratisch wirkt am besten). Links im Text sind nicht klickbar → „Link in Bio“."},
    "facebook": {
        "label": "Facebook", "symbol": "👍", "max": 63206, "ideal": 250,
        "struktur": ["Aufhänger-Satz", "Absatz mit Details (wann, wo, Preis, Anmeldung)",
                     "Direkter Link zur Anmeldung", "1–2 Hashtags reichen"],
        "hinweis": "Beiträge mit 80–250 Zeichen bekommen die meisten Reaktionen. Link wird klickbare Vorschau."},
    "mastodon": {
        "label": "Mastodon", "symbol": "🐘", "max": 500, "ideal": 400,
        "struktur": ["Klartext ohne Floskeln", "Wann/wo in einer Zeile", "Link", "1–3 Hashtags (werden dort aktiv gesucht)"],
        "hinweis": "Harte Grenze 500 Zeichen. Bild-Alt-Text wird sehr geschätzt."},
    "bluesky": {
        "label": "Bluesky", "symbol": "🦋", "max": 300, "ideal": 250,
        "struktur": ["Ein knackiger Satz", "Wann/wo", "Link"],
        "hinweis": "Harte Grenze 300 Zeichen – kürzestes Format, zuerst schreiben und dann verlängern."},
    "telegram": {
        "label": "Telegram-Kanal", "symbol": "✈️", "max": 1024, "ideal": 500,
        "struktur": ["Titel fett (*Text*)", "Details mit Emoji-Zeilen (📅 🕒 📍 💶)", "Link", "Keine Hashtags nötig"],
        "hinweis": "1024 Zeichen gelten für die Bildunterschrift (Beitrag mit Bild); ohne Bild bis 4096."},
    "whatsapp": {
        "label": "WhatsApp-Kanal", "symbol": "💬", "max": 65536, "ideal": 400,
        "struktur": ["1. Zeile fett (*Text*) – nur sie erscheint in der Kanal-Vorschau",
                     "Leerzeile, dann Details als Emoji-Zeilen (📅 🕒 📍 💶)",
                     "Link zuletzt – WhatsApp baut daraus eine Vorschaukarte",
                     "KEINE Hashtags – die funktionieren im Kanal nicht"],
        "hinweis": "Formatierung: *fett*, _kursiv_, ~durchgestrichen~. Technisch sind sehr lange Texte "
                   "möglich, aber im Kanal wird nur der Anfang angezeigt – 300–500 Zeichen sind ideal. "
                   "Abonnent:innen können nicht antworten, also immer einen Link oder Kontakt mitgeben."},
    "presse": {
        "label": "Pressemitteilung", "symbol": "📰", "max": 0, "ideal": 2500,
        "struktur": ["Überschrift: aktiv, max. 80 Zeichen",
                     "Ort, Datum – dann Vorspann: beantwortet Wer/Was/Wann/Wo/Warum in 2–3 Sätzen (ca. 40 Wörter)",
                     "Haupttext: Wichtigstes zuerst (Redaktionen kürzen von hinten!)",
                     "Ein wörtliches Zitat mit Name und Funktion",
                     "Boilerplate „Über den Verein“ (aus dem Pressefach)",
                     "Pressekontakt: Name, Telefon, E-Mail"],
        "hinweis": "Ideal 2.000–3.000 Zeichen (max. 1 DIN-A4-Seite). Sachlich schreiben, keine Werbesprache."},
}


def _datum_lang(datum):
    try:
        d = datetime.strptime(datum, "%Y-%m-%d")
        return f"{WOCHENTAGE[d.weekday()]}, {d.day}. " + \
            ["", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
             "September", "Oktober", "November", "Dezember"][d.month] + f" {d.year}"
    except Exception:  # noqa: BLE001
        return datum or ""


def _basis(t, einst, anmeldelink=""):
    wann = _datum_lang(t["datum"])
    if t["uhrzeit"]:
        wann += f", {t['uhrzeit']} Uhr"
    preis = ""
    try:
        if t["preis"]:
            preis = f"{t['preis']:.2f}".replace(".", ",") + " €"
    except Exception:  # noqa: BLE001
        pass
    return {"titel": t["titel"] or "", "wann": wann, "ort": t["ort"] or "",
            "beschreibung": (t["beschreibung"] or "").strip(), "preis": preis,
            "verein": einst.get("vereinsname", "") if hasattr(einst, "get") else (einst["vereinsname"] or ""),
            "link": anmeldelink}


def _hashtags(einst, n=4):
    name = (einst["vereinsname"] or "").replace(" e.V.", "").replace(" e. V.", "")
    kern = "".join(w.capitalize() for w in name.replace("-", " ").split())[:30]
    ort = (einst["ort"] or "").split()[0] if einst["ort"] else ""
    tags = [f"#{kern}"] + ([f"#{ort}"] if ort else []) + ["#Verein", "#Ehrenamt"]
    return " ".join(tags[:n])


def presse_geruest(einst):
    """Leeres, strukturiertes Pressemitteilungs-Gerüst für ein freies Thema
    (ohne Veranstaltung) – mit Ort/Datum-Vorspann, Zitat-, Boilerplate- und
    Pressekontakt-Bausteinen zum Ausfüllen."""
    verein = einst["vereinsname"] or "unser Verein"
    ort_kurz = (einst["ort"] or "").strip() or "Ort"
    heute = datetime.now().strftime("%d.%m.%Y")
    zeilen = ["[Aussagekräftige Überschrift – aktiv, max. 80 Zeichen]", "",
              f"{ort_kurz}, {heute}. [Vorspann: Wer? Was? Wann? Wo? Warum? – die "
              "Kernaussage in 2–3 Sätzen, damit sie auch allein abgedruckt werden kann.]", "",
              "[Haupttext: Details, Hintergrund, Zahlen. Das Wichtigste zuerst – "
              "Redaktionen kürzen von hinten.]", "",
              "„[Ein wörtliches Zitat, das Lust macht]“,", "",
              "sagt [Name, Funktion im Verein].", ""]
    boiler = (einst["presse_boilerplate"] or "").strip() if "presse_boilerplate" in einst.keys() else ""
    zeilen += [f"Über {verein}:", boiler or "[Kurzbeschreibung des Vereins – im Pressefach hinterlegbar]", ""]
    kontakt = (einst["presse_kontakt"] or "").strip() if "presse_kontakt" in einst.keys() else ""
    zeilen += ["Pressekontakt:", kontakt or f"{verein}" + (f", {einst['email']}" if einst["email"] else "")]
    return "\n".join(zeilen)


def baue(kanal, t, einst, anmeldelink=""):
    """Erzeugt einen Textvorschlag für den Kanal aus einer Veranstaltung."""
    b = _basis(t, einst, anmeldelink)
    kurz = b["beschreibung"].split("\n")[0][:180]
    if kanal == "instagram":
        teile = [f"{b['titel']} – wir freuen uns auf euch! 🎉", "",
                 f"📅 {b['wann']}"] + ([f"📍 {b['ort']}"] if b["ort"] else []) \
                + ([f"💶 {b['preis']}"] if b["preis"] else []) \
                + ([""] + [kurz] if kurz else []) \
                + ["", "Anmeldung über den Link in der Bio.", "", _hashtags(einst)]
        return "\n".join(teile)
    if kanal == "facebook":
        teile = [f"{b['titel']} 🎉", "", (kurz or f"{b['verein']} lädt herzlich ein."), "",
                 f"📅 {b['wann']}"] + ([f"📍 {b['ort']}"] if b["ort"] else []) \
                + ([f"💶 {b['preis']}"] if b["preis"] else []) \
                + ([""] + [f"👉 Anmeldung: {b['link']}"] if b["link"] else []) \
                + ["", _hashtags(einst, 2)]
        return "\n".join(teile)
    if kanal == "mastodon":
        teile = [f"{b['titel']} – {b['wann']}" + (f", {b['ort']}" if b["ort"] else "") + "."] \
                + ([kurz] if kurz else []) + ([b["link"]] if b["link"] else []) + [_hashtags(einst, 3)]
        return "\n\n".join(x for x in teile if x)[:500]
    if kanal == "bluesky":
        text = f"{b['titel']} – {b['wann']}" + (f", {b['ort']}" if b["ort"] else "") + "."
        if b["link"]:
            text += f"\n{b['link']}"
        return text[:300]
    if kanal == "telegram":
        teile = [f"*{b['titel']}*", ""] + ([kurz, ""] if kurz else []) \
                + [f"📅 {b['wann']}"] + ([f"📍 {b['ort']}"] if b["ort"] else []) \
                + ([f"💶 {b['preis']}"] if b["preis"] else []) \
                + ([f"🎟 Anmeldung: {b['link']}"] if b["link"] else [])
        return "\n".join(teile)
    if kanal == "whatsapp":
        teile = [f"*{b['titel']}*", ""] + ([kurz, ""] if kurz else []) \
                + [f"📅 {b['wann']}"] + ([f"📍 {b['ort']}"] if b["ort"] else []) \
                + ([f"💶 {b['preis']}"] if b["preis"] else []) \
                + ([""] + [f"Anmeldung: {b['link']}"] if b["link"]
                   else ["", f"Wir freuen uns auf euch – {b['verein']}"])
        return "\n".join(teile)
    if kanal == "presse":
        ort_kurz = (einst["ort"] or "").strip() or "…"
        heute = datetime.now().strftime("%d.%m.%Y")
        zeilen = [f"{b['titel']}", "",
                  f"{ort_kurz}, {heute}. {b['verein']} lädt am {b['wann']}" +
                  (f" in {b['ort']}" if b["ort"] else "") + f" zu „{b['titel']}" + "“ ein." +
                  (f" Der Eintritt beträgt {b['preis']}." if b["preis"] else " Der Eintritt ist frei."), ""]
        if b["beschreibung"]:
            zeilen += [b["beschreibung"], ""]
        zeilen += ["„Hier ein wörtliches Zitat einfügen – ein Satz, der Lust macht“,", "",
                   "sagt … (Name, Funktion im Verein).", ""]
        if b["link"]:
            zeilen += [f"Anmeldung und weitere Informationen: {b['link']}", ""]
        boiler = (einst["presse_boilerplate"] or "").strip() if "presse_boilerplate" in einst.keys() else ""
        if boiler:
            zeilen += [f"Über {b['verein']}:", boiler, ""]
        kontakt = (einst["presse_kontakt"] or "").strip() if "presse_kontakt" in einst.keys() else ""
        zeilen += ["Pressekontakt:", kontakt or f"{b['verein']}" +
                   (f", {einst['email']}" if einst["email"] else "")]
        return "\n".join(zeilen)
    return ""
