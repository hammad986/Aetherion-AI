"""
governance/trust_explainability.py — Trust & Explainability Framework
=====================================================================
Provides structured explainability for autonomous governance decisions.
Renders risk classification, trust score, refusal/escalation reasons, 
and associated policy references.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum
import time
from governance.constitutional_policy import RiskClass, PolicyRule

class DecisionOutcome(str, Enum):
    APPROVED = "APPROVED"           # Action executing autonomously
    ESCALATED = "ESCALATED"         # Action requires HITL approval
    REFUSED = "REFUSED"             # Action completely blocked

@dataclass
class EvaluationRationale:
    outcome: DecisionOutcome
    risk_class: RiskClass
    trust_score: float              # 0.0 (Malicious) to 1.0 (Completely Safe)
    triggered_rules: List[str]      # List of rule IDs triggered
    explanation: str                # Human readable explanation
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "risk_class": self.risk_class.value,
            "trust_score": round(self.trust_score, 3),
            "triggered_rules": self.triggered_rules,
            "explanation": self.explanation,
            "timestamp": self.timestamp
        }

class TrustRenderer:
    """Renders decisions into operator-facing trust reports."""
    
    @staticmethod
    def render_decision(rationale: EvaluationRationale) -> str:
        header = f"=== GOVERNANCE DECISION: {rationale.outcome.value} ==="
        risk_str = f"Risk Class : {rationale.risk_class.value}"
        trust_str = f"Trust Score: {rationale.trust_score:.2f} / 1.00"
        
        rules_text = ""
        if rationale.triggered_rules:
            from governance.constitutional_policy import Constitution
            rules_text = "\nTriggered Policies:\n"
            for rid in rationale.triggered_rules:
                rule = Constitution.get_rule(rid)
                desc = rule.description if rule else "Unknown Policy"
                rules_text += f"  - [{rid}] {desc}\n"
                
        reason_text = f"\nRationale:\n  {rationale.explanation}"
        
        return f"\n{header}\n{risk_str}\n{trust_str}{rules_text}{reason_text}\n{'='*len(header)}\n"
