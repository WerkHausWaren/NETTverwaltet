"""
Zwei-Wege-Abgleich der Mitglieder mit einem Nextcloud-Adressbuch (CardDAV).

- Es werden NUR aktive Mitglieder nach Nextcloud übertragen.
- Verknüpfung über die Kontakt-Kennung (UID); Fallback: gleiche E-Mail.
- Bei gleichzeitiger Änderung gewinnt Nextcloud (dort wird i. d. R. gepflegt).
- Gelöscht wird nichts automatisch.
- `abgleich(einst, anwenden=False)` liefert eine Vorschau; mit anwenden=True wird geschrieben.
"""
import uuid
from datetime import datetime

from . import db, carddav

ADRESSBUCH_NAME = "werkhaus-mitglieder"
ADRESSBUCH_ANZEIGE = "WerkHaus-Mitglieder"
APP_GRUPPE = "WerkHausApp"          # importierte Personen kommen zusätzlich in diese Gruppe
AKTIVE_STATUS = ("aktiv", "Ehrenmitglied")
PULL_FELDER = ["vorname", "nachname", "firma", "geburtsdatum",
               "strasse", "plz", "ort", "land", "telefon", "mobil"]


def _jetzt_vcard():
    return datetime.now().strftime("%Y%m%dT%H%M%SZ")


def _neue_uid():
    return "vv-" + uuid.uuid4().hex


def _app_geaendert(m):
    """Wurde das Mitglied seit dem letzten Abgleich in der App geändert?"""
    if not m["nc_sync_am"]:
        return False
    return (m["geaendert_am"] or "") > (m["nc_sync_am"] or "")


def _adressbuch(einst):
    return (einst["ncc_adressbuch"] or "").strip() or ADRESSBUCH_NAME


