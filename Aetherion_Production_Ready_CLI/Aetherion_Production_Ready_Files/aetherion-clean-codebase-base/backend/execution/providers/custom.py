"""
backend/execution/providers/custom.py — Specialized, test, and generic fallback model providers.
"""
from __future__ import annotations

import logging
import sys
from typing import Any, Dict, List, Optional

from ..verification.engine import NOT_VERIFIED, VERIFICATION_FAILED, VERIFIED
from .base import BaseModelProvider, ModelResponse, ProviderCapabilities, ToolCall
from .openai import OpenAIRuntimeProvider

logger = logging.getLogger("aetherion.providers.custom")


class DeterministicTestProvider(BaseModelProvider):
    """Deterministic autonomous agent reasoning provider for tests and CI.

    Parses user requests and plans/executes genuine tool calls (inspect, write,
    test, repair, verify, finish) without requiring external cloud API keys.
    """

    def __init__(self, inject_failure_once: bool = False, force_verification_fail: bool = False):
        self.inject_failure_once = inject_failure_once
        self.force_verification_fail = force_verification_fail
        self._has_failed_once = False
        self._step = 0

    def name(self) -> str:
        return "deterministic_test_provider"

    def is_available(self) -> bool:
        return True

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_tools=True,
            supports_streaming=False,
            supports_system_messages=True,
            supports_json=True,
        )

    def health_check(self) -> Dict[str, Any]:
        return {
            "status": "CONNECTED",
            "latency_ms": 0.1,
            "diagnostics": "Deterministic test provider is local and operational.",
        }

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ModelResponse:
        self._step += 1
        user_prompt = ""
        for m in messages:
            if m.get("role") == "user":
                user_prompt = m.get("content", "")

        prompt_lower = user_prompt.lower()

        # Step 1: Environment inspection
        if self._step == 1:
            return ModelResponse(
                text="Discovering environment and workspace structure.",
                tool_calls=[ToolCall(id="call_inspect_1", name="inspect_environment", arguments={})],
            )

        # Handle simple file creation: e.g. "Create hello.py that prints Hello Aetherion"
        if "hello.py" in prompt_lower or "hello aetherion" in prompt_lower:
            if self._step == 2:
                content = 'print("Hello Aetherion")\n'
                return ModelResponse(
                    text="Creating hello.py with requested output.",
                    tool_calls=[ToolCall(id="call_write_2", name="write_file", arguments={"path": "hello.py", "content": content})],
                )
            elif self._step == 3:
                return ModelResponse(
                    text="Executing hello.py to verify execution output.",
                    tool_calls=[ToolCall(id="call_run_3", name="run_command", arguments={"command": f"{sys.executable} hello.py"})],
                )
            elif self._step == 4:
                return ModelResponse(
                    text="Finishing task.",
                    tool_calls=[ToolCall(
                        id="call_finish_4",
                        name="finish",
                        arguments={"summary": "Created hello.py and verified output.", "verification": VERIFIED},
                    )],
                )

        # Handle Factorial module task: e.g. "Create a Python module implementing factorial and add tests"
        if "factorial" in prompt_lower:
            if self._step == 2:
                factorial_code = (
                    "def factorial(n: int) -> int:\n"
                    "    if n < 0:\n"
                    "        raise ValueError('Factorial not defined for negative numbers')\n"
                    "    if n in (0, 1):\n"
                    "        return 1\n"
                    "    res = 1\n"
                    "    for i in range(2, n + 1):\n"
                    "        res *= i\n"
                    "    return res\n"
                )
                return ModelResponse(
                    text="Writing factorial.py implementation.",
                    tool_calls=[ToolCall(id="call_write_fact", name="write_file", arguments={"path": "factorial.py", "content": factorial_code})],
                )
            elif self._step == 3:
                test_code = (
                    "import unittest\n"
                    "from factorial import factorial\n\n"
                    "class TestFactorial(unittest.TestCase):\n"
                    "    def test_zero_and_one(self):\n"
                    "        self.assertEqual(factorial(0), 1)\n"
                    "        self.assertEqual(factorial(1), 1)\n"
                    "    def test_five(self):\n"
                    "        self.assertEqual(factorial(5), 120)\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                )
                return ModelResponse(
                    text="Writing test_factorial.py test suite.",
                    tool_calls=[ToolCall(id="call_write_test_fact", name="write_file", arguments={"path": "test_factorial.py", "content": test_code})],
                )
            elif self._step == 4:
                return ModelResponse(
                    text="Running tests for factorial module.",
                    tool_calls=[ToolCall(id="call_run_fact_test", name="run_command", arguments={"command": f"{sys.executable} -m unittest test_factorial.py"})],
                )
            elif self._step == 5:
                return ModelResponse(
                    text="Module and tests created and verified.",
                    tool_calls=[ToolCall(
                        id="call_finish_fact",
                        name="finish",
                        arguments={"summary": "Created factorial.py and verified with test_factorial.py.", "verification": VERIFIED},
                    )],
                )

        # Handle Fibonacci task: "Create a Python file called fibonacci.py with a function fibonacci(n) and tests for n=0, 1, and 10."
        if "fibonacci" in prompt_lower:
            if self._step == 2:
                if self.inject_failure_once and not self._has_failed_once:
                    buggy_code = (
                        "def fibonacci(n: int) -> int:\n"
                        "    if n == 0: return 0\n"
                        "    if n == 1: return 999  # BUG injected for repair test\n"
                        "    a, b = 0, 1\n"
                        "    for _ in range(2, n + 1):\n"
                        "        a, b = b, a + b\n"
                        "    return b\n"
                    )
                    content = buggy_code
                else:
                    fib_code = (
                        "def fibonacci(n: int) -> int:\n"
                        "    if n == 0: return 0\n"
                        "    if n == 1: return 1\n"
                        "    a, b = 0, 1\n"
                        "    for _ in range(2, n + 1):\n"
                        "        a, b = b, a + b\n"
                        "    return b\n"
                    )
                    content = fib_code

                return ModelResponse(
                    text="Writing fibonacci.py implementation.",
                    tool_calls=[ToolCall(id="call_write_fib", name="write_file", arguments={"path": "fibonacci.py", "content": content})],
                )
            elif self._step == 3:
                test_code = (
                    "import unittest\n"
                    "from fibonacci import fibonacci\n\n"
                    "class TestFib(unittest.TestCase):\n"
                    "    def test_cases(self):\n"
                    "        self.assertEqual(fibonacci(0), 0)\n"
                    "        self.assertEqual(fibonacci(1), 1)\n"
                    "        self.assertEqual(fibonacci(10), 55)\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                )
                return ModelResponse(
                    text="Writing test_fibonacci.py.",
                    tool_calls=[ToolCall(id="call_write_test_fib", name="write_file", arguments={"path": "test_fibonacci.py", "content": test_code})],
                )
            elif self._step == 4:
                return ModelResponse(
                    text="Running fibonacci tests.",
                    tool_calls=[ToolCall(id="call_run_fib_test", name="run_command", arguments={"command": f"{sys.executable} -m unittest test_fibonacci.py"})],
                )
            elif self._step == 5:
                if self.inject_failure_once and not self._has_failed_once:
                    self._has_failed_once = True
                    return ModelResponse(
                        text="Test failed due to injected bug. Repairing fibonacci.py...",
                        tool_calls=[ToolCall(
                            id="call_edit_fib",
                            name="edit_file",
                            arguments={
                                "path": "fibonacci.py",
                                "old_str": "if n == 1: return 999  # BUG injected for repair test",
                                "new_str": "if n == 1: return 1",
                            },
                        )],
                    )
                else:
                    return ModelResponse(
                        text="Tests passed. Completing task.",
                        tool_calls=[ToolCall(
                            id="call_finish_fib",
                            name="finish",
                            arguments={"summary": "fibonacci.py and tests implemented and verified.", "verification": VERIFIED},
                        )],
                    )
            elif self._step == 6 and self.inject_failure_once:
                return ModelResponse(
                    text="Rerunning tests after code repair.",
                    tool_calls=[ToolCall(id="call_rerun_fib", name="run_command", arguments={"command": f"{sys.executable} -m unittest test_fibonacci.py"})],
                )
            elif self._step == 7 and self.inject_failure_once:
                return ModelResponse(
                    text="Repaired tests verified successfully.",
                    tool_calls=[ToolCall(
                        id="call_finish_fib_repaired",
                        name="finish",
                        arguments={"summary": "Repaired fibonacci.py and verified all tests pass.", "verification": VERIFIED},
                    )],
                )

        # Handle forced command failure prompt
        if "force_failing_command" in prompt_lower or "run failing command" in prompt_lower:
            if self._step == 2:
                return ModelResponse(
                    text="Executing intentionally failing command.",
                    tool_calls=[ToolCall(id="call_failing_cmd", name="run_command", arguments={"command": f'{sys.executable} -c "import sys; sys.exit(42)"'})],
                )
            elif self._step == 3:
                return ModelResponse(
                    text="Observing non-zero exit code and failing task properly.",
                    tool_calls=[ToolCall(
                        id="call_finish_failed",
                        name="finish",
                        arguments={"summary": "Command failed with exit code 42 as expected.", "verification": VERIFICATION_FAILED},
                    )],
                )

        # Default multi-step fallback
        if self._step == 2:
            return ModelResponse(
                text="Writing solution file app.py.",
                tool_calls=[ToolCall(id="call_write_default", name="write_file", arguments={"path": "app.py", "content": f"# Solution for: {user_prompt}\nprint('OK')\n"})],
            )
        elif self._step == 3:
            return ModelResponse(
                text="Verifying syntax of app.py.",
                tool_calls=[ToolCall(id="call_syntax_default", name="check_syntax", arguments={"path": "app.py"})],
            )
        else:
            ver = NOT_VERIFIED if self.force_verification_fail else VERIFIED
            return ModelResponse(
                text="Completed execution.",
                tool_calls=[ToolCall(id="call_finish_default", name="finish", arguments={"summary": f"Task executed for: {user_prompt}", "verification": ver})],
            )


class OfflineFallbackProvider(BaseModelProvider):
    """Designated offline fallback provider when no LLM API keys or local models exist.

    Never fakes execution or claims success.
    """

    def name(self) -> str:
        return "offline_fallback"

    def is_available(self) -> bool:
        return True

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_tools=False, supports_streaming=False)

    def health_check(self) -> Dict[str, Any]:
        return {
            "status": "CONNECTED",
            "latency_ms": 0.0,
            "diagnostics": "Offline fallback provider active (no remote connection required).",
        }

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ModelResponse:
        return ModelResponse(
            text="No external or local model provider is available.",
            tool_calls=[ToolCall(
                id="call_offline_finish",
                name="finish",
                arguments={
                    "summary": "Provider unavailable. Offline fallback generated a plan, but no code execution was performed.",
                    "verification": NOT_VERIFIED,
                },
            )],
        )


class GenericChatProvider(OpenAIRuntimeProvider):
    """Generic chat-compatible provider wrapping OpenAI endpoint with optional tool emulation."""

    def name(self) -> str:
        return f"custom_chat:{self.model}"
