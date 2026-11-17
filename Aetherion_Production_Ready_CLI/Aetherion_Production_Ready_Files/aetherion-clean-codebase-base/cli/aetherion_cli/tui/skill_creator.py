"""
aetherion_cli/tui/skill_creator.py

Offline workflow-skill creator for the Aetherion CLI.

Contract (owned by tests/test_skill_creator.py):
  * ``_slugify``            – safe, bounded directory slug
  * ``_extract_triggers``   – deterministic trigger phrases (max 6, deduped)
  * ``_summarize_messages`` – bounded conversation summary + example prompts
  * ``_build_skill_md``     – SKILL.md renderer with frontmatter
  * ``_do_create``          – atomic, thread-safe skill write ("✓"/"✗" result)
  * ``list_local_skills``   – local listing with bundled-skill bootstrap
  * ``create_skill_interactive`` – programmatic entry used by the TUI/CLI

Design notes
------------
* Writes are atomic: content goes to a temp file in the destination directory
  and is moved into place with :func:`os.replace`. A process-wide lock
  serialises create operations so concurrent creates of the same slug can
  never interleave a partial file (last writer wins, no crash).
* ``SKILLS_DIR`` is a module attribute so tests (and advanced users via
  ``AETHERION_SKILLS_DIR``) can redirect storage without patching internals.
* A fresh install bootstraps the packaged skill library once so
  ``aetherion skills local`` works fully offline.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SKILLS_DIR: Path = Path(
    os.environ.get("AETHERION_SKILLS_DIR", str(Path.home() / ".aetherion" / "skills"))
)

_BUNDLED_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
_SKILL_FILE = "SKILL.md"
_MAX_SLUG_LEN = 48
_MAX_TRIGGERS = 6
_MAX_SUMMARY_LINES = 20
_MAX_EXAMPLES = 5

_WRITE_LOCK = threading.Lock()

_STOPWORDS = frozenset(
    """
    a an and are as at be but by for from has have how i in into is it its of on
    or that the this to using was what when where which who why will with you your
    do does did can could should would about after all also any because been before
    being between both during each few more most other over some such than them
    then there these they this those through under until up very we were
    """.split()
)


# ── slugification ────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    """Lowercase ASCII slug: [a-z0-9-], collapsed hyphens, <=48 chars."""
    ascii_only = "".join(ch for ch in (name or "").lower() if ord(ch) < 128)
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:_MAX_SLUG_LEN].rstrip("-")


# ── trigger extraction ───────────────────────────────────────────────────────

def _extract_triggers(description: str, slug: str) -> list[str]:
    """Deterministic triggers: skill-name phrase first, then bigrams, then words."""
    triggers: list[str] = []

    def _add(candidate: str) -> bool:
        candidate = candidate.strip()
        if candidate and candidate not in triggers:
            triggers.append(candidate)
        return len(triggers) >= _MAX_TRIGGERS

    name_phrase = slug.replace("-", " ").strip()
    if name_phrase:
        _add(name_phrase)

    words = [
        w
        for w in re.findall(r"[a-z0-9]+", (description or "").lower())
        if w not in _STOPWORDS and len(w) > 2
    ]
    for i in range(len(words) - 1):
        if _add(f"{words[i]} {words[i + 1]}"):
            return triggers[:_MAX_TRIGGERS]
    for w in words:
        if _add(w):
            break
    return triggers[:_MAX_TRIGGERS]


# ── conversation summarisation ───────────────────────────────────────────────

def _summarize_messages(messages: Iterable[Any]) -> tuple[str, list[str]]:
    """Bounded summary (<=20 lines) plus <=5 user example prompts."""
    lines: list[str] = []
    examples: list[str] = []
    for msg in messages or []:
        content = (getattr(msg, "content", "") or "").strip()
        if not content:
            continue
        role = getattr(msg, "role", "user")
        label = "User" if role == "user" else str(role).capitalize()
        lines.append(f"{label}: {content}")
        if role == "user" and len(examples) < _MAX_EXAMPLES:
            examples.append(content)
    if not lines:
        return "No conversation recorded.", []
    return "\n".join(lines[:_MAX_SUMMARY_LINES]), examples


# ── SKILL.md rendering ───────────────────────────────────────────────────────

def _build_skill_md(
    name: str,
    description: str,
    triggers: Iterable[str],
    conversation_summary: str,
    examples: Iterable[str],
) -> str:
    created = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = ["---", f"name: {name}", f"description: {description}"]
    trigger_list = list(triggers or [])
    if trigger_list:
        parts.append("triggers:")
        parts.extend(f"  - {t}" for t in trigger_list)
    parts.append("---")
    parts.append("")
    parts.append(f"# {name}")
    parts.append("")
    parts.append(f"Created at: {created}")
    parts.append("")
    parts.append("## Workflow steps")
    parts.append("")
    parts.append(conversation_summary or "No conversation recorded.")
    example_list = list(examples or [])
    if example_list:
        parts.append("")
        parts.append("## Example requests")
        parts.append("")
        parts.extend(f"- {e}" for e in example_list)
    parts.append("")
    return "\n".join(parts)


# ── atomic write ─────────────────────────────────────────────────────────────

def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _do_create(name: str, description: str, messages: Any = None) -> str:
    """Create/update a skill. Returns a '✓ …' or '✗ …' status string."""
    slug = _slugify(name or "")
    if not slug:
        return "✗ Invalid skill name: cannot be emptied to a safe slug."

    summary, examples = _summarize_messages(messages or [])
    md = _build_skill_md(
        name=slug,
        description=description or "",
        triggers=_extract_triggers(description or "", slug),
        conversation_summary=summary,
        examples=examples,
    )
    target_dir = SKILLS_DIR / slug
    with _WRITE_LOCK:
        _atomic_write(target_dir / _SKILL_FILE, md)
    return f"✓ Created skill '{slug}' at {(target_dir / _SKILL_FILE)}"


# ── bundled bootstrap + listing ──────────────────────────────────────────────

def _bootstrap_bundled_skills() -> None:
    """Copy packaged skills into SKILLS_DIR so listing works offline."""
    if not _BUNDLED_SKILLS_DIR.is_dir():
        return
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    for skill_md in sorted(_BUNDLED_SKILLS_DIR.rglob(_SKILL_FILE)):
        src_dir = skill_md.parent
        dest = SKILLS_DIR / src_dir.name
        if dest.exists():
            continue
        try:
            shutil.copytree(src_dir, dest, dirs_exist_ok=True)
        except OSError:
            # A broken bundled asset must never break the CLI.
            continue


def _parse_description(skill_md: Path) -> str:
    try:
        in_frontmatter = False
        for line in skill_md.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped == "---":
                if in_frontmatter:
                    break
                in_frontmatter = True
                continue
            if in_frontmatter and stripped.startswith("description:"):
                return stripped.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def list_local_skills() -> list[dict[str, str]]:
    """List installed skills as {name, description, path}; bootstrap if fresh."""
    if not SKILLS_DIR.exists():
        _bootstrap_bundled_skills()
    items: list[dict[str, str]] = []
    if not SKILLS_DIR.is_dir():
        return items
    for entry in sorted(SKILLS_DIR.iterdir()):
        skill_file = entry / _SKILL_FILE
        if entry.is_dir() and skill_file.is_file():
            items.append(
                {
                    "name": entry.name,
                    "description": _parse_description(skill_file),
                    "path": str(skill_file),
                }
            )
    return items


def create_skill_interactive(name: str, description: str) -> str:
    """Programmatic creation entry point shared by the TUI and CLI."""
    return _do_create(name, description, messages=[])


def create_skill_from_conversation(name: str, description: str, messages: Any = None) -> str:
    """Create a skill from conversation context and persist to disk."""
    return _do_create(name, description, messages=messages)


def get_skills_state() -> dict[str, bool]:
    """Return {skill_name: is_enabled} mapping from disk."""
    state_file = SKILLS_DIR / "skills_state.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def set_skill_enabled(name: str, enabled: bool) -> None:
    """Persist skill enabled/disabled state to disk."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    state_file = SKILLS_DIR / "skills_state.json"
    data = get_skills_state()
    data[name] = enabled
    state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
