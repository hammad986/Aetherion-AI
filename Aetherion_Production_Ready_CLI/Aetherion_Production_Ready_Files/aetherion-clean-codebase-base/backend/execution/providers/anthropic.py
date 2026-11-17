"""
backend/execution/providers/anthropic.py — Generic Anthropic-compatible model provider adapter.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

from .base import BaseModelProvider, ModelResponse, ProviderCapabilities, ProviderUnavailableException, ToolCall

logger = logging.getLogger("aetherion.providers.anthropic")


class AnthropicRuntimeProvider(BaseModelProvider):
    """Real Anthropic Claude / Anthropic-compatible tool-calling model provider."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        model: str = "claude-3-5-sonnet-20241022",
        timeout: float = 60.0,
        custom_headers: Optional[Dict[str, str]] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.custom_headers = custom_headers or {}

    def name(self) -> str:
        return f"anthropic:{self.model}"

    def is_available(self) -> bool:
        return bool(self.api_key)

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
        anthropic_tools = []
        for t in tools:
            if "input_schema" in t:
                anthropic_tools.append(t)
            elif t.get("type") == "function" and "function" in t:
                func = t["function"]
                anthropic_tools.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {}),
                })
            else:
                anthropic_tools.append(t)
        return anthropic_tools

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ModelResponse:
        """Convert messages to Anthropic format and execute HTTP call."""
        system_prompt = ""
        anthropic_msgs: List[Dict[str, Any]] = []

        for m in messages:
            role = m.get("role", "")
            if role == "system":
                system_prompt += m.get("content", "") + "\n"
            elif role == "user":
                anthropic_msgs.append({"role": "user", "content": m.get("content", "")})
            elif role == "assistant":
                content: List[Dict[str, Any]] = []
                if m.get("content"):
                    content.append({"type": "text", "text": m["content"]})
                if m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        t_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                        t_name = tc.get("name") or (tc.get("tool") if isinstance(tc, dict) else "")
                        t_args = tc.get("args") if "args" in tc else tc.get("arguments", {})
                        content.append({
                            "type": "tool_use",
                            "id": t_id,
                            "name": t_name,
                            "input": t_args if isinstance(t_args, dict) else {},
                        })
                elif m.get("tool_call"):
                    tc = m["tool_call"]
                    content.append({
                        "type": "tool_use",
                        "id": m.get("tool_call_id", f"call_{uuid.uuid4().hex[:8]}"),
                        "name": tc["name"],
                        "input": tc.get("args", {}),
                    })
                if content:
                    anthropic_msgs.append({"role": "assistant", "content": content})
            elif role == "tool":
                call_id = m.get("tool_call_id", f"call_{uuid.uuid4().hex[:8]}")
                anthropic_msgs.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": m.get("content", ""),
                        }
                    ],
                })

        # Coalesce consecutive user/assistant messages if any
        coalesced: List[Dict[str, Any]] = []
        for msg in anthropic_msgs:
            if coalesced and coalesced[-1]["role"] == msg["role"]:
                c_prev = coalesced[-1]["content"]
                c_curr = msg["content"]
                if isinstance(c_prev, str) and isinstance(c_curr, str):
                    coalesced[-1]["content"] = c_prev + "\n\n" + c_curr
                elif isinstance(c_prev, list) and isinstance(c_curr, list):
                    coalesced[-1]["content"].extend(c_curr)
                else:
                    coalesced.append(msg)
            else:
                coalesced.append(msg)

        ua = os.environ.get("ANTHROPIC_USER_AGENT", "").strip()
        if not ua and "agentrouter" in self.base_url.lower():
            ua = "claude-desktop/0.1.0"
        if not ua:
            ua = "Aetherion/2.0"

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "user-agent": ua,
        }
        if self.custom_headers:
            headers.update(self.custom_headers)

        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": coalesced,
        }
        if "deepseek" in self.model.lower():
            payload["thinking"] = {"type": "disabled"}
        if system_prompt.strip():
            payload["system"] = system_prompt.strip()

        formatted_tools = self._format_tools(tools)
        if formatted_tools:
            payload["tools"] = formatted_tools

        url = f"{self.base_url}/v1/messages"
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
            raise ProviderUnavailableException(f"Failed to connect to Anthropic endpoint {self.base_url}: {err_msg}")

        content_blocks = data.get("content", [])
        thought = ""
        tool_name = ""
        tool_args: Dict[str, Any] = {}
        tool_id = ""
        normalized_tcs: List[ToolCall] = []

        for block in content_blocks:
            b_type = block.get("type")
            if b_type == "text":
                thought += block.get("text", "")
            elif b_type == "tool_use":
                t_name = block.get("name", "")
                t_args = block.get("input", {})
                if isinstance(t_args, str):
                    try:
                        import json
                        t_args = json.loads(t_args)
                    except Exception:
                        pass
                t_id = block.get("id", f"call_{uuid.uuid4().hex[:8]}")
                normalized_tcs.append(ToolCall(id=t_id, name=t_name, arguments=t_args))

        if not normalized_tcs:
            normalized_tcs.append(
                ToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name="finish",
                    arguments={"summary": thought or "Task completed."},
                )
            )

        finish_reason = data.get("stop_reason")
        usage = data.get("usage", {})

        return ModelResponse(
            text=thought,
            tool_calls=normalized_tcs,
            finish_reason=finish_reason,
            usage=usage,
            metadata={"model": self.model, "provider": "anthropic"},
        )

    def health_check(self) -> Dict[str, Any]:
        """Execute granular health check against Anthropic-compatible endpoint."""
        t0 = time.time()
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "user-agent": "Aetherion/2.0",
        }
        if self.custom_headers:
            headers.update(self.custom_headers)

        try:
            with httpx.Client(timeout=min(self.timeout, 10.0)) as client:
                url = f"{self.base_url}/v1/messages"
                # Minimal probe payload
                payload = {
                    "model": self.model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                }
                resp = client.post(url, headers=headers, json=payload)
                latency = (time.time() - t0) * 1000.0

                if resp.status_code in (200, 400):  # 400 with valid json is reachable
                    # Check if error message indicates auth failure or invalid parameter
                    if resp.status_code == 400 and "authentication" in resp.text.lower():
                        return {
                            "status": "AUTH_FAILED",
                            "latency_ms": round(latency, 2),
                            "diagnostics": f"Authentication rejected: {resp.text[:200]}",
                        }
                    return {
                        "status": "CONNECTED",
                        "latency_ms": round(latency, 2),
                        "diagnostics": f"Successfully reached {url} ({resp.status_code})",
                    }
                elif resp.status_code in (401, 403):
                    return {
                        "status": "AUTH_FAILED",
                        "latency_ms": round(latency, 2),
                        "diagnostics": f"Authentication failed (HTTP {resp.status_code}): {resp.text[:200]}",
                    }
                elif resp.status_code == 404:
                    return {
                        "status": "NOT_FOUND",
                        "latency_ms": round(latency, 2),
                        "diagnostics": f"Endpoint not found (HTTP 404) at {url}",
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
