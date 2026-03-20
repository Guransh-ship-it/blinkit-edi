# Blinkit EDI - Complete Flow Documentation

## Overview

This microservice handles EDI (Electronic Data Interchange) between **suppliers** and **Blinkit**. It manages the full purchase order lifecycle: receiving POs, processing amendments, sending acknowledgements, and submitting ASN (Advanced Shipment Notices) / invoices.

---

## System Architecture

```
                         BLINKIT SYSTEMS
              (api.partnersbiz.com / dev.partnersbiz.com)
                    │                        ▲
                    │ Inbound                │ Outbound
                    │ (PO, Amendments)       │ (ASN, PO Ack)
                    ▼                        │
┌─────────────────────────────────────────────────────────────┐
│                    EDI MICROSERVICE                         │
│                                                             │
│   ┌─────────────┐    ┌──────────────┐    ┌──────────────┐  │
│   │   Views      │───▶│  Services    │───▶│   Models     │  │
│   │  (API Layer) │    │ (Biz Logic)  │    │ (PostgreSQL) │  │
│   └─────────────┘    └──────────────┘    └──────────────┘  │
│          │                                      ▲           │
│          ▼                                      │           │
│   ┌─────────────┐    ┌──────────────┐           │           │
│   │  Background  │───▶│   Services   │──────────┘           │
│   │  Threads     │    │ (Outbound)   │                      │
│   └─────────────┘    └──────────────┘                      │
└─────────────────────────────────────────────────────────────┘
         ▲
         │ Manual import
   ┌─────┴──────┐
   │  Google    │
   │  Sheets    │
   └────────────┘
```

---

## API Endpoints

| Endpoint | Method | Auth | Direction | Purpose |
|----------|--------|------|-----------|---------|
| `/api/v1/health/` | GET | None | - | Health check |
| `/api/v1/webhook/po/create/` | POST | Api-Key (Blinkit) | Inbound | Receive Purchase Order |
| `/api/v1/webhook/po/amendment/` | POST | Api-Key (Blinkit) | Inbound | Receive PO Amendment |
| `/api/v1/asn/create/` | POST | None | Internal | Create ASN from invoice data |
| `/api/v1/asn/submit/<uuid>/` | POST | None | Internal | Queue ASN for submission to Blinkit |
| `/api/v1/po/` | GET | None | Internal | List POs (filterable) |
| `/api/v1/po/<po_number>/` | GET | None | Internal | PO detail with items |
| `/api/v1/asn/` | GET | None | Internal | List ASN submissions |
| `/api/v1/audit/` | GET | None | Internal | View audit logs |

---

## Flow 1: Purchase Order Creation (Inbound)

**Trigger:** Blinkit POSTs a new PO to our webhook.

```
Blinkit                          EDI Service                         Database
  │                                  │                                  │
  │  POST /webhook/po/create/        │                                  │
  │  Headers: { Api-Key: xxx }       │                                  │
  │  Body: { po_number, tenant,      │                                  │
  │          details: { items... } }  │                                  │
  │─────────────────────────────────▶│                                  │
  │                                  │                                  │
  │                          1. Validate Api-Key                        │
  │                          2. Validate payload                        │
  │                             (POCreationInboundSerializer)           │
  │                                  │                                  │
  │                          3. Check duplicate PO                      │
  │                                  │──────────────────────────────────▶│
  │                                  │◀─────────────────────────────────│
  │                                  │                                  │
  │                          4. Create PurchaseOrder                    │
  │                             status = RECEIVED                      │
  │                                  │──────────────────────────────────▶│
  │                                  │                                  │
  │                          5. Create PurchaseOrderItems               │
  │                             (for each item in payload)             │
  │                                  │──────────────────────────────────▶│
  │                                  │                                  │
  │                          6. Set final status:                       │
  │                             - ACCEPTED (all items OK)              │
  │                             - PARTIALLY_ACCEPTED (some failed)     │
  │                             - REJECTED (all items failed)          │
  │                                  │──────────────────────────────────▶│
  │                                  │                                  │
  │                          7. Log to EDIAuditLog                      │
  │                                  │──────────────────────────────────▶│
  │                                  │                                  │
  │                          8. Fire PO ack in background thread        │
  │                             (send_po_acknowledgement)              │
  │                                  │                                  │
  │  Response: 200 OK                │                                  │
  │  { success, po_status,           │                                  │
  │    po_number, errors, warnings } │                                  │
  │◀─────────────────────────────────│                                  │
```

### Status Transitions (PurchaseOrder)

```
RECEIVED ──▶ ACCEPTED ──────────▶ ACK_SENT
         ──▶ PARTIALLY_ACCEPTED ─▶ ACK_SENT
         ──▶ REJECTED ──────────▶ ACK_SENT
                                 ──▶ ACK_FAILED ──▶ (retry) ──▶ ACK_SENT
```

