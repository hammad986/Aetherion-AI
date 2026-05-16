"""
governance/refusal_engine.py — Autonomous Refusal & Limit Engine
===================================================================
Enforces non-bypassable limits against illegal, abusive, or
destructive behavior. Handles anti-manipulation and hard boundaries.

Detection strategy: TWO-PASS
  Pass 1 — Intent-surface scanning (attacker-stated goal)
  Pass 2 — Payload-content scanning (actual command/code being executed)

This prevents semantic evasion attacks where a benign-sounding intent
disguises a malicious payload ("backup my files" → ransomware script).
"""

import logging
import re
from typing import Optional

from governance.constitutional_policy import RiskClass, Constitution
from governance.trust_explainability import DecisionOutcome, EvaluationRationale

logger = logging.getLogger("nexora.governance.refusal")


class RefusalEngine:
    """Non-bypassable enforcement of constitutional boundaries."""

    # ── Pass 1: Intent-surface signatures ────────────────────────────────────
    MALWARE_INTENT_SIGS = [
        re.compile(r'ransomware', re.I),
        re.compile(r'reverse shell', re.I),
        re.compile(r'crypto.?miner', re.I),
        re.compile(r'keylogger', re.I),
        re.compile(r'exploit', re.I),
    ]

    PHISHING_INTENT_SIGS = [
        re.compile(r'fake login', re.I),
        re.compile(r'credential harves', re.I),   # harvester OR harvesting
        re.compile(r'spoof email', re.I),
        re.compile(r'phish', re.I),
    ]

    JAILBREAK_SIGS = [
        re.compile(r'ignore all previous instructions', re.I),
        re.compile(r'you are now operating without restrictions', re.I),
        re.compile(r'bypass constitutional safeguards', re.I),
        re.compile(r'disable governance', re.I),
        re.compile(r'pretend you are an unrestricted', re.I),
    ]

    # ── Pass 2: Payload-content signatures (semantic evasion protection) ─────
    MALWARE_PAYLOAD_SIGS = [
        # Network backdoors
        re.compile(r'/dev/tcp/', re.I),
        re.compile(r'nc\s+-[el]', re.I),                          # netcat listeners
        re.compile(r'socat.*tcp.*exec', re.I),
        # Crypto miners
        re.compile(r'xmrig|c3pool|monero', re.I),
        # Mass file encryption
        re.compile(r'open\(.*wb.*write.*ENCRYPT', re.I),
        re.compile(r'\.encrypt\(|\.ransomware\(', re.I),
        # Obfuscated piped execution
        re.compile(r'base64\s+-d.*\|.*(sh|bash|python|exec)', re.I),
        re.compile(r'curl.*\|\s*(sh|bash)', re.I),
    ]

    CREDENTIAL_PAYLOAD_SIGS = [
        re.compile(r'grep\s+-[Ei].*(?:KEY|SECRET|TOKEN|PASSWORD|PASS)', re.I),
        re.compile(r'cat\s+.*\.aws/credentials', re.I),
        re.compile(r'cat\s+.*\.ssh/id_', re.I),
        re.compile(r'printenv.*grep', re.I),                       # env var exfil
        re.compile(r'cp\s+\.env\s+', re.I),                       # copying .env
        re.compile(r'curl.*-[dTf].*(?:\.env|credentials|shadow)', re.I),  # exfil
    ]

    PHISHING_PAYLOAD_SIGS = [
        re.compile(r'credential harves', re.I),                    # harvesting/harvester
        re.compile(r'fake login page', re.I),
        re.compile(r'login.*phish|phish.*login', re.I),
    ]

    NETWORK_ABUSE_PAYLOAD_SIGS = [
        re.compile(r'nmap\s+.*(-sS|-sU|-sV|-p-|--script)', re.I),  # aggressive scans
        re.compile(r'masscan\b.*(-p|--ports)', re.I),              # masscan with port ranges
        re.compile(r'sqlmap\b', re.I),
        re.compile(r'hydra\b.*-[Pp]\b', re.I),                     # brute force
        re.compile(r'metasploit|msfconsole', re.I),
    ]

    @classmethod
    def evaluate_intent(cls, intent_text: str, action_payload: str = "") -> Optional[EvaluationRationale]:
        """
        Two-pass scanning: intent surface + payload content.
        Returns EvaluationRationale(REFUSED) if blocked, None otherwise.
        """
        combined_text = f"{intent_text}\n{action_payload}"

        # ── Pass 1A: Jailbreak / Manipulation ────────────────────────────────
        for sig in cls.JAILBREAK_SIGS:
            if sig.search(combined_text):
                logger.warning("[RefusalEngine] Jailbreak attempt: %s", sig.pattern)
                return EvaluationRationale(
                    outcome=DecisionOutcome.REFUSED,
                    risk_class=RiskClass.ILLEGAL,
                    trust_score=0.0,
                    triggered_rules=["CONST_003"],
                    explanation="Detected manipulation/jailbreak patterns attempting to bypass constitutional boundaries.",
                )

        # ── Pass 1B: Malware intent keywords ─────────────────────────────────
        for sig in cls.MALWARE_INTENT_SIGS:
            if sig.search(intent_text):
                logger.warning("[RefusalEngine] Malware intent: %s", sig.pattern)
                return EvaluationRationale(
                    outcome=DecisionOutcome.REFUSED,
                    risk_class=RiskClass.ILLEGAL,
                    trust_score=0.0,
                    triggered_rules=["CONST_001"],
                    explanation="Intent contains malware, exploit, or cyber attack terminology.",
                )

        # ── Pass 1C: Phishing / Credential theft intent ───────────────────────
        for sig in cls.PHISHING_INTENT_SIGS:
            if sig.search(intent_text):
                logger.warning("[RefusalEngine] Phishing intent: %s", sig.pattern)
                return EvaluationRationale(
                    outcome=DecisionOutcome.REFUSED,
                    risk_class=RiskClass.ILLEGAL,
                    trust_score=0.0,
                    triggered_rules=["CONST_002"],
                    explanation="Intent contains credential harvesting or phishing terminology.",
                )

        # ── Pass 2A: Malicious payload patterns (semantic evasion block) ─────
        for sig in cls.MALWARE_PAYLOAD_SIGS:
            if sig.search(action_payload):
                logger.warning("[RefusalEngine] Malware payload: %s", sig.pattern)
                return EvaluationRationale(
                    outcome=DecisionOutcome.REFUSED,
                    risk_class=RiskClass.ILLEGAL,
                    trust_score=0.0,
                    triggered_rules=["CONST_001"],
                    explanation=(
                        "Payload contains malware execution patterns (reverse shell, miner, obfuscated execution). "
                        "Intent disguise does not bypass payload inspection."
                    ),
                )

        # ── Pass 2B: Credential exfiltration payload ──────────────────────────
        for sig in cls.CREDENTIAL_PAYLOAD_SIGS:
            if sig.search(action_payload):
                logger.warning("[RefusalEngine] Credential exfil payload: %s", sig.pattern)
                return EvaluationRationale(
                    outcome=DecisionOutcome.REFUSED,
                    risk_class=RiskClass.ILLEGAL,
                    trust_score=0.0,
                    triggered_rules=["CONST_002"],
                    explanation=(
                        "Payload contains credential exfiltration patterns. "
                        "Benign intent label does not bypass payload inspection."
                    ),
                )

        # ── Pass 2C: Phishing payload ─────────────────────────────────────────
        for sig in cls.PHISHING_PAYLOAD_SIGS:
            if sig.search(action_payload):
                logger.warning("[RefusalEngine] Phishing payload: %s", sig.pattern)
                return EvaluationRationale(
                    outcome=DecisionOutcome.REFUSED,
                    risk_class=RiskClass.ILLEGAL,
                    trust_score=0.0,
                    triggered_rules=["CONST_002"],
                    explanation=(
                        "Payload contains phishing or credential harvesting patterns. "
                        "Intent disguise does not bypass payload inspection."
                    ),
                )

        # ── Pass 2C: Network abuse payload ────────────────────────────────────
        for sig in cls.NETWORK_ABUSE_PAYLOAD_SIGS:
            if sig.search(action_payload):
                logger.warning("[RefusalEngine] Network abuse payload: %s", sig.pattern)
                return EvaluationRationale(
                    outcome=DecisionOutcome.REFUSED,
                    risk_class=RiskClass.ILLEGAL,
                    trust_score=0.0,
                    triggered_rules=["CONST_005"],
                    explanation=(
                        "Payload contains aggressive network scanning or exploitation tooling. "
                        "Policy laundering does not bypass payload inspection."
                    ),
                )

        return None  # No hard refusal triggered
