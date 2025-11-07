# ===== IMPORTS & DEPENDENCIES =====
import asyncio
import logging
from typing import List, Dict, Any

from playwright.async_api import async_playwright, Page, Error as PlaywrightError

import config
import utils

# ===== CORE SCHEMA EXTRACTION LOGIC (RE-VALIDATED FOR NEW FORM) =====
# Note: The core selectors for Google Forms were found to be consistent
# with the new HTML provided. This code remains robust.

async def fill_required_and_parse_page(page: Page) -> List[Dict[str, Any]]:
    """
    Parses questions on the current page, filling ALL fields found to ensure navigation.
    """
    questions_on_page = []
    
    question_blocks = await page.locator('div.Qr7Oae').all()
    
    if not question_blocks:
        logging.warning("No question blocks found on this page. This might be an end page or an intro page.")
        return []
        
    logging.info(f"Found {len(question_blocks)} potential question blocks. Iterating...")

    for i, block in enumerate(question_blocks):
        # This selector robustly finds the main question text.
        question_text_element = block.locator('div[role="heading"]').first
        if not await question_text_element.count():
            continue
            
        full_question_text = await question_text_element.inner_text()
        question_text = full_question_text.strip().replace(" *", "")

        # This div contains the crucial data-params attribute for ID extraction.
        jsmodel_div = block.locator('div[jsmodel="CP1oW"]').first
        if not await jsmodel_div.count() > 0:
            logging.debug(f"Block for '{question_text}' is not a standard question block. Skipping.")
            continue
            
        data_params = await jsmodel_div.get_attribute('data-params')
        numeric_id = utils.extract_id_from_dataparams(data_params)

        if not numeric_id:
            logging.warning(f"Could not extract numeric ID for question '{question_text}'. Skipping.")
            continue
            
        question_id = f"entry.{numeric_id}"
        logging.info(f"Processing Question: '{question_text}' | ID: '{question_id}'")

        q_type = "UNKNOWN"
        options = []

        # --- Determine Question Type, Extract Options, and Fill ALL Fields ---
        if await block.locator('div[role="radiogroup"]').count() > 0:
            q_type = "RADIO"
            option_labels = await block.locator('label').all()
            for label in option_labels:
                radio_div = label.locator('div[role="radio"]')
                text_span = label.locator('span.aDTYNe')
                if await radio_div.count() > 0 and await text_span.count() > 0:
                    value = await radio_div.get_attribute('data-value')
                    text = await text_span.inner_text()
                    options.append({"text": text.strip(), "value": value.strip()})

            # Fill the first option to ensure we can proceed to the next page.
            if option_labels:
                logging.info(f"  -> Action: Filling radio field '{question_text}' with first option.")
                await option_labels[0].locator('div[role="radio"]').click()

        elif await block.locator('textarea').count() > 0:
            q_type = "TEXT_AREA"
            logging.info(f"  -> Action: Filling textarea field '{question_text}'.")
            await block.locator('textarea').fill("dummy text")
        
        elif await block.locator('input[type="text"]').count() > 0:
            q_type = "TEXT_INPUT"
            logging.info(f"  -> Action: Filling text input field '{question_text}'.")
            await block.locator('input[type="text"]').fill("25")
        
        if q_type != "UNKNOWN":
            logging.info(f"  -> Success: Parsed as {q_type}.")
            questions_on_page.append({
                "question_id": question_id,
                "question_text": question_text,
                "type": q_type,
                "options": options
            })

    return questions_on_page


async def extract_google_form_schema(p: async_playwright) -> List[Dict[str, Any]]:
    logging.info("Starting schema extraction from live Google Form URL.")
    
    browser = await p.chromium.launch(headless=config.HEADLESS_MODE, slow_mo=config.SLOW_MO)
    page = await browser.new_page()
    form_schema = []
    
    try:
        logging.info(f"Navigating to form URL: {config.BASE_FORM_URL}")
        await page.goto(config.BASE_FORM_URL, wait_until="domcontentloaded", timeout=60000)

        page_count = 1
        while True:
            logging.info(f"--- Processing Page {page_count} ---")
            
            try:
                logging.info("Waiting for page content to stabilize...")
                # This selector combination waits for questions, the next button, or the submit button.
                await page.wait_for_selector('div.Qr7Oae, div[jsname="M2UYVd"], div[jsname="OCpkoe"]', timeout=15000)
                logging.info("Page content is ready.")
            except PlaywrightError:
                logging.warning("Timed out waiting for page content. Assuming it's the end of the form.")
                break

            page_questions = await fill_required_and_parse_page(page)
            form_schema.extend(page_questions)
            
            await page.wait_for_timeout(500) # Small delay for stability

            next_button = page.locator('div[jsname="OCpkoe"]')
            
            if await next_button.count() > 0 and await next_button.is_enabled():
                logging.info("Found enabled 'Next' button. Navigating to the next page...")
                await next_button.click()
                await page.wait_for_load_state("networkidle", timeout=30000)
                page_count += 1
            else:
                submit_button = page.locator('div[jsname="M2UYVd"]')
                if await submit_button.count() > 0:
                    logging.info("Found 'Submit' button. This is the final page. Extraction finished.")
                else:
                    logging.warning("No enabled 'Next' or 'Submit' button found. This marks the end of the form.")
                break
                
        logging.info(f"Schema extraction complete. Found {len(form_schema)} questions across {page_count} pages.")

    except PlaywrightError as e:
        logging.error(f"A Playwright error occurred during schema extraction: {e}", exc_info=True)
    except Exception as e:
        logging.error(f"An unexpected error occurred during schema extraction: {e}", exc_info=True)
    finally:
        await browser.close()
        logging.info("Browser closed.")

    return form_schema

# ===== RUNNER FUNCTION =====
async def run():
    """Executes Phase 1 for Google Forms: Extracts the schema from the live URL."""
    logging.info("===== RUNNING PHASE 1: SCHEMA EXTRACTION (GOOGLE FORMS - LIVE) =====")
    
    async with async_playwright() as p:
        schema_data = await extract_google_form_schema(p)
    
    if not schema_data:
        logging.error("Schema extraction failed. No questions were found. Aborting.")
        return

    utils.save_json_file(config.SCHEMA_FILE_PATH, schema_data, "form schema")

    logging.info("===== PHASE 1 FINISHED (GOOGLE FORMS) =====")