"""Operational awareness for BABU.

Awareness observes and recommends.  It never plans, authorizes, or executes.
The module is deliberately deterministic so startup and tests do not depend on
an LLM or a live external service.
"""

from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Mapping, Optional


@dataclass(frozen=True)
class ServiceDefinition:
    name: str
    capabilities: tuple[str, ...]
    dependencies: tuple[str, ...]
    failure_modes: tuple[str, ...]
    fallbacks: tuple[str, ...]


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    available: bool
    detail: str


@dataclass(frozen=True)
class SituationReport:
    objective: str
    available_services: tuple[str, ...]
    unavailable_services: tuple[str, ...]
    relevant_history: tuple[str, ...] = ()
    known_risks: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    recommended_strategy: str = "Proceed through planning and governance."
    fallback_strategy: str = "Ask the human for direction or use a declared fallback."
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


SERVICE_REGISTRY: Mapping[str, ServiceDefinition] = {
    "telegram": ServiceDefinition("telegram", ("receive_message", "send_message"), ("TELEGRAM_BOT_TOKEN",), ("invalid token", "network unavailable"), ("console simulator",)),
    "facebook": ServiceDefinition("facebook", ("publish_page_post",), ("FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_ACCESS_TOKEN"), ("expired token", "API unavailable"), ("approval queue",)),
    "email": ServiceDefinition("email", ("send_email", "search_gmail"), ("Google OAuth",), ("expired OAuth token",), ("draft only",)),
    "google_workspace": ServiceDefinition("google_workspace", ("calendar", "docs", "drive", "sheets"), ("Google OAuth",), ("expired OAuth token",), ("local export",)),
    "scheduler": ServiceDefinition("scheduler", ("scheduled_trigger",), (), ("process stopped",), ("manual trigger",)),
    "web_search": ServiceDefinition("web_search", ("public_information_retrieval",), ("TAVILY_API_KEY or DDGS",), ("provider unavailable",), ("local knowledge base",)),
}


class AwarenessEngine:
    """Build situation reports from observed service state."""

    def __init__(self, statuses: Iterable[ServiceStatus]):
        self._statuses = tuple(statuses)

    def create_report(
        self,
        objective: str,
        *,
        relevant_history: Iterable[str] = (),
        known_risks: Iterable[str] = (),
        constraints: Iterable[str] = (),
    ) -> SituationReport:
        available = tuple(s.name for s in self._statuses if s.available)
        unavailable = tuple(s.name for s in self._statuses if not s.available)
        risks = tuple(known_risks) + tuple(
            f"Service unavailable: {s.name} ({s.detail})"
            for s in self._statuses if not s.available
        )
        confidence = max(0.0, 1.0 - (0.1 * len(unavailable)))
        return SituationReport(
            objective=objective,
            available_services=available,
            unavailable_services=unavailable,
            relevant_history=tuple(relevant_history),
            known_risks=risks,
            constraints=tuple(constraints),
            confidence=confidence,
        )


def inspect_services(environment: Mapping[str, str], google_configured: bool = False) -> tuple[ServiceStatus, ...]:
    """Inspect configuration only; no network calls and no side effects."""
    return (
        ServiceStatus("telegram", bool(environment.get("TELEGRAM_BOT_TOKEN")), "token configured" if environment.get("TELEGRAM_BOT_TOKEN") else "missing token"),
        ServiceStatus("facebook", bool(environment.get("FACEBOOK_PAGE_ID") and environment.get("FACEBOOK_PAGE_ACCESS_TOKEN")), "credentials configured" if environment.get("FACEBOOK_PAGE_ID") and environment.get("FACEBOOK_PAGE_ACCESS_TOKEN") else "missing credentials"),
        ServiceStatus("email", google_configured, "OAuth configured" if google_configured else "OAuth unavailable"),
        ServiceStatus("google_workspace", google_configured, "OAuth configured" if google_configured else "OAuth unavailable"),
        ServiceStatus("scheduler", True, "local scheduler available"),
        ServiceStatus("web_search", bool(environment.get("TAVILY_API_KEY")) or True, "Tavily or DDGS fallback"),
    )
