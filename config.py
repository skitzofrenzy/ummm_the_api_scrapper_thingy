import os
from dotenv import load_dotenv
import urllib3

# Base paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

# Load .env from the project root
load_dotenv(ENV_PATH)

# Server
PORT = int(os.getenv("PORT", 5000))
DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "t")

# Service endpoints (must be provided in env)
TARGET_URL = os.getenv("TARGET_URL")
VALIDATE_URL = os.getenv("VALIDATE_URL")
SESSION_URL = os.getenv("SESSION_URL")
BASE_URL = os.getenv("BASE_URL")

if not TARGET_URL or not VALIDATE_URL or not SESSION_URL:
    # do not raise during runtime reloads; raise only if missing at startup
    raise RuntimeError(
        "TARGET_URL, VALIDATE_URL and SESSION_URL must be set in environment variables "
        "or a local .env file. Do not commit .env to the repository."
    )

# Scraper defaults
DEFAULT_DELAY = int(os.getenv("REQUEST_DELAY", 3))

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Temporary: accept the remote server's certificate issues during development.
VERIFY_SSL = False

# Stop after this many consecutive failed requests
MAX_CONSECUTIVE_ERRORS = int(os.getenv("MAX_CONSECUTIVE_ERRORS", 3))

# File storage
DATA_DIR = os.path.join(BASE_DIR, "searches")
os.makedirs(DATA_DIR, exist_ok=True)

# Suppress insecure warnings when VERIFY_SSL is False
if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def reload_config():
    """Reload the .env file and update module-level config values at runtime.

    Call this after creating or modifying the .env file so the running process
    picks up changes without a restart.
    """
    load_dotenv(ENV_PATH, override=True)

    global PORT, DEBUG, TARGET_URL, VALIDATE_URL, SESSION_URL, BASE_URL
    global DEFAULT_DELAY, USER_AGENT, VERIFY_SSL, MAX_CONSECUTIVE_ERRORS, DATA_DIR

    PORT = int(os.getenv("PORT", PORT))
    DEBUG = os.getenv("DEBUG", str(DEBUG)).lower() in ("true", "1", "t")

    TARGET_URL = os.getenv("TARGET_URL", TARGET_URL)
    VALIDATE_URL = os.getenv("VALIDATE_URL", VALIDATE_URL)
    SESSION_URL = os.getenv("SESSION_URL", SESSION_URL)
    BASE_URL = os.getenv("BASE_URL", BASE_URL)

    DEFAULT_DELAY = int(os.getenv("REQUEST_DELAY", DEFAULT_DELAY))

    USER_AGENT = os.getenv("USER_AGENT", USER_AGENT)

    VERIFY_SSL = os.getenv("VERIFY_SSL", str(VERIFY_SSL)).lower() in ("true", "1", "t")

    MAX_CONSECUTIVE_ERRORS = int(os.getenv("MAX_CONSECUTIVE_ERRORS", MAX_CONSECUTIVE_ERRORS))

    # ensure data dir exists if changed
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)

    if not VERIFY_SSL:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
