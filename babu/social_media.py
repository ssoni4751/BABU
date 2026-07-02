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

DAILY_POST_PROMPT = """You are BABU's automated Social Media Manager. Your job is to write a highly engaging, professional business marketing campaign post for 'Anshu Computer & Tax Consultancy' (a premium Tax, Compliance, and E-Governance Consultancy run by Shubham Swarnkar, nickname Anshu, in Kaushal Market, Rath Road, Orai, Uttar Pradesh, India).

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
                base_url="https://openrouter.ai/api/v1",
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
        "Poppins-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Regular.ttf",
        "Poppins-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Bold.ttf"
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
    """Generate a high-fidelity fintech dashboard graphic card using PIL.
    
    Loads the creative FLUX backdrop and overlays a floating, highly translucent
    bilingual header bar at the top, and a sleek, compact horizontal glass panel 
    at the bottom. This bottom panel includes bullet points and a custom-drawn
    compliance dial/ring gauge, leaving the center of the image completely open
    to display the gorgeous FLUX AI illustration. All text sizes are maximized
    to provide a bold, premium branding presence visible in plain sight.
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
        
    # 3. Fonts Loading (Enlarged for supreme visibility and readability)
    font_brand = get_font("Segoeuib", 38)            # Bold, prominent brand title
    font_logo = get_font("Segoeuib", 44)             # Authoritative emblem text
    font_title = get_font("Segoeuib", 42)            # Clear card header
    font_tips = get_font("Segoeui", 26)              # Highly visible bullet body
    font_tips_bold = get_font("Segoeuib", 26)         # Step digits
    font_footer_details = get_font("Segoeui", 20)     # Clean contact info
    font_footer_stats = get_font("Segoeui", 18)       # Crisp trust stats
    font_hindi_slogan = get_font("Nirmala", 20)       # Clear, visible Devanagari slogan
    
    # 4. Draw Floating Header Bar (y = 30 to 190, expanded height = 160)
    # Background glass panel for header (45% opacity for solid contrast against busy visuals)
    img = draw_glass_card(img, 40, 30, 1000, 160, bg_color=(10, 16, 28, 115), border_color=(accent_color[0], accent_color[1], accent_color[2], 130), border_width=2, radius=20)
    
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
        
    # Hexagon Logo emblem
    logo_cx, logo_cy = 100, 110
    draw_hexagon(draw, logo_cx, logo_cy, 36, (19, 115, 51), o=(255, 255, 255), w=2)
    draw.text((logo_cx, logo_cy - 2), "A", fill=(255, 255, 255), font=font_logo, anchor="mm")
    
    # Brand title
    draw.text((160, 85), "ANSHU COMPUTER & TAX CONSULTANCY", fill=(255, 255, 255), font=font_brand, anchor="lm")
    
    # Header slogan / subtitle (Devanagari Poppins)
    slogan_text = "आस्था भरोसा, हमारी जिम्मेदारी  •  TAX • PF • GST • DIGITAL SOLUTIONS"
    draw.text((160, 138), slogan_text, fill=accent_color, font=font_hindi_slogan, anchor="lm")
    
    # Category capsule badge in top-right
    badge_w, badge_h = 200, 44
    badge_x, badge_y = 1010 - badge_w, 88
    badge_rgba = (theme_color[0], theme_color[1], theme_color[2], 160)
    draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + badge_h], radius=22, fill=badge_rgba, outline=accent_color, width=2)
    draw.text((badge_x + badge_w/2, badge_y + badge_h/2), f"{theme['name']} SERVICE", fill=(255, 255, 255), font=get_font("Segoeuib", 18), anchor="mm")
    
    # 5. Draw Bottom Info Dashboard (y = 560 to 1030, height = 470)
    # Lighter glass panel for bottom dashboard (55% opacity)
    img = draw_glass_card(img, 40, 560, 1000, 470, bg_color=(10, 16, 28, 140), border_color=(accent_color[0], accent_color[1], accent_color[2], 150), border_width=3, radius=24)
    
    # Refresh draw interface
    draw = ImageDraw.Draw(img)
    
    # --- Left Column (Text & Tips) ---
    # Section Tagline
    draw.text((80, 600), category_tagline.upper(), fill=accent_color, font=get_font("Segoeuib", 18), anchor="lm")
    # Large Section Title
    draw.text((80, 640), title, fill=(255, 255, 255), font=font_title, anchor="lm")
    
    # Draw checkmark bullet points
    start_y = 705
    spacing = 70
    for idx, tip in enumerate(tips[:3]):
        y_pos = start_y + idx * spacing
        cx, cy = 100, y_pos
        
        # Draw checkmark circle in theme color
        draw.ellipse([cx - 15, cy - 15, cx + 15, cy + 15], fill=theme_color, outline=accent_color, width=2)
        # Draw custom tick symbol programmatically
        draw.line([(cx - 6, cy), (cx - 2, cy + 4)], fill=(255, 255, 255), width=2)
        draw.line([(cx - 2, cy + 4), (cx + 8, cy - 4)], fill=(255, 255, 255), width=2)
        
        # Step number in accent color
        draw.text((140, y_pos), f"0{idx+1}.", fill=accent_color, font=font_tips_bold, anchor="lm")
        # Tip body text in white
        draw.text((190, y_pos), tip, fill=(255, 255, 255), font=font_tips, anchor="lm")
        
    # --- Right Column (Creative circular progress dial) ---
    # Center of dial
    dial_cx, dial_cy = 840, 735
    dial_r = 75
    # Background track arc
    draw.arc([dial_cx - dial_r, dial_cy - dial_r, dial_cx + dial_r, dial_cy + dial_r], start=-225, end=45, fill=(255, 255, 255, 30), width=12)
    # Glowing active track (98% compliance = spanning 260 degrees of arc)
    draw.arc([dial_cx - dial_r, dial_cy - dial_r, dial_cx + dial_r, dial_cy + dial_r], start=-225, end=35, fill=accent_color, width=12)
    
    # Inner Dial Text
    draw.text((dial_cx, dial_cy - 10), "98%", fill=(255, 255, 255), font=get_font("Segoeuib", 32), anchor="mm")
    draw.text((dial_cx, dial_cy + 22), "Accuracy", fill=accent_color, font=get_font("Segoeui", 16), anchor="mm")
    # Under Dial Label
    draw.text((dial_cx, dial_cy + 95), "Compliance Score", fill=(170, 185, 200), font=get_font("Segoeuib", 16), anchor="mm")
    
    # --- Footer Area ---
    # Thin divider line
    draw.line([(80, 915), (1000, 915)], fill=(255, 255, 255, 30), width=1)
    
    # Contact Details Line (Bilingual + Trust stats)
    draw.text((540, 945), "Phone: +91 7217646673    |    Web: anshu-computer-and-tax-consultants.onrender.com", fill=(170, 185, 200), font=font_footer_details, anchor="mm")
    
    footer_stats_text = "Rated 5.0  •  23+ Verified Google Reviews  •  Serving Nationwide"
    draw.text((540, 980), footer_stats_text, fill=accent_color, font=font_footer_stats, anchor="mm")
    
    # Save output with a unique filename to prevent overwriting/race conditions
    import uuid
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    unique_id = uuid.uuid4().hex[:8]
    image_path = os.path.join(temp_dir, f"daily_post_{unique_id}.jpg")
    img.save(image_path, "JPEG", quality=95)
    print(f"[PILLOW] High-fidelity centered floating card saved to: {image_path}", flush=True)
    return image_path


