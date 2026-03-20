"""
Blinkit EDI - Background Tasks

Runs async tasks using Python threading (no Redis/Celery required).
For retries, use the management command: python manage.py retry_failed
"""
import logging
import threading

logger = logging.getLogger("blinkit_edi")


def _run_in_background(func, *args, **kwargs):
    """Run a function in a background thread."""
    def wrapper():
        try:
            func(*args, **kwargs)
        except Exception as exc:
            logger.error(f"Background task {func.__name__} failed: {exc}")

    thread = threading.Thread(target=wrapper, daemon=True)
    thread.start()


def send_po_acknowledgement(po_id: str):
    """
    Send PO Acknowledgement to Blinkit in a background thread.
    Called after PO is processed.
    """
    _run_in_background(_send_po_ack_sync, po_id)


def _send_po_ack_sync(po_id: str):
    """Synchronous implementation of PO ack sending."""
    from .models import PurchaseOrder
    from .services import POAckService

    try:
        po = PurchaseOrder.objects.get(id=po_id)
        logger.info(f"Sending PO Ack for {po.po_number}")

        # Collect item-level errors
        errors = []
        for item in po.items.filter(is_valid=False):
            errors.append({
                "code": "E105",
                "field_name": "item_id",
                "message": f"Error in item {item.item_id}",
                "description": "; ".join(item.validation_errors),
                "error_params": {"item_id": item.item_id},
            })

        POAckService.send_ack(po, errors=errors)
        logger.info(f"PO Ack sent successfully for {po.po_number}")

    except PurchaseOrder.DoesNotExist:
        logger.error(f"PO {po_id} not found for ack")
    except Exception as exc:
        logger.error(f"PO Ack failed for {po_id}: {exc}")


def submit_asn_to_blinkit(asn_id: str):
    """
    Submit ASN to Blinkit in a background thread.
    """
    _run_in_background(_submit_asn_sync, asn_id)


def _submit_asn_sync(asn_id: str):
    """Synchronous implementation of ASN submission."""
    from .models import ASNSubmission
    from .services import ASNService, ASNSubmissionError

    try:
        asn = ASNSubmission.objects.get(id=asn_id)
        logger.info(f"Submitting ASN {asn.invoice_number} for PO {asn.po_number}")

        response = ASNService.submit_asn(asn)

        sync_status = response.get("asn_sync_status", "")
        if sync_status == "REJECTED":
            logger.warning(
                f"ASN {asn.invoice_number} REJECTED: {response.get('message')}"
            )
        else:
            logger.info(
                f"ASN {asn.invoice_number} → {sync_status} (asn_id: {response.get('asn_id')})"
            )

    except ASNSubmission.DoesNotExist:
        logger.error(f"ASN {asn_id} not found")
    except ASNSubmissionError as exc:
        logger.error(f"ASN submission failed: {exc}")
    except Exception as exc:
        logger.error(f"ASN submission unexpected error: {exc}")
