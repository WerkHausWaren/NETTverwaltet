"""Content-Hub-Anbindung: Maschinen-Schnittstelle für den n8n-Hub (n8n.nettverwaltet.de).

Vertrag: contenthub-Repo `anbindung/content-feed-spec.md`, Abschnitt „Fassung 2“ (Plan vom 09.11.2026).

  Hub liest (GET, Kopfzeile X-Content-Token = CONTENT_LESE_TOKEN):
    /api/content-feed       öffentliche Termine + Grabsteine für Zurückgezogenes
    /api/content/media      Fotos mit Social-Häkchen (+ Grabsteine)
    /api/content/personen   Freigebende (Vorstand/ÖA) mit Urlaub/Vertretung
    /api/content/bild/...   Bilddatei per Signatur mit Ablaufzeit
  Hub schreibt (POST JSON, X-Content-Token = CONTENT_SCHREIB_TOKEN):
    /api/content/inbox        Ankündigung (+ Push) – Kanal „app“
    /api/content/rueckmeldung Status je Veröffentlichung (versioniert)
    /api/content/wochenblick  Wochenblick für das ÖA-Archiv

Sicherheitsnetz (fail closed):
  - ohne Token in der Umgebung ODER ohne eingeschaltetes Modul → 404
  - falscher Token (auch Lese-Token an Schreib-Pfad) → 401, zeitkonstanter Vergleich
  - Feld-POSITIVLISTE: nur ausdrücklich genannte Felder verlassen die App,
    nie Teilnehmende, Mitglieder, Kontakt- oder Preisdaten
  - Termine nur, wenn aktiv, nicht „nur Mitglieder“, nicht Kategorie „nicht-öffentlich“
    (Kategorie per JOIN; fehlt das Feld → ausgeschlossen)
  - Fotos nur mit freigabe_ok UND social_ok UND öffentlichem Termin
"""
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timedelta, date

from flask import Blueprint, jsonify, request, abort, send_from_directory, current_app

from . import db, termin_maske

bp = Blueprint("content_hub", __name__)

STATUS = ("geplant", "probelauf", "veroeffentlicht", "fehler", "storniert")
BILD_GUELTIG_STUNDEN = 72
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


# ---------------------------------------------------------------- Grundlagen
def _env(name):
    return (os.environ.get(name) or "").strip()


def hub_verein():
    """Verein, für den die Schnittstelle gilt: CONTENT_VEREIN_ID oder der EINE Verein mit Modul.

    Mehrere Vereine mit Modul ohne ausdrückliche Wahl → None (fail closed)."""
    fest = _env("CONTENT_VEREIN_ID")
    kandidaten = []
    for v in db.alle_vereine():
        if fest and str(v["id"]) != fest:
            continue
        conn = db.get_db_fuer(v["id"])
        try:
            row = conn.execute("SELECT modul_contenthub FROM vereine WHERE id=?", (v["id"],)).fetchone()
        except Exception:  # noqa: BLE001 – alte DB ohne Spalte
            row = None
        conn.close()
        if row and row["modul_contenthub"]:
            kandidaten.append(v["id"])
    return kandidaten[0] if len(kandidaten) == 1 else None


def modul_an(vid):
    conn = db.get_db_fuer(vid)
    try:
        row = conn.execute("SELECT modul_contenthub FROM vereine WHERE id=?", (vid,)).fetchone()
    finally:
        conn.close()
    return bool(row and row["modul_contenthub"])


def _zugang(art):
    """Token prüfen; liefert die Vereins-ID. art = 'lese' | 'schreib'."""
    soll = _env("CONTENT_LESE_TOKEN" if art == "lese" else "CONTENT_SCHREIB_TOKEN")
    if not soll:
        abort(404)
    vid = hub_verein()
    if vid is None:
        abort(404)
    ist = request.headers.get("X-Content-Token", "")
    if not hmac.compare_digest(ist.encode(), soll.encode()):
        abort(401)
    conn = db.get_db_fuer(vid)
    conn.execute("INSERT INTO hub_zugriffe (verein_id, pfad, zuletzt) VALUES (?,?,?) "
                 "ON CONFLICT(verein_id, pfad) DO UPDATE SET zuletzt=excluded.zuletzt",
                 (vid, request.path.split("/bild/")[0], db.now_iso()))
    conn.commit()
    conn.close()
    return vid


