import os
import sys
import json
import base64
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Unified scopes for ARIA Google Workspace actions
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",  # To search and create files dynamically
    "https://www.googleapis.com/auth/tasks",        # To create and manage tasks
    "https://www.googleapis.com/auth/photoslibrary.readonly",  # To read Google Photos
    "https://www.googleapis.com/auth/contacts.readonly"       # To read Google Contacts
]

# Root paths for credentials
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(CURRENT_DIR, "credentials.json")):
    BASE_DIR = CURRENT_DIR
else:
    BASE_DIR = os.path.dirname(CURRENT_DIR)

CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")

def get_google_creds() -> Credentials:
    """Load, refresh, or request user OAuth 2.0 credentials."""
    creds = None
    # 1. Try to load existing token
    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
            # Re-authorize if any required scope is missing from the stored token
            if not creds.scopes or not all(scope in creds.scopes for scope in SCOPES):
                print("[GOOGLE AUTH] Stored token has missing scopes. Re-authorization required.", flush=True)
                creds = None
        except Exception as e:
            print(f"[GOOGLE AUTH] Failed to load token.json: {e}", flush=True)

    # 2. If token is invalid/expired, refresh or prompt for login
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                print("[GOOGLE AUTH] Refreshing expired token...", flush=True)
                creds.refresh(Request())
                with open(TOKEN_PATH, "w") as token_file:
                    token_file.write(creds.to_json())
                print("[GOOGLE AUTH] Token refreshed successfully.", flush=True)
                return creds
            except Exception as e:
                print(f"[GOOGLE AUTH] Token refresh failed: {e}", flush=True)
                if "deleted_client" in str(e).lower():
                    print("[GOOGLE AUTH] ERROR: The OAuth client has been deleted. Google Workspace actions will fail. Please update credentials.json and token.json.", flush=True)
                    return None

        # 3. If refresh failed or token doesn't exist, we need credentials.json
        if not os.path.exists(CREDENTIALS_PATH):
            print("[GOOGLE AUTH] ERROR: credentials.json not found in root. Google actions will fail.", flush=True)
            return None

        # Check if environment is non-interactive to prevent hanging on headless servers
        is_interactive = False
        try:
            if sys.stdin and sys.stdin.isatty():
                is_interactive = True
        except Exception:
            pass

        if os.environ.get("RENDER") or os.environ.get("CI") or not is_interactive:
            print("[GOOGLE AUTH] Non-interactive/headless environment detected. Skipping local server authorization flow to prevent hanging.", flush=True)
            return None

        try:
            print("[GOOGLE AUTH] Initiating OAuth2 flow. Please authorize via the browser tab...", flush=True)
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            # Starts local server to receive auth code, automatically opens browser
            creds = flow.run_local_server(port=0, authorization_prompt_message="Open this link to authorize ARIA:")
            # Save token for next run
            with open(TOKEN_PATH, "w") as token_file:
                token_file.write(creds.to_json())
            print("[GOOGLE AUTH] Authentication successful! token.json saved.", flush=True)
        except Exception as e:
            print(f"[GOOGLE AUTH] Authentication failed: {e}", flush=True)
            return None

    return creds

def is_google_configured() -> bool:
    """Check if Google credentials are ready to be used or configured."""
    return os.path.exists(TOKEN_PATH) or os.path.exists(CREDENTIALS_PATH)


