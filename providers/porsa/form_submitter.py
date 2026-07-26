# providers/porsa/form_submitter.py

# ===== IMPORTS & DEPENDENCIES =====
import asyncio
import logging
import os
import re
import shutil
from typing import Dict, Any, List, Tuple

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page
from thefuzz import fuzz

import config
import utils

# ===== SUCCESS DETECTION =====
# Porsa's #submitButton handler refreshes the CSRF token and POSTs #pageForm to
# SurveyStatus.storeUrl — a full-page navigation that replaces the questions DOM
# with the server's thank-you page. So the primary success signal is simply that
# #pageForm is gone. If required-field validation fails, the handler returns
# early WITHOUT submitting and the form stays, so "form gone" is reliable.
# Thank-you text is only used as a fallback while the form is still mounted.
_THANKYOU_TEXTS = (
    "شما با موفقیت",
    "پاسخ‌های شما ثبت",
    "پاسخ شما ثبت",
    "با تشکر از",
    "سپاسگزاریم",
)


async def is_success_page(page: Page) -> bool:
    try:
        if await page.locator('#pageForm').count() == 0:
            return True
    except Exception:
        pass
    try:
        for text in _THANKYOU_TEXTS:
            if await page.locator(f':text("{text}")').count() > 0:
                return True
    except Exception:
        pass
    return False


# ===== OPTION RESOLUTION =====

