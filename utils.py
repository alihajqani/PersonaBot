import re
import json
import logging
import os
from typing import Any, Dict

from playwright.async_api import Playwright
from stem import Signal
from stem.control import Controller

import config

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

def renew_tor_ip():
    """
    Connects to the Tor Control Port and requests a new IP address using a 'with' statement
    for robust connection management.
    """
    try:
        logging.info("Requesting new IP address from Tor...")
        # The 'with' statement ensures the connection is ALWAYS closed properly.
        with Controller.from_port(port=config.TOR_CONTROL_PORT) as controller:
            controller.authenticate(password=config.TOR_CONTROL_PASSWORD)
            
            # Check if Tor is ready before sending a signal
            if not controller.is_newnym_available():
                logging.warning("Tor is not ready for a new identity yet. Waiting...")
                controller.wait_for_newnym()

            logging.info("Sending NEWNYM signal to Tor...")
            controller.signal(Signal.NEWNYM)
            logging.info("Successfully requested a new IP address from Tor.")
        return True
    except Exception as e:
        logging.error(f"Failed to connect to or interact with Tor Control Port: {e}")
        logging.error("Please ensure Tor is running and the Control Port is enabled and configured correctly.")
        return False

async def log_current_ip(p: Playwright, launch_options: Dict):
    """Logs the current public IP address using the provided proxy."""
    try:
        browser = await p.chromium.launch(**launch_options)
        page = await browser.new_page()
        await page.goto("https://checkip.amazonaws.com", timeout=30000)
        ip_address = (await page.inner_text('body')).strip()
        logging.info(f"Current IP via proxy: {ip_address}")
    except Exception as e:
        logging.error(f"Could not check the current IP address. Error: {e}")
    finally:
        if 'browser' in locals() and browser:
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