def abgleich(einst, anwenden=False, app_zugang=False):
    plan = {"neu_nc": 0, "neu_app": 0, "update_nc": 0, "update_app": 0, "konflikt": 0, "neu_konto": 0,
            "details": [], "fehler": None, "anwenden": anwenden, "adressbuch": ADRESSBUCH_ANZEIGE}
    if not carddav.konfiguriert(einst):
        plan["fehler"] = "Für den Abgleich fehlt der Nextcloud-Zugang (Einstellungen → Nextcloud)."
        return plan
    name = _adressbuch(einst)
    nur_import = name.startswith("z-")     # systemgeneriertes Buch (z. B. „Konten") = schreibgeschützt → nur Import
    konto_import = app_zugang and nur_import   # WerkHausApp-Zugänge nur beim Konten-Import (uid = NC-Loginname)
    plan["nur_import"] = nur_import
    plan["app_zugang"] = konto_import
    plan["adressbuch"] = name
    if anwenden and not nur_import:
        carddav.adressbuch_anlegen(einst, name, ADRESSBUCH_ANZEIGE)
    try:
        roh = carddav.kontakte_laden(einst, name)
    except Exception as e:  # noqa: BLE001
        plan["fehler"] = f"Nextcloud-Kontakte nicht erreichbar: {e}"
        return plan

    nc_by_uid = {}
    for c in roh:
        f = carddav.vcard_parsen(c["vcard"])
        f["etag"] = c["etag"]
        if f["uid"]:
            nc_by_uid[f["uid"]] = f

    vid = db.aktiver_verein_id()
    conn = db.get_db()
    mitglieder = conn.execute("SELECT * FROM mitglieder WHERE verein_id=?", (vid,)).fetchall()
    conn.close()
    m_by_uid = {m["nc_uid"]: m for m in mitglieder if m["nc_uid"]}
    m_by_email = {}
    for m in mitglieder:
        if m["email"]:
            m_by_email.setdefault(m["email"].strip().lower(), m)

    aktionen, verarbeitet = [], set()

    # 1) Aktive Mitglieder → Nextcloud  (bei schreibgeschütztem Buch übersprungen)
    for m in ([] if nur_import else mitglieder):
        if (m["status"] or "") not in AKTIVE_STATUS:
            continue
        uid = m["nc_uid"]
        if uid and uid in nc_by_uid:
            verarbeitet.add(uid)
            ncf = nc_by_uid[uid]
            nc_geaendert = ncf["etag"] != (m["nc_etag"] or "")
            app_geaendert = _app_geaendert(m)
            if nc_geaendert and app_geaendert:
                plan["konflikt"] += 1
                aktionen.append(("pull", m, ncf))          # Konflikt → Nextcloud gewinnt
                plan["update_app"] += 1
                plan["details"].append(f"⚠️ {m['vorname']} {m['nachname']}: beidseitig geändert – Nextcloud gewinnt")
            elif nc_geaendert:
                aktionen.append(("pull", m, ncf))
                plan["update_app"] += 1
                plan["details"].append(f"⬇️ {m['vorname']} {m['nachname']}: aus Nextcloud aktualisieren")
            elif app_geaendert:
                aktionen.append(("push", m, uid, ncf["etag"]))
                plan["update_nc"] += 1
                plan["details"].append(f"⬆️ {m['vorname']} {m['nachname']}: nach Nextcloud aktualisieren")
        elif uid:
            aktionen.append(("push", m, uid, None))        # war verknüpft, in NC weg → neu anlegen
            plan["neu_nc"] += 1
            plan["details"].append(f"⬆️ {m['vorname']} {m['nachname']}: in Nextcloud (neu) anlegen")
        else:
            treffer = None
            if m["email"]:
                for f in nc_by_uid.values():
                    if m["email"].strip().lower() in [e.strip().lower() for e in f["emails"]]:
                        treffer = f
                        break
            if treffer:
                verarbeitet.add(treffer["uid"])
                aktionen.append(("verknuepf_pull", m, treffer))
                plan["update_app"] += 1
                plan["details"].append(f"🔗 {m['vorname']} {m['nachname']}: mit Nextcloud-Kontakt verknüpfen")
            else:
                aktionen.append(("push_neu", m, _neue_uid()))
                plan["neu_nc"] += 1
                plan["details"].append(f"⬆️ {m['vorname']} {m['nachname']}: nach Nextcloud übertragen")

    # 2) Nextcloud-Kontakte ohne (aktives) Mitglied → neues aktives Mitglied
    for uid, f in nc_by_uid.items():
        if uid in verarbeitet or uid in m_by_uid:
            continue
        zugeordnet = None
        for e in f["emails"]:
            if e.strip().lower() in m_by_email:
                zugeordnet = m_by_email[e.strip().lower()]
                break
        if zugeordnet:
            if nur_import:
                continue      # existiert bereits – beim reinen Import nicht anfassen/überschreiben
            if (zugeordnet["status"] or "") in AKTIVE_STATUS:
                aktionen.append(("verknuepf_pull", zugeordnet, f))
                plan["update_app"] += 1
                plan["details"].append(f"🔗 {f['vorname']} {f['nachname']}: mit vorhandenem Mitglied verknüpfen")
        else:
            aktionen.append(("neu_app", f))
            plan["neu_app"] += 1
            zusatz = " + WerkHausApp-Zugang" if nur_import else ""
            plan["details"].append(f"⬇️ {f['vorname']} {f['nachname']}: neu als aktives Mitglied{zusatz} anlegen · Gruppe „{APP_GRUPPE}“")

    if nur_import:
        # Wie viele WerkHausApp-Zugänge kämen NEU dazu? (schon in der Vorschau zeigen)
        neu_f = [a[1] for a in aktionen if a[0] == "neu_app"]
        plan["neu_konto"] = _zaehle_neue_konten(neu_f, vid)
    if anwenden:
        plan["neu_konto"] = _anwenden(einst, name, aktionen, vid, konto_import)
    return plan


