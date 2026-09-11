"""Eight-phase BABU bootstrap mandated by the institutional constitution."""

from dataclasses import dataclass, field
from pathlib import Path
import os
import threading
import time

_CURRENT_PATH = Path(__file__).resolve()
if (_CURRENT_PATH.parent / "brain").is_dir():
    ROOT = _CURRENT_PATH.parent
elif (_CURRENT_PATH.parent.parent / "brain").is_dir():
    ROOT = _CURRENT_PATH.parent.parent
else:
    ROOT = _CURRENT_PATH.parent

BRAIN_DIR = ROOT / "brain"


@dataclass
class BootstrapState:
    completed_phases: list[str] = field(default_factory=list)
    documents: dict[str, str] = field(default_factory=dict)
    service_statuses: tuple = ()
    startup_report: object | None = None
    planner_context: str = ""


def _load_required(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError(f"Critical Boot Failure: required institutional file missing: {path}")
    return path.read_text(encoding="utf-8")


def _complete(state: BootstrapState, phase: str) -> None:
    state.completed_phases.append(phase)
    print(f"[BOOTSTRAP] {phase}", flush=True)


def initialize_institution(*, initialize_storage: bool = True) -> BootstrapState:
    """Initialize BABU through Phase 7 without opening any transport."""
    state = BootstrapState()

    # Phase 0: Load Constitution (Redundant disk load removed)
    _complete(state, "Phase 0: Load Constitution")

    # Phase 1: Load ROOT_INDEX (Redundant disk load removed)
    _complete(state, "Phase 1: Load ROOT_INDEX")

    try:
        from .governance import E0_A_Rules
    except ImportError:
        from governance import E0_A_Rules
    if not E0_A_Rules.get("human_approval_required"):
        raise RuntimeError("Critical Boot Failure: human approval authority is disabled")
    _complete(state, "Phase 2: Load Governance")

    # Configuration inspection is intentionally transport-free.
    config = dict(os.environ)
    _complete(state, "Phase 3: Load Configuration")

    if initialize_storage:
        try:
            from . import bot as bot_module
        except ImportError:
            import bot as bot_module
        if bot_module.DATABASE_URL and bot_module.DATABASE_URL.startswith(("postgres://", "postgresql://")):
            bot_module.init_postgres_db()
        bot_module.init_durable_checkpoint_db()
        bot_module.cleanup_corrupt_failures()
    _complete(state, "Phase 4: Verify Infrastructure")

    # Phase 5: Load Brain (Redundant disk load removed, planner will load directly)
    _complete(state, "Phase 5: Load Brain")

    try:
        from .awareness import AwarenessEngine, inspect_services
    except ImportError:
        from awareness import AwarenessEngine, inspect_services
    try:
        try:
            from .google_service import is_google_configured
        except ImportError:
            from google_service import is_google_configured
        google_ready = is_google_configured()
    except Exception:
        google_ready = False
    state.service_statuses = inspect_services(config, google_ready)
    state.startup_report = AwarenessEngine(state.service_statuses).create_report(
        "Initialize BABU transports",
        constraints=("Human authority is supreme", "Governance precedes execution"),
    )
    _complete(state, "Phase 6: Initialize Awareness")

    try:
        from .planner import compile_planner
    except ImportError:
        from planner import compile_planner
    state.planner_context = compile_planner() or ""
    _complete(state, "Phase 7: Compile Planner")
    return state


def open_transports(state: BootstrapState) -> None:
    """Open interfaces only after all institutional phases have succeeded."""
    required = [f"Phase {n}:" for n in range(8)]
    if len(state.completed_phases) != 8 or not all(
        phase.startswith(prefix) for phase, prefix in zip(state.completed_phases, required)
    ):
        raise RuntimeError("Transport gate denied: institutional bootstrap is incomplete")

    from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters
    try:
        from . import bot as bot_module
    except ImportError:
        import bot as bot_module

    health_thread = threading.Thread(target=bot_module.start_health_server, name="web_dashboard_health_server", daemon=True)
    health_thread.start()

    # Launch Public Client Desk Bot (@Anshu4751_bot) in background daemon thread
    try:
        try:
            from .public_bot import start_public_bot_thread
        except ImportError:
            from public_bot import start_public_bot_thread
        public_thread = start_public_bot_thread()
        if public_thread:
            print("[BOOTSTRAP] Public Client Desk Bot thread started successfully (@Anshu4751_bot).", flush=True)
    except Exception as pub_err:
        print(f"[BOOTSTRAP WARNING] Could not start public bot thread: {pub_err}", flush=True)

    bot = ApplicationBuilder().token(bot_module.TELEGRAM_TOKEN).build()
    bot_module.tg_application = bot
    bot_module.start_social_scheduler(bot)
    for command, handler in (
        ("launch", bot_module.cmd_launch),
        ("clear", bot_module.cmd_clear),
        ("goals", bot_module.cmd_goals),
        ("help", bot_module.cmd_help),
        ("stats", bot_module.cmd_stats),
        ("model", bot_module.cmd_model),
        ("postnow", bot_module.cmd_postnow),
        ("promote", bot_module.cmd_promote),
        ("retire", bot_module.cmd_retire),
        ("crm", bot_module.cmd_crm),
        ("leads", bot_module.cmd_leads),
        ("followups", bot_module.cmd_followups),
        ("follow_lead", bot_module.cmd_followups),
        ("update_lead", bot_module.cmd_update_lead),
        ("add_lead", bot_module.cmd_add_lead)
    ):
        bot.add_handler(CommandHandler(command, handler))
    bot.add_handler(MessageHandler((filters.TEXT | filters.VOICE | filters.Document.ALL) & (~filters.COMMAND), bot_module.on_message))
    bot.add_handler(CallbackQueryHandler(bot_module.on_post_callback))
    bot.add_error_handler(bot_module.telegram_error_handler)
    _complete(state, "Phase 8: Open Transports")
    while True:
        try:
            bot.run_polling(drop_pending_updates=True)
            break
        except Exception as exc:
            print(f"[TELEGRAM ERROR] {exc}. Retrying in 15 seconds...", flush=True)
            time.sleep(15)


def bootstrap_brain() -> None:
    open_transports(initialize_institution())


if __name__ == "__main__":
    bootstrap_brain()
