"""
Blinkit EDI - Service Layer

Business logic separated from views.
Handles: PO ingestion, validation, ASN payload building, and amendment processing.
"""
import logging
import time
from decimal import Decimal
from typing import Optional

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import (
    PurchaseOrder, PurchaseOrderItem, POAmendment, POAmendmentItem,
    ASNSubmission, ASNItem, EDIAuditLog,
)
from .authentication import get_outbound_headers, get_blinkit_endpoint

logger = logging.getLogger("blinkit_edi")


# =============================================================================
# PO CREATION SERVICE
# =============================================================================

class POCreationService:
    """Handles inbound PO creation from Blinkit."""

    @staticmethod
    @transaction.atomic
    def process_po(validated_data: dict, raw_payload: dict) -> PurchaseOrder:
        """
        Ingest a validated PO payload into the database.
        Returns the PurchaseOrder instance.
        """
        details = validated_data["details"]
        buyer = details.get("buyer_details", {})
        supplier = details.get("supplier_details", {})
        vehicle = details.get("vehicle_details", {})

        # Check for duplicate PO
        if PurchaseOrder.objects.filter(po_number=validated_data["po_number"]).exists():
            raise DuplicatePOError(validated_data["po_number"])

        po = PurchaseOrder.objects.create(
            po_number=validated_data["po_number"],
            tenant=validated_data["tenant"],
            status=PurchaseOrder.Status.RECEIVED,

            # Dates
            issue_date=details.get("issue_date"),
            expiry_date=details.get("expiry_date"),
            delivery_date=details.get("delivery_date"),

            # Buyer
            buyer_name=buyer.get("name", ""),
            buyer_gstin=buyer.get("gstin", ""),
            buyer_destination_address=buyer.get("destination_address", {}),
            buyer_registered_address=buyer.get("registered_address", {}),
            buyer_contact_details=buyer.get("contact_details", []),

            # Supplier
            supplier_id=supplier.get("id", ""),
            supplier_name=supplier.get("name", ""),
            supplier_gstin=supplier.get("gstin", ""),
            supplier_pan=supplier.get("pan", ""),
            supplier_shipping_address=supplier.get("shipping_address", {}),
            supplier_registered_address=supplier.get("registered_address", {}),
            supplier_contact_details=supplier.get("contact_details", []),

            # Vehicle
            vehicle_license_number=vehicle.get("license_number", ""),

            # Aggregates
            total_sku=details.get("total_sku", 0),
            total_qty=details.get("total_qty", 0),
            total_amount=details.get("total_amount", 0),
            outlet_id=str(details.get("outlet_id", "")),
            custom_attributes=details.get("custom_attributes", []),

            raw_payload=raw_payload,
        )

        # Create line items
        errors = []
        for item_data in details.get("item_data", []):
            try:
                POCreationService._create_item(po, item_data)
            except Exception as e:
                errors.append({
                    "item_id": str(item_data.get("item_id")),
                    "error": str(e),
                })
                logger.warning(f"PO {po.po_number}: Item {item_data.get('item_id')} error: {e}")

        # Determine status based on item processing
        if errors and len(errors) == len(details.get("item_data", [])):
            po.status = PurchaseOrder.Status.REJECTED
        elif errors:
            po.status = PurchaseOrder.Status.PARTIALLY_ACCEPTED
        else:
            po.status = PurchaseOrder.Status.ACCEPTED

        po.save()

        # Log audit
        EDIAuditLog.objects.create(
            direction=EDIAuditLog.Direction.INBOUND,
            event_type=EDIAuditLog.EventType.PO_CREATION,
            po_number=po.po_number,
            request_body=raw_payload,
            is_success=po.status != PurchaseOrder.Status.REJECTED,
        )

        return po

    @staticmethod
    def _create_item(po: PurchaseOrder, item_data: dict) -> PurchaseOrderItem:
        tax = item_data.get("tax_details", {})
        uom = item_data.get("uom", {})
        crates = item_data.get("crates_config", {})

        return PurchaseOrderItem.objects.create(
            purchase_order=po,
            item_id=str(item_data["item_id"]),
            sku_code=item_data.get("sku_code", ""),
            line_number=item_data.get("line_number", 0),
            name=item_data.get("name", ""),
            upc=item_data.get("upc", ""),
            units_ordered=item_data["units_ordered"],
            landing_price=item_data["landing_price"],
            basic_price=item_data["basic_price"],
            mrp=item_data["mrp"],
            cgst_percentage=tax.get("cgst_percentage"),
            sgst_percentage=tax.get("sgst_percentage"),
            igst_percentage=tax.get("igst_percentage"),
            cess_percentage=tax.get("cess_percentage"),
            additional_cess_value=tax.get("additional_cess_value"),
            uom_unit=uom.get("unit", ""),
            uom_value=uom.get("value", 0),
            crates_ordered=crates.get("crates_ordered"),
            crate_size=crates.get("crate_size"),
        )

    @staticmethod
    def build_ack_payload(po: PurchaseOrder, errors=None, warnings=None) -> dict:
        """Build the PO acknowledgement payload to send back to Blinkit."""
        status_map = {
            PurchaseOrder.Status.ACCEPTED: "accepted",
            PurchaseOrder.Status.PARTIALLY_ACCEPTED: "partially_accepted",
            PurchaseOrder.Status.REJECTED: "rejected",
            PurchaseOrder.Status.PROCESSING: "processing",
        }

        return {
            "success": po.status != PurchaseOrder.Status.REJECTED,
            "message": f"PO {po.po_number} {status_map.get(po.status, 'processing')}.",
            "timestamp": timezone.now().isoformat(),
            "data": {
                "po_status": status_map.get(po.status, "processing"),
                "po_number": po.po_number,
                "errors": errors or [],
                "warnings": warnings or [],
            },
        }


