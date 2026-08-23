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


def extract_and_parse_post_json(text: str) -> dict:
    """Extract and parse JSON from LLM output, resilient to unescaped newlines, quotes, and markdown."""
    if not text:
        raise ValueError("Empty LLM output")
    
    text = text.strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    candidate = match.group(0) if match else text
    
    # 1. Standard json.loads
    try:
        return json.loads(candidate)
    except Exception:
        pass
        
    # 2. Strict=False
    try:
        return json.loads(candidate, strict=False)
    except Exception:
        pass
        
    # 3. State-machine to escape raw newlines inside double-quoted strings
    try:
        fixed = []
        in_string = False
        escape = False
        for ch in candidate:
            if ch == '\\' and not escape:
                escape = True
                fixed.append(ch)
                continue
            if ch == '"' and not escape:
                in_string = not in_string
            elif ch == '\n' and in_string:
                fixed.append('\\n')
                escape = False
                continue
            elif ch == '\r' and in_string:
                escape = False
                continue
            fixed.append(ch)
            escape = False
        fixed_str = ''.join(fixed)
        return json.loads(fixed_str, strict=False)
    except Exception:
        pass

    # 4. Regex key-value extraction fallback
    data = {}
    cat_match = re.search(r'"category"\s*:\s*"([^"]+)"', candidate)
    if cat_match:
        data["category"] = cat_match.group(1)
        
    title_match = re.search(r'"card_title"\s*:\s*"([^"]+)"', candidate)
    if title_match:
        data["card_title"] = title_match.group(1)
        
    prompt_match = re.search(r'"image_prompt"\s*:\s*"([^"]+)"', candidate)
    if prompt_match:
        data["image_prompt"] = prompt_match.group(1)
        
    cap_match = re.search(r'"caption"\s*:\s*"(.*?)"\s*,\s*"[a-zA-Z_]+"\s*:', candidate, re.DOTALL)
    if cap_match:
        data["caption"] = cap_match.group(1).replace('\n', ' ').replace('\\n', '\n')
    else:
        # Fallback simple caption search
        cap_simple = re.search(r'"caption"\s*:\s*"([^"]+)"', candidate)
        if cap_simple:
            data["caption"] = cap_simple.group(1)
        
    tips_match = re.search(r'"card_tips"\s*:\s*\[(.*?)\]', candidate, re.DOTALL)
    if tips_match:
        raw_tips = re.findall(r'"([^"]+)"', tips_match.group(1))
        if raw_tips:
            data["card_tips"] = raw_tips
            
    if data.get("caption") or data.get("card_title"):
        return data
        
    raise ValueError(f"Could not parse valid JSON from text: {text[:150]}...")


def generate_daily_post(custom_topic: str = None, language: str = "en") -> tuple[str, str, str, list, str]:
    """Use Groq LLM to generate a caption and matching graphic prompt incorporating real-time India tax and compliance trends in English or Hindi."""
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        raise ValueError("GROQ_API_KEY is not configured in the environment.")
    
    trends_context = fetch_india_tech_trends()
    print(f"[SOCIAL] Integrated Compliance Trends Context:\n{trends_context}", flush=True)
    
    # Detect language from topic if passed
    is_hindi = language == "hi" or (custom_topic and any(w in custom_topic.lower() for w in ["hindi", "हिंदी", "hinglish"]))
    
    lang_str = "Hindi" if is_hindi else "English"
    print(f"[SOCIAL] Generating daily post caption and prompt in {lang_str} via LLM...", flush=True)
    
    user_prompt = f"Generate today's scheduled post in {lang_str}."
    if is_hindi:
        user_prompt += (
            "\n\nCRITICAL LANGUAGE & BRAND INSTRUCTIONS FOR HINDI POST:\n"
            "- Output valid single-line escaped JSON only. Do not put unescaped raw newlines inside string values.\n"
            "- 'caption': Write engaging, high-converting, professional Hindi copywriting (in clean Devanagari script) with natural Hinglish terms (PF Claim, UAN, KYC, ITR Filing, GST Return, CSC, PAN). Explain the benefit clearly and mention 'अंशु कंप्यूटर एंड टैक्स कंसल्टेंसी, कौशल मार्केट, राठ रोड, उरई (समय सुबह 11:00 बजे से शाम 6:00 बजे तक)'. Add hashtags (#ITRFiling #PFClaim #GSTRegistration #Orai #AnshuConsultancy).\n"
            "- 'card_title': Punchy 2-4 word title in Hindi (e.g. 'PF क्लेम आसान समाधान', 'ITR फाइल करें टैक्स बचाएं', 'GST रिटर्न सही समय पर', 'डिजिटल सेवा केंद्र').\n"
            "- 'card_tips': Exactly 3 actionable points in clean Hindi Devanagari (max 6-8 words per point).\n"
            "- 'category': 'pf', 'itr', 'gst', or 'digital'."
        )
    if custom_topic and not any(w in custom_topic.lower() for w in ["force_catalog", "hindi", "english"]):
        user_prompt += f"\n\nIMPORTANT: Please generate today's post focusing EXACTLY on the user's requested custom topic: '{custom_topic}'. Adjust all copy, card title, and tips to focus on this topic, while keeping it relevant to 'Anshu Computer & Tax Consultancy'!"
    elif trends_context and "Could not fetch" not in trends_context and "No real-time trends" not in trends_context:
        user_prompt += (
            f"\n\nHere is some real-time financial, tax, or EPF compliance news context in India:\n"
            f"-----------\n{trends_context}\n-----------\n\n"
            f"IMPORTANT: Please draft a daily tax or compliance advice/marketing post that naturally addresses or draws inspiration from the real-time Indian tax/compliance news above. Ensure it connects seamlessly to the professional tax, compliance, and e-governance services offered by 'Anshu Computer & Tax Consultancy'!"
        )
    
    models_to_try = [
        ("groq", "openai/gpt-oss-20b"),
        ("groq", "openai/gpt-oss-120b"),
        ("groq", "qwen/qwen3.6-27b"),
        ("nvidia", "meta/llama-3.1-8b-instruct"),
        ("nvidia", "meta/llama-3.3-70b-instruct"),
        ("gemini", "gemini-2.5-flash")
    ]
    
    data = None
    for provider, model_name in models_to_try:
        try:
            if provider == "groq" and os.environ.get("GROQ_API_KEY"):
                llm = ChatGroq(model=model_name, temperature=0.7)
                res = llm.invoke([SystemMessage(content=DAILY_POST_PROMPT), HumanMessage(content=user_prompt)])
            elif provider == "nvidia" and os.environ.get("NVIDIA_API_KEY"):
                from langchain_openai import ChatOpenAI
                llm = ChatOpenAI(
                    model=model_name,
                    temperature=0.7,
                    api_key=os.environ.get("NVIDIA_API_KEY"),
                    base_url="https://integrate.api.nvidia.com/v1",
                    timeout=25.0
                )
                res = llm.invoke([SystemMessage(content=DAILY_POST_PROMPT), HumanMessage(content=user_prompt)])
            elif provider == "gemini" and os.environ.get("GEMINI_API_KEY"):
                from langchain_google_genai import ChatGoogleGenerativeAI
                llm = ChatGoogleGenerativeAI(model=model_name, temperature=0.7, google_api_key=os.environ.get("GEMINI_API_KEY"))
                res = llm.invoke([SystemMessage(content=DAILY_POST_PROMPT), HumanMessage(content=user_prompt)])
            else:
                continue

            if res and res.content:
                data = extract_and_parse_post_json(res.content)
                print(f"[SOCIAL LLM SUCCESS] Generated and parsed post using {provider}:{model_name}", flush=True)
                break
        except Exception as err:
            print(f"[SOCIAL LLM FAILOVER] {provider}:{model_name} failed ({err}). Trying next model...", flush=True)
            continue
    
    if not data:
        raise RuntimeError("All LLM providers failed to generate valid parseable post content.")
    
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


def round_image_corners(im, radius):
    from PIL import Image, ImageDraw
    mask = Image.new("L", im.size, 0)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.rounded_rectangle([0, 0, im.size[0], im.size[1]], radius=radius, fill=255)
    result = Image.new("RGBA", im.size)
    result.paste(im, (0, 0), mask=mask)
    return result

