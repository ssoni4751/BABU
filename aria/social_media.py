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

DAILY_POST_PROMPT = """You are ARIA's automated Social Media Manager. Your job is to write a highly engaging, professional business marketing campaign post for 'Anshu Computer & Tax Consultancy' (a premium Tax, Compliance, and E-Governance Consultancy run by Shubham Swarnkar, nickname Anshu, in Kaushal Market, Rath Road, Orai, Uttar Pradesh, India).

BRAND & BUSINESS PROFILE:
- Business Name: Anshu Computer & Tax Consultancy
- Type: Premium Tax, Compliance and E-Governance Consultancy
- Tagline: "PF, Tax & Compliance Solutions"
- Positioning: High-end consultancy specializing in PF (Provident Fund) claim settlement & compliance resolution, Income Tax Filing (ITR), GST filing & compliance, and specialized digital citizen registrations. Avoid positioning the brand as just a local computer shop or basic CSC centre.
- Location: Kaushal Market, Rath Road, Orai, Uttar Pradesh. Service area: India (both local and nationwide digital consultancy).
- Core target customers: Salaried employees (for ITR/PF), Shopkeepers & Small Businesses (for GST/Tax), MSMEs, PF Claimants, and Pensioners.
- Brand principles: Accuracy First, Compliance Focused, Trust Based Service, Problem Solving.

Structure your output as a clean JSON object ONLY, with exactly five keys (no markdown code blocks like ```json):
{
  "category": "The service category of today's post: 'pf' (PF consultancy), 'itr' (Income Tax Return filing), 'gst' (GST registration & filing), or 'digital' (CSC and digital citizen services). Choose based on the topic.",
  "caption": "An engaging, professional, and high-converting marketing caption. Provide highly actionable advice on ITR filing, GST compliance, PF claim corrections (such as KYC, UAN consolidation, name/DOB corrections, joint declarations, or ex-employer cases), or digital e-governance citizen services. Highlight accuracy, peace of mind, and expert problem-solving. Use emojis and professional hashtags (e.g. #AnshuComputerAndTax #PFConsultancy #TaxConsultant #GSTCompliance #Orai #ITRFiling). Reference 'Anshu Computer & Tax Consultancy' naturally as the premier expert ready to resolve complex compliance issues nationwide. IMPORTANT: USE PLAIN TEXT ONLY. DO NOT use any markdown formatting, asterisks (*), underscores (_), or bolding.",
  "image_prompt": "A premium, minimalist 3D corporate style marketing campaign poster background representing financial growth, tax compliance, or expert PF services. Features sleek abstract 3D elements, vibrant corporate colors, clean typography space, professional lighting, soft studio shadows, depth of field, 8k resolution. The top half must have a bright, eye-catching visual metaphor relevant to the topic, while the bottom half fades into a dark, clean gradient. IMPORTANT: The background must be PURELY visual. It must have NO text, NO letters, NO words, NO spelling, NO typos, NO unreadable gibberish, NO paragraphs, NO characters. Pure illustration background only.",
  "card_title": "A short, extremely punchy 2-4 word title for today's marketing card in all caps (e.g. 'CLAIM YOUR PF', 'FILE GST RIGHT', 'FILE INCOME TAX', 'SECURE YOUR TAXES', 'PF KYC CORRECTION').",
  "card_tips": [
    "A short, highly actionable marketing point (max 6-8 words) explaining Step 1/Advice 1.",
    "A short, highly actionable marketing point (max 6-8 words) explaining Step 2/Advice 2.",
    "A short, highly actionable marketing point (max 6-8 words) explaining Step 3/Advice 3."
  ]
}

VISUAL PRESETS FOR 'image_prompt' BY CATEGORY:
- For 'pf': generate an image prompt featuring a secure 3D golden key opening a digital vault of savings, emerald green glowing upward trend lines, or a protective shield over a pension document. Deep navy, emerald green, and gold colors.
- For 'itr': generate an image prompt featuring a sleek 3D financial calculator with a glowing screen, pristine folders of tax documents organized beautifully, or a gold balanced scale of financial planning. Midnight blue, royal blue, and gold colors.
- For 'gst': generate an image prompt featuring 3D interlocking puzzle pieces representing business integration, a futuristic ledger screen with success checkmarks, or orange-glowing corporate trading grids. Dark navy, vibrant orange, and gold colors.
- For 'digital': generate an image prompt featuring a futuristic 3D citizen identity card with secure seal, high-tech network nodes representing connectivity, or a glowing certificate ribbon. Midnight blue, royal purple, and silver colors.

Reply with raw JSON ONLY."""

