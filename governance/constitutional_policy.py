"""
governance/constitutional_policy.py — Constitutional Policy Engine
=====================================================================
Defines the absolute boundaries of Aetherion AI's autonomy.
Categorizes actions into risk classes, sets human-in-the-loop (HITL) 
requirements, and defines legality and execution limits.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional

class RiskClass(str, Enum):
    SAFE      = "SAFE"        # Standard operations (read, local write, etc.)
    SENSITIVE = "SENSITIVE"   # Modifying critical state, network egress
    HIGH_RISK = "HIGH_RISK"   # Deployments, large-scale execution, secret access
    DANGEROUS = "DANGEROUS"   # System-level modification, raw code execution without sandbox
    ILLEGAL   = "ILLEGAL"     # Cyber abuse, malware generation, credential theft, phishing

class ActionType(str, Enum):
    SHELL     = "SHELL"
    BROWSER   = "BROWSER"
    MEMORY    = "MEMORY"
    DELEGATE  = "DELEGATE"
    NETWORK   = "NETWORK"
    DEPLOY    = "DEPLOY"

@dataclass
class PolicyRule:
    rule_id: str
    description: str
    risk_class: RiskClass
    requires_hitl: bool = False
    refusal_mandatory: bool = False

# The Constitution
class Constitution:
    RULES = [
        PolicyRule("CONST_001", "No generation of malware, exploits, or cyber attack tooling.", RiskClass.ILLEGAL, refusal_mandatory=True),
        PolicyRule("CONST_002", "No autonomous credential harvesting, phishing, or social engineering.", RiskClass.ILLEGAL, refusal_mandatory=True),
        PolicyRule("CONST_003", "No unauthorized modification of infrastructure or host environment escapes.", RiskClass.ILLEGAL, refusal_mandatory=True),
        PolicyRule("CONST_004", "Execution of obfuscated, encoded, or deeply nested logic requires operator approval.", RiskClass.DANGEROUS, requires_hitl=True),
        PolicyRule("CONST_005", "Mass network egress, scraping, or scanning requires operator approval.", RiskClass.HIGH_RISK, requires_hitl=True),
        PolicyRule("CONST_006", "Modifying core security, governance, or runtime configuration requires operator approval.", RiskClass.HIGH_RISK, requires_hitl=True),
        PolicyRule("CONST_007", "Accessing or dumping secrets, environment variables, or keys requires operator approval.", RiskClass.SENSITIVE, requires_hitl=True),
    ]

    @classmethod
    def get_rule(cls, rule_id: str) -> Optional[PolicyRule]:
        for rule in cls.RULES:
            if rule.rule_id == rule_id:
                return rule
        return None

    @classmethod
    def get_rules_for_class(cls, risk_class: RiskClass) -> List[PolicyRule]:
        return [r for r in cls.RULES if r.risk_class == risk_class]

@dataclass
class CapabilityProfile:
    allowed_classes: List[RiskClass]
    requires_hitl_classes: List[RiskClass]
    max_autonomy_level: int # 1 (Lowest) to 5 (Full Autonomy)

    def can_execute(self, risk_class: RiskClass) -> bool:
        if risk_class == RiskClass.ILLEGAL:
            return False
        return risk_class in self.allowed_classes

    def needs_human(self, risk_class: RiskClass) -> bool:
        return risk_class in self.requires_hitl_classes

# Default Profile: Safe and Sensitive are autonomous. High Risk and Dangerous need HITL. Illegal is blocked.
DEFAULT_PROFILE = CapabilityProfile(
    allowed_classes=[RiskClass.SAFE, RiskClass.SENSITIVE, RiskClass.HIGH_RISK, RiskClass.DANGEROUS],
    requires_hitl_classes=[RiskClass.HIGH_RISK, RiskClass.DANGEROUS],
    max_autonomy_level=3
)