def validate_google_token_health(bot_token: str = None, chat_id: int = None) -> bool:
    """Check if the Google OAuth token is active and valid, sending a Telegram warning on failure."""
    try:
        creds = get_google_creds()
        if creds and creds.valid:
            return True
    except Exception as e:
        print(f"[TOKEN WATCHDOG ERROR] OAuth token is invalid or expired: {e}", flush=True)
        
    tg_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat = chat_id or os.environ.get("TELEGRAM_USER_CHAT_ID")
    
    if tg_token and tg_chat:
        try:
            import urllib.request
            import urllib.parse
            message = (
                "⚠️ *ARIA Google Workspace Alert!*\n\n"
                "Your Google Workspace OAuth Token has expired or is invalid, and could not be auto-refreshed.\n\n"
                "💡 *Action Required*:\n"
                "Please run `.venv\\Scripts\\python authenticate.py` on your host machine to re-authorize Google Workspace services!"
            )
            encoded_msg = urllib.parse.quote(message)
            url = f"https://api.telegram.org/bot{tg_token}/sendMessage?chat_id={tg_chat}&text={encoded_msg}&parse_mode=Markdown"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                response.read()
            print("[TOKEN WATCHDOG] Proactive alert sent to Telegram chat.", flush=True)
        except Exception as err:
            print(f"[TOKEN WATCHDOG ERROR] Failed to send Telegram alert: {err}", flush=True)
            
    return False


# ── Action Helper functions ──────────────────────────────────────────────────

def send_gmail(to: str, subject: str, body: str) -> tuple[bool, str]:
    """Send an email on behalf of the user using the Gmail API."""
    creds = get_google_creds()
    if not creds:
        return False, "Google Workspace authentication not configured."

    try:
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject

        # Encode mime message in urlsafe base64
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        send_body = {"raw": raw_message}

        print(f"[GMAIL] Sending email to {to}...", flush=True)
        service.users().messages().send(userId="me", body=send_body).execute()
        return True, f"📧 Email sent to {to} successfully."
    except Exception as e:
        print(f"[GMAIL ERROR] {e}", flush=True)
        return False, f"Failed to send email: {e}"


def create_calendar_event(title: str, date: str, time: str, duration: str = "1 hour", description: str = "") -> tuple[bool, str]:
    """Create an event on the user's main Google Calendar."""
    creds = get_google_creds()
    if not creds:
        return False, "Google Workspace authentication not configured."

    try:
        from googleapiclient.discovery import build
        service = build("calendar", "v3", credentials=creds)

        # 1. Parse date and time to produce start ISO string
        # Default start date is today if parsing fails
        start_dt = datetime.now()
        
        # Clean inputs
        date_str = str(date).strip()
        time_str = str(time).strip()

        # Combine date & time
        datetime_candidate = f"{date_str} {time_str}"
        formats = [
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %I:%M %p",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y %I:%M %p",
            "%B %d, %Y %I:%M %p",
            "%B %d, %Y %H:%M",
            "%Y-%m-%dT%H:%M:%S"
        ]

        parsed = False
        for fmt in formats:
            try:
                start_dt = datetime.strptime(datetime_candidate, fmt)
                parsed = True
                break
            except ValueError:
                continue

        if not parsed:
            # Fallback to date only, or time only, or today
            try:
                start_dt = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                pass

        # 2. Parse duration to get end datetime
        # Default duration is 1 hour
        dur_mins = 60
        dur_str = str(duration).lower()
        if "hour" in dur_str:
            try:
                # E.g. "1 hour", "2 hours" -> extract digits
                hours = int("".join(filter(str.isdigit, dur_str)))
                dur_mins = hours * 60
            except ValueError:
                dur_mins = 60
        elif "minute" in dur_str or "min" in dur_str:
            try:
                dur_mins = int("".join(filter(str.isdigit, dur_str)))
            except ValueError:
                dur_mins = 30
        else:
            try:
                # Just a raw number is treated as minutes
                dur_mins = int("".join(filter(str.isdigit, dur_str)))
            except ValueError:
                dur_mins = 60

        end_dt = start_dt + timedelta(minutes=dur_mins)

        # 3. Build Google Calendar Event resource
        event = {
            "summary": title,
            "description": description or "Created by ARIA Multi-Agent AI Assistant",
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": "Asia/Kolkata", # Local User timezone defaults
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": "Asia/Kolkata",
            },
        }

        print(f"[CALENDAR] Creating event '{title}' at {start_dt.isoformat()}...", flush=True)
        created_event = service.events().insert(calendarId="primary", body=event).execute()
        event_link = created_event.get("htmlLink", "")
        return True, f"📅 Event '{title}' created successfully in Google Calendar."
    except Exception as e:
        print(f"[CALENDAR ERROR] {e}", flush=True)
        return False, f"Failed to create calendar event: {e}"


