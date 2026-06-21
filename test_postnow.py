"""
BABU /postnow Preview Tester
=============================
Generates the daily marketing post (caption + FLUX graphic) and sends
the resulting image directly to your Telegram so you can inspect it.
"""
import os
import sys
import urllib.request
import urllib.parse
import json
import requests

# Force UTF-8 encoding for Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Load environment
from dotenv import load_dotenv
load_dotenv()

# Ensure GEMINI_API_KEY is set
if not os.environ.get("GEMINI_API_KEY"):
    if os.environ.get("GROQ_API_KEY"):
        os.environ["GEMINI_API_KEY"] = os.environ["GROQ_API_KEY"]
    else:
        raise RuntimeError("GEMINI_API_KEY or GROQ_API_KEY is required in environment for this test.")

# Add babu to path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)
sys.path.append(os.path.join(CURRENT_DIR, "babu"))

from babu.social_media import generate_daily_post, generate_pillow_graphic, generate_flux_graphic

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_USER_CHAT_ID")



def send_photo_to_telegram(image_path: str, caption: str):
    """Send a photo to the user's Telegram chat using the Bot API via requests."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    
    # Prepare payload
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "caption": caption[:1024],
        "parse_mode": "Markdown"
    }
    
    # Open image file
    with open(image_path, "rb") as f:
        files = {"photo": (os.path.basename(image_path), f, "image/jpeg")}
        response = requests.post(url, data=data, files=files, timeout=30)
    
    if response.status_code != 200:
        print(f"[TELEGRAM ERROR] Initial send failed with status {response.status_code}.")
        try:
            err_json = response.json()
            print(f"[TELEGRAM ERROR DETAILS] {json.dumps(err_json, indent=2)}")
        except Exception:
            print(f"[TELEGRAM ERROR BODY] {response.text}")
        
        # Fallback 1: Try without parse_mode (in case it was a markdown formatting issue)
        print("[TELEGRAM] Retrying without markdown parse_mode...")
        data.pop("parse_mode", None)
        with open(image_path, "rb") as f:
            files = {"photo": (os.path.basename(image_path), f, "image/jpeg")}
            response2 = requests.post(url, data=data, files=files, timeout=30)
        
        if response2.status_code == 200:
            print("[TELEGRAM] Fallback succeeded! Message sent without markdown formatting.")
            return True
        else:
            print(f"[TELEGRAM ERROR] Fallback failed with status {response2.status_code}.")
            try:
                print(f"[TELEGRAM FALLBACK ERROR DETAILS] {json.dumps(response2.json(), indent=2)}")
            except Exception:
                print(f"[TELEGRAM FALLBACK ERROR BODY] {response2.text}")
            response2.raise_for_status()
            
    result = response.json()
    return result.get("ok", False)


def main():
    print("=" * 60)
    print("BABU /postnow Preview — Generate & Send to Telegram")
    print("=" * 60)

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_USER_CHAT_ID missing from .env")
        sys.exit(1)

    # Step 1: Generate caption and image prompt
    print("\n[STEP 1] Generating daily post via Gemini (with model fallback)...")
    try:
        caption, img_prompt, card_title, card_tips, category = generate_daily_post()
    except Exception as e:
        print(f"❌ Post generation failed: {e}")
        sys.exit(1)

    print(f"\n✨ Caption:\n{caption}")
    print(f"\n🎨 FLUX Prompt:\n{img_prompt}")
    print(f"\n🏷️ Card Title:\n{card_title}")
    print(f"\n💡 Card Tips:\n{card_tips}")
    print(f"\n🏷️ Category:\n{category}")

    # Step 2: Generate Infographic Card
    print("\n[STEP 2] Generating high-fidelity hybrid graphic card...")
    bg_path = None
    try:
        print("[SOCIAL] Attempting to generate rich FLUX background image...", flush=True)
        bg_path = generate_flux_graphic(img_prompt)
    except Exception as e:
        print(f"[SOCIAL WARNING] Rich background generation failed: {e}. Falling back to default layout.", flush=True)
        
    try:
        img_path = generate_pillow_graphic(card_title, card_tips, background_path=bg_path, category=category)
    except Exception as e:
        print(f"❌ Graphic rendering failed: {e}")
        sys.exit(1)

    print(f"✅ Image saved: {img_path} ({os.path.getsize(img_path)} bytes)")

    # Step 3: Send to Telegram
    print("\n[STEP 3] Sending preview to your Telegram...")
    tg_caption = f"🔍 *BABU /postnow Preview*\n\n{caption}"
    try:
        ok = send_photo_to_telegram(img_path, tg_caption)
        if ok:
            print("✅ SUCCESS: Image sent to your Telegram chat!")
        else:
            print("⚠️ Telegram API returned non-ok response.")
    except Exception as e:
        print(f"❌ Failed to send to Telegram: {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("PREVIEW COMPLETE — Check your Telegram for the image!")
    print("=" * 60)


if __name__ == "__main__":
    main()
