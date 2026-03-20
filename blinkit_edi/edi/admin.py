"""
Blinkit EDI - Admin Configuration

Provides a quick monitoring dashboard via Django Admin.
"""
from django.contrib import admin
from .models import (
    PurchaseOrder, PurchaseOrderItem, POAmendment, POAmendmentItem,
    ASNSubmission, ASNItem, EDIAuditLog,
)


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 0
    readonly_fields = [
        "item_id", "sku_code", "name", "upc", "units_ordered",
        "mrp", "basic_price", "landing_price", "uom_unit", "uom_value",
        "is_valid", "validation_errors",
    ]
    can_delete = False


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = [
        "po_number", "tenant", "status", "total_sku", "total_qty",
        "total_amount", "delivery_date", "ack_sent_at", "created_at",
    ]
    list_filter = ["status", "tenant", "created_at"]
    search_fields = ["po_number", "buyer_gstin", "supplier_gstin"]
    readonly_fields = ["id", "raw_payload", "ack_response", "created_at", "updated_at"]
    inlines = [PurchaseOrderItemInline]
    date_hierarchy = "created_at"

    fieldsets = (
        ("Core", {
            "fields": ("id", "po_number", "tenant", "status"),
        }),
        ("Dates", {
            "fields": ("issue_date", "expiry_date", "delivery_date"),
        }),
        ("Buyer", {
            "fields": ("buyer_name", "buyer_gstin", "buyer_destination_address", "buyer_contact_details"),
            "classes": ("collapse",),
        }),
        ("Supplier", {
            "fields": ("supplier_id", "supplier_name", "supplier_gstin", "supplier_pan"),
            "classes": ("collapse",),
        }),
        ("Aggregates", {
            "fields": ("total_sku", "total_qty", "total_amount", "outlet_id"),
        }),
        ("Acknowledgement", {
            "fields": ("ack_sent_at", "ack_response"),
            "classes": ("collapse",),
        }),
        ("Debug", {
            "fields": ("raw_payload", "created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )


class POAmendmentItemInline(admin.TabularInline):
    model = POAmendmentItem
    extra = 0
    readonly_fields = ["item_id", "upc", "mrp", "uom_type", "uom_value", "uom_unit", "po_numbers", "previous_values"]
    can_delete = False


@admin.register(POAmendment)
class POAmendmentAdmin(admin.ModelAdmin):
    list_display = ["id", "status", "po_numbers", "created_at"]
    list_filter = ["status", "created_at"]
    readonly_fields = ["id", "raw_payload", "response_data", "created_at", "updated_at"]
    inlines = [POAmendmentItemInline]


class ASNItemInline(admin.TabularInline):
    model = ASNItem
    extra = 0
    readonly_fields = [
        "item_id", "sku_code", "batch_number", "sku_description", "upc",
        "quantity", "mrp", "hsn_code", "unit_basic_price", "unit_landing_price",
    ]
    can_delete = False


@admin.register(ASNSubmission)
class ASNSubmissionAdmin(admin.ModelAdmin):
    list_display = [
        "invoice_number", "po_number", "status", "item_count",
        "quantity", "asn_id", "submitted_at", "retry_count", "created_at",
    ]
    list_filter = ["status", "delivery_type", "created_at"]
    search_fields = ["po_number", "invoice_number", "asn_id"]
    readonly_fields = [
        "id", "submitted_payload", "blinkit_response",
        "submitted_at", "created_at", "updated_at",
    ]
    inlines = [ASNItemInline]
    date_hierarchy = "created_at"

    actions = ["resubmit_asn"]

    @admin.action(description="Re-submit selected ASNs to Blinkit")
    def resubmit_asn(self, request, queryset):
        from .tasks import submit_asn_to_blinkit
        count = 0
        for asn in queryset.filter(status__in=["DRAFT", "FAILED", "REJECTED"]):
            submit_asn_to_blinkit.delay(str(asn.id))
            count += 1
        self.message_user(request, f"Queued {count} ASN(s) for re-submission.")


@admin.register(EDIAuditLog)
class EDIAuditLogAdmin(admin.ModelAdmin):
    list_display = [
        "event_type", "direction", "po_number", "invoice_number",
        "is_success", "response_status", "response_time_ms", "created_at",
    ]
    list_filter = ["direction", "event_type", "is_success", "created_at"]
    search_fields = ["po_number", "invoice_number", "error_message"]
    readonly_fields = [
        "id", "request_headers", "request_body", "response_body",
        "error_message", "created_at",
    ]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
