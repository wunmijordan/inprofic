"""Founder-managed marketing campaign helpers.

Campaign copy is intentionally stored as a small, sanitised subset of HTML so
Founder can format public promo creative without shipping code.  The editor is
limited to INPROFIC's bundled font families and presentation-safe text styles.
"""

from html import escape
from html.parser import HTMLParser
import re


SYSTEM_MARKETING_FONTS = (
    ("Sora", "Sora, sans-serif"),
    ("Inter", "Inter, sans-serif"),
    ("Fraunces", "Fraunces, serif"),
    ("IBM Plex Mono", "IBM Plex Mono, monospace"),
)

_ALLOWED_TAGS = {"p", "br", "strong", "b", "em", "i", "u", "span", "h2", "h3", "div"}
_VOID_TAGS = {"br"}
_FONT_LOOKUP = {name.lower(): css for name, css in SYSTEM_MARKETING_FONTS}
_HEX = re.compile(r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$")
_SIZE = re.compile(r"^(\d+(?:\.\d+)?)(px|rem)$", re.I)
_SPACE = re.compile(r"^(\d+(?:\.\d+)?)(px|em)$", re.I)
_LINE_HEIGHT = re.compile(r"^(\d+(?:\.\d+)?)(?:px)?$", re.I)


def _safe_style(raw):
    cleaned = []
    for declaration in (raw or "").split(";"):
        if ":" not in declaration:
            continue
        prop, value = declaration.split(":", 1)
        prop = prop.strip().lower()
        value = value.strip().strip('"\'')
        if prop == "font-family":
            first = value.split(",", 1)[0].strip().strip('"\'').lower()
            css = _FONT_LOOKUP.get(first)
            if css:
                cleaned.append(f"font-family:{css}")
        elif prop == "font-size":
            match = _SIZE.match(value)
            if match:
                number = float(match.group(1)); unit = match.group(2).lower()
                minimum, maximum = (12, 72) if unit == "px" else (0.75, 4.5)
                if minimum <= number <= maximum:
                    cleaned.append(f"font-size:{number:g}{unit}")
        elif prop == "color" and _HEX.match(value):
            cleaned.append(f"color:{value}")
        elif prop == "text-align" and value in {"left", "center", "right"}:
            cleaned.append(f"text-align:{value}")
        elif prop == "font-weight" and value in {"400", "500", "600", "700", "bold", "normal"}:
            cleaned.append(f"font-weight:{value}")
        elif prop == "font-style" and value in {"italic", "normal"}:
            cleaned.append(f"font-style:{value}")
        elif prop == "text-decoration" and value in {"underline", "none"}:
            cleaned.append(f"text-decoration:{value}")
        elif prop == "letter-spacing":
            match = _SPACE.match(value)
            if match and float(match.group(1)) <= 8:
                cleaned.append(f"letter-spacing:{float(match.group(1)):g}{match.group(2).lower()}")
        elif prop == "line-height":
            match = _LINE_HEIGHT.match(value)
            if match:
                number = float(match.group(1))
                if (1 <= number <= 2.5) or (12 <= number <= 90):
                    cleaned.append(f"line-height:{number:g}{'px' if value.lower().endswith('px') else ''}")
        elif prop == "text-transform" and value in {"uppercase", "none"}:
            cleaned.append(f"text-transform:{value}")
    return ";".join(cleaned)


class _CampaignHTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._suppressed = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "iframe", "object"}:
            self._suppressed += 1
            return
        if self._suppressed or tag not in _ALLOWED_TAGS:
            return
        style = ""
        for key, value in attrs:
            if key.lower() == "style":
                style = _safe_style(value)
                break
        attr_text = f' style="{escape(style, quote=True)}"' if style else ""
        self.out.append(f"<{tag}{attr_text}>")

    def handle_startendtag(self, tag, attrs):
        if tag.lower() == "br":
            self.out.append("<br>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "iframe", "object"}:
            self._suppressed = max(0, self._suppressed - 1)
            return
        if not self._suppressed and tag in _ALLOWED_TAGS and tag not in _VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._suppressed:
            self.out.append(escape(data))


def sanitize_campaign_html(value):
    parser = _CampaignHTMLSanitizer()
    parser.feed(value or "")
    parser.close()
    return "".join(parser.out).strip()