def log_to_sheet(sheet_name: str, data: dict) -> tuple[bool, str]:
    """Search for an existing Google Sheet by name, create it if not found, and append data columns."""
    creds = get_google_creds()
    if not creds:
        return False, "Google Workspace authentication not configured."

    try:
        from googleapiclient.discovery import build
        drive_service = build("drive", "v3", credentials=creds)
        sheets_service = build("sheets", "v4", credentials=creds)

        # 1. Search for the sheet in user's Google Drive (escaping single quotes in name)
        safe_sheet_name = sheet_name.replace("'", "\\'")
        query = f"mimeType='application/vnd.google-apps.spreadsheet' and name='{safe_sheet_name}' and trashed=false"
        print(f"[SHEETS] Searching Drive for sheet named '{sheet_name}'...", flush=True)
        results = drive_service.files().list(q=query, spaces="drive", fields="files(id, name)").execute()
        files = results.get("files", [])

        spreadsheet_id = None
        is_new = False

        if files:
            spreadsheet_id = files[0]["id"]
            print(f"[SHEETS] Found existing sheet ID: {spreadsheet_id}", flush=True)
        else:
            # Create a brand new spreadsheet dynamically!
            print(f"[SHEETS] Sheet '{sheet_name}' not found. Creating a new Spreadsheet...", flush=True)
            spreadsheet_body = {
                "properties": {
                    "title": sheet_name
                }
            }
            new_sheet = sheets_service.spreadsheets().create(body=spreadsheet_body, fields="spreadsheetId").execute()
            spreadsheet_id = new_sheet.get("spreadsheetId")
            is_new = True
            print(f"[SHEETS] Created new sheet ID: {spreadsheet_id}", flush=True)

        # 2. Format columns dynamically based on dict keys and values
        # We append a row with the values, and if new, we also write a header row!
        headers = list(data.keys())
        values = list(data.values())

        # Ensure datetime is logged if not explicitly provided
        if "timestamp" not in headers and "time" not in headers:
            headers.insert(0, "Timestamp")
            values.insert(0, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        # Check if we need to write headers
        if is_new:
            # Write headers first
            header_body = {"values": [headers]}
            sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range="Sheet1!A1",
                valueInputOption="USER_ENTERED",
                body=header_body
            ).execute()

        # Append the data row
        body = {"values": [values]}
        sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range="Sheet1!A:A",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body
        ).execute()

        status = "Created new sheet and logged row" if is_new else "Logged row to existing sheet"
        return True, f"📊 Data successfully logged to Google Sheet '{sheet_name}' ({status})."

    except Exception as e:
        print(f"[SHEETS ERROR] {e}", flush=True)
        return False, f"Failed to log data to Google Sheets: {e}"


def log_telemetry(session_id: str, gear: str, tokens: dict, success: bool, query: str = "") -> tuple[bool, str]:
    """Logs execution, token metrics, and status to a master 'ARIA_Telemetry' Google Sheet."""
    telemetry_data = {
        "Session ID": session_id,
        "Gear": gear,
        "Success": "SUCCESS" if success else "FAILED",
        "Prompt Tokens": tokens.get("prompt", 0),
        "Completion Tokens": tokens.get("completion", 0),
        "Total Tokens": tokens.get("total", 0),
        "Query Preview": query[:120] if query else ""
    }
    print(f"[TELEMETRY] Logging execution data to sheet: {telemetry_data}", flush=True)
    return log_to_sheet("ARIA_Telemetry", telemetry_data)


