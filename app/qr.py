"""QR-Codes als PNG-Data-URI (segno) – gemeinsamer Helfer für Voucher, Install-Seite u. a."""


def data_uri(text, scale=5):
    try:
        import segno
        return segno.make(str(text), error="m").png_data_uri(scale=scale, border=0)
    except Exception:  # noqa: BLE001
        return ""
