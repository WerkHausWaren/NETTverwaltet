"""
Kommunikation: Serien-E-Mails an Mitglieder, Vorlagen, Versandprotokoll.

Platzhalter im Betreff und Text werden pro Empfänger:in ersetzt:
  {anrede} {titel} {vorname} {nachname} {email} {mitgliedsnummer}
  {beitrag} {ort} {vereinsname}
"""
import json
import mimetypes
import os
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import current_user
from ..db import get_db, get_einstellungen, get_system_db, now_iso, aktiver_verein_id
from ..funktionen import wer
from .. import mail, speicher, dateiablage

bp = Blueprint("kommunikation", __name__, url_prefix="/kommunikation")


VORLAGEN_KAT_STANDARD = {"intern": ["Mitgliedschaft", "Sitzungen & Versammlung", "Rundschreiben"],
                         "extern": ["Veranstaltungen", "Partner & Presse"]}


def _vorlagen_kategorien(einst):
    """Die editierbaren Vorlagen-Kategorien des Vereins, getrennt nach intern/extern.

    Leer = die Standardlisten; gespeichert wird kommagetrennt in
    vereine.vorlagen_kat_intern / vorlagen_kat_extern."""
    ergebnis = {}
    for art in ("intern", "extern"):
        feld = f"vorlagen_kat_{art}"
        roh = (einst[feld] if einst and feld in einst.keys() else "") or ""
        eigene = [k.strip() for k in roh.split(",") if k.strip()]
        ergebnis[art] = eigene or list(VORLAGEN_KAT_STANDARD[art])
    return ergebnis


def mail_html_rahmen(einst, inner_html):
    """Der gemeinsame HTML-Rahmen aller Vereinsmails: Schriftart aus den
    Einstellungen. Wird auch von den Veranstaltungs-Mails genutzt."""
    font = (einst["mail_schriftart"] or "Arial").replace('"', "")
    return (f'<div style="font-family:{font},Arial,sans-serif;font-size:14px;'
            f'color:#1e293b;line-height:1.55">{inner_html}</div>')


def _signatur_html(einst, persoenlich=None):
    """Baut die E-Mail-Signatur: erst der persönliche Block (nur beim Versand
    mit persönlicher Adresse: Name + eigene Signatur), dann die allgemeine
    Vereins-Signatur, die für alle gleich ist (Logo, Adresse, Website,
    rechtlicher Hinweis). persoenlich = {"name": …, "text": …} oder None."""
    if not einst["signatur_aktiv"]:
        return ""
    import html as _html
    host = request.host_url.rstrip("/")
    t = ['<div style="margin-top:18px;padding-top:10px;border-top:1px solid #ddd;color:#333;font-size:13px">']
    if persoenlich and (persoenlich.get("name") or persoenlich.get("text")):
        if persoenlich.get("name"):
            t.append(f"<strong>{_html.escape(persoenlich['name'])}</strong><br>")
        if persoenlich.get("text"):
            t.append(_html.escape(persoenlich["text"].replace("\r", "")).replace("\n", "<br>") + "<br>")
        t.append('<div style="margin:8px 0"></div>')
    if einst["logo_dateiname"]:
        t.append(f'<img src="{host}{url_for("einstellungen.logo", verein_id=einst["id"])}" alt="" style="max-height:56px;margin:6px 0"><br>')
    t.append(f'<strong>{einst["vereinsname"] or ""}</strong><br>')
    adr = " · ".join(x for x in [einst["strasse"], (str(einst["plz"] or "") + " " + str(einst["ort"] or "")).strip()] if x and x.strip())
    if adr:
        t.append(adr + "<br>")
    if einst["email"]:
        t.append(f'✉️ <a href="mailto:{einst["email"]}">{einst["email"]}</a>')
    if einst["website"]:
        web = einst["website"] if einst["website"].startswith("http") else "https://" + einst["website"]
        t.append(f'{" · " if einst["email"] else ""}🌐 <a href="{web}">{einst["website"]}</a>')
    if einst["email"] or einst["website"]:
        t.append("<br>")
    if einst["signatur_text"]:
        t.append('<div style="margin-top:8px;font-size:11px;color:#777">' + einst["signatur_text"].replace("\n", "<br>") + "</div>")
    t.append("</div>")
    return "".join(t)


def _anhaenge_sammeln(conn, einst, vid):
    """Sammelt Anhänge aus Upload + Vereinsdokumenten + Dateiablage: [{name,daten,mime}]."""
    anhaenge, gesamt = [], 0
    def _mime(name):
        return mimetypes.guess_type(name)[0] or "application/octet-stream"
    for f in request.files.getlist("anhang_upload"):
        if f and f.filename:
            daten = f.read()
            anhaenge.append({"name": os.path.basename(f.filename), "daten": daten, "mime": _mime(f.filename)})
            gesamt += len(daten)
    for did in request.form.getlist("anhang_dok"):
        row = conn.execute("SELECT dateiname, original_name FROM vereinsdokumente WHERE id=? AND verein_id=?",
                           (did, vid)).fetchone()
        if not row:
            continue
        try:
            pfad = os.path.join(speicher.dokumente_ordner(einst["id"], einst["vereinsname"], einst["dok_speicherort"]), row["dateiname"])
            with open(pfad, "rb") as fh:
                daten = fh.read()
            anhaenge.append({"name": row["original_name"] or row["dateiname"], "daten": daten,
                             "mime": _mime(row["original_name"] or row["dateiname"])})
            gesamt += len(daten)
        except Exception:  # noqa: BLE001
            pass
    for rel in request.form.getlist("anhang_dms"):
        pfad = dateiablage.aufloesen(vid, rel)
        if pfad and os.path.isfile(pfad):
            try:
                with open(pfad, "rb") as fh:
                    daten = fh.read()
                anhaenge.append({"name": os.path.basename(pfad), "daten": daten, "mime": _mime(pfad)})
                gesamt += len(daten)
            except Exception:  # noqa: BLE001
                pass
    return anhaenge, gesamt

