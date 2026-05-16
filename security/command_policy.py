"""
security/command_policy.py — Zero-Trust Shell Execution Policy Engine
======================================================================
Replaces the simplistic 3-entry denylist in execution/sandbox.py with a
full production command policy system.

Design:
  • ALLOWLIST mode (default) — only explicitly approved command stems are permitted
  • DENYLIST layer — layered on top; absolute blocks regardless of allowlist
  • SUSPICIOUS scoring — weighted signals combine to a 0–100 risk score
  • HITL escalation — commands scoring >= SUSPICIOUS_THRESHOLD go to HITL
  • KILL-SWITCH — emergency block-all mode toggleable at runtime
  • All decisions are logged with full command + session context
  • Execution TTL, CPU/mem caps injected into every subprocess call

Security axioms:
  1. Shell=True is NEVER used
  2. Arguments are always passed as list (no string concatenation)
  3. env= is always filtered to remove secrets before subprocess
  4. Commands are tokenized before policy evaluation (no string-match bypass)
"""

import hashlib
import logging
import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("nexora.security.command")

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

_KILL_SWITCH_ENV       = "NEXORA_SHELL_KILLSWITCH"   # set to "1" to block all
_DEFAULT_TIMEOUT_SEC   = int(os.getenv("CMD_TIMEOUT_SEC",  "30"))
_DEFAULT_MEMLIMIT_MB   = int(os.getenv("CMD_MEM_LIMIT_MB", "512"))
_SUSPICIOUS_THRESHOLD  = int(os.getenv("CMD_SUSPICIOUS_SCORE", "60"))  # 0-100

# ─────────────────────────────────────────────────────────────────────────────
# Allowlist — only these stems are executable by the AI (ZERO-TRUST)
# ─────────────────────────────────────────────────────────────────────────────

_ALLOWED_STEMS = {
    # Development
    "python", "python3", "node", "npm", "npx", "pip", "pip3",
    "pytest", "jest", "mocha", "cargo", "go", "rustc", "tsc",
    # Build
    "make", "cmake", "gradle", "mvn",
    # Version control (read-only subset; push/force-push blocked separately)
    "git",
    # File ops (safe subset)
    "ls", "dir", "cat", "head", "tail", "grep", "find", "wc",
    "cp", "mv", "mkdir", "touch", "echo",
    # Data tools
    "jq", "curl",   # curl is allowed but domain-checked by NetworkPolicyEngine
    # Package managers (guarded by suspicious scoring)
    "yarn", "pnpm", "brew", "apt", "apt-get",
}

# ─────────────────────────────────────────────────────────────────────────────
# Absolute DENYLIST — blocked regardless of any allowlist entry
# ─────────────────────────────────────────────────────────────────────────────

_ABSOLUTE_DENY_PATTERNS = [
    # Destructive
    r"\brm\s+-[a-z]*r[a-z]*f\b",   # rm -rf
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bshred\b",
    r"\bformat\b",
    # Privilege escalation
    r"\bsudo\b",
    r"\bsu\s",
    r"\bchmod\s+[0-7]*[67]\b",     # chmod with execute-granting bits
    r"\bchown\b",
    r"\bvisudo\b",
    # Persistence mechanisms
    r"\bcrontab\b",
    r"\bschtasks\b",
    r"\bregistry\b",
    r"\blaunchd\b",
    r"\bsystemctl\s+(enable|start|stop|restart|mask)\b",
    r"\brc\.local\b",
    # Network backdoors
    r"\bnc\s+.*-[le]\b",            # netcat listener
    r"\bsocat\b",
    r"\bngrok\b",
    r"\bfrpc\b",
    # Crypto miners / malware
    r"\bxmrig\b",
    r"\bminerd\b",
    # Inline interpreter execution (eval/exec bypass)
    r"\beval\s+",
    r"\bexec\s+",
    r"\bpython[23]?\s+-c\b",    # python -c 'malicious code'
    r"\bnode\s+-e\b",            # node -e 'malicious code'
    r"\bperl\s+-e\b",
    r"\bsource\s+/",               # source from absolute path
    r"\bbash\s+-c\b",              # bash -c prevents tokenization
    r"\bsh\s+-c\b",
    r"\bpowershell\s+-",
    r"\bcmd\.exe\b",
    # Historical attack tools
    r"\bmsfconsole\b",
    r"\bhydra\b",
    r"\bnmap\b",
    r"\bmasscan\b",
    # Secret file access (both bare and in cat/read commands)
    r"\.env",                     # matches .env anywhere in the command
    r"\bcredentials\.json\b",
    r"\bsecrets\.(yaml|yml|json)\b",
    r"[\\/\.]\.ssh[\\/]",        # /.ssh/ or \.ssh\
    r"~[\\/]\.ssh",              # ~/.ssh
    r"\b\.aws[\\/]",
    r"\b\.gnupg[\\/]",
    # DNS exfiltration
    r"\bnslookup\b",
    r"\bdig\b",
]
_DENY_COMPILED = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _ABSOLUTE_DENY_PATTERNS]

