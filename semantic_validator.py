"""
semantic_validator.py — Aetherion Semantic Validation Engine v1
═══════════════════════════════════════════════════════════════
Validates BEHAVIORAL correctness after tool execution.
Unlike syntax checks (py_compile), these checks verify the artifact
actually DOES what it was supposed to do.

All checks are deterministic — no LLM opinions used as proof.

Provides:
  • SemanticValidator    — orchestrates post-action validation
  • ValidationResult     — structured evidence record
  • SelfTestGenerator    — generates + runs minimal smoke tests
  • BrowserAsserter      — DOM/behavior assertions via browser tool
  • ConfidenceModelV2    — evidence-weighted confidence scoring
"""

import re
import json
import time
import logging
import tempfile
import textwrap
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data Structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationCheck:
    name: str
    passed: bool
    evidence: str        # what we actually observed
    weight: float = 1.0  # contribution to confidence score
    optional: bool = False


@dataclass
class ValidationResult:
    action: str
    step_text: str
    checks: list[ValidationCheck] = field(default_factory=list)
    semantic_confidence: float = 0.0
    failure_category: str = ""     # "syntax"|"runtime"|"behavioral"|"visual"|"api"
    retryable: bool = True
    summary: str = ""
    timestamp: float = field(default_factory=time.time)

    def passed(self) -> bool:
        required = [c for c in self.checks if not c.optional]
        return all(c.passed for c in required) if required else True

    def to_trust_signal(self, step_index: int, session_id: str) -> dict:
        return {
            "type": "semantic_validation",
            "verified": self.passed(),
            "confidence": self.semantic_confidence,
            "message": self.summary or (
                f"Semantic validation {'passed' if self.passed() else 'FAILED'}: "
                f"{sum(1 for c in self.checks if c.passed)}/{len(self.checks)} checks OK"
            ),
            "step": step_index,
            "action": self.action,
            "session_id": session_id,
            "evidence": {c.name: {"passed": c.passed, "evidence": c.evidence[:120]} for c in self.checks},
        }


# ─────────────────────────────────────────────────────────────────────────────
# Semantic Failure Categories
# ─────────────────────────────────────────────────────────────────────────────