def clean_text(text: str) -> str:
    """Same normalisation as the extractor so answer ↔ live label match."""
    if not text:
        return ""
    text = text.replace(' ', ' ').replace('‌', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def _label_value_pairs(container_html: str, qid: str, dtype: str) -> List[Tuple[str, str]]:
    """Parse the live container HTML into [(label, input_value), ...] in column
    order for the radio/checkbox group `qid`. The submitter re-derives the
    numeric input value from the live DOM (rather than trusting the stored
    schema) so it stays correct if the form's option order/value set changes."""
    soup = BeautifulSoup(container_html, 'html.parser')
    inputs = soup.select(f'input[name="{qid}"]')
    values = [inp.get('value', '') for inp in inputs]

    if dtype == 'likert':
        cells = soup.select('tr.likertText > td')
        labels = [clean_text(c.get_text(' ', strip=True)) for c in cells]
    elif dtype in ('matrixSingleChoice', 'matrixMultipleChoice'):
        ths = [th for row in soup.select('tbody > tr') for th in row.select('th.thvertical')]
        labels = [clean_text(th.get_text(' ', strip=True)) for th in ths]
        # drop leading empty corner cell(s), then drop a single extra leading
        # corner so the labels line up 1:1 with the row's inputs
        while len(labels) > len(values) and labels and not labels[0]:
            labels.pop(0)
        if len(labels) == len(values) + 1:
            labels.pop(0)
    else:  # singleChoice / multipleChoice
        ths = soup.select('th.click-choice-check, th.click-multichoice-check') or soup.select('th')
        labels = [clean_text(th.get_text(' ', strip=True)) for th in ths]

    pairs: List[Tuple[str, str]] = []
    for i, val in enumerate(values):
        label = labels[i] if i < len(labels) else val
        pairs.append((label, val))
    return pairs


def _resolve_value(pairs: List[Tuple[str, str]], answer_text: str) -> str | None:
    """Map a human answer label to the underlying input value: exact normalised
    match first, then a fuzzy fallback (the answer was already fuzzy-validated
    by core/answer_generator.py, so exact should normally hit)."""
    norm = clean_text(str(answer_text))
    for label, val in pairs:
        if clean_text(label) == norm:
            return val
    best, best_ratio = None, 80
    for label, val in pairs:
        ratio = fuzz.ratio(norm, clean_text(label))
        if ratio > best_ratio:
            best_ratio, best = ratio, val
    return best


# ===== FIELD FILLING =====

async def fill_answer(page: Page, qid: str, answer_text: str) -> bool:
    """Fill one question: click the radio/checkbox whose option label matches
    `answer_text`, or type into a text field. Returns True on success."""
    logging.info(f"   -> QID: {qid} | Answer: {str(answer_text)[:40]}")

    # --- TEXT questions (textarea / text / email / number) ---
    text_loc = page.locator(
        f'textarea[name="{qid}"], '
        f'input[type="text"][name="{qid}"], '
        f'input[type="email"][name="{qid}"], '
        f'input[type="number"][name="{qid}"]'
    )
    if await text_loc.count() > 0:
        try:
            await text_loc.first.fill(str(answer_text))
            logging.info("      Filled text input.")
            return True
        except Exception as e:
            logging.warning(f"      Could not fill text input: {e}")
            return False

    # --- CHOICE questions (radio / checkbox) ---
    first_field = page.locator(f'input[name="{qid}"]').first
    if not await first_field.count():
        return False

    container = first_field.locator('xpath=ancestor::div[contains(@class, "questionsBorder")][1]')
    if not await container.count():
        return False
    dtype = await container.get_attribute('data-type') or ''

    pairs = _label_value_pairs(await container.inner_html(), qid, dtype)
    if not pairs:
        return False
    target_value = _resolve_value(pairs, answer_text)
    if target_value is None:
        logging.warning(f"      No option matching '{answer_text}' for {qid}.")
        return False

    target = page.locator(f'input[name="{qid}"][value="{target_value}"]')
    if await target.count() == 0:
        return False
    try:
        await target.first.check(force=True)
        logging.info("      Selected choice.")
        return True
    except Exception:
        # styled radios/checkboxes: the visible mark is a <span> inside the
        # wrapping <label>; fall back to clicking the label.
        try:
            await target.first.locator('xpath=ancestor::label[1]').click(force=True)
            logging.info("      Selected choice (via label).")
            return True
        except Exception as e:
            logging.warning(f"      Could not select choice: {e}")
            return False


async def fill_visible_answers(page: Page, answers: Dict[str, str]) -> int:
    """Fill every answer whose field is present and visible on the current page.
    Returns how many were filled."""
    filled = 0
    for qid, ans in answers.items():
        field = page.locator(f'input[name="{qid}"], textarea[name="{qid}"]').first
        if await field.count() == 0:
            continue  # belongs to a different page
        container = field.locator('xpath=ancestor::div[contains(@class, "questionsBorder")][1]')
        try:
            if await container.count() and not await container.is_visible():
                continue
        except Exception:
            continue
        if await fill_answer(page, qid, ans):
            filled += 1
    return filled


# ===== NAVIGATION =====

async def handle_navigation(page: Page) -> str:
    """Click #nextButton if present, else #submitButton. Returns
    'next' | 'submit' | 'none'."""
    next_btn = page.locator('#nextButton')
    if await next_btn.count() > 0 and await next_btn.first.is_visible():
        try:
            await next_btn.first.click()
            logging.info("   -> Clicked Next.")
            return 'next'
        except Exception as e:
            logging.warning(f"   -> Next click failed: {e}")
            return 'next'

    submit_btn = page.locator('#submitButton')
    if await submit_btn.count() > 0 and await submit_btn.first.is_visible():
        logging.info("   -> Clicking final submit ('ثبت نهایی')...")
        # JS click is more reliable against overlays / styled buttons.
        try:
            await submit_btn.first.evaluate("el => el.click()")
        except Exception:
            try:
                await submit_btn.first.click(force=True)
            except Exception as e:
                logging.warning(f"   -> Submit click failed: {e}")
        return 'submit'

    return 'none'

# ===== MAIN WORKFLOW =====

async def submit_single_form(p, answers: Dict[str, str], persona_id: str,
                             answer_path: str, done_path: str) -> bool:
    logging.info(f"Starting submission workflow for: {persona_id}")

    moved = {"done": False}

    def mark_done():
        if moved["done"]:
            return
        try:
            shutil.move(answer_path, os.path.join(done_path, os.path.basename(answer_path)))
            moved["done"] = True
            logging.info(f"Moved {os.path.basename(answer_path)} to done.")
        except FileNotFoundError:
            moved["done"] = True
        except Exception as e:
            logging.error(f"Could not move {os.path.basename(answer_path)} to done: {e}")

    browser = await p.chromium.launch(
        channel=config.BROWSER_CHANNEL,
        headless=config.HEADLESS_MODE,
        slow_mo=0,
        proxy={"server": config.TOR_PROXY_SERVER} if config.USE_TOR else None,
    )
    context = await browser.new_context()
    page = await context.new_page()
    page.set_default_timeout(30000)

    try:
        if config.USE_TOR:
            try:
                await page.goto("https://checkip.amazonaws.com", timeout=10000)
            except Exception:
                pass

        await page.goto(config.BASE_FORM_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # One step per question plus a buffer for submit / thank-you pages.
        max_pages = len(answers) + 20
        page_idx = 0

        while page_idx < max_pages:
            page_idx += 1
            logging.info(f"--- Page Step {page_idx} ---")

            if await is_success_page(page):
                logging.info("Success message detected! Form submitted.")
                mark_done()
                return True

            await fill_visible_answers(page, answers)
            await page.wait_for_timeout(800)

            action = await handle_navigation(page)

            if action == 'none':
                # No button — maybe already landed on the thank-you page.
                await page.wait_for_timeout(2000)
                if await is_success_page(page):
                    mark_done()
                    return True
                logging.error("Stuck: cannot navigate and not on success page.")
                break

            if action == 'next':
                await page.wait_for_load_state('networkidle', timeout=20000)
                await asyncio.sleep(1.5)
                continue

            # action == 'submit' — wait up to ~25s for the form to be replaced.
            for _ in range(25):
                if await is_success_page(page):
                    logging.info("Success confirmed after submit.")
                    mark_done()
                    return True
                await asyncio.sleep(1)

            logging.error("Submit clicked but success page not detected in time.")
            try:
                html = await page.content()
                os.makedirs(config.RECEIPTS_DIR_PATH, exist_ok=True)
                with open(os.path.join(config.RECEIPTS_DIR_PATH, f"failed_submit_{persona_id}.html"),
                          "w", encoding="utf-8") as f:
                    f.write(html)
            except Exception:
                pass

    except Exception as e:
        logging.error(f"Fatal error for {persona_id}: {e}", exc_info=True)
        try:
            await page.screenshot(path=os.path.join(config.RECEIPTS_DIR_PATH, f"crash_{persona_id}.png"))
        except Exception:
            pass
    finally:
        try:
            await browser.close()
        except Exception:
            pass

    return False

# ===== RUNNER =====

async def run():
    logging.info("===== RUNNING PHASE 4: FORM SUBMISSION (PORSA) =====")

    done_path = os.path.join(config.ANSWERS_DIR_PATH, config.ANSWERS_DONE_DIR_NAME)
    os.makedirs(done_path, exist_ok=True)

    answer_files = [f for f in os.listdir(config.ANSWERS_DIR_PATH)
                    if f.endswith('.json') and not os.path.isdir(os.path.join(config.ANSWERS_DIR_PATH, f))]
    if not answer_files:
        logging.warning("No answer files found.")
        return

    logging.info(f"Found {len(answer_files)} answer sets to submit.")

    async with async_playwright() as p:
        for answer_file in answer_files:
            persona_id = answer_file.replace(".json", "")
            answer_path = os.path.join(config.ANSWERS_DIR_PATH, answer_file)

            if config.USE_TOR:
                try:
                    utils.renew_tor_ip()
                except Exception:
                    pass
                await asyncio.sleep(5)

            answers = utils.load_json_file(answer_path, f"answers from {answer_file}")
            if not answers:
                continue

            success = await submit_single_form(p, answers, persona_id, answer_path, done_path)

            if success:
                logging.info(f"Submission succeeded for {persona_id}.")
            else:
                logging.error(f"Submission failed for {persona_id}. File left in answers/ for retry.")

            logging.info("Waiting 5s before next submission...")
            await asyncio.sleep(5)

    logging.info("===== PHASE 4 FINISHED =====")