def generate_pillow_graphic(title: str, tips: list, background_path: str = None, category: str = "itr") -> str:
    """Generate a corporate flyer poster in official 4:5 Facebook Feed Ratio (1080x1350).
    
    Layout is fully responsive and calibrated for both Mobile and Desktop Facebook Feeds,
    guaranteeing 100% visible headings, logo, service points, and contact footer without
    cropping or clipping in the feed preview.
    """
    from PIL import Image, ImageDraw, ImageFont
    import math
    import uuid
    
    category = str(category).lower().strip()
    if category not in ["pf", "itr", "gst", "digital"]:
        category = "itr"
        
    try:
        print(f"[PILLOW] Rendering corporate flyer poster (1080x1350) for category '{category}' and title '{title}'...", flush=True)
    except Exception:
        print(f"[PILLOW] Rendering corporate flyer poster (1080x1350) for category '{category}'...", flush=True)
    
    # 1. Theme Configuration
    theme_color = (15, 34, 64)       # Corporate Navy Blue
    accent_color = (230, 175, 45)    # Corporate Gold
    
    category_colors = {
        "pf": (19, 115, 51),        # Green
        "itr": (26, 115, 232),      # Blue
        "gst": (232, 113, 10),      # Orange
        "digital": (104, 29, 168)   # Purple
    }
    category_color = category_colors[category]
    
    category_headlines = {
        "pf": "PF SERVICES & SOLUTIONS",
        "itr": "ITR FILING & TAX ADVISORY",
        "gst": "GST REGISTRATION & FILING",
        "digital": "E-SERVICES & CSC SOLUTIONS"
    }
    headline_text = title.upper() if title and len(title) < 35 else category_headlines[category]
    
    category_tags = {
        "pf": "Apna PF Claim, Hamare Saath Jaldi, Sahi aur Bharosemand",
        "itr": "Sahi ITR Filing, Maximum Tax Refund aur Peace of Mind",
        "gst": "GST Registration, Return Filing aur Comprehensive Compliance Help",
        "digital": "Aadhaar, PAN Card, Passport aur Government Scheme Applications"
    }
    tag_text = category_tags[category]
    
    category_card_headers = {
        "pf": "HAMARI PF SERVICES",
        "itr": "HAMARI ITR SERVICES",
        "gst": "HAMARI GST SERVICES",
        "digital": "HAMARI DIGITAL SERVICES"
    }
    card_header_text = category_card_headers[category]
    
    category_defaults = {
        "pf": ["PF Advance Withdrawal", "PF Final Settlement", "PF Member Transfer", "PF KYC Corrections", "Pension & PPO Help"],
        "itr": ["Salary ITR Filing", "Business Tax Filing", "ITR U (Updated Return)", "Tax Planning & Audit", "TDS Return Filing"],
        "gst": ["GST Registration", "Monthly Return Filing", "Annual Return Filing", "GST Notice Replies", "Reconciliation & ITC"],
        "digital": ["Aadhaar Services", "PAN Card Application", "Passport Application", "PM-Kisan & Govt Schemes", "Citizen E-Services"]
    }
    default_list = category_defaults[category]
    
    category_ctas = {
        "pf": ("PF KA KAAM, AB HOGA AARAM SE!", "Sahi Salah, Sahi Process, Sahi Samay par."),
        "itr": ("ITR FILE KAREN, TAX BACHAYEN!", "Sahi Returns, Sahi Refund, Tension Free."),
        "gst": ("GST COMPLIANCE MEIN NO DERI!", "Business Badhaye, Compliance Hum Par Chhode."),
        "digital": ("DIGITAL SARKARI YOJNA KA LABH!", "Aadhaar, PAN aur Passport Aasani Se Banwayen.")
    }
    cta_title, cta_desc = category_ctas[category]
    
    # 2. Base Canvas (1080x1350 - Standard 4:5 Mobile & PC Feed Ratio)
    width, height = 1080, 1350
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    
    # Soft light background gradient
    color_start = (248, 250, 254)
    color_end = (255, 255, 255)
    for y in range(height):
        r = int(color_start[0] + (color_end[0] - color_start[0]) * y / height)
        g = int(color_start[1] + (color_end[1] - color_start[1]) * y / height)
        b = int(color_start[2] + (color_end[2] - color_start[2]) * y / height)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
        
    # Top accents (Clean geometric corners)
    draw.polygon([(0, 0), (280, 0), (0, 110)], fill=(235, 242, 255))
    draw.polygon([(width, 0), (width, 150), (width - 150, 0)], fill=(235, 242, 255))
    
    # 3. Fonts
    font_brand = get_font("Segoeuib", 42)
    font_brand_sub = get_font("Segoeui", 18)
    font_ribbon = get_font("Segoeuib", 16)
    font_headline = get_font("Segoeuib", 40)
    font_sub_headline = get_font("Segoeuib", 18)
    font_card_header = get_font("Segoeuib", 22)
    font_bullet = get_font("Segoeuib", 18)
    font_bullet_sub = get_font("Segoeui", 15)
    font_cta = get_font("Segoeuib", 24)
    font_cta_sub = get_font("Segoeui", 16)
    font_footer = get_font("Segoeuib", 16)
    font_bottom_badge = get_font("Segoeuib", 15)
    
    # 4. Safe Header Section (Y: 35 to 145)
    logo_cx, logo_cy = 95, 88
    draw.arc([logo_cx - 38, logo_cy - 38, logo_cx + 38, logo_cy + 38], start=45, end=275, fill=accent_color, width=6)
    draw.arc([logo_cx - 28, logo_cy - 28, logo_cx + 28, logo_cy + 28], start=180, end=90, fill=theme_color, width=4)
    draw.text((logo_cx, logo_cy - 4), "A", fill=theme_color, font=get_font("Segoeuib", 48), anchor="mm")
    
    draw.text((155, 42), "ANSHU", fill=theme_color, font=get_font("Segoeuib", 42))
    draw.text((155, 88), "COMPUTER & TAX CONSULTANCY", fill=theme_color, font=get_font("Segoeuib", 24))
    draw.line([(155, 122), (620, 122)], fill=accent_color, width=2)
    draw.text((155, 128), "PF, Tax & Compliance Solutions • 11:00 AM to 6:00 PM", fill=theme_color, font=font_brand_sub)
    
    # Right Ribbon Badge
    ribbon_w, ribbon_h = 220, 88
    ribbon_x = width - ribbon_w - 40
    draw.rectangle([ribbon_x, 38, ribbon_x + ribbon_w, 38 + ribbon_h], fill=theme_color)
    draw.rectangle([ribbon_x, 38 + ribbon_h - 8, ribbon_x + ribbon_w, 38 + ribbon_h], fill=accent_color)
    draw.text((ribbon_x + ribbon_w/2, 68), "TRUSTED & TIMELY", fill=(255, 255, 255), font=font_ribbon, anchor="mm")
    draw.text((ribbon_x + ribbon_w/2, 98), "100% SECURE", fill=accent_color, font=font_ribbon, anchor="mm")
    
    # 5. Headline Section (Y: 165 to 255)
    draw.text((45, 168), headline_text, fill=theme_color, font=font_headline)
    
    # Yellow tag capsule
    tag_rect = [45, 222, 780, 260]
    draw.rounded_rectangle(tag_rect, radius=10, fill=accent_color)
    draw.text((412, 241), tag_text, fill=theme_color, font=font_sub_headline, anchor="mm")
    
    # 6. Main Content Section (Y: 280 to 920)
    # Left Card (Services & Checkmarks)
    card_x1, card_y1, card_x2, card_y2 = 45, 280, 525, 915
    draw.rounded_rectangle([card_x1 + 3, card_y1 + 3, card_x2 + 3, card_y2 + 3], radius=16, fill=(230, 235, 245))
    draw.rounded_rectangle([card_x1, card_y1, card_x2, card_y2], radius=16, fill=(255, 255, 255), outline=(215, 220, 230), width=2)
    
    draw.rounded_rectangle([card_x1, card_y1, card_x2, card_y1 + 55], radius=16, fill=theme_color)
    draw.rectangle([card_x1, card_y1 + 35, card_x2, card_y1 + 55], fill=theme_color)
    draw.text(((card_x1 + card_x2)/2, card_y1 + 28), card_header_text, fill=(255, 255, 255), font=font_card_header, anchor="mm")
    
    # Bullets blending
    merged_bullets = []
    for tip in tips:
        cleaned = str(tip).strip().replace("\n", " ")
        if cleaned:
            merged_bullets.append(cleaned)
    for d_bullet in default_list:
        if len(merged_bullets) >= 5:
            break
        if d_bullet not in merged_bullets:
            merged_bullets.append(d_bullet)
            
    start_y = 355
    spacing = 108
    for idx, bullet in enumerate(merged_bullets[:5]):
        by = start_y + idx * spacing
        icon_cx, icon_cy = card_x1 + 38, by + 12
        draw.ellipse([icon_cx - 18, icon_cy - 18, icon_cx + 18, icon_cy + 18], fill=category_color)
        
        # Checkmark
        draw.line([(icon_cx - 6, icon_cy), (icon_cx - 2, icon_cy + 4)], fill=(255, 255, 255), width=3)
        draw.line([(icon_cx - 2, icon_cy + 4), (icon_cx + 7, icon_cy - 4)], fill=(255, 255, 255), width=3)
        
        words = bullet.split(" ")
        if len(words) > 3:
            l1 = " ".join(words[:3])
            l2 = " ".join(words[3:])
        else:
            l1 = bullet
            l2 = ""
            
        draw.text((card_x1 + 68, by - 2), l1, fill=theme_color, font=font_bullet)
        if l2:
            draw.text((card_x1 + 68, by + 22), l2, fill=(80, 90, 105), font=font_bullet_sub)
            
    # Right Top Section: Custom AI Backdrop illustration (Y: 280 to 680)
    illustration_rect = [555, 280, 1035, 680]
    draw.rounded_rectangle(illustration_rect, radius=16, fill=(245, 248, 255), outline=(215, 220, 230), width=2)
    
    has_custom_bg = False
    if background_path and os.path.exists(background_path):
        try:
            bg_im = Image.open(background_path).convert("RGBA").resize((480, 400), Image.Resampling.LANCZOS)
            bg_rounded = round_image_corners(bg_im, radius=16)
            img.paste(bg_rounded, (555, 280), mask=bg_rounded)
            has_custom_bg = True
        except Exception as e:
            print(f"[PILLOW WARNING] Failed to blend custom image: {e}", flush=True)
            
    if not has_custom_bg:
        phone_cx, phone_cy = 795, 480
        draw.rectangle([phone_cx - 65, phone_cy - 100, phone_cx + 65, phone_cy + 100], fill=(255, 255, 255), outline=theme_color, width=4)
        draw.ellipse([phone_cx - 24, phone_cy - 30, phone_cx + 24, phone_cy + 18], fill=(76, 175, 80))
        draw.line([(phone_cx - 9, phone_cy - 6), (phone_cx - 2, phone_cy + 1)], fill=(255, 255, 255), width=3)
        draw.line([(phone_cx - 2, phone_cy + 1), (phone_cx + 10, phone_cy - 11)], fill=(255, 255, 255), width=3)
        draw.text((phone_cx, phone_cy + 45), "VERIFIED!", fill=theme_color, font=get_font("Segoeuib", 16), anchor="mm")
        draw.text((phone_cx, phone_cy + 70), "TENSION FREE", fill=category_color, font=get_font("Segoeuib", 14), anchor="mm")
        
    # Right Bottom Section: Trust Card (Y: 705 to 915)
    trust_x1, trust_y1, trust_x2, trust_y2 = 555, 705, 1035, 915
    draw.rounded_rectangle([trust_x1 + 3, trust_y1 + 3, trust_x2 + 3, trust_y2 + 3], radius=16, fill=(230, 235, 245))
    draw.rounded_rectangle([trust_x1, trust_y1, trust_x2, trust_y2], radius=16, fill=theme_color, outline=accent_color, width=2)
    
    badge_cx, badge_cy = trust_x1 + 55, trust_y1 + 50
    draw.ellipse([badge_cx - 22, badge_cy - 22, badge_cx + 22, badge_cy + 22], fill=accent_color)
    draw.line([(badge_cx - 7, badge_cy), (badge_cx - 2, badge_cy + 5)], fill=theme_color, width=3)
    draw.line([(badge_cx - 2, badge_cy + 5), (badge_cx + 8, badge_cy - 5)], fill=theme_color, width=3)
    
    draw.text((trust_x1 + 95, trust_y1 + 35), "100% SAFE & SECURE", fill=accent_color, font=get_font("Segoeuib", 24))
    draw.text((trust_x1 + 95, trust_y1 + 65), f"GOVT AUTHORIZED & TRUSTED {category.upper()}", fill=(255, 255, 255), font=get_font("Segoeuib", 15))
    
    stats_y = trust_y1 + 130
    stats_cx = [trust_x1 + 75, trust_x1 + 235, trust_x1 + 395]
    labels = [("FAST", "PROCESS"), ("EXPERT", "SUPPORT"), ("TRUSTED BY", "HUNDREDS")]
    for idx, cx in enumerate(stats_cx):
        lbl1, lbl2 = labels[idx]
        draw.text((cx, stats_y), lbl1, fill=(255, 255, 255), font=get_font("Segoeuib", 14), anchor="mm")
        draw.text((cx, stats_y + 18), lbl2, fill=accent_color, font=get_font("Segoeuib", 14), anchor="mm")
        
    # 7. Yellow CTA Banner (Y: 935 to 1015)
    cta_rect = [0, 935, width, 1015]
    draw.rectangle(cta_rect, fill=accent_color)
    draw.text((60, 975), cta_title, fill=theme_color, font=font_cta, anchor="lm")
    draw.text((width - 60, 975), cta_desc, fill=theme_color, font=font_cta_sub, anchor="rm")
    
    # 8. Bottom Footer Section (Navy background with full address & timings)
    # Row 1: Gold bar (Y: 1015 to 1055)
    draw.rectangle([0, 1015, width, 1055], fill=theme_color)
    draw.text((width/2, 1035), "📞 Call / WhatsApp: +91 7217646673   •   ✉ Email: anshucomputerorai@gmail.com", fill=accent_color, font=font_footer, anchor="mm")
    
    # Row 2: Dark Navy Box (Y: 1055 to 1295)
    draw.rectangle([0, 1055, width, 1295], fill=(10, 25, 48))
    draw.text((width/2, 1098), "📍 Kaushal Market, Rath Road, Orai (Jalaun) U.P. - 285001", fill=(255, 255, 255), font=get_font("Segoeuib", 18), anchor="mm")
    draw.text((width/2, 1142), "⏰ Office Hours: Monday to Saturday (11:00 AM to 6:00 PM) • Sunday Closed", fill=accent_color, font=get_font("Segoeuib", 16), anchor="mm")
    draw.text((width/2, 1185), "🌐 anshu-computer-and-tax-consultants.onrender.com", fill=(200, 220, 255), font=get_font("Segoeui", 16), anchor="mm")
    
    # Row 3: Bottom Trust Bar (Y: 1295 to 1350 - White background with gold line)
    draw.rectangle([0, 1295, width, 1350], fill=(255, 255, 255))
    draw.line([(0, 1295), (width, 1295)], fill=accent_color, width=3)
    draw.text((width/2, 1322), "★ EXPERT TAX & PF CONSULTANCY   •   ★ 100% TIMELY SERVICE   •   ★ ORAI GOVT CSC", fill=theme_color, font=font_bottom_badge, anchor="mm")
    
    # Save output
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    unique_id = uuid.uuid4().hex[:8]
    image_path = os.path.join(temp_dir, f"daily_post_{unique_id}.jpg")
    img.save(image_path, "JPEG", quality=95)
    print(f"[PILLOW] 4:5 Mobile & PC Feed Flyer saved to: {image_path}", flush=True)
    return image_path


