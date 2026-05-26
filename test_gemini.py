import os
import sys

# Force UTF-8 encoding for Windows standard streams to prevent emoji/unicode logging crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage

API_KEY = "AIzaSyDvdk3YviRanZywosse2rF8ZumBGzZqLbc"

models = [
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-1.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-pro"
]

print("="*60)
print("Testing Gemini Models with User Key...")
print("="*60)

for model in models:
    try:
        print(f"Testing model: {model} ...", end="", flush=True)
        llm = ChatGoogleGenerativeAI(model=model, google_api_key=API_KEY)
        res = llm.invoke([HumanMessage(content="Hello!")])
        print(f" SUCCESS! Reply: {res.content.strip()[:30]}")
    except Exception as e:
        print(f" FAILED! Error: {e}")