### Error Codes

| Code | Meaning |
|------|---------|
| E100 | Payload validation error (missing/invalid field) |
| E101 | Duplicate PO number |
| E105 | Item-level processing error |
| E999 | Internal server error |

---

## Flow 2: PO Acknowledgement (Outbound - Async)

**Trigger:** Background thread fires `send_po_acknowledgement` after PO is processed.

```
Background Thread                EDI Service                    Blinkit
  │                                  │                            │
  │  send_po_acknowledgement(po_id)  │                            │
  │─────────────────────────────────▶│                            │
  │                                  │                            │
  │                          1. Fetch PO from DB                  │
  │                          2. Collect item errors                │
  │                          3. Build ack payload                  │
  │                             { success, po_status,             │
  │                               po_number, errors }             │
  │                                  │                            │
  │                          4. POST to Blinkit                   │
  │                             Endpoint: /webhook/public/v1/     │
  │                                       po/acknowledgement      │
  │                             Headers: { Api-Key: outbound }    │
  │                                  │───────────────────────────▶│
  │                                  │◀──────────────────────────│
  │                                  │                            │
  │                          5. Update PO:                        │
  │                             status = ACK_SENT                │
  │                             ack_sent_at = now()              │
  │                                  │                            │
  │                          6. Log to EDIAuditLog               │
  │                                  │                            │
  │  ┌───────── On failure ──────────┤                            │
  │  │  status = ACK_FAILED          │                            │
  │  │  Retried via:                 │                            │
  │  │  python manage.py retry_failed│                            │
  │  └──────────────────────────────▶│                            │
```

### Retry Mechanism

- **On failure:** PO status is set to `ACK_FAILED`
- **Periodic retry:** Run `python manage.py retry_failed` via cron/Task Scheduler (recommended: every 10 minutes) to re-attempt all failed acks

---

## Flow 3: PO Amendment (Inbound)

**Trigger:** Blinkit sends item-level changes (MRP, UPC, UOM) to existing POs.

```
Blinkit                          EDI Service                         Database
  │                                  │                                  │
  │  POST /webhook/po/amendment/     │                                  │
  │  Body: { request_data: [         │                                  │
  │    { item_id, variants: [        │                                  │
  │      { upc, mrp, uom,           │                                  │
  │        po_numbers: [...] }       │                                  │
  │    ]}                            │                                  │
  │  ]}                              │                                  │
  │─────────────────────────────────▶│                                  │
  │                                  │                                  │
  │                          1. Validate Api-Key                        │
  │                          2. Validate payload                        │
  │                                  │                                  │
  │                          3. Create POAmendment                      │
  │                             status = RECEIVED                      │
  │                                  │──────────────────────────────────▶│
  │                                  │                                  │
  │                          4. For each item × variant:                │
  │                             a. Create POAmendmentItem               │
  │                             b. Find matching PurchaseOrderItems     │
  │                             c. Snapshot previous values             │
  │                             d. Apply: mrp, upc, uom changes        │
  │                                  │──────────────────────────────────▶│
  │                                  │                                  │
  │                          5. amendment.status = APPLIED              │
  │                          6. Log to EDIAuditLog                      │
  │                                  │──────────────────────────────────▶│
  │                                  │                                  │
  │  Response: 200 OK                │                                  │
  │  { success, message,             │                                  │
  │    updated_items }               │                                  │
  │◀─────────────────────────────────│                                  │
```

### What Gets Changed

| Field | Description |
|-------|-------------|
| `mrp` | Maximum Retail Price of the item |
| `upc` | Universal Product Code (barcode/GTIN) |
| `uom_unit` | Unit of Measurement (ml, kg, piece, etc.) |
| `uom_value` | UOM quantity value |

Previous values are stored in `POAmendmentItem.previous_values` for audit.

---

## Flow 4: ASN Creation (Internal)

**Trigger:** Internal system (API call or Google Sheets import) creates an ASN draft.

### Path A: Via API

```
Internal System                  EDI Service                         Database
  │                                  │                                  │
  │  POST /asn/create/               │                                  │
  │  Body: { po_number,              │                                  │
  │    invoice_number,               │                                  │
  │    invoice_date, delivery_date,  │                                  │
  │    supplier_details, items,      │                                  │
  │    shipment_details, ... }       │                                  │
  │─────────────────────────────────▶│                                  │
  │                                  │                                  │
  │                          1. Create ASNSubmission                    │
  │                             status = DRAFT                         │
  │                                  │──────────────────────────────────▶│
  │                                  │                                  │
  │                          2. Link to PurchaseOrder                   │
  │                             (if PO exists in DB)                   │
  │                                  │──────────────────────────────────▶│
  │                                  │                                  │
  │                          3. Create ASNItems                         │
  │                             (for each item in payload)             │
  │                                  │──────────────────────────────────▶│
  │                                  │                                  │
  │  Response: 201 Created           │                                  │
  │  { asn_id, message }             │                                  │
  │◀─────────────────────────────────│                                  │
```

