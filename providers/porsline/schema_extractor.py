# providers/porsline/schema_extractor.py

# ===== IMPORTS & DEPENDENCIES =====
import asyncio
import logging
import random
import re
from typing import List, Dict, Any, Set
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page

import config
import utils

# ===== HELPER: GET VISIBLE IDs =====

async def get_visible_question_ids(page: Page) -> Set[str]:
    """
    Returns a set of question IDs currently visible on the screen.
    This is crucial to detect if the page has auto-advanced.
    """
    try:
        # Porsline labels have 'for="question-XXXX"'
        # We only want visible labels to know current page state
        labels = await page.locator('label[for^="question-"]').all()
        ids = set()
        for lbl in labels:
            if await lbl.is_visible():
                raw_attr = await lbl.get_attribute('for')
                if raw_attr:
                    ids.add(raw_attr.replace('question-', ''))
        return ids
    except:
        return set()

# ===== HELPER: INTELLIGENT FILLER =====

async def interact_with_page(page: Page) -> str:
    """
    Fills fields and detects if an action caused auto-navigation.
    Returns: 'AUTO_ADVANCED', 'FILLED_WAITING', or 'NOTHING_DONE'
    """
    logging.info("   -> Interacting with fields...")
    
    # 1. RADIO / PICTURE CHOICE (High Priority - Triggers Auto Advance)
    # We look for containers that act as radio groups
    radio_options = await page.locator('div[role="radio"], div[class*="choice_item"]').all()
    visible_radios = [r for r in radio_options if await r.is_visible()]
    
    if visible_radios:
        try:
            # Pick one and click
            choice = random.choice(visible_radios)
            await choice.click(force=True)
            logging.info("      Clicked a radio option.")
            return 'AUTO_ADVANCED_LIKELY'
        except: pass

    # 2. RATING / SCALES
    rating_options = await page.locator('div[class*="rating_item"]').all()
    visible_ratings = [r for r in rating_options if await r.is_visible()]
    if visible_ratings:
        try:
            await random.choice(visible_ratings).click(force=True)
            logging.info("      Clicked a rating option.")
            return 'AUTO_ADVANCED_LIKELY'
        except: pass

    # 3. TEXT INPUTS / TEXTAREA (Does NOT trigger Auto Advance usually)
    inputs = await page.locator('input:visible, textarea:visible').all()
    filled_text = False
    for inp in inputs:
        try:
            type_attr = await inp.get_attribute("type") or ""
            if type_attr in ["checkbox", "radio", "hidden", "file", "range", "submit", "button", "image"]: 
                continue
            
            val = await inp.input_value()
            if not val:
                # Logic to determine what to type
                input_mode = await inp.get_attribute("inputmode") or ""
                placeholder = (await inp.get_attribute("placeholder") or "").lower()
                
                # Check label for clues
                label_text = ""
                label_id = await inp.get_attribute("aria-labelledby")
                if label_id:
                    try:
                        l = page.locator(f"#{label_id.split()[0]}")
                        if await l.count() > 0: label_text = await l.inner_text()
                    except: pass

                fill_val = "test_data"
                if (type_attr == "number" or input_mode == "numeric" or 
                    any(x in label_text for x in ["سن", "تلفن", "شماره", "عدد"])):
                    fill_val = "25"
                elif "email" in input_mode or "email" in placeholder: 
                    fill_val = "test@example.com"
                
                await inp.fill(fill_val)
                filled_text = True
        except: pass
    
    if filled_text:
        return 'FILLED_WAITING' # Needs explicit button click

    # 4. DROPDOWNS
    combos = await page.locator('div[role="combobox"]').all()
    for cb in combos:
        if await cb.is_visible():
            try:
                await cb.click()
                await page.wait_for_selector('ul[role="listbox"]', timeout=1000)
                await page.locator('li[role="option"]').first.click()
                return 'FILLED_WAITING'
            except: pass

    return 'NOTHING_DONE'

# ===== PARSING LOGIC =====

def clean_text(text: str) -> str:
    if not text: return ""
    text = text.replace('\u200c', ' ').strip()
    text = re.sub(r'^[۰-۹0-9]+[\.\s:)\-]*', '', text)
    return text

async def parse_current_page(page: Page) -> List[Dict[str, Any]]:
    try:
        await page.wait_for_selector('label', timeout=2000)
    except: pass

    content = await page.content()
    soup = BeautifulSoup(content, 'html.parser')
    
    questions = []
    labels = soup.find_all('label', attrs={'for': True})

    for label in labels:
        raw_id = label['for']
        if not raw_id.startswith('question-'): continue
        q_id = raw_id.replace('question-', '')
        
        # Title
        title_span = label.find('span', class_=lambda x: x and 'title_text' in x)
        if title_span:
            q_text = clean_text(title_span.get_text(strip=True))
        else:
            visible_texts = [s for s in label.stripped_strings if 'visually_hidden' not in (s.parent.get('class') or [])]
            q_text = clean_text(" ".join(visible_texts))

        # Container
        container = label.find_parent('div', class_=lambda x: x and 'question_wrapper' in x)
        if not container: container = label.parent.parent.parent

        q_type = "TEXT_INPUT"
        options = []
        
        # Type Detection
        if container.find('table'):
            q_type = "MATRIX"
            # Matrix logic...
        elif container.find('div', role='radiogroup') or container.find('div', class_=lambda x: x and 'choice_wrapper' in x):
            q_type = "RADIO"
            items = container.find_all('div', role='radio')
            if not items: items = container.find_all('div', class_=lambda x: x and 'choice_item' in x)
            
            for item in items:
                lbl = item.find('div', class_=lambda x: x and 'label' in x)
                val = clean_text(lbl.get_text(strip=True)) if lbl else clean_text(item.get_text(strip=True))
                if val: options.append({"text": val, "value": val})

        elif container.find('input') or container.find('textarea'):
            # It is text
            q_type = "TEXT_INPUT"

        questions.append({
            "question_id": q_id,
            "question_text": q_text,
            "type": q_type,
            "options": options
        })

    return questions

