import os
import string
import time
from datetime import datetime
import requests
import threading

import config
import state
import session_client
from utils import save_records


def scrape_worker(delay):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_name = f"search_{timestamp}.json"
    file_path = os.path.join(config.DATA_DIR, file_name)

    records = {}

    state.update_state(
        running=True,
        stop_requested=False,
        filename=file_name,
        status="starting",
        message="Initializing RGD session...",
    )

    session = session_client.create_session()

    if not session_client.initialize_session(session):
        state.update_state(running=False, status="error", message="Failed to initialize session (no JWT).")
        print("Failed to initialize session; aborting scrape.")
        return

    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
    }

    if config.BASE_URL:
        headers["Origin"] = config.BASE_URL
        headers["Referer"] = f"{config.BASE_URL}/ttNameSearch/"

    consecutive_errors = 0
    total_errors = 0

    print("\n" + "=" * 60)
    print(f"Starting A-Z scrape with {delay}s delay")
    print(f"Maximum consecutive errors: {config.MAX_CONSECUTIVE_ERRORS}")
    print("=" * 60 + "\n")

    for char in string.ascii_uppercase:
        if state.get_state()["stop_requested"]:
            print("Stop requested. Ending scrape.")
            state.update_state(running=False, current_letter=None, status="stopped", message="Scrape stopped by user.", total_records=len(records), consecutive_errors=consecutive_errors, total_errors=total_errors)
            save_records(file_path, records)
            return

        state.update_state(current_letter=char, status="scraping", message=f"Scraping letter {char}...")

        # STEP 1 - Validate
        try:
            validation_response = session_client.validate_name(session, char, headers)
            if validation_response.status_code != 200:
                consecutive_errors += 1
                total_errors += 1
                state.update_state(consecutive_errors=consecutive_errors, total_errors=total_errors, status="error", message=f"Validation failed for {char}: HTTP {validation_response.status_code}")
                if consecutive_errors >= config.MAX_CONSECUTIVE_ERRORS:
                    break
                for _ in range(delay * 10):
                    if state.get_state()["stop_requested"]:
                        break
                    time.sleep(0.1)
                continue

        except requests.RequestException as e:
            consecutive_errors += 1
            total_errors += 1
            state.update_state(consecutive_errors=consecutive_errors, total_errors=total_errors, status="error", message=f"Validation request failed for {char}: {e}")
            if consecutive_errors >= config.MAX_CONSECUTIVE_ERRORS:
                break
            for _ in range(delay * 10):
                if state.get_state()["stop_requested"]:
                    break
                time.sleep(0.1)
            continue

        # STEP 2 - Search
        try:
            response = session_client.search_name_reservation(session, char, headers)

            # refresh on 401 and retry once
            if response.status_code == 401:
                refreshed = session_client.initialize_session(session)
                if refreshed:
                    response = session_client.search_name_reservation(session, char, headers)
                else:
                    consecutive_errors += 1
                    total_errors += 1
                    state.update_state(consecutive_errors=consecutive_errors, total_errors=total_errors, total_records=len(records), status="error", message=f"Failed to refresh session after 401 on {char}.")
                    if consecutive_errors >= config.MAX_CONSECUTIVE_ERRORS:
                        break
                    for _ in range(delay * 10):
                        if state.get_state()["stop_requested"]:
                            break
                        time.sleep(0.1)
                    continue

            if response.status_code == 200:
                consecutive_errors = 0
                try:
                    data = response.json()
                except ValueError:
                    consecutive_errors += 1
                    total_errors += 1
                    state.update_state(consecutive_errors=consecutive_errors, total_errors=total_errors, status="error", message=f"Invalid JSON response for {char}.")
                    if consecutive_errors >= config.MAX_CONSECUTIVE_ERRORS:
                        break
                    for _ in range(delay * 10):
                        if state.get_state()["stop_requested"]:
                            break
                        time.sleep(0.1)
                    continue

                results = data.get("resultset", [])
                if not isinstance(results, list):
                    results = []

                added = 0
                duplicates = 0
                missing_keys = 0

                def get_field(obj, *keys):
                    if not obj or not isinstance(obj, dict):
                        return None
                    for k in keys:
                        if k in obj:
                            return obj[k]
                    return None

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

                    # Normalize top-level fields for easier UI consumption
                    normalized = dict(item)
                    fields = item.get("fields", {}) or {}

                    company = get_field(fields, 'ProposedName', 'proposedName', 'CompanyName', 'company_name') or ''
                    record_status = get_field(fields, 'ReservationStatus', 'reservationStatus', 'Reservation') or ''
                    registration_date = get_field(fields, 'ExpiryDate', 'expiryDate', 'RegistrationDate', 'registration_date') or ''
                    address = get_field(
                        fields,
                        'Address', 'address', 'RegisteredAddress', 'PostalAddress', 'PhysicalAddress',
                        'AddressLine1', 'AddrLine1', 'StreetAddress', 'address1', 'Address1',
                        'PremiseAddress', 'location', 'Location', 'Premise', 'site_address', 'registration_address'
                    ) or ''

                    normalized['company_name'] = company
                    normalized['record_status'] = record_status
                    normalized['registration_date'] = registration_date
                    normalized['address'] = address

                    # Preserve commonly expected fields at top-level for easier consumption
                    expected_keys = [
                        'CompanyIdentifier', 'CompanyName', 'CompanyNumber',
                        'CurrentBuilding', 'CurrentState', 'CurrentStreetAddress', 'CurrentTown',
                        'irn', 'RecordStatus', 'RecordType', 'RegistrationDate'
                    ]

                    for k in expected_keys:
                        # Prefer the value from fields, fall back to existing normalized value, else empty string
                        normalized[k] = fields.get(k, normalized.get(k, ''))

                    records[record_id] = normalized
                    added += 1

                save_records(file_path, records)

                state.update_state(total_records=len(records), records_added_last_request=added, records_received_last_request=len(results), consecutive_errors=0, total_errors=total_errors, status="scraping", message=(f"{char}: received {len(results)} results, added {added} new records, ignored {duplicates} duplicates."))

                current_completed = state.get_state()["completed_letters"]
                if char not in current_completed:
                    current_completed = [*current_completed, char]
                state.update_state(completed_letters=current_completed)

            else:
                consecutive_errors += 1
                total_errors += 1
                state.update_state(consecutive_errors=consecutive_errors, total_errors=total_errors, total_records=len(records), status="error", message=(f"HTTP {response.status_code} on {char}. Consecutive errors: {consecutive_errors}/{config.MAX_CONSECUTIVE_ERRORS}"))
                if consecutive_errors >= config.MAX_CONSECUTIVE_ERRORS:
                    break

        except requests.RequestException as e:
            consecutive_errors += 1
            total_errors += 1
            state.update_state(consecutive_errors=consecutive_errors, total_errors=total_errors, total_records=len(records), status="error", message=(f"Request error on {char}. Consecutive errors: {consecutive_errors}/{config.MAX_CONSECUTIVE_ERRORS}"))
            if consecutive_errors >= config.MAX_CONSECUTIVE_ERRORS:
                save_records(file_path, records)
                state.update_state(running=False, current_letter=None, status="error", message=(f"Scrape stopped after {config.MAX_CONSECUTIVE_ERRORS} consecutive errors."), total_records=len(records))
                return

        # Delay with interruptible sleep
        for _ in range(delay * 10):
            if state.get_state()["stop_requested"]:
                save_records(file_path, records)
                state.update_state(running=False, current_letter=None, status="stopped", message="Scrape stopped by user.", total_records=len(records))
                return
            time.sleep(0.1)

    # Complete
    save_records(file_path, records)
    state.update_state(running=False, current_letter=None, status="complete", message="A-Z scrape completed successfully.", total_records=len(records), consecutive_errors=0, total_errors=total_errors)
