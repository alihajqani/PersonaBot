# ===== IMPORTS & DEPENDENCIES =====
import re
import json
import logging
import os
from typing import Any, Dict
from playwright.async_api import Playwright, Error as PlaywrightError
from stem import Signal
from stem.control import Controller
import config

# ===== DIRECTORY & FILE UTILITIES =====
def setup_directories():
    """Create necessary output directories if they don't exist."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    os.makedirs(config.PERSONAS_DIR_PATH, exist_ok=True)
    os.makedirs(config.ANSWERS_DIR_PATH, exist_ok=True)
    os.makedirs(config.RECEIPTS_DIR_PATH, exist_ok=True)
    answers_done_path = os.path.join(config.ANSWERS_DIR_PATH, config.ANSWERS_DONE_DIR_NAME)
    os.makedirs(answers_done_path, exist_ok=True)
    logging.info("Output directories are set up.")

def load_json_file(filename: str, description: str) -> Any:
    """Loads a JSON file with proper error handling."""
    logging.info(f"Loading {description} from '{filename}'...")
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logging.error(f"File not found: '{filename}'.")
        return None
    except json.JSONDecodeError:
        logging.error(f"Invalid JSON format in file: '{filename}'.")
        return None
    
def save_json_file(file_path: str, data: Any, file_description: str = "JSON data"):
    """Saves data to a JSON file with consistent encoding and formatting."""
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logging.info(f"Successfully saved {file_description} to '{file_path}'.")
    except Exception as e:
        logging.error(f"Failed to save {file_description} to '{file_path}'. Error: {e}")

# ===== NETWORK & PARSING UTILITIES =====
def renew_tor_ip():
    """Connects to the Tor Control Port and requests a new IP address."""
    try:
        logging.info("Requesting new IP address from Tor...")
        with Controller.from_port(port=config.TOR_CONTROL_PORT) as controller:
            controller.authenticate(password=config.TOR_CONTROL_PASSWORD)
            if not controller.is_newnym_available():
                logging.warning("Tor is not ready for a new identity yet. Waiting...")
                controller.wait_for_newnym()
            controller.signal(Signal.NEWNYM)
            logging.info("Successfully requested a new IP address from Tor.")
        return True
    except Exception as e:
        logging.error(f"Failed to connect to or interact with Tor Control Port: {e}")
        logging.error("Please ensure Tor is running and the Control Port is enabled and configured correctly.")
        return False

async def log_current_ip_with_tor(p: Playwright):
    """Logs the current public IP address using the Tor proxy and logs it in yellow."""
    launch_options = {
        "headless": True, # Always headless for this quick check
        "proxy": {"server": config.TOR_PROXY_SERVER}
    }
    browser = None
    try:
        browser = await p.chromium.launch(**launch_options)
        page = await browser.new_page()
        await page.goto("https://checkip.amazonaws.com", timeout=30000)
        ip_address = (await page.inner_text('body')).strip()
        #  Log as a warning to get the yellow color
        logging.warning(f"Current IP via Tor: {ip_address}")
    except PlaywrightError as e:
        logging.error(f"Could not check the current IP address. Playwright Error: {e}")
    except Exception as e:
        logging.error(f"Could not check the current IP address. General Error: {e}")
    finally:
        if browser:
            await browser.close()

def normalize_string(text: str) -> str:
    """Normalizes a string for better comparison."""
    return text.strip().rstrip('.,؛').replace('، ', '،')

def extract_id_from_dataparams(data_params: str) -> str | None:
    """Extracts the numeric question ID from the data-params attribute using regex."""
    if not data_params:
        return None
    match = re.search(r',\[\[(\d+),', data_params)
    if match:
        return match.group(1)
    return None