def generate_catalog_poster() -> str:
    """Generate a comprehensive 9:16 portrait flyer detailing all services.
    
    Renders Anshu Computer & Tax Consultancy's 6 core service catalog cards
    complete with trust badges, address details, gold leader badge, and a shopfront
    illustration drawn programmatically. Output size is 1080x1920.
    """
    from PIL import Image, ImageDraw, ImageFont
    import os
    import uuid
    
    print("[POSTER] Rendering comprehensive multi-service catalog poster (1080x1920)...", flush=True)
    
    # 1. Base Canvas
    width, height = 1080, 1920
    img = Image.new("RGB", (width, height), (248, 249, 252))
    draw = ImageDraw.Draw(img)
    
    # Draw background gradient
    color_start = (242, 245, 250)
    color_end = (255, 255, 255)
    for y in range(height):
        r = int(color_start[0] + (color_end[0] - color_start[0]) * y / height)
        g = int(color_start[1] + (color_end[1] - color_start[1]) * y / height)
        b = int(color_start[2] + (color_end[2] - color_start[2]) * y / height)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
        
    # Draw top corner wave/design (navy accent)
    draw.polygon([(0, 0), (250, 0), (0, 150)], fill=(15, 34, 64))
    draw.polygon([(width, 0), (width, 220), (width - 180, 0)], fill=(15, 34, 64))
    
    # 2. Fonts
    font_logo = get_font("Segoeuib", 90)
    font_brand = get_font("Segoeuib", 42)
    font_tagline = get_font("Segoeuib", 28)
    font_slogan = get_font("Segoeuib", 30)
    font_section = get_font("Segoeuib", 24)
    font_card_title = get_font("Segoeuib", 26)
    font_bullet = get_font("Segoeui", 22)
    font_footer = get_font("Segoeuib", 20)
    font_badge = get_font("Segoeuib", 16)
    font_badge_bold = get_font("Segoeuib", 20)
    
    # 3. Header Drawing
    # Logo "A"
    logo_cx, logo_cy = 130, 175
    # Yellow swoosh arc
    draw.arc([logo_cx - 60, logo_cy - 60, logo_cx + 60, logo_cy + 60], start=45, end=275, fill=(230, 175, 45), width=8)
    # Blue swoosh arc
    draw.arc([logo_cx - 48, logo_cy - 48, logo_cx + 48, logo_cy + 48], start=180, end=90, fill=(15, 34, 64), width=6)
    draw.text((logo_cx, logo_cy - 5), "A", fill=(15, 34, 64), font=font_logo, anchor="mm")
    
    # Brand Text
    draw.text((220, 115), "ANSHU", fill=(15, 34, 64), font=get_font("Segoeuib", 64))
    draw.text((220, 195), "COMPUTER & TAX CONSULTANCY", fill=(15, 34, 64), font=font_brand)
    # Yellow divider line
    draw.line([(220, 248), (950, 248)], fill=(230, 175, 45), width=3)
    draw.text((220, 260), "PF, TAX & COMPLIANCE SOLUTIONS", fill=(230, 175, 45), font=font_tagline)
    
    # Slogan
    draw.text((width/2, 335), "Reliable Services. Maximum Value.", fill=(15, 34, 64), font=font_slogan, anchor="mm")
    
    # 4. Trust Markers Row
    trust_y = 410
    trust_x = [230, 540, 850]
    labels = [
        ("TRUSTED BY", "HUNDREDS OF CLIENTS"),
        ("TIMELY SERVICE", "QUALITY ASSURED"),
        ("100% SECURE", "& CONFIDENTIAL")
    ]
    
    for idx, cx in enumerate(trust_x):
        # Draw dark navy circle with gold border
        cy = trust_y
        draw.ellipse([cx - 32, cy - 32, cx + 32, cy + 32], fill=(15, 34, 64), outline=(230, 175, 45), width=2)
        
        # Draw simple icon outlines
        if idx == 0: # Trusted checkmark shield
            draw.line([(cx - 8, cy), (cx - 2, cy + 8)], fill=(255, 255, 255), width=3)
            draw.line([(cx - 2, cy + 8), (cx + 10, cy - 6)], fill=(255, 255, 255), width=3)
        elif idx == 1: # Timely clock
            draw.arc([cx - 15, cy - 15, cx + 15, cy + 15], start=0, end=360, fill=(255, 255, 255), width=2)
            draw.line([(cx, cy), (cx, cy - 10)], fill=(255, 255, 255), width=2)
            draw.line([(cx, cy), (cx + 8, cy)], fill=(255, 255, 255), width=2)
        elif idx == 2: # Secure shield
            draw.polygon([(cx - 12, cy - 14), (cx + 12, cy - 14), (cx + 12, cy), (cx, cy + 14), (cx - 12, cy)], outline=(255, 255, 255), width=2)
            
        # Draw text labels underneath
        line1, line2 = labels[idx]
        draw.text((cx, cy + 50), line1, fill=(15, 34, 64), font=get_font("Segoeuib", 16), anchor="mm")
        draw.text((cx, cy + 70), line2, fill=(230, 175, 45), font=get_font("Segoeui", 16), anchor="mm")
        
    # 5. Section Header Badge
    badge_rect = [width/2 - 180, 520, width/2 + 180, 570]
    draw.rounded_rectangle(badge_rect, radius=25, fill=(15, 34, 64))
    draw.text((width/2, 545), "OUR SERVICES", fill=(255, 255, 255), font=font_section, anchor="mm")
    
    # 6. Grid Cards Configuration
    cards_data = [
        {
            "title": "PF SERVICES",
            "color": (26, 115, 232),
            "bullets": ["PF KYC & Corrections", "Advance PF Claim", "PF Settlement", "PPO (Pension) Services", "Multiple Company Adjustments"]
        },
        {
            "title": "INCOME TAX",
            "color": (19, 115, 51),
            "bullets": ["ITR Filing (Salary/Business)", "Tax Planning & Audit Help", "Refund Assistance", "Form 16 & AIS Correction"]
        },
        {
            "title": "GST SERVICES",
            "color": (104, 29, 168),
            "bullets": ["GST Registration", "Monthly/Quarterly Return Filing", "GST Compliance Audits", "Notice Reply & Reconciliation"]
        },
        {
            "title": "CSC SERVICES",
            "color": (232, 113, 10),
            "bullets": ["Digital Seva Registrations", "Central/State Govt. Schemes", "Online Application Processing", "Aadhaar & Citizen Services"]
        },
        {
            "title": "COMPLIANCE",
            "color": (190, 30, 30),
            "bullets": ["Udyam (MSME) Registration", "Digital Signature (DSC) Class 3", "PAN / TAN Registration", "Trade License & Business Support"]
        },
        {
            "title": "CONSULTANCY",
            "color": (15, 140, 140),
            "bullets": ["Expert Guidance on Compliance", "Fast & Secure Claims", "Personalized Support", "Bilingual Consultation (B2B/B2C)"]
        }
    ]

    card_w, card_h = 440, 310
    col_x = [80, 560]
    row_y = [600, 930, 1260]
    
    for idx, card in enumerate(cards_data):
        col = idx % 2
        row = idx // 2
        x1 = col_x[col]
        y1 = row_y[row]
        x2 = x1 + card_w
        y2 = y1 + card_h
        
        # Draw shadow and card shape
        draw.rounded_rectangle([x1 + 3, y1 + 3, x2 + 3, y2 + 3], radius=16, fill=(230, 235, 245))
        draw.rounded_rectangle([x1, y1, x2, y2], radius=16, fill=(255, 255, 255), outline=(215, 220, 230), width=2)
        
        # Colored circle for icon badge
        circle_cx, circle_cy = x1 + 60, y1 + 55
        draw.ellipse([circle_cx - 24, circle_cy - 24, circle_cx + 24, circle_cy + 24], fill=card["color"])
        draw.text((circle_cx, circle_cy - 1), card["title"][:2], fill=(255, 255, 255), font=font_badge, anchor="mm")
        
        # Card Title
        draw.text((x1 + 105, y1 + 53), card["title"], fill=(15, 34, 64), font=font_card_title, anchor="lm")
        # Divider line
        draw.line([(x1 + 30, y1 + 95), (x2 - 30, y1 + 95)], fill=(230, 235, 245), width=1)
        
        # Bullet list points
        bullet_start_y = y1 + 125
        spacing = 35
        for b_idx, bullet in enumerate(card["bullets"]):
            by = bullet_start_y + b_idx * spacing
            draw.ellipse([x1 + 38, by - 5, x1 + 44, by + 1], fill=card["color"])
            draw.text((x1 + 60, by), bullet, fill=(50, 55, 65), font=font_bullet, anchor="lm")
            
    # 7. Bottom Section & Slogan
    # Slogan placed between cards and bottom section
    draw.text((width/2, 1590), "We Simplify Compliance, You Focus on Growth.", fill=(15, 34, 64), font=get_font("Segoeuii", 24), anchor="mm")
    
    bottom_y = 1610
    
    # Left: Gold Badge
    badge_cx, badge_cy = 240, bottom_y + 80
    draw.polygon([(badge_cx - 60, badge_cy + 60), (badge_cx + 60, badge_cy + 60), (badge_cx + 80, badge_cy + 100), (badge_cx, badge_cy + 85), (badge_cx - 80, badge_cy + 100)], fill=(15, 34, 64))
    
    draw.ellipse([badge_cx - 80, badge_cy - 80, badge_cx + 80, badge_cy + 80], fill=(255, 235, 150), outline=(225, 175, 45), width=4)
    draw.ellipse([badge_cx - 68, badge_cy - 68, badge_cx + 68, badge_cy + 68], fill=(255, 242, 185), outline=(225, 175, 45), width=2)
    
    draw.text((badge_cx, badge_cy - 45), "★ ★ ★", fill=(225, 175, 45), font=font_badge_bold, anchor="mm")
    draw.text((badge_cx, badge_cy - 12), "MARKET", fill=(15, 34, 64), font=font_badge_bold, anchor="mm")
    draw.text((badge_cx, badge_cy + 12), "LEADER", fill=(15, 34, 64), font=font_badge_bold, anchor="mm")
    draw.text((badge_cx, badge_cy + 36), "IN DISTRICT", fill=(225, 175, 45), font=font_badge, anchor="mm")
    draw.text((badge_cx, badge_cy + 75), "IN PF SERVICES", fill=(255, 255, 255), font=get_font("Segoeuib", 14), anchor="mm")
    
    # Right: Storefront Line-Drawing Illustration
    shop_cx, shop_cy = 780, bottom_y + 80
    draw.rectangle([shop_cx - 150, shop_cy - 40, shop_cx + 150, shop_cy + 80], fill=(245, 248, 255), outline=(15, 34, 64), width=3)
    draw.polygon([
        (shop_cx - 170, shop_cy - 40), 
        (shop_cx + 170, shop_cy - 40), 
        (shop_cx + 140, shop_cy - 80), 
        (shop_cx - 140, shop_cy - 80)
    ], fill=(15, 34, 64))
    
    draw.rectangle([shop_cx - 110, shop_cy - 72, shop_cx + 110, shop_cy - 48], fill=(255, 242, 185), outline=(225, 175, 45), width=2)
    draw.text((shop_cx, shop_cy - 60), "ANSHU COMPUTER & TAX", fill=(15, 34, 64), font=get_font("Segoeuib", 14), anchor="mm")
    
    # Windows & Door
    draw.rectangle([shop_cx - 120, shop_cy - 10, shop_cx - 40, shop_cy + 40], fill=(230, 245, 255), outline=(15, 34, 64), width=2)
    draw.line([(shop_cx - 80, shop_cy - 10), (shop_cx - 80, shop_cy + 40)], fill=(15, 34, 64), width=2)
    
    draw.rectangle([shop_cx + 40, shop_cy - 10, shop_cx + 120, shop_cy + 40], fill=(230, 245, 255), outline=(15, 34, 64), width=2)
    draw.line([(shop_cx + 80, shop_cy - 10), (shop_cx + 80, shop_cy + 40)], fill=(15, 34, 64), width=2)
    
    draw.rectangle([shop_cx - 25, shop_cy - 10, shop_cx + 25, shop_cy + 80], fill=(255, 255, 255), outline=(15, 34, 64), width=2)
    draw.ellipse([shop_cx + 12, shop_cy + 35, shop_cx + 18, shop_cy + 41], fill=(15, 34, 64))
    
    # 8. Footer address and contact bar (multi-line structured bar)
    footer_rect = [0, height - 140, width, height]
    draw.rectangle(footer_rect, fill=(15, 34, 64))
    draw.line([(0, height - 140), (width, height - 140)], fill=(230, 175, 45), width=3)
    
    # Row 1: WhatsApp / Call & Email
    row1_text = "📞 Call / WhatsApp: +91 7217646673   •   ✉️ Email: anshucomputerorai@gmail.com"
    draw.text((width/2, height - 105), row1_text, fill=(255, 255, 255), font=font_footer, anchor="mm")
    
    # Row 2: Website & Twitter / X
    row2_text = "🌐 Web: anshu-computer-and-tax-consultants.onrender.com   •   🐦 Twitter/X: @ssoni0007"
    draw.text((width/2, height - 70), row2_text, fill=(230, 175, 45), font=font_footer, anchor="mm")
    
    # Row 3: Address
    row3_text = "📍 Office: Kaushal Market, Rath Road, Orai (Jalaun) U.P. - 285001"
    draw.text((width/2, height - 35), row3_text, fill=(255, 255, 255), font=font_footer, anchor="mm")
    
    # Save output image with a unique filename
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    unique_id = uuid.uuid4().hex[:8]
    image_path = os.path.join(temp_dir, f"catalog_poster_{unique_id}.jpg")
    img.save(image_path, "JPEG", quality=95)
    print(f"[POSTER SUCCESS] Renders multi-service catalog poster saved to {image_path}", flush=True)
    return image_path


