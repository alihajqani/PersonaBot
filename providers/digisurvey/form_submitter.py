# providers/digisurvey/form_submitter.py

import asyncio
import logging
import os
import shutil
from typing import Dict
from playwright.async_api import async_playwright, Page

import config
import utils


# ==========================================
# HELPERS
# ==========================================

async def is_success_page(page: Page) -> bool:
    """
    DigiSurvey shows a finish/thank-you page after the final submission.
    Detect it by URL change or absence of the answer form.
    """
    url = page.url
    if "finish" in url or "done" in url or "result" in url:
        return True

    # If the answer form is gone, we've left the questions flow
    form = page.locator("#AnswerForm, #AnswerBody")
    if await form.count() == 0:
        return True

    # Explicit thank-you text patterns
    for text in ["با تشکر", "ممنون", "پاسخ‌های شما ثبت شد", "پاسخ شما ثبت", "نظرسنجی پایان"]:
        if await page.locator(f":text('{text}')").count() > 0:
            return True

    return False


async def get_visible_question_ids(page: Page) -> list:
    """Return IDs of all question containers currently visible on the page."""
    try:
        q_divs = await page.locator("div.question[data-id]").all()
        ids = []
        for q in q_divs:
            if await q.is_visible():
                q_id = await q.get_attribute("data-id")
                if q_id:
                    ids.append(q_id.strip())
        return ids
    except Exception:
        return []


async def fill_radio(page: Page, q_id: str, answer_value: str) -> bool:
    """Click the label whose text matches answer_value in the given radio question."""
    q_container = page.locator(f"div.question[data-id='{q_id}']").first
    if not await q_container.count():
        return False

    labels = q_container.locator("label.choice-text")
    count = await labels.count()

    for i in range(count):
        lbl = labels.nth(i)
        text = (await lbl.inner_text()).strip()
        if text == answer_value or answer_value in text:
            try:
                await lbl.scroll_into_view_if_needed()
                await lbl.click(force=True)
                return True
            except Exception:
                pass

    # Fuzzy fallback: click first label if nothing matched exactly
    if count > 0:
        logging.warning(f"   No exact match for '{answer_value}' in Q{q_id}. Skipping.")
    return False


async def fill_checkbox(page: Page, q_id: str, answer_value: str) -> bool:
    """Click the first matching checkbox label for the given answer."""
    q_container = page.locator(f"div.question[data-id='{q_id}']").first
    if not await q_container.count():
        return False

    labels = q_container.locator("label.choice-text")
    count = await labels.count()

    for i in range(count):
        lbl = labels.nth(i)
        text = (await lbl.inner_text()).strip()
        if text == answer_value or answer_value in text:
            try:
                await lbl.scroll_into_view_if_needed()
                await lbl.click(force=True)
                return True
            except Exception:
                pass

    return False


async def fill_text(page: Page, q_id: str, answer_value: str) -> bool:
    """Fill a textarea input for the given question."""
    textarea = page.locator(
        f"div.question[data-id='{q_id}'] div.question-answer textarea, "
        f"div.question[data-id='{q_id}'] textarea.answer-text"
    ).first

    if not await textarea.count():
        return False
    if not await textarea.is_visible():
        return False

    try:
        await textarea.fill(str(answer_value))
        return True
    except Exception:
        return False


async def fill_page_answers(page: Page, answers: Dict[str, str], q_ids: list) -> int:
    """Fill answers for all visible questions. Returns count of filled questions."""
    filled = 0
    for q_id in q_ids:
        if q_id not in answers:
            logging.warning(f"   No answer for Q{q_id}, skipping.")
            continue

        answer_value = str(answers[q_id]).strip()
        q_container = page.locator(f"div.question[data-id='{q_id}']").first

        if not await q_container.count():
            continue

        # Determine question type from DOM
        if await q_container.locator("ul.question-choices input[type='radio']").count() > 0:
            success = await fill_radio(page, q_id, answer_value)
        elif await q_container.locator("ul.question-choices input[type='checkbox']").count() > 0:
            success = await fill_checkbox(page, q_id, answer_value)
        elif await q_container.locator("div.question-answer textarea, textarea.answer-text").count() > 0:
            success = await fill_text(page, q_id, answer_value)
        else:
            logging.warning(f"   Unknown question type for Q{q_id}.")
            continue

        if success:
            filled += 1
            logging.info(f"   Filled Q{q_id}: {answer_value[:40]}")
        else:
            logging.warning(f"   Failed to fill Q{q_id}: {answer_value[:40]}")

    return filled


