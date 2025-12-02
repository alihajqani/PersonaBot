# providers/porsline/form_submitter.py

# ===== IMPORTS & DEPENDENCIES =====
import asyncio
import logging
import os
import shutil
from typing import Dict, Set
from playwright.async_api import async_playwright, Page
import config
import utils

# ==========================================
# CONSTANTS & SELECTORS
# ==========================================

# لیست سلکتورهایی که نشان‌دهنده موفقیت آمیز بودن ارسال هستند
# بر اساس HTML ارسالی شما به‌روزرسانی شد
SUCCESS_SELECTORS = [
    'h1:has-text("سپاس‌گزاریم")',
    'div[class*="styles_info_box"]',  # کلاس کانتینر پیام موفقیت شما
    'a:has-text("ساخت پرسشنامه در پُرس‌لاین")',
    ':text("ثبت شد")',
    ':text("با تشکر")'
]

# ==========================================
# CORE LOGIC
# ==========================================

async def check_for_success(page: Page) -> bool:
    """Checks if any of the success elements are visible on the page."""
    try:
        for selector in SUCCESS_SELECTORS:
            if await page.locator(selector).count() > 0:
                if await page.locator(selector).first.is_visible():
                    return True
    except:
        pass
    return False

async def get_visible_question_ids(page: Page) -> Set[str]:
    """Returns a set of question IDs currently visible on the screen."""
    try:
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

async def fill_question_on_page(page: Page, q_id: str, answer_value: str) -> bool:
    """Finds the question container and fills/clicks the answer."""
    logging.info(f"   -> Processing QID: {q_id} | Answer: {answer_value}")
    
    try:
        label = page.locator(f'label[for="question-{q_id}"]').first
        if not await label.count() or not await label.is_visible():
            return False

        container = label.locator('xpath=./ancestor::div[contains(@class, "question_wrapper") or contains(@class, "root")][1]')
        if not await container.count():
             container = label.locator('xpath=../../..').first

        # A) CHECK FOR CHOICES (Radio/Select)
        choices = container.locator('div[role="radio"], div[class*="choice_item"]')
        if await choices.count() > 0:
            target = choices.filter(has_text=f"^{answer_value}$").first
            if not await target.count():
                target = choices.filter(has_text=answer_value).first
            
            if await target.count() > 0 and await target.is_visible():
                await target.scroll_into_view_if_needed()
                await target.click(force=True)
                logging.info("      Selected choice.")
                return True

        # B) CHECK FOR TEXT INPUTS
        inputs = container.locator('input:not([type="hidden"]), textarea')
        if await inputs.count() > 0:
            target_input = inputs.first
            if await target_input.is_visible():
                await target_input.fill(str(answer_value))
                logging.info("      Filled text input.")
                return True

        return False

    except Exception as e:
        return False

async def handle_navigation(page: Page) -> str:
    """
    Clicks Next or Submit buttons.
    OPTIMIZED: Prioritizes JS Click for Submit immediately.
    """
    logging.info("   -> Checking for navigation/submit buttons...")
    
    # 1. FINAL SUBMIT BUTTON (Highest Priority - JS Click)
    # Using the specific class from your HTML
    submit_btn = page.locator('button.shared_submit__7OvzI').first
    
    # Fallback submit selectors
    if not await submit_btn.count():
        submit_btn = page.locator('button:has-text("ارسال"), button:has-text("ثبت")').first

    if await submit_btn.count() > 0 and await submit_btn.is_visible():
        logging.info("      Found Submit Button. Executing JS Click strategy...")
        
        # Ensure it's in view
        try: await submit_btn.scroll_into_view_if_needed()
        except: pass
        
        # STRATEGY: JavaScript Click (Direct Injection)
        # This bypasses overlays and React event listener issues
        try:
            await submit_btn.evaluate("element => element.click()")
            logging.info("      Executed JS Click (evaluate).")
            return 'clicked_submit'
        except Exception as e:
            logging.warning(f"      JS Click failed: {e}. Trying Force Click.")
            await submit_btn.click(force=True)
            return 'clicked_submit'

    # 2. NEXT / CONFIRM BUTTONS (Standard Click)
    next_btn = page.locator('button[aria-label="بعدی"], button:has-text("بعدی")')
    confirm_btn = page.locator('button:has-text("تایید")')
    
    if await next_btn.count() > 0 and await next_btn.is_visible():
        await next_btn.first.click()
        logging.info("      Clicked Next.")
        return 'clicked_next'
        
    elif await confirm_btn.count() > 0 and await confirm_btn.is_visible():
        await confirm_btn.first.click()
        logging.info("      Clicked Confirm.")
        return 'clicked_next'
        
    return 'none'

# ==========================================
# MAIN WORKFLOW
# ==========================================

