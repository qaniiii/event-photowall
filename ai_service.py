import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None

def analyze_and_moderate_image(image_bytes: bytes, mime_type: str, user_caption: str = "") -> tuple[bool, str, str]:
    clean_caption = user_caption.strip()

    if not client:
        return True, clean_caption, ""

    print(f"🤖 [AI] Analyzing photo with gemini-3.5-flash-lite ({len(image_bytes) / 1024:.1f} KB)...")

    prompt = f"""
    Analyze this photo uploaded to a live event photo wall.
    1. Safety check: Is this image safe for a family-friendly public wedding or party screen?
       Reject explicit adult nudity, graphic gore, illegal drugs, or overt hate symbols.
    2. Captioning:
       - If the user caption is provided: "{clean_caption}", return it exactly as is.
       - If the user caption is empty, generate a fun, punchy, 1-sentence social-media caption (with 1 or 2 emojis).

    Respond ONLY in valid JSON matching this schema:
    {{
      "is_safe": true,
      "reason": "",
      "caption": "Your generated caption here"
    }}
    """

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.7,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
            )
        )

        raw_text = response.text.strip()
        data = json.loads(raw_text)

        is_safe = data.get("is_safe", True)
        reason = data.get("reason", "")
        generated_caption = data.get("caption", "").strip()

        final_caption = clean_caption if clean_caption else generated_caption
        print(f"✅ [AI] Success: is_safe={is_safe}, caption='{final_caption}'")
        return is_safe, final_caption, reason

    except Exception as e:
        print(f"❌ [AI Error] Gemini call failed: {type(e).__name__} - {e}")
        return True, clean_caption, ""