def create_doc(title: str, content: str) -> tuple[bool, str]:
    """Create a Google Doc with title and content in user's Drive."""
    creds = get_google_creds()
    if not creds:
        return False, "Google Workspace authentication not configured."

    try:
        from googleapiclient.discovery import build
        docs_service = build("docs", "v1", credentials=creds)

        # 1. Create a blank document
        print(f"[DOCS] Creating blank document '{title}'...", flush=True)
        doc = docs_service.documents().create(body={"title": title}).execute()
        doc_id = doc.get("documentId")
        print(f"[DOCS] Created blank document ID: {doc_id}", flush=True)

        # 2. Insert content
        requests = [
            {
                "insertText": {
                    "location": {
                        "index": 1,
                    },
                    "text": content
                }
            }
        ]
        docs_service.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
        return True, f"📑 Google Doc '{title}' created successfully in Google Drive."
    except Exception as e:
        print(f"[DOCS ERROR] {e}", flush=True)
        return False, f"Failed to create Google Doc: {e}"


def parse_task_due_date(due_date_str: str) -> str:
    """Parse natural language expressions like 'tomorrow 12:00 PM' into an RFC 3339 string."""
    import re
    ds = str(due_date_str).strip().lower()
    if not ds:
        return None

    now = datetime.now()
    target_dt = now

    # 1. Evaluate relative day markers
    if "tomorrow" in ds:
        target_dt = now + timedelta(days=1)
    elif "today" in ds:
        target_dt = now
    
    # 2. Parse time structure (e.g. '12:00 PM', '14:30', '9 am')
    time_match = re.search(r'(\d{1,2}):(\d{2})\s*(pm|am)?', ds)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        ampm = time_match.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        target_dt = target_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        # Default to standard midday due date if only day is defined
        target_dt = target_dt.replace(hour=12, minute=0, second=0, microsecond=0)

    # Return as Google Tasks expected UTC/Z timestamp format
    return target_dt.isoformat() + "Z"


def create_google_task(title: str, due_date: str = "", notes: str = "") -> tuple[bool, str]:
    """Create a task on the user's default Google Tasks list."""
    creds = get_google_creds()
    if not creds:
        return False, "Google Workspace authentication not configured."

    try:
        from googleapiclient.discovery import build
        service = build("tasks", "v1", credentials=creds)
        task = {
            "title": title,
            "notes": notes or "Created by ARIA Multi-Agent AI Assistant",
        }

        if due_date:
            due_rfc = parse_task_due_date(due_date)
            if due_rfc:
                task["due"] = due_rfc

        print(f"[TASKS] Creating Google Task '{title}' with due={due_date}...", flush=True)
        service.tasks().insert(tasklist="@default", body=task).execute()
        return True, f"✅ Google Task '{title}' created successfully."
    except Exception as e:
        print(f"[TASKS ERROR] {e}", flush=True)
        return False, f"Failed to create Google Task: {e}"


