# -*- mode: python ; coding: utf-8 -*-
# aetherion.spec — PyInstaller build spec
#
# Usage:
#   pip install pyinstaller textual
#   pyinstaller aetherion.spec --noconfirm --clean
#
# Output:
#   dist/aetherion          (Linux/macOS)
#   dist/aetherion.exe      (Windows)
#
# Produces a single-file executable with embedded TUI support.
# The --onefile mode keeps deployment simple; startup is ~0.8s on warm disk.

import sys
from pathlib import Path

block_cipher = None

# Root of the CLI package
pkg_root = Path("aetherion_cli")

a = Analysis(
    ["aetherion_cli/main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        # Include all package data
        ("aetherion_cli", "aetherion_cli"),
    ],
    hiddenimports=[
        # Textual internals that are imported at runtime
        "textual",
        "textual.app",
        "textual.widgets",
        "textual.containers",
        "textual.binding",
        "textual.reactive",
        "textual.screen",
        "textual.css",
        "textual.css.query",
        "textual._text_area_theme",
        "textual.widget",
        "textual.message",

        # Rich
        "rich",
        "rich.console",
        "rich.markdown",
        "rich.syntax",
        "rich.table",
        "rich.panel",
        "rich.text",
        "rich.progress",
        "rich.live",

        # httpx transport internals
        "httpx",
        "httpx._transports.default",
        "httpcore",

        # keyring backends
        "keyring",
        "keyring.backends",
        "keyring.backends.fail",
        "keyrings.alt",

        # Click
        "click",
        "click.core",

        # Aetherion TUI screens
        "aetherion_cli.tui",
        "aetherion_cli.tui.app",
        "aetherion_cli.tui.state",
        "aetherion_cli.tui.gateway",
        "aetherion_cli.tui.theme",
        "aetherion_cli.tui.sidebar",
        "aetherion_cli.tui.overlays",
        "aetherion_cli.tui.skill_creator",
        "aetherion_cli.tui.screens.dashboard",
        "aetherion_cli.tui.screens.chat",
        "aetherion_cli.tui.screens.sessions",
        "aetherion_cli.tui.screens.memory",
        "aetherion_cli.tui.screens.plugins",
        "aetherion_cli.tui.screens.skills",
        "aetherion_cli.tui.screens.providers",
        "aetherion_cli.tui.screens.jobs",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Never needed in a CLI tool
        "tkinter",
        "matplotlib",
        "PIL",
        "PyQt5",
        "PyQt6",
        "wx",
        "gi",
        "numpy",
        "pandas",
        "scipy",
        "sklearn",
        "torch",
        "tensorflow",
        "pytest",
        "_pytest",
        "jinja2.ext",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="aetherion",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,           # CLI tool — always console mode
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows manifest for proper terminal behavior
    version="version_info.txt" if sys.platform == "win32" and Path("version_info.txt").exists() else None,
    icon=None,              # Add icon path here when available: "assets/aetherion.ico"
)
