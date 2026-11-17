"""
aetherion_cli/tui/__init__.py
TUI package — lazy import guard.

Accepts model/session_id so the CLI can pre-seed TUI state
before the Textual app starts. Falls back gracefully if
textual is not installed.
"""
from __future__ import annotations

from typing import Optional


def run_tui(
    model:      Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Launch the Aetherion terminal workspace. Requires textual>=0.60.0."""
    try:
        from .app import run_tui as _run
        from .state import state
    except ImportError as exc:
        import sys
        sys.stderr.write(
            f"\n[aetherion] TUI dependencies not installed: {exc}\n"
            "Install them with:  pip install 'aetherion[tui]'\n\n"
        )
        sys.exit(1)

    # Pre-seed state so the TUI knows what model/session to use immediately
    if model:
        state.set_model(model)
    if session_id:
        state.set_session(session_id)

    _run()
