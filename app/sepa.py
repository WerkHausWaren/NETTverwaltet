"""
Erzeugt eine SEPA-Lastschrift-Datei (pain.008.001.02) aus offenen Forderungen.

Diese Datei lädt der Verein im Online-Banking hoch, um alle Beiträge oder
Dauerspenden auf einmal einzuziehen. Reine Standardbibliothek – keine Zusatzpakete.

Drei Dinge sind hier wichtig und leicht falsch zu machen:

1. **Zeichensatz.** SEPA erlaubt nur einen kleinen Vorrat an Zeichen. Umlaute und
   alles Ungewöhnliche muss vorher umgeschrieben werden – sonst weist die Bank die
   Datei zurück oder es kommen verstümmelte Namen auf dem Kontoauszug an.
2. **Reihenfolge.** Erst umschreiben, dann kürzen, dann für XML entschärfen. Wer
   nach dem Entschärfen kürzt, zerschneidet womöglich ein „&amp;“ – und macht damit
   nicht einen Posten, sondern die ganze Datei unbrauchbar.
3. **Nichts erfinden.** Fehlt eine Mandatsreferenz oder das Unterschriftsdatum,
   darf die Datei keinen Platzhalter enthalten. Solche Posten bleiben draußen und
   werden namentlich zurückgemeldet, damit man sie in Ruhe nachtragen kann.
"""
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, date
from xml.sax.saxutils import escape

# Zeichenvorrat des SEPA-Datenformats (EPC-Regelwerk, „Latin character set")
_ERLAUBT = set("abcdefghijklmnopqrstuvwxyz"
               "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
               "0123456789"
               "/-?:().,'+ ")

# Was sich sinnvoll übersetzen lässt, wird übersetzt statt gestrichen.
_UMSCHRIFT = {
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",
    "&": "und", "%": "Prozent", "€": "EUR", "„": "'", "“": "'", "”": "'", "‚": "'",
    "«": "'", "»": "'", "–": "-", "—": "-", "…": "...", "´": "'", "`": "'", "\"": "'",
}


def sepa_text(wert, laenge=140):
    """Text so aufbereiten, dass er gefahrlos in die Datei darf.

    Reihenfolge: umschreiben → auf Länge kürzen → für XML entschärfen.
    Gibt einen fertigen, in XML einsetzbaren Text zurück.
    """
    roh = str(wert or "")
    umgeschrieben = []
    for zeichen in roh:
        if zeichen in _UMSCHRIFT:
            umgeschrieben.append(_UMSCHRIFT[zeichen])
        elif zeichen in _ERLAUBT:
            umgeschrieben.append(zeichen)
        elif zeichen.isspace():
            umgeschrieben.append(" ")          # Zeilenumbrüche und Tabulatoren
        else:
            # Akzente abtrennen (é → e); bleibt etwas Unbekanntes, fällt es weg
            zerlegt = unicodedata.normalize("NFKD", zeichen)
            ersatz = "".join(z for z in zerlegt if z in _ERLAUBT)
            umgeschrieben.append(ersatz)
    sauber = re.sub(r"\s{2,}", " ", "".join(umgeschrieben)).strip()
    return escape(sauber[:laenge])


def _iban_sauber(iban):
    return re.sub(r"\s+", "", (iban or "")).upper()


def _bic_sauber(bic):
    return re.sub(r"\s+", "", (bic or "")).upper()


def iban_gueltig(iban):
    """Prüfsummenkontrolle – nutzt die vorhandene Prüfung aus dem Bankverzeichnis."""
    from .fints_adressen import iban_pruefen
    return iban_pruefen(iban)


