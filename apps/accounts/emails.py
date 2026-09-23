from __future__ import annotations

import logging
from email.mime.image import MIMEImage
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_signup_welcome_email(*, user, business, subscription, workspace_url):
    """Send the branded welcome message for a newly-created workspace.

    Email is deliberately best-effort: signup is the source-of-truth operation,
    so a temporary mail-provider failure must never roll back or invalidate the
    tenant that was just provisioned.
    """
    email = (getattr(user, "email", "") or "").strip()
    if not email:
        return False

    context = {
        "user": user,
        "business": business,
        "subscription": subscription,
        "workspace_url": workspace_url,
        "brand_name": "INPROFIC",
        "brand_logo_cid": "cid:inprofic-logo",
        "support_email": getattr(settings, "INPROFIC_SUPPORT_EMAIL", "") or "",
    }
    subject = f"Welcome to INPROFIC — {business.name} is READY!"
    text_body = render_to_string("accounts/email/welcome.txt", context)
    html_body = render_to_string("accounts/email/welcome.html", context)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    message.attach_alternative(html_body, "text/html")
    logo_path = finders.find("core/brand/inprofic-wordmark-on-dark.png")
    if logo_path:
        logo = MIMEImage(Path(logo_path).read_bytes(), _subtype="png")
        logo.add_header("Content-ID", "<inprofic-logo>")
        logo.add_header("Content-Disposition", "inline", filename="inprofic-logo.png")
        message.attach(logo)
    try:
        return bool(message.send(fail_silently=False))
    except Exception:
        logger.exception("Welcome email failed for signup user_id=%s business_id=%s", user.pk, business.pk)
        return False
