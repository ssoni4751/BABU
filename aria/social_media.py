import os
import sys
import json
import re
import urllib.request
import urllib.parse
import tempfile
import requests
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage

DAILY_POST_PROMPT = """You are ARIA's automated Social Media Manager. Your job is to write a highly engaging daily social media post for 'Anshu Computers Orai' (a computer services and digital assistance shop run by Shubham Swarnkar, nickname Anshu, in Kaushal Market, Orai, Jalaun, Uttar Pradesh).

Structure your output as a clean JSON object ONLY, with exactly two keys (no markdown code blocks like ```json):
{
  "caption": "An engaging, warm, yet professional post caption. Provide a practical daily tech tip, hardware care advice, internet security tip, or productivity advice. Use emojis and professional hashtags (e.g. #AnshuComputersOrai #OraiTech #DailyTechTip). Reference 'Anshu Computers Orai' naturally as the shop ready to help clients with these issues.",
  "image_prompt": "A highly detailed, modern, and visually stunning square graphic prompt for a text-to-image generator (FLUX model). The graphic should represent the tip. Specify clean, premium aesthetics, high contrast, vibrant harmonious colors, and a clean bold sans-serif text banner centered inside the image representing the core concept (e.g., 'SECURE YOUR WIFI' or 'BOOST PC SPEED' in crisp readable typography)."
}

Reply with raw JSON ONLY."""

def generate_daily_post() -> tuple[str, str]:
    """Use Gemini 2.5 Flash to generate a caption and matching graphic prompt."""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        raise ValueError("GEMINI_API_KEY is not configured in the environment.")
        
    print("[SOCIAL] Generating daily post caption and prompt via Gemini...", flush=True)
    
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=gemini_key, temperature=0.7)
    res = llm.invoke([SystemMessage(content=DAILY_POST_PROMPT), HumanMessage(content="Generate today's scheduled post.")])
    
    text = res.content.strip()
    
    # Extract JSON even if the model wrapped it in code blocks
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        data = json.loads(match.group())
    else:
        data = json.loads(text)
        
    caption = data.get("caption", "Boost your digital productivity today! Visit Anshu Computers Orai for all tech assistance.")
    image_prompt = data.get("image_prompt", "Sleek modern office desk with high-tech computer monitor showing text 'TECH TIPS' in clean typography, professional lighting, 4k resolution")
    
    return caption, image_prompt


def generate_flux_graphic(prompt: str) -> str:
    """Generate an image from prompt using Pollinations.ai FLUX model and return local file path."""
    try:
        encoded_prompt = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?model=flux&width=1080&height=1080&nologo=true"
        print(f"[FLUX] Requesting image for prompt: '{prompt}'...", flush=True)
        
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=45) as response:
            image_bytes = response.read()
            
        if not image_bytes:
            raise ValueError("Empty image response from Pollinations API")
            
        # Save to local workspace
        current_dir = os.path.dirname(os.path.abspath(__file__))
        temp_dir = os.path.join(os.path.dirname(current_dir), "temp")
        os.makedirs(temp_dir, exist_ok=True)
        
        filepath = os.path.join(temp_dir, "daily_post.jpg")
        with open(filepath, "wb") as f:
            f.write(image_bytes)
            
        print(f"[FLUX] Graphic saved successfully to: {filepath}", flush=True)
        return filepath
    except Exception as e:
        print(f"[FLUX ERROR] {e}", flush=True)
        raise e


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


def run_autonomous_social_post() -> tuple[bool, str, str, str]:
    """Unified wrapper that runs the entire generation and publishing flow."""
    try:
        caption, img_prompt = generate_daily_post()
        img_path = generate_flux_graphic(img_prompt)
        ok, msg = publish_to_facebook_page(img_path, caption)
        return ok, msg, caption, img_path
    except Exception as e:
        return False, f"Autonomous workflow failed: {e}", "", ""
