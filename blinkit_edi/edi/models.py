"""
Blinkit EDI - Database Models

Four main entities:
1. PurchaseOrder       - Inbound PO from Blinkit
2. PurchaseOrderItem   - Line items within a PO
3. POAmendment         - Inbound PO amendments
4. ASNSubmission       - Outbound ASN/Invoice pushed to Blinkit

Plus:
- EDIAuditLog          - Every inbound/outbound API call logged
"""
import uuid
from django.db import models
from django.utils import timezone


class TimestampMixin(models.Model):
    """Abstract base with created/updated timestamps."""
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# =============================================================================
# PURCHASE ORDER (Inbound from Blinkit)
# =============================================================================
class PurchaseOrder(TimestampMixin):
    """
    Stores POs received from Blinkit's PO Creation webhook.
    Maps to: POVMS-Purchase_Order_Creation_API_Contracts
    """

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"           # Just landed
        PROCESSING = "PROCESSING", "Processing"     # Being validated
        ACCEPTED = "ACCEPTED", "Accepted"           # Ack sent: accepted
        PARTIALLY_ACCEPTED = "PARTIALLY_ACCEPTED", "Partially Accepted"
        REJECTED = "REJECTED", "Rejected"           # Ack sent: rejected
        ACK_SENT = "ACK_SENT", "Ack Sent"          # Async ack delivered
        ACK_FAILED = "ACK_FAILED", "Ack Failed"    # Async ack delivery failed

    class Tenant(models.TextChoices):
        BLINKIT = "BLINKIT", "Blinkit"
        HYPERPURE = "HYPERPURE", "Hyperpure"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Core PO fields
    po_number = models.CharField(max_length=50, unique=True, db_index=True)
    tenant = models.CharField(max_length=20, choices=Tenant.choices)
    status = models.CharField(
        max_length=25, choices=Status.choices, default=Status.RECEIVED, db_index=True
    )

    # Dates
    issue_date = models.DateTimeField(null=True, blank=True)
    expiry_date = models.DateTimeField(null=True, blank=True)
    delivery_date = models.DateTimeField(null=True, blank=True)

    # Buyer details
    buyer_name = models.CharField(max_length=200, blank=True)
    buyer_gstin = models.CharField(max_length=15)
    buyer_destination_address = models.JSONField(default=dict, blank=True)
    buyer_registered_address = models.JSONField(default=dict, blank=True)
    buyer_contact_details = models.JSONField(default=list, blank=True)

    # Supplier details (Jivo's info as seen by Blinkit)
    supplier_id = models.CharField(max_length=50, blank=True)
    supplier_name = models.CharField(max_length=200, blank=True)
    supplier_gstin = models.CharField(max_length=15, blank=True)
    supplier_pan = models.CharField(max_length=10, blank=True)
    supplier_shipping_address = models.JSONField(default=dict, blank=True)
    supplier_registered_address = models.JSONField(default=dict, blank=True)
    supplier_contact_details = models.JSONField(default=list, blank=True)

    # Vehicle
    vehicle_license_number = models.CharField(max_length=50, blank=True)

    # Aggregates
    total_sku = models.IntegerField(default=0)
    total_qty = models.IntegerField(default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    outlet_id = models.CharField(max_length=50, blank=True)

    # Custom attributes from Blinkit
    custom_attributes = models.JSONField(default=list, blank=True)

    # Raw payload for audit/debugging
    raw_payload = models.JSONField(default=dict)

    # Ack tracking
    ack_sent_at = models.DateTimeField(null=True, blank=True)
    ack_response = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "edi_purchase_order"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["po_number", "tenant"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"PO {self.po_number} [{self.tenant}] - {self.status}"


class PurchaseOrderItem(TimestampMixin):
    """Line items within a Purchase Order."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="items"
    )

    # Item identity
    item_id = models.CharField(max_length=50, db_index=True)  # Blinkit's item ID
    sku_code = models.CharField(max_length=100, blank=True)
    line_number = models.IntegerField(default=0)
    name = models.CharField(max_length=300)
    upc = models.CharField(max_length=50)  # Barcode / GTIN

    # Pricing
    units_ordered = models.IntegerField()
    landing_price = models.DecimalField(max_digits=10, decimal_places=2)
    basic_price = models.DecimalField(max_digits=10, decimal_places=2)
    mrp = models.DecimalField(max_digits=10, decimal_places=2)

    # Tax
    cgst_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    sgst_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    igst_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    cess_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    additional_cess_value = models.DecimalField(max_digits=10, decimal_places=2, null=True)

    # UOM
    uom_unit = models.CharField(max_length=20)  # ml, kg, piece, etc.
    uom_value = models.DecimalField(max_digits=10, decimal_places=2)

    # Crates config
    crates_ordered = models.IntegerField(null=True, blank=True)
    crate_size = models.IntegerField(null=True, blank=True)

    # Validation status
    is_valid = models.BooleanField(default=True)
    validation_errors = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "edi_purchase_order_item"
        ordering = ["line_number"]
        unique_together = [("purchase_order", "item_id")]

    def __str__(self):
        return f"Item {self.item_id} - {self.name} (x{self.units_ordered})"


# =============================================================================
# PO AMENDMENT (Inbound from Blinkit)
# =============================================================================
class POAmendment(TimestampMixin):
    """
    Stores PO amendments received from Blinkit.
    Maps to: POVMS-PO_Amendment_Contracts
    """

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        APPLIED = "APPLIED", "Applied"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RECEIVED
    )

    # Links to affected POs
    po_numbers = models.JSONField(default=list)  # List of PO numbers being amended

    # Amendment data
    request_data = models.JSONField(default=list)  # Full amendment payload
    raw_payload = models.JSONField(default=dict)

    # Response from Blinkit after we process
    response_data = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "edi_po_amendment"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Amendment {self.id} - POs: {self.po_numbers}"


class POAmendmentItem(TimestampMixin):
    """Individual item changes within an amendment."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    amendment = models.ForeignKey(
        POAmendment, on_delete=models.CASCADE, related_name="items"
    )

    item_id = models.CharField(max_length=50)
    upc = models.CharField(max_length=50)
    mrp = models.DecimalField(max_digits=10, decimal_places=2)
    uom_type = models.CharField(max_length=20)
    uom_value = models.CharField(max_length=20)
    uom_unit = models.CharField(max_length=20)
    po_numbers = models.JSONField(default=list)  # Which POs this item change affects

    # What changed (snapshot of before/after for audit)
    previous_values = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "edi_po_amendment_item"

    def __str__(self):
        return f"AmendItem {self.item_id} - MRP:{self.mrp}"


# =============================================================================
# ASN SUBMISSION (Outbound to Blinkit)
# =============================================================================
class ASNSubmission(TimestampMixin):
    """
    Outbound ASN/Invoice pushed to Blinkit.
    Maps to: POVMS-ASN_Sync_API_Contracts
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PENDING = "PENDING", "Pending"          # Queued for submission
        SUBMITTED = "SUBMITTED", "Submitted"    # Sent to Blinkit
        ACCEPTED = "ACCEPTED", "Accepted"
        PARTIALLY_ACCEPTED = "PARTIALLY_ACCEPTED", "Partially Accepted"
        REJECTED = "REJECTED", "Rejected"
        FAILED = "FAILED", "Failed"             # HTTP/network failure

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Link to the PO this ASN fulfills
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.SET_NULL, null=True, related_name="asn_submissions"
    )
    po_number = models.CharField(max_length=50, db_index=True)

    status = models.CharField(
        max_length=25, choices=Status.choices, default=Status.DRAFT
    )

    # Invoice details
    invoice_number = models.CharField(max_length=100, db_index=True)
    invoice_date = models.DateField()
    delivery_date = models.DateField()

    # Tax summary
    tax_distribution = models.JSONField(default=list)
    total_additional_cess_value = models.DecimalField(
        max_digits=10, decimal_places=2, default=0
    )

    # Pricing
    basic_price = models.DecimalField(max_digits=12, decimal_places=2)
    landing_price = models.DecimalField(max_digits=12, decimal_places=2, null=True)

    # Quantities
    box_count = models.IntegerField(default=0)
    quantity = models.IntegerField()
    case_config = models.IntegerField(null=True)
    item_count = models.IntegerField()

    # PO fulfillment status
    po_status = models.CharField(max_length=30, blank=True)  # PO_FULFILLED, PARTIALLY_FULFILLED

    # Supplier details
    supplier_name = models.CharField(max_length=200)
    supplier_gstin = models.CharField(max_length=15)
    supplier_address = models.JSONField(default=dict)

    # Buyer
    buyer_gstin = models.CharField(max_length=15)

    # Shipment
    e_way_bill_number = models.CharField(max_length=50, blank=True)
    delivery_type = models.CharField(max_length=20)  # COURIER, SELF
    delivery_partner = models.CharField(max_length=100, blank=True)
    delivery_tracking_code = models.CharField(max_length=100, blank=True)

    # Blinkit response
    asn_id = models.CharField(max_length=100, blank=True)  # Blinkit's ASN reference
    blinkit_response = models.JSONField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    retry_count = models.IntegerField(default=0)

    # Full payload sent (for audit)
    submitted_payload = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "edi_asn_submission"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["po_number", "invoice_number"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"ASN {self.invoice_number} → PO {self.po_number} [{self.status}]"


class ASNItem(TimestampMixin):
    """Line items within an ASN submission."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asn = models.ForeignKey(
        ASNSubmission, on_delete=models.CASCADE, related_name="items"
    )

    item_id = models.CharField(max_length=50)
    sku_code = models.CharField(max_length=100, blank=True)
    batch_number = models.CharField(max_length=100)
    sku_description = models.CharField(max_length=300)
    upc = models.CharField(max_length=50)
    quantity = models.IntegerField()
    mrp = models.DecimalField(max_digits=10, decimal_places=2)
    hsn_code = models.CharField(max_length=20, blank=True)

    # Tax
    cgst_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    sgst_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    igst_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    ugst_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    cess_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    additional_cess_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Pricing
    unit_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    unit_discount_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    unit_basic_price = models.DecimalField(max_digits=10, decimal_places=2)
    unit_landing_price = models.DecimalField(max_digits=10, decimal_places=2, null=True)

    # Dates
    expiry_date = models.DateField(null=True)
    mfg_date = models.DateField(null=True)
    shelf_life = models.IntegerField(null=True)  # days

    # UOM
    uom_unit = models.CharField(max_length=20)
    uom_value = models.DecimalField(max_digits=10, decimal_places=2)

    # Packaging
    no_of_packages = models.IntegerField(default=0)
    code_category = models.CharField(max_length=20, blank=True)  # QR, Barcode
    codes = models.JSONField(default=list)  # Traceability codes
    case_configuration = models.JSONField(default=list)  # outer_case, inner_case

    class Meta:
        db_table = "edi_asn_item"
        ordering = ["item_id"]

    def __str__(self):
        return f"ASNItem {self.item_id} - {self.sku_description} (x{self.quantity})"