def generate_catalog_poster() -> str:
    """Generate a comprehensive multi-service catalog flyer in official 4:5 Facebook Feed Ratio (1080x1350)."""
    from PIL import Image, ImageDraw, ImageFont
    import os
    import uuid
    
    print("[POSTER] Rendering comprehensive multi-service catalog poster (1080x1350)...", flush=True)
    
    width, height = 1080, 1350
    img = Image.new("RGB", (width, height), (248, 249, 252))
    draw = ImageDraw.Draw(img)
    
    # Background gradient
    color_start = (242, 245, 250)
    color_end = (255, 255, 255)
    for y in range(height):
        r = int(color_start[0] + (color_end[0] - color_start[0]) * y / height)
        g = int(color_start[1] + (color_end[1] - color_start[1]) * y / height)
        b = int(color_start[2] + (color_end[2] - color_start[2]) * y / height)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
        
    theme_color = (15, 34, 64)
    accent_color = (230, 175, 45)
    
    # Top accents
    draw.polygon([(0, 0), (250, 0), (0, 100)], fill=theme_color)
    draw.polygon([(width, 0), (width, 140), (width - 150, 0)], fill=theme_color)
    
    # 1. Header (Y: 35 to 135)
    logo_cx, logo_cy = 90, 80
    draw.arc([logo_cx - 38, logo_cy - 38, logo_cx + 38, logo_cy + 38], start=45, end=275, fill=accent_color, width=6)
    draw.arc([logo_cx - 28, logo_cy - 28, logo_cx + 28, logo_cy + 28], start=180, end=90, fill=theme_color, width=4)
    draw.text((logo_cx, logo_cy - 4), "A", fill=theme_color, font=get_font("Segoeuib", 48), anchor="mm")
    
    draw.text((150, 38), "ANSHU", fill=theme_color, font=get_font("Segoeuib", 42))
    draw.text((150, 84), "COMPUTER & TAX CONSULTANCY", fill=theme_color, font=get_font("Segoeuib", 24))
    draw.line([(150, 116), (600, 116)], fill=accent_color, width=2)
    draw.text((150, 122), "PF, TAX & COMPLIANCE SOLUTIONS • 11 AM - 6 PM", fill=accent_color, font=get_font("Segoeuib", 16))
    
    # Right Badge
    draw.rectangle([width - 250, 35, width - 40, 120], fill=theme_color)
    draw.rectangle([width - 250, 112, width - 40, 120], fill=accent_color)
    draw.text((width - 145, 65), "TRUSTED BY 1000+", fill=(255, 255, 255), font=get_font("Segoeuib", 16), anchor="mm")
    draw.text((width - 145, 92), "QUALITY ASSURED", fill=accent_color, font=get_font("Segoeuib", 15), anchor="mm")
    
    # 2. Section Header Badge (Y: 155 to 195)
    badge_rect = [width/2 - 160, 155, width/2 + 160, 195]
    draw.rounded_rectangle(badge_rect, radius=20, fill=theme_color)
    draw.text((width/2, 175), "OUR CORE SERVICES", fill=(255, 255, 255), font=get_font("Segoeuib", 20), anchor="mm")
    
    # 3. 6 Grid Cards (2 columns x 3 rows, Y: 215 to 975)
    cards_data = [
        {"title": "PF SERVICES", "color": (26, 115, 232), "bullets": ["PF KYC & Corrections", "Advance PF Claim", "PF Final Settlement", "PPO (Pension) Services"]},
        {"title": "INCOME TAX", "color": (19, 115, 51), "bullets": ["ITR Filing (Salary/Business)", "Tax Planning & Audit Help", "Refund Assistance", "Form 16 & AIS Correction"]},
        {"title": "GST SERVICES", "color": (104, 29, 168), "bullets": ["GST Registration", "Monthly/Quarterly Return Filing", "GST Compliance Audits", "Notice Reply & Reconciliation"]},
        {"title": "CSC SERVICES", "color": (232, 113, 10), "bullets": ["Digital Seva Registrations", "Central/State Govt Schemes", "Aadhaar & Citizen Services", "PM-Kisan & Scheme Forms"]},
        {"title": "COMPLIANCE", "color": (190, 30, 30), "bullets": ["Udyam (MSME) Registration", "Digital Signature (DSC) Class 3", "PAN / TAN Registration", "Trade License & Business Support"]},
        {"title": "CONSULTANCY", "color": (15, 140, 140), "bullets": ["Expert Guidance on Compliance", "Fast & Secure Claims", "Personalized Support", "Bilingual Consultation (B2B/B2C)"]}
    ]
    
    card_w, card_h = 460, 235
    col_x = [60, 560]
    row_y = [215, 475, 735]
    
    for idx, card in enumerate(cards_data):
        col = idx % 2
        row = idx // 2
        x1 = col_x[col]
        y1 = row_y[row]
        x2 = x1 + card_w
        y2 = y1 + card_h
        
        draw.rounded_rectangle([x1 + 3, y1 + 3, x2 + 3, y2 + 3], radius=14, fill=(230, 235, 245))
        draw.rounded_rectangle([x1, y1, x2, y2], radius=14, fill=(255, 255, 255), outline=(215, 220, 230), width=2)
        
        # Circle badge
        circle_cx, circle_cy = x1 + 45, y1 + 40
        draw.ellipse([circle_cx - 18, circle_cy - 18, circle_cx + 18, circle_cy + 18], fill=card["color"])
        draw.text((circle_cx, circle_cy - 1), card["title"][:2], fill=(255, 255, 255), font=get_font("Segoeuib", 14), anchor="mm")
        
        draw.text((x1 + 75, y1 + 38), card["title"], fill=theme_color, font=get_font("Segoeuib", 20), anchor="lm")
        draw.line([(x1 + 25, y1 + 68), (x2 - 25, y1 + 68)], fill=(235, 240, 248), width=1)
        
        bullet_start_y = y1 + 92
        spacing = 32
        for b_idx, bullet in enumerate(card["bullets"]):
            by = bullet_start_y + b_idx * spacing
            draw.ellipse([x1 + 30, by - 4, x1 + 36, by + 2], fill=card["color"])
            draw.text((x1 + 48, by), bullet, fill=(50, 55, 65), font=get_font("Segoeui", 17), anchor="lm")
            
    # 4. CTA Ribbon (Y: 995 to 1065)
    draw.rectangle([0, 995, width, 1065], fill=accent_color)
    draw.text((width/2, 1030), "We Simplify Compliance, You Focus on Growth. Contact Anshu Consultancy Today!", fill=theme_color, font=get_font("Segoeuib", 20), anchor="mm")
    
    # 5. Navy Footer Section (Y: 1065 to 1350)
    draw.rectangle([0, 1065, width, 1295], fill=(10, 25, 48))
    draw.text((width/2, 1105), "📞 Call / WhatsApp: +91 7217646673   •   ✉ Email: anshucomputerorai@gmail.com", fill=accent_color, font=get_font("Segoeuib", 18), anchor="mm")
    draw.text((width/2, 1150), "📍 Office: Kaushal Market, Rath Road, Orai (Jalaun) U.P. - 285001 • Open Mon-Sat 11 AM - 6 PM", fill=(255, 255, 255), font=get_font("Segoeuib", 16), anchor="mm")
    draw.text((width/2, 1195), "🌐 anshu-computer-and-tax-consultants.onrender.com   •   🐦 @ssoni0007", fill=(200, 220, 255), font=get_font("Segoeui", 16), anchor="mm")
    
    draw.rectangle([0, 1295, width, 1350], fill=(255, 255, 255))
    draw.line([(0, 1295), (width, 1295)], fill=accent_color, width=3)
    draw.text((width/2, 1322), "★ EXPERT TAX & PF CONSULTANCY   •   ★ 100% TIMELY SERVICE   •   ★ ORAI GOVT CSC", fill=theme_color, font=get_font("Segoeuib", 16), anchor="mm")
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    unique_id = uuid.uuid4().hex[:8]
    image_path = os.path.join(temp_dir, f"catalog_poster_{unique_id}.jpg")
    img.save(image_path, "JPEG", quality=95)
    print(f"[POSTER SUCCESS] Multi-service 4:5 catalog poster saved to {image_path}", flush=True)
    return image_path