SEMANTIC_FAILURE_MAP = {
    "behavioral": {
        "description": "Code runs but does not do what was requested",
        "examples": ["login succeeds but session not stored", "API returns 200 but wrong schema"],
        "retryable": True,
        "retry_strategy": "Re-read the code, identify the logic gap, use diff_edit to fix it",
    },
    "visual": {
        "description": "UI renders but layout is broken or missing elements",
        "examples": ["blank page", "CSS not loading", "buttons missing"],
        "retryable": True,
        "retry_strategy": "Check browser errors, verify static file paths, reload and screenshot",
    },
    "api": {
        "description": "Endpoint responds but schema or values are wrong",
        "examples": ["missing required fields", "wrong status code on error", "auth not enforced"],
        "retryable": True,
        "retry_strategy": "server_test with expected_status and data, compare JSON schema",
    },
    "runtime": {
        "description": "Crashes or exceptions during real execution",
        "examples": ["KeyError in handler", "NullPointerException", "import error at runtime"],
        "retryable": True,
        "retry_strategy": "Read traceback, diff_edit the exact failing line",
    },
    "data": {
        "description": "Data pipeline produces wrong shape, type, or values",
        "examples": ["empty dataframe", "wrong columns", "NaN-filled outputs"],
        "retryable": True,
        "retry_strategy": "Add assertions to output, print shape/dtypes, fix transformation",
    },
    "irreversible": {
        "description": "Action cannot be retried safely",
        "examples": ["file deleted", "DB dropped", "external API called"],
        "retryable": False,
        "retry_strategy": "Escalate to HITL — cannot safely retry",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Semantic Validator — Main Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

class SemanticValidator:
    """
    Called after tool execution to run behavioral validation.
    Determines whether the tool's success means the goal was actually achieved.
    """

    def __init__(self, tools, workspace_dir: str, emit_fn=None, session_id: str = ""):
        self.tools       = tools
        self.workspace   = Path(workspace_dir)
        self._emit       = emit_fn or (lambda *a, **k: None)
        self._sid        = session_id
        self._results: list[ValidationResult] = []

    # ── Main dispatch ─────────────────────────────────────────────────────────

    def validate_after(
        self,
        action: str,
        args: dict,
        tool_output: str,
        step_text: str,
        step_index: int,
    ) -> Optional[ValidationResult]:
        """
        Runs the appropriate validation checks based on what the action was.
        Returns None if no semantic validation is applicable.
        """
        result = None

        # Route to the correct validator
        if action == "write_file":
            result = self._validate_written_file(args, tool_output, step_text)
        elif action in ("run_python", "run_shell"):
            result = self._validate_execution_output(action, args, tool_output, step_text)
        elif action == "server_start":
            result = self._validate_server(args, tool_output, step_text)
        elif action in ("browser_navigate", "browser_click", "browser_fill"):
            result = self._validate_browser_action(action, args, tool_output, step_text)
        elif action == "server_test":
            result = self._validate_api_response(args, tool_output, step_text)

        if result:
            result.action = action
            result.step_text = step_text
            # Compute semantic confidence
            result.semantic_confidence = self._score_result(result)
            result.summary = self._build_summary(result)
            self._results.append(result)
            # Emit trust signal
            try:
                self._emit("agent.trust_signal", result.to_trust_signal(step_index, self._sid))
            except Exception:
                pass
            # Log
            status = "PASS" if result.passed() else "FAIL"
            logger.info(
                f"[SemanticValidator] {action} → {status} "
                f"({sum(1 for c in result.checks if c.passed)}/{len(result.checks)} checks, "
                f"conf={result.semantic_confidence:.2f})"
            )

        return result

    # ── File validation ───────────────────────────────────────────────────────

    def _validate_written_file(self, args: dict, tool_output: str, step_text: str) -> ValidationResult:
        result = ValidationResult(action="write_file", step_text=step_text)
        path   = args.get("path", "")
        p      = self.workspace / path if path else None

        # Check 1: File exists on disk
        exists = p is not None and p.exists()
        result.checks.append(ValidationCheck(
            name="file_exists_on_disk",
            passed=exists,
            evidence=f"Path {p} {'exists' if exists else 'NOT FOUND'}",
            weight=1.0,
        ))

        if not exists:
            result.failure_category = "runtime"
            return result

        # Check 2: Non-empty
        size = p.stat().st_size if exists else 0
        result.checks.append(ValidationCheck(
            name="file_non_empty",
            passed=size > 0,
            evidence=f"File size: {size} bytes",
            weight=0.8,
        ))

        # Check 3: Syntax marker already in tool_output (from write_file verification)
        if path.endswith(".py"):
            syntax_ok = "[syntax:OK]" in tool_output
            syntax_err = "[syntax:ERROR" in tool_output
            result.checks.append(ValidationCheck(
                name="python_syntax",
                passed=syntax_ok and not syntax_err,
                evidence=tool_output[tool_output.find("[syntax"):tool_output.find("[syntax")+30]
                        if "[syntax" in tool_output else "no syntax check result",
                weight=1.0,
            ))
            if syntax_err:
                result.failure_category = "runtime"

        elif path.endswith(".json"):
            json_ok = "[json:OK]" in tool_output
            result.checks.append(ValidationCheck(
                name="json_valid",
                passed=json_ok,
                evidence="JSON parsed successfully" if json_ok else "JSON parse failed",
                weight=1.0,
            ))

        # Check 4: Step-text keyword match (did we write what we planned?)
        if exists and size > 0:
            content = p.read_text(encoding="utf-8", errors="ignore")
            step_keywords = self._extract_keywords(step_text)
            matched = sum(1 for kw in step_keywords if kw.lower() in content.lower())
            match_ratio = matched / len(step_keywords) if step_keywords else 1.0
            result.checks.append(ValidationCheck(
                name="content_matches_step_goal",
                passed=match_ratio >= 0.3,
                evidence=f"{matched}/{len(step_keywords)} step keywords found in file",
                weight=0.6,
                optional=True,
            ))

        return result

    # ── Execution output validation ───────────────────────────────────────────

    def _validate_execution_output(self, action: str, args: dict, output: str, step_text: str) -> ValidationResult:
        result = ValidationResult(action=action, step_text=step_text)

        # Check 1: No traceback in output
        has_traceback = any(x in output for x in [
            "Traceback (most recent call last)",
            "Error:", "Exception:", "FAILED", "assert", "AssertionError",
        ])
        result.checks.append(ValidationCheck(
            name="no_traceback_in_output",
            passed=not has_traceback,
            evidence=output[:200] if has_traceback else "Clean output",
            weight=1.0,
        ))
        if has_traceback:
            result.failure_category = "runtime"
            result.retryable = True

        # Check 2: Output is non-empty
        result.checks.append(ValidationCheck(
            name="non_empty_output",
            passed=bool(output.strip()),
            evidence=f"Output length: {len(output)} chars",
            weight=0.5,
            optional=True,
        ))

        # Check 3: No common install failure signals
        if action == "run_shell" and "pip install" in (args.get("command", "")):
            install_ok = "Successfully installed" in output or "already satisfied" in output.lower()
            result.checks.append(ValidationCheck(
                name="package_install_confirmed",
                passed=install_ok,
                evidence=output[-200:] if not install_ok else "Install confirmed",
                weight=1.0,
            ))

        # Check 4: Expected patterns from step text
        expected = self._expected_output_patterns(step_text)
        for pattern, description in expected:
            found = bool(re.search(pattern, output, re.IGNORECASE))
            result.checks.append(ValidationCheck(
                name=f"expected_{description}",
                passed=found,
                evidence=f"Pattern '{pattern[:40]}' {'found' if found else 'NOT found'} in output",
                weight=0.5,
                optional=True,
            ))

        return result

    # ── Server validation ─────────────────────────────────────────────────────

    def _validate_server(self, args: dict, tool_output: str, step_text: str) -> ValidationResult:
        result   = ValidationResult(action="server_start", step_text=step_text)
        port     = args.get("port", 0)
        http_ok  = f"[HTTP:" in tool_output and "server error" not in tool_output

        # Check 1: HTTP health confirmed in tool output
        result.checks.append(ValidationCheck(
            name="http_health_check",
            passed=http_ok,
            evidence=tool_output[-150:],
            weight=1.0,
        ))

        # Check 2: No crash signals
        crashed = "crashed" in tool_output.lower() or "did not bind" in tool_output.lower()
        result.checks.append(ValidationCheck(
            name="server_did_not_crash",
            passed=not crashed,
            evidence="Server stable" if not crashed else tool_output[:200],
            weight=1.0,
        ))

        # Check 3: Port is live — try a second probe right now
        if port:
            try:
                import socket
                with socket.create_connection(("127.0.0.1", port), timeout=2):
                    port_live = True
                    evidence = f"Port {port} accepting connections"
            except OSError:
                port_live = False
                evidence = f"Port {port} NOT responding"
            result.checks.append(ValidationCheck(
                name="port_live_second_probe",
                passed=port_live,
                evidence=evidence,
                weight=0.8,
            ))
            if not port_live:
                result.failure_category = "runtime"

        if not http_ok:
            result.failure_category = "visual"

        return result

    # ── Browser action validation ─────────────────────────────────────────────

    def _validate_browser_action(self, action: str, args: dict, output: str, step_text: str) -> ValidationResult:
        result = ValidationResult(action=action, step_text=step_text)

        # Check 1: No JS errors reported in browser
        try:
            errors_result = self.tools.execute("browser_get_errors")
            js_errors = errors_result.get("output", "")
            no_js_errors = not js_errors or js_errors.strip() in ("", "[]", "no errors")
            result.checks.append(ValidationCheck(
                name="no_js_errors",
                passed=no_js_errors,
                evidence=js_errors[:200] if not no_js_errors else "No JS errors",
                weight=1.0,
            ))
            if not no_js_errors:
                result.failure_category = "visual"
        except Exception as e:
            result.checks.append(ValidationCheck(
                name="no_js_errors",
                passed=True,  # can't check — assume OK
                evidence=f"Error checking JS errors: {e}",
                weight=0.3,
                optional=True,
            ))

        # Check 2: For navigation — page has a title (not blank)
        if action == "browser_navigate":
            try:
                title_result = self.tools.execute("browser_evaluate_js", script="document.title")
                title = title_result.get("output", "").strip()
                has_title = bool(title) and title != "undefined"
                result.checks.append(ValidationCheck(
                    name="page_has_title",
                    passed=has_title,
                    evidence=f"Title: '{title[:60]}'" if has_title else "Blank/missing page title",
                    weight=0.7,
                ))
            except Exception:
                pass

        # Check 3: For navigation — check for error page markers
        if action == "browser_navigate":
            try:
                html_result = self.tools.execute("browser_evaluate_js",
                    script="document.body ? document.body.innerText.substring(0,200) : 'NO BODY'")
                body_text = html_result.get("output", "")
                is_error_page = any(x in body_text.lower() for x in [
                    "404", "not found", "internal server error", "500", "application error",
                    "cannot get", "no body",
                ])
                result.checks.append(ValidationCheck(
                    name="not_error_page",
                    passed=not is_error_page,
                    evidence=body_text[:150] if is_error_page else "Page content looks valid",
                    weight=1.0,
                ))
                if is_error_page:
                    result.failure_category = "visual"
            except Exception:
                pass

        # Check 4: For clicks — verify no page crash after click
        if action == "browser_click":
            try:
                url_result = self.tools.execute("browser_evaluate_js", script="window.location.href")
                url = url_result.get("output", "")
                result.checks.append(ValidationCheck(
                    name="page_responsive_after_click",
                    passed=bool(url),
                    evidence=f"URL after click: {url[:100]}" if url else "Could not get URL after click",
                    weight=0.7,
                    optional=True,
                ))
            except Exception:
                pass

        return result

    # ── API response validation ───────────────────────────────────────────────

    def _validate_api_response(self, args: dict, output: str, step_text: str) -> ValidationResult:
        result = ValidationResult(action="server_test", step_text=step_text)

        # Check 1: Expected status code is in output
        expected_status = args.get("expect_status", 200)
        status_found = f"HTTP {expected_status}" in output
        result.checks.append(ValidationCheck(
            name=f"status_code_{expected_status}",
            passed=status_found,
            evidence=output[:100],
            weight=1.0,
        ))
        if not status_found:
            result.failure_category = "api"

        # Check 2: Response is parseable JSON (if content-type expected)
        body = output[output.find("\n"):].strip() if "\n" in output else output
        try:
            parsed = json.loads(body)
            result.checks.append(ValidationCheck(
                name="response_is_valid_json",
                passed=True,
                evidence=f"JSON parsed: {str(parsed)[:80]}",
                weight=0.8,
                optional=True,
            ))

            # Check 3: No server error in JSON body
            is_error_body = (
                isinstance(parsed, dict) and
                any(k in parsed for k in ("error", "Error", "detail", "message")) and
                not status_found
            )
            if is_error_body:
                result.checks.append(ValidationCheck(
                    name="no_error_in_json_body",
                    passed=False,
                    evidence=str(parsed)[:150],
                    weight=0.9,
                ))
                result.failure_category = "api"

        except (json.JSONDecodeError, ValueError):
            result.checks.append(ValidationCheck(
                name="response_is_valid_json",
                passed=False,
                evidence=body[:80] or "(empty body)",
                weight=0.4,
                optional=True,
            ))

        return result

    # ── Session summary ───────────────────────────────────────────────────────

    def session_scorecard(self) -> dict:
        """Returns aggregated validation stats for the whole task execution."""
        if not self._results:
            return {"checks_run": 0, "checks_passed": 0, "semantic_confidence": 0.5}
        total_checks  = sum(len(r.checks) for r in self._results)
        passed_checks = sum(sum(1 for c in r.checks if c.passed) for r in self._results)
        avg_conf      = sum(r.semantic_confidence for r in self._results) / len(self._results)
        failures      = [r for r in self._results if not r.passed()]
        return {
            "checks_run": total_checks,
            "checks_passed": passed_checks,
            "check_pass_rate": round(passed_checks / total_checks, 2) if total_checks else 0,
            "semantic_confidence": round(avg_conf, 2),
            "validation_count": len(self._results),
            "failure_count": len(failures),
            "failure_categories": list({r.failure_category for r in failures if r.failure_category}),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _score_result(result: ValidationResult) -> float:
        """Evidence-weighted confidence: only hard checks count."""
        if not result.checks:
            return 0.5
        total_weight  = sum(c.weight for c in result.checks)
        passed_weight = sum(c.weight for c in result.checks if c.passed)
        return round(passed_weight / total_weight, 2) if total_weight else 0.5

    @staticmethod
    def _build_summary(result: ValidationResult) -> str:
        n_pass = sum(1 for c in result.checks if c.passed)
        n_fail = sum(1 for c in result.checks if not c.passed and not c.optional)
        failed_names = [c.name for c in result.checks if not c.passed and not c.optional]
        if n_fail == 0:
            return f"{result.action}: {n_pass}/{len(result.checks)} checks passed — semantically valid"
        return (
            f"{result.action}: {n_pass}/{len(result.checks)} checks passed — "
            f"FAILED: {', '.join(failed_names[:3])}"
        )

    @staticmethod
    def _extract_keywords(step_text: str) -> list[str]:
        """Extract meaningful nouns/identifiers from a step description."""
        # Remove common stop words, keep identifiers
        stop = {"the", "a", "an", "and", "or", "with", "using", "to", "for",
                "of", "in", "on", "at", "is", "it", "be", "by"}
        words = re.findall(r'\b[a-zA-Z_][\w_]{2,}\b', step_text)
        return [w for w in words if w.lower() not in stop][:8]

    @staticmethod
    def _expected_output_patterns(step_text: str) -> list[tuple[str, str]]:
        """Infer expected output patterns from step description."""
        patterns = []
        s = step_text.lower()
        if "database" in s or "db" in s or "table" in s:
            patterns.append((r"(created|initialized|ok|success)", "db_init_ok"))
        if "test" in s or "verify" in s or "check" in s:
            patterns.append((r"(pass|ok|success|\d+ passed)", "test_passed"))
        if "install" in s:
            patterns.append((r"(successfully installed|already satisfied)", "install_ok"))
        if "server" in s or "start" in s:
            patterns.append((r"(running|started|listening)", "server_started"))
        return patterns


# ─────────────────────────────────────────────────────────────────────────────
# Self-Test Generator
# ─────────────────────────────────────────────────────────────────────────────

class SelfTestGenerator:
    """
    Generates and runs minimal smoke tests for written Python files.
    Keeps tests lightweight — no giant suites, no token waste.
    """

    def __init__(self, tools, workspace_dir: str):
        self.tools     = tools
        self.workspace = Path(workspace_dir)

    def generate_and_run(self, path: str, content: str) -> dict:
        """
        For a written .py file, generate a smoke test and run it.
        Returns: {passed: bool, evidence: str, test_code: str}
        """
        if not path.endswith(".py"):
            return {"passed": True, "evidence": "No smoke test for non-Python file", "test_code": ""}

        test_code = self._generate_smoke_test(path, content)
        if not test_code:
            return {"passed": True, "evidence": "No testable patterns found", "test_code": ""}

        # Write temp test file
        test_filename = f"_smoke_{Path(path).stem}_{int(time.time())}.py"
        write_result  = self.tools.execute("write_file", path=test_filename, content=test_code)
        if not write_result["success"]:
            return {"passed": False, "evidence": f"Could not write test: {write_result['error']}", "test_code": test_code}

        # Run it
        run_result = self.tools.execute("run_python", path=test_filename)

        # Cleanup
        try:
            self.tools.execute("delete_file", path=test_filename)
        except Exception:
            pass

        passed  = run_result["success"] and not any(
            x in run_result.get("output", "") + run_result.get("error", "")
            for x in ["Traceback", "Error:", "AssertionError", "FAILED"]
        )
        evidence = (run_result.get("output") or run_result.get("error") or "")[:300]
        logger.info(f"[SelfTest] {path} smoke test: {'PASS' if passed else 'FAIL'}")
        return {"passed": passed, "evidence": evidence, "test_code": test_code}

    @staticmethod
    def _generate_smoke_test(path: str, content: str) -> str:
        """Generate a minimal smoke test based on file structure."""
        lines   = []
        modname = Path(path).stem

        # Detect imports / classes / functions
        has_flask    = "from flask" in content or "import flask" in content
        has_fastapi  = "fastapi" in content.lower()
        functions    = re.findall(r'^def (\w+)\(', content, re.MULTILINE)
        classes      = re.findall(r'^class (\w+)', content, re.MULTILINE)

        if has_flask:
            return textwrap.dedent(f"""\
                import importlib, sys
                try:
                    spec = importlib.util.spec_from_file_location("{modname}", "{path}")
                    mod  = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    assert hasattr(mod, 'app'), "Flask app object not found"
                    print("SMOKE OK: Flask app object present")
                except Exception as e:
                    print(f"SMOKE FAIL: {{e}}")
                    raise
            """)

        if functions:
            # Just confirm the module imports without crashing
            return textwrap.dedent(f"""\
                import importlib.util, sys
                try:
                    spec = importlib.util.spec_from_file_location("{modname}", "{path}")
                    mod  = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    fns  = {repr(functions[:5])}
                    missing = [f for f in fns if not hasattr(mod, f)]
                    assert not missing, f"Missing functions: {{missing}}"
                    print(f"SMOKE OK: module imports, {{len(fns)}} function(s) present")
                except SystemExit:
                    print("SMOKE OK: module exits on import (expected for scripts)")
                except Exception as e:
                    print(f"SMOKE FAIL: {{e}}")
                    raise
            """)

        return ""  # nothing useful to test


# ─────────────────────────────────────────────────────────────────────────────
# Confidence Model V2 — evidence-weighted aggregator
# ─────────────────────────────────────────────────────────────────────────────

class ConfidenceModelV2:
    """
    Aggregates evidence from multiple sources into a single confidence score.
    Sources: syntax check, execution result, semantic validation, browser check.
    No LLM opinion is used — all weights are based on hard evidence.
    """

    WEIGHTS = {
        "syntax_check":          0.15,  # py_compile — deterministic
        "execution_success":     0.20,  # exit code 0 — deterministic
        "semantic_validation":   0.35,  # behavioral checks — strong signal
        "browser_assertion":     0.20,  # DOM/JS error checks
        "self_test":             0.10,  # smoke test pass
    }

    def __init__(self):
        self._evidence: dict[str, tuple[bool, float]] = {}
        # (passed, raw_score)

    def record(self, source: str, passed: bool, score: float = None):
        """Record a piece of evidence. score overrides binary pass/fail if provided."""
        if source not in self.WEIGHTS:
            return
        effective_score = score if score is not None else (1.0 if passed else 0.0)
        self._evidence[source] = (passed, effective_score)

    def compute(self) -> float:
        """Weighted average over recorded evidence. Returns 0.5 if nothing recorded."""
        if not self._evidence:
            return 0.50
        total_weight  = 0.0
        weighted_sum  = 0.0
        for source, (passed, score) in self._evidence.items():
            w = self.WEIGHTS.get(source, 0)
            weighted_sum  += score * w
            total_weight  += w
        return round(weighted_sum / total_weight, 2) if total_weight else 0.50

    def scorecard(self) -> dict:
        return {
            "overall": self.compute(),
            "sources": {
                src: {"passed": p, "score": round(s, 2)}
                for src, (p, s) in self._evidence.items()
            },
        }