def generate_flux_graphic(prompt: str) -> str:
    """Generate or retrieve a high-quality campaign poster background.
    
    Tries:
    1. NVIDIA NIM API (FLUX.1-schnell) if NVIDIA_API_KEY is configured. (Free, custom AI generation)
    2. Google Gemini API (Imagen 4) if GEMINI_API_KEY is configured. (Free, custom AI generation)
    3. Hugging Face Inference API if HF_TOKEN or HUGGINGFACE_API_KEY is configured. (Free, custom AI generation)
    4. DuckDuckGo Images search as a keyless high-quality stock illustration fallback (optimized keywords).
    5. Pollinations.ai (Flux) as a keyless AI fallback.
    6. Hercai v3 as a secondary keyless AI fallback.
    """
    import uuid
    import time
    import requests
    import urllib.parse
    import re
    import base64
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    unique_id = uuid.uuid4().hex[:8]
    image_path = os.path.join(temp_dir, f"flux_backdrop_{unique_id}.jpg")
    
    # Attempt 1: NVIDIA NIM API (FLUX.1-schnell)
    nvidia_key = os.environ.get("NVIDIA_API_KEY")
    if nvidia_key:
        print("[IMAGE ENGINE] Attempting image generation via NVIDIA FLUX.1-schnell...", flush=True)
        try:
            url = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-schnell"
            headers = {
                "Authorization": f"Bearer {nvidia_key}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            payload = {
                "prompt": prompt,
                "steps": 4,
                "seed": 0,
                "width": 1024,
                "height": 1024
            }
            # Set a timeout of 15 seconds so we don't hang the thread if the key has no access/times out
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            if resp.status_code == 200:
                res_json = resp.json()
                if "artifacts" in res_json and res_json["artifacts"]:
                    item = res_json["artifacts"][0]
                    if "base64" in item:
                        img_bytes = base64.b64decode(item["base64"])
                        with open(image_path, "wb") as f:
                            f.write(img_bytes)
                        print(f"[IMAGE ENGINE SUCCESS] Generated image via NVIDIA FLUX.1-schnell saved to {image_path}", flush=True)
                        return image_path
                elif "data" in res_json and res_json["data"]:
                    item = res_json["data"][0]
                    if "b64_json" in item:
                        img_bytes = base64.b64decode(item["b64_json"])
                        with open(image_path, "wb") as f:
                            f.write(img_bytes)
                        print(f"[IMAGE ENGINE SUCCESS] Generated image via NVIDIA FLUX.1-schnell saved to {image_path}", flush=True)
                        return image_path
            else:
                print(f"[IMAGE ENGINE WARNING] NVIDIA API returned status code {resp.status_code}: {resp.text[:200]}", flush=True)
        except Exception as e:
            print(f"[IMAGE ENGINE WARNING] NVIDIA image generation failed: {e}", flush=True)

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        gemini_key = os.environ.get("GOOGLE_API_KEY")
        
    # Attempt 2: Google Gemini API (Imagen 4)
    if gemini_key:
        print("[IMAGE ENGINE] Attempting image generation via Google Imagen 4...", flush=True)
        try:
            from google import genai
            from google.genai import types
            
            client = genai.Client(api_key=gemini_key)
            response = client.models.generate_images(
                model='imagen-4.0-generate-001',
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    output_mime_type='image/jpeg',
                    aspect_ratio='1:1'
                )
            )
            if response.generated_images:
                img_bytes = response.generated_images[0].image.image_bytes
                with open(image_path, "wb") as f:
                    f.write(img_bytes)
                print(f"[IMAGE ENGINE SUCCESS] Generated image via Gemini Imagen 4 saved to {image_path}", flush=True)
                return image_path
            else:
                print("[IMAGE ENGINE WARNING] Gemini response returned no images.", flush=True)
        except Exception as e:
            print(f"[IMAGE ENGINE WARNING] Gemini Imagen 4 generation failed: {e}", flush=True)
            
    # Attempt 3: Hugging Face Inference API (Flux Schnell)
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_KEY")
    if hf_token:
        print("[IMAGE ENGINE] Attempting image generation via Hugging Face Inference API...", flush=True)
        model_id = "black-forest-labs/FLUX.1-schnell"
        api_url = f"https://api-inference.huggingface.co/models/{model_id}"
        headers = {"Authorization": f"Bearer {hf_token}"}
        try:
            resp = requests.post(api_url, headers=headers, json={"inputs": prompt}, timeout=40)
            if resp.status_code == 200:
                with open(image_path, "wb") as f:
                    f.write(resp.content)
                print(f"[IMAGE ENGINE SUCCESS] Generated image via Hugging Face {model_id} saved to {image_path}", flush=True)
                return image_path
            else:
                print(f"[IMAGE ENGINE WARNING] Hugging Face returned status code {resp.status_code}: {resp.text}", flush=True)
        except Exception as e:
            print(f"[IMAGE ENGINE WARNING] Hugging Face generation failed: {e}", flush=True)

    # Attempt 4: Pollinations.ai (Flux) keyless AI fallback
    print("[IMAGE ENGINE] Attempting keyless generation via Pollinations.ai...", flush=True)
    encoded_prompt = urllib.parse.quote_plus(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&private=true"
    for attempt in range(1, 4):
        try:
            resp = requests.get(
                url, 
                timeout=25, 
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            )
            resp.raise_for_status()
            with open(image_path, "wb") as f:
                f.write(resp.content)
            print(f"[IMAGE ENGINE SUCCESS] Generated image via Pollinations saved to {image_path}", flush=True)
            return image_path
        except Exception as e:
            print(f"[IMAGE ENGINE WARNING] Pollinations attempt {attempt} failed: {e}", flush=True)
            time.sleep(2)
            
    # Attempt 5: Hercai v3 keyless AI fallback
    print("[IMAGE ENGINE] Attempting keyless generation via Hercai API...", flush=True)
    try:
        encoded_prompt = urllib.parse.quote_plus(prompt)
        hercai_url = f"https://hercai.onrender.com/v3/text2image?prompt={encoded_prompt}"
        resp = requests.get(hercai_url, timeout=25)
        if resp.status_code == 200:
            data = resp.json()
            img_url = data.get("url")
            if img_url:
                print(f"[IMAGE ENGINE] Downloading image from Hercai URL: {img_url}", flush=True)
                img_resp = requests.get(
                    img_url, 
                    timeout=20,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
                )
                img_resp.raise_for_status()
                with open(image_path, "wb") as f:
                    f.write(img_resp.content)
                print(f"[IMAGE ENGINE SUCCESS] Generated image via Hercai saved to {image_path}", flush=True)
                return image_path
            else:
                print("[IMAGE ENGINE WARNING] Hercai API did not return an image URL.", flush=True)
        else:
            print(f"[IMAGE ENGINE WARNING] Hercai API returned status: {resp.status_code}", flush=True)
    except Exception as e:
        print(f"[IMAGE ENGINE WARNING] Hercai fallback failed: {e}", flush=True)

    # Attempt 6: DuckDuckGo Images stock photo fallback (Zero-key, reliable and fast!)
    print("[IMAGE ENGINE] Attempting to retrieve stock background illustration via DuckDuckGo Images...", flush=True)
    try:
        from ddgs import DDGS
        
        # Clean prompt and extract core keywords to make a concise search term
        words = [w for w in re.split(r'[\s,.:;!?()"\']', prompt) if w.strip()]
        stop_words = {"a", "an", "the", "and", "or", "but", "with", "featuring", "representing", "minimalist", "minimalism", "3d", "illustration", "premium", "style", "features", "sleek", "abstract", "elements", "vibrant", "corporate", "colors", "clean"}
        keywords = [w for w in words if w.lower() not in stop_words]
        
        # Build search query (max 4 keywords)
        search_term = "minimalist 3d " + " ".join(keywords[:4])
        search_term = search_term[:100]
        
        print(f"[IMAGE ENGINE] Searching DuckDuckGo for: '{search_term}'", flush=True)
        with DDGS() as ddgs:
            results = list(ddgs.images(search_term, max_results=3))
            
        if results:
            for idx, result in enumerate(results):
                img_url = result.get("image")
                if not img_url:
                    continue
                try:
                    print(f"[IMAGE ENGINE] Downloading stock photo (option {idx+1}): {img_url}", flush=True)
                    resp = requests.get(
                        img_url, 
                        timeout=15, 
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
                    )
                    resp.raise_for_status()
                    with open(image_path, "wb") as f:
                        f.write(resp.content)
                    print(f"[IMAGE ENGINE SUCCESS] Retrieved stock background saved to {image_path}", flush=True)
                    return image_path
                except Exception as ex:
                    print(f"[IMAGE ENGINE WARNING] Failed to download from {img_url}: {ex}", flush=True)
            print("[IMAGE ENGINE WARNING] All retrieved DuckDuckGo Image options failed to download.", flush=True)
        else:
            print("[IMAGE ENGINE WARNING] DuckDuckGo Images returned no results.", flush=True)
    except Exception as e:
        print(f"[IMAGE ENGINE WARNING] DuckDuckGo Images fallback failed: {e}", flush=True)
        
    raise RuntimeError("All background image generation/retrieval engines failed.")


def publish_to_facebook_page(image_path: str, caption: str) -> tuple[bool, str]:
    """Publish the photo (if provided) or caption to Facebook Page via Graph API."""
    page_id = os.environ.get("FACEBOOK_PAGE_ID")
    page_token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN")
    
    if not page_id or not page_token:
        return False, "Missing FACEBOOK_PAGE_ID or FACEBOOK_PAGE_ACCESS_TOKEN in environment variables."
        
    has_image = image_path and os.path.exists(image_path)
    
    if has_image:
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
                
                # Auto-backup to Google Drive if credentials exist
                try:
                    try:
                        from .google_service import upload_file_to_drive
                    except ImportError:
                        from google_service import upload_file_to_drive
                    
                    folder_name = "BABU Marketing Posts"
                    print(f"[DRIVE] Backing up published graphic '{os.path.basename(image_path)}' to Google Drive folder '{folder_name}'...", flush=True)
                    ok_drv, drv_msg = upload_file_to_drive(image_path, folder_name)
                    if ok_drv:
                        print(f"[DRIVE SUCCESS] Backup complete: {drv_msg}", flush=True)
                    else:
                        print(f"[DRIVE WARNING] Backup skipped/failed: {drv_msg}", flush=True)
                except Exception as drv_err:
                    print(f"[DRIVE WARNING] Google Drive upload failed: {drv_err}", flush=True)
                
                return True, f"Successfully published to Facebook Page! Post ID: {post_id}"
            else:
                error_msg = res_json.get("error", {}).get("message", "Unknown Graph API error")
                return False, f"Facebook API Error: {error_msg}"
        except Exception as e:
            return False, f"Failed to publish to Facebook: {e}"
    else:
        url = f"https://graph.facebook.com/v19.0/{page_id}/feed"
        try:
            data = {
                "message": caption,
                "access_token": page_token
            }
            print(f"[FACEBOOK] Publishing text update to page {page_id}...", flush=True)
            response = requests.post(url, data=data, timeout=30)
            
            res_json = response.json()
            if response.status_code == 200 and "id" in res_json:
                post_id = res_json["id"]
                return True, f"Successfully published to Facebook Page! Post ID: {post_id}"
            else:
                error_msg = res_json.get("error", {}).get("message", "Unknown Graph API error")
                return False, f"Facebook API Error: {error_msg}"
        except Exception as e:
            return False, f"Failed to publish to Facebook: {e}"


def clean_old_temp_files(temp_dir: str):
    """Clean up files in temp directory older than 12 hours."""
    try:
        import time
        now = time.time()
        for f in os.listdir(temp_dir):
            path = os.path.join(temp_dir, f)
            if os.path.isfile(path) and (f.startswith("daily_post_") or f.startswith("flux_backdrop_") or f.startswith("catalog_poster_")):
                if now - os.path.getmtime(path) > 43200:
                    os.remove(path)
    except Exception as e:
        print(f"[CLEANUP WARNING] Failed to clean old temp files: {e}", flush=True)


def generate_social_post_draft(custom_topic: str = None) -> dict:
    """Scrape trends (or use custom topic), generate caption, image prompt, download FLUX backdrop, and render Pillow glass card."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, "temp")
    if os.path.exists(temp_dir):
        clean_old_temp_files(temp_dir)
        
    import random
    import datetime
    
    # Determine the daily schedule calendar topic based on day of week in India (Asia/Kolkata)
    try:
        tz_offset = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now = datetime.datetime.now(tz_offset)
    except Exception:
        now = datetime.datetime.now()
        
    day_of_week = now.weekday()  # 0 = Monday, ..., 6 = Sunday
    
    day_topics = {
        0: "ITR Filing Reminder & Tax Compliance advice",
        1: "PF Withdrawal Services & EPF Claim Settlement solutions",
        2: "GST Compliance, registration, and monthly return filing advice",
        3: "Pension, PPO Services, and Jeevan Pramaan advice",
        4: "CSC and Digital Citizen Services (Aadhaar, government schemes)",
        5: "Customer Testimonials, trust, and success stories for tax/PF help",
        6: "Motivational Business, Finance, or Compliance Quote for growth"
    }
    today_topic = day_topics.get(day_of_week, "General Tax and PF Compliance consultancy advice")
    
    # Renders catalog poster with 30% probability on autonomous runs, otherwise follows day-of-week single service
    is_catalog = (custom_topic == "FORCE_CATALOG") or (custom_topic is None and random.random() < 0.3)
    
    if is_catalog:
        print("[SOCIAL] Generating Multi-Service Catalog Poster...", flush=True)
        catalog_topic = "complete services overview and brand introduction listing PF, Income Tax, GST, CSC and Compliance"
        caption, img_prompt, card_title, card_tips, category = generate_daily_post(catalog_topic)
        img_path = generate_catalog_poster()
    else:
        selected_topic = custom_topic if custom_topic else today_topic
        print(f"[SOCIAL] Generating Featured Single-Service Post for topic: '{selected_topic}'...", flush=True)
        caption, img_prompt, card_title, card_tips, category = generate_daily_post(selected_topic)
        
        bg_path = None
        try:
            print(f"[SOCIAL] Attempting to generate rich FLUX background image for topic '{selected_topic}'...", flush=True)
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
                exception_msg=msg,
                goal="Daily Autonomous Marketing Post generation and publishing"
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
            exception_msg=error_msg,
            goal="Daily Autonomous Marketing Post generation and publishing"
        )
        import gc
        gc.collect()
        return False, f"Autonomous workflow failed: {error_msg}", caption, img_path
  
