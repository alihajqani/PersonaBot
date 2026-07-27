import asyncio

from google import genai
from google.genai import types


# =========================
# CONFIGURATION
# =========================

API_KEY = "AIzaSyAehwiZdf4NsosGsu_ziFfODudYH3Bhxo4"
MODEL_NAME = "gemma-4-31b-it"


async def main():
    if not API_KEY:
        print("❌ Please set API_KEY")
        return

    if not MODEL_NAME:
        print("❌ Please set MODEL_NAME")
        return

    try:
        print("Connecting to Gemini API...")

        client = genai.Client(
            api_key=API_KEY,
            http_options=types.HttpOptions(
                timeout=120000
            ),
        )

        response = await client.aio.models.generate_content(
            model=MODEL_NAME,
            contents="Say exactly: API CONNECTION IS WORKING goooood!",
            config=types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="application/json",
            ),
        )

        print("\n✅ API CONNECTION SUCCESSFUL")
        print("\nResponse:")
        print(response.text)

    except Exception as e:
        print("\n❌ API CONNECTION FAILED")
        print(f"Error type: {type(e).__name__}")
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())