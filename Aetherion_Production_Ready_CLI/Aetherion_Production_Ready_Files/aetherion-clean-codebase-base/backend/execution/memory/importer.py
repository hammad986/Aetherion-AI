"""
backend/execution/memory/importer.py — Multi-Format Memory Importer with Preview and Deduplication.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .models import MemoryEntry, MemorySource, MemoryStatus, MemoryTrust, MemoryType
from .store import MemoryStore

logger = logging.getLogger("aetherion.memory.importer")

MAX_IMPORT_TEXT_SIZE = 2 * 1024 * 1024  # 2MB cap
MAX_IMPORT_ENTRIES = 100


class MemoryImportException(Exception):
    """Raised when memory import content is malformed, oversized, or unparseable."""
    pass


class MemoryImporter:
    """Parses, previews, normalizes, deduplicates, and commits external memory sources."""

    def __init__(self, store: Optional[MemoryStore] = None):
        self.store = store or MemoryStore()
        self._previews: Dict[str, Dict[str, Any]] = {}

    def preview_import(
        self,
        raw_data: str | bytes | Dict[str, Any] | List[Any],
        owner_id: str,
        source_format: str = "auto",  # auto, json, markdown, text
    ) -> Dict[str, Any]:
        """Parse raw content into candidate MemoryEntry objects and produce an inspection preview."""
        if isinstance(raw_data, bytes):
            raw_text = raw_data.decode("utf-8", errors="replace")
        elif isinstance(raw_data, str):
            raw_text = raw_data
        elif isinstance(raw_data, (dict, list)):
            raw_text = json.dumps(raw_data)
        else:
            raise MemoryImportException(f"Unsupported data type for memory import: {type(raw_data)}")

        if len(raw_text) > MAX_IMPORT_TEXT_SIZE:
            raise MemoryImportException(
                f"Import payload exceeds maximum allowed size of {MAX_IMPORT_TEXT_SIZE // 1024}KB."
            )

        # Detect format
        fmt = source_format.lower()
        if fmt == "auto":
            trimmed = raw_text.strip()
            if (trimmed.startswith("{") and trimmed.endswith("}")) or (trimmed.startswith("[") and trimmed.endswith("]")):
                fmt = "json"
            elif "# " in trimmed or "---" in trimmed or "\n- " in trimmed:
                fmt = "markdown"
            else:
                fmt = "text"

        parsed_items: List[Dict[str, Any]] = []
        if fmt == "json":
            parsed_items = self._parse_json(raw_text)
        elif fmt == "markdown":
            parsed_items = self._parse_markdown(raw_text)
        else:
            parsed_items = self._parse_text(raw_text)

        if not parsed_items:
            raise MemoryImportException("No valid memory entries could be extracted from the provided source.")

        if len(parsed_items) > MAX_IMPORT_ENTRIES:
            parsed_items = parsed_items[:MAX_IMPORT_ENTRIES]

        # Check duplicates against store
        candidates: List[Dict[str, Any]] = []
        duplicate_count = 0

        for item in parsed_items:
            title = (item.get("title") or "Imported Note").strip()
            content = (item.get("content") or "").strip()
            if not content:
                continue

            existing = self.store.find_duplicate(owner_id, content, title)
            is_dup = existing is not None
            if is_dup:
                duplicate_count += 1

            candidates.append({
                "temp_id": f"temp_{uuid.uuid4().hex[:8]}",
                "title": title,
                "content": content,
                "type": item.get("type", MemoryType.IMPORTED_MEMORY.value),
                "source": item.get("source", MemorySource.IMPORTED_FILE.value),
                "trust_level": MemoryTrust.IMPORTED.value,
                "importance": float(item.get("importance", 0.7)),
                "tags": item.get("tags") or ["imported"],
                "is_duplicate": is_dup,
                "duplicate_of": existing.id if existing else None,
            })

        preview_id = f"prev_mem_{uuid.uuid4().hex[:10]}"
        preview_data = {
            "preview_id": preview_id,
            "owner_id": str(owner_id),
            "format": fmt,
            "total_extracted": len(candidates),
            "duplicate_count": duplicate_count,
            "entries": candidates,
        }
        self._previews[preview_id] = preview_data
        return preview_data

    def commit_import(
        self,
        preview_id_or_entries: str | List[Dict[str, Any]],
        owner_id: str,
        skip_duplicates: bool = True,
    ) -> List[MemoryEntry]:
        """Commit previewed entries into persistent storage."""
        if isinstance(preview_id_or_entries, str):
            preview = self._previews.get(preview_id_or_entries)
            if not preview:
                raise MemoryImportException(f"Preview '{preview_id_or_entries}' not found or expired.")
            entries = preview["entries"]
        else:
            entries = preview_id_or_entries

        committed: List[MemoryEntry] = []
        for e in entries:
            if skip_duplicates and e.get("is_duplicate"):
                continue

            # Ensure trust level remains IMPORTED
            entry = MemoryEntry(
                owner_id=str(owner_id),
                type=e.get("type", MemoryType.IMPORTED_MEMORY.value),
                title=e.get("title", "Imported Note"),
                content=e.get("content", ""),
                source=e.get("source", MemorySource.IMPORTED_FILE.value),
                trust_level=MemoryTrust.IMPORTED.value,
                importance=float(e.get("importance", 0.7)),
                tags=e.get("tags") or ["imported"],
            )
            saved = self.store.create_memory(entry)
            committed.append(saved)

        if isinstance(preview_id_or_entries, str):
            self._previews.pop(preview_id_or_entries, None)

        return committed

    # ── Parsing Strategies ───────────────────────────────────────────────────

    def _parse_json(self, raw_text: str) -> List[Dict[str, Any]]:
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as jde:
            raise MemoryImportException(f"Invalid JSON in memory import: {jde}")

        results: List[Dict[str, Any]] = []
        if isinstance(data, list):
            for i, item in enumerate(data):
                if isinstance(item, dict):
                    results.append(self._normalize_item(item, f"Imported Entry {i+1}"))
                elif isinstance(item, str) and item.strip():
                    results.append({
                        "title": f"Note {i+1}",
                        "content": item.strip(),
                        "type": MemoryType.IMPORTED_MEMORY.value,
                        "source": MemorySource.IMPORTED_AI_EXPORT.value,
                    })
        elif isinstance(data, dict):
            # Check for ChatGPT / Claude conversation export structure
            if "mapping" in data or "messages" in data:
                # Conversation structure
                msgs = data.get("messages", [])
                if not msgs and "mapping" in data:
                    msgs = [v.get("message") for v in data["mapping"].values() if v.get("message")]
                for i, m in enumerate(msgs):
                    if not m:
                        continue
                    author = m.get("author", {}).get("role", "unknown") if isinstance(m.get("author"), dict) else m.get("role", "unknown")
                    content_parts = m.get("content", {}).get("parts", []) if isinstance(m.get("content"), dict) else [m.get("content", "")]
                    text = " ".join(str(p) for p in content_parts if p).strip()
                    if text and len(text) > 10:
                        results.append({
                            "title": f"Chat {author.title()} Message {i+1}",
                            "content": text[:1000],
                            "type": MemoryType.IMPORTED_MEMORY.value,
                            "source": MemorySource.IMPORTED_AI_EXPORT.value,
                            "tags": ["chat-export", author],
                        })
            elif "memories" in data and isinstance(data["memories"], list):
                for i, item in enumerate(data["memories"]):
                    if isinstance(item, dict):
                        results.append(self._normalize_item(item, f"Imported Memory {i+1}"))
            else:
                # Single memory dict or key-value summary
                results.append(self._normalize_item(data, data.get("title") or "Imported Data"))

        return results

    def _parse_markdown(self, raw_text: str) -> List[Dict[str, Any]]:
        # Check if text has sections separated by "# " or "## "
        sections = re.split(r"\n(?=#{1,3}\s+)", raw_text.strip())
        results: List[Dict[str, Any]] = []

        for i, sec in enumerate(sections):
            lines = sec.strip().splitlines()
            if not lines:
                continue

            first_line = lines[0].strip()
            if first_line.startswith("#"):
                title = first_line.lstrip("#").strip()
                content = "\n".join(lines[1:]).strip()
            else:
                title = f"Document Section {i+1}"
                content = sec.strip()

            if content:
                results.append({
                    "title": title,
                    "content": content,
                    "type": MemoryType.KNOWLEDGE.value,
                    "source": MemorySource.IMPORTED_FILE.value,
                    "tags": ["markdown"],
                })

        # If no sections detected, treat entire text as single markdown document
        if not results and raw_text.strip():
            results.append({
                "title": "Imported Markdown Document",
                "content": raw_text.strip(),
                "type": MemoryType.KNOWLEDGE.value,
                "source": MemorySource.IMPORTED_FILE.value,
                "tags": ["markdown"],
            })

        return results

    def _parse_text(self, raw_text: str) -> List[Dict[str, Any]]:
        # Split on double newlines or bullet points
        blocks = re.split(r"\n\s*\n", raw_text.strip())
        results: List[Dict[str, Any]] = []

        for i, block in enumerate(blocks):
            clean = block.strip()
            if not clean:
                continue
            lines = clean.splitlines()
            title = lines[0][:60].strip()
            content = clean
            results.append({
                "title": title if len(title) > 3 else f"Note {i+1}",
                "content": content,
                "type": MemoryType.IMPORTED_MEMORY.value,
                "source": MemorySource.IMPORTED_TEXT.value,
                "tags": ["text-paste"],
            })

        return results

    def _normalize_item(self, item: Dict[str, Any], default_title: str) -> Dict[str, Any]:
        title = item.get("title") or item.get("name") or default_title
        content = item.get("content") or item.get("text") or item.get("value") or ""
        m_type = item.get("type", MemoryType.IMPORTED_MEMORY.value)
        if m_type not in {t.value for t in MemoryType}:
            m_type = MemoryType.IMPORTED_MEMORY.value

        return {
            "title": str(title)[:100],
            "content": str(content),
            "type": m_type,
            "source": MemorySource.IMPORTED_FILE.value,
            "importance": float(item.get("importance", item.get("score", 0.7))),
            "tags": item.get("tags") if isinstance(item.get("tags"), list) else ["imported"],
        }
