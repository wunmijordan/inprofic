from __future__ import annotations

from datetime import timedelta
from email.mime.image import MIMEImage
from html import unescape
import logging
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import connection, transaction
from django.db.models import F
from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

from .models import PlatformMailCampaign, PlatformMailRecipient


logger = logging.getLogger(__name__)
MAX_DELIVERY_ATTEMPTS = 3
STALE_CLAIM_AFTER = timedelta(minutes=15)


def _plain(html):
    return " ".join(unescape(strip_tags(html or "")).split())


def _render(value, context):
    return Template(value or "").render(context)


def _build_message(row, logo_bytes):
    campaign = row.campaign
    token_context = Context(
        {
            "business_name": row.business_name,
            "service": row.service,
            "plan_name": row.plan_name,
            "recipient_name": row.recipient_name,
        },
        autoescape=True,
    )
    rendered_body = _render(campaign.body_html, token_context)
    rendered_subject = _render(campaign.subject, token_context)
    rendered_heading = _render(campaign.heading, token_context)
    rendered_cta_url = _render(campaign.cta_url, token_context)
    context = {
        "subject": rendered_subject,
        "heading": rendered_heading,
        "rendered_body": rendered_body,
        "rendered_body_text": _plain(rendered_body),
        "recipient_name": row.recipient_name,
        "business_name": row.business_name,
        "service": row.service,
        "plan_name": row.plan_name,
        "cta_label": campaign.cta_label,
        "cta_url": rendered_cta_url,
        "brand_logo_cid": "cid:inprofic-logo",
        "support_email": getattr(settings, "INPROFIC_SUPPORT_EMAIL", "") or "",
    }
    message = EmailMultiAlternatives(
        rendered_subject,
        render_to_string("accounts/email/campaign.txt", context),
        settings.DEFAULT_FROM_EMAIL,
        [row.email],
    )
    message.attach_alternative(
        render_to_string("accounts/email/campaign.html", context),
        "text/html",
    )
    if logo_bytes:
        logo = MIMEImage(logo_bytes, _subtype="png")
        logo.add_header("Content-ID", "<inprofic-logo>")
        logo.add_header("Content-Disposition", "inline", filename="inprofic-logo.png")
        message.attach(logo)
    return message


def _refresh_campaigns(campaign_ids):
    now = timezone.now()
    for campaign_id in campaign_ids:
        with transaction.atomic():
            campaign = PlatformMailCampaign.objects.select_for_update().get(pk=campaign_id)
            recipients = campaign.recipients.all()
            campaign.sent_count = recipients.filter(
                status=PlatformMailRecipient.STATUS_SENT
            ).count()
            campaign.failed_count = recipients.filter(
                status=PlatformMailRecipient.STATUS_FAILED
            ).count()
            outstanding = recipients.filter(
                status__in=[
                    PlatformMailRecipient.STATUS_PENDING,
                    PlatformMailRecipient.STATUS_SENDING,
                ]
            ).exists()
            if outstanding:
                campaign.status = PlatformMailCampaign.STATUS_SENDING
                campaign.completed_at = None
            else:
                campaign.status = (
                    PlatformMailCampaign.STATUS_PARTIAL
                    if campaign.failed_count
                    else PlatformMailCampaign.STATUS_SENT
                )
                campaign.completed_at = now
            campaign.save(
                update_fields=[
                    "sent_count",
                    "failed_count",
                    "status",
                    "completed_at",
                ]
            )


def _release_stale_claims(*, campaign_id=None):
    stale = PlatformMailRecipient.objects.filter(
        status=PlatformMailRecipient.STATUS_SENDING,
        last_attempt_at__lt=timezone.now() - STALE_CLAIM_AFTER,
    )
    if campaign_id is not None:
        stale = stale.filter(campaign_id=campaign_id)
    return stale.update(
        status=PlatformMailRecipient.STATUS_PENDING,
        error="A previous delivery worker stopped before reporting a result; retrying.",
    )


