"""
Bankverzeichnis: Aus der IBAN des Vereins alles Weitere ableiten.

Der Verein tippt nur seine IBAN – Bankleitzahl, Bankname, BIC und die
FinTS-Serveradresse für den Umsatzabruf trägt die Verwaltung selbst ein.

Datengrundlage ist `app/daten/fints_adressen.csv`, gewonnen aus dem
öffentlichen Bankverzeichnis des hbci4java-Projekts (BLZ → Name, BIC,
PIN/TAN-Adresse). Stand: Juli 2026. Aktualisieren:
https://raw.githubusercontent.com/hbci4j/hbci4java/master/src/main/resources/blz.properties
"""
from __future__ import annotations

import csv
import os
import re

_DATEI = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "daten", "fints_adressen.csv")
_verzeichnis: dict | None = None


def _laden() -> dict:
    global _verzeichnis
    if _verzeichnis is None:
        _verzeichnis = {}
        try:
            with open(_DATEI, newline="", encoding="utf-8") as f:
                for zeile in csv.DictReader(f, delimiter=";"):
                    _verzeichnis[zeile["blz"]] = {
                        "bank": zeile["bank"], "bic": zeile["bic"],
                        "url": zeile["url"]}
        except OSError:
            pass  # ohne Verzeichnis eben keine Automatik – nie ein Absturz
    return _verzeichnis


def bank_zu_blz(blz: str) -> dict | None:
    """{bank, bic, url} zu einer Bankleitzahl – oder None."""
    return _laden().get((blz or "").strip())


def iban_pruefen(iban: str) -> bool:
    """Prüfsummen-Kontrolle (Modulo 97) – erkennt Tippfehler."""
    iban = re.sub(r"\s+", "", iban or "").upper()
    if not re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$", iban):
        return False
    umgestellt = iban[4:] + iban[:4]
    zahl = "".join(str(int(z, 36)) for z in umgestellt)
    return int(zahl) % 97 == 1


def aus_iban(iban: str) -> dict | None:
    """Leitet aus einer deutschen IBAN alles ab, was das Verzeichnis hergibt.

    Ergebnis: {blz, kontonummer, bank, bic, url, gueltig} – oder None,
    wenn es keine deutsche IBAN im richtigen Format ist. `gueltig` ist das
    Ergebnis der Prüfsummen-Kontrolle; bank/bic/url sind leer, wenn die
    Bankleitzahl nicht im Verzeichnis steht.
    """
    kompakt = re.sub(r"\s+", "", iban or "").upper()
    if not re.match(r"^DE\d{20}$", kompakt):
        return None
    blz = kompakt[4:12]
    eintrag = bank_zu_blz(blz) or {"bank": "", "bic": "", "url": ""}
    return {"blz": blz, "kontonummer": kompakt[12:22].lstrip("0") or "0",
            "gueltig": iban_pruefen(kompakt), **eintrag}
