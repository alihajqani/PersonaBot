# providers/avalform/schema_extractor.py

# ===== IMPORTS & DEPENDENCIES =====
import asyncio
import json
import logging
import os
import random
from typing import List, Dict, Any

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page, Error as PlaywrightError

import config
import utils

# ===== UTILITY FUNCTIONS FOR SCHEMA EXTRACTION =====

def parse_simple_question(li_tag: BeautifulSoup) -> Dict[str, Any] | None:
    """Parses text inputs, simple radio buttons, and dropdowns from a BeautifulSoup tag."""
    description_tag = li_tag.find('label', class_='description') or li_tag.find('span', class_='description')
    if not description_tag:
        return None

    question_text = ' '.join(description_tag.get_text(strip=True).replace('*', '').split())
    
    input_tag = li_tag.find('input') or li_tag.find('select') or li_tag.find('textarea')
    if not (input_tag and input_tag.has_attr('name')):
        return None
    
    question_id = input_tag['name']
    q_type = "UNKNOWN"
    options = []
    
    if input_tag.name == 'select':
        q_type = "SELECT"
        option_tags = li_tag.find_all('option')
        for opt in option_tags:
            if opt.has_attr('value') and opt['value']:
                options.append({"text": opt.get_text(strip=True), "value": opt['value']})
    elif input_tag.has_attr('type') and input_tag['type'] == 'radio':
        q_type = "RADIO"
        choice_labels = li_tag.find_all('label', class_='choice')
        for label in choice_labels:
            radio_input = label.find_previous_sibling('input')
            if radio_input and radio_input.has_attr('value'):
                options.append({"text": label.get_text(strip=True), "value": radio_input['value']})
    elif input_tag.name == 'textarea' or (input_tag.has_attr('type') and input_tag['type'] == 'text'):
        q_type = "TEXT_INPUT"
        
    if q_type != "UNKNOWN":
        return {
            "question_id": question_id,
            "question_text": question_text,
            "type": q_type,
            "options": options,
        }
    return None

def parse_matrix_question(li_tag: BeautifulSoup) -> List[Dict[str, Any]]:
    """Parses complex matrix (table-based) questions from a BeautifulSoup tag."""
    sub_questions = []
    headers = li_tag.select('thead th[id^="mc_"]')
    options = [{"text": th.get_text(strip=True), "value": str(i + 1)} for i, th in enumerate(headers)]
    rows = li_tag.select('tbody tr')
    for row in rows:
        question_cell = row.find('td', class_='first_col')
        first_input = row.find('input', {'type': 'radio'})
        if question_cell and first_input and first_input.has_attr('name'):
            question_text = ' '.join(question_cell.get_text(strip=True).split())
            question_id = first_input['name']
            sub_questions.append({
                "question_id": question_id,
                "question_text": question_text,
                "type": "MATRIX_RADIO",
                "options": options,
            })
    return sub_questions

