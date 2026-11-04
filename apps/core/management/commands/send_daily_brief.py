from django.core.management.base import BaseCommand

from core.daily_brief import send_due_briefs


class Command(BaseCommand):
    help = "Email the morning business brief to members who opted in (once per day each)."

    def handle(self, *args, **options):
        self.stdout.write(f"Daily briefs sent: {send_due_briefs()}")