def _iso(lokal):
    """'YYYY-MM-DD HH:MM:SS' bzw. 'YYYY-MM-DD' (Ortszeit des Prozesses) → ISO mit Zeitzone."""
    if not lokal:
        return None
    s = str(lokal).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt).astimezone().isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def _zeit(datum, uhrzeit):
    if not datum:
        return None
    return _iso(f"{datum} {uhrzeit}" if uhrzeit and re.match(r"^\d{1,2}:\d{2}$", uhrzeit) else datum)


def _basis():
    return (current_app.config.get("BASIS_URL") or _env("APP_PUBLIC_URL") or request.host_url).rstrip("/")


def _bild_schluessel():
    return (_env("HUB_BILD_SCHLUESSEL") or "").encode()


def bild_signatur(ref, exp):
    s = _bild_schluessel()
    if not s:
        return ""
    return hmac.new(s, f"{ref}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def bild_url(ref, stunden=BILD_GUELTIG_STUNDEN):
    exp = int(time.time()) + stunden * 3600
    sig = bild_signatur(ref, exp)
    return f"{_basis()}/api/content/bild/{ref}/{exp}/{sig}" if sig else ""


def _slug(name):
    s = (name or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40] or None


def ist_oeffentlich(t):
    """Fail closed: fehlt eines der Prüf-Felder, gilt der Termin als NICHT öffentlich."""
    try:
        keys = set(t.keys())
    except AttributeError:
        return False
    if not {"aktiv", "nur_mitglieder", "kategorie"} <= keys:
        return False
    if not t["aktiv"]:
        return False
    return not (termin_maske.ist_intern(t) or termin_maske.ist_nicht_oeffentlich(t))


_TERMIN_SQL = ("SELECT t.*, k.name AS kategorie FROM termine t "
               "LEFT JOIN termin_kategorien k ON k.id=t.kategorie_id "
               "WHERE t.verein_id=? AND COALESCE(t.datum,'')!=''")


def oeffentliche_termine(conn, vid):
    return [t for t in conn.execute(_TERMIN_SQL, (vid,)).fetchall() if ist_oeffentlich(t)]


def _termin_item(t):
    """Positivliste – NUR diese Felder verlassen die App."""
    from flask import url_for
    basis = _basis()
    anmelde = ""
    if t["anmeldung_aktiv"] and t["anmeldung_token"]:
        anmelde = basis + url_for("veranstaltungen.oeffentlich", token=t["anmeldung_token"])
    bilder = []
    if t["bild"]:
        u = bild_url(f"t{t['id']}")
        if u:
            bilder.append({"url": u, "art": "plakat"})
    return {
        "id": f"termin-{t['id']}", "type": "event", "subtype": _slug(t["kategorie"]),
        "title": t["titel"] or "", "text": t["beschreibung"] or "",
        "starts_at": _zeit(t["datum"], t["uhrzeit"]),
        "ends_at": _zeit(t["datum"], t["uhrzeit_bis"]) if t["uhrzeit_bis"] else None,
        "location": t["ort"] or "",
        "url": basis + url_for("portal.termin", tid=t["id"]),
        "anmelde_url": anmelde or None,
        "images": bilder,
        "updated_at": _iso(t["geaendert_am"]) or _iso(t["erstellt_am"]),
        "extra": {"kategorie": t["kategorie"] or "", "abgesagt": bool(t["abgesagt"])},
    }


def _export_abgleich(conn, vid, art, aktuell_ids, geliefert):
    """hub_export fortschreiben; liefert Grabsteine [(item_id, zuletzt)] für zurückgezogene Items.

    aktuell_ids: alle Items, die JETZT öffentlich sind; geliefert: {item_id: updated_lokal}."""
    jetzt = db.now_iso()
    for iid, upd in geliefert.items():
        conn.execute("INSERT INTO hub_export (verein_id, item_id, art, zuletzt, zurueckgezogen) VALUES (?,?,?,?,0) "
                     "ON CONFLICT(verein_id, item_id) DO UPDATE SET zuletzt=excluded.zuletzt, zurueckgezogen=0",
                     (vid, iid, art, upd or jetzt))
    for r in conn.execute("SELECT item_id FROM hub_export WHERE verein_id=? AND art=? AND zurueckgezogen=0",
                          (vid, art)).fetchall():
        if r["item_id"] not in aktuell_ids:
            conn.execute("UPDATE hub_export SET zurueckgezogen=1, zuletzt=? WHERE verein_id=? AND item_id=?",
                         (jetzt, vid, r["item_id"]))
    conn.commit()


def _seit():
    s = (request.args.get("since") or "").strip()
    if s:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return d.astimezone() if d.tzinfo else d.astimezone()
        except ValueError:
            pass
    return (datetime.now() - timedelta(days=30)).astimezone()


def _grabsteine(conn, vid, art, seit):
    out = []
    for r in conn.execute("SELECT item_id, zuletzt FROM hub_export WHERE verein_id=? AND art=? AND zurueckgezogen=1",
                          (vid, art)).fetchall():
        z = _iso(r["zuletzt"])
        if z and datetime.fromisoformat(z) >= seit:
            out.append({"id": r["item_id"], "type": "event" if art == "termin" else "image",
                        "zurueckgezogen": True, "updated_at": z})
    return out


# ---------------------------------------------------------------- Lesen
@bp.get("/api/content-feed")
def feed():
    vid = _zugang("lese")
    seit = _seit()
    jetzt = datetime.now().astimezone()
    limit = max(1, min(request.args.get("limit", 200, type=int) or 200, 200))
    nach = (request.args.get("nach") or "").strip()
    conn = db.get_db_fuer(vid)
    oeff = oeffentliche_termine(conn, vid)
    items, geliefert = [], {}
    for t in oeff:
        it = _termin_item(t)
        start = datetime.fromisoformat(it["starts_at"]) if it["starts_at"] else None
        upd = datetime.fromisoformat(it["updated_at"]) if it["updated_at"] else None
        if (upd and upd >= seit) or (start and start >= jetzt - timedelta(hours=12)):
            items.append(it)
    items.sort(key=lambda i: (i["updated_at"] or "", i["id"]))
    if nach:
        items = [i for i in items if f"{i['updated_at']}|{i['id']}" > nach]
    seite = items[:limit]
    for i in seite:
        tid = int(i["id"].split("-")[1])
        geliefert[i["id"]] = next((t["geaendert_am"] for t in oeff if t["id"] == tid), None)
    _export_abgleich(conn, vid, "termin", {f"termin-{t['id']}" for t in oeff}, geliefert)
    steine = _grabsteine(conn, vid, "termin", seit) if not nach else []
    conn.close()
    weiter = f"{seite[-1]['updated_at']}|{seite[-1]['id']}" if len(items) > limit else None
    return jsonify(mandant=_env("CONTENT_FEED_MANDANT") or "werkhaus-app",
                   generated_at=jetzt.isoformat(timespec="seconds"), items=seite + steine, next=weiter)


def _social_fotos(conn, vid):
    oeff = {t["id"]: t for t in oeffentliche_termine(conn, vid)}
    fotos = conn.execute("SELECT * FROM galerie_fotos WHERE verein_id=? AND freigabe_ok=1 AND social_ok=1 "
                         "AND COALESCE(dateiname,'')!=''", (vid,)).fetchall()
    return [(f, oeff[f["termin_id"]]) for f in fotos if f["termin_id"] in oeff]


@bp.get("/api/content/media")
def medien():
    vid = _zugang("lese")
    seit = _seit()
    conn = db.get_db_fuer(vid)
    name = (conn.execute("SELECT vereinsname FROM vereine WHERE id=?", (vid,)).fetchone() or {"vereinsname": ""})["vereinsname"]
    paare = _social_fotos(conn, vid)
    items, geliefert = [], {}
    for f, t in paare:
        upd_lokal = max(filter(None, [f["social_ok_am"] or "", f["erstellt_am"] or ""]), default="")
        upd = _iso(upd_lokal)
        if upd and datetime.fromisoformat(upd) < seit:
            continue
        iid = f"foto-{f['id']}"
        items.append({"id": iid, "type": "image", "url": bild_url(f"f{f['id']}"), "termin": f"termin-{t['id']}",
                      "album": t["titel"] or "", "caption": f["beschreibung"] or "",
                      "taken_at": _iso(f["erstellt_am"]), "consent": True, "credit": name or "",
                      "updated_at": upd})
        geliefert[iid] = upd_lokal
    _export_abgleich(conn, vid, "foto", {f"foto-{f['id']}" for f, _ in paare}, geliefert)
    steine = _grabsteine(conn, vid, "foto", seit)
    conn.close()
    return jsonify(items=items + steine)


@bp.get("/api/content/personen")
def personen():
    vid = _zugang("lese")
    heute = date.today().isoformat()
    s = db.get_system_db()
    rows = s.execute("SELECT id, email, anzeigename, rolle, abwesend_bis, vertretung_id FROM benutzer "
                     "WHERE verein_id=? AND aktiv=1 AND rolle IN ('admin','oeffentlichkeit') ORDER BY id",
                     (vid,)).fetchall()
    s.close()
    ids = {r["id"] for r in rows}
    out = []
    for r in rows:
        abw = r["abwesend_bis"] if (r["abwesend_bis"] or "") >= heute else None
        out.append({"kennung": f"app:{r['id']}", "name": r["anzeigename"] or (r["email"] or "").split("@")[0],
                    "email": r["email"] or "", "rolle": r["rolle"], "aktiv": True,
                    "abwesend_bis": abw,
                    "vertretung": f"app:{r['vertretung_id']}" if abw and r["vertretung_id"] in ids
                    and r["vertretung_id"] != r["id"] else None})
    return jsonify(stand=datetime.now().astimezone().isoformat(timespec="seconds"), personen=out)


@bp.get("/api/content/bild/<ref>/<int:exp>/<sig>")
def bild(ref, exp, sig):
    """Bilddatei für den Hub – Signatur mit Ablauf; Freigaben werden bei JEDEM Abruf neu geprüft."""
    vid = hub_verein()
    if vid is None or not _bild_schluessel() or not re.match(r"^[tf]\d{1,9}$", ref):
        abort(404)
    if exp < time.time() or not hmac.compare_digest(sig.encode(), bild_signatur(ref, exp).encode()):
        abort(404)
    conn = db.get_db_fuer(vid)
    nr = int(ref[1:])
    try:
        if ref[0] == "t":
            t = next((x for x in oeffentliche_termine(conn, vid) if x["id"] == nr), None)
            if not t or not t["bild"]:
                abort(404)
            from .routes.termine import _event_bild_dir
            return send_from_directory(_event_bild_dir(), t["bild"])
        paar = next(((f, t) for f, t in _social_fotos(conn, vid) if f["id"] == nr), None)
        if not paar:
            abort(404)
        from .routes.portal import _galerie_dir
        return send_from_directory(_galerie_dir(), paar[0]["dateiname"])
    finally:
        conn.close()


# ---------------------------------------------------------------- Schreiben
def _body():
    b = request.get_json(silent=True)
    if not isinstance(b, dict):
        abort(400)
    return b


def _benachrichtigen(vid, titel, text):
    try:
        from . import push
        from .oea_auto import _oea_und_admin_ids
        ids = _oea_und_admin_ids(vid)
        if ids:
            push.senden(ids, titel[:80], text[:150], "/oea/hub", thema="oea")
    except Exception:  # noqa: BLE001 – Push darf den Aufruf nie kippen
        current_app.logger.exception("Content-Hub: Push fehlgeschlagen")


@bp.post("/api/content/inbox")
def inbox():
    vid = _zugang("schreib")
    b = _body()
    hub_id = str(b.get("beitrag_id") or "").strip()
    titel = str(b.get("title") or "").strip()[:120]
    text = str(b.get("text") or "").strip()[:2000]
    if not _ID.match(hub_id) or not titel or not text:
        return jsonify(error="beitrag_id, title und text erforderlich"), 400
    conn = db.get_db_fuer(vid)
    da = conn.execute("SELECT id FROM ankuendigungen WHERE verein_id=? AND hub_id=?", (vid, hub_id)).fetchone()
    if da:
        conn.close()
        return jsonify(id=f"an_{da['id']}", url=_basis() + "/portal/"), 200
    cur = conn.execute("INSERT INTO ankuendigungen (verein_id, titel, text, aktiv, erstellt_am, oeffentlich, "
                       "sichtbar_von, sichtbar_bis, push_gesendet, erstellt_von, hub_id) "
                       "VALUES (?,?,?,1,?,1,'','',1,'Content-Hub',?)",
                       (vid, titel, text, db.now_iso(), hub_id))
    conn.commit()
    neu_id = cur.lastrowid
    conn.close()
    if b.get("push"):
        try:
            from . import push
            empf = db.benutzer_ids_mit_rolle(["mitglied", "admin", "extern"], vid)
            if empf:
                push.senden(empf, "📣 " + titel[:60], str(b.get("push_text") or text)[:140], "/portal/",
                            thema="neuigkeiten")
        except Exception:  # noqa: BLE001
            current_app.logger.exception("Content-Hub: Inbox-Push fehlgeschlagen")
    return jsonify(id=f"an_{neu_id}", url=_basis() + "/portal/"), 201


@bp.post("/api/content/rueckmeldung")
def rueckmeldung():
    vid = _zugang("schreib")
    b = _body()
    vf = str(b.get("veroeff_id") or "").strip()
    status = b.get("status")
    try:
        version = int(b.get("version") or 0)
    except (TypeError, ValueError):
        version = -1
    if not _ID.match(vf) or status not in STATUS or version < 1:
        return jsonify(error="veroeff_id, status und version erforderlich"), 400
    conn = db.get_db_fuer(vid)
    alt = conn.execute("SELECT * FROM hub_beitraege WHERE veroeff_id=?", (vf,)).fetchone()
    if alt and (alt["verein_id"] != vid or alt["version"] >= version):
        conn.close()
        return jsonify(ok=True, geaendert=False), 200
    oea_id = b.get("oea_beitrag") if isinstance(b.get("oea_beitrag"), int) else None
    werte = dict(verein_id=vid, veroeff_id=vf, beitrag_id=str(b.get("beitrag_id") or "")[:64],
                 thema_id=str(b.get("thema_id") or "")[:64], kanal=str(b.get("kanal") or "")[:20],
                 status=status, titel=str(b.get("titel") or "")[:200],
                 geplant_fuer=str(b.get("geplant_fuer") or "")[:40], veroeffentlicht=str(b.get("veroeffentlicht") or "")[:40],
                 url=str(b.get("url") or "")[:500] if str(b.get("url") or "").startswith("https://") else "",
                 fehler=str(b.get("fehler") or "")[:500],
                 zahlen=json.dumps(b.get("zahlen") or {}, ensure_ascii=False)[:500],
                 version=version, termin=str(b.get("termin") or "")[:40], oea_beitrag_id=oea_id,
                 aktualisiert=db.now_iso())
    spalten = ", ".join(werte)
    conn.execute(f"INSERT INTO hub_beitraege ({spalten}) VALUES ({', '.join('?' * len(werte))}) "
                 f"ON CONFLICT(veroeff_id) DO UPDATE SET " + ", ".join(f"{k}=excluded.{k}" for k in werte if k != "veroeff_id"),
                 list(werte.values()))
    if oea_id:
        if status == "veroeffentlicht":
            conn.execute("UPDATE oea_beitraege SET status='veroeffentlicht', veroeffentlicht_am=?, hub_status=? "
                         "WHERE id=? AND verein_id=?", (db.now_iso(), status, oea_id, vid))
        else:
            conn.execute("UPDATE oea_beitraege SET hub_status=? WHERE id=? AND verein_id=?", (status, oea_id, vid))
    conn.commit()
    conn.close()
    wechsel = not alt or alt["status"] != status
    if wechsel and status in ("veroeffentlicht", "fehler"):
        kanal = werte["kanal"].capitalize() or "Kanal"
        if status == "veroeffentlicht":
            _benachrichtigen(vid, f"✅ Veröffentlicht: {kanal}", werte["titel"] or "Ein Hub-Beitrag ist online.")
        else:
            _benachrichtigen(vid, f"⚠️ Nicht veröffentlicht: {kanal}", (werte["titel"] + ": " + werte["fehler"]).strip(": "))
    return jsonify(ok=True, geaendert=True), 200


@bp.post("/api/content/wochenblick")
def wochenblick():
    vid = _zugang("schreib")
    b = _body()
    woche = str(b.get("woche") or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", woche):
        return jsonify(error="woche (YYYY-MM-DD) erforderlich"), 400
    daten = json.dumps({k: b.get(k) for k in ("woche", "zeitraum", "kennzahlen", "einschaetzung",
                                               "vorschlaege", "top", "cockpit_url")}, ensure_ascii=False)
    if len(daten) > 60000:
        return jsonify(error="zu groß"), 413
    conn = db.get_db_fuer(vid)
    neu = conn.execute("SELECT 1 FROM hub_wochenblicke WHERE verein_id=? AND woche=?", (vid, woche)).fetchone() is None
    conn.execute("INSERT INTO hub_wochenblicke (verein_id, woche, daten, erstellt) VALUES (?,?,?,?) "
                 "ON CONFLICT(verein_id, woche) DO UPDATE SET daten=excluded.daten, erstellt=excluded.erstellt",
                 (vid, woche, daten, db.now_iso()))
    conn.commit()
    conn.close()
    if neu:
        _benachrichtigen(vid, "📊 Wochenblick ist da", "Was in der letzten Woche gut ankam – im ÖA-Cockpit.")
    return jsonify(ok=True), 200


ENDPUNKTE = ("content_hub.feed", "content_hub.medien", "content_hub.personen", "content_hub.bild",
             "content_hub.inbox", "content_hub.rueckmeldung", "content_hub.wochenblick")
POST_SICHTEN = ("inbox", "rueckmeldung", "wochenblick")
