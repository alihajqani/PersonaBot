# core/persona_generator.py

import asyncio
import json
import logging
import os
import uuid
import random
from typing import List, Dict, Any, Tuple

import config
import utils
from core import trait_sampler
from core.llm_client import get_llm_client


def build_persona_prompts(profiles: List[Dict[str, Any]]) -> Tuple[str, str]:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_file_path = os.path.join(project_root, "prompts", "persona_generation_prompt.json")

    prompt_templates = utils.load_json_file(prompt_file_path, "persona generation prompts")
    if not prompt_templates:
        raise FileNotFoundError("Could not load persona generation prompts.")

    profiles_spec = ""
    for p in profiles:
        profiles_spec += f"### {p['id']}\n{trait_sampler.format_profile_fa(p)}\n\n"

    num_personas = len(profiles)
    system_instruction = prompt_templates['system_instruction'].format(num_personas=num_personas)
    user_prompt = prompt_templates['user_prompt_template'].format(
        profiles_spec=profiles_spec.strip(),
        num_personas=num_personas,
    )

    return system_instruction, user_prompt


async def _render_batch(client, batch: List[Dict[str, Any]], idx: int, total: int) -> List[Dict[str, Any]]:
    """Renders one batch of personas. Returns [] on failure so other batches still run."""
    try:
        system_instruction, user_prompt = build_persona_prompts(batch)
    except FileNotFoundError as e:
        logging.error(f"Failed to build persona prompts: {e}")
        return []

    temperature = random.uniform(0.75, 0.95)
    logging.info(f"[Batch {idx}/{total}] Rendering {len(batch)} personas. Temperature: {temperature:.2f}")

    response_text = ""
    try:
        response_text = await client.generate(system_instruction, user_prompt, temperature)
        data = json.loads(response_text)
        return data if isinstance(data, list) else data.get("personas", [])
    except json.JSONDecodeError:
        logging.error(f"[Batch {idx}/{total}] Failed to decode JSON from LLM response. Raw:\n{response_text}")
        return []
    except Exception as e:
        logging.error(f"[Batch {idx}/{total}] Generation failed: {e}", exc_info=True)
        return []


async def generate_and_save_personas(schema: List[Dict[str, Any]], num_personas: int):
    logging.info(f"Sampling {num_personas} latent-trait profiles...")
    profiles = trait_sampler.sample_population(num_personas)
    profiles_by_id = {p["id"]: p for p in profiles}

    client = get_llm_client()

    batch_size = max(1, config.PERSONA_BATCH_SIZE)
    batches = [profiles[i:i + batch_size] for i in range(0, len(profiles), batch_size)]
    logging.info(f"Rendering persona prose in {len(batches)} batch(es) of up to {batch_size}.")

    saved = 0
    for idx, batch in enumerate(batches, 1):
        rendered = await _render_batch(client, batch, idx, len(batches))

        for item in rendered:
            pid = item.get("id")
            base = profiles_by_id.get(pid)
            if not base or "details" not in item:
                logging.warning(f"Skipping persona with unknown id or missing details: {pid}")
                continue
            # merge the sampled ground-truth profile with the LLM-rendered prose
            persona = {
                "id": pid,
                "demographics": base["demographics"],
                "latent_traits_z": base["latent_traits_z"],
                "latent_traits_band": base["latent_traits_band"],
                "response_style": base["response_style"],
                "details": item["details"],
            }
            file_path = os.path.join(config.PERSONAS_DIR_PATH, f"{uuid.uuid4()}.json")
            utils.save_json_file(file_path, persona, f"persona '{pid}'")
            saved += 1

        if config.AI_CALL_DELAY_SECONDS and idx < len(batches):
            await asyncio.sleep(config.AI_CALL_DELAY_SECONDS)

    logging.info(f"Saved {saved}/{num_personas} personas.")


async def run(num_personas: int):
    logging.info("===== RUNNING PHASE: PERSONA GENERATION =====")

    schema_data = utils.load_json_file(config.SCHEMA_FILE_PATH, "form schema")
    if not schema_data:
        logging.error("Schema file not found. Run schema extraction first.")
        return

    await generate_and_save_personas(schema_data, num_personas)

    logging.info("===== PHASE FINISHED: PERSONA GENERATION =====")