# ===== MAIN EXECUTOR =====

async def handle_welcome_page(page: Page):
    try:
        start_btn = page.locator('button:has-text("شروع")')
        if await start_btn.count() > 0 and await start_btn.is_visible():
            logging.info("Welcome page detected. Clicking 'Start'...")
            await start_btn.click()
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)
    except: pass

async def extract_porsline_schema(p: async_playwright) -> List[Dict[str, Any]]:
    logging.info("Connecting to Porsline...")
    browser = await p.chromium.launch(headless=config.HEADLESS_MODE, slow_mo=config.SLOW_MO)
    page = await browser.new_page()
    
    final_schema = []
    processed_ids = set()

    try:
        await page.goto(config.BASE_FORM_URL, wait_until="networkidle", timeout=60000)
        await handle_welcome_page(page)

        page_num = 1
        
        # Initial IDs
        current_ids = await get_visible_question_ids(page)

        while True:
            logging.info(f"--- Processing Page {page_num} ---")
            
            # 1. Parse Questions
            qs = await parse_current_page(page)
            new_qs_count = 0
            for q in qs:
                if q['question_id'] not in processed_ids:
                    final_schema.append(q)
                    processed_ids.add(q['question_id'])
                    new_qs_count += 1
            
            if new_qs_count == 0 and len(current_ids) == 0:
                # No questions found, maybe end page?
                if await page.locator('button:has-text("ارسال")').count() > 0:
                     logging.info("Submit button found on final page.")
                     break
            
            # 2. Interact (Click Radio OR Fill Text)
            interaction_result = await interact_with_page(page)
            
            # 3. Detect Change (Auto-Advance vs Button Needed)
            await asyncio.sleep(1.5) # Wait for potential auto-advance animation
            
            new_ids = await get_visible_question_ids(page)
            
            # Logic: If IDs changed entirely, we moved to next page automatically
            if new_ids and new_ids != current_ids and not new_ids.intersection(current_ids):
                logging.info("   -> Auto-advanced to next page.")
                current_ids = new_ids
                page_num += 1
                continue # Loop back to parse new page
            
            # If IDs didn't change (or it was text input), look for buttons
            logging.info("   -> Checking for navigation buttons...")
            
            next_btn = page.locator('button[aria-label="بعدی"]')
            confirm_btn = page.locator('button:has-text("تایید")')
            submit_btn = page.locator('button:has-text("ارسال"), button:has-text("ثبت")')

            if await next_btn.count() > 0 and await next_btn.is_visible():
                await next_btn.click()
                logging.info("   -> Clicked Next.")
                await asyncio.sleep(2)
                current_ids = await get_visible_question_ids(page)
                page_num += 1
                
            elif await confirm_btn.count() > 0 and await confirm_btn.is_visible():
                await confirm_btn.click()
                logging.info("   -> Clicked Confirm.")
                await asyncio.sleep(2)
                current_ids = await get_visible_question_ids(page)
                page_num += 1
                
            elif await submit_btn.count() > 0 and await submit_btn.is_visible():
                logging.info("   -> Submit button found. End of form.")
                break
            else:
                # If we interacted (radio) but didn't move, and no buttons... 
                # check if maybe we *did* move but Porsline DOM is tricky.
                if interaction_result == 'AUTO_ADVANCED_LIKELY':
                     # Force a re-read of IDs
                     await asyncio.sleep(2)
                     recheck_ids = await get_visible_question_ids(page)
                     if recheck_ids != current_ids:
                         logging.info("   -> Late auto-advance detected.")
                         current_ids = recheck_ids
                         page_num += 1
                         continue

                logging.warning("   -> No navigation possible. Terminating extraction.")
                break

    except Exception as e:
        logging.error(f"Extraction Error: {e}", exc_info=True)
    finally:
        await browser.close()

    return final_schema

async def run():
    logging.info("===== RUNNING PHASE 1: SCHEMA EXTRACTION (PORSLINE) =====")
    async with async_playwright() as p:
        data = await extract_porsline_schema(p)
    
    if data:
        utils.save_json_file(config.SCHEMA_FILE_PATH, data, "schema")
    else:
        logging.error("No data extracted.")
    
    logging.info("===== PHASE 1 FINISHED =====")