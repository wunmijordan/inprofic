from django.core.management.base import BaseCommand

from commerce.webpush import dispatch_pending_pushes


class Command(BaseCommand):
    help = "Enqueue and dispatch pending commerce Web Push notifications."

    def handle(self, *args, **options):
        result = dispatch_pending_pushes(notice_limit=100, delivery_limit=200)
        self.stdout.write(self.style.SUCCESS(
            "Web Push: configured={configured} queued={queued} requeued={requeued} sent={sent} failed={failed} expired={expired}".format(**result)
        ))
