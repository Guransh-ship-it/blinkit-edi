# Blinkit EDI Microservice - Deployment & Integration Guide

## Prerequisites

- **Python 3.12+**
- **PostgreSQL 16+** (local install or Supabase)

### Install Prerequisites (Ubuntu/Debian)

```bash
# Python
sudo apt update
sudo apt install python3.12 python3.12-venv python3-pip

# PostgreSQL
sudo apt install postgresql postgresql-contrib
```

### Install Prerequisites (Windows)

- Python: Download from https://python.org
- PostgreSQL: Download from https://www.postgresql.org/download/windows/

---

## Quick Start

```bash
# 1. Clone and setup
cd blinkit-edi
cp .env.template .env
# Edit .env with your actual values

# 2. Create virtual environment
python -m venv venv

# Activate (Linux/Mac)
source venv/bin/activate

# Activate (Windows)
venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create the database
# Linux:
sudo -u postgres createdb blinkit_edi
# Or connect to psql and run:
# CREATE DATABASE blinkit_edi;

# 5. Create logs directory
mkdir logs

# 6. Run migrations
python manage.py migrate

# 7. Create admin user
python manage.py createsuperuser

# 8. Start the server
python manage.py runserver 0.0.0.0:8000

# 9. Verify
curl http://localhost:8000/api/v1/health/
```

---

## Running in Production

### Start the server

```bash
# Development
python manage.py runserver 0.0.0.0:8000

# Production (use gunicorn)
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4 --timeout 120
```

### Retry failed tasks

PO acknowledgements and ASN submissions run in background threads automatically. If any fail, retry them with:

```bash
# Retry both failed ASNs and failed PO acks
python manage.py retry_failed

# Retry only failed ASNs
python manage.py retry_failed --asn-only

# Retry only failed PO acks
python manage.py retry_failed --ack-only
```

### Schedule automatic retries (recommended)

**Linux (cron)** — every 10 minutes:
```bash
crontab -e
# Add this line:
*/10 * * * * cd /path/to/blinkit-edi && /path/to/venv/bin/python manage.py retry_failed >> logs/retry.log 2>&1
```

**Windows (Task Scheduler):**
1. Open Task Scheduler → Create Basic Task
2. Trigger: Repeat every 10 minutes
3. Action: Start a program
   - Program: `C:\path\to\venv\Scripts\python.exe`
   - Arguments: `manage.py retry_failed`
   - Start in: `C:\path\to\blinkit-edi`

---

## Using systemd (Linux Production)

### Django (Gunicorn) — `/etc/systemd/system/blinkit-edi.service`

```ini
[Unit]
Description=Blinkit EDI Django Server
After=network.target postgresql.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/home/deploy/blinkit-edi
Environment="PATH=/home/deploy/blinkit-edi/venv/bin"
EnvironmentFile=/home/deploy/blinkit-edi/.env
ExecStart=/home/deploy/blinkit-edi/venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 4 --timeout 120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable blinkit-edi
sudo systemctl start blinkit-edi

# Check status
sudo systemctl status blinkit-edi
```

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                     BLINKIT SYSTEM                                │
│  (api.partnersbiz.com / dev.partnersbiz.com)                     │
└──────┬───────────────────────────────────┬───────────────────────┘
       │ PO Creation / Amendment           │ ASN Response
       │ (Blinkit → Us)                    │ (Blinkit → Us)
       ▼                                   ▲
┌──────────────────────────────────────────────────────────────────┐
│              YOUR SERVER (Static IP: x.x.x.x)                   │
│                                                                  │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────┐        │
│  │   Nginx     │───▶│  Django/DRF  │───▶│  PostgreSQL  │        │
│  │ (Reverse    │    │  (Gunicorn)  │    │  (local or   │        │
│  │  Proxy+SSL) │    │              │    │   Supabase)  │        │
│  └─────────────┘    └──────────────┘    └──────────────┘        │
│                                                                  │
│  Background threads handle PO ack & ASN submission async         │
│  Cron/Task Scheduler retries failed operations every 10 min     │
│                                                                  │
│  Outbound calls (PO Ack, ASN Push) ──────────────▶ Blinkit API  │
└──────────────────────────────────────────────────────────────────┘
```

## API Endpoints Summary

### Inbound Webhooks (Give these URLs to Blinkit)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/webhook/po/create/` | POST | Receive PO from Blinkit |
| `/api/v1/webhook/po/amendment/` | POST | Receive PO amendments |

