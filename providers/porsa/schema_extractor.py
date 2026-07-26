# providers/porsa/schema_extractor.py

# ===== IMPORTS & DEPENDENCIES =====
import asyncio
import logging
import re
from typing import List, Dict, Any

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page

import config
import utils

# ===== PORSA BACKGROUND =====
# Porsa (porsa.irandoc.ac.ir) is a jQuery/Bootstrap survey platform. A form is a
# single <form id="pageForm" method="POST"> holding one <div id="page_N"
# class="page-class"> per page. Each question is a
#   <div class="questionsBorder" id="questionIdN" data-type="<TYPE>">
# container. The platform's own functionality.js (`clear_answer`) enumerates the
# data-types and their DOM:
#
#   likert              tr.likertText <td> (option labels) parallel to
#                       tr.likertCheck <td> input[type=radio]  — ONE radio group,
#                       options are the columns.
#   matrixSingleChoice  <tbody> first row = header <th> column labels; each
#                       following row = sub-question, one input[type=radio] group
#                       per row (name=msc_1_X_N, value=1..K).
#   matrixMultipleChoice same layout but input[type=checkbox] per row.
#   singleChoice        option label in a <th>, input[type=radio] in the next <td>.
#   multipleChoice      same but input[type=checkbox].
#   textAnswer          <textarea>
#   textEmail           <input type="email">
#   textNumber          <input type="number"> or <input type="text" data-type="number">
#   textDate            a MdPersianDateTimePicker widget.
#
# Navigation: #prevButton / #nextButton step between pages; #submitButton
# ("ثبت نهایی") does the final POST. The sample form is a single page
# (SurveyStatus.surveyTotalPageNumber = 1).
#
# The schema produced follows the project's standard shape
# {question_id, question_text, type, options:[{text,value}]} where `value` is the
# human-readable option label (so core/answer_generator.py can show it to the LLM
# and fuzzy-match the reply). The submitter re-derives the underlying numeric
# input value from the live DOM at click time.

# ===== TEXT UTILITIES =====

# Persian & standard whitespace that porsa sprinkles between cells (non-breaking
# space &nbsp;= , ZWNJ=‌) — collapsed to a plain space for matching.
_WS_RE = re.compile(r'\s+')


def clean_text(text: str) -> str:
    """Normalise &nbsp;/ZWNJ and collapse whitespace. Does NOT strip leading
    item numbers — likert options such as "18 تا 27 سال" must be preserved."""
    if not text:
        return ""
    text = text.replace(' ', ' ').replace('‌', ' ')
    return _WS_RE.sub(' ', text).strip()


def _question_title(container: BeautifulSoup) -> str:
    """The question header text. Porsa renders the title in
    `div.question-header div.textareaStyle.questionBreak` and an optional
    description in a sibling `div.textareaStyle2.questionBreak`; the class token
    `textareaStyle` (without the `2`) selects only the title."""
    node = container.select_one('div.question-header div.textareaStyle.questionBreak')
    return clean_text(node.get_text(' ', strip=True)) if node else ""


# ===== OPTION / ROW PARSING =====

def _likert_options(container: BeautifulSoup) -> List[Dict[str, str]]:
    """Pair the likert option-label cells (tr.likertText td) with the radios in
    tr.likertCheck by column index — the platform's `.click-choice-likert-check`
    handler does exactly this index pairing."""
    label_cells = container.select('tr.likertText > td')
    radios = container.select('tr.likertCheck input[type="radio"]')
    options: List[Dict[str, str]] = []
    for i, radio in enumerate(radios):
        label = clean_text(label_cells[i].get_text(' ', strip=True)) if i < len(label_cells) else ""
        if not label:
            label = radio.get('value', '')
        options.append({"text": label, "value": label})
    return options


def _matrix_columns(container: BeautifulSoup) -> List[str]:
    """Column option labels from a matrix header row — the <th class="thvertical">
    cells, skipping the empty row-label corner cell that precedes them."""
    for row in container.select('tbody > tr'):
        ths = row.select('th.thvertical')
        if not ths:
            continue
        texts = [clean_text(th.get_text(' ', strip=True)) for th in ths]
        # drop leading empty corner cell(s) so the columns line up with the
        # per-row inputs
        while texts and not texts[0]:
            texts.pop(0)
        return texts
    return []


