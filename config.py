# ===== IMPORTS & DEPENDENCIES =====
import os
import logging
import colorlog
from dotenv import load_dotenv
from core.services import APIKeyManager

# ===== INITIALIZATION & STARTUP =====
env_file_path = os.path.join(os.path.dirname(__file__), '.env')
is_loaded = load_dotenv(dotenv_path=env_file_path)

# --- Colored Logging Setup ---
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
if root_logger.hasHandlers():
    root_logger.handlers.clear()

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
handler = colorlog.StreamHandler()
handler.setFormatter(formatter)
root_logger.addHandler(handler)

logging.getLogger('stem').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

if is_loaded:
    logging.info(f"Loaded environment variables from: {env_file_path}")
else:
    logging.error(f"Could not load .env file at: {env_file_path}")

# ===== AI PROVIDER SELECTION =====

# Switch between providers by setting AI_PROVIDER in .env
# Supported values: "gemini" | "vllm"
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()

# --- Gemini (AI_PROVIDER=gemini) ---
# Keys: GEMINI_API_KEY_1, GEMINI_API_KEY_2, … for round-robin rotation
try:
    gemini_api_key_manager = APIKeyManager(env_prefix="GEMINI_API_KEY")
except ValueError:
    gemini_api_key_manager = None

GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-pro")

# --- vLLM / OpenAI-compatible API (AI_PROVIDER=vllm) ---
# Forward the remote port first: ssh -L 8000:localhost:8000 user@server
VLLM_BASE_URL   = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "")
VLLM_API_KEY    = os.getenv("VLLM_API_KEY", "EMPTY")

# Delay (seconds) between consecutive AI calls.
# Gemini free tier: set to 20. Local vLLM: 0 is fine.
AI_CALL_DELAY_SECONDS = int(os.getenv("AI_CALL_DELAY_SECONDS", "0"))

# HTTP proxy for Gemini API calls (e.g. "http://10.16.0.170:2080")
GEMINI_PROXY = os.getenv("GEMINI_PROXY", "") or None
if GEMINI_PROXY:
    os.environ.setdefault("HTTPS_PROXY", GEMINI_PROXY)
    os.environ.setdefault("HTTP_PROXY", GEMINI_PROXY)

# --- Startup validation ---
if AI_PROVIDER == "gemini":
    if not gemini_api_key_manager:
        logging.warning("AI_PROVIDER=gemini but no GEMINI_API_KEY_* found. Phases 2 & 3 will fail.")
    else:
        logging.info(f"AI provider: Gemini | model: {GEMINI_MODEL_NAME} | keys: {gemini_api_key_manager.get_key_count()}")
elif AI_PROVIDER == "vllm":
    if not VLLM_MODEL_NAME:
        logging.warning("AI_PROVIDER=vllm but VLLM_MODEL_NAME is not set. Phases 2 & 3 will fail.")
    else:
        logging.info(f"AI provider: vLLM | model: {VLLM_MODEL_NAME} | endpoint: {VLLM_BASE_URL}")
else:
    logging.error(f"Unknown AI_PROVIDER='{AI_PROVIDER}'. Use 'gemini' or 'vllm'.")

# ===== FORM & AUTOMATION SETTINGS =====

BASE_FORM_URL = os.getenv("BASE_FORM_URL")
if not BASE_FORM_URL:
    logging.critical("BASE_FORM_URL is not set.")
    raise ValueError("BASE_FORM_URL is not set. Please check your .env file.")

HEADLESS_MODE = os.getenv("HEADLESS_MODE", "True").lower() == "true"
SLOW_MO = int(os.getenv("SLOW_MO", "50"))
# "chrome" uses system-installed Google Chrome; None uses Playwright's bundled Chromium
BROWSER_CHANNEL = os.getenv("BROWSER_CHANNEL", "chrome") or None

# ===== TOR SETTINGS =====

USE_TOR = os.getenv("USE_TOR", "False").lower() == "true"
TOR_SOCKS_HOST = os.getenv("TOR_SOCKS_HOST", "127.0.0.1")
TOR_SOCKS_PORT = int(os.getenv("TOR_SOCKS_PORT", "9050"))
TOR_CONTROL_PORT = int(os.getenv("TOR_CONTROL_PORT", "9051"))
TOR_CONTROL_PASSWORD = os.getenv("TOR_CONTROL_PASSWORD", "")
TOR_PROXY_SERVER = f"socks5://{TOR_SOCKS_HOST}:{TOR_SOCKS_PORT}" if USE_TOR else None

# ===== OUTPUT PATHS =====

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
PERSONAS_DIR_NAME     = "personas"
ANSWERS_DIR_NAME      = "answers"
ANSWERS_DONE_DIR_NAME = "done"
RECEIPTS_DIR_NAME     = "receipts"

SCHEMA_FILE_PATH  = os.path.join(OUTPUT_DIR, "form_schema.json")
PERSONAS_DIR_PATH = os.path.join(OUTPUT_DIR, PERSONAS_DIR_NAME)
ANSWERS_DIR_PATH  = os.path.join(OUTPUT_DIR, ANSWERS_DIR_NAME)
RECEIPTS_DIR_PATH = os.path.join(OUTPUT_DIR, RECEIPTS_DIR_NAME)

logging.info("Configuration loaded.")
