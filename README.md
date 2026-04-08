# 🛒 Blinkit EDI Integration

A Django-based **Electronic Data Interchange (EDI)** integration system for Blinkit's supply chain and vendor ecosystem. This project facilitates automated, standardized exchange of business documents (purchase orders, invoices, shipment notices, etc.) between Blinkit and its suppliers/partners.

## 📋 Table of Contents

- [Overview](#overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Documentation](#documentation)
- [Contributing](#contributing)

## Overview

`blinkit-edi` is a backend service built with Django that handles EDI workflows for Blinkit integrations. It enables seamless data exchange between internal systems and external vendor/partner platforms using standard EDI protocols and formats.

## Tech Stack

- **Language:** Python 3.x
- **Framework:** Django
- **Configuration:** Environment-based (`.env`)

## Project Structure

    blinkit-edi/
    ├── blinkit_edi/        # Core app (models, views, serializers, EDI logic)
    ├── config/             # Django settings (base, dev, prod)
    ├── docs/               # Documentation and EDI schema references
    ├── .env.template       # Template for environment variables
    ├── manage.py           # Django management CLI
    └── requirements.txt    # Python dependencies

## Getting Started

### Prerequisites

- Python 3.9+
- pip
- Virtualenv (recommended)

### Installation

```bash
git clone https://github.com/Guransh-ship-it/blinkit-edi.git
cd blinkit-edi
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Environment Variables

```bash
cp .env.template .env
```

Open `.env` and configure your database credentials, secret key, and API keys.

### Running the Server

```bash
python manage.py migrate
python manage.py runserver
```

## Configuration

All environment-specific settings live in the `config/` directory. The project uses a `.env` file for secrets — **never commit your `.env` file**.

## Documentation

EDI schema definitions and integration guides are available in the [`docs/`](./docs/) folder.

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Commit your changes: `git commit -m "feat: add your feature"`
4. Push to the branch: `git push origin feature/your-feature-name`
5. Open a Pull Request

## Author

**Guransh Singh** — [@Guransh-ship-it](https://github.com/Guransh-ship-it)
