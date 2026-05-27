import os
import sys
import json
import re
import urllib.request
import urllib.parse
import tempfile
import requests
import time
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage

DAILY_POST_PROMPT = """You are ARIA's automated Social Media Manager. Your job is to write a highly engaging daily social media post for 'Anshu Computers Orai' (a computer services and digital assistance shop run by Shubham Swarnkar, nickname Anshu, in Kaushal Market, Orai, Jalaun, Uttar Pradesh).

Structure your output as a clean JSON object ONLY, with exactly four keys (no markdown code blocks like ```json):
{
  "caption": "An engaging, warm, yet professional post caption. Provide a practical daily tech tip, hardware care advice, internet security tip, or productivity advice. Use emojis and professional hashtags (e.g. #AnshuComputersOrai #OraiTech #DailyTechTip). Reference 'Anshu Computers Orai' naturally as the shop ready to help clients with these issues. IMPORTANT: USE PLAIN TEXT ONLY. DO NOT use any markdown formatting, asterisks (*), underscores (_), or bolding.",
  "image_prompt": "A highly detailed, modern, and visually stunning square graphic prompt for a text-to-image generator (FLUX model).",
  "card_title": "A short, extremely punchy 2-4 word title for today's tech card in all caps (e.g. 'SECURE YOUR UPI', 'BOOST PC SPEED', 'STOP AI SCAMS').",
  "card_tips": [
    "A short, highly actionable bullet point (max 6-8 words) explaining Step 1/Advice 1.",
    "A short, highly actionable bullet point (max 6-8 words) explaining Step 2/Advice 2.",
    "A short, highly actionable bullet point (max 6-8 words) explaining Step 3/Advice 3."
  ]
}

Reply with raw JSON ONLY."""

def fetch_india_tech_trends() -> str:
    """Fetch current technology trends and digital news/scams in India using DuckDuckGo."""
    print("[SOCIAL] Fetching real-time India tech trends from DuckDuckGo...", flush=True)
    query = "latest technology news India cybersecurity digital scams 2026"
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
        if not results:
            return "No real-time trends found."
        lines = []
        for r in results:
            lines.append(f"• {r['title']}\n  {r['body']}\n  Source: {r['href']}")
        raw_text = "\n\n".join(lines)
        from memory import compress_context_payload
        compressed = compress_context_payload(raw_text, "India tech trends news")
        return compressed
    except Exception as e:
        print(f"[SOCIAL TRENDS ERROR] Failed to fetch live trends: {e}", flush=True)
        return "Could not fetch real-time trends due to network error."


def generate_daily_post(custom_topic: str = None) -> tuple[str, str, str, list]:
    """Use Groq LLM to generate a caption and matching graphic prompt incorporating real-time India tech trends."""
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        raise ValueError("GROQ_API_KEY is not configured in the environment.")
    
    trends_context = fetch_india_tech_trends()
    print(f"[SOCIAL] Integrated Tech Trends Context:\n{trends_context}", flush=True)
    
    print("[SOCIAL] Generating daily post caption and prompt via Groq...", flush=True)
    
    user_prompt = "Generate today's scheduled post."
    if custom_topic:
        user_prompt += f"\n\nIMPORTANT: Please generate today's post focusing EXACTLY on the user's requested custom topic: '{custom_topic}'. Adjust all copy, card title, and tips to focus on this topic, while keeping it relevant to 'Anshu Computers Orai'!"
    elif trends_context and "Could not fetch" not in trends_context and "No real-time trends" not in trends_context:
        user_prompt += (
            f"\n\nHere is some real-time digital news/cybersecurity trend context in India:\n"
            f"-----------\n{trends_context}\n-----------\n\n"
            f"IMPORTANT: Please draft a daily tech tip or advice post that naturally addresses or draws inspiration from the real-time Indian tech/security trends above. Ensure it connects seamlessly to the tech services offered by 'Anshu Computers Orai'!"
        )
    
    llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0.7)
    res = llm.invoke([SystemMessage(content=DAILY_POST_PROMPT), HumanMessage(content=user_prompt)])
    
    text = res.content.strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        data = json.loads(match.group())
    else:
        data = json.loads(text)
    
    caption = data.get("caption", "Boost your digital productivity today! Visit Anshu Computers Orai for all tech assistance.")
    image_prompt = data.get("image_prompt", "Sleek modern office desk with high-tech computer monitor showing text 'TECH TIPS' in clean typography, professional lighting, 4k resolution")
    card_title = data.get("card_title", "BOOST PC PRODUCTIVITY")
    card_tips = data.get("card_tips", [
        "Clean temporary cache files weekly.",
        "Disable heavy startup applications.",
        "Keep your Windows OS updated."
    ])
    
    # Clean markdown formatting characters that crash Telegram V1 Markdown parser
    caption = caption.replace("*", "").replace("_", "").replace("`", "")
    
    return caption, image_prompt, card_title, card_tips


