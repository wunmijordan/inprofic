from django.core.management.base import BaseCommand

from core.jobs import acquire_scheduled_job_lease, finish_scheduled_job_lease, run_all_jobs
from core.models import ScheduledJobLease


class Command(BaseCommand):
    help = "Run every idempotent INPROFIC scheduled maintenance job."

    def handle(self, *args, **options):
        token = acquire_scheduled_job_lease()
        if token is None:
            self.stdout.write(self.style.WARNING("Scheduled jobs are already running; skipped."))
            return

        try:
            completed = run_all_jobs(stdout=self.stdout)
        except Exception as exc:
            finish_scheduled_job_lease(
                token,
                status=ScheduledJobLease.STATUS_FAILED,
                error=str(exc),
            )
            raise

        finish_scheduled_job_lease(token, status=ScheduledJobLease.STATUS_SUCCEEDED)
        self.stdout.write(self.style.SUCCESS(f"Completed {len(completed)} scheduled job(s)."))
