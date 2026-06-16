import json
import os
import re
import sys
from datetime import datetime

# Force UTF-8 encoding for Windows standard streams to prevent emoji/unicode logging crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# Paths
LOG_FILE = r"C:\Users\LENOVO\.gemini\antigravity\brain\592b40b0-97b4-4126-ac4c-99fd0a65df0e\.system_generated\logs\transcript.jsonl"
OUTPUT_FILE = r"C:\Users\LENOVO\.gemini\antigravity\scratch\Babu\Babu_Session_Transcript.md"

print("="*60)
print("ARIA Session Transcript Generator & Uploader")
print("="*60)

if not os.path.exists(LOG_FILE):
    print("❌ Error: Transcript log file not found.")
    sys.exit(1)

dialogue = []
with open(LOG_FILE, "r", encoding="utf-8") as f:
    for line in f:
        try:
            data = json.loads(line)
            source = data.get("source")
            type_ = data.get("type")
            content = data.get("content", "")
            created_at = data.get("created_at", "")
            
            # Format time if available
            time_str = ""
            if created_at:
                try:
                    dt = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
                    time_str = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    time_str = created_at
            
            if source == "USER_EXPLICIT" and type_ == "USER_INPUT" and content:
                user_msg = content
                # Clean up <USER_REQUEST> tags
                match = re.search(r'<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>', content, re.DOTALL)
                if match:
                    user_msg = match.group(1)
                
                # Check if it contains an image URL (to make it look pretty in MD)
                if "github.com" in user_msg and ("/assets/" in user_msg or "tree/main" in user_msg or ".png" in user_msg):
                    # Keep it as is or handle it
                    pass
                dialogue.append(f"### 👤 User ({time_str})\n\n{user_msg.strip()}\n\n")
                
            elif source == "MODEL" and type_ == "PLANNER_RESPONSE" and content:
                # Clean up assistant message
                dialogue.append(f"### 🤖 Antigravity Assistant ({time_str})\n\n{content.strip()}\n\n")
        except Exception as e:
            continue

if not dialogue:
    print("❌ Error: No dialogue extracted from log file.")
    sys.exit(1)

# Write markdown file
header = f"""# ARIA & Antigravity - Pair Programming Session Transcript

**Date:** {datetime.now().strftime("%Y-%m-%d")}
**Session ID:** `592b40b0-97b4-4126-ac4c-99fd0a65df0e`
**Description:** Full pair programming transcript migrating the ARIA bot from Replit to Render, configuring direct Google Workspace APIs, implementing dynamic pathing, Google Contacts Syncing, and dynamic model switching with Gemini support.

---

"""

with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
    out.write(header)
    out.write("\n".join(dialogue))

print(f"✅ Local transcript file created: {OUTPUT_FILE}")

# ── Google Drive Upload ──
sys.path.append(r"C:\Users\LENOVO\.gemini\antigravity\scratch\Babu\babu")
try:
    from google_service import get_google_creds
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    print("🔑 Authenticating with Google Drive...")
    creds = get_google_creds()
    if not creds:
        print("❌ Error: Could not authenticate with Google Workspace.")
        sys.exit(1)
        
    drive_service = build("drive", "v3", credentials=creds)
    
    file_metadata = {
        'name': f'Babu_Session_Transcript_{datetime.now().strftime("%Y%m%d_%H%M")}.md',
        'mimeType': 'text/markdown'
    }
    media = MediaFileUpload(OUTPUT_FILE, mimetype='text/markdown', resumable=True)
    
    print("📤 Uploading transcript to Google Drive...")
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, name, webViewLink'
    ).execute()
    
    print("\n" + "="*60)
    print("✅ SUCCESS: Transcript uploaded successfully!")
    print(f"📄 File Name: {file.get('name')}")
    print(f"🔗 Google Drive Link: {file.get('webViewLink')}")
    print("="*60)

except Exception as e:
    print(f"\n❌ Error during Google Drive upload: {e}")
