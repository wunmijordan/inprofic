from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from assistant.models import AssistantConversation


class Command(BaseCommand):
    help = "Delete staff-assistant conversations not touched within the retention window."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=settings.ASSISTANT_RETENTION_DAYS)

    def handle(self, *args, **options):
        days = max(1, options["days"])
        cutoff = timezone.now() - timedelta(days=days)
        deleted, _ = AssistantConversation.raw_objects.filter(updated_at__lt=cutoff).delete()
        self.stdout.write(f"Deleted {deleted} assistant record(s) older than {days} days.")
