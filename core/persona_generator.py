# core/persona_generator.py

import json
import logging
import os
import uuid
import random
from typing import List, Dict, Any, Tuple

import config
import utils
from core.llm_client import get_llm_client


def build_persona_prompts(schema: List[Dict[str, Any]], num_personas: int) -> Tuple[str, str]:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_file_path = os.path.join(project_root, "prompts", "persona_generation_prompt.json")

    prompt_templates = utils.load_json_file(prompt_file_path, "persona generation prompts")
    if not prompt_templates:
        raise FileNotFoundError("Could not load persona generation prompts.")

    schema_summary = ""
    for q in schema:
        options = [opt.get('text', opt.get('value')) for opt in q.get('options', [])]
        schema_summary += f"- Question: \"{q['question_text']}\"\n"
        if options:
            schema_summary += f"  Options: {', '.join(filter(None, options))}\n"

    system_instruction = prompt_templates['system_instruction'].format(num_personas=num_personas)
    user_prompt = prompt_templates['user_prompt_template'].format(
        schema_summary=schema_summary,
        num_personas=num_personas,
    )

    return system_instruction, user_prompt


async def generate_and_save_personas(schema: List[Dict[str, Any]], num_personas: int):
    logging.info(f"Generating {num_personas} personas...")

    try:
        system_instruction, user_prompt = build_persona_prompts(schema, num_personas)
    except FileNotFoundError as e:
        logging.error(f"Failed to build persona prompts: {e}")
        return

    temperature = random.uniform(0.75, 0.95)
    logging.info(f"Temperature: {temperature:.2f}")

    response_text = ""
    try:
        client = get_llm_client()
        response_text = await client.generate(system_instruction, user_prompt, temperature)

        data = json.loads(response_text)
        personas = data if isinstance(data, list) else data.get("personas", [])

        if not personas:
            logging.error("No personas found in the LLM response.")
            return

        for persona in personas:
            human_readable_id = persona.get("id", "unnamed_persona")
            file_path = os.path.join(config.PERSONAS_DIR_PATH, f"{uuid.uuid4()}.json")
            utils.save_json_file(file_path, persona, f"persona '{human_readable_id}'")

    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON from LLM response. Raw:\n{response_text}")
    except Exception as e:
        logging.error(f"Error during persona generation: {e}", exc_info=True)


async def run(num_personas: int):
    logging.info("===== RUNNING PHASE: PERSONA GENERATION =====")

    schema_data = utils.load_json_file(config.SCHEMA_FILE_PATH, "form schema")
    if not schema_data:
        logging.error("Schema file not found. Run schema extraction first.")
        return

    await generate_and_save_personas(schema_data, num_personas)

    logging.info("===== PHASE FINISHED: PERSONA GENERATION =====")
