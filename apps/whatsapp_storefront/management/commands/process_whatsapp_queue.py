from django.core.management.base import BaseCommand

from whatsapp_storefront.services import process_whatsapp_queue


class Command(BaseCommand):
    help = "Process persisted WhatsApp webhooks and outbound messages."

    def handle(self, *args, **options):
        count = process_whatsapp_queue()
        self.stdout.write(self.style.SUCCESS(f"Processed {count} WhatsApp queue item(s)."))
