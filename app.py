import os
import json
import string
import time
import threading
import requests
import urllib3

from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory
from dotenv import load_dotenv


# ============================================================
# Configuration
# ============================================================

load_dotenv()

PORT = int(os.getenv("PORT", 5000))
DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "t")

TARGET_URL = os.getenv("TARGET_URL")
BASE_URL = os.getenv("BASE_URL")

if not TARGET_URL or not BASE_URL:
    raise RuntimeError(
        "TARGET_URL and BASE_URL must be set in environment variables "
        "or a local .env file. Do not commit .env to the repository."
    )

DEFAULT_DELAY = int(os.getenv("REQUEST_DELAY", 3))

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Temporary workaround for the RGD certificate issue.
VERIFY_SSL = False

# Maximum consecutive request failures before stopping.
MAX_CONSECUTIVE_ERRORS = 3


# ============================================================
# SSL
# ============================================================

if not VERIFY_SSL:
    urllib3.disable_warnings(
        urllib3.exceptions.InsecureRequestWarning
    )


# ============================================================
# Flask
# ============================================================

app = Flask(__name__, static_folder=".")


# ============================================================
# Files
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "searches")

os.makedirs(DATA_DIR, exist_ok=True)


# ============================================================
# Scraper state
# ============================================================

scrape_lock = threading.Lock()

scrape_state = {
    "running": False,
    "stop_requested": False,
    "current_letter": None,
    "completed_letters": [],
    "total_records": 0,
    "consecutive_errors": 0,
    "total_errors": 0,
    "filename": None,
    "status": "idle",
    "message": ""
}


# ============================================================
# Helpers
# ============================================================

def clean_text(raw_text):
    if not raw_text:
        return ""

    try:
        return raw_text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw_text


def save_records(file_path, records):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(
            list(records.values()),
            f,
            indent=2,
            ensure_ascii=False
        )


def update_state(**kwargs):
    with scrape_lock:
        scrape_state.update(kwargs)


def get_state():
    with scrape_lock:
        return dict(scrape_state)


def reset_state():
    with scrape_lock:
        scrape_state.update({
            "running": False,
            "stop_requested": False,
            "current_letter": None,
            "completed_letters": [],
            "total_records": 0,
            "consecutive_errors": 0,
            "total_errors": 0,
            "filename": None,
            "status": "idle",
            "message": ""
        })


# ============================================================
# Session
# ============================================================

def create_session():
    session = requests.Session()

    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    })

    return session


def initialize_session(session):
    print("")
    print("=" * 60)
    print("Initializing RGD session")
    print("=" * 60)

    try:
        response = session.get(
            BASE_URL,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,image/avif,"
                    "image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
            verify=VERIFY_SSL,
            timeout=25,
        )

        print(
            f"RGD initial request: "
            f"{response.status_code} {response.reason}"
        )

        print(
            f"Received {len(response.cookies)} cookie(s)"
        )

        print(
            f"Session contains {len(session.cookies)} cookie(s)"
        )

        return response

    except requests.RequestException as e:
        print(f"RGD session initialization failed: {e}")
        return None


# ============================================================
# Scraper
# ============================================================