### Path B: Via Google Sheets

```bash
python manage.py import_asn_from_sheets --sheet-id <SHEET_ID> [--submit] [--dry-run]
```

```
Google Sheets                    Management Command                   Database
  │                                  │                                  │
  │  Read all rows                   │                                  │
  │◀─────────────────────────────────│                                  │
  │─────────────────────────────────▶│                                  │
  │                                  │                                  │
  │                          1. Group rows by                           │
  │                             (po_number, invoice_number)            │
  │                                  │                                  │
  │                          2. For each group:                         │
  │                             a. Skip if ASN already exists          │
  │                             b. Calculate totals                    │
  │                             c. Create ASNSubmission (DRAFT)        │
  │                             d. Create ASNItems                     │
  │                                  │──────────────────────────────────▶│
  │                                  │                                  │
  │                          3. If --submit flag:                       │
  │                             Submit each ASN to Blinkit             │
  │                             immediately                            │
```

**Expected Sheet Columns:**
`po_number`, `invoice_number`, `invoice_date`, `delivery_date`, `item_id`, `sku_code`, `batch_number`, `sku_description`, `upc`, `quantity`, `mrp`, `hsn_code`, `unit_basic_price`, `unit_landing_price`, `cgst_pct`, `sgst_pct`, `igst_pct`, `expiry_date`, `mfg_date`, `uom_unit`, `uom_value`, `delivery_type`, `delivery_partner`, `tracking_code`

---

## Flow 5: ASN Submission to Blinkit (Outbound)

**Trigger:** `POST /api/v1/asn/submit/<asn_id>/` or `--submit` flag on import command.

```
API / Command                    Background Thread                   Blinkit
  │                                  │                                  │
  │  submit_asn_to_blinkit(asn_id)   │                                  │
  │─────────────────────────────────▶│                                  │
  │                                  │                                  │
  │                          1. Fetch ASN + items from DB              │
  │                                  │                                  │
  │                          2. Build ASN payload                      │
  │                             (ASNService.build_asn_payload)         │
  │                             { po_number, invoice_number,           │
  │                               items, supplier_details,            │
  │                               shipment_details, ... }             │
  │                                  │                                  │
  │                          3. Save payload to                        │
  │                             asn.submitted_payload                  │
  │                                  │                                  │
  │                          4. status = PENDING                       │
  │                                  │                                  │
  │                          5. POST to Blinkit                        │
  │                             Endpoint: /webhook/public/v1/asn       │
  │                             Headers: { Api-Key: outbound }         │
  │                                  │─────────────────────────────────▶│
  │                                  │◀────────────────────────────────│
  │                                  │                                  │
  │                          6. Parse response:                        │
  │                             asn_sync_status → map to status        │
  │                             - ACCEPTED → Status.ACCEPTED           │
  │                             - PARTIALLY_ACCEPTED → ...             │
  │                             - REJECTED → Status.REJECTED           │
  │                             - other → Status.SUBMITTED             │
  │                             - HTTP error → Status.FAILED           │
  │                                  │                                  │
  │                          7. Save: asn_id, blinkit_response,        │
  │                             submitted_at                           │
  │                                  │                                  │
  │                          8. Log to EDIAuditLog                     │
```

### Status Transitions (ASNSubmission)

```
DRAFT ──▶ PENDING ──▶ SUBMITTED ──▶ (terminal)
                   ──▶ ACCEPTED ──▶ (terminal)
                   ──▶ PARTIALLY_ACCEPTED ──▶ (terminal)
                   ──▶ REJECTED ──▶ (terminal)
                   ──▶ FAILED ──▶ (retry) ──▶ PENDING ──▶ ...
```

### Retry Mechanism

- **On failure:** ASN status is set to `FAILED`, `retry_count` is incremented
- **Periodic retry:** Run `python manage.py retry_failed` via cron/Task Scheduler (recommended: every 10 minutes) to re-attempt all FAILED ASNs where `retry_count < MAX_RETRIES`

---

## Authentication

### Inbound (Blinkit calls us)

```
Request Header:  Api-Key: <BLINKIT_INBOUND_API_KEY>
```

- Validated by `BlinkitAPIKeyAuthentication` class
- Applied to webhook endpoints only (`/webhook/po/create/`, `/webhook/po/amendment/`)
- Rate limited: 500 requests/hour per scope

### Outbound (We call Blinkit)

