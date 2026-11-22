"""Prüft die IBAN-Automatik: Ableitung, Verzeichnis, Prüfsumme."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import fints_adressen as fa  # noqa: E402


def test(name, bedingung, zusatz=""):
    print(("  ✓ " if bedingung else "  ✗ ") + name + (f"  {zusatz}" if not bedingung else ""))
    return bool(bedingung)


ok = True
# Müritz-Sparkasse: erfundene Kontonummer 0, gültige Prüfsumme (keine echte Kontoverbindung)
b = fa.aus_iban("DE80 1505 0100 0000 0000 00")
ok &= test("Deutsche IBAN wird zerlegt", b is not None)
ok &= test("Bankleitzahl stimmt", b["blz"] == "15050100", b["blz"])
ok &= test("Prüfsumme gültig", b["gueltig"])
ok &= test("Bank erkannt", "ritz-Sparkasse" in b["bank"], b["bank"])
ok &= test("BIC ergänzt", b["bic"] == "NOLADE21WRN", b["bic"])
ok &= test("FinTS-Adresse gefunden",
           b["url"] == "https://banking-mv6.s-fints-pt-mv.de/fints30", b["url"])

# Tippfehler: eine Ziffer verdreht -> Prüfsumme schlägt an
b2 = fa.aus_iban("DE08150501000000000000")
ok &= test("Zahlendreher fällt auf", b2 is not None and not b2["gueltig"])

# Keine deutsche IBAN / Unsinn
ok &= test("Österreich wird nicht zerlegt", fa.aus_iban("AT611904300234573201") is None)
ok &= test("Unsinn wird abgewiesen", fa.aus_iban("Hallo") is None)
ok &= test("Leer wird abgewiesen", fa.aus_iban("") is None)

# Unbekannte (aber formal mögliche) Bankleitzahl: Felder leer, kein Absturz
b3 = fa.aus_iban("DE02 9999 9999 0123 4567 89")
ok &= test("Unbekannte BLZ: leere Bankfelder", b3 is not None and b3["bank"] == "" and b3["url"] == "")

# Verzeichnis direkt
ok &= test("Verzeichnis kennt >2500 FinTS-Adressen",
           sum(1 for v in fa._laden().values() if v["url"]) > 2500)

print("\nAlles bestanden." if ok else "\nFEHLER – siehe oben.")
sys.exit(0 if ok else 1)
