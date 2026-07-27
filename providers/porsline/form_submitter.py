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
import random

# ==========================================
# CONSTANTS & SELECTORS
# ==========================================

# لیست سلکتورهایی که نشان‌دهنده موفقیت آمیز بودن ارسال هستند
# بر اساس HTML ارسالی شما به‌روزرسانی شد
# Selectors that confirm the Porsline thank-you / appreciation page.
# Verified against the live thank-you HTML captured in output/receipts/.
# The appreciation text is owner-configurable per survey, so the class-based
# selectors are the primary signal; the text selector is a fallback only.
# NOTE: the real page uses "سپاسگزاریم" with NO ZWNJ — the old selector used a
# ZWNJ and therefore never matched.
SUCCESS_SELECTORS = [
    'div[class*="styles_appreciation_custom_page"]',       # thank-you page wrapper
    'h1[class*="appreciation_custom_page_detail_title"]',  # heading holding "سپاسگزاریم"
    ':text("سپاسگزاریم")',                                  # NO ZWNJ; fallback only
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

async def submit_single_form(p: async_playwright, answers: Dict[str, str], persona_id: str,
                             answer_path: str, done_path: str) -> bool:
    logging.info(f"Starting submission workflow for: {persona_id}")

    moved = {"done": False}
    def mark_done():
        """Move the answer file to done/ the instant success is confirmed."""
        if moved["done"]:
            return
        try:
            shutil.move(answer_path, os.path.join(done_path, os.path.basename(answer_path)))
            moved["done"] = True
            logging.info(f"Moved {os.path.basename(answer_path)} to done.")
        except FileNotFoundError:
            moved["done"] = True
            logging.warning(f"{os.path.basename(answer_path)} already moved to done.")
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

        # Give one step per question plus a buffer for welcome/submit/thank-you pages.
        max_pages = len(answers) + 20
        page_idx = 0
        current_ids = await get_visible_question_ids(page)

        while page_idx < max_pages:
            page_idx += 1
            logging.info(f"--- Page Step {page_idx} ---")
            
            # 1. FAST CHECK FOR SUCCESS
            if await check_for_success(page):
                logging.info("Success message detected! Form submitted.")
                mark_done()
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
                    mark_done()
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
                    mark_done()
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
                     mark_done()
                     return True
                 
                 logging.error("Lost: No visible questions and no navigation buttons.")
                 break

    except Exception as e:
        logging.error(f"Fatal error for {persona_id}: {e}")
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

# ==========================================
# RUNNER
# ==========================================
async def run():
    logging.info("===== RUNNING PHASE 4: FORM SUBMISSION (FINAL FIX) =====")

    done_path = os.path.join(config.ANSWERS_DIR_PATH, "done")
    inprogress_path = os.path.join(config.ANSWERS_DIR_PATH, "inprogress")
    os.makedirs(done_path, exist_ok=True)
    os.makedirs(inprogress_path, exist_ok=True)

    # --- ORPHAN RECOVERY ---
    # Reclaim files stranded in inprogress/ by a previous interrupted run back
    # into answers/ so they are retried instead of being stuck forever. Done
    # BEFORE building answer_files so recovered files are processed this run.
    for stranded in os.listdir(inprogress_path):
        if not stranded.endswith('.json'):
            continue
        src = os.path.join(inprogress_path, stranded)
        dst = os.path.join(config.ANSWERS_DIR_PATH, stranded)
        if os.path.exists(dst):
            # Already present in answers/ (e.g. retried manually): drop the stray claim.
            try:
                os.remove(src)
                logging.info(f"Removed duplicate stranded claim {stranded} (already in answers/).")
            except OSError as e:
                logging.error(f"Could not remove duplicate stranded claim {stranded}: {e}")
        else:
            try:
                os.rename(src, dst)
                logging.info(f"Recovered stranded file {stranded} from inprogress/ to answers/.")
            except OSError as e:
                logging.error(f"Could not recover {stranded} from inprogress/: {e}")

    answer_files = [f for f in os.listdir(config.ANSWERS_DIR_PATH) if f.endswith('.json')]
    if not answer_files:
        logging.warning("No answer files found.")
        return

    async with async_playwright() as p:
        for answer_file in answer_files:
            answer_path = os.path.join(config.ANSWERS_DIR_PATH, answer_file)
            claimed_path = os.path.join(inprogress_path, answer_file)

            # --- ATOMIC CLAIM ---
            # os.rename is atomic on a single filesystem: only ONE concurrent shell
            # can successfully move the file out of answers/. Everyone else gets
            # FileNotFoundError and skips, preventing duplicate submissions.
            try:
                os.rename(answer_path, claimed_path)
            except (FileNotFoundError, OSError):
                logging.info(f"{answer_file} already claimed by another worker. Skipping.")
                continue

            if config.USE_TOR:
                try: utils.renew_tor_ip()
                except: pass
                await asyncio.sleep(5)

            persona_id = answer_file.replace(".json", "")
            answers = utils.load_json_file(claimed_path, f"answers from {answer_file}")

            if not answers:
                # Nothing to submit — release the claim back to answers/.
                try: os.rename(claimed_path, answer_path)
                except OSError: pass
                continue

            # The file is moved from inprogress/ to done/ inside submit_single_form
            # the instant success is confirmed.
            try:
                success = await submit_single_form(p, answers, persona_id, claimed_path, done_path)
            except Exception as e:
                # Defensive: an escaped exception must not strand the file in
                # inprogress/ or abort the whole run. Treat as failure -> return
                # it to answers/ for retry. (KeyboardInterrupt is intentionally
                # NOT caught here so Ctrl+C still interrupts the run; stranded
                # files are reclaimed by the orphan-recovery pass on the next run.)
                logging.error(f"Fatal exception during submission for {persona_id}: {e}")
                success = False

            if not success:
                logging.error(f"Submission failed for {persona_id}.")
                # Release the claim so it can be retried on a later run.
                if os.path.exists(claimed_path):
                    try:
                        os.rename(claimed_path, answer_path)
                        logging.info(f"Returned {answer_file} to answers/ for retry.")
                    except OSError as e:
                        logging.error(f"Could not return {answer_file} to answers/: {e}")
            else:
                # Success: the form was submitted. If mark_done() somehow failed
                # to move the file to done/ (rare local-IO error), move it now so
                # it is neither stranded in inprogress/ nor re-submitted (duplicate).
                if os.path.exists(claimed_path):
                    logging.warning(f"{answer_file} succeeded but is still in inprogress/. Moving to done/.")
                    try:
                        shutil.move(claimed_path, os.path.join(done_path, answer_file))
                    except Exception as e:
                        logging.error(f"Could not move {answer_file} to done/ after success: {e}")

            logging.info("Waiting 5s...")
            await asyncio.sleep(5)