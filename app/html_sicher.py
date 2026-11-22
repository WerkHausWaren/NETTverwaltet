"""Kleine Helfer gegen Schadcode in der Ausgabe (XSS) – Demo-Schutz-Audit 16.11.2026.

- json_fuer_script(): JSON, das gefahrlos in <script> … </script> steht
  (ein gespeichertes „</script><script>…“ kann den Block nicht mehr verlassen).
- sichere_url(): nur http(s)-, mailto-, tel- und relative Links; alles andere
  (javascript:, data:, vbscript: …) wird zu „#“. Als Jinja-Filter „sichere_url“.
"""
import json

_SCRIPT_ERSATZ = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026", " ": "\\u2028", " ": "\\u2029"}


def json_fuer_script(wert):
    text = json.dumps(wert)
    for zeichen, ersatz in _SCRIPT_ERSATZ.items():
        text = text.replace(zeichen, ersatz)
    return text


def sichere_url(url):
    u = str(url or "").strip()
    if not u:
        return ""
    # Steuer- und Leerzeichen entfernen, die Browser vor dem Schema ignorieren
    roh = "".join(ch for ch in u if ch > " ").lower()
    if roh.startswith(("http://", "https://", "mailto:", "tel:", "/", "#", "?")) and not roh.startswith("//"):
        return u
    if ":" not in roh.split("/", 1)[0]:
        return u          # relative Adresse wie „seite.html“ oder „//server/…“
    return "#"


# ------------------------------------------------------------ HTML säubern ---
from html import escape as _esc  # noqa: E402
from html.parser import HTMLParser  # noqa: E402

_ERLAUBT = {"a", "b", "strong", "i", "em", "u", "s", "p", "br", "div", "span", "ul", "ol", "li", "h1", "h2",
            "h3", "h4", "blockquote", "hr", "img", "table", "thead", "tbody", "tr", "td", "th", "font",
            "small", "sup", "sub", "pre", "code", "center"}
_LEER = {"br", "hr", "img"}
_ATTRIBUTE = {"href", "src", "alt", "title", "style", "width", "height", "align", "colspan", "rowspan",
              "color", "size", "target", "rel"}
_WEG_MIT_INHALT = {"script", "style", "iframe", "object", "embed", "noscript", "template", "svg", "math",
                   "form", "textarea", "select", "button", "head", "title"}


class _Saeuberer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.aus, self.weg = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in _WEG_MIT_INHALT:
            self.weg += 1
            return
        if self.weg or tag not in _ERLAUBT:
            return
        teile = []
        for name, wert in attrs:
            name = (name or "").lower()
            if name not in _ATTRIBUTE or wert is None:
                continue
            if name in ("href", "src"):
                wert = sichere_url(wert)
                if name == "src" and not wert.lower().startswith(("http://", "https://", "/", "data:image/")):
                    continue
            if name == "style" and any(x in wert.lower() for x in ("expression", "url(", "javascript", "@import")):
                continue
            teile.append(f' {name}="{_esc(wert, quote=True)}"')
        self.aus.append(f"<{tag}{''.join(teile)}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _WEG_MIT_INHALT:
            self.weg = max(0, self.weg - 1)
            return
        if not self.weg and tag in _ERLAUBT and tag not in _LEER:
            self.aus.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.weg:
            self.aus.append(_esc(data, quote=False))


def html_saeubern(html):
    """Nur harmloses Formatierungs-HTML (Positivliste); Skripte, Ereignis-Attribute
    (onerror=…), javascript:-Links und eingebettete Objekte fliegen raus."""
    p = _Saeuberer()
    p.feed(str(html or ""))
    p.close()
    return "".join(p.aus)