def generate_flux_graphic(prompt: str) -> str:
    """Generate a high-quality campaign poster background.
    
    Order of operations:
    1. Pollinations.ai (Flux) - Primary keyless fast 1024x1024 AI image generation.
    2. DuckDuckGo Images search - Secondary keyless high-quality stock illustration fallback.
    3. Hercai v3 - Keyless AI fallback.
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
    
    # 1. Primary Engine: Pollinations.ai (Flux Keyless AI - Fast & High Quality)
    print(f"[IMAGE ENGINE] Generating 3D background graphic via Pollinations.ai...", flush=True)
    encoded_prompt = urllib.parse.quote_plus(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&private=true"
    for attempt in range(1, 3):
        try:
            resp = requests.get(
                url, 
                timeout=12, 
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            )
            if resp.status_code == 200 and len(resp.content) > 5000:
                with open(image_path, "wb") as f:
                    f.write(resp.content)
                print(f"[IMAGE ENGINE SUCCESS] Generated 3D backdrop saved to {image_path}", flush=True)
                return image_path
        except Exception as e:
            print(f"[IMAGE ENGINE WARNING] Pollinations attempt {attempt} failed: {e}", flush=True)
            time.sleep(1)

    # 2. Secondary Engine: DuckDuckGo Images Stock Vector Search (Keyless, fast stock graphics)
    print("[IMAGE ENGINE] Searching stock background illustration via DuckDuckGo...", flush=True)
    try:
        from ddgs import DDGS
        words = [w for w in re.split(r'[\s,.:;!?()"\']', prompt) if w.strip()]
        stop_words = {"a", "an", "the", "and", "or", "but", "with", "featuring", "representing", "minimalist", "minimalism", "3d", "illustration", "premium", "style", "features", "sleek", "abstract", "elements", "vibrant", "corporate", "colors", "clean"}
        keywords = [w for w in words if w.lower() not in stop_words]
        query = " ".join(keywords[:4]) + " corporate illustration backdrop" if keywords else "corporate vector illustration backdrop"
        
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=3))
            for res in results:
                img_url = res.get("image")
                if img_url and img_url.startswith("http"):
                    try:
                        r = requests.get(img_url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
                        if r.status_code == 200 and len(r.content) > 5000:
                            with open(image_path, "wb") as f:
                                f.write(r.content)
                            print(f"[IMAGE ENGINE SUCCESS] Stock backdrop saved to {image_path}", flush=True)
                            return image_path
                    except Exception:
                        continue
    except Exception as ddg_err:
        print(f"[IMAGE ENGINE WARNING] DuckDuckGo image search skipped: {ddg_err}", flush=True)

    # 3. Third Engine: Hercai v3 Fallback
    try:
        print("[IMAGE ENGINE] Attempting keyless generation via Hercai API...", flush=True)
        hercai_url = f"https://hercai.onrender.com/v3/text2image?prompt={encoded_prompt}"
        resp = requests.get(hercai_url, timeout=10)
        if resp.status_code == 200:
            img_url = resp.json().get("url")
            if img_url:
                img_resp = requests.get(img_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                if img_resp.status_code == 200 and len(img_resp.content) > 5000:
                    with open(image_path, "wb") as f:
                        f.write(img_resp.content)
                    print(f"[IMAGE ENGINE SUCCESS] Generated image via Hercai saved to {image_path}", flush=True)
                    return image_path
    except Exception as e:
        print(f"[IMAGE ENGINE WARNING] Hercai fallback failed: {e}", flush=True)

    raise RuntimeError("All background image generation/retrieval engines failed.")


def publish_to_facebook_page(image_path: str, caption: str) -> tuple[bool, str]:
    """Publish photo or caption directly to the Facebook Page Timeline Feed via Graph API."""
    page_id = os.environ.get("FACEBOOK_PAGE_ID")
    page_token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN")
    
    if not page_id or not page_token:
        return False, "Missing FACEBOOK_PAGE_ID or FACEBOOK_PAGE_ACCESS_TOKEN in environment variables."
        
    has_image = image_path and os.path.exists(image_path)
    
    if has_image:
        # Step 1: Upload photo to /{page_id}/photos as published=false to obtain media_fbid
        photo_url = f"https://graph.facebook.com/v19.0/{page_id}/photos"
        try:
            with open(image_path, "rb") as img_file:
                files = {"source": img_file}
                data = {
                    "published": "false",
                    "access_token": page_token
                }
                print(f"[FACEBOOK] Step 1: Uploading media asset to page {page_id} (published=false)...", flush=True)
                response = requests.post(photo_url, files=files, data=data, timeout=30)
                
            res_json = response.json()
            if response.status_code != 200 or "id" not in res_json:
                # Fallback: if published=false fails, try legacy direct photo upload
                error_msg = res_json.get("error", {}).get("message", "Unknown Graph API photo upload error")
                print(f"[FACEBOOK WARNING] Step 1 published=false failed ({error_msg}). Retrying standard photo post...", flush=True)
                with open(image_path, "rb") as img_file:
                    files = {"source": img_file}
                    data = {"message": caption, "access_token": page_token}
                    resp_legacy = requests.post(photo_url, files=files, data=data, timeout=30)
                    res_leg_json = resp_legacy.json()
                    if resp_legacy.status_code == 200 and "id" in res_leg_json:
                        return True, f"Successfully published to Facebook Page via photo API! Post ID: {res_leg_json['id']}"
                    else:
                        leg_err = res_leg_json.get("error", {}).get("message", "Unknown error")
                        return False, f"Facebook API Error: {leg_err}"

            photo_id = res_json["id"]
            print(f"[FACEBOOK] Step 1 complete. Photo FBID: {photo_id}. Step 2: Creating Page Timeline Feed post...", flush=True)

            # Step 2: Publish Timeline Feed Story referencing attached media_fbid
            feed_url = f"https://graph.facebook.com/v19.0/{page_id}/feed"
            feed_data = {
                "message": caption,
                "attached_media": json.dumps([{"media_fbid": photo_id}]),
                "access_token": page_token
            }
            feed_resp = requests.post(feed_url, data=feed_data, timeout=30)
            feed_json = feed_resp.json()

            if feed_resp.status_code == 200 and "id" in feed_json:
                post_id = feed_json["id"]
                
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
                
                return True, f"Successfully published photo post directly to Facebook Page Feed! Post ID: {post_id}"
            else:
                feed_err = feed_json.get("error", {}).get("message", "Unknown Feed API error")
                return False, f"Facebook Feed API Error: {feed_err}"

        except Exception as e:
            return False, f"Failed to publish photo post to Facebook Feed: {e}"
    else:
        url = f"https://graph.facebook.com/v19.0/{page_id}/feed"
        try:
            data = {
                "message": caption,
                "access_token": page_token
            }
            print(f"[FACEBOOK] Publishing text update to page feed {page_id}...", flush=True)
            response = requests.post(url, data=data, timeout=30)
            
            res_json = response.json()
            if response.status_code == 200 and "id" in res_json:
                post_id = res_json["id"]
                return True, f"Successfully published text post to Facebook Page Feed! Post ID: {post_id}"
            else:
                error_msg = res_json.get("error", {}).get("message", "Unknown Graph API error")
                return False, f"Facebook API Error: {error_msg}"
        except Exception as e:
            return False, f"Failed to publish to Facebook Feed: {e}"


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


def generate_social_post_draft(custom_topic: str = None, language: str = "en") -> dict:
    """Scrape trends (or use custom topic), generate caption, image prompt, download FLUX backdrop, and render Pillow 4:5 mobile-friendly card in English or Hindi."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(current_dir, "temp")
    if os.path.exists(temp_dir):
        clean_old_temp_files(temp_dir)
        
    import random
    import datetime
    
    # Auto-detect language if specified in custom_topic
    if custom_topic and any(w in custom_topic.lower() for w in ["hindi", "हिंदी", "hinglish"]):
        language = "hi"
    
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
        print("[SOCIAL] Generating Multi-Service Catalog Poster (4:5)...", flush=True)
        catalog_topic = "complete services overview and brand introduction listing PF, Income Tax, GST, CSC and Compliance"
        caption, img_prompt, card_title, card_tips, category = generate_daily_post(catalog_topic, language=language)
        img_path = generate_catalog_poster()
    else:
        selected_topic = custom_topic if custom_topic else today_topic
        print(f"[SOCIAL] Generating Featured Single-Service Post for topic: '{selected_topic}' (lang: {language})...", flush=True)
        caption, img_prompt, card_title, card_tips, category = generate_daily_post(selected_topic, language=language)
        
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


