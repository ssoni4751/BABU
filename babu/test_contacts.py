import sys
import os

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "babu"))

from google_service import get_google_creds
from googleapiclient.discovery import build

try:
    creds = get_google_creds()
    if not creds:
        print("ERROR: Credentials not found.")
        sys.exit(1)
        
    print("Connecting to People API...")
    people_service = build("people", "v1", credentials=creds)
    
    print("Fetching connections...")
    results = people_service.people().connections().list(
        resourceName="people/me",
        pageSize=5,
        personFields="names,emailAddresses,phoneNumbers"
    ).execute()
    
    connections = results.get("connections", [])
    print(f"Success! Found {len(connections)} connections.")
    for idx, person in enumerate(connections):
        names = person.get("names", [])
        name = names[0].get("displayName", "No Name") if names else "No Name"
        print(f"[{idx+1}] {name}")
        
except Exception as e:
    print(f"CRITICAL ERROR: {e}")