def fetch_india_tech_trends() -> str:
    """Fetch current tax, GST, and EPF compliance trends in India using DuckDuckGo."""
    print("[SOCIAL] Fetching real-time India compliance and tax trends from DuckDuckGo...", flush=True)
    query = "latest Income Tax GST EPFO compliance news India 2026"
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
        if not results:
            return "No real-time tax trends found."
        lines = []
        for r in results:
            lines.append(f"• {r['title']}\n  {r['body']}\n  Source: {r['href']}")
        raw_text = "\n\n".join(lines)
        from memory import compress_context_payload
        compressed = compress_context_payload(raw_text, "India tax and compliance trends")
        return compressed
    except Exception as e:
        print(f"[SOCIAL TRENDS ERROR] Failed to fetch live trends: {e}", flush=True)
        return "Could not fetch real-time trends due to network error."


def generate_daily_post(custom_topic: str = None) -> tuple[str, str, str, list, str]:
    """Use Groq LLM to generate a caption and matching graphic prompt incorporating real-time India tax and compliance trends."""
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        raise ValueError("GROQ_API_KEY is not configured in the environment.")
    
    trends_context = fetch_india_tech_trends()
    print(f"[SOCIAL] Integrated Compliance Trends Context:\n{trends_context}", flush=True)
    
    print("[SOCIAL] Generating daily post caption and prompt via Groq...", flush=True)
    
    user_prompt = "Generate today's scheduled post."
    if custom_topic:
        user_prompt += f"\n\nIMPORTANT: Please generate today's post focusing EXACTLY on the user's requested custom topic: '{custom_topic}'. Adjust all copy, card title, and tips to focus on this topic, while keeping it relevant to 'Anshu Computer & Tax Consultancy'!"
    elif trends_context and "Could not fetch" not in trends_context and "No real-time trends" not in trends_context:
        user_prompt += (
            f"\n\nHere is some real-time financial, tax, or EPF compliance news context in India:\n"
            f"-----------\n{trends_context}\n-----------\n\n"
            f"IMPORTANT: Please draft a daily tax or compliance advice/marketing post that naturally addresses or draws inspiration from the real-time Indian tax/compliance news above. Ensure it connects seamlessly to the professional tax, compliance, and e-governance services offered by 'Anshu Computer & Tax Consultancy'!"
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
    category = data.get("category", "itr").lower().strip()
    if category not in ["pf", "itr", "gst", "digital"]:
        category = "itr"
    
    # Clean markdown formatting characters that crash Telegram V1 Markdown parser
    caption = caption.replace("*", "").replace("_", "").replace("`", "")
    
    return caption, image_prompt, card_title, card_tips, category


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


def generate_pillow_graphic(title: str, tips: list, background_path: str = None, category: str = "itr") -> str:
    """Generate a clean, professional social media graphic card using PIL.
    
    If background_path is provided (from FLUX model), opens it and overlays the floating glass card.
    Otherwise, draws a premium dark gradient tech-grid background locally.
    """
    from PIL import Image, ImageDraw, ImageFont
    import math
    
    category = str(category).lower().strip()
    if category not in ["pf", "itr", "gst", "digital"]:
        category = "itr"
        
    print(f"[PILLOW] Rendering floating graphic card for category '{category}' and title '{title}'...", flush=True)
    
    # 1. Category Theme Definitions
    themes = {
        "pf": {
            "name": "PF",
            "theme_color": (19, 115, 51),       # Corporate Green
            "accent_color": (0, 230, 118),      # Emerald Green
            "tagline": "EPF Settlement & Compliance Resolution",
        },
        "itr": {
            "name": "ITR",
            "theme_color": (26, 115, 232),      # Corporate Blue
            "accent_color": (0, 242, 254),      # Cyan Tech Accent
            "tagline": "Tax Planning & Accurate ITR Filing",
        },
        "gst": {
            "name": "GST",
            "theme_color": (232, 113, 10),      # Corporate Orange
            "accent_color": (255, 215, 0),      # Glowing Gold
            "tagline": "Seamless GST Registration & Compliance",
        },
        "digital": {
            "name": "DIGITAL",
            "theme_color": (104, 29, 168),     # Royal Purple
            "accent_color": (179, 136, 255),    # Electric Violet
            "tagline": "CSC E-Governance & Citizen Services",
        }
    }
    
    theme = themes[category]
    theme_color = theme["theme_color"]
    accent_color = theme["accent_color"]
    category_tagline = theme["tagline"]
    
    # 2. Load background or create fallback
    if background_path and os.path.exists(background_path):
        try:
            img = Image.open(background_path).convert("RGB").resize((1080, 1080), Image.Resampling.LANCZOS)
            print("[PILLOW] Loaded rich FLUX background graphic successfully, resized to 1080x1080.", flush=True)
        except Exception as e:
            print(f"[PILLOW WARNING] Failed to open background image: {e}. Falling back to gradient.", flush=True)
            background_path = None
            
    if not background_path:
        img = Image.new("RGB", (1080, 1080), (13, 17, 23))
        # Draw gradient background matching the category theme
        gradient_end = (int(theme_color[0]*0.3), int(theme_color[1]*0.3), int(theme_color[2]*0.3))
        draw_gradient_background(img, (10, 12, 18), gradient_end)
        # Draw tech grid
        img = draw_tech_grid(img, grid_size=60, color=(accent_color[0], accent_color[1], accent_color[2], 12))
        
    # 3. Centered Floating Glassmorphic Card (x = 60, y = 100, width = 960, height = 880)
    card_x, card_y = 60, 100
    card_w, card_h = 960, 880
    
    # Semi-translucent glassy navy backdrop
    bg_rgba = (10, 16, 28, 215)
    border_rgba = (accent_color[0], accent_color[1], accent_color[2], 180)
    img = draw_glass_card(img, card_x, card_y, card_w, card_h, bg_color=bg_rgba, border_color=border_rgba, border_width=3, radius=24)
    
    draw = ImageDraw.Draw(img)
    
    # Helper to draw a regular hexagon
    def draw_hexagon(d, cx, cy, r, f, o=None, w=1):
        pts = []
        for i in range(6):
            angle = math.radians(60 * i - 30)
            px = cx + r * math.cos(angle)
            py = cy + r * math.sin(angle)
            pts.append((px, py))
        d.polygon(pts, fill=f, outline=o, width=w)
        
    # Hexagon Logo (Matching official flyer style)
    logo_cx, logo_cy = 120, 180
    draw_hexagon(draw, logo_cx, logo_cy, 35, f=(19, 115, 51), o=(255, 255, 255), w=2)
    
    # Fonts
    font_brand = get_font("Segoeuib", 30)
    if font_brand == ImageFont.load_default(): font_brand = get_font("Arialbd", 30)
    
    font_logo = get_font("Segoeuib", 38)
    if font_logo == ImageFont.load_default(): font_logo = get_font("Arialbd", 38)
    
    font_title = get_font("Segoeuib", 50)
    if font_title == ImageFont.load_default(): font_title = get_font("Arialbd", 50)
    
    font_tips = get_font("Segoeui", 32)
    if font_tips == ImageFont.load_default(): font_tips = get_font("Arial", 32)
    
    font_tips_bold = get_font("Segoeuib", 36)
    if font_tips_bold == ImageFont.load_default(): font_tips_bold = get_font("Arialbd", 36)
    
    font_footer_details = get_font("Segoeui", 20)
    font_footer_stats = get_font("Segoeui", 18)
    
    # Try loading Nirmala UI (Windows standard high-quality Hindi/Devanagari font)
    font_hindi_slogan = get_font("Nirmala", 20)
    font_hindi_slogan_b = get_font("Nirmalab", 20)
    font_footer_hindi = get_font("Nirmalab", 24)
    
    # Logo text "A"
    draw.text((logo_cx, logo_cy - 2), "A", fill=(255, 255, 255), font=font_logo, anchor="mm")
    
    # Brand title
    draw.text((170, 160), "ANSHU COMPUTER & TAX CONSULTANCY", fill=(255, 255, 255), font=font_brand, anchor="lm")
    
    # Header slogan / subtitle
    if font_hindi_slogan != ImageFont.load_default():
        slogan_text = "आस्था भरोसा, हमारी जिम्मेदारी  •  TAX • PF • GST • DIGITAL SOLUTIONS"
        draw.text((170, 202), slogan_text, fill=accent_color, font=font_hindi_slogan, anchor="lm")
    else:
        slogan_text = "TRUST & RESPONSIBILITY  •  TAX • PF • GST • DIGITAL SOLUTIONS"
        draw.text((170, 202), slogan_text, fill=accent_color, font=get_font("Segoeui", 18), anchor="lm")
        
    # Thin divider line below header
    draw.line([(100, 245), (980, 245)], fill=(255, 255, 255, 40), width=1)
    
    # Category capsule badge in top-right
    badge_w, badge_h = 180, 42
    badge_x, badge_y = 940 - badge_w, 158
    badge_rgba = (theme_color[0], theme_color[1], theme_color[2], 120)
    draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + badge_h], radius=21, fill=badge_rgba, outline=accent_color, width=2)
    draw.text((badge_x + badge_w/2, badge_y + badge_h/2), f"{theme['name']} SERVICE", fill=(255, 255, 255), font=get_font("Segoeuib", 18), anchor="mm")
    
    # Category specific tagline
    draw.text((540, 290), category_tagline, fill=accent_color, font=get_font("Segoeuib", 24), anchor="mm")
    
    # Large Card Title
    draw.text((540, 350), title, fill=(255, 255, 255), font=font_title, anchor="mm")
    # Neon highlight line under title
    draw.line([(540 - 220, 390), (540 + 220, 390)], fill=accent_color, width=3)
    
    # Draw checkmark bullet points
    start_y = 460
    spacing = 110
    for idx, tip in enumerate(tips[:3]):
        y_pos = start_y + idx * spacing
        cx, cy = 140, y_pos
        
        # Draw checkmark circle in theme color
        draw.ellipse([cx - 20, cy - 20, cx + 20, cy + 20], fill=theme_color, outline=accent_color, width=2)
        # Draw custom tick symbol programmatically
        draw.line([(cx - 8, cy), (cx - 2, cy + 6)], fill=(255, 255, 255), width=3)
        draw.line([(cx - 2, cy + 6), (cx + 10, cy - 6)], fill=(255, 255, 255), width=3)
        
        # Step number in accent color
        draw.text((190, y_pos), f"0{idx+1}.", fill=accent_color, font=font_tips_bold, anchor="lm")
        # Tip body text in white
        draw.text((260, y_pos), tip, fill=(255, 255, 255), font=font_tips, anchor="lm")
        
    # Card bottom footer divider
    draw.line([(100, 810), (980, 810)], fill=(255, 255, 255, 30), width=1)
    
    # Footer slogan (Hindi Devanagari with English fallback)
    if font_footer_hindi != ImageFont.load_default():
        footer_slogan = "कंप्लायंस सही, भविष्य सुरक्षित।"
        draw.text((540, 845), footer_slogan, fill=(255, 215, 0), font=font_footer_hindi, anchor="mm")
    else:
        footer_slogan = "ACCURATE COMPLIANCE, SECURE FUTURE"
        draw.text((540, 845), footer_slogan, fill=(255, 215, 0), font=get_font("Segoeuib", 22), anchor="mm")
        
    # Contact Details Line
    draw.text((540, 890), "📞 +91 7217646673   |   🌐 https://anshu-computer-and-tax-consultants.onrender.com", fill=(170, 185, 200), font=font_footer_details, anchor="mm")
    # Trust statistics Line
    draw.text((540, 925), "⭐ 5.0 Rated  •  23+ Verified Google Reviews  •  Serving Nationwide", fill=accent_color, font=font_footer_stats, anchor="mm")
    
    # Save output
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    image_path = os.path.join(temp_dir, "daily_post.jpg")
    img.save(image_path, "JPEG", quality=95)
    print(f"[PILLOW] High-fidelity centered floating card saved to: {image_path}", flush=True)
    return image_path
    
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
    caption, img_prompt, card_title, card_tips, category = generate_daily_post(custom_topic)
    
    bg_path = None
    try:
        print(f"[SOCIAL] Attempting to generate rich FLUX background image for topic '{custom_topic}'...", flush=True)
        bg_path = generate_flux_graphic(img_prompt)
    except Exception as e:
        print(f"[SOCIAL WARNING] Rich background generation failed: {e}. Falling back to default layout.", flush=True)
        
    img_path = generate_pillow_graphic(card_title, card_tips, background_path=bg_path, category=category)
    
    return {
        "caption": caption,
        "image_prompt": img_prompt,
        "card_title": card_title,
        "card_tips": card_tips,
        "image_path": img_path,
        "category": category
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
