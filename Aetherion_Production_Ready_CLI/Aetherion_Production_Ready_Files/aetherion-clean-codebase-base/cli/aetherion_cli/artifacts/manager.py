"""
aetherion_cli/artifacts/manager.py
Artifact listing, inspection, SHA-256 integrity validation, and path-safe downloads.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..api.client import ApiClient
from ..api.contracts import ArtifactInfo


class ArtifactManager:
    """Manages artifact operations with integrity checking and path safety."""

    def __init__(self, client: Optional[ApiClient] = None):
        self.client = client or ApiClient()

    def list_artifacts(self, session_id: Optional[str] = None) -> List[ArtifactInfo]:
        """List artifacts from the backend."""
        return self.client.list_artifacts(session_id=session_id)

    def download_artifact(
        self,
        artifact_id: str,
        destination_dir: Optional[Path] = None,
        expected_checksum: Optional[str] = None,
    ) -> Path:
        """Download artifact and safely write to disk."""
        target_dir = destination_dir or Path.cwd()
        target_dir.mkdir(parents=True, exist_ok=True)

        url = f"/api/artifacts/{artifact_id}/download"
        raw_bytes = self.client.get(url)
        if isinstance(raw_bytes, str):
            content = raw_bytes.encode("utf-8")
        elif isinstance(raw_bytes, bytes):
            content = raw_bytes
        elif isinstance(raw_bytes, dict):
            # Fallback if server returned json metadata
            content = str(raw_bytes.get("data", "")).encode("utf-8")
        else:
            content = b""

        # Verify integrity if checksum provided
        if expected_checksum:
            actual_sha = hashlib.sha256(content).hexdigest()
            if actual_sha != expected_checksum:
                raise ValueError(
                    f"Checksum mismatch for artifact {artifact_id}! "
                    f"Expected {expected_checksum}, got {actual_sha}"
                )

        # Path-safe filename sanitization
        safe_name = f"artifact_{artifact_id.replace('/', '_').replace('\\', '_')}"
        dest_path = (target_dir / safe_name).resolve()

        # Prevent directory traversal
        if not str(dest_path).startswith(str(target_dir.resolve())):
            raise ValueError(f"Illegal path traversal attempt: {dest_path}")

        dest_path.write_bytes(content)
        return dest_path
