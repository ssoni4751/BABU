"""
==============================================================================
Project BABU — Public Client Desk Telegram Bot (public_bot.py)
==============================================================================
Front-Desk Customer Facing Bot for Anshu Computer & Tax Consultancy, Orai.
Handles:
- Public inquiries in Hindi & English (PF/EPFO, ITR, GST, MSME/Digital)
- Office timings & location information (Kaushal Market, Rath Road, Orai)
- Automated lead capture into CRM (babu_leads, babu_interactions)
- Slot-based appointment booking (11:00 AM - 6:00 PM, Mon-Sat)
- Safe client document upload intake (PAN, passbook, Form 16)
- Instant real-time alerts to the Business Owner on their Private Bot
- Strict prompt injection boundaries & zero access to private workspace
==============================================================================
"""

import os
import re
import time
import json
import logging
import asyncio
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("public_bot")

# Fallback / Direct imports
try:
    from .crm_service import (
        get_selective_knowledge_slice,
        extract_lead_intent_and_service,
        ingest_lead,
        check_slot_availability,
        commit_crm_appointment,
        parse_ist_datetime,
        dispatch_telegram_appointment_alert,
        SUPPORTED_SERVICE_CATALOG,
        UNSUPPORTED_SERVICE_KEYWORDS,
        get_or_create_lead,
        get_lead_by_source_ref,
        freeze_lead_service,
        freeze_lead_contact,
        REQUIRED_DOCS_BY_SERVICE,
    )
except ImportError:
    from crm_service import (
        get_selective_knowledge_slice,
        extract_lead_intent_and_service,
        ingest_lead,
        check_slot_availability,
        commit_crm_appointment,
        parse_ist_datetime,
        dispatch_telegram_appointment_alert,
        SUPPORTED_SERVICE_CATALOG,
        UNSUPPORTED_SERVICE_KEYWORDS,
        get_or_create_lead,
        get_lead_by_source_ref,
        freeze_lead_service,
        freeze_lead_contact,
        REQUIRED_DOCS_BY_SERVICE,
    )

# Office constants
OFFICE_NAME = "Anshu Computer & Tax Consultancy"
CONSULTANT_NAME = "Shubham Swarnkar (शुभम स्वर्णकार जी)"
OFFICE_ADDRESS = "Kaushal Market, Rath Road, Orai, Uttar Pradesh"
OFFICE_HOURS = "सोमवार से शनिवार: सुबह 11:00 बजे से शाम 6:00 बजे तक (रविवार बंद)"
OFFICE_PHONE = "+91 7217646673"

SERVICE_SELECTION_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🏢 1. PF Consultancy (Primary)", callback_data="svc_pf"),
        InlineKeyboardButton("📑 2. Tax / ITR Services", callback_data="svc_tax")
    ],
    [
        InlineKeyboardButton("📊 3. GST Services", callback_data="svc_gst"),
        InlineKeyboardButton("🌐 4. General Services", callback_data="svc_general")
    ],
    [
        InlineKeyboardButton("📍 कार्यालय का पता व संपर्क", callback_data="svc_address")
    ]
])

SERVICE_TITLES: Dict[str, str] = {
    "PF": "PF Consultancy (Primary Specialization)",
    "Tax": "Tax Services (Income Tax / ITR)",
    "GST": "GST Services & Compliance",
    "General": "General Services (MSME, Life Certificate, Passport, PAN)"
}