# =============================================================================
# PO AMENDMENT SERVICE
# =============================================================================

class POAmendmentService:
    """Handles inbound PO amendments from Blinkit."""

    @staticmethod
    @transaction.atomic
    def process_amendment(validated_data: dict, raw_payload: dict) -> POAmendment:
        """Process a PO amendment payload."""
        request_data = validated_data["request_data"]

        # Extract all affected PO numbers
        po_numbers = set()
        for item in request_data:
            for variant in item.get("variants", []):
                po_numbers.update(variant.get("po_numbers", []))

        amendment = POAmendment.objects.create(
            po_numbers=list(po_numbers),
            request_data=request_data,
            raw_payload=raw_payload,
            status=POAmendment.Status.RECEIVED,
        )

        updated_items = []
        for item_data in request_data:
            for variant in item_data.get("variants", []):
                amend_item = POAmendmentItem.objects.create(
                    amendment=amendment,
                    item_id=str(item_data["item_id"]),
                    upc=variant["upc"],
                    mrp=variant["mrp"],
                    uom_type=variant.get("uom", {}).get("type", ""),
                    uom_value=variant.get("uom", {}).get("value", ""),
                    uom_unit=variant.get("uom", {}).get("unit", ""),
                    po_numbers=variant.get("po_numbers", []),
                )

                # Apply amendment to existing PO items
                affected_pos = PurchaseOrder.objects.filter(
                    po_number__in=variant.get("po_numbers", [])
                )
                for po in affected_pos:
                    po_items = PurchaseOrderItem.objects.filter(
                        purchase_order=po,
                        item_id=str(item_data["item_id"]),
                    )
                    for po_item in po_items:
                        # Store previous values
                        amend_item.previous_values = {
                            "mrp": str(po_item.mrp),
                            "upc": po_item.upc,
                            "uom_unit": po_item.uom_unit,
                            "uom_value": str(po_item.uom_value),
                        }
                        amend_item.save()

                        # Apply changes
                        po_item.mrp = variant["mrp"]
                        po_item.upc = variant["upc"]
                        po_item.uom_unit = variant.get("uom", {}).get("unit", po_item.uom_unit)
                        po_item.uom_value = Decimal(
                            variant.get("uom", {}).get("value", str(po_item.uom_value))
                        )
                        po_item.save()

                updated_items.append({
                    "item_id": str(item_data["item_id"]),
                    "variants": [variant],
                })

        amendment.status = POAmendment.Status.APPLIED
        amendment.save()

        # Audit log
        EDIAuditLog.objects.create(
            direction=EDIAuditLog.Direction.INBOUND,
            event_type=EDIAuditLog.EventType.PO_AMENDMENT,
            po_number=", ".join(po_numbers),
            request_body=raw_payload,
            is_success=True,
        )

        return amendment

    @staticmethod
    def build_response(amendment: POAmendment) -> dict:
        """Build the response payload for amendment endpoint."""
        items_data = []
        for item in amendment.items.all():
            items_data.append({
                "item_id": item.item_id,
                "variants": [{
                    "upc": item.upc,
                    "mrp": float(item.mrp),
                    "uom": {
                        "type": item.uom_type,
                        "value": item.uom_value,
                        "unit": item.uom_unit,
                    } if item.uom_type else None,
                    "po_numbers": item.po_numbers,
                }],
            })

        return {
            "success": amendment.status == POAmendment.Status.APPLIED,
            "message": "Items updated successfully" if amendment.status == POAmendment.Status.APPLIED else "Amendment failed",
            "updated_items": items_data,
        }


