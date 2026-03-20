"""
Blinkit EDI - URL Configuration
"""
from django.urls import path
from .views import (
    POCreationWebhookView,
    POAmendmentWebhookView,
    ASNCreateView,
    ASNSubmitView,
    POListView,
    PODetailView,
    ASNListView,
    AuditLogView,
    health_check,
)

app_name = "edi"

urlpatterns = [
    # Health
    path("health/", health_check, name="health"),

    # =========================================================================
    # INBOUND WEBHOOKS (Blinkit → Us)
    # These are the endpoints you give to Blinkit for them to call
    # =========================================================================
    path("webhook/po/create/", POCreationWebhookView.as_view(), name="po-create-webhook"),
    path("webhook/po/amendment/", POAmendmentWebhookView.as_view(), name="po-amendment-webhook"),

    # =========================================================================
    # OUTBOUND / INTERNAL (Us → Blinkit)
    # Called by your internal systems to create and push ASNs
    # =========================================================================
    path("asn/create/", ASNCreateView.as_view(), name="asn-create"),
    path("asn/submit/<uuid:asn_id>/", ASNSubmitView.as_view(), name="asn-submit"),

    # =========================================================================
    # READ / MONITORING
    # Internal dashboard endpoints
    # =========================================================================
    path("po/", POListView.as_view(), name="po-list"),
    path("po/<str:po_number>/", PODetailView.as_view(), name="po-detail"),
    path("asn/", ASNListView.as_view(), name="asn-list"),
    path("audit/", AuditLogView.as_view(), name="audit-log"),
]
