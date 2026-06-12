# core/llm_client.py

import logging

import config


def _is_quota_error(e: Exception) -> bool:
    """Returns True for rate-limit / quota-exhausted errors that warrant a key rotation."""
    msg = str(e).lower()
    return any(kw in msg for kw in ("429", "quota", "rate", "resource_exhausted", "too many requests"))


# ===== BASE CLASS =====

class LLMClient:
    """Unified interface for all LLM backends."""

    async def generate(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        """
        Sends a chat request and returns the raw text response.
        Always requests JSON output (json_mode=True).
        """
        raise NotImplementedError


# ===== GEMINI BACKEND =====

class GeminiClient(LLMClient):
    """
    Google Gemini via the new google-genai SDK (v1+).
    Uses round-robin key rotation from config.gemini_api_key_manager.
    """

    async def generate(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        from google import genai
        from google.genai import types

        num_keys = config.gemini_api_key_manager.get_key_count()
        last_error: Exception | None = None

        for attempt in range(num_keys):
            api_key = config.gemini_api_key_manager.get_next_key()
            try:
                client = genai.Client(api_key=api_key)
                response = await client.aio.models.generate_content(
                    model=config.GEMINI_MODEL_NAME,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=temperature,
                        top_p=0.95,
                        response_mime_type="application/json",
                        safety_settings=[
                            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",        threshold="BLOCK_NONE"),
                            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",       threshold="BLOCK_NONE"),
                            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                        ],
                    ),
                )

                if not response.candidates:
                    raise RuntimeError(f"Gemini response blocked. Feedback: {response.prompt_feedback}")

                return response.text

            except Exception as e:
                if _is_quota_error(e):
                    last_error = e
                    logging.warning(
                        f"[Gemini] Key ...{api_key[-4:]} quota/rate-limited "
                        f"(attempt {attempt + 1}/{num_keys}). Rotating to next key..."
                    )
                    continue
                raise

        raise RuntimeError(f"All {num_keys} Gemini API key(s) exhausted. Last error: {last_error}")


# ===== vLLM BACKEND =====

class VLLMClient(LLMClient):
    """
    Any OpenAI-compatible API endpoint (vLLM, Ollama, LM Studio, etc.).
    Connects to config.VLLM_BASE_URL with config.VLLM_MODEL_NAME.
    """

    async def generate(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url=config.VLLM_BASE_URL, api_key=config.VLLM_API_KEY)

        response = await client.chat.completions.create(
            model=config.VLLM_MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=temperature,
            top_p=0.95,
            response_format={"type": "json_object"},
        )

        return response.choices[0].message.content


# ===== FACTORY =====

def get_llm_client() -> LLMClient:
    """Returns the configured LLM client based on config.AI_PROVIDER."""
    provider = config.AI_PROVIDER

    if provider == "gemini":
        if not config.gemini_api_key_manager:
            raise RuntimeError(
                "AI_PROVIDER=gemini but no GEMINI_API_KEY_* keys are configured in .env"
            )
        logging.info(f"[LLM] Gemini | model={config.GEMINI_MODEL_NAME}")
        return GeminiClient()

    if provider == "vllm":
        if not config.VLLM_MODEL_NAME:
            raise RuntimeError(
                "AI_PROVIDER=vllm but VLLM_MODEL_NAME is not set in .env"
            )
        logging.info(f"[LLM] vLLM | model={config.VLLM_MODEL_NAME} @ {config.VLLM_BASE_URL}")
        return VLLMClient()

    raise ValueError(f"Unknown AI_PROVIDER='{provider}'. Supported: 'gemini', 'vllm'")