def get_drive_folder_id(drive_service, folder_name: str) -> str:
    """Find a folder by name, or create it if not found."""
    safe_folder_name = folder_name.replace("'", "\\'")
    query = f"mimeType='application/vnd.google-apps.folder' and name='{safe_folder_name}' and trashed=false"
    results = drive_service.files().list(q=query, spaces="drive", fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    
    # Create folder
    folder_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    folder = drive_service.files().create(body=folder_metadata, fields='id').execute()
    return folder.get('id')


def copy_google_photos_to_drive(category: str, folder_name: str) -> tuple[bool, str]:
    """Copy documents (ID cards/docs) or videos from Google Photos to Google Drive."""
    creds = get_google_creds()
    if not creds:
        return False, "Google Workspace authentication not configured."

    try:
        from googleapiclient.discovery import build
        drive_service = build("drive", "v3", credentials=creds)
        
        # 1. Ensure target folder in Drive exists
        folder_id = get_drive_folder_id(drive_service, folder_name)
        
        # 2. Call Google Photos search API using urllib.request
        import urllib.request
        import urllib.error
        
        url = "https://photoslibrary.googleapis.com/v1/mediaItems:search"
        
        if category == "DOCUMENTS":
            payload = {
                "filters": {
                    "contentFilter": {
                        "includedContentCategories": ["DOCUMENTS"]
                    }
                },
                "pageSize": 10
            }
        elif category in ("PHOTO", "PHOTOS", "IMAGE", "IMAGES"):
            payload = {
                "filters": {
                    "mediaTypeFilter": {
                        "mediaTypes": ["PHOTO"]
                    }
                },
                "pageSize": 5
            }
        else: # VIDEO
            payload = {
                "filters": {
                    "mediaTypeFilter": {
                        "mediaTypes": ["VIDEO"]
                    }
                },
                "pageSize": 5
            }
            
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        
        print(f"[PHOTOS] Fetching {category} from Google Photos...", flush=True)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as he:
            err_body = he.read().decode('utf-8')
            print(f"[PHOTOS ERROR] {he} - {err_body}", flush=True)
            if "has not been used in project" in err_body:
                return False, "Google Photos Library API is not enabled in your Google Cloud Project. Please enable it."
            return False, f"Google Photos API returned error: {he.reason}"
            
        items = result.get("mediaItems", [])
        if not items:
            return True, f"No matching {category.lower()} found in your Google Photos library."
            
        print(f"[PHOTOS] Found {len(items)} items. Copying to Google Drive folder '{folder_name}'...", flush=True)
        
        copied_count = 0
        for item in items:
            filename = item.get("filename", f"photo_{item['id'][:8]}.jpg")
            base_url = item.get("baseUrl")
            
            is_video = item.get("mediaMetadata", {}).get("video") is not None
            download_url = f"{base_url}=dv" if is_video else f"{base_url}=d"
            
            try:
                # Download bytes
                with urllib.request.urlopen(download_url, timeout=30) as img_resp:
                    file_bytes = img_resp.read()
            except Exception as de:
                print(f"[PHOTOS DOWNLOAD ERROR] Failed to download {filename}: {de}", flush=True)
                continue
                
            # Upload to Drive
            from googleapiclient.http import MediaByteArrayUpload
            media = MediaByteArrayUpload(file_bytes, mimetype=item.get("mimeType", "application/octet-stream"))
            
            file_metadata = {
                'name': filename,
                'parents': [folder_id]
            }
            drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            copied_count += 1
            
        return True, f"✅ Successfully copied {copied_count} {category.lower()} from Google Photos to Google Drive folder '{folder_name}'!"
        
    except Exception as e:
        print(f"[PHOTOS MAIN ERROR] {e}", flush=True)
        return False, f"Failed to copy files from Google Photos: {e}"


def copy_google_contacts_to_drive(sheet_name: str = "Contacts") -> tuple[bool, str]:
    """Fetch all user contacts using Google People API and write them to a Google Sheet."""
    creds = get_google_creds()
    if not creds:
        return False, "Google Workspace authentication not configured."

    try:
        from googleapiclient.discovery import build
        people_service = build("people", "v1", credentials=creds)
        drive_service = build("drive", "v3", credentials=creds)
        sheets_service = build("sheets", "v4", credentials=creds)

        print("[CONTACTS] Fetching contacts from Google People API...", flush=True)
        results = people_service.people().connections().list(
            resourceName="people/me",
            pageSize=1000,
            personFields="names,emailAddresses,phoneNumbers"
        ).execute()

        connections = results.get("connections", [])
        if not connections:
            return True, "No contacts found in your Google Account."

        contacts_data = []
        for person in connections:
            names = person.get("names", [])
            emails = person.get("emailAddresses", [])
            phones = person.get("phoneNumbers", [])

            first_name = names[0].get("givenName", "") if names else ""
            last_name = names[0].get("familyName", "") if names else ""
            email = emails[0].get("value", "") if emails else ""
            phone = phones[0].get("value", "") if phones else ""

            if first_name or last_name or email or phone:
                contacts_data.append({
                    "First Name": first_name,
                    "Last Name": last_name,
                    "Email": email,
                    "Phone": phone
                })

        if not contacts_data:
            return True, "No valid contact details found."

        # Search for sheet in Drive
        safe_sheet_name = sheet_name.replace("'", "\\'")
        query = f"mimeType='application/vnd.google-apps.spreadsheet' and name='{safe_sheet_name}' and trashed=false"
        results = drive_service.files().list(q=query, spaces="drive", fields="files(id, name)").execute()
        files = results.get("files", [])

        spreadsheet_id = None
        if files:
            spreadsheet_id = files[0]["id"]
        else:
            spreadsheet_body = {"properties": {"title": sheet_name}}
            new_sheet = sheets_service.spreadsheets().create(body=spreadsheet_body, fields="spreadsheetId").execute()
            spreadsheet_id = new_sheet.get("spreadsheetId")

        # Prepare values
        headers = ["Timestamp", "First Name", "Last Name", "Email", "Phone"]
        rows = [[datetime.now().strftime("%Y-%m-%d %H:%M:%S"), c["First Name"], c["Last Name"], c["Email"], c["Phone"]] for c in contacts_data]

        # Clear existing content first
        sheets_service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range="Sheet1!A1:Z"
        ).execute()

        # Update values
        body = {
            "values": [headers] + rows
        }
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range="Sheet1!A1",
            valueInputOption="RAW",
            body=body
        ).execute()

        return True, f"✅ Successfully copied {len(contacts_data)} contacts to Google Sheet '{sheet_name}' in Google Drive!"

    except Exception as e:
        print(f"[CONTACTS ERROR] {e}", flush=True)
        return False, f"Failed to copy contacts: {e}"


