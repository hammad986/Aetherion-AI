"""
backend/execution/verification/engine.py — Task verification engine.
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

if TYPE_CHECKING:
    from ..sandbox.boundary import WorkspaceBoundary
    from ..tools.registry import AgentToolRegistry

logger = logging.getLogger("aetherion.verification.engine")

VERIFIED = "VERIFIED"
PARTIALLY_VERIFIED = "PARTIALLY VERIFIED"
NOT_VERIFIED = "NOT VERIFIED"
VERIFICATION_FAILED = "FAILED"


class VerificationEngine:
    """Verifies that requested coding tasks were actually completed and verified."""

    def __init__(self, boundary: Any, tool_registry: Any):
        self.boundary = boundary
        self.tools = tool_registry
        self.workspace_dir = Path(boundary.workspace_dir)

    def verify(
        self,
        task: str,
        files_created_or_edited: Sequence[str],
        test_command: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        requested_verification: Optional[str] = None,
        has_tool_failures: bool = False,
        criteria: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """Verify the state of the workspace against task requirements.

        Returns: (status, details_str, metadata_dict)
        where status is VERIFIED, PARTIALLY_VERIFIED, NOT_VERIFIED, or FAILED.
        """
        details: List[str] = []
        metadata: Dict[str, Any] = {
            "verified_files": [],
            "test_exit_code": None,
            "criteria_checked": bool(criteria),
        }
        crit = criteria or {}

        if requested_verification == VERIFICATION_FAILED or (has_tool_failures and not files_created_or_edited):
            return VERIFICATION_FAILED, "Command or tool execution failed with non-zero exit code.", metadata

        # 1. Determine mutation expectation
        expected_files = list(crit.get("expected_files", []))
        require_mutation = crit.get("require_mutation", None)
        prompt_lower = task.lower()
        mutation_keywords = (
            "create", "write", "implement", "build", "fix", "code", "file", "test",
            "add", "update", "modify", "generate", "refactor", "patch", "script",
            "function", "class", "module", "bug", "repo", "program", "develop", "make", "solution"
        )
        task_expects_mutation = bool(expected_files) or (
            require_mutation is True or (require_mutation is not False and any(w in prompt_lower for w in mutation_keywords))
        )

        created_or_edited_set = set(files_created_or_edited or [])

        # FAIL-CLOSED: If task expects mutation but no files were created or edited:
        # Pre-existing repository files must NEVER satisfy a mutation expectation!
        if not files_created_or_edited:
            if has_tool_failures:
                return VERIFICATION_FAILED, "Tool execution failed and no files were produced.", metadata
            if task_expects_mutation:
                if expected_files:
                    return NOT_VERIFIED, f"Task expected creation or modification of {expected_files}, but no files were created or modified by the agent.", metadata
                return NOT_VERIFIED, "No files were created or modified for verification.", metadata
            # Read-only / informational task with no file modification required
            return VERIFIED, "Task completed successfully (no file modifications required).", metadata

        # 2. Files were created or edited by the agent
        files_to_check = list(files_created_or_edited)

        # If specific expected_files were required:
        # Every expected file MUST be part of files_created_or_edited. Pre-existing files in the worktree
        # that were not touched in this execution cannot satisfy an expected file!
        if expected_files:
            missing_expected = [ef for ef in expected_files if ef not in created_or_edited_set]
            if missing_expected:
                pre_existing_notes = []
                for mef in missing_expected:
                    if (self.workspace_dir / mef).exists():
                        pre_existing_notes.append(f"'{mef}' exists on disk but was not created/modified in this run (pre-existing files cannot satisfy mutation expectations)")
                    else:
                        pre_existing_notes.append(f"'{mef}' does not exist on disk")
                details.append("Expected file verification failed: " + "; ".join(pre_existing_notes))
                allow_partial = crit.get("allow_partial", False)
                if allow_partial and len(missing_expected) < len(expected_files):
                    return PARTIALLY_VERIFIED, "\n".join(details), metadata
                return NOT_VERIFIED, "\n".join(details), metadata

        # 3. Verify existence and integrity of all touched files
        all_files_exist = True
        for rf in files_to_check:
            fp = self.workspace_dir / rf
            if not fp.exists():
                all_files_exist = False
                details.append(f"Expected file '{rf}' does not exist on disk.")
            elif fp.stat().st_size == 0:
                all_files_exist = False
                details.append(f"File '{rf}' is empty (0 bytes).")
            else:
                if rf.endswith(".py"):
                    syn_res = self.tools.check_syntax(rf)
                    if not syn_res.ok:
                        return VERIFICATION_FAILED, f"Syntax verification failed for '{rf}': {syn_res.error}", metadata
                metadata["verified_files"].append(rf)
                details.append(f"File '{rf}' exists and verified ({fp.stat().st_size} bytes).")

        if not all_files_exist:
            return NOT_VERIFIED, "One or more target files failed existence or size verification: " + "; ".join(details), metadata

        # 2. Run automated tests if test files exist or test command provided
        test_files = [f for f in files_to_check if "test" in Path(f).name.lower()]
        cmd = crit.get("test_command") or test_command
        has_tests = bool(test_files or cmd)

        if crit.get("tests_required") and not has_tests:
            return VERIFICATION_FAILED, "Verification criteria required automated tests, but no test suite was executed.", metadata

        if has_tests:
            details.append("Running verification test suite...")
            if not cmd and test_files:
                cmd = f"{sys.executable} -m pytest -q {test_files[0]}"

            test_res = self.tools.run_tests(cmd=cmd, cancel_event=cancel_event)
            metadata["test_exit_code"] = test_res.exit_code
            metadata["test_stdout"] = test_res.stdout
            metadata["test_stderr"] = test_res.stderr

            if not test_res.ok:
                details.append(f"Test suite failed with exit code {test_res.exit_code}: {test_res.stderr or test_res.stdout}")
                return VERIFICATION_FAILED, "\n".join(details), metadata
            details.append(f"Test suite passed with exit code 0 ({cmd}).")

        # 3. Check expected output substring if specified in criteria
        if crit.get("expected_output"):
            target_sub = crit["expected_output"]
            found = False
            combined_out = (metadata.get("test_stdout") or "") + "\n" + (metadata.get("test_stderr") or "")
            if target_sub in combined_out:
                found = True
            else:
                for vf in metadata.get("verified_files", []):
                    try:
                        f_text = (self.workspace_dir / vf).read_text(encoding="utf-8", errors="replace")
                        if target_sub in f_text:
                            found = True
                            break
                    except Exception:
                        pass
            if not found:
                details.append(f"Expected output substring '{target_sub}' was not found in output or files.")
                return VERIFICATION_FAILED, "\n".join(details), metadata

        # 4. Check custom verifier if provided
        if crit.get("custom_verifier"):
            custom_fn = crit["custom_verifier"]
            if callable(custom_fn):
                try:
                    res = custom_fn(self.workspace_dir)
                    c_ok = res[0] if isinstance(res, tuple) else bool(res)
                    c_msg = res[1] if isinstance(res, tuple) and len(res) > 1 else ("Custom verifier passed" if c_ok else "Custom verifier returned False")
                    if not c_ok:
                        details.append(f"Custom verifier check failed: {c_msg}")
                        return VERIFICATION_FAILED, "\n".join(details), metadata
                    details.append(f"Custom verifier check passed: {c_msg}")
                except Exception as c_exc:
                    details.append(f"Custom verifier raised exception: {c_exc}")
                    return VERIFICATION_FAILED, "\n".join(details), metadata

        # Files verified
        prompt_lower = task.lower()
        if any(w in prompt_lower for w in ("test", "tests", "pytest", "unittest", "verify with")) and not has_tests:
            return PARTIALLY_VERIFIED, "\n".join(details) + "\nFiles verified. Tests were requested but no test suite was executed.", metadata
        return VERIFIED, "\n".join(details) + "\nFiles verified successfully.", metadata