### Internal APIs (Your systems call these)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/asn/create/` | POST | Create ASN from invoice data |
| `/api/v1/asn/submit/<uuid>/` | POST | Trigger ASN push to Blinkit |
| `/api/v1/po/` | GET | List all POs |
| `/api/v1/po/<po_number>/` | GET | PO detail with items |
| `/api/v1/asn/` | GET | List ASN submissions |
| `/api/v1/audit/` | GET | View audit logs |
| `/api/v1/health/` | GET | Health check |

### Admin Dashboard

| URL | Purpose |
|---|---|
| `/admin/` | Full Django admin with PO/ASN/Audit views |

## Integration Steps with Blinkit

### Step 1: Share with Blinkit Tech Team
- Your static IP for their whitelist
- Your webhook URLs:
  - PO Creation: `https://your-domain.com/api/v1/webhook/po/create/`
  - PO Amendment: `https://your-domain.com/api/v1/webhook/po/amendment/`

### Step 2: Receive from Blinkit
- Their API key (set as `BLINKIT_INBOUND_API_KEY` in .env)
- Their outbound API key for you (set as `BLINKIT_OUTBOUND_API_KEY` in .env)

### Step 3: Configure Preprod First
- Set `BLINKIT_USE_PROD=False` in .env
- Test with Blinkit's preprod endpoints
- Verify PO creation → Ack flow works
- Verify ASN submission → Response flow works

### Step 4: Go Live
- Set `BLINKIT_USE_PROD=True`
- Monitor via `/admin/` and `/api/v1/audit/`

## ASN Bridge: Google Sheets → Blinkit

Since your invoice data currently lives in Google Sheets, use the bridge command:

```bash
# Preview what will be imported
python manage.py import_asn_from_sheets \
    --sheet-id YOUR_SHEET_ID \
    --dry-run

# Import and auto-submit to Blinkit
python manage.py import_asn_from_sheets \
    --sheet-id YOUR_SHEET_ID \
    --submit
```

### Google Sheet Expected Format

| po_number | invoice_number | invoice_date | delivery_date | item_id | sku_code | batch_number | upc | quantity | mrp | unit_basic_price | unit_landing_price | cgst_pct | sgst_pct | igst_pct | hsn_code | expiry_date | mfg_date | uom_unit | uom_value | delivery_type | delivery_partner | tracking_code |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PO123 | INV001 | 2025-03-20 | 2025-03-22 | 10016623 | SKU1 | BATCH1 | 890102301 | 50 | 99 | 80.5 | 95 | 2.5 | 2.5 | 0 | 15159020 | 2026-03-20 | 2025-01-01 | ml | 500 | COURIER | BlueDart | BD123 |

## Nginx Configuration (Production)

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # Allow only Blinkit IPs + your internal IPs
    # Uncomment and set Blinkit's IPs when they share them
    # allow 1.2.3.4;  # Blinkit IP 1
    # allow 5.6.7.8;  # Blinkit IP 2
    # allow 10.0.0.0/8;  # Internal network
    # deny all;

    location /api/v1/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }

    location /admin/ {
        # Restrict admin to internal IPs only
        allow 10.0.0.0/8;
        allow 192.168.0.0/16;
        deny all;

        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Monitoring & Troubleshooting

### Check PO status
```bash
curl http://localhost:8000/api/v1/po/?status=RECEIVED
curl http://localhost:8000/api/v1/po/PO123456/
```

### Check failed ASNs
```bash
curl http://localhost:8000/api/v1/asn/?status=FAILED
```

### Check audit trail
```bash
curl "http://localhost:8000/api/v1/audit/?po_number=PO123&event_type=PO_CREATION"
```

### View logs
```bash
# Application log
tail -f logs/app.log

# Service log (systemd)
sudo journalctl -u blinkit-edi -f
```

### Manual ASN retry
```bash
# Via API
curl -X POST http://localhost:8000/api/v1/asn/submit/<asn-uuid>/

# Via management command
python manage.py retry_failed

# Via Django admin → ASN list → select → "Re-submit selected ASNs"
```

## Database Schema (ER Summary)

```
PurchaseOrder (1) ──── (N) PurchaseOrderItem
     │
     └──── (N) ASNSubmission (1) ──── (N) ASNItem

POAmendment (1) ──── (N) POAmendmentItem

EDIAuditLog (standalone - logs all API interactions)
```