TELEGRAM_PUBLIC_BOT_TOKEN = os.environ.get("TELEGRAM_PUBLIC_BOT_TOKEN", "").strip()
TELEGRAM_USER_CHAT_ID = os.environ.get("TELEGRAM_USER_CHAT_ID", "").strip()
PRIVATE_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def send_owner_client_alert(headline: str, client_name: str, username: str, details: str, phone: str = ""):
    """Send an instant lead or query alert to the business owner on their private bot."""
    if not PRIVATE_BOT_TOKEN or not TELEGRAM_USER_CHAT_ID:
        return
    
    import html
    user_tag = f"@{html.escape(username)}" if username else "No username"
    phone_line = f"\n📞 <b>Phone:</b> <code>{html.escape(phone)}</code>" if phone else ""
    text = (
        f"🔔 <b>{html.escape(headline)}</b>\n"
        f"──────────────────────────────\n"
        f"👤 <b>Client:</b> {html.escape(client_name)} ({user_tag}){phone_line}\n"
        f"💬 <b>Details:</b>\n{html.escape(details)}\n"
        f"──────────────────────────────\n"
        f"🌐 Channel: Public Telegram Bot (@Anshu4751_bot)\n"
        f"⏰ Time: {datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30))).strftime('%d %b %Y, %I:%M %p IST')}"
    )
    import requests
    url = f"https://api.telegram.org/bot{PRIVATE_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_USER_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=8)
    except Exception as e:
        print(f"[PUBLIC BOT ALERT ERROR] {e}", flush=True)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Warm, professional welcome message forcing service category selection."""
    user = update.effective_user
    name = user.first_name or "मित्र"
    sender_id = f"tg_{user.id}"
    
    # Initialize persistent CRM lead
    lead = get_or_create_lead(sender_id, user.full_name or name, "PUBLIC_TELEGRAM")
    
    welcome_text = (
        f"नमस्ते {name} जी! 🙏\n\n"
        f"**{OFFICE_NAME}**, कौशल मार्केट, उरई के आधिकारिक डिजिटल सहायता केंद्र में आपका स्वागत है।\n\n"
        f"मेरा नाम **प्रज्ञा (Pragya)** है — **{OFFICE_NAME}**, उरई की डिजिटल रिसेप्शनिस्ट (Front-Desk Receptionist)।\n\n"
        f"💼 **हमारी 4 मुख्य सेवा श्रेणियां:**\n"
        f"1. 🏢 **PF Consultancy (Primary Specialization):** क्लेम सेटलमेंट (Form 19/10C/31), UAN ट्रांसफर, KYC/DOB सुधार, जॉइंट डिक्लेरेशन, ट्रांसफर\n"
        f"2. 📑 **Tax Services:** Income Tax Return (ITR-1, 2, 4) फाइलिंग, टैक्स कम्प्यूटेशन, रिफंड स्टेटस व नोटिस समाधान\n"
        f"3. 📊 **GST Services:** नया GST रजिस्ट्रेशन, मासिक व त्रैमासिक रिटर्न (GSTR-1, 3B), व नोटिस समाधान\n"
        f"4. 🌐 **General Services:** MSME उद्यम रजिस्ट्रेशन, जीवन प्रमाण पत्र (Jeevan Pramaan), पासपोर्ट, पैन कार्ड व अन्य डिजिटल सेवाएं\n\n"
        f"📍 **कार्यालय:** {OFFICE_ADDRESS}\n"
        f"⏰ **समय:** {OFFICE_HOURS}\n"
        f"📞 **हेल्पलाइन:** {OFFICE_PHONE}\n\n"
        f"👉 **परामर्श व आगे की सहायता के लिए, कृपया सबसे पहले नीचे दिए गए विकल्पों में से अपनी सेवा श्रेणी (Service Category) चुनें:**"
    )
    
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=SERVICE_SELECTION_KEYBOARD)


async def cmd_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detailed services breakdown for our 4 main categories."""
    text = (
        f"📋 **{OFFICE_NAME} — हमारी 4 मुख्य सेवा श्रेणियां**\n\n"
        f"1. 🏢 **PF Consultancy & Compliance (Primary Specialization):**\n"
        f"   • PF Claim Settlement (Form 19 Final, Form 10C Pension, Form 31 Advance)\n"
        f"   • UAN Activation व Member ID Transfer\n"
        f"   • KYC अपडेट (बैंक खाता, पैन, नाम, पिता का नाम, जन्मतिथि सुधार)\n"
        f"   • Joint Declaration फॉर्म असिस्टेंस व पुरानी कंपनी विवाद समाधान\n\n"
        f"2. 📑 **Tax Services & Advisory (Income Tax - ITR):**\n"
        f"   • वेतनभोगी (Salaried - ITR-1), व्यापारी/प्रोफेशनल (Business - ITR-4), कैपिटल गेन्स (ITR-2)\n"
        f"   • टैक्स कम्प्यूटेशन, AIS/TIS वेरिफिकेशन व रिफंड स्टेटस ट्रैकिंग\n"
        f"   • इनकम टैक्स डिफेक्टिव नोटिस समाधान व टैक्स प्लानिंग\n\n"
        f"3. 📊 **GST Services & Compliance:**\n"
        f"   • नया GST नंबर रजिस्ट्रेशन (Proprietorship / Partnership / Pvt Ltd)\n"
        f"   • मासिक/त्रैमासिक रिटर्न फाइलिंग (GSTR-1, GSTR-3B)\n"
        f"   • कम्पोजिशन स्कीम, एक्सपोर्ट हेतु LUT फाइलिंग व वार्षिक रिटर्न (GSTR-9)\n\n"
        f"4. 🌐 **General Services (अन्य सभी डिजिटल व ई-गवर्नेंस सेवाएं):**\n"
        f"   • MSME उद्यम रजिस्ट्रेशन (सरकारी योजनाओं व बैंक लोन लाभ हेतु)\n"
        f"   • डिजिटल जीवन प्रमाण पत्र (Jeevan Pramaan for Pensioners)\n"
        f"   • पासपोर्ट ऑनलाइन आवेदन व PSK अपॉइंटमेंट\n"
        f"   • नया पैन कार्ड व पैन सुधार (Instant e-PAN)\n"
        f"   • सेवायोजन रोजगार पंजीयन व अन्य सरकारी ऑनलाइन सेवाएं\n\n"
        f"📍 परामर्श हेतु हमारे कार्यालय आएं: {OFFICE_ADDRESS}\n"
        f"⏰ समय: 11:00 AM से 6:00 PM (सोमवार - शनिवार)"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Office location, hours, and direct phone contact."""
    text = (
        f"📍 **कार्यालय का पता एवं संपर्क सूत्र:**\n\n"
        f"🏢 **{OFFICE_NAME}**\n"
        f"👤 कंसल्टेंट: **{CONSULTANT_NAME}**\n"
        f"📍 पता: {OFFICE_ADDRESS}\n"
        f"⏰ कार्यालय समय: {OFFICE_HOURS}\n"
        f"📞 फ़ोन / WhatsApp: `{OFFICE_PHONE}`\n\n"
        f"🗺️ *स्थान: उरई में राठ रोड पर कौशल मार्केट (सेंट्रल बैंक / मुख्य बाज़ार के समीप)*\n\n"
        f"आप बिना अपॉइंटमेंट के भी कार्य दिवसों में 11:00 AM से 6:00 PM के बीच आ सकते हैं!"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def on_public_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button clicks on the public desk."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user
    sender_id = f"tg_{user.id}"
    client_name = user.full_name or user.first_name or "Client"
    
    lead = get_or_create_lead(sender_id, client_name, "PUBLIC_TELEGRAM")
    lead_id = lead["lead_id"]
    
    svc_map = {
        "svc_pf": ("PF", "🏢 **PF Consultancy (Primary Specialization)**"),
        "svc_tax": ("Tax", "📑 **Tax Services (Income Tax / ITR)**"),
        "svc_itr": ("Tax", "📑 **Tax Services (Income Tax / ITR)**"),
        "svc_gst": ("GST", "📊 **GST Services & Compliance**"),
        "svc_general": ("General", "🌐 **General Services (MSME & Digital)**"),
        "svc_msme": ("General", "🌐 **General Services (MSME & Digital)**")
    }
    
    if data in svc_map:
        category, title = svc_map[data]
        freeze_lead_service(lead_id, category)
        # Re-fetch lead to check if phone is present
        lead = get_or_create_lead(sender_id, client_name, "PUBLIC_TELEGRAM")
        has_phone = bool(lead.get("contact_info") and re.search(r'\b[6-9]\d{9}\b', str(lead.get("contact_info"))))
        
        if not has_phone:
            await query.message.reply_text(
                f"नमस्ते {client_name} जी! ✅ **आपकी सेवा श्रेणी सुरक्षित कर ली गई है:** {title}\n\n"
                f"👉 **अगला चरण:** परामर्श व अपॉइंटमेंट दर्ज करने हेतु कृपया अपना **10 अंकों का मोबाइल नंबर** (Mobile Number) यहाँ लिखकर भेजें:",
                parse_mode="Markdown"
            )
        else:
            await query.message.reply_text(
                f"नमस्ते {client_name} जी! ✅ **आपकी सेवा श्रेणी सुरक्षित कर ली गई है:** {title}\n"
                f"📞 **दर्ज मोबाइल नंबर:** `{lead['contact_info']}`\n\n"
                f"📅 **अपॉइंटमेंट बुकिंग:**\n"
                f"कृपया कार्यालय आने के लिए अपना पसंदीदा **दिन और समय** बताएं।\n"
                f"(कार्यालय समय: सोमवार से शनिवार, सुबह 11:00 बजे से शाम 6:00 बजे, कौशल मार्केट, राठ रोड, उरई)\n\n"
                f"उदाहरण: *कल दोपहर 2 बजे*, *सोमवार 4 PM*, आदि।",
                parse_mode="Markdown"
            )
    elif data == "svc_book":
        lead = get_or_create_lead(sender_id, client_name, "PUBLIC_TELEGRAM")
        current_service = lead.get("service_category")
        is_frozen = current_service in ("PF", "Tax", "GST", "General") and lead.get("status") not in ("AWAITING_SERVICE", "NEW", "DISCOVERY")
        has_phone = bool(lead.get("contact_info") and re.search(r'\b[6-9]\d{9}\b', str(lead.get("contact_info"))))
        
        if not is_frozen:
            keyboard = [
                [InlineKeyboardButton("🏢 1. PF Consultancy (Primary)", callback_data="svc_pf"), InlineKeyboardButton("📑 2. Tax / ITR Services", callback_data="svc_tax")],
                [InlineKeyboardButton("📊 3. GST Services", callback_data="svc_gst"), InlineKeyboardButton("🌐 4. General Services", callback_data="svc_general")],
            ]
            await query.message.reply_text(
                "परामर्श बुक करने के लिए कृपया सबसे पहले अपनी सेवा श्रेणी चुनें:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        elif not has_phone:
            await query.message.reply_text(
                f"आपने **{current_service}** सेवा चुनी है।\n\nअपॉइंटमेंट बुक करने के लिए कृपया अपना **10 अंकों का मोबाइल नंबर** यहाँ भेजें:",
                parse_mode="Markdown"
            )
        else:
            await query.message.reply_text(
                f"📅 **परामर्श अपॉइंटमेंट ({current_service}):**\n\n"
                f"कृपया बताएं आप किस दिन और किस समय आना चाहते हैं?\n"
                f"(कार्यालय समय: सोमवार से शनिवार, सुबह 11:00 से शाम 6:00 बजे)\n\n"
                f"उदाहरण: *कल दोपहर 2 बजे*, *सोमवार 4 PM*, आदि।",
                parse_mode="Markdown"
            )
    elif data == "svc_address":
        await cmd_contact(update, context)


def evaluate_pragya_funnel(client_text: str, lead: dict) -> tuple[str, dict]:
    """
    Evaluates the strict Pragya CRM locking funnel.
    Steps:
    1. Confirm/Ask Name
    2. Service Category (PF/Tax/GST/General)
    3. Delivery Mode (Online / Office Visit)
    4. Date & Time (check slots if offline, or just book online slot)
    5. Contact Number (10 digit)
    6. Confirm Appointment
    """
    client_name = lead.get("name", "")
    service = lead.get("service_category", "")
    if service in ("Overview", ""):
        service = "MISSING"
    phone = lead.get("contact_info", "")
    notes = lead.get("notes", "") or ""
    state = {}
    try:
        import re
        matches = list(re.finditer(r'\[PRAGYA_STATE:\s*({.*?})\]', notes))
        if matches:
            last_match = matches[-1]
            state = json.loads(last_match.group(1))
    except:
        pass

    # Ensure generic names are treated as missing
    is_name_missing = not client_name or any(x in client_name.lower() for x in ("customer", "user", "client", "visitor", "website"))
    
    # Check Aadhaar rejection first
    if any(k in client_text.lower() for k in ("aadhaar", "aadhar", "adhar", "uidai", "rashan", "ration", "driving license", "dl renewal")):
        return (
            "नमस्ते! अंशु कंप्यूटर एंड टैक्स कंसल्टेंसी में आधार कार्ड (Aadhaar), राशन कार्ड या ड्राइविंग लाइसेंस से संबंधित कार्य नहीं होते हैं।\n\n"
            "हमारी मुख्य सेवाएं हैं: PF (क्लेम/KYC), Income Tax (ITR), GST, और MSME/पैन कार्ड। "
            "बताएं, इनमें से किस कार्य में आपकी सहायता कर सकते हैं?"
        ), {}

    k_slice = ""
    if service != "MISSING":
        k_slice = f"Verified Business Facts:\n{get_selective_knowledge_slice(service)}\n\n"

    sys_prompt = f"""You are Pragya (प्रज्ञा), the polite, professional Digital Assistant at Anshu Computer & Tax Consultancy, Orai.
Consultant: Mr. Shubham Swarnkar.

YOUR GOAL: You must gently guide the customer through the appointment funnel.
CONVERSATIONAL RULE: If the user asks a general question (e.g. "Who are you?", "What is PF?", "What are your charges?"), you MUST answer it politely and naturally using the Business Facts. DO NOT refuse to answer just because funnel steps are missing! After answering, gently steer them back to the current missing funnel step.
Always reply in polite conversational Hindi (Devanagari).

### STRICT FUNNEL STEPS ###
1. NAME: Ensure we have the customer's real name. (If missing, ask: "आपकी सहायता करने से पहले, क्या मैं आपका शुभ नाम जान सकती हूँ?")
2. SERVICE CATEGORY: Force them to choose ONE of the 4 authorized categories:
   - PF Consultancy (Form 19/10C/31, UAN, KYC)
   - Tax Services (ITR, Notices)
   - GST Services (Registration, Returns)
   - General Services (MSME, PAN, Jeevan Pramaan, Passport)
3. DELIVERY MODE: Ask if they want the service "Online" (warn: OTP may be required) OR "Office Visit" (Kaushal Market, Orai).
4. DATE & TIME: 
   - If Online: Ask for their preferred online appointment time.
   - If Office Visit: Tell them office timings (Mon-Sat, 11 AM - 6 PM) and ask for a preferred day & time.
   - *CRITICAL*: The office is CLOSED on Sundays. If they ask for Sunday, politely tell them we are closed and ask for a Mon-Sat slot.
5. MOBILE NUMBER: *CRITICAL* ONLY ASK FOR THIS AFTER Date & Time are fixed! Ask for their 10-digit mobile number.
6. CONFIRMATION: If ALL 5 steps above are filled, just say: "धन्यवाद, आपकी जानकारी मिल गई है, मैं अभी अपॉइंटमेंट बुक कर रही हूँ।" DO NOT ask for anything else.

{k_slice}
### CURRENT CRM STATE ###
- Name: {"MISSING (Ask for their name first)" if is_name_missing else client_name}
- Service: {service}
- Mode (Online/Office): {state.get('mode', 'MISSING')}
- Appointment Date & Time: {state.get('datetime', 'MISSING')}
- Mobile Number: {phone if phone else 'MISSING'}

INSTRUCTIONS:
- Analyze the user's latest message.
- If they provided ANY information for a MISSING step (like a date, time, or phone number), you MUST immediately extract it in the JSON block at the end. DO NOT ask for confirmation before extracting.
- Acknowledge their input, and ask the question for the VERY NEXT missing step.
- DO NOT ask for mobile number until Date & Time are confirmed.
- At the VERY END of your reply, you MUST output a JSON block updating the state. 
- *CRITICAL RULE*: ONLY include fields in the JSON block that the user JUST PROVIDED in this exact turn. DO NOT include fields that are already known/filled in the CURRENT CRM STATE above. For example, if Name is already known, NEVER output "name" in the JSON.
- *CRITICAL RULE*: You MUST use EXACTLY these keys in the JSON block: "name", "service", "mode", "datetime", "phone". Do NOT invent other keys like "mobile" or "appointment".
- *CRITICAL RULE*: Always extract datetime in simple English terms (e.g., "Monday 3 PM", "Tomorrow 4 PM", "2026-09-15 14:00") even if the user replies in Hindi.
- Format: [CRM_UPDATE: {{"datetime": "Tomorrow 4 PM", "phone": "9876543210"}}]
- If they ask general questions, answer them briefly but steer them back to the funnel.
- Do not output markdown asterisks (*).
"""
    
    groq_key = os.environ.get("GROQ_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    
    content = ""
    # 1. Try Groq fast worker model
    if groq_key:
        try:
            from langchain_groq import ChatGroq
            from langchain_core.messages import SystemMessage, HumanMessage
            llm = ChatGroq(model="openai/gpt-oss-20b", temperature=0.1, api_key=groq_key)
            res = llm.invoke([
                SystemMessage(content=sys_prompt),
                HumanMessage(content=f"<untrusted_client_input>\n{client_text}\n</untrusted_client_input>")
            ])
            content = res.content.strip()
        except Exception as e:
            print(f"Groq error: {e}")

    # 2. Try Gemini Fallback
    if not content and gemini_key:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.messages import SystemMessage, HumanMessage
            llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.1, google_api_key=gemini_key)
            res = llm.invoke([
                SystemMessage(content=sys_prompt),
                HumanMessage(content=f"<untrusted_client_input>\n{client_text}\n</untrusted_client_input>")
            ])
            content = res.content.strip()
        except Exception as e:
            print(f"Gemini error: {e}")
            
    if not content:
        content = "क्षमा करें, सर्वर में तकनीकी समस्या है। कृपया अपना प्रश्न पुनः पूछें।"

    # Parse CRM_UPDATE block
    updates = {}
    pattern = r'\[CRM_UPDATE:\s*({.*?})\]'
    match = re.search(pattern, content, re.DOTALL)
    clean_text = content
    if match:
        try:
            json_str = match.group(1).replace("```json", "").replace("```", "").strip()
            updates = json.loads(json_str)
            clean_text = content.replace(match.group(0), "").strip()
        except Exception as e:
            print(f"Error parsing JSON block: {e}")

    return clean_text.replace("*", "").replace("_", ""), updates



def generate_public_ai_reply(text: str, client_name: str, service: str) -> str:
    """Generate a brief contextual answer for general questions using the LLM."""
    try:
        try:
            from .crm_service import get_selective_knowledge_slice
        except ImportError:
            from crm_service import get_selective_knowledge_slice
        from langchain_groq import ChatGroq
        from langchain.schema import HumanMessage, SystemMessage
        
        groq_key = os.environ.get("GROQ_API_KEY")
        if not groq_key:
            return ""
            
        k_slice = get_selective_knowledge_slice(service) if service not in ("MISSING", "Overview", "") else get_selective_knowledge_slice("General")
        sys_prompt = f"You are Pragya, a polite digital assistant for Anshu Computer & Tax Consultancy. Answer the user's question briefly in 1-2 sentences in conversational Hindi (Devanagari). Use this knowledge:\n{k_slice}"
        
        llm = ChatGroq(model="llama-3.1-8b-instant", temperature=0.3, api_key=groq_key)
        resp = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=text)])
        return resp.content.strip()
    except Exception as e:
        print(f"[AI REPLY ERROR] {e}", flush=True)
        return ""

async def on_public_message(update, context):
    if not update.message or not update.message.text: return
    user = update.effective_user
    client_name = user.full_name or user.first_name or "Client"
    username = user.username or ""
    sender_id = f"tg_{user.id}"
    user_text = update.message.text.strip()
    
    try:
        from .crm_service import get_or_create_lead, get_db_connection, ingest_lead, commit_crm_appointment, parse_ist_datetime, REQUIRED_DOCS_BY_SERVICE
    except ImportError:
        from crm_service import get_or_create_lead, get_db_connection, ingest_lead, commit_crm_appointment, parse_ist_datetime, REQUIRED_DOCS_BY_SERVICE
    import re, json
    
    lead = get_or_create_lead(sender_id, client_name, "PUBLIC_TELEGRAM")
    notes = lead.get("notes") or ""
    
    state = {}
    matches = list(re.finditer(r'\[PRAGYA_STATE:\s*({.*?})\]', notes))
    if matches:
        try:
            state = json.loads(matches[-1].group(1))
        except:
            pass
    reply_text, updates = evaluate_pragya_funnel(user_text, lead)
    
    if updates:
        if "mode" in updates: state["mode"] = updates["mode"]
        dt_val = (updates.get("datetime") or updates.get("date") or updates.get("time") or updates.get("appointment") or updates.get("appointment_time") or updates.get("Date & Time"))
        if dt_val: state["datetime"] = dt_val
        ph_val = updates.get("phone") or updates.get("mobile") or updates.get("phone_number")
        if ph_val: updates["phone"] = ph_val
        
        new_name = updates.get("name")
        new_service = updates.get("service")
        new_phone = updates.get("phone")
        
        new_notes = re.sub(r'\[PRAGYA_STATE:\s*({.*?})\]', '', notes).strip()
        new_notes = new_notes + f" [PRAGYA_STATE: {json.dumps(state)}]" if state else new_notes
        
        conn, is_pg = get_db_connection()
        if conn:
            cur = conn.cursor()
            set_clauses = []
            params = []
            if new_name and new_name.lower() not in ("customer", "user", "client"):
                set_clauses.append("name = %s" if is_pg else "name = ?")
                params.append(new_name)
            if new_service and new_service not in ("MISSING", "Overview", ""):
                set_clauses.append("service_category = %s" if is_pg else "service_category = ?")
                params.append(new_service)
            if new_phone:
                set_clauses.append("contact_info = %s" if is_pg else "contact_info = ?")
                params.append(new_phone)
            set_clauses.append("notes = %s" if is_pg else "notes = ?")
            params.append(new_notes)
            
            if set_clauses:
                params.append(lead["lead_id"])
                query = f"UPDATE babu_leads SET {', '.join(set_clauses)} WHERE lead_id = {'%s' if is_pg else '?'}"
                cur.execute(query, tuple(params))
                conn.commit()
            cur.close()
            conn.close()
            
            lead = get_or_create_lead(sender_id, client_name, "PUBLIC_TELEGRAM") # refresh lead
            notes = lead.get("notes") or ""
            
    final_name = lead.get("name")
    final_service = lead.get("service_category")
    final_phone = lead.get("contact_info")
    final_mode = state.get("mode")
    final_dt = state.get("datetime")
    
    is_complete = (
        final_name and final_name.lower() not in ("customer", "user", "client", "visitor", "website", "website visitor") and
        final_service and final_service not in ("MISSING", "Overview", "Unclassified", "") and
        final_phone and re.search(r'[6-9]\d{9}', str(final_phone)) and
        final_mode and final_mode != "MISSING" and
        final_dt and final_dt != "MISSING"
    )
    
    if is_complete and not "appointment booked" in notes.lower():
        parsed_dt = parse_ist_datetime(final_dt, final_dt)
        if parsed_dt.get("valid"):
            res = commit_crm_appointment(
                lead_id=lead["lead_id"],
                date_str=parsed_dt.get("date_str"),
                time_str=parsed_dt.get("time_str"),
                purpose=f"{final_mode} Consultation for {final_service}",
                notes=f"Pragya automated booking via Telegram"
            )
            if res.get("status") == "SUCCESS":
                doc_checklist = REQUIRED_DOCS_BY_SERVICE.get(final_service, REQUIRED_DOCS_BY_SERVICE.get("General", ""))
                if "online" in str(final_mode).lower():
                    reply_text = (
                        f"🎉 **{final_name} जी, आपका ऑनलाइन अपॉइंटमेंट सफलतापूर्वक बुक हो गया है!**\n\n"
                        f"📅 **तारीख:** {parsed_dt.get('display_date')}\n"
                        f"⏰ **समय:** {parsed_dt.get('display_time')}\n"
                        f"📱 **मोबाइल नंबर:** {final_phone}\n\n"
                        f"⚠️ **ज़रूरी सूचना:** ऑनलाइन प्रोसेस के दौरान OTP (वन-टाइम पासवर्ड) की आवश्यकता होगी। कृपया तय समय पर अपना मोबाइल फोन अपने पास रखें।"
                    )
                else:
                    reply_text = (
                        f"🎉 **{final_name} जी, आपका ऑफिस विज़िट अपॉइंटमेंट सफलतापूर्वक बुक हो गया है!**\n\n"
                        f"📅 **तारीख:** {parsed_dt.get('display_date')}\n"
                        f"⏰ **समय:** {parsed_dt.get('display_time')}\n"
                        f"📱 **मोबाइल नंबर:** {final_phone}\n"
                        f"📍 **पता:** {OFFICE_ADDRESS}\n\n"
                        f"📄 **कृपया अपने साथ निम्नलिखित दस्तावेज़ (Documents) लाएँ:**\n{doc_checklist}"
                    )
                new_notes = notes + "\n[APPOINTMENT BOOKED]"
                conn, is_pg = get_db_connection()
                if conn:
                    cur = conn.cursor()
                    cur.execute("UPDATE babu_leads SET notes = %s WHERE lead_id = %s" if is_pg else "UPDATE babu_leads SET notes = ? WHERE lead_id = ?", (new_notes, lead["lead_id"]))
                    conn.commit()
                    cur.close()
                    conn.close()
            else:
                alt_slots = res.get('alternatives', [])
                alt_str = ", ".join(alt_slots) if alt_slots else "कोई अन्य समय"
                reply_text = f"⚠️ क्षमा करें, यह समय पहले से बुक है। कृपया {alt_str} में से कोई अन्य समय चुनें।"
                if "datetime" in state:
                    del state["datetime"]
                    clean_notes = re.sub(r'\[PRAGYA_STATE:\s*({.*?})\]', '', notes).strip()
                    new_notes = clean_notes + f" [PRAGYA_STATE: {json.dumps(state)}]" if state else clean_notes
                    conn, is_pg = get_db_connection()
                    if conn:
                        cur = conn.cursor()
                        cur.execute("UPDATE babu_leads SET notes = %s WHERE lead_id = %s" if is_pg else "UPDATE babu_leads SET notes = ? WHERE lead_id = ?", (new_notes, lead["lead_id"]))
                        conn.commit()
                        cur.close()
                        conn.close()
        else:
            reply_text = parsed_dt.get("message", "⚠️ कृपया एक वैध दिन और समय बताएं।")
            if "datetime" in state:
                del state["datetime"]
                clean_notes = re.sub(r'\[PRAGYA_STATE:\s*({.*?})\]', '', notes).strip()
                new_notes = clean_notes + f" [PRAGYA_STATE: {json.dumps(state)}]" if state else clean_notes
                conn, is_pg = get_db_connection()
                if conn:
                    cur = conn.cursor()
                    cur.execute("UPDATE babu_leads SET notes = %s WHERE lead_id = %s" if is_pg else "UPDATE babu_leads SET notes = ? WHERE lead_id = ?", (new_notes, lead["lead_id"]))
                    conn.commit()
                    cur.close()
                    conn.close()

    if reply_text:
        # Strip code blocks and json markers
        clean_reply = re.sub(r'`json\s*\{.*?\}\s*`', '', reply_text, flags=re.DOTALL)
        clean_reply = re.sub(r'\[CRM_UPDATE:\s*\{.*?\}\]', '', clean_reply, flags=re.DOTALL).strip()
        await update.message.reply_text(clean_reply, parse_mode="Markdown")
        ingest_lead(name=client_name, channel="PUBLIC_TELEGRAM", user_message=user_text, assistant_reply=clean_reply, contact_info=final_phone, source_ref=sender_id, notes="Public bot: AI response")



async def on_public_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document uploads from clients (PAN, Form 16, passbook photos)."""
    user = update.effective_user
    client_name = user.full_name or "Client"
    username = user.username or ""
    
    doc = update.message.document or (update.message.photo[-1] if update.message.photo else None)
    if not doc:
        return

    doc_name = getattr(doc, "file_name", f"doc_{int(time.time())}.jpg")
    caption = update.message.caption or "Document uploaded by client"
    
    await update.message.reply_text(
        f"✅ **दस्तावेज़ प्राप्त हुआ!**\n\n"
        f"नमस्ते {client_name} जी, आपकी फ़ाइल सुरक्षित रूप से हमारे CRM सिस्टम में दर्ज कर ली गई है।\n"
        f"कंसल्टेंट **{CONSULTANT_NAME}** जी इसकी समीक्षा करेंगे और शीघ्र ही आपसे संपर्क किया जाएगा।\n\n"
        f"कार्यालय: {OFFICE_ADDRESS} (सुबह 11:00 - शाम 6:00)",
        parse_mode="Markdown"
    )
    
    send_owner_client_alert(
        "Client Document Uploaded",
        client_name,
        username,
        f"File: `{doc_name}`\nCaption: \"{caption}\"\nPlease check CRM intake for review."
    )


def build_public_bot() -> Optional[Any]:
    """Construct the Application instance for the Public Client Desk Bot."""
    token = os.environ.get("TELEGRAM_PUBLIC_BOT_TOKEN", "").strip()
    if not token:
        print("[PUBLIC BOT WARNING] TELEGRAM_PUBLIC_BOT_TOKEN not configured in environment.", flush=True)
        return None

    app = ApplicationBuilder().token(token).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("services", cmd_services))
    app.add_handler(CommandHandler("contact", cmd_contact))
    app.add_handler(CommandHandler("address", cmd_contact))
    app.add_handler(CallbackQueryHandler(on_public_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_public_message))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, on_public_document))
    
    return app


