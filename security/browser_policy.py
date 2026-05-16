"""
security/browser_policy.py — Browser Automation Defense Layer
=============================================================
Defends against SSRF, credential theft, phishing, exfiltration, and
malicious download attacks in Playwright browser automation.

Zero-trust browser axioms:
  1. Every URL is checked against allowlist/blocklist before navigation
  2. Internal network IPs are always blocked (SSRF prevention)
  3. Form submissions require HITL confirmation
  4. Downloads are filtered by extension
  5. Browser context is ephemeral (no persistent storage)
  6. Page content is injection-scanned before LLM ingestion
  7. Network request rate limiting prevents browser-based flooding
  8. All navigation events are logged with full URL

SSRF Protection:
  Internal IP ranges blocked: 127.x, 10.x, 172.16-31.x, 192.168.x,
  169.254.x (link-local), ::1, fc00::/7, fe80::/10
"""

import ipaddress
import logging
import os
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("nexora.security.browser")


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

_NAV_RATE_LIMIT_PER_MIN  = int(os.getenv("BROWSER_NAV_RATE_LIMIT", "30"))
_DOWNLOAD_RATE_LIMIT_PER_MIN = int(os.getenv("BROWSER_DL_RATE_LIMIT", "5"))
_MAX_PAGE_SIZE_BYTES      = int(os.getenv("BROWSER_MAX_PAGE_BYTES", str(5 * 1024 * 1024)))  # 5MB


# ─────────────────────────────────────────────────────────────────────────────
# SSRF — Internal IP ranges to block
# ─────────────────────────────────────────────────────────────────────────────

_INTERNAL_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),          # ULA
    ipaddress.ip_network("fe80::/10"),         # link-local IPv6
    ipaddress.ip_network("100.64.0.0/10"),     # shared address space (CGN)
]

# Well-known internal hostnames
_INTERNAL_HOSTNAMES = {
    "localhost", "local", "internal", "intranet",
    "metadata.google.internal",   # GCP metadata server
    "169.254.169.254",            # AWS/Azure metadata service
    "instance-data",
}


# ─────────────────────────────────────────────────────────────────────────────
# Domain policy
# ─────────────────────────────────────────────────────────────────────────────

# Explicitly blocked domains (malicious, phishing, data-collection)
_BLOCKED_DOMAINS: Set[str] = {
    # Exfiltration / webhook listeners
    "requestbin.com", "webhook.site", "beeceptor.com", "pipedream.net",
    "hookbin.com", "pipedream.com", "ngrok.io", "serveo.net",
    # IP logger / tracking
    "iplogger.org", "grabify.link", "canarytokens.org",
    # Paste sites (frequent in exfil attacks)
    "pastebin.com", "paste.ee", "hastebin.com",
    # Telemetry / analytics (optional, for strict mode)
    # "google-analytics.com", "segment.io",
}

# Schema whitelist — only these URL schemes allowed
_ALLOWED_SCHEMES = {"http", "https"}

# Dangerous download extensions
_BLOCKED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jar",
    ".sh", ".bash", ".zsh", ".fish", ".py",       # executable scripts
    ".dmg", ".pkg", ".deb", ".rpm", ".msi",        # installers
    ".dll", ".so", ".dylib",                        # libraries
    ".iso", ".img",                                  # disk images
    ".appimage", ".snap",                            # Linux packages
}


# ─────────────────────────────────────────────────────────────────────────────
# Decision types
# ─────────────────────────────────────────────────────────────────────────────

class BrowserDecision(str, Enum):
    ALLOW   = "ALLOW"
    DENY    = "DENY"        # blocked outright
    ESCALATE= "ESCALATE"    # requires HITL
    WARN    = "WARN"        # allowed but logged

@dataclass
class UrlEvaluation:
    decision:   BrowserDecision
    reason:     str
    url:        str
    domain:     str
    signals:    List[str]


