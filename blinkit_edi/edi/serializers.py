"""
Blinkit EDI - Serializers

Inbound serializers validate Blinkit's webhook payloads.
Outbound serializers build payloads we send to Blinkit.
"""
from rest_framework import serializers
from .models import (
    PurchaseOrder, PurchaseOrderItem, POAmendment, POAmendmentItem,
    ASNSubmission, ASNItem,
)


# =============================================================================
# INBOUND: PO Creation (Blinkit → Us)
# =============================================================================

class TaxDetailsSerializer(serializers.Serializer):
    cgst_percentage = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    sgst_percentage = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    igst_percentage = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    cess_percentage = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    additional_cess_value = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )


class UOMSerializer(serializers.Serializer):
    unit = serializers.CharField()
    value = serializers.DecimalField(max_digits=10, decimal_places=2)


class CratesConfigSerializer(serializers.Serializer):
    crates_ordered = serializers.IntegerField(required=False)
    crate_size = serializers.IntegerField(required=False)


class POItemDataSerializer(serializers.Serializer):
    """Validates individual items in the PO creation payload."""
    item_id = serializers.IntegerField()
    sku_code = serializers.CharField(required=False, allow_blank=True, default="")
    line_number = serializers.IntegerField(default=0)
    units_ordered = serializers.IntegerField()
    landing_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    basic_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    tax_details = TaxDetailsSerializer(required=False)
    crates_config = CratesConfigSerializer(required=False)
    name = serializers.CharField()
    mrp = serializers.DecimalField(max_digits=10, decimal_places=2)
    upc = serializers.CharField()
    uom = UOMSerializer()


class AddressSerializer(serializers.Serializer):
    line1 = serializers.CharField(required=False, default="")
    line2 = serializers.CharField(required=False, default="")
    city = serializers.CharField(required=False, default="")
    state = serializers.CharField(required=False, default="")
    postal_code = serializers.CharField(required=False, default="")
    country = serializers.CharField(required=False, default="India")


class ContactDetailSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, default="")
    phone = serializers.CharField(required=False, default="")
    email = serializers.EmailField(required=False, default="")


class BuyerDetailsSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, default="")
    gstin = serializers.CharField()
    destination_address = AddressSerializer(required=False)
    registered_address = AddressSerializer(required=False)
    contact_details = ContactDetailSerializer(many=True, required=False)


class SupplierDetailsSerializer(serializers.Serializer):
    id = serializers.CharField(required=False, default="")
    name = serializers.CharField(required=False, default="")
    gstin = serializers.CharField(required=False, default="")
    pan = serializers.CharField(required=False, default="")
    shipping_address = AddressSerializer(required=False)
    registered_address = AddressSerializer(required=False)
    contact_details = ContactDetailSerializer(many=True, required=False)


class VehicleDetailsSerializer(serializers.Serializer):
    license_number = serializers.CharField(required=False, default="")


class PODetailsSerializer(serializers.Serializer):
    po_number = serializers.CharField()
    outlet_id = serializers.IntegerField(required=False)
    issue_date = serializers.DateTimeField(required=False, allow_null=True)
    expiry_date = serializers.DateTimeField(required=False, allow_null=True)
    delivery_date = serializers.DateTimeField(required=False, allow_null=True)
    vehicle_details = VehicleDetailsSerializer(required=False)
    buyer_details = BuyerDetailsSerializer()
    supplier_details = SupplierDetailsSerializer()
    item_data = POItemDataSerializer(many=True)
    total_sku = serializers.IntegerField()
    total_qty = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    custom_attributes = serializers.ListField(required=False, default=list)


class POCreationInboundSerializer(serializers.Serializer):
    """
    Top-level serializer for PO_CREATION webhook from Blinkit.
    Validates the full payload structure.
    """
    type = serializers.CharField()  # PO_CREATION
    po_number = serializers.CharField()
    tenant = serializers.ChoiceField(choices=["BLINKIT", "HYPERPURE"])
    details = PODetailsSerializer()

    def validate_type(self, value):
        if value != "PO_CREATION":
            raise serializers.ValidationError(
                f"Expected type PO_CREATION, got {value}"
            )
        return value

    def validate(self, data):
        # Ensure top-level po_number matches details.po_number
        if data["po_number"] != data["details"]["po_number"]:
            raise serializers.ValidationError(
                "Top-level po_number must match details.po_number"
            )
        return data


# =============================================================================
# INBOUND: PO Amendment (Blinkit → Us)
# =============================================================================

class AmendmentUOMSerializer(serializers.Serializer):
    type = serializers.CharField()  # STANDARD or NON_STANDARD
    value = serializers.CharField()
    unit = serializers.CharField()


class AmendmentVariantSerializer(serializers.Serializer):
    upc = serializers.CharField()
    mrp = serializers.DecimalField(max_digits=10, decimal_places=2)
    uom = AmendmentUOMSerializer()
    po_numbers = serializers.ListField(child=serializers.CharField())


class AmendmentItemSerializer(serializers.Serializer):
    item_id = serializers.CharField()
    variants = AmendmentVariantSerializer(many=True)


class POAmendmentInboundSerializer(serializers.Serializer):
    """Top-level serializer for PO Amendment webhook."""
    request_data = AmendmentItemSerializer(many=True)


# =============================================================================
# OUTBOUND: PO Acknowledgement (Us → Blinkit)
# =============================================================================

class POAckErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    field_name = serializers.CharField(required=False, default="")
    message = serializers.CharField()
    description = serializers.CharField(required=False, default="")
    error_params = serializers.DictField(required=False, default=dict)


class POAckWarningSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    description = serializers.CharField(required=False, default="")


class POAckDataSerializer(serializers.Serializer):
    po_status = serializers.ChoiceField(
        choices=["processing", "accepted", "partially_accepted", "rejected"]
    )
    po_number = serializers.CharField()
    errors = POAckErrorSerializer(many=True, required=False, default=list)
    warnings = POAckWarningSerializer(many=True, required=False, default=list)


class POAckOutboundSerializer(serializers.Serializer):
    """Payload we send to Blinkit's PO Acknowledgement endpoint."""
    success = serializers.BooleanField()
    message = serializers.CharField()
    timestamp = serializers.DateTimeField()
    data = POAckDataSerializer()


# =============================================================================
# OUTBOUND: ASN/Invoice (Us → Blinkit)
# =============================================================================

class ASNItemTaxSerializer(serializers.Serializer):
    cgst_percentage = serializers.DecimalField(max_digits=5, decimal_places=2)
    sgst_percentage = serializers.DecimalField(max_digits=5, decimal_places=2)
    igst_percentage = serializers.DecimalField(max_digits=5, decimal_places=2)
    ugst_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, default=0)
    cess_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, default=0)
    additional_cess_value = serializers.DecimalField(max_digits=10, decimal_places=2, default=0)


class ASNGSTSummarySerializer(serializers.Serializer):
    gst_type = serializers.ChoiceField(
        choices=["CGST", "SGST", "IGST", "CESS", "AdditionalCESS"]
    )
    gst_percentage = serializers.DecimalField(max_digits=5, decimal_places=2)
    gst_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    taxable_value = serializers.CharField()  # Blinkit expects stringified number


class CaseConfigSerializer(serializers.Serializer):
    level = serializers.CharField()  # outer_case, inner_case
    type = serializers.CharField()   # CRATE, PACKETS
    value = serializers.IntegerField()


class ASNItemOutboundSerializer(serializers.Serializer):
    """Single item in the outbound ASN payload."""
    item_id = serializers.CharField()
    sku_code = serializers.CharField(required=False, default="")
    batch_number = serializers.CharField()
    sku_description = serializers.CharField()
    upc = serializers.CharField()
    quantity = serializers.IntegerField()
    mrp = serializers.DecimalField(max_digits=10, decimal_places=2)
    hsn_code = serializers.CharField(required=False, default="")
    tax_distribution = ASNItemTaxSerializer()
    unit_discount_amount = serializers.CharField(default="0")
    unit_discount_percentage = serializers.CharField(default="0")
    unit_basic_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    unit_landing_price = serializers.CharField()
    expiry_date = serializers.DateField(format="%Y-%m-%d", required=False)
    mfg_date = serializers.DateField(format="%Y-%m-%d", required=False)
    uom = UOMSerializer()
    no_of_packages = serializers.CharField(default="0")
    code_category = serializers.CharField(required=False, default="")
    codes = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    case_configuration = CaseConfigSerializer(many=True, required=False, default=list)


class ASNSupplierAddressSerializer(serializers.Serializer):
    address_line_1 = serializers.CharField()
    address_line_2 = serializers.CharField(required=False, default="")
    city = serializers.CharField()
    country = serializers.CharField(default="India")
    phone = serializers.CharField(required=False, default="")
    postal_code = serializers.CharField()
    state = serializers.CharField()


class ASNSupplierSerializer(serializers.Serializer):
    name = serializers.CharField()
    gstin = serializers.CharField()
    supplier_address = ASNSupplierAddressSerializer()


class ASNShipmentSerializer(serializers.Serializer):
    e_way_bill_number = serializers.CharField(required=False, default="")
    delivery_type = serializers.ChoiceField(choices=["COURIER", "SELF"])
    delivery_partner = serializers.CharField(required=False, default="")
    delivery_tracking_code = serializers.CharField(required=False, default="")


class ASNOutboundSerializer(serializers.Serializer):
    """Full ASN payload we POST to Blinkit's /webhook/public/v1/asn."""
    po_number = serializers.CharField()
    invoice_number = serializers.CharField()
    invoice_date = serializers.DateField(format="%Y-%m-%d")
    delivery_date = serializers.DateField(format="%Y-%m-%d")
    total_additional_cess_value = serializers.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_distribution = ASNGSTSummarySerializer(many=True)
    basic_price = serializers.CharField()  # Stringified number per Blinkit spec
    landing_price = serializers.CharField()
    box_count = serializers.CharField(required=False, default="0")
    quantity = serializers.CharField()
    case_config = serializers.IntegerField(required=False)
    item_count = serializers.CharField()
    po_status = serializers.CharField(required=False, default="")
    supplier_details = ASNSupplierSerializer()
    buyer_details = serializers.DictField()  # Just { "gstin": "..." }
    shipment_details = ASNShipmentSerializer()
    items = ASNItemOutboundSerializer(many=True)


# =============================================================================
# RESPONSE: PO Creation Ack (immediate sync response)
# =============================================================================

class POCreationResponseSerializer(serializers.Serializer):
    """Response we return immediately to Blinkit's PO webhook."""
    success = serializers.BooleanField()
    message = serializers.CharField()
    timestamp = serializers.DateTimeField()
    data = POAckDataSerializer()