def _matrix_rows(container: BeautifulSoup, input_type: str):
    """Yield (row_text, first_input) for each data row of a matrix (skipping the
    header row). `input_type` is 'radio' or 'checkbox'."""
    for row in container.select('tbody > tr'):
        if row.select('th.thvertical'):
            continue  # header row
        first_input = row.find('input', {'type': input_type})
        if not first_input or not first_input.has_attr('name'):
            continue
        label_cell = row.find('td')
        row_text = clean_text(label_cell.get_text(' ', strip=True)) if label_cell else ""
        yield row_text, first_input


# ===== PER-TYPE PARSERS =====

def parse_likert(container: BeautifulSoup) -> Dict[str, Any] | None:
    radio = container.select_one('input[type="radio"]')
    if not radio or not radio.has_attr('name'):
        return None
    return {
        "question_id": radio['name'],
        "question_text": _question_title(container),
        "type": "RADIO",
        "options": _likert_options(container),
    }


def parse_matrix(container: BeautifulSoup, input_type: str, q_type: str) -> List[Dict[str, Any]]:
    columns = _matrix_columns(container)
    out: List[Dict[str, Any]] = []
    for row_text, first_input in _matrix_rows(container, input_type):
        out.append({
            "question_id": first_input['name'],
            "question_text": row_text,
            "type": q_type,
            "options": [{"text": c, "value": c} for c in columns],
        })
    return out


def _choice_options(container: BeautifulSoup, inputs) -> List[Dict[str, str]]:
    """singleChoice/multipleChoice: option labels live in <th> cells
    (`.click-choice-check` / `.click-multichoice-check`); pair them with the
    inputs by document order."""
    label_ths = container.select('th.click-choice-check, th.click-multichoice-check')
    if not label_ths:
        label_ths = container.select('th')
    options: List[Dict[str, str]] = []
    for i, inp in enumerate(inputs):
        label = clean_text(label_ths[i].get_text(' ', strip=True)) if i < len(label_ths) else ""
        if not label:
            label = inp.get('value', '')
        options.append({"text": label, "value": label})
    return options


def parse_single_choice(container: BeautifulSoup, input_type: str, q_type: str) -> Dict[str, Any] | None:
    inputs = container.select(f'input[type="{input_type}"]')
    if not inputs or not inputs[0].has_attr('name'):
        return None
    qid = inputs[0]['name'].rstrip('[]')
    return {
        "question_id": qid,
        "question_text": _question_title(container),
        "type": q_type,
        "options": _choice_options(container, inputs),
    }


def parse_text(container: BeautifulSoup) -> Dict[str, Any] | None:
    """textAnswer (textarea) / textEmail / textNumber / textDate — all become a
    TEXT_INPUT whose id is the field's name attribute."""
    field = (container.select_one('textarea')
             or container.select_one('input[type="email"]')
             or container.select_one('input[type="number"]')
             or container.select_one('input[type="text"][data-type="number"]')
             or container.select_one('input[type="text"]'))
    if not field or not field.has_attr('name'):
        return None
    return {
        "question_id": field['name'],
        "question_text": _question_title(container),
        "type": "TEXT_INPUT",
        "options": [],
    }


def parse_question(container: BeautifulSoup) -> List[Dict[str, Any]]:
    """Dispatch one questionsBorder container to its type-specific parser."""
    dtype = container.get('data-type', '')
    if dtype == 'likert':
        q = parse_likert(container)
        return [q] if q else []
    if dtype == 'matrixSingleChoice':
        return parse_matrix(container, 'radio', 'MATRIX_RADIO')
    if dtype == 'matrixMultipleChoice':
        # Best-effort: mirrors matrixSingleChoice with checkboxes (no sample in
        # hand). The submitter checks the single column matching the answer.
        return parse_matrix(container, 'checkbox', 'CHECKBOX')
    if dtype == 'singleChoice':
        q = parse_single_choice(container, 'radio', 'RADIO')
        return [q] if q else []
    if dtype == 'multipleChoice':
        q = parse_single_choice(container, 'checkbox', 'CHECKBOX')
        return [q] if q else []
    if dtype in ('textAnswer', 'textEmail', 'textNumber', 'textDate'):
        q = parse_text(container)
        return [q] if q else []
    logging.warning(f"Porsa: unhandled question data-type '{dtype}' on #{container.get('id')}, skipping.")
    return []


