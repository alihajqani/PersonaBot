# core/answer_generator.py

import asyncio
import json
import logging
import os
import shutil
import random
from typing import List, Dict, Any, Tuple

from thefuzz import fuzz

import config
import utils
from core.llm_client import get_llm_client


# ===== PROMPT BUILDER =====

def build_answer_prompts(schema: List[Dict[str, Any]], persona_details: Dict[str, Any]) -> Tuple[str, str]:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_file_path = os.path.join(project_root, "prompts", "answer_generation_prompt.json")

    prompt_templates = utils.load_json_file(prompt_file_path, "answer generation prompts")
    if not prompt_templates:
        raise FileNotFoundError("Could not load answer generation prompts.")

    persona_json_str = json.dumps(persona_details, ensure_ascii=False, indent=2)

    questions_str = ""
    for index, question in enumerate(schema):
        q_text = question['question_text'].replace('\n*', '').strip()
        questions_str += f"--- Question {index + 1} ---\n"
        questions_str += f"ID: {question['question_id']}\n"
        questions_str += f"Question: \"{q_text}\"\n"
        if question.get("options"):
            option_values = [opt["value"] for opt in question["options"]]
            questions_str += "Type: RADIO — copy your answer EXACTLY from this list, character-for-character:\n"
            for opt in option_values:
                questions_str += f'  • "{opt}"\n'
        else:
            questions_str += "Type: TEXT INPUT — write a number string only, do not pick from any list.\n"

    system_instruction = prompt_templates['system_instruction'].format(persona_json_str=persona_json_str)
    user_prompt = prompt_templates['user_prompt_template'].format(questions_str=questions_str)

    return system_instruction, user_prompt


# ===== ANSWER VALIDATION =====

def validate_and_clean_answers(raw_answers: Dict[str, Any], schema: List[Dict[str, Any]]) -> Dict[str, Any]:
    logging.debug(f"Validating {len(raw_answers)} raw answers...")
    option_schema_map = {
        q['question_id']: {opt['value'] for opt in q['options']}
        for q in schema if q.get('options')
    }
    all_valid_ids = {q['question_id'] for q in schema}
    cleaned_answers = {}

    for question_id, raw_value in raw_answers.items():
        if question_id.startswith("_"):
            continue
        if question_id not in all_valid_ids:
            logging.warning(f"Rogue question_id '{question_id}' in LLM response. Discarding.")
            continue

        if question_id in option_schema_map:
            valid_options = option_schema_map[question_id]
            normalized = utils.normalize_string(str(raw_value))

            if normalized in valid_options:
                cleaned_answers[question_id] = normalized
                continue

            best_match, best_ratio = None, 85
            for option in valid_options:
                ratio = fuzz.ratio(normalized, option)
                if ratio > best_ratio:
                    best_ratio, best_match = ratio, option

            if best_match:
                logging.warning(
                    f"SELF-CORRECTION: Q_ID '{question_id}': '{raw_value}' → '{best_match}' ({best_ratio}%)"
                )
                cleaned_answers[question_id] = best_match
            else:
                logging.warning(
                    f"DISCARDING: Q_ID '{question_id}' — LLM gave '{raw_value}', valid: {valid_options}"
                )
        else:
            cleaned_answers[question_id] = str(raw_value).strip()

    missing = all_valid_ids - set(cleaned_answers.keys())
    if missing:
        logging.warning(f"Missing answers for {len(missing)} questions: {missing}")

    logging.debug(f"Validation done. Kept {len(cleaned_answers)} answers.")
    return cleaned_answers


def _extract_json(text: str) -> str:
    """Strips markdown code fences and extracts the first {...} block."""
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text


# ===== CORE LOGIC =====

async def generate_answers_for_persona(
    schema: List[Dict[str, Any]],
    persona: Dict[str, Any],
) -> Dict[str, Any]:
    persona_id = persona.get("id") or persona.get("persona_id", "unknown")
    logging.info(f"Generating answers for persona: {persona_id}...")

    try:
        system_instruction, user_prompt = build_answer_prompts(schema, persona['details'])
    except FileNotFoundError as e:
        logging.error(f"Failed to build answer prompts: {e}")
        return {}

    temperature = random.uniform(0.4, 0.7)
    response_text = ""

    try:
        client = get_llm_client()
        response_text = await client.generate(system_instruction, user_prompt, temperature)

        raw_answers = json.loads(_extract_json(response_text))
        logging.info(f"Received answers for persona: {persona_id}.")
        return validate_and_clean_answers(raw_answers, schema)

    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON for persona {persona_id}. Raw:\n{response_text}")
        return {}
    except Exception as e:
        logging.error(f"Error generating answers for {persona_id}: {e}", exc_info=True)
        return {}


# ===== RUNNER =====

async def run():
    logging.info("===== RUNNING PHASE: ANSWER GENERATION =====")

    schema_data = utils.load_json_file(config.SCHEMA_FILE_PATH, "form schema")
    if not schema_data:
        logging.error("Schema file not found. Run schema extraction first.")
        return

    persona_files = [f for f in os.listdir(config.PERSONAS_DIR_PATH) if f.endswith('.json')]
    if not persona_files:
        logging.error(f"No persona files in '{config.PERSONAS_DIR_PATH}'. Run persona generation first.")
        return

    done_dir = os.path.join(config.PERSONAS_DIR_PATH, "done")
    os.makedirs(done_dir, exist_ok=True)

    for persona_file in persona_files:
        persona_path = os.path.join(config.PERSONAS_DIR_PATH, persona_file)
        persona_data = utils.load_json_file(persona_path, f"persona {persona_file}")

        p_id = persona_data.get("id") or persona_data.get("persona_id") or os.path.splitext(persona_file)[0]
        if "details" not in persona_data:
            logging.warning(f"Skipping invalid persona file: {persona_file}")
            continue

        answers = await generate_answers_for_persona(schema=schema_data, persona=persona_data)

        if answers and len(answers) >= len(schema_data) * 0.8:
            utils.save_json_file(os.path.join(config.ANSWERS_DIR_PATH, persona_file), answers, f"answers for '{p_id}'")
            shutil.move(persona_path, os.path.join(done_dir, persona_file))
            logging.info(f"Persona '{p_id}' processed and moved to done/.")
        else:
            logging.error(f"Insufficient answers for '{p_id}'. Persona kept for retry.")

        if config.AI_CALL_DELAY_SECONDS > 0:
            logging.info(f"Waiting {config.AI_CALL_DELAY_SECONDS}s...")
            await asyncio.sleep(config.AI_CALL_DELAY_SECONDS)

    logging.info("===== PHASE FINISHED: ANSWER GENERATION =====")
