import os
import re
import uuid
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

# Import CRM and AI logic from Babu
try:
    from crm_service import get_or_create_lead, ingest_lead
    from public_bot import generate_public_ai_reply
except ImportError:
    from .crm_service import get_or_create_lead, ingest_lead
    from .public_bot import generate_public_ai_reply

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("web_api")

app = FastAPI(title="Pragya Web API", description="Babu Public Chat API")

# Allow requests from the Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Render frontend will hit this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str
    client_name: Optional[str] = "Customer"
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    reply: str
    session_id: str

@app.get("/")
def read_root():
    return {"status": "Pragya Web API is running!"}

@app.post("/api/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest):
    try:
        session_id = request.session_id if request.session_id else f"web_{uuid.uuid4().hex[:12]}"
        client_name = request.client_name or "Customer"
        text = request.message.strip()

        if not text:
            raise HTTPException(status_code=400, detail="Message cannot be empty")

        lead = get_or_create_lead(session_id, client_name, "PUBLIC_WEB")
        current_service = lead.get("service_category", "General")
        existing_phone = lead.get("contact_info", "")

        phone_match = re.search(r'\b(?:(?:\+91|0)?[6-9]\d{9})\b', text)
        extracted_phone = phone_match.group(0) if phone_match else None
        
        contact_frozen = bool(existing_phone and re.search(r'\b[6-9]\d{9}\b', str(existing_phone)))
        
        reply_text = ""
        
        if extracted_phone and not contact_frozen:
            from crm_service import freeze_lead_contact
            lead_id = lead["lead_id"]
            freeze_lead_contact(lead_id, extracted_phone)
            existing_phone = extracted_phone
            reply_text = f"धन्यवाद {client_name} जी! ✅ आपका मोबाइल नंबर सुरक्षित कर लिया गया है: {existing_phone}\nबताएं मैं आपकी कैसे सहायता कर सकती हूँ?"
        else:
            if any(k in text.lower() for k in ("aadhaar", "aadhar", "adhar", "uidai", "rashan", "ration", "driving license", "dl renewal")):
                reply_text = (
                    f"नमस्ते {client_name} जी! अंशु कंप्यूटर एंड टैक्स कंसल्टेंसी में आधार कार्ड संशोधन (Aadhaar Card Update), "
                    f"राशन कार्ड या ड्राइविंग लाइसेंस की सुविधा उपलब्ध नहीं है।\n\n"
                    f"हमारी मुख्य सेवाएं PF, Income Tax, GST, और MSME/पैन कार्ड हैं। बताएं, इनमें से किस कार्य में आपकी सहायता कर सकते हैं?"
                )
            else:
                reply_text = generate_public_ai_reply(text, client_name, current_service)
        
        ingest_lead(
            name=client_name, 
            channel="PUBLIC_WEB", 
            user_message=text, 
            assistant_reply=reply_text, 
            contact_info=existing_phone if existing_phone else None, 
            source_ref=session_id, 
            notes="Web Chat Interaction"
        )

        return ChatResponse(reply=reply_text, session_id=session_id)

    except Exception as e:
        logger.error(f"Error in chat endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while processing message.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web_api:app", host="0.0.0.0", port=8000, reload=False)