def _claim_recipients(*, limit, campaign_id=None):
    claimed_at = timezone.now()
    with transaction.atomic():
        queryset = PlatformMailRecipient.objects.filter(
            status=PlatformMailRecipient.STATUS_PENDING,
            campaign__status__in=[
                PlatformMailCampaign.STATUS_QUEUED,
                PlatformMailCampaign.STATUS_SENDING,
            ],
        ).order_by("campaign_id", "id")
        if campaign_id is not None:
            queryset = queryset.filter(campaign_id=campaign_id)
        if connection.features.has_select_for_update:
            queryset = queryset.select_for_update(
                skip_locked=connection.features.has_select_for_update_skip_locked
            )
        recipient_ids = list(queryset.values_list("pk", flat=True)[:limit])
        if not recipient_ids:
            return []
        PlatformMailRecipient.objects.filter(
            pk__in=recipient_ids,
            status=PlatformMailRecipient.STATUS_PENDING,
        ).update(
            status=PlatformMailRecipient.STATUS_SENDING,
            delivery_attempts=F("delivery_attempts") + 1,
            last_attempt_at=claimed_at,
            error="",
        )
        rows = list(
            PlatformMailRecipient.objects.filter(
                pk__in=recipient_ids,
                status=PlatformMailRecipient.STATUS_SENDING,
                last_attempt_at=claimed_at,
            )
            .select_related("campaign")
            .order_by("campaign_id", "id")
        )
        PlatformMailCampaign.objects.filter(
            pk__in={row.campaign_id for row in rows},
            status=PlatformMailCampaign.STATUS_QUEUED,
        ).update(status=PlatformMailCampaign.STATUS_SENDING)
    return rows


def _record_delivery_failure(row, exc):
    terminal = row.delivery_attempts >= MAX_DELIVERY_ATTEMPTS
    PlatformMailRecipient.objects.filter(
        pk=row.pk,
        status=PlatformMailRecipient.STATUS_SENDING,
    ).update(
        status=(
            PlatformMailRecipient.STATUS_FAILED
            if terminal
            else PlatformMailRecipient.STATUS_PENDING
        ),
        error=(str(exc).strip()[:220] or exc.__class__.__name__),
    )
    return terminal


def _release_connection_failure(rows, exc):
    failed = retrying = 0
    for row in rows:
        if _record_delivery_failure(row, exc):
            failed += 1
        else:
            retrying += 1
    return failed, retrying


def dispatch_queued_platform_mail(*, limit=100, campaign_id=None):
    """Deliver a claimed batch, recording each recipient result independently."""
    limit = max(1, min(int(limit or 100), 1000))
    _release_stale_claims(campaign_id=campaign_id)
    recipients = _claim_recipients(limit=limit, campaign_id=campaign_id)
    if not recipients:
        return {"attempted": 0, "sent": 0, "failed": 0, "retrying": 0}

    campaign_ids = {row.campaign_id for row in recipients}
    logo_path = finders.find("core/brand/inprofic-wordmark-on-dark.png")
    try:
        logo_bytes = Path(logo_path).read_bytes() if logo_path else None
    except OSError:
        logger.warning("Platform mail logo could not be read; sending without it")
        logo_bytes = None
    mail_connection = get_connection(fail_silently=False)
    try:
        mail_connection.open()
    except Exception as exc:
        logger.exception("Platform mail connection could not be opened")
        failed, retrying = _release_connection_failure(recipients, exc)
        _refresh_campaigns(campaign_ids)
        return {
            "attempted": len(recipients),
            "sent": 0,
            "failed": failed,
            "retrying": retrying,
            "error": str(exc).strip()[:160],
        }

    sent = failed = retrying = 0
    try:
        for row in recipients:
            try:
                accepted = mail_connection.send_messages(
                    [_build_message(row, logo_bytes)]
                )
                if accepted != 1:
                    raise RuntimeError("The email backend did not accept this message.")
            except Exception as exc:
                logger.exception(
                    "Platform mail delivery failed for recipient %s", row.pk
                )
                if _record_delivery_failure(row, exc):
                    failed += 1
                else:
                    retrying += 1
            else:
                PlatformMailRecipient.objects.filter(
                    pk=row.pk,
                    status=PlatformMailRecipient.STATUS_SENDING,
                ).update(
                    status=PlatformMailRecipient.STATUS_SENT,
                    sent_at=timezone.now(),
                    error="",
                )
                sent += 1
    finally:
        try:
            mail_connection.close()
        except Exception:
            logger.warning("Platform mail connection did not close cleanly", exc_info=True)

    _refresh_campaigns(campaign_ids)
    return {
        "attempted": len(recipients),
        "sent": sent,
        "failed": failed,
        "retrying": retrying,
    }


def retry_failed_platform_mail(campaign):
    """Explicitly make terminal failures eligible for another delivery cycle."""
    reset = campaign.recipients.filter(
        status=PlatformMailRecipient.STATUS_FAILED
    ).update(
        status=PlatformMailRecipient.STATUS_PENDING,
        delivery_attempts=0,
        last_attempt_at=None,
        error="",
    )
    if reset:
        campaign.status = PlatformMailCampaign.STATUS_QUEUED
        campaign.completed_at = None
        campaign.save(update_fields=["status", "completed_at"])
    return reset
