# providers/avalform/form_submitter.py

# ===== IMPORTS & DEPENDENCIES =====
import asyncio
import logging
import os
import shutil
from typing import Dict, Any

from playwright.async_api import async_playwright, Page, Error as PlaywrightError

import config
import utils

# ===== CORE SUBMISSION LOGIC =====

async def fill_form_page(page: Page, answers: Dict[str, str], schema_map: Dict[str, Any]):
    """Fills all form fields on the current page using a dual-strategy for radio buttons."""
    logging.info(f"Attempting to fill answers on page: '{await page.title()}'")
    filled_count = 0
    
    for question_id, q_info in schema_map.items():
        if question_id not in answers:
            continue

        answer_value = str(answers[question_id]).strip()
        if not answer_value:
            continue

        q_type = q_info["type"]
        
        try:
            if q_type == "TEXT_INPUT":
                locator = page.locator(f'input[name="{question_id}"], textarea[name="{question_id}"]')
                if await locator.count() > 0:
                    await locator.scroll_into_view_if_needed(timeout=3000)
                    await locator.fill(answer_value)
                    logging.debug(f"Filled TEXT '{question_id}'")
                    filled_count += 1

            elif q_type == "SELECT":
                locator = page.locator(f'select[name="{question_id}"]')
                if await locator.count() > 0:
                    await locator.scroll_into_view_if_needed(timeout=3000)
                    await locator.select_option(value=answer_value)
                    logging.debug(f"Selected OPTION '{answer_value}' for '{question_id}'")
                    filled_count += 1
            
            elif q_type == "RADIO":
                radio_input_locator = page.locator(f'input[name="{question_id}"][value="{answer_value}"]')
                if await radio_input_locator.count() > 0:
                    radio_id = await radio_input_locator.get_attribute('id')
                    if radio_id:
                        label_locator = page.locator(f'label[for="{radio_id}"]')
                        await label_locator.scroll_into_view_if_needed(timeout=3000)
                        await label_locator.click()
                        logging.debug(f"Clicked LABEL for RADIO '{question_id}' with value '{answer_value}'")
                        filled_count += 1

            elif q_type == "MATRIX_RADIO":
                locator = page.locator(f'input[name="{question_id}"][value="{answer_value}"]')
                if await locator.count() > 0:
                    await locator.scroll_into_view_if_needed(timeout=3000)
                    await locator.check()
                    logging.debug(f"Checked INPUT for MATRIX_RADIO '{question_id}' with value '{answer_value}'")
                    filled_count += 1

            await page.wait_for_timeout(50)

        except Exception as e:
            logging.warning(f"Could not interact with element for Q_ID '{question_id}'. Type: {q_type}. Error: {e}")
                
    logging.info(f"Successfully filled {filled_count} questions on this page.")

