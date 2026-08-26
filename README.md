# ummm_the_api_scrapper_thingy

A lightweight local web application built with Python (Flask) and plain JavaScript to automate A–Z database scrapes, preserve unique records, handle rate limits, and interactively view/manage query history.

---
Automated A-Z registry scraping web app built with Python Flask.

## Quick Setup
1. Install dependencies: pip install -r requirements.txt
2. Configure your .env file
3. Start the server: python app.py
4. Open http://localhost:5000 in your browser

---

## Key Features
* **Automated Scraping Pipeline:** Loops through characters `A-Z` with configurable rate-limiting delays to protect against request blocking.
* **Idempotent Storage:** Deduplicates records automatically by unique IRN/key.
* **Text Normalization:** Fixes common UTF-8 double-encoding (Mojibake) artifacts during data collection.
* **Timestamped Runs:** Organizes data by date and time, auto-creating a local `searches/` directory for record isolation.
* **Interactive UI:** Features client-side search, filtering, and pagination using DataTables.
* **Full Local Control:** Load historical search logs or purge outdated files directly from your browser.

---

## Directory Structure

```text
my_registry_app/
├── app.py              # Flask server backend & scraping engine
├── index.html          # Frontend dashboard UI
├── requirements.txt    # Python dependencies
└── searches/           # Auto-created folder containing timestamped JSON results