async def fill_all_visible_inputs(page: Page):
    """
    Intelligently fills all visible inputs to satisfy 'required' constraints.
    Uses a dual-strategy for radio buttons to handle both simple and matrix types.
    """
    logging.info("Attempting to intelligently fill all visible inputs to proceed...")
    NUMERIC_KEYWORDS = ["سن", "عدد", "تعداد", "شماره", "رقم"]

    # Fill text inputs
    text_inputs = await page.locator('input[type="text"]:visible, textarea:visible').all()
    for input_el in text_inputs:
        try:
            parent_li = input_el.locator('xpath=./ancestor::li[1]')
            label_text = ""
            if await parent_li.count() > 0:
                label_element = parent_li.locator('label.description, span.description').first
                if await label_element.count() > 0:
                    label_text = await label_element.inner_text()
            
            fill_value = "dummy_text"
            if any(keyword in label_text for keyword in NUMERIC_KEYWORDS):
                fill_value = str(random.randint(20, 50))
            
            await input_el.fill(fill_value, timeout=2000)
        except Exception as e:
            logging.warning(f"Could not fill a text input. Error: {e}")

    # Fill selects
    selects = await page.locator('select:visible').all()
    for select_el in selects:
        try:
            all_options = await select_el.locator('option').all()
            for option in all_options:
                value = await option.get_attribute('value')
                if value:
                    await select_el.select_option(value=value)
                    break
        except Exception as e:
            logging.warning(f"Could not select an option for a dropdown. Error: {e}")

    # ⭐ DUAL-STRATEGY FOR RADIO BUTTONS
    all_radios = await page.locator('input[type="radio"]:visible').all()
    processed_groups = set()
    for radio_input in all_radios:
        name = await radio_input.get_attribute('name')
        if name and name not in processed_groups:
            try:
                # STRATEGY 1: Try clicking the label first (for simple radio buttons)
                radio_id = await radio_input.get_attribute('id')
                if radio_id:
                    label_locator = page.locator(f'label[for="{radio_id}"]')
                    if await label_locator.is_visible(timeout=1000):
                        await label_locator.click(timeout=3000)
                        logging.debug(f"Clicked LABEL for radio group '{name}'.")
                        processed_groups.add(name)
                        continue # Skip to next radio group
                
                # STRATEGY 2: Fallback to direct check (for matrix radio buttons with hidden labels)
                await radio_input.check(timeout=3000)
                logging.debug(f"Checked INPUT directly for radio group '{name}'.")
                processed_groups.add(name)

            except Exception as e:
                logging.warning(f"Could not interact with radio button group '{name}'. Error: {e}")
            
    logging.info(f"Filled {len(text_inputs)} text fields, {len(selects)} dropdowns, and {len(processed_groups)} radio groups.")


# ===== CORE SCHEMA EXTRACTION LOGIC =====

async def extract_avalform_schema(p: async_playwright) -> List[Dict[str, Any]]:
    logging.info("Starting schema extraction from Avalform URL.")
    browser = await p.chromium.launch(headless=config.HEADLESS_MODE, slow_mo=config.SLOW_MO)
    page = await browser.new_page()
    form_schema = []
    
    try:
        await page.goto(config.BASE_FORM_URL, wait_until="networkidle", timeout=60000)
        logging.info(f"Initial page loaded: {await page.title()}")
        page_count = 1
        processed_ids = set()

        while True:
            logging.info(f"--- Processing Page {page_count} ---")
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            question_tags = soup.select('form > ul > li[id^="li_"]')
            for li_tag in question_tags:
                if 'buttons' in li_tag.get('class', []): continue
                if 'matrix' in li_tag.get('class', []):
                    matrix_questions = parse_matrix_question(li_tag)
                    for q in matrix_questions:
                        if q['question_id'] not in processed_ids:
                            form_schema.append(q)
                            processed_ids.add(q['question_id'])
                else:
                    simple_question = parse_simple_question(li_tag)
                    if simple_question and simple_question['question_id'] not in processed_ids:
                        form_schema.append(simple_question)
                        processed_ids.add(simple_question['question_id'])
            
            await fill_all_visible_inputs(page)
            await asyncio.sleep(1)

            next_button = page.locator('input.button_text.btn_primary[name="submit_primary"]')
            final_submit_button = page.locator('input.button_text.btn_primary[value="ارسال"]')

            if await next_button.count() > 0 and await next_button.is_visible():
                logging.info("Found 'Next' button. Clicking to navigate...")
                await next_button.click()
                await page.wait_for_load_state("networkidle", timeout=20000)
                page_count += 1
            elif await final_submit_button.count() > 0 and await final_submit_button.is_visible():
                logging.info("Found final 'Submit' button. Extraction is complete.")
                break
            else:
                logging.warning("No visible 'Next' or 'Submit' button found. Assuming end of form.")
                break
                
        logging.info(f"Schema extraction complete. Found {len(form_schema)} unique questions.")
    except Exception as e:
        logging.error(f"An unexpected error occurred during schema extraction: {e}", exc_info=True)
    finally:
        await browser.close()
    return form_schema

# ===== RUNNER FUNCTION =====
async def run():
    logging.info("===== RUNNING PHASE 1: SCHEMA EXTRACTION (AVALFORM - LIVE) =====")
    async with async_playwright() as p:
        schema_data = await extract_avalform_schema(p)
    if not schema_data:
        logging.error("Schema extraction failed. No questions were found. Aborting.")
        return
    utils.save_json_file(config.SCHEMA_FILE_PATH, schema_data, "form schema")
    logging.info("===== PHASE 1 FINISHED (AVALFORM) =====")