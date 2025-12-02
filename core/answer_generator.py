# core/answer_generator.py

# ===== IMPORTS & DEPENDENCIES =====
import asyncio
import json
import logging
import os
import shutil
import random
from typing import List, Dict, Any, Tuple

import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from thefuzz import fuzz

import config
import utils

# ===== PROMPT ENGINEERING & UTILITIES =====

def build_answer_prompts(schema: List[Dict[str, Any]], persona_details: Dict[str, Any]) -> Tuple[str, str]:
    """Loads answer prompts from JSON and formats them with dynamic data."""
    
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_file_path = os.path.join(project_root, "prompts", "answer_generation_prompt.json")
    
    prompt_templates = utils.load_json_file(prompt_file_path, "answer generation prompts")
    if not prompt_templates:
        raise FileNotFoundError("Could not load answer generation prompts.")
        
    # Prepare dynamic content for the system instruction (persona)
    persona_json_str = json.dumps(persona_details, ensure_ascii=False, indent=2)
    
    # Prepare dynamic content for the user prompt (questions)
    questions_str = ""
    for index, question in enumerate(schema):
        q_text = question['question_text'].replace('\n*', '').strip()
        
        question_block = f"--- Question {index + 1} ---\n"
        question_block += f"ID: {question['question_id']}\n"
        question_block += f"Question: \"{q_text}\"\n"
        
        if question.get("options"):
            option_values = [opt["value"] for opt in question["options"]]
            question_block += f"Options: {json.dumps(option_values, ensure_ascii=False)}\n"
        else:
            question_block += "Type: Text Input\n"
            
        questions_str += question_block
        
    # Format the prompts with the dynamic data
    system_instruction = prompt_templates['system_instruction'].format(persona_json_str=persona_json_str)
    user_prompt = prompt_templates['user_prompt_template'].format(questions_str=questions_str)
    
    return system_instruction, user_prompt

def validate_and_clean_answers(raw_answers: Dict[str, Any], schema: List[Dict[str, Any]]) -> Dict[str, Any]:
    # This function remains highly valuable for data quality. No changes needed.
    logging.debug(f"Validating {len(raw_answers)} raw answers against the form schema...")
    option_schema_map = {
        q['question_id']: {opt['value'] for opt in q['options']}
        for q in schema if q.get('options')
    }
    all_valid_ids = {q['question_id'] for q in schema}
    cleaned_answers = {}

    for question_id, raw_answer_value in raw_answers.items():
        # Ignore meta keys like _reasoning or _internal_thought
        if question_id.startswith("_"):
            continue

        if question_id not in all_valid_ids:
            logging.warning(f"Rogue question_id '{question_id}' in LLM response. Discarding.")
            continue
        if question_id in option_schema_map:
            valid_options = option_schema_map[question_id]
            normalized_answer = utils.normalize_string(str(raw_answer_value))
            if normalized_answer in valid_options:
                cleaned_answers[question_id] = normalized_answer
                continue
            best_match, highest_ratio = None, 85
            for option in valid_options:
                ratio = fuzz.ratio(normalized_answer, option)
                if ratio > highest_ratio:
                    highest_ratio, best_match = ratio, option
            if best_match:
                logging.warning(
                    f"SELF-CORRECTION: For Q_ID '{question_id}', answer '{raw_answer_value}' "
                    f"was fuzzy-matched to '{best_match}' ({highest_ratio}% confidence)."
                )
                cleaned_answers[question_id] = best_match
            else:
                logging.warning(
                    f"DISCARDING: Invalid option for Q_ID '{question_id}'. "
                    f"LLM provided: '{raw_answer_value}'. Valid options: {valid_options}."
                )
        else:
            cleaned_answers[question_id] = str(raw_answer_value).strip()
            
    missing_ids = all_valid_ids - set(cleaned_answers.keys())
    if missing_ids:
        logging.warning(f"LLM did not provide valid answers for {len(missing_ids)} questions: {missing_ids}")
        
    logging.debug(f"Validation complete. Retained {len(cleaned_answers)} valid answers.")
    return cleaned_answers

def extract_json_from_string(text: str) -> str:
    try:
        start_index = text.find('{')
        end_index = text.rfind('}')
        if start_index != -1 and end_index != -1 and end_index > start_index:
            return text[start_index : end_index + 1]
    except Exception:
        return text

