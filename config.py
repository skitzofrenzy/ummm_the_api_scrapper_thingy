import os
from dotenv import load_dotenv
import urllib3

load_dotenv()

# Server
PORT = int(os.getenv("PORT", 5000))
DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "t")

# Service endpoints (must be provided in env)
TARGET_URL = os.getenv("TARGET_URL")
VALIDATE_URL = os.getenv("VALIDATE_URL")
SESSION_URL = os.getenv("SESSION_URL")
BASE_URL = os.getenv("BASE_URL")

if not TARGET_URL or not VALIDATE_URL or not SESSION_URL:
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
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "searches")
os.makedirs(DATA_DIR, exist_ok=True)

# Suppress insecure warnings when VERIFY_SSL is False
if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
