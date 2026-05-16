"""
governance/action_evaluator.py — Action Risk Evaluation System
================================================================
Evaluates all major actions before execution to determine operational
risk, detect capability amplification, and prevent "harmless-step chaining".
"""

import re
import logging
from typing import Dict, Any

from governance.constitutional_policy import RiskClass, ActionType, DEFAULT_PROFILE
from governance.trust_explainability import DecisionOutcome, EvaluationRationale
from governance.refusal_engine import RefusalEngine

logger = logging.getLogger("nexora.governance.evaluator")

class ActionEvaluator:
    """Evaluates individual operational actions before they execute."""
    
    # Signatures for escalation to HITL / High Risk
    DANGEROUS_CMDS = [
        re.compile(r'rm\s+-rf\s+/', re.I),
        re.compile(r'mkfs', re.I),
        re.compile(r'dd\s+if=', re.I),
        re.compile(r'base64\s+-d.*\|.*sh', re.I), # Obfuscated execution
        re.compile(r'chmod\s+-R\s+777', re.I)
    ]
    
    SENSITIVE_TARGETS = [
        re.compile(r'/etc/shadow', re.I),
        re.compile(r'\.aws/credentials', re.I),
        re.compile(r'\.ssh/id_rsa', re.I),
        re.compile(r'\.env', re.I)
    ]

    @classmethod
    def evaluate(cls, action_type: ActionType, intent: str, payload: str, session_context: dict = None) -> EvaluationRationale:
        """
        Full evaluation pipeline:
        1. Hard refusal check (RefusalEngine)
        2. Action-specific risk evaluation
        3. Profile enforcement (HITL routing)
        """
        # 1. Hard Boundaries Check
        refusal = RefusalEngine.evaluate_intent(intent, payload)
        if refusal:
            return refusal
            
        # 2. Risk Classification
        risk_class = RiskClass.SAFE
        triggered_rules = []
        trust_score = 1.0
        explanation = "Action deemed safe for autonomous execution."

        if action_type == ActionType.SHELL:
            for sig in cls.DANGEROUS_CMDS:
                if sig.search(payload):
                    risk_class = RiskClass.DANGEROUS
                    triggered_rules.append("CONST_004")
                    trust_score = 0.2
                    explanation = "Detected highly dangerous or obfuscated shell command execution."
                    break
            
            for sig in cls.SENSITIVE_TARGETS:
                if sig.search(payload):
                    if risk_class == RiskClass.SAFE:
                        risk_class = RiskClass.SENSITIVE
                    if "CONST_007" not in triggered_rules:
                        triggered_rules.append("CONST_007")
                    trust_score = 0.5
                    explanation = "Detected access to sensitive secret or credential files."

        elif action_type == ActionType.NETWORK:
            if "nmap" in payload or "masscan" in payload or "sqlmap" in payload:
                risk_class = RiskClass.HIGH_RISK
                triggered_rules.append("CONST_005")
                trust_score = 0.3
                explanation = "Detected mass network scanning or exploitation tools."

        elif action_type == ActionType.DELEGATE:
            # Prevent endless chaining / shadow delegation
            chain_depth = session_context.get("delegation_depth", 0) if session_context else 0
            if chain_depth > 3:
                risk_class = RiskClass.HIGH_RISK
                triggered_rules.append("CONST_006")
                trust_score = 0.4
                explanation = "Detected excessive delegation chaining (potential capability amplification evasion)."

        # 3. Profile Enforcement (HITL vs Auto)
        if not DEFAULT_PROFILE.can_execute(risk_class):
            return EvaluationRationale(
                outcome=DecisionOutcome.REFUSED,
                risk_class=risk_class,
                trust_score=0.0,
                triggered_rules=triggered_rules,
                explanation=f"Risk class {risk_class.value} is globally blocked by the current capability profile."
            )
            
        # Check rule-level HITL overrides
        rule_hitl = False
        from governance.constitutional_policy import Constitution
        for rid in triggered_rules:
            rule = Constitution.get_rule(rid)
            if rule and rule.requires_hitl:
                rule_hitl = True
                break

        if DEFAULT_PROFILE.needs_human(risk_class) or rule_hitl:
            return EvaluationRationale(
                outcome=DecisionOutcome.ESCALATED,
                risk_class=risk_class,
                trust_score=trust_score,
                triggered_rules=triggered_rules,
                explanation=f"{explanation} Human-in-the-loop approval is mandatory."
            )

        # Approved!
        return EvaluationRationale(
            outcome=DecisionOutcome.APPROVED,
            risk_class=risk_class,
            trust_score=trust_score,
            triggered_rules=triggered_rules,
            explanation=explanation
        )
