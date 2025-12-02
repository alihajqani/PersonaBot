# core/persona_generator.py

# ===== IMPORTS & DEPENDENCIES =====
import json
import logging
import os
import uuid
import random 
from typing import List, Dict, Any, Tuple

import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

import config
import utils

# ===== PROMPT ENGINEERING LOGIC =====

def build_persona_prompts(schema: List[Dict[str, Any]], num_personas: int) -> Tuple[str, str]:
    """Loads persona prompts from JSON and formats them with dynamic data."""
    
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_file_path = os.path.join(project_root, "prompts", "persona_generation_prompt.json")
    
    prompt_templates = utils.load_json_file(prompt_file_path, "persona generation prompts")
    if not prompt_templates:
        raise FileNotFoundError("Could not load persona generation prompts.")

    # Prepare the dynamic content for the user prompt (schema summary)
    schema_summary = ""
    for q in schema:
        question_text = q['question_text']
        options = [opt.get('text', opt.get('value')) for opt in q.get('options', [])]
        schema_summary += f"- Question: \"{question_text}\"\n"
        if options:
            schema_summary += f"  Options: {', '.join(filter(None, options))}\n"
    
    # Format the prompts with the dynamic data
    system_instruction = prompt_templates['system_instruction'].format(num_personas=num_personas)
    
    user_prompt = prompt_templates['user_prompt_template'].format(
        schema_summary=schema_summary,
        num_personas=num_personas 
    )
    
    return system_instruction, user_prompt

# ===== CORE BUSINESS LOGIC =====

async def generate_and_save_personas(schema: List[Dict[str, Any]], num_personas: int):
    """Generates personas using the Gemini API and saves each to a separate JSON file."""
    if not config.google_api_key_manager or config.google_api_key_manager.get_key_count() == 0:
        logging.error("Google API Key Manager is not configured or has no keys. Aborting persona generation.")
        return

    logging.info(f"Generating {num_personas} personas using model '{config.GEMINI_MODEL_NAME}'...")
    
    api_key = config.google_api_key_manager.get_next_key()
    logging.info(f"Using Google API Key: {api_key}")
    genai.configure(api_key=api_key)
    
    try:
        system_instruction, user_prompt = build_persona_prompts(schema, num_personas)
    except FileNotFoundError as e:
        logging.error(f"Failed to build persona prompts: {e}")
        return

    safety_settings = {
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }
    
    # ===== Randomized Temperature =====
    # We use a random temperature between 0.75 and 0.95.
    # Higher temperature prevents "Modal Collapse" (all personas looking the same).
    # It encourages the model to create more diverse, unique, and outlier personalities.
    dynamic_temperature = random.uniform(0.75, 0.95)
    logging.info(f"Using dynamic temperature: {dynamic_temperature:.2f} for diversity.")

    generation_config = {
        "response_mime_type": "application/json",
        "temperature": dynamic_temperature,
        "top_p": 0.95 
    }
    
    model = genai.GenerativeModel(
        config.GEMINI_MODEL_NAME,
        system_instruction=system_instruction,
        generation_config=generation_config, # Updated config passed here
        safety_settings=safety_settings
    )
    
    response_text = ""
    try:
        response = await model.generate_content_async(user_prompt)
        
        if not response.candidates:
            feedback = response.prompt_feedback
            logging.error(f"Persona generation was blocked. Reason: {feedback.block_reason}")
            logging.error(f"Safety Ratings: {feedback.safety_ratings}")
            return

        response_text = response.text
        data = json.loads(response_text)
        
        # ===== Handle both List and Dict responses =====
        if isinstance(data, list):
            personas = data
        elif isinstance(data, dict):
            personas = data.get("personas", [])
        else:
            logging.error(f"Unexpected JSON structure. Expected list or dict, got: {type(data)}")
            return

        if not personas:
            logging.error("Persona generation failed: No personas found in the response.")
            return

        for persona in personas:
            human_readable_id = persona.get("id", "unnamed_persona")
            unique_filename = f"{uuid.uuid4()}.json"
            
            file_path = os.path.join(config.PERSONAS_DIR_PATH, unique_filename)
            utils.save_json_file(file_path, persona, f"persona '{human_readable_id}'")
            
    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON response from Gemini. Raw response:\n{response_text}")
    except Exception as e:
        logging.error(f"An error occurred during persona generation with Gemini API: {e}", exc_info=True)

# ===== RUNNER FUNCTION =====

async def run(num_personas: int):
    """Executes the persona generation phase."""
    logging.info("===== RUNNING PHASE: PERSONA GENERATION =====")

    schema_data = utils.load_json_file(config.SCHEMA_FILE_PATH, "form schema")
    if not schema_data:
        logging.error("Schema file not found. Please run the schema extraction phase first.")
        return
    
    await generate_and_save_personas(schema_data, num_personas)
    
    logging.info("===== PHASE FINISHED: PERSONA GENERATION =====")