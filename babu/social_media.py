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

# ===========================================================================
# ARIA AUTOMATED CONTENT GENERATOR SYSTEM PROMPT OVERRIDE
# ===========================================================================
DAILY_POST_PROMPT = """Act as a 'Content Generator' for the business. Your primary role is to create engaging promotional posters and social media content to drive brand awareness and growth.

Purpose and Goals:
* Create high-quality, visually descriptive promotional posters tailored for social media audiences.
* Ensure all content aligns with the business identity and marketing objectives.
* Help maintain a consistent presence by planning content for daily Facebook posts at 9:00 AM.

Behaviors and Rules:
1) Brand Onboarding: Aligned with Anshu Computer & Tax Consultancy (PF, Tax, GST, and premium compliance specialization). 
2) Poster Creation: For every poster request, provide a suggested headline, a short caption, and a description of the recommended visual elements. Include a clear 'Call to Action' (CTA: Kaushal Market, Rath Road, Orai) to encourage user interaction. Use relevant hashtags and emojis.
3) Social Media Strategy: Frame all content specifically for the Facebook environment. Adhere to the schedule of preparing or drafting content for a daily 9:00 AM release.

Structure your output as a clean JSON object ONLY, with exactly five keys (no markdown code blocks like ```json):
{
  "category": "The service category of today's post: 'pf' (PF consultancy), 'itr' (Income Tax Return filing), 'gst' (GST registration & filing), or 'digital' (CSC and digital citizen services). Choose based on the topic.",
  "caption": "An engaging, professional, and high-converting marketing caption. Provide highly actionable advice on ITR filing, GST compliance, PF claim corrections. IMPORTANT: USE PLAIN TEXT ONLY. DO NOT use any markdown formatting, asterisks (*), underscores (_), or bolding.",
  "image_prompt": "A premium, minimalist 3D corporate style marketing campaign poster background representing financial growth, tax compliance, or expert PF services. Features sleek abstract 3D elements, vibrant corporate colors, clean typography space. IMPORTANT: The background must be PURELY visual. It must have NO text, letters, or gibberish.",
  "card_title": "A short, extremely punchy 2-4 word title for today's marketing card in all caps.",
  "card_tips": [
    "Advice 1 (max 6-8 words).",
    "Advice 2 (max 6-8 words).",
    "Advice 3 (max 6-8 words)."
  ]
}

Language: Hindi/English Mix (Hinglish)
Overall Tone: Professional, innovative, marketing-savvy, and schedule-oriented.

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
    
    res = None
    try:
        llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0.7)
        res = llm.invoke([SystemMessage(content=DAILY_POST_PROMPT), HumanMessage(content=user_prompt)])
    except Exception as groq_err:
        print(f"[SOCIAL LLM WARNING] Groq Llama-3.3 failed: {groq_err}. Falling back to gemini-2.5-flash...", flush=True)
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        if gemini_key:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.7, google_api_key=gemini_key)
            res = llm.invoke([SystemMessage(content=DAILY_POST_PROMPT), HumanMessage(content=user_prompt)])
        elif openrouter_key:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model="google/gemini-2.5-flash",
                temperature=0.7,
                api_key=openrouter_key,
                base_url="[https://openrouter.ai/api/v1](https://openrouter.ai/api/v1)",
                max_tokens=1500
            )
            res = llm.invoke([SystemMessage(content=DAILY_POST_PROMPT), HumanMessage(content=user_prompt)])
        else:
            raise groq_err
    
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


def ensure_poppins_fonts():
    """Ensure Poppins-Regular and Poppins-Bold are downloaded and available in babu/fonts."""
    import os
    import requests
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    font_dir = os.path.join(base_dir, "fonts")
    os.makedirs(font_dir, exist_ok=True)
    
    urls = {
        "Poppins-Regular.ttf": "[https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Regular.ttf](https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Regular.ttf)",
        "Poppins-Bold.ttf": "[https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Bold.ttf](https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Bold.ttf)"
    }
    
    for name, url in urls.items():
        dest = os.path.join(font_dir, name)
        if not os.path.exists(dest) or os.path.getsize(dest) < 10000:
            print(f"[FONTS] Downloading missing professional font {name} from Google Fonts...", flush=True)
            try:
                r = requests.get(url, timeout=30)
                if r.status_code == 200:
                    with open(dest, "wb") as f:
                        f.write(r.content)
                    print(f"[FONTS SUCCESS] Saved {name} to {dest}.", flush=True)
                else:
                    print(f"[FONTS WARNING] Failed to download {name}: Status {r.status_code}", flush=True)
            except Exception as e:
                print(f"[FONTS WARNING] Error downloading {name}: {e}", flush=True)


def get_font(font_name: str, size: int):
    """Retrieve TrueType font from bundled Poppins fonts, Windows system fonts, or fall back to default."""
    ensure_poppins_fonts()
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    font_dir = os.path.join(base_dir, "fonts")
    
    poppins_regular = os.path.join(font_dir, "Poppins-Regular.ttf")
    poppins_bold = os.path.join(font_dir, "Poppins-Bold.ttf")
    
    is_bold = "bd" in font_name.lower() or "bold" in font_name.lower() or "nirmalab" in font_name.lower() or "segoeuib" in font_name.lower()
    
    paths = []
    # 1. Prefer Poppins for high-fidelity bilingual display
    if is_bold and os.path.exists(poppins_bold):
        paths.append(poppins_bold)
    elif os.path.exists(poppins_regular):
        paths.append(poppins_regular)
        
    # 2. Local OS fallbacks
    paths.extend([
        f"C:\\Windows\\Fonts\\{font_name}.ttf",
        f"C:\\Windows\\Fonts\\{font_name.lower()}.ttf",
        f"C:\\Windows\\Fonts\\{font_name}bd.ttf",
        f"C:\\Windows\\Fonts\\{font_name.lower()}b.ttf",
        f"/usr/share/fonts/truetype/dejavu/{font_name}.ttf"
    ])
    
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
    from PIL import ImageDrop, ImageDraw
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
    """Generate a high-fidelity fintech dashboard graphic card using PIL."""
    from PIL import Image, ImageDraw, ImageFont
    import math
    
    category = str(category).lower().strip()
    if category not in ["pf", "itr", "gst", "digital"]:
        category = "itr"
        
    print(f"[PILLOW] Rendering floating graphic card for category '{category}' and title '{title}'...", flush=True)
    
    # Category Theme Definitions
    themes = {
        "pf": {
            "name": "PF",
            "theme_color": (19, 115, 51),       
            "accent_color": (0, 230, 118),      
            "tagline": "EPF Settlement & Compliance Resolution",
        },
        "itr": {
            "name": "ITR",
            "theme_color": (26, 115, 232),      
            "accent_color": (0, 242, 254),      
            "tagline": "Tax Planning & Accurate ITR Filing",
        },
        "gst": {
            "name": "GST",
            "theme_color": (232, 113, 10),      
            "accent_color": (255, 215, 0),      
            "tagline": "Seamless GST Registration & Compliance",
        },
        "digital": {
            "name": "DIGITAL",
            "theme_color": (104, 29, 168),     
            "accent_color": (179, 136, 255),    
            "tagline": "CSC E-Governance & Citizen Services",
        }
    }
    
    theme = themes[category]
    theme_color = theme["theme_color"]
    accent_color = theme["accent_color"]
    category_tagline = theme["tagline"]
    
    if background_path and os.path.exists(background_path):
        try:
            img = Image.open(background_path).convert("RGB").resize((1080, 1080), Image.Resampling.LANCZOS)
            print("[PILLOW] Loaded rich FLUX background graphic successfully, resized to 1080x1080.", flush=True)
        except Exception as e:
            print(f"[PILLOW WARNING] Failed to open background image: {e}. Falling back to gradient.", flush=True)
            background_path = None
            
    if not background_path:
        img = Image.new("RGB", (1080, 1080), (13, 17, 23))
        gradient_end = (int(theme_color[0]*0.3), int(theme_color[1]*0.3), int(theme_color[2]*0.3))
        draw_gradient_background(img, (10, 12, 18), gradient_end)
        img = draw_tech_grid(img, grid_size=60, color=(accent_color[0], accent_color[1], accent_color[2], 12))
        
    font_brand = get_font("Segoeuib", 38)            
    font_logo = get_font("Segoeuib", 44)             
    font_title = get_font("Segoeuib", 42)            
    font_tips = get_font("Segoeui", 26)              
    font_tips_bold = get_font("Segoeuib", 26)         
    font_footer_details = get_font("Segoeui", 20)     
    font_footer_stats = get_font("Segoeui", 18)       
    font_hindi_slogan = get_font("Nirmala", 20)       
    
    img = draw_glass_card(img, 40, 30, 1000, 160, bg_color=(10, 16, 28, 115), border_color=(accent_color[0], accent_color[1], accent_color[2], 130), border_width=2, radius=20)
    
    draw = ImageDraw.Draw(img)
    
    def draw_hexagon(d, cx, cy, r, f, o=None, w=1):
        pts = []
        for i in range(6):
            angle = math.radians(60 * i - 30)
            px = cx + r * math.cos(angle)
            py = cy + r * math.sin(angle)
            pts.append((px, py))
        d.polygon(pts, fill=f, outline=o, width=w)
        
    logo_cx, logo_cy = 100, 110
    draw_hexagon(draw, logo_cx, logo_cy, 36, (19, 115, 51), o=(255, 255, 255), w=2)
    draw.text((logo_cx, logo_cy - 2), "A", fill=(255, 255, 255), font=font_logo, anchor="mm")
    
    draw.text((160, 85), "ANSHU COMPUTER & TAX CONSULTANCY", fill=(255, 255, 255), font=font_brand, anchor="lm")
    
    slogan_text = "आस्था भरोसा, हमारी जिम्मेदारी  •  TAX • PF • GST • DIGITAL SOLUTIONS"
    draw.text((160, 138), slogan_text, fill=accent_color, font=font_hindi_slogan, anchor="lm")
    
    badge_w, badge_h = 200, 44
    badge_x, badge_y = 1010 - badge_w, 88
    badge_rgba = (theme_color[0], theme_color[1], theme_color[2], 160)
    draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + badge_h], radius=22, fill=badge_rgba, outline=accent_color, width=2)
    draw.text((badge_x + badge_w/2, badge_y + badge_h/2), f"{theme['name']} SERVICE", fill=(255, 255, 255), font=get_font("Segoeuib", 18), anchor="mm")
    
    img = draw_glass_card(img, 40, 560, 1000, 470, bg_color=(10, 16, 28, 140), border_color=(accent_color[0], accent_color[1], accent_color[2], 150), border_width=3, radius=24)
    
    draw = ImageDraw.Draw(img)
    
    draw.text((80, 600), category_tagline.upper(), fill=accent_color, font=get_font("Segoeuib", 18), anchor="lm")
    draw.text((80, 640), title, fill=(255, 255, 255), font=font_title, anchor="lm")
    
    start_y = 705
    spacing = 70
    for idx, tip in enumerate(tips[:3]):
        y_pos = start_y + idx * spacing
        cx, cy = 100, y_pos
        
        draw.ellipse([cx - 15, cy - 15, cx + 15, cy + 15], fill=theme_color, outline=accent_color, width=2)
        draw.line([(cx - 6, cy), (cx - 2, cy + 4)], fill=(255, 255, 255), width=2)
        draw.line([(cx - 2, cy + 4), (cx + 8, cy - 4)], fill=(255, 255, 255), width=2)
        
        draw.text((140, y_pos), f"0{idx+1}.", fill=accent_color, font=font_tips_bold, anchor="lm")
        draw.text((190, y_pos), tip, fill=(255, 255, 255), font=font_tips, anchor="lm")
        
    dial_cx, dial_cy = 840, 735
    dial_r = 75
    draw.arc([dial_cx - dial_r, dial_cy - dial_r, dial_cx + dial_r, dial_cy + dial_r], start=-225, end=45, fill=(255, 255, 255, 30), width=12)
    draw.arc([dial_cx - dial_r, dial_cy - dial_r, dial_cx + dial_r, dial_cy + dial_r], start=-225, end=35, fill=accent_color, width=12)
    
    draw.text((dial_cx, dial_cy - 10), "98%", fill=(255, 255, 255), font=get_font("Segoeuib", 32), anchor="mm")
    draw.text((dial_cx, dial_cy + 22), "Accuracy", fill=accent_color, font=get_font("Segoeui", 16), anchor="mm")
    draw.text((dial_cx, dial_cy + 95), "Compliance Score", fill=(170, 185, 200), font=get_font("Segoeuib", 16), anchor="mm")
    
    draw.line([(80, 915), (1000, 915)], fill=(255, 255, 255, 30), width=1)
    
    draw.text((540, 945), "Phone: +91 7217646673    |    Web: anshu-computer-and-tax-consultants.onrender.com", fill=(170, 185, 200), font=font_footer_details, anchor="mm")
    
    footer_stats_text = "Rated 5.0  •  23+ Verified Google Reviews  •  Serving Nationwide"
    draw.text((540, 980), footer_stats_text, fill=accent_color, font=font_footer_stats, anchor="mm")
    
    import uuid
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    unique_id = uuid.uuid4().hex[:8]
    image_path = os.path.join(temp_dir, f"daily_post_{unique_id}.jpg")
    img.save(image_path, "JPEG", quality=95)
    print(f"[PILLOW] High-fidelity centered floating card saved to: {image_path}", flush=True)
    return image_path


def generate_flux_graphic(prompt: str) -> str:
    """Generate or retrieve a high-quality campaign poster background."""
    import uuid
    import time
    import requests
    import urllib.parse
    import re
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    unique_id = uuid.uuid4().hex[:8]
    image_path = os.path.join(temp_dir, f"flux_ba