```
Request Headers:
  Content-Type: application/json
  Api-Key: <BLINKIT_OUTBOUND_API_KEY>
```

### Environment Toggle

| `BLINKIT_USE_PROD` | Base URL |
|---------------------|----------|
| `False` (default) | `https://dev.partnersbiz.com` |
| `True` | `https://api.partnersbiz.com` |

---

## Database Models

```
┌──────────────────────┐       ┌───────────────────────┐
│   PurchaseOrder      │       │   POAmendment         │
│──────────────────────│       │───────────────────────│
│ po_number (unique)   │       │ po_numbers []         │
│ tenant               │       │ status                │
│ status               │       │ request_data          │
│ buyer/supplier info   │       └───────────┬───────────┘
│ totals               │                   │ 1:N
│ raw_payload          │       ┌───────────▼───────────┐
│ ack_sent_at          │       │   POAmendmentItem     │
└───────────┬──────────┘       │───────────────────────│
            │ 1:N              │ item_id, upc, mrp     │
┌───────────▼──────────┐       │ uom, previous_values  │
│ PurchaseOrderItem    │       └───────────────────────┘
│──────────────────────│
│ item_id, sku_code    │
│ name, upc            │
│ units_ordered, mrp   │
│ tax, uom, crates     │
└──────────────────────┘

┌──────────────────────┐       ┌───────────────────────┐
│   ASNSubmission      │       │   EDIAuditLog         │
│──────────────────────│       │───────────────────────│
│ po_number            │       │ direction (IN/OUT)    │
│ invoice_number       │       │ event_type            │
│ status               │       │ po_number             │
│ supplier/buyer info   │       │ request/response body │
│ shipment_details     │       │ status, latency       │
│ blinkit_response     │       │ error_message         │
│ retry_count          │       └───────────────────────┘
└───────────┬──────────┘
            │ 1:N
┌───────────▼──────────┐
│      ASNItem         │
│──────────────────────│
│ item_id, batch_number│
│ upc, quantity, mrp   │
│ tax, pricing         │
│ expiry, mfg, uom     │
│ codes, packaging     │
└──────────────────────┘
```

---

## Background Tasks

| Task | Trigger | Purpose |
|------|---------|---------|
| `send_po_acknowledgement` | On PO create (background thread) | Send PO ack to Blinkit |
| `submit_asn_to_blinkit` | On ASN submit (background thread) | Push ASN to Blinkit |
| `python manage.py retry_failed` | Cron / Task Scheduler (every 10 min) | Retry all FAILED ASNs and ACK_FAILED POs |

---

## End-to-End Lifecycle

```
 ┌─────────┐     ┌──────────┐     ┌───────────┐     ┌───────────┐     ┌──────────┐
 │ Blinkit  │     │  Receive  │     │  Process   │     │  Create   │     │  Submit  │
 │ sends PO │────▶│  & Store  │────▶│  & Ack     │────▶│  ASN      │────▶│  ASN to  │
 │          │     │  PO       │     │  (async)   │     │  (manual) │     │  Blinkit │
 └─────────┘     └──────────┘     └───────────┘     └───────────┘     └──────────┘
       │                                                                     │
       │          ┌──────────┐                                               │
       └─────────▶│ Amendment│  (optional, can happen anytime)              │
                  │ updates  │                                               │
                  │ PO items │                                               │
                  └──────────┘                                               │
                                                                             ▼
                                                                    ┌──────────────┐
                                                                    │   Blinkit     │
                                                                    │   responds:   │
                                                                    │   ACCEPTED /  │
                                                                    │   REJECTED    │
                                                                    └──────────────┘
```

1. **Blinkit creates a PO** → stored with status `RECEIVED` → validated → `ACCEPTED`/`PARTIALLY_ACCEPTED`/`REJECTED`
2. **Async ack sent** to Blinkit's acknowledgement endpoint → PO status becomes `ACK_SENT`
3. **Amendments arrive** (optional) → MRP/UPC/UOM changes applied to existing PO items
4. **Supplier creates ASN** (via API or Google Sheets) → ASN stored as `DRAFT`
5. **ASN submitted** to Blinkit → Blinkit responds with `ACCEPTED`/`REJECTED`
6. **Failed operations auto-retry** via `python manage.py retry_failed` (cron/Task Scheduler)

---

## Audit Trail

Every inbound and outbound API call is logged in `EDIAuditLog` with:

- **Direction:** INBOUND (Blinkit → us) or OUTBOUND (us → Blinkit)
- **Event type:** PO_CREATION, PO_ACK, PO_AMENDMENT, ASN_SYNC
- **Request/response bodies** (full payload)
- **HTTP status code and response latency** (ms)
- **Error messages** on failures
- **PO number and invoice number** for cross-referencing