# =============================================================================
# AUDIT LOG
# =============================================================================
class EDIAuditLog(TimestampMixin):
    """Logs every inbound and outbound API interaction."""

    class Direction(models.TextChoices):
        INBOUND = "INBOUND", "Inbound"    # Blinkit → Us
        OUTBOUND = "OUTBOUND", "Outbound"  # Us → Blinkit

    class EventType(models.TextChoices):
        PO_CREATION = "PO_CREATION", "PO Creation"
        PO_ACK = "PO_ACK", "PO Acknowledgement"
        PO_AMENDMENT = "PO_AMENDMENT", "PO Amendment"
        ASN_SYNC = "ASN_SYNC", "ASN Sync"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    direction = models.CharField(max_length=10, choices=Direction.choices)
    event_type = models.CharField(max_length=20, choices=EventType.choices)

    # Reference
    po_number = models.CharField(max_length=50, blank=True, db_index=True)
    invoice_number = models.CharField(max_length=100, blank=True)

    # HTTP details
    http_method = models.CharField(max_length=10, default="POST")
    endpoint = models.URLField(max_length=500, blank=True)
    request_headers = models.JSONField(default=dict)
    request_body = models.JSONField(default=dict)
    response_status = models.IntegerField(null=True)
    response_body = models.JSONField(null=True, blank=True)
    response_time_ms = models.IntegerField(null=True)  # Latency tracking

    # Error tracking
    error_message = models.TextField(blank=True)
    is_success = models.BooleanField(default=True)

    class Meta:
        db_table = "edi_audit_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event_type", "direction", "created_at"]),
            models.Index(fields=["po_number", "created_at"]),
        ]

    def __str__(self):
        return f"{self.direction} {self.event_type} - PO:{self.po_number} @ {self.created_at}"
