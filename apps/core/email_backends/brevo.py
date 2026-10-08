"""Django email backend for Brevo's transactional email HTTP API."""

from __future__ import annotations

import base64
import json
import logging
from email.utils import parseaddr
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.exceptions import ImproperlyConfigured
from django.templatetags.static import static

logger = logging.getLogger(__name__)


class BrevoEmailError(Exception):
    """Raised when Brevo rejects a message or cannot be reached."""


class EmailBackend(BaseEmailBackend):
    """Send Django EmailMessage instances through Brevo without hosted templates."""

    endpoint = "https://api.brevo.com/v3/smtp/email"

    def __init__(self, *args, api_key=None, timeout=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.api_key = api_key or getattr(settings, "BREVO_API_KEY", "")
        self.timeout = timeout or getattr(settings, "BREVO_API_TIMEOUT", 8)
        self._opened = False

    def open(self):
        if not self.api_key:
            raise ImproperlyConfigured("EMAIL_PROVIDER=brevo requires BREVO_API_KEY.")
        if any(character in self.api_key for character in "\r\n\t"):
            # Environment variables in Render must be entered one per row.
            # Reject a pasted multi-line .env block before urllib can include
            # the credential value in an Invalid header value exception/log.
            raise ImproperlyConfigured(
                "BREVO_API_KEY must contain only the Brevo API key. "
                "Set each Render environment variable in its own row."
            )
        self._opened = True
        return True

    def close(self):
        self._opened = False

    def send_messages(self, email_messages):
        messages = list(email_messages or [])
        if not messages:
            return 0
        new_connection = not self._opened
        if new_connection:
            try:
                self.open()
            except Exception:
                if not self.fail_silently:
                    raise
                logger.exception("Brevo email backend could not be opened")
                return 0

        sent = 0
        try:
            for message in messages:
                try:
                    if self._send(message):
                        sent += 1
                except Exception:
                    if not self.fail_silently:
                        raise
                    logger.exception("Brevo failed to accept a Django email message")
        finally:
            if new_connection:
                self.close()
        return sent

    def _send(self, message):
        sender_email, sender_name = self._sender(message)
        if not sender_email:
            raise BrevoEmailError("Email message has no valid sender address.")
        to = self._addresses(getattr(message, "to", []))
        if not to:
            return False

        html_content = ""
        for alternative, mimetype in getattr(message, "alternatives", []) or []:
            if mimetype == "text/html":
                html_content = alternative.decode("utf-8", errors="replace") if isinstance(alternative, bytes) else str(alternative)
                break
        text_content = message.body.decode("utf-8", errors="replace") if isinstance(message.body, bytes) else str(message.body or "")

        attachments, inline_logo_found = self._attachments(message)
        if inline_logo_found and html_content:
            logo_url = self._brand_logo_url()
            if logo_url:
                html_content = html_content.replace("cid:inprofic-logo", logo_url)

        payload = {
            "sender": {"email": sender_email, "name": sender_name or "INPROFIC"},
            "to": to,
            "subject": str(message.subject or ""),
        }
        if html_content:
            payload["htmlContent"] = html_content
        else:
            payload["textContent"] = text_content
        cc = self._addresses(getattr(message, "cc", []))
        bcc = self._addresses(getattr(message, "bcc", []))
        if cc:
            payload["cc"] = cc
        if bcc:
            payload["bcc"] = bcc
        reply_to = self._addresses(getattr(message, "reply_to", []))
        if reply_to:
            payload["replyTo"] = reply_to[0]
        if attachments:
            payload["attachment"] = attachments

        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "accept": "application/json",
                "api-key": self.api_key,
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return 200 <= response.status < 300
        except HTTPError as exc:
            raise BrevoEmailError(f"Brevo rejected the email (HTTP {exc.code}).") from None
        except (URLError, TimeoutError, OSError) as exc:
            raise BrevoEmailError(f"Brevo email API connection failed: {exc}") from exc

    def _sender(self, message):
        default_name, default_email = parseaddr(message.from_email or settings.DEFAULT_FROM_EMAIL)
        configured_email = getattr(settings, "BREVO_SENDER_EMAIL", "")
        configured_name = getattr(settings, "BREVO_SENDER_NAME", "")
        return configured_email or default_email, configured_name or default_name

    @staticmethod
    def _addresses(values):
        addresses = []
        for value in values or []:
            name, address = parseaddr(str(value))
            if address:
                item = {"email": address}
                if name:
                    item["name"] = name
                addresses.append(item)
        return addresses

    @staticmethod
    def _attachments(message):
        api_attachments = []
        inline_logo_found = False
        for attachment in getattr(message, "attachments", []) or []:
            if hasattr(attachment, "get_payload"):
                filename = attachment.get_filename()
                content = attachment.get_payload(decode=True)
                content_id = (attachment.get("Content-ID") or "").strip("<>")
                if content_id == "inprofic-logo":
                    inline_logo_found = True
                    # The project logo is linked by a stable public static URL
                    # in API-delivered HTML instead of a provider-specific CID.
                    if EmailBackend._brand_logo_url():
                        continue
                mimetype = attachment.get_content_type()
            elif isinstance(attachment, (tuple, list)) and len(attachment) >= 2:
                filename, content = attachment[0], attachment[1]
                mimetype = attachment[2] if len(attachment) > 2 else "application/octet-stream"
            else:
                continue
            if not filename or content is None:
                continue
            if isinstance(content, str):
                content = content.encode("utf-8")
            api_attachments.append({
                "name": str(filename),
                "content": base64.b64encode(content).decode("ascii"),
            })
        return api_attachments, inline_logo_found

    @staticmethod
    def _brand_logo_url():
        site_url = getattr(settings, "SITE_URL", "") or ""
        if not site_url:
            render_host = getattr(settings, "RENDER_EXTERNAL_HOSTNAME", "")
            site_url = f"https://{render_host}" if render_host else ""
        if not site_url:
            return ""
        return urljoin(
            site_url.rstrip("/") + "/",
            static("core/brand/inprofic-wordmark-on-dark.png"),
        )