PLATZHALTER = ["anrede", "titel", "vorname", "nachname", "email",
               "mitgliedsnummer", "beitrag", "ort", "vereinsname"]


def _ersetze(text, m, vereinsname):
    """Ersetzt Platzhalter für eine:n Empfänger:in – Mitglied, Kontakt oder
    freie Adresse. Fehlende Angaben werden leer ersetzt statt zu knallen."""
    def w(k):
        try:
            return m[k] or ""
        except (KeyError, IndexError, TypeError):
            return ""
    werte = {
        "anrede": w("anrede"),
        "titel": w("titel"),
        "vorname": w("vorname"),
        "nachname": w("nachname"),
        "email": w("email"),
        "mitgliedsnummer": w("mitgliedsnummer"),
        "beitrag": f"{m['beitrag']:.2f}".replace(".", ",") if w("beitrag") else "0,00",
        "ort": w("ort"),
        "vereinsname": vereinsname or "",
    }
    for schluessel, wert in werte.items():
        text = text.replace("{" + schluessel + "}", str(wert))
    return text


@bp.route("/", methods=["GET", "POST"])
def neu():
    conn = get_db()
    vid = aktiver_verein_id()
    einst = get_einstellungen()

    if request.method == "POST":
        betreff = request.form.get("betreff", "").strip()
        # HTML-Editor bevorzugt; Fallback: Klartextfeld (Zeilenumbrüche → <br>)
        inhalt_html = (request.form.get("inhalt_html") or "").strip()
        inhalt = request.form.get("inhalt", "")
        if inhalt_html:
            body_html = inhalt_html
        else:
            from markupsafe import escape as _esc
            body_html = str(_esc(inhalt)).replace("\n", "<br>")
        # Relative Bild-/Link-Adressen (z. B. das Vereinslogo aus den Standard-
        # Vorlagen) für den Mailversand absolut machen – Mails haben keine Basis-URL.
        _host = request.host_url.rstrip("/")
        body_html = body_html.replace('src="/', f'src="{_host}/').replace("src='/", f"src='{_host}/")
        empfaenger_ids = request.form.getlist("empfaenger")
        kontakt_ids = request.form.getlist("empfaenger_kontakte")

        def _adressliste(feld):
            roh = [a.strip() for a in (request.form.get(feld) or "").replace(";", ",").split(",")]
            gute = [a for a in roh if a and "@" in a and " " not in a]
            return list(dict.fromkeys(gute))
        cc_liste = _adressliste("cc")
        bcc_liste = _adressliste("bcc")
        freie_zeilen = [z.strip() for z in (request.form.get("empfaenger_frei") or "").splitlines()
                        if z.strip()]

        # Optional als Vorlage speichern (als HTML). Gleicher Name = Vorlage
        # aktualisieren – so werden Vorlagen bearbeitet, statt Duplikate anzulegen.
        if request.form.get("als_vorlage") and request.form.get("vorlage_name", "").strip():
            v_name = request.form["vorlage_name"].strip()
            da = conn.execute("SELECT id FROM vorlagen WHERE verein_id=? AND name=? "
                              "AND COALESCE(schluessel,'')=''",
                              (vid, v_name)).fetchone()
            v_art, v_kat = "", ""
            einordnung = request.form.get("vorlage_einordnung", "")
            if "|" in einordnung:
                v_art, v_kat = einordnung.split("|", 1)
                if v_art not in ("intern", "extern") or v_kat not in _vorlagen_kategorien(einst).get(v_art, []):
                    v_art, v_kat = "", ""
            if da:
                conn.execute("UPDATE vorlagen SET betreff=?, inhalt=?, "
                             "art=CASE WHEN ?!='' THEN ? ELSE art END, "
                             "kategorie=CASE WHEN ?!='' THEN ? ELSE kategorie END WHERE id=?",
                             (betreff, body_html, v_art, v_art, v_art, v_kat, da["id"]))
                flash(f"Vorlage „{v_name}“ aktualisiert.", "erfolg")
            else:
                conn.execute("INSERT INTO vorlagen (verein_id, name, betreff, inhalt, art, kategorie, erstellt_am) "
                             "VALUES (?,?,?,?,?,?,?)",
                             (vid, v_name, betreff, body_html, v_art, v_kat, now_iso()))
            conn.commit()

        if not empfaenger_ids and not kontakt_ids and not freie_zeilen:
            flash("Bitte mindestens eine:n Empfänger:in auswählen oder eintragen.", "fehler")
            conn.close()
            return redirect(url_for("kommunikation.neu"))
        if not betreff:
            flash("Bitte einen Betreff eingeben.", "fehler")
            conn.close()
            return redirect(url_for("kommunikation.neu"))

        # SMTP konfiguriert?
        if not einst["smtp_host"] or not (einst["smtp_absender_email"] or einst["smtp_user"]):
            flash("Bitte zuerst die E-Mail-Einstellungen (SMTP) hinterlegen.", "fehler")
            conn.close()
            return redirect(url_for("einstellungen.index"))

        mitglieder = []
        if empfaenger_ids:
            fragezeichen = ",".join(["?"] * len(empfaenger_ids))
            mitglieder = conn.execute(
                f"SELECT * FROM mitglieder WHERE verein_id=? AND id IN ({fragezeichen})",
                [vid] + empfaenger_ids).fetchall()
        kontakte_rows = []
        if kontakt_ids:
            fragezeichen = ",".join(["?"] * len(kontakt_ids))
            kontakte_rows = conn.execute(
                f"SELECT * FROM kontakte WHERE verein_id=? AND id IN ({fragezeichen})",
                [vid] + kontakt_ids).fetchall()

        # Absenderwahl: Vereinsadresse oder das persönliche Profil der
        # angemeldeten Person. Die Wahl wird am Konto gemerkt.
        profil = None
        wahl = request.form.get("absender_wahl", "verein")
        if wahl == "selbst" and getattr(current_user, "is_authenticated", False):
            profil = mail.profil_laden(int(current_user.id))
            if profil is None:
                flash("Kein persönlicher Absender hinterlegt – bitte zuerst unter "
                      "Einstellungen → Mein Absender eintragen.", "fehler")
                conn.close()
                return redirect(url_for("kommunikation.neu"))
        if getattr(current_user, "is_authenticated", False):
            try:
                sys_conn = get_system_db()
                sys_conn.execute("UPDATE benutzer SET absender_wahl=? WHERE id=?",
                                 (wahl, int(current_user.id)))
                sys_conn.commit()
                sys_conn.close()
            except Exception:  # noqa: BLE001 – Komfort, darf den Versand nie stoppen
                pass

        anhaenge, anhang_bytes = _anhaenge_sammeln(conn, einst, vid)
        # Persönlicher Signatur-Block nur, wenn mit der persönlichen Adresse
        # gesendet wird – die Vereins-Signatur bleibt für alle gleich.
        persoenlich = None
        if profil:
            persoenlich = {"name": profil.get("name")
                           or getattr(current_user, "anzeigename", "")
                           or getattr(current_user, "email", ""),
                           "text": profil.get("signatur") or ""}
        signatur = _signatur_html(einst, persoenlich)
        font = (einst["mail_schriftart"] or "Arial").replace('"', "")
        erfolg, fehler, protokoll = 0, 0, []
        empfaenger_alle, gesehen = [], set()

        def _dazu(adresse, daten, hinweis):
            adresse = (adresse or "").strip()
            if not adresse:
                return False
            if adresse.lower() in gesehen:
                protokoll.append(f"↔️ {adresse}: doppelt – nur einmal gesendet ({hinweis})")
                return True
            gesehen.add(adresse.lower())
            empfaenger_alle.append({"adresse": adresse, "daten": daten, "hinweis": hinweis})
            return True

        for m in mitglieder:
            # Nur die HAUPTADRESSE (mitglieder.email) – weitere Adressen sind
            # Kontaktinfo, kein Verteiler. Wer keine Hauptadresse hat, bekommt
            # ersatzweise die erste weitere Adresse (besser als gar keine Mail).
            adresse = (m["email"] or "").strip()
            if not adresse and "email_weitere" in m.keys() and m["email_weitere"]:
                weitere = [e.strip() for e in m["email_weitere"].splitlines() if e.strip()]
                adresse = weitere[0] if weitere else ""
            if not _dazu(adresse, m, "👤 Mitglied"):
                fehler += 1
                protokoll.append(f"⚠️ {m['vorname']} {m['nachname']}: keine E-Mail-Adresse")
        for k in kontakte_rows:
            daten = {"anrede": "", "titel": "",
                     "vorname": (k["ansprechpartner"] or k["name"] or "").strip(),
                     "nachname": "", "email": (k["email"] or "").strip(),
                     "mitgliedsnummer": "", "beitrag": None, "ort": k["ort"] or ""}
            if not _dazu(k["email"], daten, f"📇 Kontakt: {k['name']}"):
                fehler += 1
                protokoll.append(f"⚠️ 📇 {k['name']}: keine E-Mail-Adresse hinterlegt")
        for zeile in freie_zeilen:
            # „Name <adresse>" oder nur die Adresse
            name, adresse = "", zeile
            if "<" in zeile and zeile.rstrip().endswith(">"):
                name, adresse = zeile.rsplit("<", 1)
                name, adresse = name.strip(), adresse.rstrip(">").strip()
            if "@" not in adresse or " " in adresse:
                fehler += 1
                protokoll.append(f"⚠️ ✍️ „{zeile}“: keine gültige E-Mail-Adresse")
                continue
            daten = {"anrede": "", "titel": "", "vorname": name, "nachname": "",
                     "email": adresse, "mitgliedsnummer": "", "beitrag": None, "ort": ""}
            _dazu(adresse, daten, "✍️ frei eingetragen")
            # Auf Wunsch gleich als Kontakt speichern (dublettensicher)
            if request.form.get("frei_als_kontakt"):
                try:
                    from .kontakte import finden_oder_anlegen
                    finden_oder_anlegen(conn, vid, {
                        "name": name or adresse, "email": adresse,
                        "kategorie": (request.form.get("frei_kategorie") or "").strip()})
                    conn.commit()
                except Exception:  # noqa: BLE001 – das Anlegen darf den Versand nie stoppen
                    pass

        vn = einst["vereinsname"]
        for e in empfaenger_alle:
            m = e["daten"]
            b = _ersetze(betreff, m, vn)
            html = mail_html_rahmen(einst, _ersetze(body_html, m, vn) + _ersetze(signatur, m, vn))
            plain = mail._html_zu_text(html)
            ok, meldung = mail.einzelmail(einst, e["adresse"], b, plain, html=html,
                                          anhaenge=anhaenge, profil=profil,
                                          cc=cc_liste, bcc=bcc_liste)
            if ok:
                erfolg += 1
                protokoll.append(f"✅ {e['adresse']} ({e['hinweis']})")
            else:
                fehler += 1
                protokoll.append(f"❌ {e['adresse']}: {meldung}")
        if cc_liste:
            protokoll.append("👁️ CC bei jeder Mail: " + ", ".join(cc_liste))
        if bcc_liste:
            protokoll.append("🕶️ BCC bei jeder Mail: " + ", ".join(bcc_liste))
        if anhaenge:
            protokoll.append(f"📎 {len(anhaenge)} Anhang/Anhänge (~{anhang_bytes // 1024} KB)")

        conn.execute(
            "INSERT INTO versand (verein_id, betreff, inhalt, anzahl_empfaenger, anzahl_erfolg, anzahl_fehler, details, gesendet_am, erstellt_von) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (vid, betreff, mail._html_zu_text(body_html), len(empfaenger_alle), erfolg, fehler, "\n".join(protokoll), now_iso(), wer()))
        conn.commit()
        conn.close()
        if fehler == 0:
            flash(f"E-Mail an {erfolg} Empfänger:innen erfolgreich versendet.", "erfolg")
        else:
            flash(f"{erfolg} versendet, {fehler} fehlgeschlagen. Details im Verlauf.", "info")
        return redirect(url_for("kommunikation.verlauf"))

    # GET: Formular aufbauen
    vorausgewaehlt = request.args.get("mitglied", type=int)
    gruppe = request.args.get("gruppe", type=int)
    mitglieder = conn.execute(
        "SELECT id, vorname, nachname, email, status FROM mitglieder WHERE verein_id=? "
        "ORDER BY nachname COLLATE NOCASE, vorname COLLATE NOCASE", (vid,)).fetchall()
    gruppen = conn.execute(
        "SELECT g.*, (SELECT COUNT(*) FROM mitglied_gruppe mg WHERE mg.gruppe_id=g.id) anzahl "
        "FROM gruppen g WHERE g.verein_id=? ORDER BY g.name", (vid,)).fetchall()
    vorlagen = conn.execute("SELECT * FROM vorlagen WHERE verein_id=? AND COALESCE(schluessel,'')='' "
                            "ORDER BY CASE COALESCE(art,'') WHEN 'intern' THEN 0 WHEN 'extern' THEN 1 ELSE 2 END, "
                            "kategorie, name", (vid,)).fetchall()
    # Mitglieder-IDs je Gruppe (für Schnellauswahl im Browser)
    gruppen_map = {}
    for g in gruppen:
        ids = [r["mitglied_id"] for r in conn.execute(
            "SELECT mitglied_id FROM mitglied_gruppe WHERE gruppe_id=?", (g["id"],)).fetchall()]
        gruppen_map[g["id"]] = ids
    dokumente = conn.execute(
        "SELECT id, titel, original_name FROM vereinsdokumente WHERE verein_id=? ORDER BY hochgeladen_am DESC",
        (vid,)).fetchall()
    # Kontakte (Firmen, Presse, Partner …) als zweite Empfängerquelle
    kontakte = conn.execute(
        "SELECT id, name, ansprechpartner, email, COALESCE(kategorie,'') kategorie "
        "FROM kontakte WHERE verein_id=? AND email!='' ORDER BY name COLLATE NOCASE", (vid,)).fetchall()
    kontakt_kategorien = {}
    for k in kontakte:
        kontakt_kategorien[k["kategorie"] or "ohne Kategorie"] =             kontakt_kategorien.get(k["kategorie"] or "ohne Kategorie", 0) + 1
    conn.close()
    dms_an = bool(einst and "modul_dms" in einst.keys() and einst["modul_dms"])
    dms_dateien = dateiablage.alle_dateien(vid) if dms_an else []

    smtp_ok = bool(einst["smtp_host"] and (einst["smtp_absender_email"] or einst["smtp_user"]))
    # Persönlicher Absender der angemeldeten Person (für die Absenderwahl)
    mein_profil, meine_wahl = None, "verein"
    if getattr(current_user, "is_authenticated", False):
        mein_profil = mail.profil_laden(int(current_user.id))
        try:
            sys_conn = get_system_db()
            z = sys_conn.execute("SELECT absender_wahl FROM benutzer WHERE id=?",
                                 (int(current_user.id),)).fetchone()
            sys_conn.close()
            if z and z["absender_wahl"] in ("verein", "selbst"):
                meine_wahl = z["absender_wahl"]
        except Exception:  # noqa: BLE001
            pass
    if mein_profil is None:
        meine_wahl = "verein"
    return render_template("kommunikation/neu.html", aktiv="kommunikation",
                           kategorien=_vorlagen_kategorien(einst),
                           kontakte=kontakte, kontakt_kategorien=kontakt_kategorien,
                           vorlage_vor=request.args.get("vorlage", type=int),
                           mitglieder=mitglieder, gruppen=gruppen, vorlagen=vorlagen,
                           gruppen_map=json.dumps(gruppen_map),
                           platzhalter=PLATZHALTER, vorausgewaehlt=vorausgewaehlt,
                           gruppe_vor=gruppe, smtp_ok=smtp_ok,
                           mein_profil=mein_profil, meine_wahl=meine_wahl,
                           verein_adresse=einst["smtp_absender_email"] or einst["smtp_user"] or "",
                           dokumente=dokumente, dms_dateien=dms_dateien,
                           schrift=einst["mail_schriftart"] or "Arial", signatur_aktiv=einst["signatur_aktiv"])


@bp.route("/verlauf")
def verlauf():
    conn = get_db()
    eintraege = conn.execute("SELECT * FROM versand WHERE verein_id=? ORDER BY gesendet_am DESC",
                             (aktiver_verein_id(),)).fetchall()
    conn.close()
    return render_template("kommunikation/verlauf.html", aktiv="kommunikation", eintraege=eintraege)


# ------------------------------- Vorlagen --------------------------------
@bp.route("/vorlagen")
def vorlagen():
    conn = get_db()
    vid = aktiver_verein_id()
    vorlagen = conn.execute("SELECT * FROM vorlagen WHERE verein_id=? AND COALESCE(schluessel,'')='' "
                            "ORDER BY CASE COALESCE(art,'') WHEN 'intern' THEN 0 WHEN 'extern' THEN 1 ELSE 2 END, "
                            "kategorie, name", (vid,)).fetchall()
    einst = get_einstellungen()
    auto = None
    if einst and "modul_veranstaltungen" in einst.keys() and einst["modul_veranstaltungen"]:
        from .veranstaltungen import vorlagen_laden, vorlagen_namen_laden, VORLAGEN_NAMEN
        auto_vorlagen = vorlagen_laden(conn, vid)
        auto = {"vorlagen": auto_vorlagen,
                "namen": vorlagen_namen_laden(conn, vid),
                "eigene": [k for k in auto_vorlagen if k not in VORLAGEN_NAMEN]}
    conn.close()
    return render_template("kommunikation/vorlagen.html", aktiv="kommunikation", vorlagen=vorlagen,
                           kategorien=_vorlagen_kategorien(einst), auto=auto)


@bp.route("/vorlagen/veranstaltungen")
def vorlagen_veranstaltungen():
    """Bearbeiten-Seite der Veranstaltungs-Vorlagen – nur mit aktivem Modul."""
    einst = get_einstellungen()
    if not (einst and "modul_veranstaltungen" in einst.keys() and einst["modul_veranstaltungen"]):
        flash("Das Modul Veranstaltungen & Anmeldungen ist nicht aktiviert.", "fehler")
        return redirect(url_for("kommunikation.vorlagen"))
    from .veranstaltungen import (vorlagen_laden, vorlagen_namen_laden,
                                  VORLAGEN_NAMEN, VORLAGEN_PLATZHALTER)
    conn = get_db()
    vid = aktiver_verein_id()
    va_vorlagen = vorlagen_laden(conn, vid)
    namen = vorlagen_namen_laden(conn, vid)
    angepasst = {r["schluessel"] for r in conn.execute(
        "SELECT schluessel FROM vorlagen WHERE verein_id=? AND COALESCE(schluessel,'')!=''",
        (vid,)).fetchall()}
    conn.close()
    return render_template("kommunikation/vorlagen_veranstaltungen.html", aktiv="kommunikation",
                           vorlagen=va_vorlagen, namen=namen, angepasst=angepasst,
                           eigene=[k for k in va_vorlagen if k not in VORLAGEN_NAMEN],
                           platzhalter=VORLAGEN_PLATZHALTER, einst=einst,
                           signatur_text=einst["signatur_text"] or "",
                           signatur_aktiv=bool(einst["signatur_aktiv"]))


@bp.route("/vorlagen/<int:vorlage_id>/loeschen", methods=["POST"])
def vorlage_loeschen(vorlage_id):
    conn = get_db()
    conn.execute("DELETE FROM vorlagen WHERE id=? AND verein_id=? AND COALESCE(schluessel,'')=''",
                 (vorlage_id, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Vorlage gelöscht.", "erfolg")
    return redirect(url_for("kommunikation.vorlagen"))


@bp.route("/vorlagen/<int:vorlage_id>/einordnen", methods=["POST"])
def vorlage_einordnen(vorlage_id):
    """Eine Vorlage einer Kategorie zuordnen (Wert „art|kategorie" oder leer)."""
    einordnung = request.form.get("einordnung", "")
    art, kat = "", ""
    if "|" in einordnung:
        art, kat = einordnung.split("|", 1)
        if art not in ("intern", "extern") or kat not in _vorlagen_kategorien(get_einstellungen()).get(art, []):
            art, kat = "", ""
    conn = get_db()
    conn.execute("UPDATE vorlagen SET art=?, kategorie=? WHERE id=? AND verein_id=? "
                 "AND COALESCE(schluessel,'')=''",
                 (art, kat, vorlage_id, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Einordnung gespeichert.", "erfolg")
    return redirect(url_for("kommunikation.vorlagen"))


@bp.route("/vorlagen/kategorien", methods=["POST"])
def vorlagen_kategorien_speichern():
    """Die Kategorienlisten (intern/extern) bearbeiten – kommagetrennt."""
    conn = get_db()
    for art in ("intern", "extern"):
        roh = request.form.get(f"kat_{art}", "")
        sauber = ", ".join(dict.fromkeys(k.strip() for k in roh.split(",") if k.strip()))
        conn.execute(f"UPDATE vereine SET vorlagen_kat_{art}=? WHERE id=?",
                     (sauber, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Kategorien gespeichert. Bestehende Zuordnungen bleiben erhalten.", "erfolg")
    return redirect(url_for("kommunikation.vorlagen"))


# ------------------------------ Ankündigungen ------------------------------
# Dieselben Ankündigungen wie in der Mitglieder-App (portal.news_neu), hier
# aber mit voller Verwaltung: erstellen, bearbeiten, löschen.

@bp.route("/ankuendigungen")
def ankuendigungen():
    conn = get_db()
    eintraege = conn.execute(
        "SELECT * FROM ankuendigungen WHERE verein_id=? AND aktiv=1 ORDER BY id DESC",
        (aktiver_verein_id(),)).fetchall()
    conn.close()
    from datetime import date as _date
    return render_template("kommunikation/ankuendigungen.html", aktiv="ankuendigungen",
                           eintraege=eintraege, heute_iso=_date.today().isoformat())


@bp.route("/ankuendigungen/neu", methods=["POST"])
def ankuendigung_neu():
    from datetime import date as _date
    titel = (request.form.get("titel") or "").strip()
    text = (request.form.get("text") or "").strip()
    oeff = 1 if request.form.get("oeffentlich") else 0
    von = (request.form.get("sichtbar_von") or "").strip()
    bis = (request.form.get("sichtbar_bis") or "").strip()
    if not titel:
        flash("Bitte einen Titel eingeben.", "fehler")
        return redirect(url_for("kommunikation.ankuendigungen"))
    # Geplante Ankündigung (Start in der Zukunft): Push übernimmt der stündliche Cron
    geplant = bool(von and von > _date.today().isoformat())
    conn = get_db()
    conn.execute("INSERT INTO ankuendigungen (verein_id, titel, text, aktiv, erstellt_am, oeffentlich, "
                 "sichtbar_von, sichtbar_bis, push_gesendet, erstellt_von) VALUES (?,?,?,1,?,?,?,?,?,?)",
                 (aktiver_verein_id(), titel, text, now_iso(), oeff, von, bis, 0 if geplant else 1, wer()))
    conn.commit()
    conn.close()
    if geplant:
        flash(f"Ankündigung geplant – erscheint ab dem {von[8:10]}.{von[5:7]}.{von[0:4]}. ⏰", "erfolg")
    else:
        flash("Ankündigung veröffentlicht – " + ("öffentlich sichtbar. 🌍" if oeff else "nur intern. 🔒"), "erfolg")
        try:
            from ..db import benutzer_ids_mit_rolle
            from .. import push
            empf = benutzer_ids_mit_rolle(["mitglied", "admin", "extern"] if oeff else ["mitglied", "admin"],
                                          aktiver_verein_id())
            if empf:
                push.senden(empf, "📣 " + titel, text[:120] or "Neue Ankündigung", url_for("portal.home"), thema="neuigkeiten")
        except Exception:  # noqa: BLE001
            pass
    return redirect(url_for("kommunikation.ankuendigungen"))


@bp.route("/ankuendigungen/<int:nid>/speichern", methods=["POST"])
def ankuendigung_speichern(nid):
    titel = (request.form.get("titel") or "").strip()
    text = (request.form.get("text") or "").strip()
    oeff = 1 if request.form.get("oeffentlich") else 0
    von = (request.form.get("sichtbar_von") or "").strip()
    bis = (request.form.get("sichtbar_bis") or "").strip()
    if not titel:
        flash("Bitte einen Titel eingeben.", "fehler")
        return redirect(url_for("kommunikation.ankuendigungen"))
    conn = get_db()
    conn.execute("UPDATE ankuendigungen SET titel=?, text=?, oeffentlich=?, sichtbar_von=?, sichtbar_bis=? "
                 "WHERE id=? AND verein_id=?",
                 (titel, text, oeff, von, bis, nid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Ankündigung gespeichert.", "erfolg")
    return redirect(url_for("kommunikation.ankuendigungen"))


@bp.route("/ankuendigungen/<int:nid>/loeschen", methods=["POST"])
def ankuendigung_loeschen(nid):
    conn = get_db()
    conn.execute("UPDATE ankuendigungen SET aktiv=0 WHERE id=? AND verein_id=?",
                 (nid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Ankündigung gelöscht.", "erfolg")
    return redirect(url_for("kommunikation.ankuendigungen"))


# ---------------------------------------------------------------------------
# Schwarzes Brett in der Verwaltung: Gesuche und Angebote nebeneinander,
# mit Kontaktdaten und Meldungen – zum Zusammenbringen (Matching).
# ---------------------------------------------------------------------------
def _brett_kategorien(einst):
    roh = (einst["pinnwand_kategorien"] if einst and "pinnwand_kategorien" in einst.keys() else "") or ""
    return [k.strip() for k in roh.split(",") if k.strip()]


@bp.route("/schwarzes-brett")
def schwarzes_brett():
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    kategorien = _brett_kategorien(einst)
    kat_filter = (request.args.get("kat") or "").strip()
    if kat_filter and kat_filter not in kategorien:
        kat_filter = ""
    conn = get_db()
    sql = ("SELECT p.*, mg.vorname, mg.nachname, mg.email, mg.telefon, mg.mobil "
           "FROM pinnwand p LEFT JOIN mitglieder mg ON mg.id=p.mitglied_id "
           "WHERE p.verein_id=? AND p.aktiv=1 AND COALESCE(p.moderation,0)=0 ")
    params = [vid]
    if kat_filter:
        sql += "AND p.kategorie=? "
        params.append(kat_filter)
    posts = [dict(r) for r in conn.execute(sql + "ORDER BY p.id DESC", params).fetchall()]
    wartend = [dict(r) for r in conn.execute(
        "SELECT p.*, mg.vorname, mg.nachname, mg.email, mg.telefon, mg.mobil "
        "FROM pinnwand p LEFT JOIN mitglieder mg ON mg.id=p.mitglied_id "
        "WHERE p.verein_id=? AND p.aktiv=1 AND COALESCE(p.moderation,0)=1 "
        "ORDER BY p.id DESC", (vid,)).fetchall()]
    erledigte = [dict(r) for r in conn.execute(
        "SELECT p.*, mg.vorname, mg.nachname "
        "FROM pinnwand p LEFT JOIN mitglieder mg ON mg.id=p.mitglied_id "
        "WHERE p.verein_id=? AND p.aktiv=0 ORDER BY p.id DESC LIMIT 100",
        (vid,)).fetchall()]
    for p in posts:
        p["meldungen"] = [dict(r) for r in conn.execute(
            "SELECT a.mitglied_id, a.erstellt_am, mg.vorname, mg.nachname, "
            "mg.email, mg.telefon, mg.mobil "
            "FROM pinnwand_antworten a LEFT JOIN mitglieder mg ON mg.id=a.mitglied_id "
            "WHERE a.pinnwand_id=? ORDER BY a.id", (p["id"],)).fetchall()]
        try:
            from datetime import date as _date, datetime as _dt
            p["offen_tage"] = (_date.today()
                               - _dt.strptime((p["erstellt_am"] or "")[:10], "%Y-%m-%d").date()).days
        except ValueError:
            p["offen_tage"] = 0
    conn.close()
    gesuche = [p for p in posts if p["typ"] == "gesuch"]
    angebote = [p for p in posts if p["typ"] == "angebot"]
    infos = [p for p in posts if p["typ"] not in ("gesuch", "angebot")]
    return render_template("kommunikation/schwarzes_brett.html", aktiv="kommunikation",
                           gesuche=gesuche, angebote=angebote, infos=infos, wartend=wartend,
                           erledigte=erledigte, kategorien=kategorien, kat_filter=kat_filter)


@bp.route("/schwarzes-brett/<int:pid>/freigeben", methods=["POST"])
def schwarzes_brett_freigeben(pid):
    conn = get_db()
    conn.execute("UPDATE pinnwand SET moderation=0 WHERE id=? AND verein_id=?",
                 (pid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Eintrag freigegeben – er ist jetzt am Schwarzen Brett sichtbar.", "erfolg")
    return redirect(url_for("kommunikation.schwarzes_brett"))


@bp.route("/schwarzes-brett/<int:pid>/ablehnen", methods=["POST"])
def schwarzes_brett_ablehnen(pid):
    conn = get_db()
    conn.execute("DELETE FROM pinnwand WHERE id=? AND verein_id=? AND COALESCE(moderation,0)=1",
                 (pid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Eintrag abgelehnt und entfernt.", "info")
    return redirect(url_for("kommunikation.schwarzes_brett"))


@bp.route("/schwarzes-brett/<int:pid>/erledigt", methods=["POST"])
def schwarzes_brett_erledigt(pid):
    conn = get_db()
    conn.execute("UPDATE pinnwand SET aktiv=0 WHERE id=? AND verein_id=?",
                 (pid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Eintrag als vermittelt/erledigt geschlossen.", "erfolg")
    return redirect(url_for("kommunikation.schwarzes_brett"))


@bp.route("/schwarzes-brett/<int:pid>/verbinden", methods=["POST"])
def schwarzes_brett_verbinden(pid):
    """Vorstellungs-Mail an Ersteller:in und ausgewählte:n Melder:in."""
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    mid = request.form.get("mitglied_id", type=int)
    conn = get_db()
    p = conn.execute("SELECT p.*, mg.vorname, mg.nachname, mg.email FROM pinnwand p "
                     "LEFT JOIN mitglieder mg ON mg.id=p.mitglied_id "
                     "WHERE p.id=? AND p.verein_id=?", (pid, vid)).fetchone()
    melder = conn.execute("SELECT vorname, nachname, email FROM mitglieder "
                          "WHERE id=? AND verein_id=?", (mid, vid)).fetchone()
    conn.close()
    if not p or not melder:
        flash("Eintrag oder Mitglied nicht gefunden.", "fehler")
        return redirect(url_for("kommunikation.schwarzes_brett"))
    # Gast-Einträge: E-Mail aus der angegebenen Kontaktmöglichkeit verwenden
    p = dict(p)
    if not p["mitglied_id"]:
        p["vorname"] = p["gast_name"] or "Gast"
        p["email"] = p["gast_kontakt"] if "@" in (p["gast_kontakt"] or "") else ""
    if not (p["email"] and melder["email"]):
        flash("Für beide wird eine E-Mail-Adresse benötigt – bitte telefonisch verbinden.", "fehler")
        return redirect(url_for("kommunikation.schwarzes_brett"))
    inhalt = (f"Hallo {p['vorname']} und {melder['vorname']},\n\n"
              f"zum Eintrag „{p['titel']}“ am Schwarzen Brett möchten wir euch "
              f"miteinander verbinden – ihr erreicht euch direkt über diese E-Mail "
              f"(einfach allen antworten).\n\nViele Grüße\n"
              f"{(einst['vereinsname'] if einst else '') or 'Die Verwaltung'}")
    fehler = []
    for empf in (p["email"], melder["email"]):
        ok, meldung = mail.einzelmail(einst, empf, f"Kontakt zum Eintrag: {p['titel']}", inhalt)
        if not ok:
            fehler.append(meldung)
    if fehler:
        flash(f"E-Mail konnte nicht gesendet werden ({fehler[0]}). "
              "Tipp: SMTP in den Einstellungen einrichten oder telefonisch verbinden.", "fehler")
    else:
        flash(f"{p['vorname']} und {melder['vorname']} wurden per E-Mail verbunden.", "erfolg")
    return redirect(url_for("kommunikation.schwarzes_brett"))


@bp.route("/schwarzes-brett/neu", methods=["POST"])
def schwarzes_brett_neu():
    """Eintrag aus der Verwaltung anlegen – z. B. wenn jemand anruft und die
    Verwaltung das Gesuch/Angebot stellvertretend einträgt."""
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    typ = request.form.get("typ", "gesuch")
    if typ not in ("gesuch", "angebot", "info"):
        typ = "gesuch"
    titel = (request.form.get("titel") or "").strip()
    if not titel:
        flash("Bitte einen Titel eingeben.", "fehler")
        return redirect(url_for("kommunikation.schwarzes_brett"))
    kategorie = (request.form.get("kategorie") or "").strip()
    if kategorie not in _brett_kategorien(einst):
        kategorie = ""
    conn = get_db()
    conn.execute("INSERT INTO pinnwand (verein_id, titel, text, typ, kategorie, sprachen, "
                 "gast_name, gast_kontakt, mitglied_id, aktiv, moderation, erstellt_am, erstellt_von) "
                 "VALUES (?,?,?,?,?,?,?,?,NULL,1,0,?,?)",
                 (vid, titel, (request.form.get("text") or "").strip(), typ, kategorie,
                  (request.form.get("sprachen") or "").strip(),
                  (request.form.get("gast_name") or "").strip(),
                  (request.form.get("gast_kontakt") or "").strip(), now_iso(), wer()))
    conn.commit()
    conn.close()
    flash("Eintrag angelegt und veröffentlicht.", "erfolg")
    return redirect(url_for("kommunikation.schwarzes_brett"))


@bp.route("/schwarzes-brett/<int:pid>/bearbeiten", methods=["POST"])
def schwarzes_brett_bearbeiten(pid):
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    typ = request.form.get("typ", "gesuch")
    if typ not in ("gesuch", "angebot", "info"):
        typ = "gesuch"
    titel = (request.form.get("titel") or "").strip()
    if not titel:
        flash("Bitte einen Titel eingeben.", "fehler")
        return redirect(url_for("kommunikation.schwarzes_brett"))
    kategorie = (request.form.get("kategorie") or "").strip()
    if kategorie not in _brett_kategorien(einst):
        kategorie = ""
    conn = get_db()
    conn.execute("UPDATE pinnwand SET titel=?, text=?, typ=?, kategorie=?, sprachen=?, "
                 "gast_name=COALESCE(NULLIF(?,''), gast_name), "
                 "gast_kontakt=COALESCE(NULLIF(?,''), gast_kontakt) "
                 "WHERE id=? AND verein_id=?",
                 (titel, (request.form.get("text") or "").strip(), typ, kategorie,
                  (request.form.get("sprachen") or "").strip(),
                  (request.form.get("gast_name") or "").strip(),
                  (request.form.get("gast_kontakt") or "").strip(), pid, vid))
    conn.commit()
    conn.close()
    flash("Eintrag gespeichert.", "erfolg")
    return redirect(url_for("kommunikation.schwarzes_brett"))


@bp.route("/schwarzes-brett/<int:pid>/loeschen", methods=["POST"])
def schwarzes_brett_loeschen(pid):
    """Endgültig löschen (anders als „erledigt", das nur schließt)."""
    vid = aktiver_verein_id()
    conn = get_db()
    conn.execute("DELETE FROM pinnwand_antworten WHERE pinnwand_id IN "
                 "(SELECT id FROM pinnwand WHERE id=? AND verein_id=?)", (pid, vid))
    conn.execute("DELETE FROM pinnwand WHERE id=? AND verein_id=?", (pid, vid))
    conn.commit()
    conn.close()
    flash("Eintrag endgültig gelöscht.", "info")
    return redirect(url_for("kommunikation.schwarzes_brett"))


@bp.route("/schwarzes-brett/<int:pid>/oeffnen", methods=["POST"])
def schwarzes_brett_oeffnen(pid):
    """Erledigten Eintrag wieder ans Schwarze Brett stellen."""
    conn = get_db()
    conn.execute("UPDATE pinnwand SET aktiv=1 WHERE id=? AND verein_id=?",
                 (pid, aktiver_verein_id()))
    conn.commit()
    conn.close()
    flash("Eintrag wieder geöffnet.", "erfolg")
    return redirect(url_for("kommunikation.schwarzes_brett"))


@bp.route("/schwarzes-brett/paar-verbinden", methods=["POST"])
def schwarzes_brett_paar_verbinden():
    """Ein ausgewähltes Gesuch mit einem ausgewählten Angebot verbinden:
    Vorstellungs-Mail an beide Seiten (Mitglieder- oder Gast-E-Mail)."""
    vid = aktiver_verein_id()
    einst = get_einstellungen()
    gid = request.form.get("gesuch_id", type=int)
    aid = request.form.get("angebot_id", type=int)
    if not gid or not aid:
        flash("Bitte links ein Gesuch und rechts ein Angebot auswählen.", "fehler")
        return redirect(url_for("kommunikation.schwarzes_brett"))
    conn = get_db()

    def _laden(pid, typ):
        row = conn.execute(
            "SELECT p.*, mg.vorname, mg.nachname, mg.email, mg.telefon, mg.mobil "
            "FROM pinnwand p LEFT JOIN mitglieder mg ON mg.id=p.mitglied_id "
            "WHERE p.id=? AND p.verein_id=? AND p.typ=?", (pid, vid, typ)).fetchone()
        if not row:
            return None
        e = dict(row)
        if not e["mitglied_id"]:
            e["vorname"] = e["gast_name"] or "Gast"
            e["nachname"] = ""
            e["email"] = e["gast_kontakt"] if "@" in (e["gast_kontakt"] or "") else ""
            e["telefon"] = "" if "@" in (e["gast_kontakt"] or "") else e["gast_kontakt"]
        return e

    gesuch = _laden(gid, "gesuch")
    angebot = _laden(aid, "angebot")
    conn.close()
    if not gesuch or not angebot:
        flash("Gesuch oder Angebot nicht gefunden.", "fehler")
        return redirect(url_for("kommunikation.schwarzes_brett"))

    kontakte = (f"{gesuch['vorname']} {gesuch['nachname']}".strip()
                + f" ({gesuch['email'] or gesuch['telefon'] or 'kein Kontakt'}) · "
                + f"{angebot['vorname']} {angebot['nachname']}".strip()
                + f" ({angebot['email'] or angebot['telefon'] or 'kein Kontakt'})")
    if not (gesuch["email"] and angebot["email"]):
        flash("Für den E-Mail-Weg fehlt eine Adresse – bitte telefonisch verbinden: "
              + kontakte, "fehler")
        return redirect(url_for("kommunikation.schwarzes_brett"))
    inhalt = (f"Hallo {gesuch['vorname']} und {angebot['vorname']},\n\n"
              f"wir glauben, ihr passt zusammen:\n\n"
              f"🙋 Gesuch #{gesuch['id']}: {gesuch['titel']}\n"
              f"🎁 Angebot #{angebot['id']}: {angebot['titel']}\n\n"
              f"Ihr erreicht euch direkt über diese E-Mail – einfach allen antworten.\n\n"
              f"Viele Grüße\n{(einst['vereinsname'] if einst else '') or 'Die Verwaltung'}")
    fehler = []
    for empf in (gesuch["email"], angebot["email"]):
        ok, meldung = mail.einzelmail(einst, empf,
                                      f"Wir haben eine Verbindung für euch: {gesuch['titel']}", inhalt)
        if not ok:
            fehler.append(meldung)
    if fehler:
        flash(f"E-Mail fehlgeschlagen ({fehler[0]}). Bitte telefonisch verbinden: {kontakte}", "fehler")
    else:
        flash(f"{gesuch['vorname']} und {angebot['vorname']} wurden per E-Mail verbunden. "
              "Wenn es passt: beide Einträge auf „erledigt“ setzen.", "erfolg")
    return redirect(url_for("kommunikation.schwarzes_brett"))
