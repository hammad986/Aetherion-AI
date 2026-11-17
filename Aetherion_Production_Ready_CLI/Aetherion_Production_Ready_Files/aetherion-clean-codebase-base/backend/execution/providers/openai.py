"""
backend/execution/providers/openai.py — Generic OpenAI-compatible model provider adapter.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

from .base import BaseModelProvider, ModelResponse, ProviderCapabilities, ProviderUnavailableException, ToolCall

logger = logging.getLogger("aetherion.providers.openai")


class OpenAIRuntimeProvider(BaseModelProvider):
    """Real OpenAI-compatible tool-calling model provider."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
        timeout: float = 60.0,
        custom_headers: Optional[Dict[str, str]] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.custom_headers = custom_headers or {}

    def name(self) -> str:
        return f"openai:{self.model}"

    def is_available(self) -> bool:
        return bool(self.api_key or "localhost" in self.base_url or "127.0.0.1" in self.base_url)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_tools=True,
            supports_streaming=True,
            supports_system_messages=True,
            supports_json=True,
            supports_vision=True,
            supports_parallel_tools=True,
        )

    def _scrub_secret(self, text: str) -> str:
        """Sanitize raw API credentials from error messages and diagnostics."""
        if not text or not self.api_key:
            return text
        return text.replace(self.api_key, "[REDACTED_API_KEY]")

    def _format_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        oai_tools = []
        for t in tools:
            # Check if already in OpenAI format
            if t.get("type") == "function" and "function" in t:
                oai_tools.append(t)
            else:
                # Convert Anthropic-style tool to OpenAI function definition
                oai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    },
                })
        return oai_tools

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ModelResponse:
        """Convert messages to OpenAI format and execute HTTP call."""
        oai_msgs: List[Dict[str, Any]] = []
        for m in messages:
            role = m.get("role", "")
            if role == "tool":
                oai_msgs.append({
                    "role": "tool",
                    "content": m.get("content", ""),
                    "tool_call_id": m.get("tool_call_id", "call_default"),
                })
            elif role == "assistant" and (m.get("tool_calls") or m.get("tool_call")):
                oai_tcs = []
                if m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        tcid = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                        tc_name = tc.get("name") or (tc.get("tool") if isinstance(tc, dict) else "")
                        tc_args = tc.get("args") if "args" in tc else tc.get("arguments", {})
                        oai_tcs.append({
                            "id": tcid,
                            "type": "function",
                            "function": {
                                "name": tc_name,
                                "arguments": json.dumps(tc_args) if isinstance(tc_args, dict) else str(tc_args),
                            },
                        })
                elif m.get("tool_call"):
                    tc = m["tool_call"]
                    tcid = m.get("tool_call_id", f"call_{uuid.uuid4().hex[:8]}")
                    oai_tcs.append({
                        "id": tcid,
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc.get("args", {}))},
                    })
                oai_msgs.append({
                    "role": "assistant",
                    "content": m.get("content", "") or None,
                    "tool_calls": oai_tcs,
                })
            else:
                oai_msgs.append({"role": role, "content": m.get("content", "")})

        oai_tools = self._format_tools(tools)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Aetherion/2.0",
        }
        if self.custom_headers:
            headers.update(self.custom_headers)

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": oai_msgs,
        }
        if oai_tools:
            payload["tools"] = oai_tools
            payload["tool_choice"] = "auto"

        url = f"{self.base_url}/chat/completions"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, headers=headers, json=payload)
                if resp.status_code != 200:
                    err_body = self._scrub_secret(resp.text[:400])
                    raise ProviderUnavailableException(f"Provider HTTP {resp.status_code}: {err_body}")
                data = resp.json()
        except ProviderUnavailableException:
            raise
        except Exception as exc:
            err_msg = self._scrub_secret(str(exc))
            raise ProviderUnavailableException(f"Failed to connect to OpenAI endpoint {self.base_url}: {err_msg}")

        choices = data.get("choices", [])
        if not choices:
            raise ProviderUnavailableException("OpenAI returned no choices in response.")

        choice = choices[0]
        msg = choice.get("message", {})
        thought = msg.get("content") or ""
        raw_tcs = msg.get("tool_calls", [])
        finish_reason = choice.get("finish_reason")
        usage = data.get("usage", {})

        normalized_tcs: List[ToolCall] = []
        if raw_tcs:
            for tc in raw_tcs:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except Exception:
                    args = {}
                tcid = tc.get("id", f"call_{uuid.uuid4().hex[:8]}")
                normalized_tcs.append(ToolCall(id=tcid, name=tool_name, arguments=args))
        else:
            normalized_tcs.append(
                ToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name="finish",
                    arguments={"summary": thought or "Task completed."},
                )
            )

        return ModelResponse(
            text=thought,
            tool_calls=normalized_tcs,
            finish_reason=finish_reason,
            usage=usage,
            metadata={"model": self.model, "provider": "openai"},
        )

    def health_check(self) -> Dict[str, Any]:
        """Execute granular health check against OpenAI-compatible endpoint."""
        t0 = time.time()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Aetherion/2.0",
        }
        if self.custom_headers:
            headers.update(self.custom_headers)

        # First attempt /models GET, fallback to minimal dry-run /chat/completions
        try:
            with httpx.Client(timeout=min(self.timeout, 10.0)) as client:
                models_url = f"{self.base_url}/models"
                resp = client.get(models_url, headers=headers)
                latency = (time.time() - t0) * 1000.0

                if resp.status_code == 200:
                    return {
                        "status": "CONNECTED",
                        "latency_ms": round(latency, 2),
                        "diagnostics": f"Successfully connected to {self.base_url}/models ({resp.status_code})",
                    }
                elif resp.status_code in (401, 403):
                    return {
                        "status": "AUTH_FAILED",
                        "latency_ms": round(latency, 2),
                        "diagnostics": f"Authentication failed (HTTP {resp.status_code}): {resp.text[:200]}",
                    }
                elif resp.status_code == 404:
                    # Endpoint may only support /chat/completions; try that
                    chat_url = f"{self.base_url}/chat/completions"
                    chat_resp = client.post(
                        chat_url,
                        headers=headers,
                        json={"model": self.model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                    )
                    chat_latency = (time.time() - t0) * 1000.0
                    if chat_resp.status_code in (200, 400):  # 400 with valid json indicates endpoint exists
                        return {
                            "status": "CONNECTED",
                            "latency_ms": round(chat_latency, 2),
                            "diagnostics": f"Connected to {chat_url}",
                        }
                    elif chat_resp.status_code in (401, 403):
                        return {
                            "status": "AUTH_FAILED",
                            "latency_ms": round(chat_latency, 2),
                            "diagnostics": f"Authentication failed (HTTP {chat_resp.status_code})",
                        }
                    return {
                        "status": "NOT_FOUND",
                        "latency_ms": round(chat_latency, 2),
                        "diagnostics": f"Endpoint not found (HTTP 404) at {self.base_url}",
                    }
                else:
                    return {
                        "status": "UNSUPPORTED_PROTOCOL",
                        "latency_ms": round(latency, 2),
                        "diagnostics": f"Unexpected HTTP status {resp.status_code}: {resp.text[:200]}",
                    }

        except httpx.TimeoutException:
            latency = (time.time() - t0) * 1000.0
            return {
                "status": "TIMEOUT",
                "latency_ms": round(latency, 2),
                "diagnostics": f"Connection timed out to {self.base_url}",
            }
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            latency = (time.time() - t0) * 1000.0
            return {
                "status": "NETWORK_ERROR",
                "latency_ms": round(latency, 2),
                "diagnostics": f"Network error connecting to {self.base_url}: {exc}",
            }
        except Exception as exc:
            latency = (time.time() - t0) * 1000.0
            return {
                "status": "INVALID_CONFIGURATION",
                "latency_ms": round(latency, 2),
                "diagnostics": f"Configuration or client error: {exc}",
            }
