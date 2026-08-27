import os
import json
import threading
from flask import Flask, jsonify, request, send_from_directory

import config
import state
from utils import save_records
import session_client
from worker import scrape_worker


app = Flask(__name__, static_folder=".")


@app.route("/")
def index():
    return send_from_directory(config.BASE_DIR, "index.html")


@app.route("/api/files", methods=["GET"])
def list_files():
    files = [f for f in os.listdir(config.DATA_DIR) if f.endswith(".json")]
    files.sort(reverse=True)
    return jsonify(files)


@app.route("/api/files/<filename>", methods=["GET", "DELETE"])
def handle_file(filename):
    if os.path.basename(filename) != filename:
        return jsonify({"error": "Invalid filename"}), 400

    file_path = os.path.join(config.DATA_DIR, filename)

    if request.method == "DELETE":
        if os.path.exists(file_path):
            os.remove(file_path)
            return jsonify({"status": "deleted"})
        return jsonify({"error": "File not found"}), 404

    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return jsonify(data)
        except (json.JSONDecodeError, OSError) as e:
            return jsonify({"error": str(e)}), 500

    return jsonify({"error": "File not found"}), 404


@app.route("/api/scrape", methods=["POST"])
def start_scrape():
    state_data = state.get_state()
    if state_data["running"]:
        return jsonify({"error": "A scrape is already running."}), 409

    req_data = request.get_json(silent=True) or {}
    try:
        delay = int(req_data.get("delay", config.DEFAULT_DELAY))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid delay."}), 400

    if delay < 0:
        delay = 0

    state.reset_state()

    thread = threading.Thread(target=scrape_worker, args=(delay,), daemon=True)
    thread.start()

    return jsonify({"status": "started", "delay": delay})


@app.route("/api/scrape/status", methods=["GET"])
def scrape_status():
    return jsonify(state.get_state())


@app.route("/api/scrape/stop", methods=["POST"])
def stop_scrape():
    st = state.get_state()
    if not st["running"]:
        return jsonify({"status": "not_running", "message": "No scrape is currently running."})

    state.update_state(stop_requested=True, status="stopping", message="Stop requested. Finishing current request...")
    return jsonify({"status": "stopping"})


# Debug endpoint: returns masked cookie names from the last session (no sensitive values)
@app.route("/api/debug/session-cookies", methods=["GET"])
def debug_session_cookies():
    sess = session_client.get_last_session()
    if not sess:
        return jsonify({"cookies": None})

    out = {}
    for c in sess.cookies:
        # mask cookie value but show first/last few chars and length
        val = c.value or ""
        if len(val) > 8:
            masked = val[:4] + "..." + val[-4:]
        else:
            masked = "*" * len(val)
        out[c.name] = {"masked": masked, "len": len(val), "domain": c.domain, "path": c.path}

    return jsonify({"cookies": out})
