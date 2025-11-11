# ===== IMPORTS & DEPENDENCIES =====
import os
import logging
import colorlog
from dotenv import load_dotenv
from core.services import APIKeyManager

# ===== INITIALIZATION & STARTUP =====
# --- Load Environment Variables from .env file ---
env_file_path = os.path.join(os.path.dirname(__file__), '.env')
is_loaded = load_dotenv(dotenv_path=env_file_path)

# --- ROBUST Application-wide Colored Logging Setup ---
# 1. Get the root logger.
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO) # Set the lowest level to handle.

# 2. CRITICAL STEP: Clear any handlers pre-configured by other libraries.
if root_logger.hasHandlers():
    root_logger.handlers.clear()

# 3. Create a colored formatter.
formatter = colorlog.ColoredFormatter(
    '%(log_color)s%(asctime)s - %(levelname)s - [%(name)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    log_colors={
        'DEBUG':    'cyan',
        'INFO':     'white',
        'WARNING':  'yellow',
        'ERROR':    'red',
        'CRITICAL': 'red,bg_white',
    }
)

# 4. Create a handler to use the colored formatter and add it to the root logger.
handler = colorlog.StreamHandler()
handler.setFormatter(formatter)
root_logger.addHandler(handler)

# 5. Silence noisy libraries by setting their log level higher.
# This ensures only WARNING and above messages from them are processed.
logging.getLogger('stem').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)


# --- Sanity Check for .env file ---
if is_loaded:
    logging.info(f"Successfully loaded environment variables from: {env_file_path}")
    key_check = os.getenv("GOOGLE_API_KEY_1")
    if key_check:
        logging.info("DIAGNOSTIC: GOOGLE_API_KEY_1 was found successfully.")
    else:
        logging.warning("DIAGNOSTIC: .env file was loaded, but GOOGLE_API_KEY_1 was NOT found inside. Check for typos.")
else:
    logging.error(f"CRITICAL FAILURE: Could not find or load the .env file at the expected path: {env_file_path}")
    logging.error("Please ensure the .env file exists in the root directory of the project.")

# ===== CENTRAL SERVICES & CONFIGURATION =====

# --- AI Models API Key Management ---
try:
    google_api_key_manager = APIKeyManager(env_prefix="GOOGLE_API_KEY")
except ValueError as e:
    logging.warning(f"Could not initialize APIKeyManager: {e}. AI-related phases will fail.")
    google_api_key_manager = None

# --- AI Model Settings ---
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-pro")

# --- Form & Automation Settings ---
BASE_FORM_URL = os.getenv("BASE_FORM_URL")
if not BASE_FORM_URL:
    logging.critical("BASE_FORM_URL is not set. Please check your .env file.")
    raise ValueError("BASE_FORM_URL is not set. Please check your .env file.")

# --- Browser & Playwright Settings ---
HEADLESS_MODE = os.getenv("HEADLESS_MODE", "True").lower() == "true"
SLOW_MO = int(os.getenv("SLOW_MO", "50"))

# --- Tor Network Settings ---
USE_TOR = os.getenv("USE_TOR", "False").lower() == "true"
TOR_SOCKS_HOST = os.getenv("TOR_SOCKS_HOST", "127.0.0.1")
TOR_SOCKS_PORT = int(os.getenv("TOR_SOCKS_PORT", "9050"))
TOR_CONTROL_PORT = int(os.getenv("TOR_CONTROL_PORT", "9051"))
TOR_CONTROL_PASSWORD = os.getenv("TOR_CONTROL_PASSWORD", "")
TOR_PROXY_SERVER = f"socks5://{TOR_SOCKS_HOST}:{TOR_SOCKS_PORT}" if USE_TOR else None

# --- Directory & File Path Constants ---
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
PERSONAS_DIR_NAME = "personas"
ANSWERS_DIR_NAME = "answers"
ANSWERS_DONE_DIR_NAME = "done"
RECEIPTS_DIR_NAME = "receipts"

SCHEMA_FILE_PATH = os.path.join(OUTPUT_DIR, "form_schema.json")
PERSONAS_DIR_PATH = os.path.join(OUTPUT_DIR, PERSONAS_DIR_NAME)
ANSWERS_DIR_PATH = os.path.join(OUTPUT_DIR, ANSWERS_DIR_NAME)
RECEIPTS_DIR_PATH = os.path.join(OUTPUT_DIR, RECEIPTS_DIR_NAME)

logging.info("Configuration loaded and services initialized.")