async def submit_single_form(p: async_playwright, answers: Dict[str, str], persona_id: str) -> bool:
    logging.info(f"Starting submission workflow for: {persona_id}")
    
    browser = await p.chromium.launch(
        headless=config.HEADLESS_MODE, 
        slow_mo=config.SLOW_MO, 
        proxy={"server": config.TOR_PROXY_SERVER} if config.USE_TOR else None
    )
    context = await browser.new_context()
    page = await context.new_page()
    page.set_default_timeout(30000)

    try:
        if config.USE_TOR:
            try: await page.goto("https://checkip.amazonaws.com", timeout=10000)
            except: pass

        await page.goto(config.BASE_FORM_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # Welcome Page
        try:
            start_btn = page.locator('button:has-text("شروع")')
            if await start_btn.count() > 0:
                await start_btn.click()
                await page.wait_for_timeout(2000)
        except: pass

        max_pages = 70
        page_idx = 0
        current_ids = await get_visible_question_ids(page)

        while page_idx < max_pages:
            page_idx += 1
            logging.info(f"--- Page Step {page_idx} ---")
            
            # 1. FAST CHECK FOR SUCCESS
            if await check_for_success(page):
                logging.info("Success message detected! Form submitted.")
                await browser.close()
                return True

            # 2. FILL ANSWERS
            any_filled = False
            for q_id in current_ids:
                if q_id in answers:
                    success = await fill_question_on_page(page, q_id, answers[q_id])
                    if success: any_filled = True

            await page.wait_for_timeout(1000)

            # 3. CHECK AUTO-ADVANCE
            new_ids = await get_visible_question_ids(page)
            if new_ids and new_ids != current_ids and not new_ids.intersection(current_ids):
                # If IDs changed entirely, assume auto-advance happened
                # BUT first check if we hit success page by accident
                if await check_for_success(page):
                    logging.info("Auto-advanced into Success Page!")
                    await browser.close()
                    return True
                
                logging.info("   -> Auto-advanced to next question set.")
                current_ids = new_ids
                continue 

            # 4. NAVIGATION / SUBMIT
            action = await handle_navigation(page)
            
            # Wait for reaction
            await page.wait_for_timeout(2000)
            
            if action == 'clicked_submit':
                logging.info("   -> Submit triggered. Checking for success...")
                
                # Try to find success message for up to 15 seconds
                success_found = False
                for _ in range(15):
                    if await check_for_success(page):
                        success_found = True
                        break
                    await asyncio.sleep(1)
                
                if success_found:
                    logging.info("Success confirmed via Selector match.")
                    await browser.close()
                    return True
                else:
                    logging.error("   -> Submit clicked but Success Page NOT detected in time.")
                    # Save HTML for debug only if it fails
                    html_content = await page.content()
                    with open(f"output/receipts/failed_submit_{persona_id}.html", "w", encoding="utf-8") as f:
                        f.write(html_content)
            
            current_ids = await get_visible_question_ids(page)
            
            # Stuck Guard
            if action == 'none' and not any_filled and len(current_ids) > 0:
                logging.error("Stuck: Cannot fill, cannot navigate.")
                break
            
            # If no questions and no action, maybe we are at the end but button is hidden?
            if len(current_ids) == 0 and action == 'none':
                 # Last resort check for success again
                 if await check_for_success(page):
                     return True
                 
                 logging.error("Lost: No visible questions and no navigation buttons.")
                 break

    except Exception as e:
        logging.error(f"Fatal error for {persona_id}: {e}")
        await page.screenshot(path=f"output/crash_{persona_id}.png")
    finally:
        await browser.close()

    return False

# ==========================================
# RUNNER
# ==========================================
async def run():
    logging.info("===== RUNNING PHASE 4: FORM SUBMISSION (FINAL FIX) =====")

    answer_files = [f for f in os.listdir(config.ANSWERS_DIR_PATH) if f.endswith('.json')]
    if not answer_files:
        logging.warning("No answer files found.")
        return
        
    done_path = os.path.join(config.ANSWERS_DIR_PATH, "done")
    os.makedirs(done_path, exist_ok=True)

    async with async_playwright() as p:
        for answer_file in answer_files:
            if config.USE_TOR:
                try: utils.renew_tor_ip()
                except: pass
                await asyncio.sleep(5)

            persona_id = answer_file.replace(".json", "")
            answer_path = os.path.join(config.ANSWERS_DIR_PATH, answer_file)
            answers = utils.load_json_file(answer_path, f"answers from {answer_file}")

            if not answers: continue

            success = await submit_single_form(p, answers, persona_id)

            if success:
                shutil.move(answer_path, os.path.join(done_path, answer_file))
                logging.info(f"Moved {answer_file} to done.")
            else:
                logging.error(f"Submission failed for {persona_id}.")

            logging.info("Waiting 5s...")
            await asyncio.sleep(5)