def search_google_sheet(sheet_name: str, query: str) -> tuple[bool, str]:
    """Search for a specific query inside a Google Sheet by name."""
    creds = get_google_creds()
    if not creds:
        return False, "Google Workspace authentication not configured."

    try:
        from googleapiclient.discovery import build
        drive_service = build("drive", "v3", credentials=creds)
        sheets_service = build("sheets", "v4", credentials=creds)

        # 1. Search for the sheet in user's Google Drive
        safe_sheet_name = sheet_name.replace("'", "\\'")
        drive_query = f"mimeType='application/vnd.google-apps.spreadsheet' and name='{safe_sheet_name}' and trashed=false"
        print(f"[SHEETS] Searching Drive for sheet: '{sheet_name}'...", flush=True)
        results = drive_service.files().list(q=drive_query, spaces="drive", fields="files(id, name)").execute()
        files = results.get("files", [])

        # Fallback 1: Broad search for sheets containing keywords
        if not files:
            import re
            clean_words = [w.strip() for w in re.split(r'[\s_]+', sheet_name.replace("'", "")) if len(w.strip()) >= 3]
            if clean_words:
                sub_queries = " and ".join(f"name contains '{w}'" for w in clean_words)
                broad_query = f"mimeType='application/vnd.google-apps.spreadsheet' and {sub_queries} and trashed=false"
                print(f"[SHEETS] Trying broad query: {broad_query}", flush=True)
                try:
                    results = drive_service.files().list(q=broad_query, spaces="drive", fields="files(id, name)").execute()
                    files = results.get("files", [])
                except Exception as e:
                    print(f"[SHEETS] Broad query failed: {e}", flush=True)

        # Fallback 2: General fallback to find sheets containing 'Review' or the user's business keywords
        if not files:
            fallback_keywords = ["review", "anshu", "computers", "orai"]
            for kw in fallback_keywords:
                kw_query = f"mimeType='application/vnd.google-apps.spreadsheet' and name contains '{kw}' and trashed=false"
                print(f"[SHEETS] Trying general fallback keyword query: name contains '{kw}'...", flush=True)
                try:
                    results = drive_service.files().list(q=kw_query, spaces="drive", fields="files(id, name)").execute()
                    files = results.get("files", [])
                    if files:
                        print(f"[SHEETS] Found matches for keyword '{kw}': {[f['name'] for f in files]}", flush=True)
                        break
                except Exception:
                    pass

        # Fallback 3: List last 5 recently modified spreadsheets
        if not files:
            print("[SHEETS] Fuzzy search failed. Fetching last 5 spreadsheets as absolute fallback...", flush=True)
            try:
                list_query = "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
                results = drive_service.files().list(
                    q=list_query, spaces="drive", fields="files(id, name)", orderBy="modifiedTime desc", pageSize=5
                ).execute()
                files = results.get("files", [])
                if files:
                    print(f"[SHEETS] Using most recently modified spreadsheet: '{files[0]['name']}'", flush=True)
            except Exception as e:
                print(f"[SHEETS] Absolute fallback failed: {e}", flush=True)

        if not files:
            return False, f"Google Sheet '{sheet_name}' not found in your Google Drive."

        spreadsheet_id = files[0]["id"]

        # 2. Get all values in the sheet
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Sheet1!A1:Z"
        ).execute()

        rows = result.get("values", [])
        if not rows:
            return True, f"Google Sheet '{sheet_name}' is empty."

        headers = rows[0]
        data_rows = rows[1:]

        # 3. Search for matches
        q = str(query).lower().strip()
        matches = []

        for r_idx, row in enumerate(data_rows):
            # Check if query matches any cell in the row
            row_str = " ".join(str(cell) for cell in row).lower()
            if q in row_str:
                match_details = []
                for c_idx, cell in enumerate(row):
                    header = headers[c_idx] if c_idx < len(headers) else f"Column {c_idx+1}"
                    match_details.append(f"{header}: {cell}")
                matches.append(f"• {', '.join(match_details)}")

        if not matches:
            return True, f"No matches found for '{query}' in Google Sheet '{sheet_name}'."

        formatted_result = f"🔍 Found {len(matches)} match(es) for '{query}' in '{sheet_name}':\n" + "\n".join(matches)
        return True, formatted_result

    except Exception as e:
        print(f"[SHEETS SEARCH ERROR] {e}", flush=True)
        return False, f"Failed to search Google Sheet: {e}"


