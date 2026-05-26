import sys
import os

# Force UTF-8 encoding for Windows standard streams to prevent emoji/unicode logging crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Add the 'aria' directory to the path so we can import google_service
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "aria"))

from google_service import get_google_creds, TOKEN_PATH

print("="*60)
print("ARIA Google Workspace OAuth 2.0 Authenticator")
print("="*60)
print("This script will open a browser window to authorize ARIA.")
print("Once authorized, it will save a permanent 'token.json' file.")
print("="*60)

try:
    creds = get_google_creds()
    if creds:
        print("\n" + "="*60)
        print("✅ SUCCESS: Authentication completed successfully!")
        print(f"Permanent credentials saved to: {TOKEN_PATH}")
        print("You can now safely close this window and run the bot.")
        print("="*60)
    else:
        print("\n❌ ERROR: Authentication failed. Please check your credentials.json file.")
except Exception as e:
    print(f"\n❌ CRITICAL ERROR: {e}")

input("\nPress Enter to exit...")
