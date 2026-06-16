import os
import sys
import zipfile
from datetime import datetime

# Force UTF-8 encoding for Windows standard streams to prevent emoji/unicode logging crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Add babu to python path so we can import google_service
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "babu"))

from google_service import get_google_creds, get_drive_folder_id
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

def create_backup_zip(zip_path: str, source_dirs: list, source_files: list):
    """Compress specified directories and files into a ZIP archive, excluding cache files."""
    print(f"[ZIP] Creating backup archive at: {zip_path}...", flush=True)
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # 1. Compress directories
        for src_dir in source_dirs:
            if not os.path.exists(src_dir):
                continue
            dir_name = os.path.basename(src_dir)
            for root, dirs, files in os.walk(src_dir):
                # Exclude __pycache__, .venv, .git, etc.
                if any(exclude in root for exclude in ['__pycache__', '.venv', '.git', '.replit', '.replitignore', 'temp']):
                    continue
                for file in files:
                    file_path = os.path.join(root, file)
                    # Exclude huge binary/state files or temporary cache files
                    if file.endswith(('.pyc', '.log', '.txt')) and file not in ['chat_id.txt', 'last_post_date.txt']:
                        continue
                    # Compute arcname (relative path inside zip)
                    rel_path = os.path.relpath(file_path, os.path.dirname(src_dir))
                    zipf.write(file_path, rel_path)
                    
        # 2. Compress root files
        for src_file in source_files:
            if os.path.exists(src_file):
                zipf.write(src_file, os.path.basename(src_file))
                
    print(f"[ZIP] Archive created successfully. Size: {os.path.getsize(zip_path)} bytes.", flush=True)


def upload_to_drive(zip_path: str, folder_name: str = "ARIA Backups") -> str:
    """Upload the zip file directly to Google Drive under 'ARIA Backups' folder."""
    creds = get_google_creds()
    if not creds:
        raise ValueError("Google Workspace credentials could not be loaded.")
        
    drive_service = build("drive", "v3", credentials=creds)
    
    # 1. Get or create the ARIA Backups folder ID
    print(f"[DRIVE] Resolving folder '{folder_name}'...", flush=True)
    folder_id = get_drive_folder_id(drive_service, folder_name)
    print(f"[DRIVE] Folder ID: {folder_id}", flush=True)
    
    # 2. Upload file
    file_metadata = {
        'name': os.path.basename(zip_path),
        'parents': [folder_id]
    }
    media = MediaFileUpload(zip_path, mimetype='application/zip', resumable=True)
    
    print(f"[DRIVE] Uploading {os.path.basename(zip_path)} to Google Drive...", flush=True)
    uploaded_file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, name, webViewLink'
    ).execute()
    
    print(f"[DRIVE] Upload complete! File ID: {uploaded_file.get('id')}", flush=True)
    return uploaded_file.get('webViewLink', '')


def main():
    print("="*60)
    print("ARIA CODELAB PACKUP & GOOGLE DRIVE BACKUP SYSTEM")
    print("="*60)
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Paths to back up
    babu_dir = os.path.join(current_dir, "babu")
    test_fb_file = os.path.join(current_dir, "test_facebook.py")
    pyproject_file = os.path.join(current_dir, "pyproject.toml")
    env_file = os.path.join(current_dir, ".env")
    
    # Temp zip path
    temp_zip_path = os.path.join(current_dir, f"babu_backup_{today_str}.zip")
    
    try:
        # Create ZIP
        create_backup_zip(
            temp_zip_path,
            source_dirs=[babu_dir],
            source_files=[test_fb_file, pyproject_file, env_file]
        )
        
        # Upload to Google Drive
        view_link = upload_to_drive(temp_zip_path)
        
        # Cleanup local zip
        if os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)
            
        print("\n" + "="*60)
        print("SUCCESSFUL PACKUP! DAY'S PROGRESS BACKED UP TO GOOGLE DRIVE!")
        print(f"Link: {view_link}")
        print("="*60)
        
    except Exception as e:
        print(f"\nBACKUP FAILED: {e}")
        # Clean up zip in case of failure
        if os.path.exists(temp_zip_path):
            try:
                os.remove(temp_zip_path)
            except Exception:
                pass
        sys.exit(1)

if __name__ == "__main__":
    main()