def send_facebook_messenger_reply(sender_id: str, message_text: str) -> tuple[bool, str]:
    """Send a private reply to a Facebook Messenger user via Meta Graph API."""
    page_token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_token:
        return False, "Missing FACEBOOK_PAGE_ACCESS_TOKEN."
    
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={page_token}"
    payload = {
        "recipient": {"id": sender_id},
        "message": {"text": message_text}
    }
    try:
        res = requests.post(url, json=payload, timeout=15)
        if res.status_code == 200:
            return True, f"Sent Messenger DM to {sender_id} successfully."
        else:
            err = res.json().get("error", {}).get("message", res.text)
            return False, f"Messenger API Error: {err}"
    except Exception as e:
        return False, f"Messenger request failed: {e}"


def auto_refresh_facebook_token() -> str:
    """Attempts to exchange short-lived tokens for long-lived page token if APP_SECRET is configured in environment."""
    cur_token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")
    app_id = os.environ.get("FACEBOOK_APP_ID", "947606281427456")
    app_secret = os.environ.get("FACEBOOK_APP_SECRET", "")
    page_id = os.environ.get("FACEBOOK_PAGE_ID", "901875296346087")
    
    if cur_token and app_id and app_secret:
        try:
            ex_url = f"https://graph.facebook.com/v19.0/oauth/access_token?grant_type=fb_exchange_token&client_id={app_id}&client_secret={app_secret}&fb_exchange_token={cur_token}"
            r1 = requests.get(ex_url, timeout=10).json()
            long_user = r1.get("access_token")
            if long_user:
                p_url = f"https://graph.facebook.com/v19.0/{page_id}?fields=access_token&access_token={long_user}"
                r2 = requests.get(p_url, timeout=10).json()
                never_exp_token = r2.get("access_token")
                if never_exp_token:
                    os.environ["FACEBOOK_PAGE_ACCESS_TOKEN"] = never_exp_token
                    print(f"[FACEBOOK TOKEN AUTO-REFRESH] Exchanged token into Never-Expiring Page Token successfully!", flush=True)
                    return never_exp_token
        except Exception as e:
            print(f"[FACEBOOK TOKEN AUTO-REFRESH ERROR] {e}", flush=True)
    return cur_token


def send_facebook_comment_reply(comment_id: str, message_text: str) -> tuple[bool, str]:
    """
    Dual-Dispatch:
    1. Replies publicly to the comment on Facebook post so all users see prompt engagement.
    2. Drops a direct private Messenger DM to the user for dedicated client inquiry / onboarding.
    Returns True if either or both dispatches succeed.
    """
    page_token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_token:
        return False, "Missing FACEBOOK_PAGE_ACCESS_TOKEN."
    
    clean_id = comment_id.split("_")[-1] if "_" in comment_id else comment_id
    errors = []
    public_success = False
    dm_success = False
    
    # 1. Public Comment Reply
    for cid in list(dict.fromkeys([comment_id, clean_id])):
        url = f"https://graph.facebook.com/v19.0/{cid}/comments"
        data = {"message": message_text, "access_token": page_token}
        try:
            res = requests.post(url, data=data, timeout=15)
            if res.status_code == 200:
                print(f"[FACEBOOK COMMENT SUCCESS] Public comment reply posted to {cid}: {res.json()}", flush=True)
                public_success = True
                break
            else:
                errors.append(f"Public {cid}: {res.text}")
        except Exception as e:
            errors.append(f"Public Exception {cid}: {e}")

    # 2. Private Messenger DM Reply (recipient.comment_id with pages_messaging)
    for cid in list(dict.fromkeys([comment_id, clean_id])):
        url_dm = f"https://graph.facebook.com/v19.0/me/messages?access_token={page_token}"
        dm_payload = {
            "recipient": {"comment_id": cid},
            "message": {"text": message_text}
        }
        try:
            res_dm = requests.post(url_dm, json=dm_payload, timeout=15)
            if res_dm.status_code == 200:
                print(f"[FACEBOOK MESSENGER COMMENT REPLY SUCCESS] Sent Private DM for comment {cid}: {res_dm.json()}", flush=True)
                dm_success = True
                break
            else:
                err_json = res_dm.json().get("error", {})
                code = err_json.get("code")
                msg = err_json.get("message", "")
                if code == 10900 or "already replied" in msg.lower():
                    print(f"[FACEBOOK MESSENGER COMMENT REPLY SUCCESS] Comment {cid} was already replied to: {msg}", flush=True)
                    dm_success = True
                    break
                errors.append(f"Messenger DM {cid}: {res_dm.text}")
        except Exception as e:
            errors.append(f"Messenger DM Exception {cid}: {e}")
            
    # 3. Fallback to legacy private_replies endpoint if DM hasn't succeeded
    if not dm_success:
        for cid in list(dict.fromkeys([comment_id, clean_id])):
            priv_url = f"https://graph.facebook.com/v19.0/{cid}/private_replies"
            priv_data = {"message": message_text, "access_token": page_token}
            try:
                res_priv = requests.post(priv_url, data=priv_data, timeout=15)
                if res_priv.status_code == 200:
                    print(f"[FACEBOOK PRIVATE REPLY SUCCESS] Private reply sent for comment {cid}: {res_priv.json()}", flush=True)
                    dm_success = True
                    break
                else:
                    errors.append(f"Private {cid}: {res_priv.text}")
            except Exception as e:
                errors.append(f"Private Exception {cid}: {e}")

    if public_success and dm_success:
        return True, f"Successfully posted public comment reply AND sent private Messenger DM for {comment_id}."
    elif public_success:
        return True, f"Posted public comment reply for {comment_id} (DM: {' | '.join(errors)})."
    elif dm_success:
        return True, f"Sent private Messenger DM for {comment_id} (Public reply: {' | '.join(errors)})."
    else:
        return False, f"Could not dispatch comment reply for {comment_id}. Details: {' | '.join(errors)}"


def fetch_facebook_recent_comments(limit: int = 5) -> tuple[bool, str]:
    """Fetch recent comments across Facebook Page posts using Meta Graph API with full Devanagari/UTF-8 support."""
    page_id = os.environ.get("FACEBOOK_PAGE_ID")
    page_token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_id or not page_token:
        return False, "Facebook credentials (FACEBOOK_PAGE_ID / FACEBOOK_PAGE_ACCESS_TOKEN) are not configured."
    
    url = f"https://graph.facebook.com/v19.0/{page_id}/feed"
    params = {
        "fields": "id,message,created_time,from{id,name},comments{id,message,from{id,name},created_time}",
        "limit": limit,
        "access_token": page_token
    }
    try:
        resp = requests.get(url, params=params, timeout=12)
        resp.encoding = "utf-8"
        if resp.status_code == 200:
            data = resp.json()
            posts = data.get("data", [])
            summary_lines = []
            total_comments = 0
            for post in posts:
                post_id = post.get("id")
                post_msg = (post.get("message") or "Post")[:70]
                comments_data = post.get("comments", {}).get("data", [])
                if comments_data:
                    summary_lines.append(f"📌 **Post** `{post_id}` ('{post_msg}...'):")
                    for c in comments_data:
                        total_comments += 1
                        c_id = c.get("id")
                        from_obj = c.get("from")
                        if isinstance(from_obj, dict):
                            commenter = from_obj.get("name") or from_obj.get("id") or "Customer"
                        else:
                            commenter = "Customer"
                        msg = c.get("message", "")
                        created = c.get("created_time", "")[:10]
                        summary_lines.append(f"  • **{commenter}** on {created} (ID: `{c_id}`): \"{msg}\"")
            if total_comments == 0:
                return True, "Checked Facebook Page feed: No comments found on recent posts."
            return True, f"Found {total_comments} recent comments on Facebook Page:\n\n" + "\n".join(summary_lines)
        else:
            if "#10" in resp.text or "pages_read_engagement" in resp.text:
                return False, "Meta Graph API requires `pages_read_engagement` permission in Meta Developer Dashboard for feed reading. Note: Incoming customer post comments are automatically received in real-time via live 2-way Webhooks!"
            return False, f"Meta Graph API error ({resp.status_code}): {resp.text[:200]}"
    except Exception as e:
        return False, f"Failed to fetch Facebook comments: {e}"


def fetch_facebook_recent_posts(limit: int = 5) -> tuple[bool, str]:
    """Fetch recent published posts from Facebook Page timeline with full Devanagari/UTF-8 support."""
    page_id = os.environ.get("FACEBOOK_PAGE_ID")
    page_token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_id or not page_token:
        return False, "Facebook credentials are not configured."
    
    url = f"https://graph.facebook.com/v19.0/{page_id}/feed"
    params = {
        "fields": "id,message,created_time,full_picture,shares",
        "limit": limit,
        "access_token": page_token
    }
    try:
        resp = requests.get(url, params=params, timeout=12)
        resp.encoding = "utf-8"
        if resp.status_code == 200:
            posts = resp.json().get("data", [])
            if not posts:
                return True, "No recent posts found on Facebook Page timeline."
            summary_lines = []
            for idx, p in enumerate(posts, 1):
                p_id = p.get("id")
                msg = (p.get("message") or "Image/Graphic Post")[:80]
                created = p.get("created_time", "")[:10]
                summary_lines.append(f"{idx}. **Post ID** `{p_id}` ({created}): \"{msg}...\"")
            return True, f"Recent Published Posts on Facebook Page:\n\n" + "\n".join(summary_lines)
        else:
            return False, f"Meta Graph API error ({resp.status_code}): {resp.text[:200]}"
    except Exception as e:
        return False, f"Failed to fetch Facebook posts: {e}"


