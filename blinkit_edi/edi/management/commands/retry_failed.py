"""
Management Command: Retry failed ASN submissions and PO acknowledgements.

Replaces Celery Beat periodic tasks. Run via cron or Windows Task Scheduler.

Usage:
    python manage.py retry_failed              # Retry both ASNs and acks
    python manage.py retry_failed --asn-only   # Retry failed ASNs only
    python manage.py retry_failed --ack-only   # Retry failed PO acks only

Schedule (recommended):
    # Linux cron — every 10 minutes
    */10 * * * * cd /path/to/blinkit-edi && /path/to/venv/bin/python manage.py retry_failed

    # Windows Task Scheduler — every 10 minutes
    python manage.py retry_failed
"""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger("blinkit_edi")


class Command(BaseCommand):
    help = "Retry failed ASN submissions and PO acknowledgements"

    def add_arguments(self, parser):
        parser.add_argument("--asn-only", action="store_true", help="Retry failed ASNs only")
        parser.add_argument("--ack-only", action="store_true", help="Retry failed PO acks only")

    def handle(self, *args, **options):
        asn_only = options["asn_only"]
        ack_only = options["ack_only"]
        both = not asn_only and not ack_only

        if both or asn_only:
            self._retry_failed_asns()

        if both or ack_only:
            self._retry_failed_acks()

    def _retry_failed_asns(self):
        from blinkit_edi.edi.models import ASNSubmission
        from blinkit_edi.edi.tasks import _submit_asn_sync

        max_retries = settings.BLINKIT_EDI["MAX_RETRIES"]
        failed_asns = ASNSubmission.objects.filter(
            status=ASNSubmission.Status.FAILED,
            retry_count__lt=max_retries,
        )

        count = 0
        for asn in failed_asns:
            self.stdout.write(f"  Retrying ASN {asn.invoice_number} for PO {asn.po_number}...")
            _submit_asn_sync(str(asn.id))
            count += 1

        if count:
            self.stdout.write(self.style.SUCCESS(f"Retried {count} failed ASN(s)"))
        else:
            self.stdout.write("No failed ASNs to retry")

    def _retry_failed_acks(self):
        from blinkit_edi.edi.models import PurchaseOrder
        from blinkit_edi.edi.tasks import _send_po_ack_sync

        failed_pos = PurchaseOrder.objects.filter(
            status=PurchaseOrder.Status.ACK_FAILED,
        )

        count = 0
        for po in failed_pos:
            self.stdout.write(f"  Retrying ack for PO {po.po_number}...")
            _send_po_ack_sync(str(po.id))
            count += 1

        if count:
            self.stdout.write(self.style.SUCCESS(f"Retried {count} failed PO ack(s)"))
        else:
            self.stdout.write("No failed PO acks to retry")
