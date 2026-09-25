from django.core.management.base import BaseCommand

from accounts.mailing import dispatch_queued_platform_mail


class Command(BaseCommand):
    help = "Dispatch queued INPROFIC platform mailing campaigns in bounded batches."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Maximum recipients to attempt in this run (1-1000).",
        )

    def handle(self, *args, **options):
        result = dispatch_queued_platform_mail(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(str(result)))
