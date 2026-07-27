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
from core import instrument, trait_sampler
from core.llm_client import get_llm_client


# ===== PROMPT BUILDER (per construct chunk) =====

def build_chunk_prompts(
    questions: List[Dict[str, Any]],
    persona: Dict[str, Any],
    construct_key: str,
) -> Tuple[str, str]:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_file_path = os.path.join(project_root, "prompts", "answer_generation_prompt.json")

    prompt_templates = utils.load_json_file(prompt_file_path, "answer generation prompts")
    if not prompt_templates:
        raise FileNotFoundError("Could not load answer generation prompts.")

    construct_label, trait_dims = instrument.CONSTRUCT_TRAIT_DIMS.get(
        construct_key, ("سایر", list(trait_sampler.LOADINGS.keys()))
    )

    # persona context = demographics + the prose details (no raw z-scores leaked as numbers)
    persona_ctx = {
        "demographics": persona.get("demographics", {}),
        "details": persona.get("details", {}),
    }
    persona_json_str = json.dumps(persona_ctx, ensure_ascii=False, indent=2)

    # only the trait bands relevant to THIS construct, as words (not z-scores)
    trait_profile_str = trait_sampler.format_profile_fa(persona, dims=trait_dims)
    response_style_str = instrument.response_style_instruction(persona.get("response_style", {}))

    questions_str = ""
    for index, question in enumerate(questions):
        q_text = question['question_text'].replace('\n*', '').strip()
        questions_str += f"--- Question {index + 1} ---\n"
        questions_str += f"ID: {question['question_id']}\n"
        questions_str += f"Question: \"{q_text}\"\n"
        if question.get("options"):
            option_values = [opt["value"] for opt in question["options"]]
            questions_str += "Type: RADIO — copy your answer EXACTLY from this list, character-for-character:\n"
            for opt in option_values:
                questions_str += f'  • "{opt}"\n'
            # Per-item direction tag for constructs whose items mix direct and
            # reverse wording. [معکوس] = the HIGH/agree end means LOWER of your
            # trait for this item (answer inverted); [مستقیم] = HIGH/agree = HIGHER.
            # The set of reverse-keyed constructs is owned by instrument.py so this
            # file stays free of any survey-specific construct names.
            if construct_key in instrument.REVERSE_KEYED_CONSTRUCTS:
                if instrument.is_reverse(question, construct_key):
                    questions_str += "Direction: [معکوس] — the HIGH/agree end of THIS scale means LOWER of your trait here (answer inverted).\n"
                else:
                    questions_str += "Direction: [مستقیم] — the HIGH/agree end means HIGHER of your trait here.\n"
        else:
            questions_str += "Type: TEXT INPUT — write a number string only, do not pick from any list.\n"

    system_instruction = prompt_templates['system_instruction'].format(
        construct_label=construct_label,
        persona_json_str=persona_json_str,
        trait_profile_str=trait_profile_str,
        response_style_str=response_style_str,
    )
    user_prompt = prompt_templates['user_prompt_template'].format(
        construct_label=construct_label,
        questions_str=questions_str,
    )

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

    # --- Demographic questions are answered directly from the persona (never by
    # the LLM): a persona's age band, gender, education, marital status and
    # daily social-media use are taken straight from her profile, guaranteeing
    # identity consistency across the whole questionnaire. ---
    all_answers: Dict[str, Any] = instrument.static_answers(schema, persona)

    # --- careless / inattentive responders: straightline the psychometric items in code ---
    if persona.get("response_style", {}).get("careless"):
        logging.info(f"Persona {persona_id} is a careless responder — generating straightlining pattern.")
        rng = random.Random(hash(persona_id) & 0xFFFFFFFF)
        all_answers.update(instrument.careless_answers(schema, rng))
        return all_answers

    logging.info(f"Generating answers for persona: {persona_id} (chunked by construct)...")
    client = get_llm_client()

    for construct_key, questions in instrument.group_by_construct(schema):
        # shuffle within the chunk so items are not presented in scale order
        chunk = list(questions)
        random.shuffle(chunk)

        try:
            system_instruction, user_prompt = build_chunk_prompts(chunk, persona, construct_key)
        except FileNotFoundError as e:
            logging.error(f"Failed to build answer prompts: {e}")
            return {}

        temperature = random.uniform(0.55, 0.85)
        response_text = ""
        try:
            response_text = await client.generate(system_instruction, user_prompt, temperature)
            raw = json.loads(_extract_json(response_text))
            cleaned = validate_and_clean_answers(raw, chunk)
            all_answers.update(cleaned)
            logging.info(f"  [{persona_id}] construct '{construct_key}': {len(cleaned)}/{len(chunk)} answered.")
        except json.JSONDecodeError:
            logging.error(f"  [{persona_id}] construct '{construct_key}': JSON decode failed. Raw:\n{response_text}")
        except Exception as e:
            logging.error(f"  [{persona_id}] construct '{construct_key}' error: {e}", exc_info=True)

        if config.AI_CALL_DELAY_SECONDS > 0:
            await asyncio.sleep(config.AI_CALL_DELAY_SECONDS)

    return all_answers


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
