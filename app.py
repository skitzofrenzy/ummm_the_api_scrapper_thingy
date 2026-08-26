import os
import json
import string
import time
import threading
import requests
import urllib3
import re
from urllib.parse import urlparse

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
VALIDATE_URL = os.getenv("VALIDATE_URL")
SESSION_URL = os.getenv("SESSION_URL")
BASE_URL = os.getenv("BASE_URL")

if not TARGET_URL or not VALIDATE_URL or not SESSION_URL:
    raise RuntimeError(
        "TARGET_URL, VALIDATE_URL and SESSION_URL must be set in environment variables "
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
    "records_added_last_request": 0,
    "records_received_last_request": 0,
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
    temporary_path = file_path + ".tmp"

    with open(temporary_path, "w", encoding="utf-8") as f:
        json.dump(
            list(records.values()),
            f,
            indent=2,
            ensure_ascii=False
        )

    os.replace(temporary_path, file_path)


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
            "records_added_last_request": 0,
            "records_received_last_request": 0,
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

    session_url = SESSION_URL

    # First, load the frontend page to establish any base cookies.
    if BASE_URL:
        frontend_url = f"{BASE_URL}/ttNameSearch/"
        try:
            resp_front = session.get(
                frontend_url,
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

            print(f"RGD frontend request: {resp_front.status_code} {resp_front.reason}")

        except requests.RequestException as e:
            print(f"RGD frontend request failed: {e}")

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    if BASE_URL:
        headers["Referer"] = f"{BASE_URL}/ttNameSearch/"

    # Try GET the session endpoint, with a POST fallback, and a couple retries.
    attempts = 3
    for attempt in range(attempts):
        try:
            response = session.get(
                session_url,
                headers=headers,
                verify=VERIFY_SSL,
                timeout=25,
            )

            print(f"RGD session request: {response.status_code} {response.reason}")

            # Show Set-Cookie header if present (shortened)
            sc = response.headers.get("Set-Cookie")
            if sc:
                print(f"Set-Cookie header: {sc[:200]}")

            print(f"Received cookies: {len(response.cookies)}")
            print(f"Session cookies: {len(session.cookies)}")

            for cookie in session.cookies:
                print(f"  {cookie.name}={cookie.value[:20]}...")

            # Try to obtain jwt from response.cookies, session.cookies or Set-Cookie header
            jwt_value = None
            jwt_value = response.cookies.get("jwt") or session.cookies.get("jwt")

            if not jwt_value:
                sc_hdr = response.headers.get("Set-Cookie", "") or ""
                m = re.search(r"\bjwt=([^;\s]+)", sc_hdr)
                if m:
                    jwt_value = m.group(1)

                    # set cookie into the session cookiejar with a domain/path so it will be sent
                    host = urlparse(session_url).hostname
                    try:
                        if host:
                            session.cookies.set("jwt", jwt_value, domain=host, path="/namesearch-server")
                        else:
                            session.cookies.set("jwt", jwt_value)

                        print("Parsed and injected jwt into session.cookies (masked).")
                    except Exception as e:
                        print(f"Failed injecting jwt into session cookies: {e}")

            if session.cookies.get("jwt"):
                print("JWT cookie successfully obtained.")
                return True

            # If no jwt, try POST once per attempt (some servers expect POST)
            try:
                post_resp = session.post(
                    session_url,
                    headers=headers,
                    json={},
                    verify=VERIFY_SSL,
                    timeout=25,
                )

                print(f"RGD session POST: {post_resp.status_code} {post_resp.reason}")

                sc = post_resp.headers.get("Set-Cookie")
                if sc:
                    print(f"Set-Cookie header (POST): {sc[:200]}")

                for cookie in session.cookies:
                    print(f"  {cookie.name}={cookie.value[:20]}...")

                # Check post response for jwt in cookies or headers
                jwt_value = post_resp.cookies.get("jwt") or session.cookies.get("jwt")
                if not jwt_value:
                    sc_post = post_resp.headers.get("Set-Cookie", "") or ""
                    m2 = re.search(r"\bjwt=([^;\s]+)", sc_post)
                    if m2:
                        jwt_value = m2.group(1)
                        host = urlparse(session_url).hostname
                        try:
                            if host:
                                session.cookies.set("jwt", jwt_value, domain=host, path="/namesearch-server")
                            else:
                                session.cookies.set("jwt", jwt_value)
                            print("Parsed and injected jwt into session.cookies from POST (masked).")
                        except Exception as e:
                            print(f"Failed injecting jwt from POST: {e}")

                if session.cookies.get("jwt"):
                    print("JWT cookie successfully obtained (POST).")
                    return True

            except requests.RequestException:
                pass

        except requests.RequestException as e:
            print(f"RGD session initialization failed (attempt {attempt+1}): {e}")

        # small backoff before retrying
        time.sleep(1)

    print("WARNING: JWT cookie was not received after retries.")
    return False


def validate_name(session, name, headers):
    payload = {
        "rvr-input-lang": "en",
        "CompanyName": name,
        "id": "NSPPublicSearch"
    }

    response = session.post(
        VALIDATE_URL,
        json=payload,
        headers=headers,
        verify=VERIFY_SSL,
        timeout=25
    )

    return response


def search_name_reservation(session, name, headers):
    payload = {
        "rvr-input-lang": "en",
        "ProposedName": name,
        "searchName": "ns-name-reservation"
    }

    response = session.post(
        TARGET_URL,
        json=payload,
        headers=headers,
        verify=VERIFY_SSL,
        timeout=25
    )

    return response


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

    if not initialize_session(session):
        update_state(
            running=False,
            status="error",
            message="Failed to initialize session (no JWT)."
        )

        print("Failed to initialize session; aborting scrape.")
        return

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
    }

    if BASE_URL:
        headers["Origin"] = BASE_URL
        headers["Referer"] = f"{BASE_URL}/ttNameSearch/"

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


        print(f"[{char}] Requesting registry data...")

        # ====================================================
        # STEP 1 - Validate
        # ====================================================

        try:
            print(f"[{char}] Validation request...")

            validation_response = validate_name(session, char, headers)

            print(f"[{char}] Validation HTTP {validation_response.status_code}")

            if validation_response.status_code != 200:
                consecutive_errors += 1
                total_errors += 1

                print(f"[{char}] Validation failed: {validation_response.text[:1000]}")

                update_state(
                    consecutive_errors=consecutive_errors,
                    total_errors=total_errors,
                    status="error",
                    message=(
                        f"Validation failed for {char}: HTTP {validation_response.status_code}"
                    )
                )

                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    break

                # brief interruptible delay before retrying
                for _ in range(delay * 10):
                    if get_state()["stop_requested"]:
                        break
                    time.sleep(0.1)

                continue

        except requests.RequestException as e:
            consecutive_errors += 1
            total_errors += 1

            print(f"[{char}] Validation request error:")
            print(e)

            update_state(
                consecutive_errors=consecutive_errors,
                total_errors=total_errors,
                status="error",
                message=(
                    f"Validation request failed for {char}: {e}"
                )
            )

            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                break

            for _ in range(delay * 10):
                if get_state()["stop_requested"]:
                    break
                time.sleep(0.1)

            continue

        # ====================================================
        # STEP 2 - Name reservation search
        # ====================================================

        try:

            print(f"[{char}] Requesting name reservation data...")

            response = search_name_reservation(session, char, headers)

            print(f"[{char}] Search HTTP {response.status_code}")

            # ------------------------------------------------
            # Handle 401 by refreshing JWT and retrying once
            # ------------------------------------------------
            if response.status_code == 401:
                print(f"[{char}] Received 401 — refreshing session and retrying")

                refreshed = initialize_session(session)

                if refreshed:
                    response = search_name_reservation(session, char, headers)
                    print(f"[{char}] Retry HTTP {response.status_code}")
                else:
                    consecutive_errors += 1
                    total_errors += 1
                    update_state(
                        consecutive_errors=consecutive_errors,
                        total_errors=total_errors,
                        total_records=len(records),
                        status="error",
                        message=(
                            f"Failed to refresh session after 401 on {char}."
                        )
                    )

                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        break

                    for _ in range(delay * 10):
                        if get_state()["stop_requested"]:
                            break
                        time.sleep(0.1)

                    continue

            # ------------------------------------------------
            # SUCCESS
            # ------------------------------------------------

            if response.status_code == 200:

                # A successful request resets the consecutive error counter.
                consecutive_errors = 0

                try:
                    data = response.json()
                except ValueError:
                    consecutive_errors += 1
                    total_errors += 1

                    print(f"[{char}] Invalid JSON response.")

                    update_state(
                        consecutive_errors=consecutive_errors,
                        total_errors=total_errors,
                        status="error",
                        message=(
                            f"Invalid JSON response for {char}."
                        )
                    )

                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        break

                    for _ in range(delay * 10):
                        if get_state()["stop_requested"]:
                            break
                        time.sleep(0.1)

                    continue

                results = data.get("resultset", [])

                if not isinstance(results, list):
                    results = []

                print(f"[{char}] API returned {len(results)} result(s)")

                added = 0
                duplicates = 0
                missing_keys = 0

                for item in results:

                    if not isinstance(item, dict):
                        continue

                    record_id = item.get("key")

                    if not record_id:
                        missing_keys += 1
                        continue

                    if record_id in records:
                        duplicates += 1
                        continue

                    # Preserve the original API item
                    records[record_id] = item
                    added += 1

                # SAVE IMMEDIATELY AFTER THIS SEARCH
                save_records(file_path, records)

                print(f"[{char}] New records: {added}")
                print(f"[{char}] Duplicates: {duplicates}")

                if missing_keys:
                    print(f"[{char}] Results without key: {missing_keys}")

                print(f"[{char}] Total unique records: {len(records)}")

                update_state(
                    total_records=len(records),
                    records_added_last_request=added,
                    records_received_last_request=len(results),
                    consecutive_errors=0,
                    total_errors=total_errors,
                    status="scraping",
                    message=(
                        f"{char}: received {len(results)} results, added {added} new records, ignored {duplicates} duplicates."
                    )
                )

                # Add this letter to completed letters.
                current_completed = get_state()["completed_letters"]

                if char not in current_completed:
                    current_completed = [*current_completed, char]

                update_state(completed_letters=current_completed)

            # ------------------------------------------------
            # ERROR
            # ------------------------------------------------

            else:

                consecutive_errors += 1
                total_errors += 1

                print(f"[{char}] ERROR {response.status_code}")
                print(response.text[:2000])

                update_state(
                    consecutive_errors=consecutive_errors,
                    total_errors=total_errors,
                    total_records=len(records),
                    status="error",
                    message=(
                        f"HTTP {response.status_code} on {char}. Consecutive errors: {consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}"
                    )
                )

                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    break

        except requests.RequestException as e:

            consecutive_errors += 1
            total_errors += 1

            print(f"[{char}] Request error:")
            print(e)

            update_state(
                consecutive_errors=consecutive_errors,
                total_errors=total_errors,
                total_records=len(records),
                status="error",
                message=(
                    f"Request error on {char}. Consecutive errors: {consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}"
                )
            )

            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:

                print(f"Stopping after {MAX_CONSECUTIVE_ERRORS} consecutive errors.")

                save_records(file_path, records)

                update_state(
                    running=False,
                    current_letter=None,
                    status="error",
                    message=(
                        f"Scrape stopped after {MAX_CONSECUTIVE_ERRORS} consecutive errors."
                    ),
                    total_records=len(records)
                )

                return

        except Exception as e:

            consecutive_errors += 1
            total_errors += 1

            print(f"[{char}] Unexpected error:")
            print(e)

            update_state(
                consecutive_errors=consecutive_errors,
                total_errors=total_errors,
                total_records=len(records),
            )

            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:

                save_records(file_path, records)

                update_state(
                    running=False,
                    current_letter=None,
                    status="error",
                    message=(
                        f"Scrape stopped after {MAX_CONSECUTIVE_ERRORS} consecutive errors."
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
        "status": "started",
        "delay": delay
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