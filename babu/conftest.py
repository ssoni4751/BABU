"""Pytest collection policy.

These legacy files are manual live-service smoke scripts, not isolated tests.
Keeping them out of default collection makes the deterministic suite runnable
without OAuth, social-media credentials, network access, or user data.
"""

collect_ignore = [
    "test_contacts.py",
    "test_facebook.py",
    "test_gemini.py",
    "test_memory.py",
    "test_postnow.py",
    "test_telegram_simulator.py",
    "test_telemetry.py",
]

def pytest_collect_file(file_path, parent):
    if file_path.suffix == ".py":
        print(f"COLLECTED FILE: {file_path}", flush=True)

