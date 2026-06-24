"""Policy loader – reads config/policy.yaml and provides governance helpers.
Provides:
- get_action_class(action: str) -> str | None
- policy_version() -> int
- reload_policy() – explicit reload, thread‑safe.
"""
import yaml
from pathlib import Path
from threading import Lock

_POLICY_PATH = Path(__file__).parent / "config" / "policy.yaml"
_lock = Lock()
_policy_data: dict = {}

def _load() -> None:
    global _policy_data
    with open(_POLICY_PATH, "r", encoding="utf-8") as f:
        _policy_data = yaml.safe_load(f) or {}
    if "actions" not in _policy_data:
        _policy_data["actions"] = {}

def reload_policy() -> None:
    """Reload the YAML policy file. Must be called explicitly."""
    with _lock:
        _load()

def get_action_class(action: str) -> str | None:
    return _policy_data.get("actions", {}).get(action, {}).get("class")

def policy_version() -> int:
    return int(_policy_data.get("policy_version", 0))

# Load at import time
_load()
