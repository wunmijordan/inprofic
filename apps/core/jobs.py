"""Registry and overlap protection for recurring INPROFIC maintenance work.

Add future idempotent scheduled commands here so the management command and
the authenticated HTTP trigger always execute the same job set.
"""

from datetime import timedelta
import uuid

from django.conf import settings
from django.core.management import call_command
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from .models import ScheduledJobLease


SCHEDULED_COMMANDS = ("sync_subscriptions", "dispatch_platform_mail")
SCHEDULED_REGISTRY_LEASE = "scheduled-job-registry"


def run_all_jobs(*, stdout=None):
    """Run the shared registry synchronously.

    Callers that can overlap with another process/request should acquire the
    database lease first. Keeping the registry executor itself small preserves
    reuse by tests and by already-guarded callers.
    """
    completed = []
    for command_name in SCHEDULED_COMMANDS:
        options = {"stdout": stdout} if stdout is not None else {}
        call_command(command_name, **options)
        completed.append(command_name)
    return completed


def acquire_scheduled_job_lease():
    """Atomically claim the shared scheduled-job registry lease.

    The lease is database-backed so duplicate cron requests, manual management
    commands, and stale web requests all observe the same lock. An expired
    lease is reclaimable after a crashed/restarted process.
    """
    now = timezone.now()
    lease_seconds = max(300, int(settings.SCHEDULED_JOB_LEASE_SECONDS))
    token = uuid.uuid4().hex
    expires_at = now + timedelta(seconds=lease_seconds)

    try:
        ScheduledJobLease.objects.get_or_create(name=SCHEDULED_REGISTRY_LEASE)
    except IntegrityError:
        # A concurrent creator won the unique-row race; the conditional update
        # below is still the authoritative acquisition step.
        pass

    acquired = (
        ScheduledJobLease.objects
        .filter(name=SCHEDULED_REGISTRY_LEASE)
        .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now))
        .update(
            owner_token=token,
            lease_expires_at=expires_at,
            last_started_at=now,
            last_status=ScheduledJobLease.STATUS_RUNNING,
            last_error="",
        )
    )
    return token if acquired == 1 else None


def finish_scheduled_job_lease(token, *, status, error=""):
    """Release a lease only when ``token`` still owns it.

    Token matching prevents a stale worker from clearing a newer lease that was
    legitimately reclaimed after the original lease expired.
    """
    return (
        ScheduledJobLease.objects
        .filter(name=SCHEDULED_REGISTRY_LEASE, owner_token=token)
        .update(
            owner_token="",
            lease_expires_at=None,
            last_finished_at=timezone.now(),
            last_status=status,
            last_error=(error or "")[:4000],
        )
    )
