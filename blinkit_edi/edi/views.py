"""
Blinkit EDI - API Views

4 main endpoints:
1. POST /api/v1/webhook/po/create/       — Receive PO from Blinkit (inbound)
2. POST /api/v1/webhook/po/amendment/     — Receive PO amendment from Blinkit (inbound)
3. POST /api/v1/asn/submit/{asn_id}/      — Trigger ASN submission to Blinkit (internal)
4. POST /api/v1/asn/create/               — Create ASN from internal data (internal)

Plus read endpoints:
5. GET  /api/v1/po/                       — List POs
6. GET  /api/v1/po/{po_number}/           — PO detail
7. GET  /api/v1/asn/                      — List ASN submissions
8. GET  /api/v1/audit/                    — Audit log
"""
import logging
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import BlinkitAPIKeyAuthentication, IsBlinkitAuthenticated
from .models import (
    PurchaseOrder, PurchaseOrderItem, POAmendment,
    ASNSubmission, ASNItem, EDIAuditLog,
)
from .serializers import (
    POCreationInboundSerializer, POAmendmentInboundSerializer,
    ASNOutboundSerializer,
)
from .services import (
    POCreationService, POAmendmentService, ASNService,
    DuplicatePOError,
)
from .tasks import send_po_acknowledgement, submit_asn_to_blinkit

logger = logging.getLogger("blinkit_edi")


# =============================================================================
# INBOUND: PO CREATION WEBHOOK (Blinkit → Us)
# =============================================================================

