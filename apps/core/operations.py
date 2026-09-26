import logging
import secrets
import threading

from django.conf import settings
from django.db import close_old_connections
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .jobs import acquire_scheduled_job_lease, finish_scheduled_job_lease, run_all_jobs
from .models import ScheduledJobLease


logger = logging.getLogger(__name__)


def _run_scheduled_jobs_background(token):
    """Run scheduled maintenance outside the cron request lifecycle."""
    close_old_connections()
    try:
        completed = run_all_jobs()
    except Exception as exc:
        logger.exception("Scheduled INPROFIC jobs failed")
        finish_scheduled_job_lease(
            token,
            status=ScheduledJobLease.STATUS_FAILED,
            error=str(exc),
        )
    else:
        finish_scheduled_job_lease(token, status=ScheduledJobLease.STATUS_SUCCEEDED)
        logger.info("Scheduled INPROFIC jobs completed: %s", ", ".join(completed))
    finally:
        close_old_connections()


@require_GET
def health(request):
    """Lightweight wake-up/readiness endpoint that intentionally avoids the DB."""
    return JsonResponse({"status": "ok", "service": "inprofic"})


@csrf_exempt
@require_POST
def run_jobs(request):
    """Accept the cron trigger and run the shared registry outside the request."""
    expected = settings.CRON_SECRET
    scheme, separator, supplied = request.headers.get("Authorization", "").partition(" ")
    authorized = (
        expected
        and separator
        and scheme.lower() == "bearer"
        and secrets.compare_digest(supplied.strip(), expected)
    )
    if not authorized:
        return JsonResponse({"detail": "Forbidden"}, status=403)

    try:
        token = acquire_scheduled_job_lease()
    except Exception:
        logger.exception("Could not acquire scheduled-job lease")
        return JsonResponse({"status": "error"}, status=500)

    if token is None:
        return JsonResponse(
            {
                "status": "already_running",
                "detail": "Scheduled maintenance is already running.",
            },
            status=202,
        )

    worker = threading.Thread(
        target=_run_scheduled_jobs_background,
        args=(token,),
        name="inprofic-scheduled-jobs",
        daemon=True,
    )
    try:
        worker.start()
    except Exception as exc:
        logger.exception("Could not start scheduled-job background worker")
        finish_scheduled_job_lease(
            token,
            status=ScheduledJobLease.STATUS_FAILED,
            error=str(exc),
        )
        return JsonResponse({"status": "error"}, status=500)

    return JsonResponse(
        {
            "status": "accepted",
            "detail": "Scheduled maintenance started in the background.",
        },
        status=202,
    )


@csrf_exempt
@require_POST
def dispatch_web_push(request):
    """Retry the durable Web Push outbox without running unrelated jobs."""
    expected = settings.CRON_SECRET
    scheme, separator, supplied = request.headers.get("Authorization", "").partition(" ")
    authorized = (
        expected and separator and scheme.lower() == "bearer"
        and secrets.compare_digest(supplied.strip(), expected)
    )
    if not authorized:
        return JsonResponse({"detail": "Forbidden"}, status=403)
    try:
        from commerce.webpush import dispatch_pending_pushes
        result = dispatch_pending_pushes(notice_limit=40, delivery_limit=20)
    except Exception:
        logger.exception("Commerce Web Push retry failed")
        return JsonResponse({"status": "error"}, status=500)
    return JsonResponse({"status": "ok", **result})
