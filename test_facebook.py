import os
import sys

# Force UTF-8 encoding for Windows standard streams to prevent emoji/unicode logging crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Configure API Key for local test
if not os.environ.get("GEMINI_API_KEY"):
    from dotenv import load_dotenv
    load_dotenv()
    if os.environ.get("GROQ_API_KEY"):
        os.environ["GEMINI_API_KEY"] = os.environ["GROQ_API_KEY"]
    else:
        raise RuntimeError("GEMINI_API_KEY or GROQ_API_KEY is required in environment for this test.")

# Add babu to python path so we can import from it
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "babu"))

from babu.social_media import generate_daily_post, generate_flux_graphic, publish_to_facebook_page

def main():
    print("="*60)
    print("RUNNING BABU SOCIAL MEDIA INTEGRATION TEST")
    print("="*60)
    
    try:
        # 1. Generate Caption and Image Prompt
        print("\n[STEP 1] Generating caption and FLUX prompt via Groq...")
        caption, img_prompt, card_title, card_tips, category = generate_daily_post()
        print(f"\n✨ Generated Caption:\n{caption}")
        print(f"\n🎨 Generated FLUX Prompt:\n{img_prompt}")
        
        # 2. Download Image via FLUX & render Pillow glass card
        print("\n[STEP 2] Downloading custom graphic from Pollinations.ai FLUX...")
        bg_path = None
        try:
            bg_path = generate_flux_graphic(img_prompt)
            print(f"✅ Backdrop saved at: {bg_path}")
        except Exception as e:
            print(f"⚠️ Backdrop download failed: {e}. Falling back to default layout.")
        
        print("\n[STEP 2.5] Rendering premium Pillow dashboard graphic card...")
        from babu.social_media import generate_pillow_graphic
        img_path = generate_pillow_graphic(card_title, card_tips, background_path=bg_path, category=category)
        print(f"✅ Finished Graphic saved at: {img_path}")
        print(f"Size of graphic: {os.path.getsize(img_path)} bytes")
        
        # 3. Simulate or Publish to Facebook
        print("\n[STEP 3] Testing Facebook Page Publishing...")
        success, message = publish_to_facebook_page(img_path, caption)
        if success:
            print(f"🚀 SUCCESS: {message}")
        else:
            print(f"⚠️ INFO: {message}")
            print("Note: Facebook posting will complete on Render once FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN are supplied.")
            
        print("\n" + "="*60)
        print("TEST COMPLETED SUCCESSFULLY!")
        print("="*60)
        
    except Exception as e:
        print(f"\n❌ TEST FAILED with error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