class POCreationWebhookView(APIView):
    """
    Webhook endpoint that Blinkit calls when creating a new Purchase Order.
    
    Blinkit POSTs to this endpoint with the PO payload.
    We validate, store, and return an immediate acknowledgement.
    If async processing is needed, we queue the ack task.
    """
    authentication_classes = [BlinkitAPIKeyAuthentication]
    permission_classes = [IsBlinkitAuthenticated]
    throttle_scope = "blinkit_webhook"

    def post(self, request):
        raw_payload = request.data
        logger.info(f"PO Creation webhook received: {raw_payload.get('po_number', 'unknown')}")

        serializer = POCreationInboundSerializer(data=raw_payload)
        if not serializer.is_valid():
            logger.warning(f"PO validation failed: {serializer.errors}")
            return Response(
                {
                    "success": False,
                    "message": "Payload validation failed",
                    "timestamp": timezone.now().isoformat(),
                    "data": {
                        "po_status": "rejected",
                        "po_number": raw_payload.get("po_number", ""),
                        "errors": [
                            {
                                "code": "E100",
                                "field_name": field,
                                "message": str(errors),
                                "description": f"Validation error on field: {field}",
                            }
                            for field, errors in serializer.errors.items()
                        ],
                        "warnings": [],
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            po = POCreationService.process_po(serializer.validated_data, raw_payload)
        except DuplicatePOError as e:
            return Response(
                {
                    "success": False,
                    "message": f"Duplicate PO: {e.po_number}",
                    "timestamp": timezone.now().isoformat(),
                    "data": {
                        "po_status": "rejected",
                        "po_number": e.po_number,
                        "errors": [{
                            "code": "E101",
                            "field_name": "po_number",
                            "message": "Duplicate PO number",
                            "description": f"PO {e.po_number} already exists",
                        }],
                        "warnings": [],
                    },
                },
                status=status.HTTP_409_CONFLICT,
            )
        except Exception as e:
            logger.exception(f"PO processing error: {e}")
            return Response(
                {
                    "success": False,
                    "message": "Internal processing error",
                    "timestamp": timezone.now().isoformat(),
                    "data": {
                        "po_status": "rejected",
                        "po_number": raw_payload.get("po_number", ""),
                        "errors": [{
                            "code": "E999",
                            "field_name": "",
                            "message": "Internal server error",
                            "description": str(e),
                        }],
                        "warnings": [],
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Build response
        item_errors = []
        for item in po.items.filter(is_valid=False):
            item_errors.append({
                "code": "E105",
                "field_name": "item_id",
                "message": f"Error processing item {item.item_id}",
                "description": "; ".join(item.validation_errors),
                "error_params": {"item_id": item.item_id},
            })

        ack_payload = POCreationService.build_ack_payload(po, errors=item_errors)

        # Queue async ack to Blinkit's ack endpoint (if they process async)
        send_po_acknowledgement(str(po.id))

        return Response(ack_payload, status=status.HTTP_200_OK)


# =============================================================================
# INBOUND: PO AMENDMENT WEBHOOK (Blinkit → Us)
# =============================================================================

class POAmendmentWebhookView(APIView):
    """
    Webhook endpoint for PO amendments from Blinkit.
    Receives item-level changes (MRP, UPC, UOM) and applies to existing POs.
    """
    authentication_classes = [BlinkitAPIKeyAuthentication]
    permission_classes = [IsBlinkitAuthenticated]
    throttle_scope = "blinkit_webhook"

    def post(self, request):
        raw_payload = request.data
        logger.info("PO Amendment webhook received")

        serializer = POAmendmentInboundSerializer(data=raw_payload)
        if not serializer.is_valid():
            return Response(
                {"success": False, "message": "Validation failed", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            amendment = POAmendmentService.process_amendment(
                serializer.validated_data, raw_payload
            )
            response_data = POAmendmentService.build_response(amendment)
            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception(f"Amendment processing error: {e}")
            return Response(
                {"success": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# OUTBOUND: ASN CREATION & SUBMISSION (Internal trigger)
# =============================================================================

class ASNCreateView(APIView):
    """
    Internal endpoint to create an ASN record from invoice data.
    Called by your internal systems (Google Sheets bridge, admin UI, etc.)
    
    After creation, call /api/v1/asn/submit/{id}/ to push to Blinkit.
    """

    def post(self, request):
        data = request.data

        try:
            # Create ASN header
            asn = ASNSubmission.objects.create(
                po_number=data["po_number"],
                invoice_number=data["invoice_number"],
                invoice_date=data["invoice_date"],
                delivery_date=data["delivery_date"],
                tax_distribution=data.get("tax_distribution", []),
                total_additional_cess_value=data.get("total_additional_cess_value", 0),
                basic_price=data["basic_price"],
                landing_price=data.get("landing_price", 0),
                box_count=data.get("box_count", 0),
                quantity=data["quantity"],
                case_config=data.get("case_config"),
                item_count=data["item_count"],
                po_status=data.get("po_status", "PO_FULFILLED"),
                supplier_name=data["supplier_details"]["name"],
                supplier_gstin=data["supplier_details"]["gstin"],
                supplier_address=data["supplier_details"].get("supplier_address", {}),
                buyer_gstin=data["buyer_details"]["gstin"],
                e_way_bill_number=data.get("shipment_details", {}).get("e_way_bill_number", ""),
                delivery_type=data.get("shipment_details", {}).get("delivery_type", "SELF"),
                delivery_partner=data.get("shipment_details", {}).get("delivery_partner", ""),
                delivery_tracking_code=data.get("shipment_details", {}).get("delivery_tracking_code", ""),
                status=ASNSubmission.Status.DRAFT,
            )

            # Link to PO if exists
            try:
                po = PurchaseOrder.objects.get(po_number=data["po_number"])
                asn.purchase_order = po
                asn.save()
            except PurchaseOrder.DoesNotExist:
                logger.warning(f"PO {data['po_number']} not found when creating ASN")

            # Create ASN items
            for item_data in data.get("items", []):
                tax = item_data.get("tax_distribution", {})
                uom = item_data.get("uom", {})
                ASNItem.objects.create(
                    asn=asn,
                    item_id=str(item_data["item_id"]),
                    sku_code=item_data.get("sku_code", ""),
                    batch_number=item_data["batch_number"],
                    sku_description=item_data.get("sku_description", ""),
                    upc=item_data["upc"],
                    quantity=item_data["quantity"],
                    mrp=item_data["mrp"],
                    hsn_code=item_data.get("hsn_code", ""),
                    cgst_percentage=tax.get("cgst_percentage", 0),
                    sgst_percentage=tax.get("sgst_percentage", 0),
                    igst_percentage=tax.get("igst_percentage", 0),
                    ugst_percentage=tax.get("ugst_percentage", 0),
                    cess_percentage=tax.get("cess_percentage", 0),
                    additional_cess_value=tax.get("additional_cess_value", 0),
                    unit_discount_amount=item_data.get("unit_discount_amount", 0),
                    unit_discount_percentage=item_data.get("unit_discount_percentage", 0),
                    unit_basic_price=item_data["unit_basic_price"],
                    unit_landing_price=item_data.get("unit_landing_price", 0),
                    expiry_date=item_data.get("expiry_date"),
                    mfg_date=item_data.get("mfg_date"),
                    shelf_life=item_data.get("shelf_life"),
                    uom_unit=uom.get("unit", ""),
                    uom_value=uom.get("value", 0),
                    no_of_packages=item_data.get("no_of_packages", 0),
                    code_category=item_data.get("code_category", ""),
                    codes=item_data.get("codes", []),
                    case_configuration=item_data.get("case_configuration", []),
                )

            return Response(
                {
                    "success": True,
                    "asn_id": str(asn.id),
                    "message": f"ASN created for PO {asn.po_number}. Call /api/v1/asn/submit/{asn.id}/ to push to Blinkit.",
                },
                status=status.HTTP_201_CREATED,
            )

        except KeyError as e:
            return Response(
                {"success": False, "message": f"Missing required field: {e}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception(f"ASN creation error: {e}")
            return Response(
                {"success": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ASNSubmitView(APIView):
    """
    Internal endpoint: triggers async ASN submission to Blinkit.
    POST /api/v1/asn/submit/{asn_id}/
    """

    def post(self, request, asn_id):
        try:
            asn = ASNSubmission.objects.get(id=asn_id)
        except ASNSubmission.DoesNotExist:
            return Response(
                {"success": False, "message": "ASN not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if asn.status not in [ASNSubmission.Status.DRAFT, ASNSubmission.Status.FAILED]:
            return Response(
                {"success": False, "message": f"ASN already in status: {asn.status}"},
                status=status.HTTP_409_CONFLICT,
            )

        submit_asn_to_blinkit(str(asn.id))

        return Response(
            {
                "success": True,
                "message": f"ASN {asn.invoice_number} queued for submission",
                "asn_id": str(asn.id),
            },
            status=status.HTTP_202_ACCEPTED,
        )


# =============================================================================
# READ ENDPOINTS (Internal dashboard/monitoring)
# =============================================================================

class SmallPagination(PageNumberPagination):
    page_size = 20
    max_page_size = 100


class POListView(APIView):
    """List all POs with basic filters."""

    def get(self, request):
        qs = PurchaseOrder.objects.all()

        # Filters
        po_status = request.query_params.get("status")
        tenant = request.query_params.get("tenant")
        po_number = request.query_params.get("po_number")

        if po_status:
            qs = qs.filter(status=po_status)
        if tenant:
            qs = qs.filter(tenant=tenant)
        if po_number:
            qs = qs.filter(po_number__icontains=po_number)

        paginator = SmallPagination()
        page = paginator.paginate_queryset(qs, request)

        data = []
        for po in page:
            data.append({
                "id": str(po.id),
                "po_number": po.po_number,
                "tenant": po.tenant,
                "status": po.status,
                "total_sku": po.total_sku,
                "total_qty": po.total_qty,
                "total_amount": str(po.total_amount),
                "delivery_date": po.delivery_date,
                "created_at": po.created_at,
                "ack_sent_at": po.ack_sent_at,
            })

        return paginator.get_paginated_response(data)


class PODetailView(APIView):
    """Get full PO details including line items."""

    def get(self, request, po_number):
        try:
            po = PurchaseOrder.objects.prefetch_related("items").get(po_number=po_number)
        except PurchaseOrder.DoesNotExist:
            return Response({"error": "PO not found"}, status=status.HTTP_404_NOT_FOUND)

        items_data = []
        for item in po.items.all():
            items_data.append({
                "item_id": item.item_id,
                "sku_code": item.sku_code,
                "name": item.name,
                "upc": item.upc,
                "units_ordered": item.units_ordered,
                "mrp": str(item.mrp),
                "basic_price": str(item.basic_price),
                "landing_price": str(item.landing_price),
                "uom": f"{item.uom_value} {item.uom_unit}",
                "is_valid": item.is_valid,
            })

        return Response({
            "id": str(po.id),
            "po_number": po.po_number,
            "tenant": po.tenant,
            "status": po.status,
            "issue_date": po.issue_date,
            "delivery_date": po.delivery_date,
            "buyer_gstin": po.buyer_gstin,
            "supplier_name": po.supplier_name,
            "total_sku": po.total_sku,
            "total_qty": po.total_qty,
            "total_amount": str(po.total_amount),
            "items": items_data,
            "ack_sent_at": po.ack_sent_at,
            "created_at": po.created_at,
        })


class ASNListView(APIView):
    """List ASN submissions."""

    def get(self, request):
        qs = ASNSubmission.objects.all()

        asn_status = request.query_params.get("status")
        if asn_status:
            qs = qs.filter(status=asn_status)

        paginator = SmallPagination()
        page = paginator.paginate_queryset(qs, request)

        data = []
        for asn in page:
            data.append({
                "id": str(asn.id),
                "po_number": asn.po_number,
                "invoice_number": asn.invoice_number,
                "status": asn.status,
                "item_count": asn.item_count,
                "quantity": asn.quantity,
                "submitted_at": asn.submitted_at,
                "asn_id": asn.asn_id,
                "retry_count": asn.retry_count,
                "created_at": asn.created_at,
            })

        return paginator.get_paginated_response(data)


class AuditLogView(APIView):
    """View EDI audit logs."""

    def get(self, request):
        qs = EDIAuditLog.objects.all()

        direction = request.query_params.get("direction")
        event_type = request.query_params.get("event_type")
        po_number = request.query_params.get("po_number")

        if direction:
            qs = qs.filter(direction=direction)
        if event_type:
            qs = qs.filter(event_type=event_type)
        if po_number:
            qs = qs.filter(po_number=po_number)

        paginator = SmallPagination()
        page = paginator.paginate_queryset(qs, request)

        data = []
        for log in page:
            data.append({
                "id": str(log.id),
                "direction": log.direction,
                "event_type": log.event_type,
                "po_number": log.po_number,
                "is_success": log.is_success,
                "response_status": log.response_status,
                "response_time_ms": log.response_time_ms,
                "error_message": log.error_message,
                "created_at": log.created_at,
            })

        return paginator.get_paginated_response(data)


# =============================================================================
# HEALTH CHECK
# =============================================================================

@api_view(["GET"])
@authentication_classes([])
@permission_classes([])
def health_check(request):
    return Response({"status": "ok", "service": "blinkit-edi", "timestamp": timezone.now().isoformat()})