async def submit_single_form(p: async_playwright, launch_options: Dict, answers: Dict[str, str], schema_map: Dict[str, Any], persona_id: str) -> bool:
    """Opens a browser, fills out, and submits the multi-page form."""
    logging.info(f"Starting form submission for persona: {persona_id}")
    browser = await p.chromium.launch(**launch_options)
    page = await browser.new_page()
    
    try:
        if config.USE_TOR:
            await page.goto("https://checkip.amazonaws.com", timeout=30000)
            ip_address = (await page.inner_text('body')).strip()
            logging.info(f"Current IP for '{persona_id}': {ip_address}")

        await page.goto(config.BASE_FORM_URL, wait_until="networkidle", timeout=60000)
        
        page_count = 1
        while page_count <= 10:
            logging.info(f"--- Processing Page {page_count} for persona {persona_id} ---")
            await fill_form_page(page, answers, schema_map)

            final_submit_button = page.locator('input.button_text.btn_primary[value="ارسال"]')
            if await final_submit_button.count() > 0 and await final_submit_button.is_visible():
                logging.info("Final page detected. Clicking 'ارسال' to submit.")
                await final_submit_button.click()
                break

            next_button = page.locator('input.button_text.btn_primary[name="submit_primary"]')
            if await next_button.count() > 0 and await next_button.is_visible():
                logging.info("Found 'ادامه' button. Navigating to the next page.")
                await next_button.click()
                await page.wait_for_load_state("networkidle", timeout=20000)
                page_count += 1
            else:
                logging.error("Could not find a visible 'Next' or 'Submit' button. Form flow is broken.")
                return False

        # This targets the h2 that is a direct child of the div, which is unique.
        confirmation_locator = page.locator('div.form_success > h2:has-text("پاسخ شما با موفقیت ثبت شد")')
        
        await confirmation_locator.wait_for(state="visible", timeout=30000)
        
        # Now that we've confirmed the unique element is visible, we can safely consider it a success.
        logging.info("Successfully submitted form. Confirmation element verified.")
        await page.screenshot(path=os.path.join(config.RECEIPTS_DIR_PATH, f"{persona_id}_success.png"))
        return True

    except Exception as e:
        logging.error(f"An error occurred during submission for persona {persona_id}: {e}", exc_info=True)
        await page.screenshot(path=os.path.join(config.RECEIPTS_DIR_PATH, f"{persona_id}_error.png"))
        return False
    finally:
        await browser.close()
        logging.info(f"Browser closed for persona: {persona_id}")

# ===== RUNNER FUNCTION =====
async def run():
    """Executes Phase 4: Reads answer files and submits them one by one using Avalform logic."""
    logging.info("===== RUNNING PHASE 4: FORM SUBMISSION (AVALFORM) =====")

    schema_data = utils.load_json_file(config.SCHEMA_FILE_PATH, "form schema")
    if not schema_data:
        logging.error("Schema file not found. Please run Phase 1 first. Aborting.")
        return
        
    answer_files = [f for f in os.listdir(config.ANSWERS_DIR_PATH) if f.endswith('.json') and not os.path.isdir(os.path.join(config.ANSWERS_DIR_PATH, f))]
    if not answer_files:
        logging.warning("No answer files found in 'output/answers/'. Nothing to submit.")
        return
        
    done_dir_path = os.path.join(config.ANSWERS_DIR_PATH, config.ANSWERS_DONE_DIR_NAME)
    os.makedirs(done_dir_path, exist_ok=True)
    
    schema_map = {item['question_id']: item for item in schema_data}
    logging.info(f"Found {len(answer_files)} answer sets to submit.")

    async with async_playwright() as p:
        for answer_file in answer_files:
            persona_id = answer_file.replace(".json", "")
            answer_path = os.path.join(config.ANSWERS_DIR_PATH, answer_file)
            
            answers_data = utils.load_json_file(answer_path, f"answers for {persona_id}")
            if not answers_data:
                continue

            launch_options = {"headless": config.HEADLESS_MODE, "slow_mo": config.SLOW_MO}
            if config.USE_TOR:
                if utils.renew_tor_ip():
                    logging.info("Waiting 5 seconds for new Tor circuit...")
                    await asyncio.sleep(5)
                    launch_options["proxy"] = {"server": config.TOR_PROXY_SERVER}
                else:
                    logging.error(f"Failed to renew Tor IP. Aborting submission for {persona_id}.")
                    continue

            was_successful = await submit_single_form(p, launch_options, answers_data, schema_map, persona_id)

            if was_successful:
                processed_path = os.path.join(done_dir_path, answer_file)
                shutil.move(answer_path, processed_path)
                logging.info(f"Moved successfully submitted answer file to '{processed_path}'.")
            else:
                logging.error(f"Submission FAILED for {persona_id}. The answer file will NOT be moved.")

            delay = int(os.getenv("SUBMISSION_DELAY_SECONDS", "15"))
            logging.info(f"Waiting for {delay} seconds before next submission...")
            await asyncio.sleep(delay)

    logging.info("===== PHASE 4 FINISHED (AVALFORM) =====")