# ===== CORE BUSINESS LOGIC =====
async def generate_answers_for_persona(
    schema: List[Dict[str, Any]], 
    persona: Dict[str, Any],
) -> Dict[str, Any]:
    # ===== Check for 'id' OR 'persona_id' =====
    persona_id = persona.get("id") or persona.get("persona_id", "unknown_persona")
    
    logging.info(f"Generating answers for persona: {persona_id}...")
    
    api_key = config.google_api_key_manager.get_next_key()
    logging.info(f"Using Google API Key: {api_key}")
    genai.configure(api_key=api_key)
    
    try:
        system_instruction, user_prompt = build_answer_prompts(schema, persona['details'])
    except FileNotFoundError as e:
        logging.error(f"Failed to build answer prompts: {e}")
        return {}
    
    # Using randomized temperature for better human-like variance
    dynamic_temperature = random.uniform(0.4, 0.7)
    
    generation_config = {
        "response_mime_type": "application/json",
        "temperature": dynamic_temperature
    }
    
    safety_settings = {
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }

    try:
        model = genai.GenerativeModel(
            model_name=config.GEMINI_MODEL_NAME,
            system_instruction=system_instruction,
            generation_config=generation_config,
            safety_settings=safety_settings
        )

        response = await model.generate_content_async(user_prompt)
        response_text = response.text
        
        cleaned_json_string = extract_json_from_string(response_text)
        raw_answers = json.loads(cleaned_json_string)
        
        logging.info(f"Successfully received and parsed answers for persona: {persona_id}.")
        validated_answers = validate_and_clean_answers(raw_answers, schema)
        return validated_answers

    except Exception as e:
        logging.error(f"An unexpected error occurred with Gemini API for persona {persona_id}: {e}", exc_info=True)
        return {}
    
# ===== RUNNER FUNCTION =====
async def run():
    logging.info("===== RUNNING PHASE: ANSWER GENERATION =====")
    
    if not config.google_api_key_manager or config.google_api_key_manager.get_key_count() == 0:
        logging.error("Google API Key Manager is not configured or has no keys. Aborting.")
        return

    schema_data = utils.load_json_file(config.SCHEMA_FILE_PATH, "form schema")
    if not schema_data:
        logging.error("Schema file not found. Please run schema extraction first.")
        return
        
    persona_files = [f for f in os.listdir(config.PERSONAS_DIR_PATH) if f.endswith('.json')]
    if not persona_files:
        logging.error(f"No persona files found in '{config.PERSONAS_DIR_PATH}'. Please run persona generation first.")
        return

    done_dir_path = os.path.join(config.PERSONAS_DIR_PATH, "done")
    os.makedirs(done_dir_path, exist_ok=True)
    
    for persona_file in persona_files:
        persona_path = os.path.join(config.PERSONAS_DIR_PATH, persona_file)
        persona_data = utils.load_json_file(persona_path, f"persona from {persona_file}")

        # ===== Check for 'id' OR 'persona_id' =====
        p_id = persona_data.get("id") or persona_data.get("persona_id")

        if not persona_data or not p_id or "details" not in persona_data:
            logging.warning(f"Skipping invalid persona file: {persona_file} (Missing 'id'/'persona_id' or 'details')")
            continue
        
        human_readable_id = p_id
        
        answers = await generate_answers_for_persona(schema=schema_data, persona=persona_data)
        
        if answers and len(answers) > len(schema_data) * 0.8:
            answer_file_path = os.path.join(config.ANSWERS_DIR_PATH, persona_file)
            utils.save_json_file(answer_file_path, answers, f"answers for '{human_readable_id}'")

            processed_persona_path = os.path.join(done_dir_path, persona_file)
            shutil.move(persona_path, processed_persona_path)
            logging.info(f"Moved processed persona '{human_readable_id}' to 'done' directory.")
        else:
            logging.error(f"Failed to generate sufficient valid answers for '{human_readable_id}'. Persona file will not be moved.")
        
        logging.info("Waiting for 20 seconds before the next API call to respect rate limits...")
        await asyncio.sleep(20)

    logging.info("===== PHASE FINISHED: ANSWER GENERATION =====")