import threading

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