def scrape_worker(delay):
    timestamp = datetime.now().strftime(
        "%Y-%m-%d_%H-%M-%S"
    )

    file_name = f"search_{timestamp}.json"

    file_path = os.path.join(
        DATA_DIR,
        file_name
    )

    records = {}

    update_state(
        running=True,
        stop_requested=False,
        filename=file_name,
        status="starting",
        message="Initializing RGD session..."
    )

    session = create_session()

    initialize_session(session)

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": BASE_URL,
        "Referer": BASE_URL + "/",
    }

    consecutive_errors = 0
    total_errors = 0

    print("")
    print("=" * 60)
    print(f"Starting A-Z scrape with {delay}s delay")
    print(f"Maximum consecutive errors: {MAX_CONSECUTIVE_ERRORS}")
    print("=" * 60)
    print("")

    for char in string.ascii_uppercase:

        # ----------------------------------------------------
        # Check for manual stop
        # ----------------------------------------------------

        state = get_state()

        if state["stop_requested"]:
            print("Stop requested. Ending scrape.")

            update_state(
                running=False,
                current_letter=None,
                status="stopped",
                message="Scrape stopped by user.",
                total_records=len(records),
                consecutive_errors=consecutive_errors,
                total_errors=total_errors
            )

            save_records(file_path, records)
            return

        update_state(
            current_letter=char,
            status="scraping",
            message=f"Scraping letter {char}..."
        )

        print(
            f"[{char}] Requesting registry data..."
        )

        payload = {
            "rvr-input-lang": "en",
            "CompanyName": char,
            "searchName": "ns-public-search"
        }

        try:

            response = session.post(
                TARGET_URL,
                json=payload,
                headers=headers,
                verify=VERIFY_SSL,
                timeout=25,
            )

            print(
                f"[{char}] HTTP {response.status_code}"
            )

            # =================================================
            # SUCCESS
            # =================================================

            if response.status_code == 200:

                consecutive_errors = 0

                update_state(
                    consecutive_errors=0,
                    total_errors=total_errors
                )

                try:
                    data = response.json()

                except ValueError:

                    total_errors += 1
                    consecutive_errors += 1

                    print(
                        f"[{char}] Invalid JSON response."
                    )

                    update_state(
                        consecutive_errors=consecutive_errors,
                        total_errors=total_errors
                    )

                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        print(
                            "Maximum consecutive errors reached."
                        )

                        update_state(
                            running=False,
                            current_letter=None,
                            status="error",
                            message=(
                                f"Stopped after "
                                f"{MAX_CONSECUTIVE_ERRORS} "
                                f"consecutive errors."
                            ),
                            total_records=len(records)
                        )

                        save_records(file_path, records)
                        return

                    continue

                results = data.get(
                    "resultset",
                    []
                )

                print(
                    f"[{char}] Received "
                    f"{len(results)} result(s)"
                )

                for item in results:

                    fields = item.get(
                        "fields",
                        {}
                    )

                    record_id = (
                        fields.get("CompanyNumber")
                        or fields.get("irn")
                        or item.get("key")
                    )

                    if not record_id:
                        continue

                    if record_id in records:
                        continue

                    records[record_id] = {
                        "company_number": fields.get(
                            "CompanyNumber"
                        ),

                        "company_name": clean_text(
                            fields.get("CompanyName")
                        ),

                        "record_type": fields.get(
                            "RecordType"
                        ),

                        "record_status": fields.get(
                            "RecordStatus"
                        ),

                        "registration_date": fields.get(
                            "RegistrationDate"
                        ),

                        "address": clean_text(
                            fields.get(
                                "CurrentStreetAddress"
                            )
                        ),

                        "state_area": clean_text(
                            fields.get(
                                "CurrentState"
                            )
                        ),

                        "irn": fields.get("irn")
                    }

                save_records(
                    file_path,
                    records
                )

                print(
                    f"[{char}] Total unique records: "
                    f"{len(records)}"
                )

                update_state(
                    total_records=len(records),
                    completed_letters=[
                        *get_state()["completed_letters"],
                        char
                    ],
                    consecutive_errors=0,
                    total_errors=total_errors
                )

            # =================================================
            # ERROR
            # =================================================

            else:

                consecutive_errors += 1
                total_errors += 1

                print(
                    f"[{char}] ERROR "
                    f"{response.status_code}"
                )

                print(
                    response.text[:1000]
                )

                update_state(
                    consecutive_errors=consecutive_errors,
                    total_errors=total_errors,
                    total_records=len(records),
                    status="error",
                    message=(
                        f"HTTP {response.status_code} "
                        f"on letter {char}. "
                        f"Consecutive errors: "
                        f"{consecutive_errors}/"
                        f"{MAX_CONSECUTIVE_ERRORS}"
                    )
                )

                # ------------------------------------------------
                # Stop after 3 consecutive errors
                # ------------------------------------------------

                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:

                    print("")
                    print("=" * 60)
                    print(
                        f"Stopping after "
                        f"{MAX_CONSECUTIVE_ERRORS} "
                        f"consecutive errors."
                    )
                    print("=" * 60)

                    save_records(
                        file_path,
                        records
                    )

                    update_state(
                        running=False,
                        current_letter=None,
                        status="error",
                        message=(
                            f"Scrape stopped after "
                            f"{MAX_CONSECUTIVE_ERRORS} "
                            f"consecutive errors."
                        ),
                        total_records=len(records)
                    )

                    return

        except requests.RequestException as e:

            consecutive_errors += 1
            total_errors += 1

            print(
                f"[{char}] Request error:"
            )

            print(e)

            update_state(
                consecutive_errors=consecutive_errors,
                total_errors=total_errors,
                total_records=len(records),
                status="error",
                message=(
                    f"Request error on {char}. "
                    f"Consecutive errors: "
                    f"{consecutive_errors}/"
                    f"{MAX_CONSECUTIVE_ERRORS}"
                )
            )

            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:

                print(
                    f"Stopping after "
                    f"{MAX_CONSECUTIVE_ERRORS} "
                    f"consecutive errors."
                )

                save_records(
                    file_path,
                    records
                )

                update_state(
                    running=False,
                    current_letter=None,
                    status="error",
                    message=(
                        f"Scrape stopped after "
                        f"{MAX_CONSECUTIVE_ERRORS} "
                        f"consecutive errors."
                    ),
                    total_records=len(records)
                )

                return

        except Exception as e:

            consecutive_errors += 1
            total_errors += 1

            print(
                f"[{char}] Unexpected error:"
            )

            print(e)

            update_state(
                consecutive_errors=consecutive_errors,
                total_errors=total_errors,
                total_records=len(records)
            )

            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:

                save_records(
                    file_path,
                    records
                )

                update_state(
                    running=False,
                    current_letter=None,
                    status="error",
                    message=(
                        f"Scrape stopped after "
                        f"{MAX_CONSECUTIVE_ERRORS} "
                        f"consecutive errors."
                    ),
                    total_records=len(records)
                )

                return

        # ----------------------------------------------------
        # Delay
        # ----------------------------------------------------

        # Sleep in small increments so the scraper can be
        # manually stopped without waiting for the full delay.

        for _ in range(delay * 10):

            state = get_state()

            if state["stop_requested"]:
                print("Stop requested during delay.")

                save_records(
                    file_path,
                    records
                )

                update_state(
                    running=False,
                    current_letter=None,
                    status="stopped",
                    message="Scrape stopped by user.",
                    total_records=len(records)
                )

                return

            time.sleep(0.1)

    # ========================================================
    # COMPLETE
    # ========================================================

    save_records(
        file_path,
        records
    )

    print("")
    print("=" * 60)
    print("Scrape complete")
    print(
        f"Total unique records: {len(records)}"
    )
    print(
        f"Saved to: {file_path}"
    )
    print("=" * 60)
    print("")

    update_state(
        running=False,
        current_letter=None,
        status="complete",
        message="A-Z scrape completed successfully.",
        total_records=len(records),
        consecutive_errors=0,
        total_errors=total_errors
    )