def process_facebook_webhook_event(payload: dict):
    """Process incoming Meta Webhook events (Messenger DMs & Post Comments) and auto-reply."""
    try:
        page_id = os.environ.get("FACEBOOK_PAGE_ID", "")
        print(f"[FACEBOOK WEBHOOK RAW PAYLOAD] {json.dumps(payload)}", flush=True)
        entries = payload.get("entry", [])
        for entry in entries:
            # 1. Handle Messenger DMs
            messaging = entry.get("messaging", [])
            for msg_event in messaging:
                sender_id = msg_event.get("sender", {}).get("id")
                message = msg_event.get("message", {})
                user_text = message.get("text")
                is_echo = message.get("is_echo", False)
                
                if sender_id and user_text and not is_echo and sender_id != page_id:
                    print(f"[FACEBOOK WEBHOOK] Incoming DM from {sender_id}: '{user_text}'", flush=True)
                    reply = generate_conversational_dm_response(str(sender_id), user_text)
                    
                    ok, msg = send_facebook_messenger_reply(sender_id, reply)
                    print(f"[FACEBOOK WEBHOOK DM REPLY] {msg}", flush=True)
                    if ok:
                        record_social_interaction("Facebook Messenger", f"User {sender_id}", sender_id, user_text, reply, "FB_DM")

            # 2. Handle Post Comments
            changes = entry.get("changes", [])
            for change in changes:
                field = change.get("field")
                value = change.get("value", {})
                item = value.get("item")
                verb = value.get("verb", "add")
                comment_id = value.get("comment_id") or value.get("id")
                comment_text = value.get("message")
                sender_id = value.get("from", {}).get("id")
                raw_name = value.get("from", {}).get("name", "Customer")
                sender_name = str(raw_name).strip() if raw_name else "Customer"
                
                # Filter out self-comments from Page itself
                if sender_id and str(sender_id) == str(page_id):
                    print(f"[FACEBOOK WEBHOOK] Skipping self-comment by Page Admin ({sender_name})", flush=True)
                    continue

                if (field == "feed" or item in ("comment", "post")) and verb in ("add", "created") and comment_id and comment_text:
                    try:
                        print(f"[FACEBOOK WEBHOOK] Incoming Comment from {sender_name} ({sender_id}) on comment {comment_id}: '{comment_text}'", flush=True)
                    except Exception:
                        pass
                    reply = generate_comment_reply(comment_text, sender_name)
                    
                    ok, msg = send_facebook_comment_reply(comment_id, reply)
                    print(f"[FACEBOOK WEBHOOK COMMENT REPLY] {msg}", flush=True)
                    if ok:
                        record_social_interaction("Facebook Comment", sender_name, sender_id, comment_text, reply, "FB_COMMENT", post_id=comment_id)

    except Exception as e:
        print(f"[FACEBOOK WEBHOOK ERROR] Exception in process_facebook_webhook_event: {e}", flush=True)