# =============================================================================
# ASN SUBMISSION SERVICE (Outbound)
# =============================================================================

class ASNService:
    """Builds and submits ASN payloads to Blinkit."""

    @staticmethod
    def build_asn_payload(asn: ASNSubmission) -> dict:
        """
        Build the full ASN JSON payload from an ASNSubmission + ASNItems.
        This is what we POST to Blinkit's /webhook/public/v1/asn
        """
        items_payload = []
        for item in asn.items.all():
            items_payload.append({
                "item_id": item.item_id,
                "sku_code": item.sku_code,
                "batch_number": item.batch_number,
                "sku_description": item.sku_description,
                "upc": item.upc,
                "quantity": item.quantity,
                "mrp": float(item.mrp),
                "hsn_code": item.hsn_code,
                "tax_distribution": {
                    "cgst_percentage": float(item.cgst_percentage),
                    "sgst_percentage": float(item.sgst_percentage),
                    "igst_percentage": float(item.igst_percentage),
                    "ugst_percentage": float(item.ugst_percentage),
                    "cess_percentage": float(item.cess_percentage),
                    "additional_cess_value": float(item.additional_cess_value),
                },
                "unit_discount_amount": str(item.unit_discount_amount),
                "unit_discount_percentage": str(item.unit_discount_percentage),
                "unit_basic_price": float(item.unit_basic_price),
                "unit_landing_price": str(item.unit_landing_price),
                "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
                "mfg_date": item.mfg_date.isoformat() if item.mfg_date else None,
                "uom": {
                    "unit": item.uom_unit,
                    "value": float(item.uom_value),
                },
                "no_of_packages": str(item.no_of_packages),
                "code_category": item.code_category,
                "codes": item.codes,
                "case_configuration": item.case_configuration,
            })

        payload = {
            "po_number": asn.po_number,
            "invoice_number": asn.invoice_number,
            "invoice_date": asn.invoice_date.isoformat(),
            "delivery_date": asn.delivery_date.isoformat(),
            "total_additional_cess_value": float(asn.total_additional_cess_value),
            "tax_distribution": asn.tax_distribution,
            "basic_price": str(asn.basic_price),
            "landing_price": str(asn.landing_price) if asn.landing_price else "0",
            "box_count": str(asn.box_count),
            "quantity": str(asn.quantity),
            "case_config": asn.case_config,
            "item_count": str(asn.item_count),
            "po_status": asn.po_status,
            "supplier_details": {
                "name": asn.supplier_name,
                "gstin": asn.supplier_gstin,
                "supplier_address": asn.supplier_address,
            },
            "buyer_details": {
                "gstin": asn.buyer_gstin,
            },
            "shipment_details": {
                "e_way_bill_number": asn.e_way_bill_number,
                "delivery_type": asn.delivery_type,
                "delivery_partner": asn.delivery_partner,
                "delivery_tracking_code": asn.delivery_tracking_code,
            },
            "items": items_payload,
        }

        return payload

    @staticmethod
    def submit_asn(asn: ASNSubmission) -> dict:
        """
        Submit ASN to Blinkit's endpoint.
        Returns the response dict.
        """
        payload = ASNService.build_asn_payload(asn)
        endpoint = get_blinkit_endpoint("ASN_ENDPOINT")
        headers = get_outbound_headers()

        # Save what we're sending
        asn.submitted_payload = payload
        asn.status = ASNSubmission.Status.PENDING
        asn.save()

        start_time = time.time()
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=30,
            )
            elapsed_ms = int((time.time() - start_time) * 1000)

            response_data = response.json()

            # Update ASN with Blinkit's response
            asn.blinkit_response = response_data
            asn.submitted_at = timezone.now()

            if response.status_code < 300:
                sync_status = response_data.get("asn_sync_status", "")
                if sync_status == "ACCEPTED":
                    asn.status = ASNSubmission.Status.ACCEPTED
                elif sync_status == "PARTIALLY_ACCEPTED":
                    asn.status = ASNSubmission.Status.PARTIALLY_ACCEPTED
                elif sync_status == "REJECTED":
                    asn.status = ASNSubmission.Status.REJECTED
                else:
                    asn.status = ASNSubmission.Status.SUBMITTED

                asn.asn_id = response_data.get("asn_id", "")
            else:
                asn.status = ASNSubmission.Status.FAILED

            asn.save()

            # Audit
            EDIAuditLog.objects.create(
                direction=EDIAuditLog.Direction.OUTBOUND,
                event_type=EDIAuditLog.EventType.ASN_SYNC,
                po_number=asn.po_number,
                invoice_number=asn.invoice_number,
                endpoint=endpoint,
                request_body=payload,
                response_status=response.status_code,
                response_body=response_data,
                response_time_ms=elapsed_ms,
                is_success=response.status_code < 300,
            )

            return response_data

        except requests.exceptions.RequestException as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            asn.status = ASNSubmission.Status.FAILED
            asn.retry_count += 1
            asn.save()

            EDIAuditLog.objects.create(
                direction=EDIAuditLog.Direction.OUTBOUND,
                event_type=EDIAuditLog.EventType.ASN_SYNC,
                po_number=asn.po_number,
                invoice_number=asn.invoice_number,
                endpoint=endpoint,
                request_body=payload,
                response_time_ms=elapsed_ms,
                error_message=str(e),
                is_success=False,
            )

            raise ASNSubmissionError(f"Failed to submit ASN: {e}")