# ============================================================
# API
# ============================================================

@app.route("/")
def index():
    return send_from_directory(
        BASE_DIR,
        "index.html"
    )


@app.route("/api/files", methods=["GET"])
def list_files():

    files = [
        f
        for f in os.listdir(DATA_DIR)
        if f.endswith(".json")
    ]

    files.sort(reverse=True)

    return jsonify(files)


@app.route(
    "/api/files/<filename>",
    methods=["GET", "DELETE"]
)
def handle_file(filename):

    if os.path.basename(filename) != filename:
        return jsonify({
            "error": "Invalid filename"
        }), 400

    file_path = os.path.join(
        DATA_DIR,
        filename
    )

    if request.method == "DELETE":

        if os.path.exists(file_path):

            os.remove(file_path)

            return jsonify({
                "status": "deleted"
            })

        return jsonify({
            "error": "File not found"
        }), 404

    if os.path.exists(file_path):

        with open(
            file_path,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        return jsonify(data)

    return jsonify({
        "error": "File not found"
    }), 404


# ============================================================
# Start scrape
# ============================================================

@app.route(
    "/api/scrape",
    methods=["POST"]
)
def start_scrape():

    state = get_state()

    if state["running"]:

        return jsonify({
            "error": "A scrape is already running."
        }), 409

    req_data = request.json or {}

    delay = int(
        req_data.get(
            "delay",
            DEFAULT_DELAY
        )
    )

    if delay < 0:
        delay = 0

    reset_state()

    thread = threading.Thread(
        target=scrape_worker,
        args=(delay,),
        daemon=True
    )

    thread.start()

    return jsonify({
        "status": "started"
    })


# ============================================================
# Scrape status
# ============================================================

@app.route(
    "/api/scrape/status",
    methods=["GET"]
)
def scrape_status():

    return jsonify(
        get_state()
    )


# ============================================================
# Stop scrape
# ============================================================

@app.route(
    "/api/scrape/stop",
    methods=["POST"]
)
def stop_scrape():

    state = get_state()

    if not state["running"]:

        return jsonify({
            "status": "not_running",
            "message": "No scrape is currently running."
        })

    update_state(
        stop_requested=True,
        status="stopping",
        message="Stop requested. Finishing current request..."
    )

    return jsonify({
        "status": "stopping"
    })


# ============================================================
# Run
# ============================================================

if __name__ == "__main__":

    print(
        f"Server starting at "
        f"http://localhost:{PORT}"
    )

    app.run(
        host="127.0.0.1",
        port=PORT,
        debug=DEBUG
    )