def generate_conversational_dm_response(sender_id: str, user_text: str) -> str:
    """
    Stateful CRM-Driven Conversational Funnel:
    1. Retrieves existing lead state & past interaction turns from CRM.
    2. Runs LLM intent & entity extraction (service, date_expr, time_expr, intent, phone).
    3. Runs Deterministic Business Validation (IST datetime, 11 AM - 6 PM, conflict check).
    4. Commits CRM transaction (APPOINTMENT_SCHEDULED + babu_followups) and sends Telegram alert.
    5. Retrieves selective K-slice (PF/ITR/GST facts) rather than full JSON dump.
    6. Generates tailored, truthful customer response in customer's language (Hindi/Hinglish/English).
    """
    try:
        try:
            from .crm_service import (
                get_lead_by_source_ref,
                get_selective_knowledge_slice,
                parse_ist_datetime,
                check_slot_availability,
                commit_crm_appointment,
                ingest_lead,
                update_lead_funnel_stage
            )
        except ImportError:
            from crm_service import (
                get_lead_by_source_ref,
                get_selective_knowledge_slice,
                parse_ist_datetime,
                check_slot_availability,
                commit_crm_appointment,
                ingest_lead,
                update_lead_funnel_stage
            )

        lead_data = get_lead_by_source_ref(str(sender_id))
        lead_id = lead_data["lead_id"] if lead_data else None
        existing_service = lead_data.get("service_category", "General") if lead_data else "General"
        existing_status = lead_data.get("status", "NEW") if lead_data else "NEW"
        
        # 1. First extract candidate entities with fast model and rule checking
        from crm_service import extract_lead_intent_and_service
        rule_extracted = extract_lead_intent_and_service(user_text)
        is_unsupported = rule_extracted.get("is_unsupported", False)
        
        extraction_sys = (
            "You are an intent and entity extractor for a tax, PF, and e-governance consultancy in Orai, India.\n"
            "Authorized services we offer: 'PF' | 'ITR' | 'GST' | 'General' (MSME Udyam, Life Certificate, Passport, Sevayojan, PAN).\n"
            "Services we DO NOT offer: 'Unsupported' (Aadhaar Card Correction / Update / Biometrics, Ration Card, Driving License, Voter Card Correction).\n"
            "Extract JSON with fields:\n"
            "- 'service': 'PF' | 'ITR' | 'GST' | 'General' | 'Unsupported' | 'Unknown'\n"
            "- 'date_expr': requested appointment date (e.g. 'kal', 'tomorrow', 'somwar', '22/08', or null)\n"
            "- 'time_expr': requested time slot (e.g. '2 baje', '2 pm', '14:00', '11:30 am', 'shaam 4 baje', or null)\n"
            "- 'intent': 'DISCOVERY' | 'INFO_REQUEST' | 'APPOINTMENT_REQUEST' | 'CONFIRM_APPOINTMENT' | 'DECLINE' | 'GENERAL'\n"
            "- 'phone': 10-digit mobile number if mentioned, else null\n"
            "Return valid JSON only."
        )
        
        entities = {}
        extract_models = [("groq", "openai/gpt-oss-20b"), ("groq", "openai/gpt-oss-120b"), ("gemini", "gemini-2.5-flash")]
        for prov, mod in extract_models:
            try:
                if prov == "groq" and os.environ.get("GROQ_API_KEY"):
                    llm = ChatGroq(model=mod, temperature=0.0)
                    raw = llm.invoke([SystemMessage(content=extraction_sys), HumanMessage(content=f"Customer message: {user_text}\nPrevious Service: {existing_service}")]).content
                    m = re.search(r'\{.*\}', raw, re.DOTALL)
                    if m:
                        entities = json.loads(m.group(0))
                        break
                elif prov == "gemini" and os.environ.get("GEMINI_API_KEY"):
                    from langchain_google_genai import ChatGoogleGenerativeAI
                    llm = ChatGoogleGenerativeAI(model=mod, temperature=0.0, google_api_key=os.environ.get("GEMINI_API_KEY"))
                    raw = llm.invoke([SystemMessage(content=extraction_sys), HumanMessage(content=f"Customer message: {user_text}\nPrevious Service: {existing_service}")]).content
                    m = re.search(r'\{.*\}', raw, re.DOTALL)
                    if m:
                        entities = json.loads(m.group(0))
                        break
            except Exception:
                continue

        extracted_service = entities.get("service") or rule_extracted.get("service_category") or "General"
        if is_unsupported or extracted_service == "Unsupported":
            service = "Unsupported"
        elif extracted_service in ("PF", "ITR", "GST"):
            service = extracted_service
        elif extracted_service in ("General", "CSC", "PAN", "MSME", "PASSPORT"):
            service = "General"
        else:
            service = existing_service if existing_service != "General" else "General"

        date_expr = entities.get("date_expr")
        time_expr = entities.get("time_expr")
        phone = entities.get("phone") or rule_extracted.get("contact_info")

        # Ingest/update lead with extracted info
        if not lead_id:
            ingest_res = ingest_lead(
                name=f"User {sender_id}",
                channel="Facebook Messenger",
                user_message=user_text,
                contact_info=phone,
                source_ref=str(sender_id)
            )
            lead_id = ingest_res.get("lead_id")
        elif phone:
            try:
                from .services import get_db_connection
            except ImportError:
                from services import get_db_connection
            conn, is_pg = get_db_connection()
            if conn:
                cur = conn.cursor()
                if is_pg:
                    cur.execute("UPDATE babu_leads SET contact_info = %s, updated_at = CURRENT_TIMESTAMP WHERE lead_id = %s", (phone, lead_id))
                else:
                    cur.execute("UPDATE babu_leads SET contact_info = ?, updated_at = CURRENT_TIMESTAMP WHERE lead_id = ?", (phone, lead_id))
                conn.commit()
                cur.close()
                conn.close()

        # 2. Deterministic Funnel & Appointment Handling
        booking_status_instruction = ""
        
        # Check for Identity / "Who are you" query
        is_identity_query = any(k in user_text.lower() for k in (
            "who are you", "who r u", "who is this", "tum kaun ho", "aap kaun ho",
            "kaun ho", "kisse baat", "bot ho", "human ho", "apna intro", "introduce yourself",
            "kya ho", "what are you", "whom am i talking"
        ))

        # Case A: Unsupported / Out-of-Scope Services (e.g. Aadhaar Correction)
        if service == "Unsupported":
            update_lead_funnel_stage(lead_id, "UNSUPPORTED_INQUIRY", f"Customer inquired for unsupported service: {user_text[:60]}")
            booking_status_instruction = (
                "CRITICAL POLICY INSTRUCTION:\n"
                "The customer is asking for an UNSUPPORTED service (such as Aadhaar Card Correction/Update, Ration Card, or Driving License).\n"
                "1. Politely inform them that Anshu Computer & Tax Consultancy DOES NOT provide Aadhaar Card correction / update services.\n"
                "2. Clearly present our 4 authorized service categories:\n"
                "   1️⃣ PF / EPFO (Advance Claim, Transfer, KYC, Joint Declaration, Settlement)\n"
                "   2️⃣ Income Tax Return (ITR-1, 2, 4 Filing, Tax Planning & Refund)\n"
                "   3️⃣ GST Services (Registration & GSTR-1/3B Monthly Filing)\n"
                "   4️⃣ Digital & E-Governance (MSME Udyam, Life Certificate / Jeevan Pramaan, Passport, Sevayojan, PAN Card)\n"
                "3. Ask which of these 4 services they need help with.\n"
                "4. STRICT INVARIANT: DO NOT schedule, propose, or confirm any appointment for unsupported services."
            )
        
        # Case B: Supported Service + Timing Request -> Schedule Appointment
        elif service in ("PF", "ITR", "GST", "General") and (date_expr or time_expr or entities.get("intent") in ("APPOINTMENT_REQUEST", "CONFIRM_APPOINTMENT")):
            dt_res = parse_ist_datetime(date_expr, time_expr)
            
            if not dt_res.get("valid"):
                if dt_res.get("reason") == "SUNDAY_CLOSED":
                    booking_status_instruction = "IMPORTANT: Inform the customer that our consultancy is CLOSED on Sundays. Politely invite them to choose any time between Monday and Saturday from 11:00 AM to 6:00 PM."
                    update_lead_funnel_stage(lead_id, "APPOINTMENT_PROPOSED", "Customer requested Sunday; offered Mon-Sat 11 AM - 6 PM")
                elif dt_res.get("reason") == "OUTSIDE_WORKING_HOURS":
                    booking_status_instruction = f"IMPORTANT: Inform the customer that {dt_res.get('time_str')} is outside office hours. Office timings are strictly 11:00 AM to 6:00 PM (Mon-Sat). Propose an appointment slot within 11 AM - 6 PM."
                    update_lead_funnel_stage(lead_id, "APPOINTMENT_PROPOSED", f"Customer requested {dt_res.get('time_str')}; offered 11 AM - 6 PM")
                else:
                    booking_status_instruction = "Politely ask the customer what date and time between 11:00 AM and 6:00 PM (Monday to Saturday) they would prefer to visit our Kaushal Market, Orai office."
            else:
                # Check slot conflict
                avail_ok, alt_slots = check_slot_availability(dt_res["date_str"], dt_res["time_str"])
                if not avail_ok:
                    alt_str = " or ".join(alt_slots) if alt_slots else "another time between 11 AM and 6 PM"
                    booking_status_instruction = f"IMPORTANT: The slot on {dt_res['display_date']} at {dt_res['display_time']} is already reserved. Truthfully inform them and propose alternate open slots: {alt_str}."
                    update_lead_funnel_stage(lead_id, "APPOINTMENT_PROPOSED", f"Slot {dt_res['time_str']} occupied; offered {alt_str}")
                else:
                    # Commit appointment deterministically
                    commit_res = commit_crm_appointment(
                        lead_id=lead_id,
                        date_str=dt_res["date_str"],
                        time_str=dt_res["time_str"],
                        purpose=f"{service} Consultation",
                        notes=f"Booked via Messenger DM"
                    )
                    if commit_res.get("status") == "SUCCESS":
                        booking_status_instruction = (
                            f"CRITICAL: Appointment has been successfully BOOKED and CONFIRMED in the system for {dt_res['display_date']} at {dt_res['display_time']}! "
                            f"Confirm the appointment clearly to the customer. Remind them to bring the required documents for {service} to our office at Kaushal Market, Rath Road, Orai."
                        )
                    elif commit_res.get("status") == "SLOT_CONFLICT":
                        alts = commit_res.get("alternatives", [])
                        alt_str = " or ".join(alts) if alts else "another time between 11 AM and 6 PM"
                        booking_status_instruction = f"IMPORTANT: The slot on {dt_res['display_date']} at {dt_res['display_time']} was just reserved a moment ago by another client. Truthfully inform them and propose alternate open slots: {alt_str}."
                        update_lead_funnel_stage(lead_id, "APPOINTMENT_PROPOSED", f"Concurrent slot collision on {dt_res['time_str']}; offered {alt_str}")
                    else:
                        booking_status_instruction = "Apologize and inform them there was a temporary system delay. Ask them to confirm if they can visit at that time or call/WhatsApp +91 7217646673."

        # Case C: Supported Service Inquiry (No timing yet) -> Document Checklist & Offer Booking
        elif service in ("PF", "ITR", "GST", "General") and service != "General" and not is_identity_query:
            update_lead_funnel_stage(lead_id, "SERVICE_IDENTIFIED", f"Customer identified service: {service}")
            booking_status_instruction = (
                f"The customer is inquiring about {service}. Provide the required document checklist for {service}. "
                "Ask what day and time (Monday to Saturday, 11:00 AM to 6:00 PM) they would like to visit our Kaushal Market, Rath Road, Orai office to schedule their consultation."
            )
        
        # Case D: Identity Query ("Who are you?") or General Discovery
        else:
            update_lead_funnel_stage(lead_id, "DISCOVERY", "Presented identity and service catalog")
            booking_status_instruction = (
                "Introduce yourself clearly: 'I am JARVIS, the AI-Powered Social Media Manager of Mr. Shubham Swarnkar (Consultant) at Anshu Computer & Tax Consultancy, Orai.'\n"
                "Explain what you can do (answer inquiries, explain document requirements, and book consultations with Mr. Shubham Swarnkar).\n"
                "Present our 4 core service categories:\n"
                "1️⃣ PF / EPFO (Advance Claim, Transfer, KYC, Joint Declaration, Settlement)\n"
                "2️⃣ Income Tax (ITR-1, 2, 4 Filing, Tax Planning & Refund)\n"
                "3️⃣ GST Services (Registration & GSTR-1/3B Monthly Filing)\n"
                "4️⃣ Digital & E-Governance (MSME Udyam, Life Certificate, Passport, Sevayojan, PAN Card)\n"
                "Ask how you can assist them today."
            )

        # 3. Retrieve Selective Knowledge Slice (ADR-091/092)
        k_slice = get_selective_knowledge_slice(service)

        # 4. Generate Final Natural Language Response
        sys_prompt = (
            "You are JARVIS, the official AI-Powered Social Media Manager of Mr. Shubham Swarnkar (Consultant) at 'Anshu Computer & Tax Consultancy', Kaushal Market, Rath Road, Orai, UP.\n"
            f"Verified Business Facts:\n{k_slice}\n\n"
            f"CRM Workflow Direction:\n{booking_status_instruction}\n\n"
            "Language & Communication Directives:\n"
            "- DEFAULT LANGUAGE: Use natural, polite, daily-life Hindi in Devanagari script (सरल और सहज दैनिक बोलचाल की हिंदी) as your primary communication language.\n"
            "- LANGUAGE SWITCH OPTION: Offer the customer the option to switch to English if they prefer (e.g. 'आप चाहें तो बातचीत के लिए English भी चुन सकते हैं।').\n"
            "- If the user specifically writes entirely in English, respond in fluent, courteous English.\n"
            "- Identity Directive: When asked who you are, introduce yourself: 'नमस्ते! मैं अंशु कंप्यूटर एंड टैक्स कंसल्टेंसी, उरई से मिस्टर शुभम स्वर्णकार (कंसल्टेंट) का AI सोशल मीडिया मैनेजर जार्विस (JARVIS) हूँ।'\n"
            "- Explain your role: Assisting clients with PF, ITR, GST, and MSME/Digital services, and scheduling consultations with Mr. Shubham Swarnkar.\n"
            "- Plain text only, NO markdown asterisks (*) or bold symbols."
        )

        reply = None
        for prov, mod in [("groq", "openai/gpt-oss-20b"), ("groq", "openai/gpt-oss-120b"), ("gemini", "gemini-2.5-flash")]:
            try:
                if prov == "groq" and os.environ.get("GROQ_API_KEY"):
                    llm = ChatGroq(model=mod, temperature=0.4)
                    res = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=f"Customer message: {user_text}")])
                    reply = res.content.strip().replace("*", "").replace("_", "")
                    break
                elif prov == "gemini" and os.environ.get("GEMINI_API_KEY"):
                    from langchain_google_genai import ChatGoogleGenerativeAI
                    llm = ChatGoogleGenerativeAI(model=mod, temperature=0.4, google_api_key=os.environ.get("GEMINI_API_KEY"))
                    res = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=f"Customer message: {user_text}")])
                    reply = res.content.strip().replace("*", "").replace("_", "")
                    break
            except Exception:
                continue

        if not reply:
            if service == "Unsupported":
                reply = (
                    "नमस्ते! अंशु कंप्यूटर एंड टैक्स कंसल्टेंसी में आधार कार्ड संशोधन (Aadhaar Card Correction/Update) की सुविधा उपलब्ध नहीं है। "
                    "हमारी मुख्य सेवाएं: 1) PF / EPFO क्लेम एवं KYC सुधार, 2) इनकम टैक्स (ITR) फाइलिंग, 3) GST सेवाएं, 4) MSME उद्यम व डिजिटल सेवाएं। "
                    "बताएं, इनमें से किस कार्य में आपकी सहायता कर सकते हैं? (कार्यालय: कौशल मार्केट, उरई | समय: सुबह 11 से शाम 6 बजे, सोम-शनि)। "
                    "You can also chat in English if you prefer."
                )
            elif is_identity_query:
                reply = (
                    "नमस्ते! मैं अंशु कंप्यूटर एंड टैक्स कंसल्टेंसी, कौशल मार्केट, उरई से मिस्टर शुभम स्वर्णकार (कंसल्टेंट) का AI सोशल मीडिया मैनेजर 'जार्विस' (JARVIS) हूँ। "
                    "मैं आपके प्रश्नों के उत्तर देने और मिस्टर शुभम स्वर्णकार जी से अपॉइंटमेंट बुक करने में सहायता करता हूँ: "
                    "1) PF / EPFO सेवाएं, 2) इनकम टैक्स (ITR) फाइलिंग, 3) GST सेवाएं, 4) MSME उद्यम व डिजिटल सेवाएं। "
                    "आज आपकी किस सेवा में सहायता कर सकता हूँ? (आप चाहें तो बातचीत के लिए English भी चुन सकते हैं।)"
                )
            elif service in ("PF", "ITR", "GST"):
                reply = (
                    f"नमस्ते! अंशु कंप्यूटर एंड टैक्स कंसल्टेंसी, उरई में {service} सेवा के लिए संपर्क करने हेतु धन्यवाद। "
                    "हमारा कार्यालय सोमवार से शनिवार सुबह 11:00 बजे से शाम 6:00 बजे तक कौशल मार्केट, राठ रोड, उरई में खुला है। "
                    "मिस्टर शुभम स्वर्णकार जी से परामर्श के लिए आप किस दिन और समय आना चाहेंगे? (You can also reply in English.)"
                )
            else:
                reply = (
                    "नमस्ते! मैं अंशु कंप्यूटर एंड टैक्स कंसल्टेंसी, उरई से मिस्टर शुभम स्वर्णकार जी का AI मैनेजर 'जार्विस' हूँ। "
                    "हमारी प्रमुख सेवाएं: 1) PF / EPFO सेवाएं, 2) Income Tax (ITR) फाइलिंग, 3) GST सेवाएं, 4) MSME उद्यम व डिजिटल सेवाएं। "
                    "बताएं, आपकी किस सेवा में सहायता करें? (आप चाहें तो English में भी बातचीत कर सकते हैं।)"
                )
        return reply
    except Exception as e:
        print(f"[DM RESPONSE ERROR] {e}", flush=True)
        return "नमस्ते! मैं अंशु कंप्यूटर एंड टैक्स कंसल्टेंसी, उरई से मिस्टर शुभम स्वर्णकार जी का AI मैनेजर 'जार्विस' हूँ। हम सोमवार से शनिवार सुबह 11:00 से शाम 6:00 बजे तक उपलब्ध हैं। बताएं, हम आपकी क्या मदद कर सकते हैं? (You can also chat in English.)"


