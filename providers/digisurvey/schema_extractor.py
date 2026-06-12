# providers/digisurvey/schema_extractor.py

import asyncio
import logging
import re
from typing import List, Dict, Any
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page

import config
import utils


def clean_text(html_text: str) -> str:
    """Strip HTML tags and clean whitespace from a question/choice text."""
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_page(html: str) -> List[Dict[str, Any]]:
    """Parse all questions from the current page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    questions = []

    for q_div in soup.select("div.question[data-id]"):
        q_id = q_div.get("data-id", "").strip()
        if not q_id:
            continue

        title_div = q_div.select_one("div.question-title")
        q_text = clean_text(title_div.decode_contents() if title_div else "")

        # Determine type and collect options
        radio_inputs = q_div.select("ul.question-choices input[type='radio']")
        checkbox_inputs = q_div.select("ul.question-choices input[type='checkbox']")
        textarea = q_div.select_one("div.question-answer textarea")

        if radio_inputs:
            q_type = "RADIO"
            options = []
            for label in q_div.select("label.choice-text"):
                val = clean_text(label.decode_contents())
                if val:
                    options.append({"text": val, "value": val})

        elif checkbox_inputs:
            q_type = "CHECKBOX"
            options = []
            for label in q_div.select("label.choice-text"):
                val = clean_text(label.decode_contents())
                if val:
                    options.append({"text": val, "value": val})

        elif textarea:
            q_type = "TEXT_INPUT"
            options = []

        else:
            # Unknown type — skip paragraph/description blocks
            continue

        if q_text:
            questions.append({
                "question_id": q_id,
                "question_text": q_text,
                "type": q_type,
                "options": options,
            })

    return questions


async def fill_dummy_answers(page: Page) -> None:
    """Fill minimum required answers on the page so 'Next' can be clicked."""
    question_divs = await page.locator("div.question[data-id]").all()
    for q_div in question_divs:
        try:
            q_id = await q_div.get_attribute("data-id", timeout=5000)
        except Exception:
            continue
        if not q_id:
            continue

        # Radio
        radios = q_div.locator("ul.question-choices input[type='radio']")
        if await radios.count() > 0:
            first = radios.first
            if await first.is_visible():
                try:
                    label_id = await first.get_attribute("id")
                    if label_id:
                        lbl = page.locator(f"label[for='{label_id}']")
                        if await lbl.count() > 0:
                            await lbl.first.click(force=True)
                        else:
                            await first.check(force=True)
                except Exception:
                    pass
            continue

        # Checkbox
        checkboxes = q_div.locator("ul.question-choices input[type='checkbox']")
        if await checkboxes.count() > 0:
            first = checkboxes.first
            if await first.is_visible():
                try:
                    label_id = await first.get_attribute("id")
                    if label_id:
                        lbl = page.locator(f"label[for='{label_id}']")
                        if await lbl.count() > 0:
                            await lbl.first.click(force=True)
                        else:
                            await first.check(force=True)
                except Exception:
                    pass
            continue

        # Textarea
        textarea = q_div.locator("div.question-answer textarea")
        if await textarea.count() > 0 and await textarea.first.is_visible():
            try:
                if not await textarea.first.input_value():
                    await textarea.first.fill("25")
            except Exception:
                pass


async def click_next(page: Page) -> str:
    """
    Click the primary navigation button.
    Returns: 'next' | 'submit' | 'none'
    """
    btn = page.locator("button.btn.next.green").first

    if await btn.count() == 0:
        return "none"

    if not await btn.is_visible():
        return "none"

    btn_text = (await btn.inner_text()).strip()
    await btn.scroll_into_view_if_needed()
    await btn.click()

    if "ارسال" in btn_text:
        return "submit"
    return "next"


async def extract_schema(p: async_playwright) -> List[Dict[str, Any]]:
    logging.info("Launching browser for DigiSurvey schema extraction...")
    browser = await p.chromium.launch(channel=config.BROWSER_CHANNEL, headless=config.HEADLESS_MODE, slow_mo=config.SLOW_MO)
    page = await browser.new_page()

    final_schema: List[Dict[str, Any]] = []
    seen_ids: set = set()

    try:
        await page.goto(config.BASE_FORM_URL, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(1)

        # Welcome page — click "شروع" (Start)
        start_btn = page.locator("input[type='submit'][value='شروع'], button:has-text('شروع')")
        if await start_btn.count() > 0 and await start_btn.first.is_visible():
            logging.info("Welcome page found. Clicking 'شروع'...")
            await start_btn.first.click()
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

        page_num = 0
        max_pages = 50

        while page_num < max_pages:
            page_num += 1
            logging.info(f"--- Extracting page {page_num} ---")

            html = await page.content()
            questions = parse_page(html)

            new_count = 0
            for q in questions:
                if q["question_id"] not in seen_ids:
                    final_schema.append(q)
                    seen_ids.add(q["question_id"])
                    new_count += 1
                    logging.info(f"   Found: [{q['type']}] {q['question_id']} — {q['question_text'][:60]}")

            # If 0 new questions, the SPA may not have rendered yet — wait and re-parse
            if new_count == 0:
                try:
                    await page.locator("div.question[data-id]").first.wait_for(state="visible", timeout=8000)
                    html = await page.content()
                    questions = parse_page(html)
                    for q in questions:
                        if q["question_id"] not in seen_ids:
                            final_schema.append(q)
                            seen_ids.add(q["question_id"])
                            new_count += 1
                            logging.info(f"   Found (re-wait): [{q['type']}] {q['question_id']} — {q['question_text'][:60]}")
                except Exception:
                    pass

            logging.info(f"   New questions on this page: {new_count}")

            # Check if this is the last page (submit button present)
            submit_btn = page.locator("button.btn.next.green")
            if await submit_btn.count() > 0:
                btn_text = (await submit_btn.first.inner_text()).strip()
                if "ارسال" in btn_text:
                    logging.info("Last page reached. Stopping extraction (not submitting).")
                    break

            # Fill dummy data so validation passes, then advance
            await fill_dummy_answers(page)
            await asyncio.sleep(0.5)

            result = await click_next(page)
            if result == "none":
                logging.warning("No navigation button found. Stopping.")
                break

            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

    except Exception as e:
        logging.error(f"Schema extraction error: {e}", exc_info=True)
    finally:
        await browser.close()

    return final_schema


async def run():
    logging.info("===== RUNNING PHASE 1: SCHEMA EXTRACTION (DIGISURVEY) =====")
    async with async_playwright() as p:
        data = await extract_schema(p)

    if data:
        utils.save_json_file(config.SCHEMA_FILE_PATH, data, "schema")
        logging.info(f"Extracted {len(data)} questions total.")
    else:
        logging.error("No questions extracted.")

    logging.info("===== PHASE 1 FINISHED =====")