def _zaehle_neue_konten(vcard_felder, vid):
    """Zählt, für wie viele der zu importierenden Personen noch KEIN Zugang existiert."""
    conn = db.get_system_db()
    n = 0
    for f in vcard_felder:
        emails = [e for e in f.get("emails", []) if e]
        email = emails[0] if emails else ""
        uid = f.get("uid") or ""
        r = conn.execute(
            "SELECT 1 FROM benutzer WHERE IFNULL(verein_id,0)=IFNULL(?,0) AND ("
            "(nc_benutzer=? AND nc_benutzer!='') OR (email=? AND email!=''))",
            (vid, uid, email)).fetchone()
        if not r:
            n += 1
    conn.close()
    return n


def _felder_aus_vcard(f):
    """vCard-Felder → Mitglieder-Spalten (inkl. E-Mail-Aufteilung)."""
    daten = {k: f.get(k, "") for k in PULL_FELDER}
    emails = [e for e in f.get("emails", []) if e]
    daten["email"] = emails[0] if emails else ""
    daten["email_weitere"] = "\n".join(emails[1:])
    return daten


def _gruppe_id(conn, vid, name):
    """Gibt die id der Gruppe zurück; legt sie an, wenn es sie noch nicht gibt."""
    r = conn.execute("SELECT id FROM gruppen WHERE verein_id=? AND name=?", (vid, name)).fetchone()
    if r:
        return r["id"]
    cur = conn.execute("INSERT INTO gruppen (verein_id, name, beschreibung) VALUES (?,?,?)",
                       (vid, name, "Automatisch beim Kontakte-Import angelegt."))
    return cur.lastrowid


def _anwenden(einst, name, aktionen, vid, konto_import=False):
    conn = db.get_db()
    jetzt = db.now_iso()
    konten = 0
    app_gruppe_id = None
    for akt in aktionen:
        typ = akt[0]
        try:
            if typ in ("push", "push_neu"):
                if typ == "push_neu":
                    _, m, uid = akt
                    etag = None
                else:
                    _, m, uid, etag = akt
                vcard = carddav.vcard_bauen(m, uid, _jetzt_vcard())
                ok, neu_etag = carddav.kontakt_schreiben(einst, name, uid, vcard, etag)
                if ok:
                    conn.execute("UPDATE mitglieder SET nc_uid=?, nc_etag=?, nc_sync_am=? WHERE id=?",
                                 (uid, neu_etag, jetzt, m["id"]))
            elif typ in ("pull", "verknuepf_pull"):
                _, m, ncf = akt
                daten = _felder_aus_vcard(ncf)
                setz = ", ".join(f"{k}=?" for k in daten) + ", nc_uid=?, nc_etag=?, nc_sync_am=?, geaendert_am=?"
                werte = list(daten.values()) + [ncf["uid"], ncf["etag"], jetzt, jetzt, m["id"]]
                conn.execute(f"UPDATE mitglieder SET {setz} WHERE id=?", werte)
            elif typ == "neu_app":
                _, ncf = akt
                daten = _felder_aus_vcard(ncf)
                spalten = list(daten.keys()) + ["verein_id", "status", "nc_uid", "nc_etag",
                                                "nc_sync_am", "erstellt_am", "geaendert_am", "erstellt_von"]
                werte = list(daten.values()) + [vid, "aktiv", ncf["uid"], ncf["etag"], jetzt, jetzt, jetzt,
                                                "Nextcloud-Sync"]
                platz = ",".join(["?"] * len(spalten))
                cur = conn.execute(f"INSERT INTO mitglieder ({','.join(spalten)}) VALUES ({platz})", werte)
                if app_gruppe_id is None:
                    app_gruppe_id = _gruppe_id(conn, vid, APP_GRUPPE)
                conn.execute("INSERT OR IGNORE INTO mitglied_gruppe (mitglied_id, gruppe_id) VALUES (?,?)",
                             (cur.lastrowid, app_gruppe_id))
                if konto_import:
                    voll = f"{daten.get('vorname', '')} {daten.get('nachname', '')}".strip()
                    if db.benutzer_fuer_mitglied_anlegen(ncf["uid"], daten.get("email", ""),
                                                         voll, "mitglied", cur.lastrowid, vid):
                        konten += 1
        except Exception:  # noqa: BLE001 – ein Fehler darf den Rest nicht stoppen
            pass
    conn.commit()
    conn.close()
    return konten
