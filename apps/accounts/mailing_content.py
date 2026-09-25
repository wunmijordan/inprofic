"""Safe personalization and HTML handling for platform email campaigns."""

from html import escape
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.template import Template, TemplateSyntaxError
from django.utils.html import strip_tags


ALLOWED_PERSONALIZATION_FIELDS = {
    "business_name",
    "plan_name",
    "recipient_name",
    "service",
}
_TOKEN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
_ALLOWED_TAGS = {
    "a", "b", "blockquote", "br", "em", "h2", "h3", "i", "li",
    "ol", "p", "strong", "u", "ul",
}
_VOID_TAGS = {"br"}
_SUPPRESSED_TAGS = {"iframe", "noscript", "object", "script", "style"}


class _MailHTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._suppressed = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SUPPRESSED_TAGS:
            self._suppressed += 1
            return
        if self._suppressed or tag not in _ALLOWED_TAGS:
            return
        safe_attrs = []
        if tag == "a":
            attributes = {key.lower(): value or "" for key, value in attrs}
            href = attributes.get("href", "").strip()
            if _safe_link(href):
                safe_attrs.append(f' href="{escape(href, quote=True)}"')
                safe_attrs.append(' target="_blank"')
                safe_attrs.append(' rel="noopener noreferrer"')
        self.out.append(f"<{tag}{''.join(safe_attrs)}>")

    def handle_startendtag(self, tag, attrs):
        if not self._suppressed and tag.lower() == "br":
            self.out.append("<br>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SUPPRESSED_TAGS:
            self._suppressed = max(0, self._suppressed - 1)
            return
        if not self._suppressed and tag in _ALLOWED_TAGS and tag not in _VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._suppressed:
            self.out.append(escape(data))


def _safe_link(value):
    if not value:
        return False
    if "{{" in value:
        return validate_personalization(value) == value
    return urlsplit(value).scheme.lower() in {"http", "https", "mailto"}


def validate_personalization(value):
    """Accept simple known variables while rejecting template tags and typos."""
    value = value or ""
    if "{%" in value or "{#" in value:
        raise ValidationError("Template tags and comments are not allowed.")
    try:
        Template(value)
    except TemplateSyntaxError as exc:
        raise ValidationError(f"Invalid personalization syntax: {exc}") from exc
    unknown = sorted(set(_TOKEN.findall(value)) - ALLOWED_PERSONALIZATION_FIELDS)
    if unknown:
        raise ValidationError(
            "Unknown personalization field(s): "
            + ", ".join(unknown)
            + "."
        )
    # A compiled template can treat malformed opening braces as plain text.
    remainder = _TOKEN.sub("", value)
    if "{{" in remainder or "}}" in remainder:
        raise ValidationError("Personalization variables must use {{ variable_name }}.")
    return value


def clean_mail_html(value):
    value = validate_personalization(value)
    parser = _MailHTMLSanitizer()
    parser.feed(value)
    parser.close()
    cleaned = "".join(parser.out).strip()
    if not strip_tags(cleaned).strip():
        raise ValidationError("Write a message before sending.")
    return cleaned