async def click_navigation(page: Page) -> str:
    """
    Click the primary navigation button.
    Returns: 'next' | 'submit' | 'none'
    """
    btn = page.locator("button.btn.next.green").first

    if not await btn.count() or not await btn.is_visible():
        return "none"

    btn_text = (await btn.inner_text()).strip()

    try:
        await btn.scroll_into_view_if_needed()
    except Exception:
        pass

    is_submit = "ارسال" in btn_text

    if is_submit:
        logging.info("   Clicking final submit button ('ارسال و پایان')...")
        # JS click is more reliable for React-driven submit buttons
        try:
            await btn.evaluate("el => el.click()")
        except Exception:
            await btn.click(force=True)
        return "submit"
    else:
        logging.info("   Clicking 'بعدی'...")
        await btn.click()
        return "next"


# ==========================================
# MAIN SUBMISSION FLOW
# ==========================================

async def submit_single_form(p: async_playwright, answers: Dict[str, str], persona_id: str) -> bool:
    logging.info(f"Starting DigiSurvey submission for: {persona_id}")

    browser = await p.chromium.launch(
        channel=config.BROWSER_CHANNEL,
        headless=config.HEADLESS_MODE,
        slow_mo=config.SLOW_MO,
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

        await page.goto(config.BASE_FORM_URL, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(1)

        # Welcome page
        start_btn = page.locator("input[type='submit'][value='شروع'], button:has-text('شروع')")
        if await start_btn.count() > 0 and await start_btn.first.is_visible():
            logging.info("Clicking 'شروع' on welcome page...")
            await start_btn.first.click()
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

        max_pages = 50
        page_idx = 0

        while page_idx < max_pages:
            page_idx += 1
            logging.info(f"--- Page step {page_idx} ---")

            if await is_success_page(page):
                logging.info("Success page detected!")
                await browser.close()
                return True

            q_ids = await get_visible_question_ids(page)
            logging.info(f"   Visible questions: {q_ids}")

            if q_ids:
                await fill_page_answers(page, answers, q_ids)
                await asyncio.sleep(0.5)

            action = await click_navigation(page)

            if action == "none":
                # Maybe we already reached the success page without a button
                await asyncio.sleep(2)
                if await is_success_page(page):
                    logging.info("Reached success page (no button needed).")
                    await browser.close()
                    return True
                logging.error("   No navigation button found and not on success page. Aborting.")
                break

            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

            if action == "submit":
                # Wait up to 15s for success page
                for _ in range(15):
                    if await is_success_page(page):
                        logging.info("Submission confirmed!")
                        await browser.close()
                        return True
                    await asyncio.sleep(1)

                # Save debug HTML if submission failed
                html = await page.content()
                debug_path = os.path.join(config.RECEIPTS_DIR_PATH, f"failed_{persona_id}.html")
                os.makedirs(config.RECEIPTS_DIR_PATH, exist_ok=True)
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(html)
                logging.error(f"   Submission failed. Debug HTML saved to {debug_path}")
                break

    except Exception as e:
        logging.error(f"Fatal error for {persona_id}: {e}", exc_info=True)
        try:
            screenshot_path = os.path.join(config.RECEIPTS_DIR_PATH, f"crash_{persona_id}.png")
            os.makedirs(config.RECEIPTS_DIR_PATH, exist_ok=True)
            await page.screenshot(path=screenshot_path)
        except Exception:
            pass
    finally:
        await browser.close()

    return False


# ==========================================
# RUNNER
# ==========================================

async def run():
    logging.info("===== RUNNING PHASE 4: FORM SUBMISSION (DIGISURVEY) =====")

    answer_files = [f for f in os.listdir(config.ANSWERS_DIR_PATH) if f.endswith(".json")]
    if not answer_files:
        logging.warning("No answer files found.")
        return

    done_dir = os.path.join(config.ANSWERS_DIR_PATH, "done")
    os.makedirs(done_dir, exist_ok=True)

    async with async_playwright() as p:
        for answer_file in answer_files:
            if config.USE_TOR:
                try:
                    utils.renew_tor_ip()
                except Exception:
                    pass
                await asyncio.sleep(5)

            persona_id = answer_file.replace(".json", "")
            answer_path = os.path.join(config.ANSWERS_DIR_PATH, answer_file)
            answers = utils.load_json_file(answer_path, f"answers from {answer_file}")

            if not answers:
                continue

            success = await submit_single_form(p, answers, persona_id)

            if success:
                shutil.move(answer_path, os.path.join(done_dir, answer_file))
                logging.info(f"Moved {answer_file} to done.")
            else:
                logging.error(f"Submission failed for {persona_id}.")

            logging.info("Waiting 5s before next submission...")
            await asyncio.sleep(5)

    logging.info("===== PHASE 4 FINISHED =====")