def parse_page(html: str) -> List[Dict[str, Any]]:
    """Parse every question container out of a page's HTML."""
    soup = BeautifulSoup(html, 'html.parser')
    questions: List[Dict[str, Any]] = []
    for container in soup.select('div.questionsBorder[id^="questionId"]'):
        questions.extend(parse_question(container))
    return questions

# ===== DUMMY FILL (multi-page navigation) =====

async def fill_dummy_answers(page: Page) -> None:
    """Pick the first option of every visible radio/checkbox group so required-
    field validation lets a multi-page form advance with #nextButton. The
    sample form is single-page so this is only exercised on multi-page forms."""
    for container in await page.locator('div.questionsBorder[id^="questionId"]').all():
        try:
            if not await container.is_visible():
                continue
        except Exception:
            continue
        dtype = await container.get_attribute('data-type') or ''
        sel = None
        if dtype in ('likert', 'singleChoice'):
            sel = 'input[type="radio"]'
        elif dtype == 'matrixSingleChoice':
            sel = 'tbody tr input[type="radio"]'
        elif dtype in ('multipleChoice', 'matrixMultipleChoice'):
            sel = 'input[type="checkbox"]'
        if not sel:
            continue
        field = container.locator(sel).first
        try:
            if await field.count() and await field.is_visible():
                await field.check(force=True)
        except Exception:
            pass

# ===== CORE EXTRACTION =====

async def extract_porsa_schema(p) -> List[Dict[str, Any]]:
    logging.info("Connecting to Porsa...")
    browser = await p.chromium.launch(
        channel=config.BROWSER_CHANNEL,
        headless=config.HEADLESS_MODE,
        slow_mo=config.SLOW_MO,
    )
    page = await browser.new_page()
    final_schema: List[Dict[str, Any]] = []
    processed_ids: set = set()

    try:
        # Porsa renders the full question set server-side, so the DOM is ready at
        # DOMContentLoaded. We avoid "networkidle" because porsa pages can hold a
        # pending request open (token/asset) that never settles within the wait
        # window — same approach the porsline submitter takes.
        await page.goto(config.BASE_FORM_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)
        page_num = 0
        max_pages = 50

        while page_num < max_pages:
            page_num += 1
            logging.info(f"--- Processing Page {page_num} ---")

            html = await page.content()
            new_count = 0
            for q in parse_page(html):
                if q['question_id'] not in processed_ids:
                    final_schema.append(q)
                    processed_ids.add(q['question_id'])
                    new_count += 1
                    logging.info(f"   Found: [{q['type']}] {q['question_id']} — {q['question_text'][:60]}")
            logging.info(f"   New questions on this page: {new_count}")

            # Multi-page forms step with #nextButton; the final page shows
            # #submitButton ("ثبت نهایی"). We never submit during extraction.
            next_btn = page.locator('#nextButton')
            submit_btn = page.locator('#submitButton')

            if await next_btn.count() > 0 and await next_btn.first.is_visible():
                await fill_dummy_answers(page)
                await asyncio.sleep(0.5)
                await next_btn.first.click()
                try:
                    await page.wait_for_load_state('domcontentloaded', timeout=20000)
                except Exception:
                    pass
                await asyncio.sleep(1.5)
                continue

            if await submit_btn.count() > 0 and await submit_btn.first.is_visible():
                logging.info("Submit button reached. Schema extraction complete.")
                break

            logging.warning("No navigation button found. Ending extraction.")
            break

        logging.info(f"Schema extraction complete. Found {len(final_schema)} unique questions.")
    except Exception as e:
        logging.error(f"Extraction Error: {e}", exc_info=True)
    finally:
        await browser.close()

    return final_schema

# ===== RUNNER =====

async def run():
    logging.info("===== RUNNING PHASE 1: SCHEMA EXTRACTION (PORSA) =====")
    async with async_playwright() as p:
        data = await extract_porsa_schema(p)

    if data:
        utils.save_json_file(config.SCHEMA_FILE_PATH, data, "schema")
        logging.info(f"Extracted {len(data)} questions total.")
    else:
        logging.error("No data extracted.")

    logging.info("===== PHASE 1 FINISHED =====")