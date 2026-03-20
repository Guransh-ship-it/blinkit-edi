"""
Management Command: Import ASN data from Google Sheets

This bridges the current process (invoice data in Google Sheets)
to the new EDI system. Run manually or schedule via cron.

Usage:
    python manage.py import_asn_from_sheets --sheet-id <GOOGLE_SHEET_ID>
    python manage.py import_asn_from_sheets --sheet-id <ID> --submit  # Auto-submit to Blinkit

Expects the Google Sheet to have these columns (first row = headers):
    po_number, invoice_number, invoice_date, delivery_date,
    item_id, sku_code, batch_number, sku_description, upc,
    quantity, mrp, hsn_code, unit_basic_price, unit_landing_price,
    cgst_pct, sgst_pct, igst_pct, expiry_date, mfg_date,
    uom_unit, uom_value, delivery_type, delivery_partner, tracking_code

Configure Google Sheets API credentials via:
    GOOGLE_SERVICE_ACCOUNT_JSON env var (path to service account JSON)
"""
import json
import logging
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger("blinkit_edi")

# Jivo Wellness supplier defaults - update these
JIVO_SUPPLIER = {
    "name": "Jivo Wellness Pvt Ltd",
    "gstin": "",  # TODO: Set your GSTIN
    "supplier_address": {
        "address_line_1": "",  # TODO: Set address
        "address_line_2": "",
        "city": "",
        "country": "India",
        "phone": "",
        "postal_code": "",
        "state": "",
    },
}


class Command(BaseCommand):
    help = "Import ASN/invoice data from Google Sheets and create ASN records"

    def add_arguments(self, parser):
        parser.add_argument("--sheet-id", required=True, help="Google Sheet ID")
        parser.add_argument("--sheet-name", default="Sheet1", help="Tab name")
        parser.add_argument("--submit", action="store_true", help="Auto-submit to Blinkit after creation")
        parser.add_argument("--dry-run", action="store_true", help="Preview without creating records")

    def handle(self, *args, **options):
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError:
            raise CommandError(
                "Install gspread and google-auth: pip install gspread google-auth"
            )

        import os
        creds_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        if not creds_path:
            raise CommandError("Set GOOGLE_SERVICE_ACCOUNT_JSON env var")

        # Auth with Google Sheets
        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        gc = gspread.authorize(creds)

        sheet = gc.open_by_key(options["sheet_id"])
        worksheet = sheet.worksheet(options["sheet_name"])
        records = worksheet.get_all_records()

        self.stdout.write(f"Found {len(records)} rows in sheet")

        if not records:
            self.stdout.write("No data found")
            return

        # Group rows by PO number + invoice number (one ASN per PO+invoice combo)
        asn_groups = {}
        for row in records:
            key = (row.get("po_number", ""), row.get("invoice_number", ""))
            if key not in asn_groups:
                asn_groups[key] = {
                    "header": row,
                    "items": [],
                }
            asn_groups[key]["items"].append(row)

        self.stdout.write(f"Grouped into {len(asn_groups)} ASN(s)")

        if options["dry_run"]:
            for (po, inv), group in asn_groups.items():
                self.stdout.write(
                    f"  PO: {po} | Invoice: {inv} | Items: {len(group['items'])}"
                )
            return

        from blinkit_edi.edi.models import ASNSubmission, ASNItem
        from blinkit_edi.edi.tasks import _submit_asn_sync

        created = 0
        for (po_number, invoice_number), group in asn_groups.items():
            header = group["header"]
            items = group["items"]

            # Check for existing
            if ASNSubmission.objects.filter(
                po_number=po_number, invoice_number=invoice_number
            ).exists():
                self.stdout.write(
                    self.style.WARNING(f"  SKIP: ASN already exists for PO {po_number} / INV {invoice_number}")
                )
                continue

            total_qty = sum(int(i.get("quantity", 0)) for i in items)
            total_basic = sum(
                Decimal(str(i.get("unit_basic_price", 0))) * int(i.get("quantity", 0))
                for i in items
            )

            asn = ASNSubmission.objects.create(
                po_number=po_number,
                invoice_number=invoice_number,
                invoice_date=self._parse_date(header.get("invoice_date", "")),
                delivery_date=self._parse_date(header.get("delivery_date", "")),
                basic_price=total_basic,
                landing_price=0,
                quantity=total_qty,
                item_count=len(items),
                po_status="PO_FULFILLED",
                supplier_name=JIVO_SUPPLIER["name"],
                supplier_gstin=JIVO_SUPPLIER["gstin"],
                supplier_address=JIVO_SUPPLIER["supplier_address"],
                buyer_gstin=header.get("buyer_gstin", ""),
                delivery_type=header.get("delivery_type", "SELF"),
                delivery_partner=header.get("delivery_partner", ""),
                delivery_tracking_code=header.get("tracking_code", ""),
                status=ASNSubmission.Status.DRAFT,
            )

            for item_row in items:
                ASNItem.objects.create(
                    asn=asn,
                    item_id=str(item_row.get("item_id", "")),
                    sku_code=item_row.get("sku_code", ""),
                    batch_number=item_row.get("batch_number", ""),
                    sku_description=item_row.get("sku_description", ""),
                    upc=item_row.get("upc", ""),
                    quantity=int(item_row.get("quantity", 0)),
                    mrp=Decimal(str(item_row.get("mrp", 0))),
                    hsn_code=item_row.get("hsn_code", ""),
                    cgst_percentage=Decimal(str(item_row.get("cgst_pct", 0))),
                    sgst_percentage=Decimal(str(item_row.get("sgst_pct", 0))),
                    igst_percentage=Decimal(str(item_row.get("igst_pct", 0))),
                    unit_basic_price=Decimal(str(item_row.get("unit_basic_price", 0))),
                    unit_landing_price=Decimal(str(item_row.get("unit_landing_price", 0))),
                    expiry_date=self._parse_date(item_row.get("expiry_date", "")),
                    mfg_date=self._parse_date(item_row.get("mfg_date", "")),
                    uom_unit=item_row.get("uom_unit", "ml"),
                    uom_value=Decimal(str(item_row.get("uom_value", 0))),
                )

            created += 1
            self.stdout.write(
                self.style.SUCCESS(f"  CREATED: ASN {invoice_number} for PO {po_number} ({len(items)} items)")
            )

            if options["submit"]:
                _submit_asn_sync(str(asn.id))
                self.stdout.write(f"    → Submitted to Blinkit")

        self.stdout.write(self.style.SUCCESS(f"\nDone. Created {created} ASN(s)."))

    @staticmethod
    def _parse_date(val):
        if not val:
            return None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(str(val), fmt).date()
            except ValueError:
                continue
        return None