def generate_comment_reply(comment_text: str, sender_name: str) -> str:
    """Generate grounded, polite comment reply + mention Messenger DM."""
    try:
        try:
            from .crm_service import extract_lead_intent_and_service, get_selective_knowledge_slice
        except ImportError:
            from crm_service import extract_lead_intent_and_service, get_selective_knowledge_slice

        extracted = extract_lead_intent_and_service(comment_text)
        service = extracted.get("service_category", "General")
        k_slice = get_selective_knowledge_slice(service)

        sys_prompt = (
            "You are JARVIS, the official AI-Powered Social Media Manager of Mr. Shubham Swarnkar (Consultant) at Anshu Computer & Tax Consultancy, Orai, replying publicly to a comment on a Facebook post.\n"
            f"Business Facts:\n{k_slice}\n\n"
            "Guidelines:\n"
            "- Default to simple, polite daily-life Hindi in Devanagari script (सरल और सहज बोलचाल की हिंदी) unless the user wrote entirely in English.\n"
            "- State office timing (सोमवार से शनिवार सुबह 11:00 बजे से शाम 6:00 बजे, कौशल मार्केट, राठ रोड, उरई).\n"
            "- Mention that we have also sent a private message to their Messenger inbox for direct guidance.\n"
            "- Plain text only, no asterisks (*)."
        )
        reply = None
        for prov, mod in [("groq", "openai/gpt-oss-20b"), ("groq", "openai/gpt-oss-120b"), ("gemini", "gemini-2.5-flash")]:
            try:
                if prov == "groq" and os.environ.get("GROQ_API_KEY"):
                    llm = ChatGroq(model=mod, temperature=0.4)
                    res = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=f"Comment from {sender_name}: {comment_text}")])
                    reply = res.content.strip().replace("*", "").replace("_", "")
                    break
                elif prov == "gemini" and os.environ.get("GEMINI_API_KEY"):
                    from langchain_google_genai import ChatGoogleGenerativeAI
                    llm = ChatGoogleGenerativeAI(model=mod, temperature=0.4, google_api_key=os.environ.get("GEMINI_API_KEY"))
                    res = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=f"Comment from {sender_name}: {comment_text}")])
                    reply = res.content.strip().replace("*", "").replace("_", "")
                    break
            except Exception:
                continue

        if not reply:
            reply = f"Namaste {sender_name}! Thank you for reaching out. Anshu Computer & Tax Consultancy is open 11:00 AM to 6:00 PM (Mon-Sat) at Kaushal Market, Orai. We have also sent you a private message on Messenger for direct assistance."

        return reply
    except Exception as e:
        print(f"[COMMENT REPLY ERROR] {e}", flush=True)
        return f"Namaste {sender_name}! Thank you for reaching out. We are open 11:00 AM to 6:00 PM (Mon-Sat) at Kaushal Market, Rath Road, Orai. Please check your Messenger inbox for direct assistance."

def record_social_interaction(channel: str, sender_name: str, sender_id: str, user_text: str, reply_text: str, interaction_type: str = "FB_COMMENT", post_id: str = ""):
    """
    Persist incoming social media interaction and AI response to:
    1. babu_k0_working_memory (Session working memory)
    2. execution_ledger (Swarm telemetry and activity tracking)
    3. babu_temporal_timeline (Chronological event timeline)
    4. Lead classification
    """
    import time
    from datetime import datetime
    try:
        from .services import get_db_connection
    except ImportError:
        from services import get_db_connection

    conn, is_pg = get_db_connection()
    if not conn:
        print("[SOCIAL MEMORY ERROR] Database connection unavailable to record social interaction.", flush=True)
        return

    try:
        cursor = conn.cursor()
        goal_id = f"SOC-{interaction_type}-{int(time.time())}"
        session_id = f"{channel.lower().replace(' ', '_')}_{sender_id}"
        clean_user_text = f"[{sender_name} via {channel}]: {user_text}"
        
        # 1. babu_k0_working_memory
        if is_pg:
            cursor.execute("""
                INSERT INTO babu_k0_working_memory (session_id, goal_id, user_query, response, status, failures, retrieved_records)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (session_id, goal_id, clean_user_text, reply_text, "SUCCESS", None, "K3 - Business Context, K1 - Social Rules"))
        else:
            cursor.execute("""
                INSERT INTO babu_k0_working_memory (session_id, goal_id, user_query, response, status, failures, retrieved_records)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (session_id, goal_id, clean_user_text, reply_text, "SUCCESS", None, "K3 - Business Context, K1 - Social Rules"))

        # 2. execution_ledger
        meta_json = json.dumps({
            "channel": channel,
            "sender_name": sender_name,
            "sender_id": sender_id,
            "user_query": user_text,
            "reply": reply_text,
            "post_id": post_id
        })
        if is_pg:
            cursor.execute("""
                INSERT INTO execution_ledger (session_id, goal_id, task_id, department, event_type, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (session_id, goal_id, f"T-{interaction_type}", "writing", f"{interaction_type}_REPLY", meta_json))
        else:
            cursor.execute("""
                INSERT INTO execution_ledger (session_id, goal_id, task_id, department, event_type, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (session_id, goal_id, f"T-{interaction_type}", "writing", f"{interaction_type}_REPLY", meta_json))

        # 3. babu_temporal_timeline
        summary_text = f"Replied to {sender_name} on {channel}: '{user_text[:60]}...'"
        cause_text = f"Incoming inquiry on {channel} from {sender_name}"
        effect_text = f"Dispatched AI guidance for appointment / tax consultancy"
        resolution_text = "Replied"
        
        # Check if prospect lead
        q_lower = user_text.lower()
        is_lead = any(k in q_lower for k in ("appointment", "book", "milna", "contact", "number", "call", "fee", "charge", "price", "itr", "gst", "pf", "address", "sir", "kab"))
        category = "PROSPECT_LEAD" if is_lead else "SOCIAL_ENGAGEMENT"
        
        if is_pg:
            cursor.execute("""
                INSERT INTO babu_temporal_timeline (event_category, summary, outcome, cause, effect, resolution, impact_score, confidence, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (category, summary_text, "SUCCESS", cause_text, effect_text, resolution_text, 1.0 if is_lead else 0.8, 0.95, meta_json))
        else:
            cursor.execute("""
                INSERT INTO babu_temporal_timeline (event_category, summary, outcome, cause, effect, resolution, impact_score, confidence, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (category, summary_text, "SUCCESS", cause_text, effect_text, resolution_text, 1.0 if is_lead else 0.8, 0.95, meta_json))

        conn.commit()
        cursor.close()
        conn.close()
        print(f"[SOCIAL MEMORY SUCCESS] Recorded {interaction_type} interaction with {sender_name} into K0, Ledger & Timeline.", flush=True)

        # 4. Ingest into Independent Business CRM Plane
        try:
            try:
                from .crm_service import ingest_lead
            except ImportError:
                from crm_service import ingest_lead
            ingest_lead(
                name=sender_name,
                channel=channel,
                user_message=user_text,
                assistant_reply=reply_text,
                source_ref=str(sender_id),
                notes=f"Source: {channel} (Ref: {post_id})"
            )
        except Exception as crm_err:
            print(f"[CRM INGEST ERROR] {crm_err}", flush=True)

    except Exception as err:
        print(f"[SOCIAL MEMORY ERROR] Failed to record interaction: {err}", flush=True)


  
