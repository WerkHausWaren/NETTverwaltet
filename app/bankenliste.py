"""
Eingebaute Bankenliste (wie bei WISO): erkennt aus IBAN/BLZ automatisch
Bankname, BIC und die FinTS-PIN/TAN-Adresse – damit die App nicht mehr nach
technischen FinTS-Angaben fragen muss.

Datenquelle: app/data/blz.properties (hbci4java-Liste, gepflegt vom
Hibiscus-Projekt). Format je Zeile:
  BLZ=Name|Ort|BIC|?|HBCI-Host|https://.../fints30|...
"""
import os

_PFAD = os.path.join(os.path.dirname(__file__), "data", "blz.properties")
_CACHE = None


def _laden():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    _CACHE = {}
    try:
        with open(_PFAD, encoding="utf-8") as f:
            for zeile in f:
                zeile = zeile.strip()
                if not zeile or "=" not in zeile:
                    continue
                blz, rest = zeile.split("=", 1)
                teile = rest.split("|")
                name = teile[0] if len(teile) > 0 else ""
                ort = teile[1] if len(teile) > 1 else ""
                bic = teile[2] if len(teile) > 2 else ""
                url = next((t for t in teile if t.startswith("https://")), "")
                _CACHE[blz.strip()] = {"blz": blz.strip(), "name": name, "ort": ort,
                                       "bic": bic, "fints_url": url}
    except OSError:
        pass
    return _CACHE


def blz_aus_iban(iban):
    """Deutsche IBAN: DEkk BBBBBBBB KKKKKKKKKK → BLZ = Stellen 5–12."""
    s = (iban or "").replace(" ", "").upper()
    if s.startswith("DE") and len(s) >= 12:
        return s[4:12]
    return ""


def finde(iban_oder_blz):
    """Liefert das Bank-Dict zu einer IBAN oder BLZ (oder None)."""
    wert = (iban_oder_blz or "").replace(" ", "").upper()
    blz = blz_aus_iban(wert) if wert.startswith("DE") else wert
    if not blz:
        return None
    bank = _laden().get(blz)
    # Nur zurückgeben, wenn eine FinTS-Adresse bekannt ist
    if bank and bank.get("fints_url"):
        return bank
    return bank  # auch ohne URL zurückgeben (Name/BIC nützlich)
