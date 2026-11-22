"""GiroCode (EPC-QR-Code): Überweisungsdaten als QR-Code für Banking-Apps.

Offizieller Standard des European Payments Council (Quick Response Code,
EPC069-12, Version 002) – von praktisch allen deutschen Banking-Apps
unterstützt ("GiroCode"/"Foto-Überweisung"). Die App übernimmt Empfänger,
IBAN, Betrag und Verwendungszweck; Tippfehler beim Zahlcode entfallen.

Rechtlich unbedenklich: Der Code transportiert exakt die Zahlungsangaben,
die ohnehin auf der Rechnung stehen – er ersetzt keine Pflichtangabe.
"""
import io


def payload(name, iban, betrag, zweck, bic=""):
    """EPC-Datensatz oder '' bei unvollständigen/ungültigen Angaben."""
    iban = (iban or "").replace(" ", "").upper()
    name = (name or "").strip()[:70]
    zweck = (zweck or "").strip()[:140]
    try:
        betrag = round(float(betrag), 2)
    except (TypeError, ValueError):
        return ""
    if not iban or len(iban) < 15 or not name or not (0 < betrag <= 999999999.99):
        return ""
    return "\n".join([
        "BCD",                       # Service-Kennung
        "002",                       # Version (BIC im EWR optional)
        "1",                         # Zeichensatz UTF-8
        "SCT",                       # SEPA Credit Transfer
        (bic or "").replace(" ", ""),
        name,                        # Empfänger
        iban,
        f"EUR{betrag:.2f}",
        "",                          # Purpose-Code (leer)
        "",                          # strukturierte Referenz (leer)
        zweck,                       # Verwendungszweck (Zahlcode)
    ])


def png_bytes(text, scale=4):
    """QR als PNG-Bytes (fürs PDF) – mit Ruhezone, Fehlerkorrektur M lt. Standard."""
    import segno
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=scale, border=2)
    return buf.getvalue()


def data_uri(text, scale=5):
    """QR als PNG-Data-URI (für die Druck-/Webansicht)."""
    try:
        import segno
        return segno.make(text, error="m").png_data_uri(scale=scale, border=2)
    except Exception:  # noqa: BLE001
        return ""
