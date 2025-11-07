# ===== IMPORTS & DEPENDENCIES =====
import asyncio
import logging
import os
import shutil
from typing import Dict

from playwright.async_api import async_playwright, Page, Error as PlaywrightError

import config
import utils

# ===== CORE SUBMISSION LOGIC (RE-VALIDATED FOR NEW FORM) =====
# Note: The core selectors for Google Forms were found to be consistent
# with the new HTML provided. This code remains robust.

async def fill_current_page(page: Page, answers: Dict[str, str]):
    """Fills all form fields on the current page based on the provided answers."""
    question_blocks = await page.locator('div.Qr7Oae').all()
    if not question_blocks:
        logging.info("No questions to fill on this page, likely an intro or thank you page. Proceeding.")
        return

    logging.info(f"Attempting to fill {len(question_blocks)} potential fields on the current page...")

    for block in question_blocks:
        jsmodel_div = block.locator('div[jsmodel="CP1oW"]').first
        if not await jsmodel_div.count() > 0:
            continue
        
        data_params = await jsmodel_div.get_attribute('data-params')
        numeric_id = utils.extract_id_from_dataparams(data_params)
        if not numeric_id:
            continue
            
        question_id = f"entry.{numeric_id}"

        if question_id in answers:
            answer = answers[question_id]
            
            # Radio button group
            if await block.locator('div[role="radiogroup"]').count() > 0:
                # Use a robust selector to find the clickable element by its data-value.
                option_to_click = block.locator(f'div[data-value="{answer}"]')
                if await option_to_click.count() > 0:
                    await option_to_click.click()
                    logging.info(f"Filled radio '{question_id}' with: '{answer}'")
                else:
                    logging.warning(f"Could not find radio option '{answer}' for question '{question_id}'")

            # Long answer text area
            elif await block.locator('textarea').count() > 0:
                await block.locator('textarea').fill(answer)
                logging.info(f"Filled textarea '{question_id}'")

            # Short answer text input
            elif await block.locator('input[type="text"]').count() > 0:
                await block.locator('input[type="text"]').fill(answer)
                logging.info(f"Filled text input '{question_id}'")
            
            await page.wait_for_timeout(100) # Brief pause after each action

async def submit_single_form(p: async_playwright, answers: Dict[str, str], persona_id: str) -> bool:
    """
    Opens a browser, fills out and submits the form.
    Returns True on successful submission, False otherwise.
    """
    logging.info(f"Starting form submission for persona: {persona_id}")
    browser = await p.chromium.launch(headless=config.HEADLESS_MODE, slow_mo=config.SLOW_MO)
    context = await browser.new_context()
    page = await context.new_page()
    
    try:
        await page.goto(config.BASE_FORM_URL, wait_until="domcontentloaded")

        page_count = 1
        while True:
            logging.info(f"--- Filling Page {page_count} for persona {persona_id} ---")
            # Wait for any of the key elements to appear before proceeding.
            selector_to_wait_for = 'div.Qr7Oae, div[jsname="OCpkoe"], div[jsname="M2UYVd"]'
            await page.wait_for_selector(selector_to_wait_for, timeout=15000)

            await fill_current_page(page, answers)
            await page.wait_for_timeout(500)

            next_button = page.locator('div[jsname="OCpkoe"]') # Standard 'Next' button
            if await next_button.count() > 0 and await next_button.is_enabled():
                await next_button.click()
                await page.wait_for_load_state("networkidle")
                page_count += 1
            else:
                submit_button = page.locator('div[jsname="M2UYVd"]') # Standard 'Submit' button
                if await submit_button.count() > 0 and await submit_button.is_enabled():
                    logging.info("Final page reached. Clicking 'Submit'...")
                    await submit_button.click()
                    # Wait for the confirmation message div to appear.
                    await page.wait_for_selector('div.vHW8K', timeout=20000) 
                    logging.info(f"Successfully submitted form for persona: {persona_id}")
                    return True
                else:
                    logging.error(f"Could not find an enabled 'Next' or 'Submit' button for {persona_id}.")
                    return False
    except Exception as e:
        logging.error(f"An error occurred while submitting for persona {persona_id}: {e}", exc_info=True)
        return False
    finally:
        await browser.close()
        logging.info(f"Browser closed for persona: {persona_id}")
    
    return False

# ===== RUNNER FUNCTION =====
async def run():
    """Executes Phase 4: Reads all answer files and submits them one by one."""
    logging.info("===== RUNNING PHASE 4: FORM SUBMISSION (GOOGLE FORMS) =====")

    answer_files = [f for f in os.listdir(config.ANSWERS_DIR_PATH) if f.endswith('.json') and not os.path.isdir(os.path.join(config.ANSWERS_DIR_PATH, f))]
    if not answer_files:
        logging.warning("No answer files found in 'output/answers/'. Nothing to submit.")
        return
        
    done_dir_path = os.path.join(config.ANSWERS_DIR_PATH, config.ANSWERS_DONE_DIR_NAME)
    os.makedirs(done_dir_path, exist_ok=True)

    logging.info(f"Found {len(answer_files)} answer sets to submit.")

    async with async_playwright() as p:
        for answer_file in answer_files:
            persona_id = answer_file.replace(".json", "")
            answer_path = os.path.join(config.ANSWERS_DIR_PATH, answer_file)
            
            answers_data = utils.load_json_file(answer_path, f"answers for {persona_id}")
            if not answers_data:
                continue

            was_successful = await submit_single_form(p, answers_data, persona_id)

            if was_successful:
                processed_path = os.path.join(done_dir_path, answer_file)
                shutil.move(answer_path, processed_path)
                logging.info(f"Moved successfully submitted answer file to '{processed_path}'.")
            else:
                logging.error(f"Submission FAILED for {persona_id}. The answer file will NOT be moved and can be retried later.")

            logging.info("Waiting for 10 seconds before next submission...")
            await asyncio.sleep(10)

    logging.info("===== PHASE 4 FINISHED (GOOGLE FORMS) =====")