def search_duckduckgo_image(query: str) -> tuple[bool, str]:
    """Search DuckDuckGo for an image and return a tagged result for Telegram with retries."""
    import time
    from ddgs import DDGS
    
    max_retries = 3
    delay = 1.0
    
    print(f"[IMAGE SEARCH] Searching for images of '{query}'...", flush=True)
    
    for attempt in range(1, max_retries + 1):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(query, max_results=3))
            if results:
                img_url = results[0]["image"]
                title = results[0].get("title", f"Image of {query}")
                print(f"[IMAGE SEARCH SUCCESS] Attempt {attempt}: Found {len(results)} images.", flush=True)
                return True, f"[IMAGE] url={img_url} caption={title}"
            else:
                print(f"[IMAGE SEARCH] Attempt {attempt}: No results found.", flush=True)
        except Exception as e:
            print(f"[IMAGE SEARCH ERROR] Attempt {attempt} failed: {e}", flush=True)
            if attempt == max_retries:
                return False, f"Failed to search for image: {e} after {max_retries} attempts."
            time.sleep(delay * attempt)
            
    return False, f"No images found for '{query}'."


def upload_file_to_drive(file_path: str, folder_name: str = "ARIA Reports") -> tuple[bool, str]:
    """Uploads a local file directly to a specified folder in the user's Google Drive."""
    creds = get_google_creds()
    if not creds:
        return False, "Google Workspace authentication not configured."

    if not os.path.exists(file_path):
        return False, f"Local file not found at: {file_path}"

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        
        drive_service = build("drive", "v3", credentials=creds)
        
        # 1. Get or create folder
        folder_id = get_drive_folder_id(drive_service, folder_name)
        
        # 2. Upload file
        filename = os.path.basename(file_path)
        media = MediaFileUpload(
            file_path, 
            mimetype="application/pdf" if filename.endswith(".pdf") else "application/octet-stream", 
            resumable=True
        )
        
        file_metadata = {
            "name": filename,
            "parents": [folder_id]
        }
        
        print(f"[DRIVE] Uploading {filename} to folder '{folder_name}'...", flush=True)
        uploaded_file = drive_service.files().create(body=file_metadata, media_body=media, fields="id, name, webViewLink").execute()
        
        file_id = uploaded_file.get("id")
        view_link = uploaded_file.get("webViewLink", "")
        print(f"[DRIVE SUCCESS] Uploaded file ID: {file_id}. View Link: {view_link}", flush=True)
        return True, f"✅ Successfully uploaded '{filename}' to Google Drive folder '{folder_name}'! View Link: {view_link}"
        
    except Exception as e:
        print(f"[DRIVE ERROR] Upload failed: {e}", flush=True)
        return False, f"Failed to upload file to Google Drive: {e}"