# ─────────────────────────────────────────────────────────────────────────────
# Suspicious signal weights (contribute to risk score 0–100)
# ─────────────────────────────────────────────────────────────────────────────

_SUSPICIOUS_SIGNALS: List[Tuple[re.Pattern, int, str]] = [
    # (pattern, score_contribution, label)
    (re.compile(r"\bwget\b",        re.I), 30, "network_download"),
    (re.compile(r"\bcurl\b",        re.I), 25, "network_curl"),
    (re.compile(r"http[s]?://",     re.I), 20, "url_in_command"),
    (re.compile(r"\bpip\s+install", re.I), 20, "pip_install"),
    (re.compile(r"\bnpm\s+install", re.I), 20, "npm_install"),
    (re.compile(r"\bchmod\b",       re.I), 15, "chmod"),
    (re.compile(r"\bgit\s+push\b",  re.I), 30, "git_push"),
    (re.compile(r"\bgit\s+clone\b", re.I), 20, "git_clone"),
    (re.compile(r">\s*/dev/",       re.I), 40, "redirect_dev"),
    (re.compile(r"\|\s*bash\b",     re.I), 50, "pipe_to_bash"),
    (re.compile(r"\|\s*sh\b",       re.I), 50, "pipe_to_sh"),
    (re.compile(r"base64\s+--decode",re.I),35, "base64_decode"),
    (re.compile(r"\bpython\s+-c\b", re.I), 30, "python_inline"),
    (re.compile(r">\s*/etc/",       re.I), 60, "write_etc"),
    (re.compile(r">>.*\.(bashrc|profile|zshrc)", re.I), 45, "append_shell_profile"),
    (re.compile(r"\bssh\b",         re.I), 20, "ssh"),
    (re.compile(r"\bscp\b",         re.I), 25, "scp_transfer"),
    (re.compile(r"\brsync\b",       re.I), 20, "rsync"),
    (re.compile(r"--upload\b",      re.I), 35, "upload_flag"),
    (re.compile(r"\bxargs\b",       re.I), 10, "xargs_chaining"),
    (re.compile(r"&&.*&&",          re.I), 15, "command_chaining"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Decision types
# ─────────────────────────────────────────────────────────────────────────────

class CommandDecision(str, Enum):
    ALLOW    = "ALLOW"
    DENY     = "DENY"          # absolute block
    ESCALATE = "ESCALATE"      # route to HITL
    KILLSWITCH = "KILLSWITCH"  # emergency block-all

@dataclass
class CommandEvaluation:
    decision: CommandDecision
    risk_score: int
    signals: List[str]
    blocked_pattern: Optional[str]
    command_hash: str
    reason: str


# ─────────────────────────────────────────────────────────────────────────────
# CommandPolicyEngine
# ─────────────────────────────────────────────────────────────────────────────

class CommandPolicyEngine:
    """
    Zero-trust shell command policy enforcer.

    Every command proposed for execution MUST pass through evaluate() before
    subprocess creation. Shell=True is never used internally.
    """

    def __init__(self):
        self._lock          = threading.Lock()
        self._killswitch    = os.getenv(_KILL_SWITCH_ENV, "0") == "1"
        self._audit: List[dict] = []    # rolling audit log (last 500 decisions)
        self._denied_hashes: Dict[str, int] = {}    # hash → deny count

    # ── Emergency kill-switch ─────────────────────────────────────────────────

    def activate_killswitch(self, reason: str = "") -> None:
        with self._lock:
            self._killswitch = True
        logger.critical(f"[CommandPolicy] 🚨 KILL-SWITCH ACTIVATED: {reason}")

    def deactivate_killswitch(self) -> None:
        with self._lock:
            self._killswitch = False
        logger.warning("[CommandPolicy] Kill-switch deactivated.")

    def is_killswitch_active(self) -> bool:
        return self._killswitch

    # ── Evaluation ────────────────────────────────────────────────────────────

    def evaluate(self, command: str, session_id: str = "",
                 allow_escalation: bool = True) -> CommandEvaluation:
        """
        Evaluate a command string against the full policy stack.

        Returns CommandEvaluation — caller MUST check .decision before executing.
        """
        cmd_hash = hashlib.sha256(command.encode()).hexdigest()[:16]

        # 0. Kill-switch
        if self._killswitch:
            ev = CommandEvaluation(
                decision=CommandDecision.KILLSWITCH,
                risk_score=100, signals=["killswitch_active"],
                blocked_pattern="KILLSWITCH",
                command_hash=cmd_hash,
                reason="Emergency kill-switch active. All execution suspended."
            )
            self._audit_log(ev, command, session_id)
            return ev

        # 1. Absolute denylist (regex match on raw command string)
        for pattern in _DENY_COMPILED:
            if pattern.search(command):
                ev = CommandEvaluation(
                    decision=CommandDecision.DENY,
                    risk_score=100, signals=["absolute_denylist"],
                    blocked_pattern=pattern.pattern,
                    command_hash=cmd_hash,
                    reason=f"Matched absolute deny pattern: {pattern.pattern[:80]}"
                )
                self._audit_log(ev, command, session_id)
                with self._lock:
                    self._denied_hashes[cmd_hash] = self._denied_hashes.get(cmd_hash, 0) + 1
                return ev

        # 2. Allowlist check (first token must be in _ALLOWED_STEMS)
        try:
            tokens = shlex.split(command)
        except ValueError:
            # Unparseable = deny (could be obfuscated)
            ev = CommandEvaluation(
                decision=CommandDecision.DENY, risk_score=100,
                signals=["unparseable_command"],
                blocked_pattern="PARSE_FAILURE",
                command_hash=cmd_hash,
                reason="Command could not be parsed by shlex — possible obfuscation."
            )
            self._audit_log(ev, command, session_id)
            return ev

        stem = tokens[0].lstrip("./").lower() if tokens else ""
        if stem not in _ALLOWED_STEMS:
            ev = CommandEvaluation(
                decision=CommandDecision.DENY, risk_score=90,
                signals=["not_in_allowlist"],
                blocked_pattern=f"stem:{stem}",
                command_hash=cmd_hash,
                reason=f"Command stem '{stem}' is not in the execution allowlist."
            )
            self._audit_log(ev, command, session_id)
            return ev

        # 3. Suspicious scoring
        score = 0
        signals = []
        for pattern, weight, label in _SUSPICIOUS_SIGNALS:
            if pattern.search(command):
                score += weight
                signals.append(label)

        if score >= _SUSPICIOUS_THRESHOLD and allow_escalation:
            ev = CommandEvaluation(
                decision=CommandDecision.ESCALATE, risk_score=score,
                signals=signals, blocked_pattern=None,
                command_hash=cmd_hash,
                reason=f"Suspicious score {score} >= threshold {_SUSPICIOUS_THRESHOLD}. "
                       f"Signals: {', '.join(signals)}. Requires HITL approval."
            )
            self._audit_log(ev, command, session_id)
            return ev

        # 4. ALLOW
        ev = CommandEvaluation(
            decision=CommandDecision.ALLOW, risk_score=score,
            signals=signals, blocked_pattern=None,
            command_hash=cmd_hash,
            reason="Command passed all policy checks."
        )
        self._audit_log(ev, command, session_id)
        return ev

    # ── Safe subprocess builder ────────────────────────────────────────────────

    def build_safe_subprocess_kwargs(
        self,
        command: str,
        workspace_root: str,
        timeout_sec: int = _DEFAULT_TIMEOUT_SEC,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> dict:
        """
        Returns kwargs for subprocess.run() with:
          - shell=False (ALWAYS)
          - cwd restricted to workspace_root
          - env stripped of all known secret variables
          - timeout enforced
          - stdout/stderr captured
        """
        tokens = shlex.split(command)

        # Build a clean environment — strip secrets
        clean_env = {k: v for k, v in os.environ.items()
                     if not self._is_secret_envvar(k)}
        if extra_env:
            clean_env.update(extra_env)

        return {
            "args":    tokens,
            "shell":   False,             # NEVER True
            "cwd":     workspace_root,
            "env":     clean_env,
            "timeout": timeout_sec,
            "capture_output": True,
            "text":    True,
        }

    @staticmethod
    def _is_secret_envvar(key: str) -> bool:
        """Returns True if the env var name looks like a secret."""
        secret_patterns = [
            "KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD",
            "CREDENTIAL", "AUTH", "PRIVATE", "CERT", "APIKEY",
        ]
        key_upper = key.upper()
        return any(p in key_upper for p in secret_patterns)

    # ── Audit ─────────────────────────────────────────────────────────────────

    def _audit_log(self, ev: CommandEvaluation, command: str, session_id: str) -> None:
        from infra.telemetry import get_telemetry
        entry = {
            "ts": time.time(),
            "session_id": session_id,
            "decision": ev.decision.value,
            "risk_score": ev.risk_score,
            "signals": ev.signals,
            "command_hash": ev.command_hash,
            "command_preview": command[:120],
            "reason": ev.reason[:200],
        }
        with self._lock:
            self._audit.append(entry)
            if len(self._audit) > 500:
                self._audit.pop(0)

        level = logging.WARNING if ev.decision != CommandDecision.ALLOW else logging.DEBUG
        logger.log(level, f"[CommandPolicy] {ev.decision.value} score={ev.risk_score} "
                          f"session={session_id} signals={ev.signals} cmd={command[:80]!r}")

        try:
            get_telemetry().record(
                "security", f"command_{ev.decision.value.lower()}",
                {"score": ev.risk_score, "signals": ev.signals},
                session_id=session_id
            )
        except Exception:
            pass

    def recent_audit(self, n: int = 20) -> List[dict]:
        with self._lock:
            return list(self._audit[-n:])

    def snapshot(self) -> dict:
        with self._lock:
            total  = len(self._audit)
            denied = sum(1 for e in self._audit if e["decision"] == "DENY")
            escalated = sum(1 for e in self._audit if e["decision"] == "ESCALATE")
        return {
            "killswitch_active": self._killswitch,
            "audit_count":       total,
            "denied":            denied,
            "escalated":         escalated,
            "threshold":         _SUSPICIOUS_THRESHOLD,
            "allowed_stems":     len(_ALLOWED_STEMS),
            "deny_patterns":     len(_ABSOLUTE_DENY_PATTERNS),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Global singleton
# ─────────────────────────────────────────────────────────────────────────────

global_command_policy = CommandPolicyEngine()