def get_font(font_name: str, size: int):
    """Retrieve TrueType font from Windows system fonts folder or fall back to default."""
    paths = [
        f"C:\\Windows\\Fonts\\{font_name}.ttf",
        f"C:\\Windows\\Fonts\\{font_name.lower()}.ttf",
        f"C:\\Windows\\Fonts\\{font_name}bd.ttf",
        f"C:\\Windows\\Fonts\\{font_name.lower()}b.ttf",
        f"/usr/share/fonts/truetype/dejavu/{font_name}.ttf"
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                from PIL import ImageFont
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    from PIL import ImageFont
    return ImageFont.load_default()


def draw_gradient_background(image, color1, color2):
    """Draw a vertical linear gradient on an image."""
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    width, height = image.size
    for y in range(height):
        r = int(color1[0] + (color2[0] - color1[0]) * y / height)
        g = int(color1[1] + (color2[1] - color1[1]) * y / height)
        b = int(color1[2] + (color2[2] - color1[2]) * y / height)
        draw.line([(0, y), (width, y)], fill=(r, g, b))


def draw_tech_grid(image, grid_size=60, color=(0, 242, 254, 15)):
    """Overlay a translucent high-tech grid layer on the canvas."""
    from PIL import Image, ImageDraw
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = image.size
    for x in range(0, width, grid_size):
        draw.line([(x, 0), (x, height)], fill=color, width=1)
    for y in range(0, height, grid_size):
        draw.line([(0, y), (width, y)], fill=color, width=1)
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def draw_glass_card(image, x, y, w, h, bg_color=(20, 24, 30, 200), border_color=(0, 242, 254, 100), border_width=2, radius=24):
    """Draw a semi-translucent rounded card representing glassmorphism."""
    from PIL import Image, ImageDraw
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=bg_color, outline=border_color, width=border_width)
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def generate_pillow_graphic(title: str, tips: list, background_path: str = None) -> str:
    """Generate a clean, professional social media graphic card using PIL.
    
    If background_path is provided (from FLUX model), opens it and overlays the glass card.
    Otherwise, draws a premium dark gradient tech-grid background locally.
    """
    from PIL import Image, ImageDraw, ImageFont
    
    print(f"[PILLOW] Rendering high-fidelity custom graphic card for '{title}'...", flush=True)
    
    # 1. Load background or create fallback
    if background_path and os.path.exists(background_path):
        try:
            img = Image.open(background_path).convert("RGB")
            print("[PILLOW] Loaded rich FLUX background graphic successfully.", flush=True)
        except Exception as e:
            print(f"[PILLOW WARNING] Failed to open background image: {e}. Falling back to gradient.", flush=True)
            background_path = None
            
    if not background_path:
        img = Image.new("RGB", (1080, 1080), (13, 17, 23))
        # Draw gradient background
        draw_gradient_background(img, (10, 15, 30), (20, 24, 40))
        # Draw tech grid
        img = draw_tech_grid(img, grid_size=60, color=(0, 242, 254, 12))
        
    # 2. Draw lower glassmorphic card (bottom 43% of the graphic)
    # This allows the stunning AI-generated artwork on the top half to be fully visible!
    card_x = 60
    card_y = 540
    card_w = 960
    card_h = 460
    
    # Draw a gorgeous dark glass card with a glowing cyan border
    img = draw_glass_card(img, card_x, card_y, card_w, card_h, bg_color=(10, 15, 25, 210), border_color=(0, 242, 254, 200), border_width=3, radius=24)
    
    draw = ImageDraw.Draw(img)
    
    # Load fonts
    font_brand = get_font("Segouib", 28)
    if font_brand == ImageFont.load_default(): font_brand = get_font("Arialbd", 28)
    font_title = get_font("Segoeuib", 46)
    if font_title == ImageFont.load_default(): font_title = get_font("Arialbd", 46)
    font_tips = get_font("Segoeui", 32)
    if font_tips == ImageFont.load_default(): font_tips = get_font("Arial", 32)
    font_tips_bold = get_font("Segoeuib", 34)
    if font_tips_bold == ImageFont.load_default(): font_tips_bold = get_font("Arialbd", 34)
    font_footer = get_font("Segoeui", 22)
    
    # 3. Draw elements on the glass card
    # Card branding
    draw.text((540, card_y + 40), "ANSHU COMPUTERS ORAI", fill=(255, 255, 255), font=font_brand, anchor="mm")
    draw.line([(450, card_y + 60), (630, card_y + 60)], fill=(0, 242, 254), width=2)
    
    # Card title
    draw.text((540, card_y + 100), title, fill=(0, 242, 254), font=font_title, anchor="mm")
    
    # Card tips
    start_y = card_y + 170
    spacing = 75
    for idx, tip in enumerate(tips[:3]):
        y_pos = start_y + idx * spacing
        # Draw custom bullet
        draw.ellipse([120, y_pos - 6, 136, y_pos + 10], fill=(0, 242, 254))
        # Draw tip text
        draw.text((160, y_pos), f"0{idx+1}.", fill=(0, 242, 254), font=font_tips_bold, anchor="lm")
        draw.text((220, y_pos), tip, fill=(255, 255, 255), font=font_tips, anchor="lm")
        
    # 4. Bottom footer line inside the card
    draw.line([(100, card_y + 395), (980, card_y + 395)], fill=(255, 255, 255, 30), width=1)
    draw.text((540, card_y + 420), "📍 Shop No. 3, Kaushal Market, Orai | 📞 +91 7217646673", fill=(170, 185, 200), font=font_footer, anchor="mm")
    
    # Save output
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    image_path = os.path.join(temp_dir, "daily_post.jpg")
    img.save(image_path, "JPEG", quality=95)
    print(f"[PILLOW] High-fidelity hybrid graphic saved successfully to: {image_path}", flush=True)
    return image_path


