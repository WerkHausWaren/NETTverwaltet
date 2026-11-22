"""
Abwesenheit von Mitgliedern: „abwesend" (an/aus) + optionaler Zeitraum (von/bis) + Notiz.
Stellt Jinja-Helfer bereit, um überall dasselbe Badge grafisch einzubinden.
"""
from datetime import date
from markupsafe import Markup, escape


def _feld(m, name):
    try:
        return m[name] if name in m.keys() else None
    except (AttributeError, TypeError, KeyError, IndexError):
        return None


def ist_abwesend(m, heute=None):
    """True, wenn das Mitglied aktuell abwesend ist (Zeitraum von/bis berücksichtigt)."""
    if not m or not _feld(m, "abwesend"):
        return False
    h = heute or date.today().isoformat()
    von = _feld(m, "abwesend_von")
    if von and str(von)[:10] > h:       # Abwesenheit beginnt erst später
        return False
    bis = _feld(m, "abwesend_bis")
    if bis and str(bis)[:10] < h:       # Abwesenheit schon vorbei
        return False
    return True


def _kurz(d):
    s = str(d)
    return s[8:10] + "." + s[5:7] + "." if len(s) >= 10 else s


def badge(m, gross=False):
    """HTML-Badge „🌴 Abwesend [Zeitraum] [· Notiz]" – oder leer, wenn anwesend."""
    if not ist_abwesend(m):
        return Markup("")
    von = _feld(m, "abwesend_von")
    bis = _feld(m, "abwesend_bis")
    notiz = _feld(m, "abwesend_notiz") or ""
    text = "🌴 Abwesend"
    if von and bis:
        text += " " + _kurz(von) + "–" + _kurz(bis)
    elif bis:
        text += " bis " + _kurz(bis)
    if gross and notiz:
        text += " · " + str(notiz)
    stil = "padding:5px 12px;font-size:13px" if gross else "padding:1px 8px;font-size:11px"
    return Markup(
        '<span title="%s" style="display:inline-block;background:#fff4e5;color:#9a5a1e;'
        'border:1px solid #f3d3ab;border-radius:999px;font-weight:600;white-space:nowrap;%s">%s</span>'
        % (escape(notiz or "abwesend"), stil, escape(text)))