def bic_gueltig(bic):
    """Eine BIC hat 8 oder 11 Stellen; leer ist erlaubt (dann NOTPROVIDED)."""
    b = _bic_sauber(bic)
    return not b or bool(re.match(r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$", b))


def glaeubiger_id_gueltig(gid):
    """Aufbau der Gläubiger-Identifikationsnummer: DE + 2 Prüfziffern + ZZZ + 11 Stellen."""
    g = re.sub(r"\s+", "", (gid or "")).upper()
    return bool(re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]{3}[A-Z0-9]{1,28}$", g))


def _datum_gueltig(wert):
    """True, wenn 'JJJJ-MM-TT' und nicht in der Zukunft (Unterschrift kann nicht später sein)."""
    try:
        d = date.fromisoformat(str(wert or "")[:10])
    except (ValueError, TypeError):
        return False
    return d <= date.today()


def posten_pruefen(posten):
    """Trennt einziehbare Posten von unvollständigen.

    Rückgabe: (gute, probleme). `probleme` ist eine Liste aus
    {name, grund} – gedacht zum Anzeigen, damit man weiß, wen es betrifft.
    """
    gute, probleme = [], []
    for p in posten:
        name = (p.get("name") or "").strip() or "Ohne Namen"
        iban = _iban_sauber(p.get("iban"))
        if not iban:
            probleme.append({"name": name, "grund": "keine IBAN hinterlegt"})
            continue
        if not iban_gueltig(iban):
            probleme.append({"name": name, "grund": "die IBAN stimmt nicht (Prüfziffer falsch)"})
            continue
        if not bic_gueltig(p.get("bic")):
            probleme.append({"name": name, "grund": "die BIC hat ein ungültiges Format"})
            continue
        if not (p.get("mandatsreferenz") or "").strip():
            probleme.append({"name": name, "grund": "keine Mandatsreferenz vergeben"})
            continue
        if not _datum_gueltig(p.get("mandatsdatum")):
            probleme.append({"name": name, "grund": "Unterschriftsdatum des Mandats fehlt oder liegt in der Zukunft"})
            continue
        try:
            betrag = round(float(p.get("betrag") or 0), 2)
        except (TypeError, ValueError):
            probleme.append({"name": name, "grund": "der Betrag ist keine Zahl"})
            continue
        if betrag <= 0:
            probleme.append({"name": name, "grund": "der Betrag ist null oder negativ"})
            continue
        gute.append(dict(p, iban=iban, bic=_bic_sauber(p.get("bic")), betrag=betrag))
    return gute, probleme


def naechster_bankarbeitstag(tag):
    """Schiebt ein Datum auf den nächsten Bankarbeitstag (Wochenenden und die
    TARGET2-Feiertage, an denen der Zahlungsverkehr ruht)."""
    fest = {(1, 1), (5, 1), (12, 25), (12, 26)}      # Neujahr, 1. Mai, beide Weihnachtstage
    d = tag
    for _ in range(14):                              # Schutz gegen Endlosschleifen
        if d.weekday() < 5 and (d.month, d.day) not in fest:
            return d
        d = d + timedelta(days=1)
    return d


def standard_faelligkeit(vorlauf_tage=3):
    """Vorschlag für den Einzugstag: heute + Vorlauf, auf einen Bankarbeitstag gelegt."""
    return naechster_bankarbeitstag(date.today() + timedelta(days=vorlauf_tage))


def erzeuge_xml(einst, posten, ausfuehrungsdatum=None, referenz_praefix="EINZUG"):
    """Baut die Lastschriftdatei.

    einst  : Einstellungen des Vereins (Gläubiger-Daten)
    posten : Liste von Dicts mit name, iban, bic, betrag, mandatsreferenz,
             mandatsdatum, verwendungszweck – und optional `referenz` für die
             Ende-zu-Ende-Kennung (sonst wird eine laufende erzeugt).
    ausfuehrungsdatum : 'JJJJ-MM-TT'; ohne Angabe der nächste passende Bankarbeitstag.

    Rückgabe: (xml, probleme). Unvollständige Posten sind NICHT in der Datei,
    sondern stehen in `probleme` – siehe posten_pruefen().
    """
    gute, probleme = posten_pruefen(posten)

    glaeubiger_name = einst["bank_kontoinhaber"] or einst["vereinsname"] or "Verein"
    glaeubiger_iban = _iban_sauber(einst["bank_iban"])
    glaeubiger_bic = _bic_sauber(einst["bank_bic"])
    glaeubiger_id = re.sub(r"\s+", "", (einst["glaeubiger_id"] or "")).upper()

    if not ausfuehrungsdatum:
        ausfuehrungsdatum = standard_faelligkeit().isoformat()

    jetzt = datetime.now()
    # Die Nachrichten-Kennung muss eindeutig sein – auch wenn jemand zweimal
    # kurz hintereinander klickt. Zeitstempel allein reicht dafür nicht.
    kennung = jetzt.strftime("%Y%m%d%H%M%S") + secrets.token_hex(3).upper()
    stempel = jetzt.strftime("%Y%m%d")
    msg_id = "MSG" + kennung
    pmt_id = "PMT" + kennung

    summe = round(sum(p["betrag"] for p in gute), 2)
    anzahl = len(gute)

    tx = []
    for i, p in enumerate(gute, 1):
        betrag = f"{p['betrag']:.2f}"
        referenz = (p.get("referenz") or "").strip() or f"{referenz_praefix}-{stempel}-{i}"
        bic = p["bic"]
        agent = (f"<BIC>{sepa_text(bic, 11)}</BIC>" if bic
                 else "<Othr><Id>NOTPROVIDED</Id></Othr>")
        tx.append(f"""
        <DrctDbtTxInf>
          <PmtId><EndToEndId>{sepa_text(referenz, 35)}</EndToEndId></PmtId>
          <InstdAmt Ccy="EUR">{betrag}</InstdAmt>
          <DrctDbtTx>
            <MndtRltdInf>
              <MndtId>{sepa_text(p['mandatsreferenz'], 35)}</MndtId>
              <DtOfSgntr>{str(p['mandatsdatum'])[:10]}</DtOfSgntr>
            </MndtRltdInf>
          </DrctDbtTx>
          <DbtrAgt><FinInstnId>{agent}</FinInstnId></DbtrAgt>
          <Dbtr><Nm>{sepa_text(p['name'], 70)}</Nm></Dbtr>
          <DbtrAcct><Id><IBAN>{p['iban']}</IBAN></Id></DbtrAcct>
          <RmtInf><Ustrd>{sepa_text(p.get('verwendungszweck') or 'Mitgliedsbeitrag', 140)}</Ustrd></RmtInf>
        </DrctDbtTxInf>""")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.008.001.02" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <CstmrDrctDbtInitn>
    <GrpHdr>
      <MsgId>{msg_id}</MsgId>
      <CreDtTm>{jetzt.strftime("%Y-%m-%dT%H:%M:%S")}</CreDtTm>
      <NbOfTxs>{anzahl}</NbOfTxs>
      <CtrlSum>{summe:.2f}</CtrlSum>
      <InitgPty><Nm>{sepa_text(glaeubiger_name, 70)}</Nm></InitgPty>
    </GrpHdr>
    <PmtInf>
      <PmtInfId>{pmt_id}</PmtInfId>
      <PmtMtd>DD</PmtMtd>
      <NbOfTxs>{anzahl}</NbOfTxs>
      <CtrlSum>{summe:.2f}</CtrlSum>
      <PmtTpInf>
        <SvcLvl><Cd>SEPA</Cd></SvcLvl>
        <LclInstrm><Cd>CORE</Cd></LclInstrm>
        <SeqTp>RCUR</SeqTp>
      </PmtTpInf>
      <ReqdColltnDt>{ausfuehrungsdatum}</ReqdColltnDt>
      <Cdtr><Nm>{sepa_text(glaeubiger_name, 70)}</Nm></Cdtr>
      <CdtrAcct><Id><IBAN>{glaeubiger_iban}</IBAN></Id></CdtrAcct>
      <CdtrAgt><FinInstnId>{('<BIC>' + sepa_text(glaeubiger_bic, 11) + '</BIC>') if glaeubiger_bic else '<Othr><Id>NOTPROVIDED</Id></Othr>'}</FinInstnId></CdtrAgt>
      <ChrgBr>SLEV</ChrgBr>
      <CdtrSchmeId><Id><PrvtId><Othr><Id>{sepa_text(glaeubiger_id, 35)}</Id><SchmeNm><Prtry>SEPA</Prtry></SchmeNm></Othr></PrvtId></Id></CdtrSchmeId>{''.join(tx)}
    </PmtInf>
  </CstmrDrctDbtInitn>
</Document>"""
    return xml, probleme


def pruefe_voraussetzungen(einst):
    """Gibt eine Liste fehlender oder falscher Angaben des Vereins zurück (leer = alles ok)."""
    fehlt = []
    iban = (einst["bank_iban"] or "").strip()
    if not iban:
        fehlt.append("IBAN des Vereins")
    elif not iban_gueltig(iban):
        fehlt.append("eine gültige IBAN des Vereins (die hinterlegte stimmt nicht)")
    gid = (einst["glaeubiger_id"] or "").strip()
    if not gid:
        fehlt.append("SEPA-Gläubiger-Identifikationsnummer")
    elif not glaeubiger_id_gueltig(gid):
        fehlt.append("eine gültige Gläubiger-Identifikationsnummer "
                     "(Aufbau: DE, zwei Ziffern, ZZZ und elf weitere Zeichen)")
    if not bic_gueltig(einst["bank_bic"]):
        fehlt.append("eine gültige BIC des Vereinskontos (8 oder 11 Zeichen)")
    if not (einst["bank_kontoinhaber"] or einst["vereinsname"] or "").strip():
        fehlt.append("Kontoinhaber / Vereinsname")
    return fehlt