def generate_flux_graphic(prompt: str) -> str:
    """Generate a FLUX image using Pollinations.ai and save it locally."""
    # Encode the prompt for URL usage
    encoded_prompt = urllib.parse.quote_plus(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"
    # Retry logic (max 3 attempts)
    for attempt in range(1, 4):
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            break
        except Exception as e:
            if attempt == 3:
                raise RuntimeError(f"Failed to fetch FLUX image from Pollinations after {attempt} attempts: {e}")
            time.sleep(attempt * 2)
            
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    image_path = os.path.join(temp_dir, "daily_post.jpg")
    with open(image_path, "wb") as f:
        f.write(resp.content)
    return image_path


def publish_to_facebook_page(image_path: str, caption: str) -> tuple[bool, str]:
    """Publish the photo and caption to Facebook Page via Graph API."""
    page_id = os.environ.get("FACEBOOK_PAGE_ID")
    page_token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN")
    
    if not page_id or not page_token:
        return False, "Missing FACEBOOK_PAGE_ID or FACEBOOK_PAGE_ACCESS_TOKEN in environment variables."
        
    url = f"https://graph.facebook.com/v19.0/{page_id}/photos"
    
    try:
        with open(image_path, "rb") as img_file:
            files = {
                "source": img_file
            }
            data = {
                "message": caption,
                "access_token": page_token
            }
            print(f"[FACEBOOK] Publishing photo to page {page_id}...", flush=True)
            response = requests.post(url, files=files, data=data, timeout=30)
            
        res_json = response.json()
        if response.status_code == 200 and "id" in res_json:
            post_id = res_json["id"]
            return True, f"Successfully published to Facebook Page! Post ID: {post_id}"
        else:
            error_msg = res_json.get("error", {}).get("message", "Unknown Graph API error")
            return False, f"Facebook API Error: {error_msg}"
    except Exception as e:
        return False, f"Failed to publish to Facebook: {e}"


def generate_social_post_draft(custom_topic: str = None) -> dict:
    """Scrape trends (or use custom topic), generate caption, image prompt, download FLUX backdrop, and render Pillow glass card."""
    caption, img_prompt, card_title, card_tips = generate_daily_post(custom_topic)
    
    bg_path = None
    try:
        print(f"[SOCIAL] Attempting to generate rich FLUX background image for topic '{custom_topic}'...", flush=True)
        bg_path = generate_flux_graphic(img_prompt)
    except Exception as e:
        print(f"[SOCIAL WARNING] Rich background generation failed: {e}. Falling back to default layout.", flush=True)
        
    img_path = generate_pillow_graphic(card_title, card_tips, background_path=bg_path)
    
    return {
        "caption": caption,
        "image_prompt": img_prompt,
        "card_title": card_title,
        "card_tips": card_tips,
        "image_path": img_path
    }


def run_autonomous_social_post() -> tuple[bool, str, str, str]:
    """Unified wrapper that runs the entire generation and publishing flow, auditing metrics."""
    from memory import append_to_profile_ledger, log_execution_failure
    
    caption, img_prompt = "", ""
    img_path = ""
    try:
        draft = generate_social_post_draft()
        caption = draft["caption"]
        img_prompt = draft["image_prompt"]
        img_path = draft["image_path"]
        
        ok, msg = publish_to_facebook_page(img_path, caption)
        
        # Log work progress atomically to profile
        append_to_profile_ledger("work_summaries", {
            "task_name": "Daily FB Marketing Post",
            "status": "SUCCESS" if ok else "FAILED",
            "details": f"Message: {msg} | Graphic Prompt: {img_prompt[:80]}..."
        })
        
        if not ok:
            log_execution_failure(
                domain="social_media.facebook_publisher",
                method=f"publish_to_facebook_page(img_path, caption) with Page ID {os.environ.get('FACEBOOK_PAGE_ID')}",
                exception_msg=msg
            )
            
        import gc
        gc.collect()
        return ok, msg, caption, img_path
    except Exception as e:
        error_msg = str(e)
        print(f"[SOCIAL CRITICAL ERROR] {error_msg}", flush=True)
        
        append_to_profile_ledger("work_summaries", {
            "task_name": "Daily FB Marketing Post",
            "status": "CRITICAL_ERROR",
            "details": error_msg
        })
        
        log_execution_failure(
            domain="social_media.autonomous_social_post",
            method="run_autonomous_social_post() full pipeline execution",
            exception_msg=error_msg
        )
        import gc
        gc.collect()
        return False, f"Autonomous workflow failed: {error_msg}", caption, img_path