# ── Central Execution Router ─────────────────────────────────────────────────

def execute_google_action(action: str, params: dict) -> tuple[bool, str]:
    """Directly route automation queries to official Google Workspace APIs."""
    if action == "send_email":
        to = params.get("to", "")
        subject = params.get("subject", "Automated Message from ARIA")
        body = params.get("body", "")
        if not to or not body:
            return False, "Missing recipient 'to' or message 'body' parameters."
        return send_gmail(to, subject, body)

    elif action == "create_event":
        title = params.get("title", "New Event")
        date = params.get("date", datetime.now().strftime("%Y-%m-%d"))
        time = params.get("time", "12:00")
        duration = params.get("duration", "1 hour")
        description = params.get("description", "")
        return create_calendar_event(title, date, time, duration, description)

    elif action == "log_to_sheet":
        sheet_name = params.get("sheet_name", "ARIA Log")
        data = params.get("data", {})
        if not data:
            return False, "No logging 'data' parameters provided."
        return log_to_sheet(sheet_name, data)

    elif action == "create_doc":
        title = params.get("title", "ARIA New Document")
        content = params.get("content", "")
        return create_doc(title, content)

    elif action == "create_task":
        title = params.get("title", "New Task")
        due_date = params.get("due_date", "")
        notes = params.get("notes", "")
        return create_google_task(title, due_date, notes)

    elif action == "copy_photos_to_drive":
        category = params.get("category", "DOCUMENTS")
        folder_name = params.get("folder_name", "Photos")
        return copy_google_photos_to_drive(category, folder_name)

    elif action == "copy_contacts_to_drive":
        sheet_name = params.get("sheet_name", "Contacts")
        return copy_google_contacts_to_drive(sheet_name)

    elif action == "search_sheet":
        sheet_name = params.get("sheet_name", "Contacts")
        query = params.get("query", "")
        if not query:
            return False, "Missing 'query' parameter to search."
        return search_google_sheet(sheet_name, query)

    elif action == "search_image":
        query = params.get("query", "")
        if not query:
            return False, "Missing 'query' parameter to search image."
        return search_duckduckgo_image(query)

    elif action == "upload_to_drive":
        file_path = params.get("file_path", "")
        folder_name = params.get("folder_name", "ARIA Reports")
        if not file_path:
            return False, "Missing 'file_path' parameter to upload."
        return upload_file_to_drive(file_path, folder_name)

    else:
        return False, f"Action `{action}` is not natively supported in direct Google Workspace integration."
