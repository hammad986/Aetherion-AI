"""
aetherion_cli/__main__.py
=========================
Entry point for:  python -m aetherion_cli

MUST be the first Python file that executes so the UTF-8 reconfigure
happens before Rich / Click import-time I/O captures sys.stdout/stderr.
"""
from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# Phase 0: Cross-platform UTF-8 enforcement
# ---------------------------------------------------------------------------
# Set PYTHONUTF8 so child processes also start in UTF-8 mode.
os.environ.setdefault("PYTHONUTF8", "1")

if sys.platform == "win32":
    # Enable Windows virtual terminal processing for ANSI colours in CMD/PS
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-12), 7)
    except Exception:
        pass

    # Reconfigure stdout/stderr to UTF-8 before any import that captures them
    for _stream in ("stdout", "stderr"):
        _s = getattr(sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Phase 1: Signal handling for clean Ctrl+C / SIGTERM on all platforms
# ---------------------------------------------------------------------------
import signal as _signal


def _handle_sigterm(signum, frame):  # type: ignore[no-untyped-def]
    """Clean exit on SIGTERM so pipx / supervisord shutdown gracefully."""
    sys.stderr.write("\nTerminated.\n")
    sys.exit(0)


try:
    _signal.signal(_signal.SIGTERM, _handle_sigterm)
except (OSError, ValueError):
    # SIGTERM not available on Windows in some contexts — ignore
    pass

# ---------------------------------------------------------------------------
# Phase 2: Crash safety guard
# ---------------------------------------------------------------------------
# Any uncaught exception that escapes main() should produce a friendly
# message rather than a raw Python traceback.
import traceback as _tb


def _safe_excepthook(exc_type, exc_val, exc_tb):  # type: ignore[no-untyped-def]
    if exc_type is KeyboardInterrupt:
        sys.stderr.write("\nCancelled.\n")
        sys.exit(130)
    sys.stderr.write(f"\n[aetherion] Unexpected error: {exc_type.__name__}: {exc_val}\n")
    sys.stderr.write("Run with AETHERION_DEBUG=1 for full traceback.\n")
    if os.environ.get("AETHERION_DEBUG"):
        _tb.print_exception(exc_type, exc_val, exc_tb)
    sys.exit(1)


sys.excepthook = _safe_excepthook

# ---------------------------------------------------------------------------
# Phase 3: Run
# ---------------------------------------------------------------------------
from aetherion_cli.main import main  # noqa: E402

main()