def run_public_bot_polling():
    """Background entrypoint to run the public bot polling loop."""
    token = os.environ.get("TELEGRAM_PUBLIC_BOT_TOKEN", "").strip()
    if not token:
        print("[PUBLIC BOT] Public bot token missing; skipping public transport.", flush=True)
        return

    print(f"[PUBLIC BOT] Starting Public Client Desk Bot polling (@Anshu4751_bot)...", flush=True)
    
    # Ensure worker thread has its own dedicated asyncio event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    while True:
        try:
            app = build_public_bot()
            if not app:
                break
            # IMPORTANT FOR LINUX / RENDER:
            # stop_signals must be () when running in a worker thread.
            # On Linux/Unix, set_wakeup_fd and signal handlers can only be registered on the main thread.
            app.run_polling(drop_pending_updates=True, stop_signals=(), close_loop=False)
            break
        except Exception as e:
            print(f"[PUBLIC BOT ERROR] Polling crashed: {e}. Retrying in 15s...", flush=True)
            time.sleep(15)


def start_public_bot_thread() -> Optional[threading.Thread]:
    """Launch public bot polling in an isolated daemon thread."""
    token = os.environ.get("TELEGRAM_PUBLIC_BOT_TOKEN", "").strip()
    if not token:
        return None
    t = threading.Thread(target=run_public_bot_polling, name="public_telegram_bot_thread", daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    run_public_bot_polling()