# =============================================================================
# PO ACKNOWLEDGEMENT SERVICE (Outbound - Async)
# =============================================================================

class POAckService:
    """Sends PO Acknowledgements to Blinkit asynchronously."""

    @staticmethod
    def send_ack(po: PurchaseOrder, errors=None, warnings=None) -> dict:
        """POST PO ack to Blinkit's ack endpoint."""
        payload = POCreationService.build_ack_payload(po, errors, warnings)
        endpoint = get_blinkit_endpoint("PO_ACK_ENDPOINT")
        headers = get_outbound_headers()

        start_time = time.time()
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=30,
            )
            elapsed_ms = int((time.time() - start_time) * 1000)
            response_data = response.json()

            po.ack_sent_at = timezone.now()
            po.ack_response = response_data
            po.status = PurchaseOrder.Status.ACK_SENT
            po.save()

            EDIAuditLog.objects.create(
                direction=EDIAuditLog.Direction.OUTBOUND,
                event_type=EDIAuditLog.EventType.PO_ACK,
                po_number=po.po_number,
                endpoint=endpoint,
                request_body=payload,
                response_status=response.status_code,
                response_body=response_data,
                response_time_ms=elapsed_ms,
                is_success=response.status_code < 300,
            )

            return response_data

        except requests.exceptions.RequestException as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            po.status = PurchaseOrder.Status.ACK_FAILED
            po.save()

            EDIAuditLog.objects.create(
                direction=EDIAuditLog.Direction.OUTBOUND,
                event_type=EDIAuditLog.EventType.PO_ACK,
                po_number=po.po_number,
                endpoint=endpoint,
                request_body=payload,
                response_time_ms=elapsed_ms,
                error_message=str(e),
                is_success=False,
            )
            raise


# =============================================================================
# CUSTOM EXCEPTIONS
# =============================================================================

class DuplicatePOError(Exception):
    def __init__(self, po_number):
        self.po_number = po_number
        super().__init__(f"Duplicate PO: {po_number}")


class ASNSubmissionError(Exception):
    pass