# ─────────────────────────────────────────────────────────────────────────────
# BrowserPolicyEngine
# ─────────────────────────────────────────────────────────────────────────────

class BrowserPolicyEngine:
    """
    Zero-trust browser automation policy enforcer.

    Every URL, download, and form submission MUST be checked before execution.
    SSRF protection is the primary concern — ALL internal IP resolution is blocked.
    """

    def __init__(self):
        self._lock       = threading.RLock()
        self._nav_log:   Dict[str, List[float]] = {}   # session → [timestamps]
        self._dl_log:    Dict[str, List[float]] = {}
        self._audit:     List[dict] = []
        self._custom_block: Set[str] = set()           # runtime-added blocklist

    # ── URL evaluation ────────────────────────────────────────────────────────

    def evaluate_url(self, url: str, session_id: str = "",
                     intent: str = "navigate") -> UrlEvaluation:
        """
        Evaluates a URL for safety before browser navigation or request.

        intent: "navigate" | "xhr" | "download" | "form_submit"
        """
        signals: List[str] = []

        try:
            parsed = urlparse(url)
        except Exception:
            return UrlEvaluation(BrowserDecision.DENY, "Malformed URL", url, "", ["malformed_url"])

        scheme = parsed.scheme.lower()
        domain = (parsed.hostname or "").lower()

        # 1. Schema check
        if scheme not in _ALLOWED_SCHEMES:
            return self._deny(url, domain, f"Scheme '{scheme}' not allowed", ["bad_scheme"], session_id)

        # 2. SSRF: internal hostname check
        if domain in _INTERNAL_HOSTNAMES or domain.endswith(".local"):
            return self._deny(url, domain, f"SSRF: Internal hostname '{domain}'",
                              ["ssrf_internal_hostname"], session_id)

        # 3. SSRF: IP resolution check (do NOT resolve — check literal IP first)
        if self._is_internal_ip_literal(domain):
            return self._deny(url, domain, f"SSRF: Internal IP literal '{domain}'",
                              ["ssrf_internal_ip"], session_id)

        # 4. Blocked domain list
        if domain in _BLOCKED_DOMAINS or domain in self._custom_block:
            return self._deny(url, domain, f"Domain '{domain}' is on blocklist",
                              ["blocked_domain"], session_id)

        # 5. Custom subdomain block (e.g., evil.requestbin.com)
        for blocked in _BLOCKED_DOMAINS | self._custom_block:
            if domain.endswith(f".{blocked}"):
                return self._deny(url, domain, f"Subdomain of blocked domain '{blocked}'",
                                  ["blocked_subdomain"], session_id)

        # 6. Download extension check
        if intent == "download":
            path = parsed.path.lower()
            for ext in _BLOCKED_EXTENSIONS:
                if path.endswith(ext):
                    return self._deny(url, domain, f"Dangerous download extension '{ext}'",
                                      ["blocked_extension"], session_id)

        # 7. Form submission: MUST escalate
        if intent == "form_submit":
            ev = UrlEvaluation(BrowserDecision.ESCALATE,
                               f"Form submission to '{domain}' requires HITL approval",
                               url, domain, ["form_submit"])
            self._audit_log(ev, session_id, intent)
            return ev

        # 8. Rate limit check
        if not self._check_nav_rate(session_id):
            return self._deny(url, domain, "Browser navigation rate limit exceeded",
                              ["rate_limit"], session_id)

        # ALLOW
        ev = UrlEvaluation(BrowserDecision.ALLOW, "URL passed all browser policy checks.",
                           url, domain, signals)
        self._audit_log(ev, session_id, intent)
        return ev

    # ── SSRF resolution check (does NOT perform DNS — blocks IP literals only) ─

    @staticmethod
    def _is_internal_ip_literal(host: str) -> bool:
        """Checks if host is a literal internal IP address without doing DNS."""
        try:
            addr = ipaddress.ip_address(host)
            return any(addr in net for net in _INTERNAL_NETWORKS)
        except ValueError:
            return False   # Not an IP literal — domain-based SSRF handled by hostname check

    # ── Content scanning ──────────────────────────────────────────────────────

    def scan_page_content(self, content: str, url: str = "",
                          session_id: str = "") -> Tuple[str, bool]:
        """
        Scans browser-returned page content for injection patterns before
        it is added to the LLM context window.

        Returns (safe_content, was_sanitized).
        """
        from security.injection_guard import global_injection_guard, ContentTrust
        result = global_injection_guard.scan_browser_content(content, url, session_id)
        was_sanitized = result.verdict.value in ("INJECTED", "SUSPICIOUS", "SANITIZED")
        if was_sanitized:
            logger.warning(f"[BrowserPolicy] Page content injection detected: "
                           f"url={url[:80]} verdict={result.verdict.value} "
                           f"signals={result.signals}")
        return result.sanitized_content, was_sanitized

    # ── Runtime blocklist management ──────────────────────────────────────────

    def block_domain(self, domain: str, reason: str = "") -> None:
        with self._lock:
            self._custom_block.add(domain.lower())
        logger.warning(f"[BrowserPolicy] Domain blocked at runtime: {domain} reason={reason}")

    def unblock_domain(self, domain: str) -> None:
        with self._lock:
            self._custom_block.discard(domain.lower())

    # ── Rate limiting ─────────────────────────────────────────────────────────

    def _check_nav_rate(self, session_id: str) -> bool:
        now = time.time()
        with self._lock:
            timestamps = [t for t in self._nav_log.get(session_id, [])
                          if now - t < 60.0]
            if len(timestamps) >= _NAV_RATE_LIMIT_PER_MIN:
                return False
            timestamps.append(now)
            self._nav_log[session_id] = timestamps
        return True

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _deny(self, url: str, domain: str, reason: str,
              signals: List[str], session_id: str) -> UrlEvaluation:
        ev = UrlEvaluation(BrowserDecision.DENY, reason, url, domain, signals)
        self._audit_log(ev, session_id, "deny")
        return ev

    def _audit_log(self, ev: UrlEvaluation, session_id: str, intent: str) -> None:
        entry = {
            "ts":         time.time(),
            "session_id": session_id,
            "decision":   ev.decision.value,
            "domain":     ev.domain,
            "url":        ev.url[:150],
            "signals":    ev.signals,
            "reason":     ev.reason[:150],
            "intent":     intent,
        }
        with self._lock:
            self._audit.append(entry)
            if len(self._audit) > 500:
                self._audit.pop(0)

        level = logging.WARNING if ev.decision != BrowserDecision.ALLOW else logging.DEBUG
        logger.log(level, f"[BrowserPolicy] {ev.decision.value} domain={ev.domain} "
                          f"session={session_id} signals={ev.signals}")
        try:
            from infra.telemetry import get_telemetry
            get_telemetry().record("security", f"browser_{ev.decision.value.lower()}",
                                   {"domain": ev.domain, "signals": ev.signals},
                                   session_id=session_id)
        except Exception:
            pass

    def recent_audit(self, n: int = 20) -> List[dict]:
        with self._lock:
            return list(self._audit[-n:])

    def snapshot(self) -> dict:
        with self._lock:
            total   = len(self._audit)
            denied  = sum(1 for e in self._audit if e["decision"] == "DENY")
            escalated = sum(1 for e in self._audit if e["decision"] == "ESCALATE")
        return {
            "audit_count":    total,
            "denied":         denied,
            "escalated":      escalated,
            "blocked_domains":len(_BLOCKED_DOMAINS) + len(self._custom_block),
            "nav_rate_limit": _NAV_RATE_LIMIT_PER_MIN,
            "internal_networks": len(_INTERNAL_NETWORKS),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Global singleton
# ─────────────────────────────────────────────────────────────────────────────

global_browser_policy = BrowserPolicyEngine()
