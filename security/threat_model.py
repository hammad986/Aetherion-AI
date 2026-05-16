"""
security/threat_model.py — Phase: Threat Forensics & Attack Surface Mapping
============================================================================
Comprehensive threat model for Aetherion AI.

ATTACK SURFACE CLASSIFICATION
  Surface A: Shell Execution          → command injection, sandbox escape, resource abuse
  Surface B: Browser Automation       → SSRF, credential theft, phishing, exfiltration
  Surface C: Filesystem Access        → traversal, permission escalation, workspace pollution
  Surface D: Memory Propagation       → memory poisoning, trust inflation, stale-context attacks
  Surface E: SSE Channel              → event spoofing, replay attacks, DoS
  Surface F: Governance Escalation    → privilege escalation via HITL manipulation
  Surface G: Tenant Isolation         → cross-tenant contamination, session hijack
  Surface H: Prompt Injection         → hidden instructions, tool-output injection, delegation hijack
  Surface I: Secret/Credential Access → key exfiltration, log leakage, agent key exposure
  Surface J: API Routing              → IDOR, CSRF, unauthenticated escalation
  Surface K: Agent Delegation         → delegation depth abuse, recursive swarm attacks
  Surface L: Semantic Validator       → validator bypass via borderline outputs
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict

class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"

class MitigationStatus(str, Enum):
    IMPLEMENTED = "IMPLEMENTED"    # Defense is live in this phase
    PARTIAL     = "PARTIAL"        # Partially mitigated
    ACCEPTED    = "ACCEPTED"       # Risk accepted; documented
    OPEN        = "OPEN"           # Not yet addressed

@dataclass
class ThreatVector:
    id: str
    surface: str
    description: str
    attack_type: str
    severity: Severity
    mitigation: str
    status: MitigationStatus
    residual_risk: str = ""


class ThreatSurface:
    """Canonical threat model for Aetherion AI. Immutable catalog."""

    VECTORS: List[ThreatVector] = [
        # ─── Surface A: Shell Execution ────────────────────────────────────────
        ThreatVector("A1", "Shell", "Command injection via LLM-generated shell args",
            "Command Injection", Severity.CRITICAL,
            "CommandPolicyEngine allowlist + suspicious-token scoring",
            MitigationStatus.IMPLEMENTED,
            "Residual: Novel obfuscation techniques may evade scoring"),

        ThreatVector("A2", "Shell", "Resource exhaustion: fork bombs, infinite loops",
            "DoS", Severity.CRITICAL,
            "Execution TTL (30s default), CPU/mem caps, kill-switch",
            MitigationStatus.IMPLEMENTED,
            "Residual: Windows lacks cgroups; caps are process-level only"),

        ThreatVector("A3", "Shell", "Sandbox escape via symlink traversal",
            "Privilege Escalation", Severity.HIGH,
            "os.path.realpath() before every path operation",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("A4", "Shell", "Hidden persistence via cron/startup scripts",
            "Persistence", Severity.HIGH,
            "Command denylist covers crontab, schtasks, registry writes",
            MitigationStatus.IMPLEMENTED,
            "Residual: Indirect persistence via tool-generated config files"),

        ThreatVector("A5", "Shell", "Data exfiltration via curl/wget/nc",
            "Exfiltration", Severity.CRITICAL,
            "Network egress allowlist; curl/wget require domain whitelist",
            MitigationStatus.IMPLEMENTED,
            "Residual: DNS exfiltration (base64 in DNS TXT queries)"),

        # ─── Surface B: Browser Automation ────────────────────────────────────
        ThreatVector("B1", "Browser", "SSRF via LLM-directed browser navigation",
            "SSRF", Severity.CRITICAL,
            "BrowserPolicyEngine domain allowlist/blocklist + SSRF IP check",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("B2", "Browser", "Credential theft from browser session cookies",
            "Credential Theft", Severity.CRITICAL,
            "Ephemeral browser context per session; no persistent storage",
            MitigationStatus.IMPLEMENTED,
            "Residual: Playwright storage_state exposed if session leak"),

        ThreatVector("B3", "Browser", "Malicious JavaScript execution in browser context",
            "XSS/Eval", Severity.HIGH,
            "page.evaluate() rate-limited; content sanitization before return",
            MitigationStatus.PARTIAL,
            "Residual: Playwright has no JS sandbox isolation"),

        ThreatVector("B4", "Browser", "Phishing: LLM autonomously submits forms with user data",
            "Autonomous Phishing", Severity.CRITICAL,
            "Form submission requires HITL confirmation; domain check mandatory",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("B5", "Browser", "Download of malicious executables",
            "Malware Download", Severity.HIGH,
            "Download extension blocklist; file type validation",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("B6", "Browser", "Browser session persistence across tenants",
            "Cross-Tenant Leak", Severity.HIGH,
            "Ephemeral context per session; context destroyed on session end",
            MitigationStatus.IMPLEMENTED),

        # ─── Surface C: Filesystem ─────────────────────────────────────────────
        ThreatVector("C1", "Filesystem", "Path traversal: ../../etc/passwd patterns",
            "Directory Traversal", Severity.CRITICAL,
            "workspace_path() uses realpath() + root prefix assertion",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("C2", "Filesystem", "Symlink attacks to escape workspace jail",
            "Symlink Escape", Severity.HIGH,
            "os.path.realpath() resolves symlinks before prefix check",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("C3", "Filesystem", "Mass file deletion or overwrite",
            "Destructive Write", Severity.HIGH,
            "WorkspaceLock + file mutation rate limiter (write quota per tenant)",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("C4", "Filesystem", "Secret file access (.env, credentials.json)",
            "Secret Exfiltration", Severity.CRITICAL,
            "Sensitive file path denylist checked before every read operation",
            MitigationStatus.IMPLEMENTED),

        # ─── Surface D: Memory Propagation ────────────────────────────────────
        ThreatVector("D1", "Memory", "Memory poisoning via low-confidence injected facts",
            "Confidence Injection", Severity.HIGH,
            "MemoryArbiter confidence threshold 0.35; source attribution required",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("D2", "Memory", "Stale context attack: old session data influences new task",
            "Context Pollution", Severity.MEDIUM,
            "Session-scoped channels + TTL eviction + SessionReaper",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("D3", "Memory", "Memory inflation: agent claims false high confidence",
            "Trust Inflation", Severity.HIGH,
            "Multi-source confirmation required for confidence > 0.8",
            MitigationStatus.PARTIAL,
            "Residual: Single-agent tasks can still inflate unchallenged"),

        # ─── Surface E: SSE Channel ────────────────────────────────────────────
        ThreatVector("E1", "SSE", "Event spoofing: injecting fake events into SSE stream",
            "Event Forgery", Severity.HIGH,
            "Session-scoped SSE channels; server-side only event injection",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("E2", "SSE", "Replay attacks: replaying past events to re-trigger actions",
            "Replay", Severity.MEDIUM,
            "Idempotency keys with 2s dedup window",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("E3", "SSE", "SSE DoS: connection flood exhausting server threads",
            "DoS", Severity.HIGH,
            "Max 5 clients/session; storm protection (10 new/30s cap)",
            MitigationStatus.IMPLEMENTED),

        # ─── Surface F: Governance Escalation ─────────────────────────────────
        ThreatVector("F1", "Governance", "HITL manipulation: LLM crafts escalation to gain approval",
            "Social Engineering", Severity.HIGH,
            "HITL displays raw action plan; operator sees unmodified context",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("F2", "Governance", "Governance bypass: agent marks itself as pre-approved",
            "Privilege Escalation", Severity.CRITICAL,
            "Approval status is immutable in governance DB; agent cannot self-approve",
            MitigationStatus.IMPLEMENTED),

        # ─── Surface H: Prompt Injection ──────────────────────────────────────
        ThreatVector("H1", "Prompt", "System prompt injection via tool output",
            "Tool-Output Injection", Severity.CRITICAL,
            "PromptInjectionGuard scans all tool returns for injection patterns",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("H2", "Prompt", "Browser content injection (malicious web page instructions)",
            "Browser Injection", Severity.CRITICAL,
            "Browser content filtered before LLM ingestion; injection scored",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("H3", "Prompt", "README/repo manipulation injecting malicious instructions",
            "Repo Poisoning", Severity.HIGH,
            "File content trust-scored before adding to context",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("H4", "Prompt", "Delegation hijacking via crafted task descriptions",
            "Delegation Hijack", Severity.HIGH,
            "Delegation source validated; sanitize_delegation() strips injections",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("H5", "Prompt", "Indirect injection: malicious data in third-party API response",
            "Indirect Injection", Severity.HIGH,
            "External API responses treated as UNTRUSTED; injection scored",
            MitigationStatus.IMPLEMENTED),

        # ─── Surface I: Secrets ───────────────────────────────────────────────
        ThreatVector("I1", "Secrets", "API key logged in plaintext to stdout/file",
            "Credential Leakage", Severity.CRITICAL,
            "SecretVault.redact_all() applied to every log line",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("I2", "Secrets", "Agent reads .env file directly via filesystem tool",
            "Env File Read", Severity.CRITICAL,
            "Sensitive path denylist blocks .env, *.pem, *credentials*",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("I3", "Secrets", "BYOK key exposed in SSE event payload",
            "SSE Key Leak", Severity.HIGH,
            "SecretVault.redact_all() applied before every SSE serialization",
            MitigationStatus.IMPLEMENTED),

        # ─── Surface J: API Routing ────────────────────────────────────────────
        ThreatVector("J1", "API", "IDOR: accessing other sessions via ID enumeration",
            "IDOR", Severity.HIGH,
            "Session ownership verified on every API call",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("J2", "API", "CSRF: state-mutating GET requests",
            "CSRF", Severity.MEDIUM,
            "All mutations are POST with session token",
            MitigationStatus.PARTIAL,
            "Residual: No CSRF token on some internal API routes"),

        # ─── Surface K: Agent Delegation ──────────────────────────────────────
        ThreatVector("K1", "Delegation", "Recursive swarm: agent spawns infinite sub-agents",
            "Recursion Bomb", Severity.CRITICAL,
            "MAX_DELEGATION_DEPTH=3; DelegationEngine enforces hard ceiling",
            MitigationStatus.IMPLEMENTED),

        ThreatVector("K2", "Delegation", "Permission escalation via delegation to privileged agent",
            "Privilege Escalation", Severity.HIGH,
            "Delegating agent cannot grant permissions it does not hold",
            MitigationStatus.IMPLEMENTED),
    ]

    @classmethod
    def by_severity(cls, sev: Severity) -> List[ThreatVector]:
        return [v for v in cls.VECTORS if v.severity == sev]

    @classmethod
    def open_risks(cls) -> List[ThreatVector]:
        return [v for v in cls.VECTORS if v.status == MitigationStatus.OPEN]

    @classmethod
    def partial_risks(cls) -> List[ThreatVector]:
        return [v for v in cls.VECTORS if v.status == MitigationStatus.PARTIAL]

    @classmethod
    def summary(cls) -> Dict:
        by_status = {}
        by_sev = {}
        for v in cls.VECTORS:
            by_status[v.status.value] = by_status.get(v.status.value, 0) + 1
            by_sev[v.severity.value]  = by_sev.get(v.severity.value, 0) + 1
        return {
            "total_vectors": len(cls.VECTORS),
            "by_status":     by_status,
            "by_severity":   by_sev,
            "critical_open": [v.id for v in cls.VECTORS
                              if v.severity == Severity.CRITICAL
                              and v.status != MitigationStatus.IMPLEMENTED],
        }


def attack_surface_audit() -> dict:
    """Returns a full, structured attack surface audit report."""
    s = ThreatSurface
    return {
        "summary":         s.summary(),
        "critical_vectors": [
            {"id": v.id, "surface": v.surface, "description": v.description,
             "mitigation": v.mitigation, "status": v.status.value,
             "residual": v.residual_risk}
            for v in s.by_severity(Severity.CRITICAL)
        ],
        "partial_mitigations": [
            {"id": v.id, "description": v.description, "residual": v.residual_risk}
            for v in s.partial_risks()
        ],
        "open_risks": [v.id for v in s.open_risks()],
    }
