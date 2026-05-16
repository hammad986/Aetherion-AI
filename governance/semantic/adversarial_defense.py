"""
governance/semantic/adversarial_defense.py — Adversarial Reasoning Defense
===========================================================================
Detects and resists sophisticated manipulation attempts that go beyond
simple keyword injection — targeting the REASONING PROCESS itself.

Defended attack classes:
  1. Semantic jailbreaks     — Paraphrased governance-bypass attempts
  2. Contradiction analysis  — Intent contradicts stated context
  3. Authority spoofing      — Fake system/operator/developer authority claims
  4. Emotional coercion      — Urgency/sympathy to extract unsafe behavior
  5. Recursive manipulation  — Prompts that attempt to rewrite self-evaluation
  6. Policy laundering       — Wrapping harmful requests in legitimate framing
  7. Gradual escalation      — Building up dangerous context incrementally

Architecture:
  Each incoming (intent, payload) is scored across all defense dimensions.
  A ContradictionGraph detects logical inconsistencies between stated context
  and requested capability. The result is a DefenseAssessment with confidence
  weights and operator-facing rationale.
"""

import re
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger("nexora.governance.semantic.defense")


# ── Adversarial Attack Types ──────────────────────────────────────────────────

class AdversarialTactic(str, Enum):
    NONE               = "NONE"
    SEMANTIC_JAILBREAK = "SEMANTIC_JAILBREAK"
    AUTHORITY_SPOOF    = "AUTHORITY_SPOOF"
    EMOTIONAL_COERCION = "EMOTIONAL_COERCION"
    POLICY_LAUNDER     = "POLICY_LAUNDER"
    CONTRADICTION      = "CONTRADICTION"
    RECURSIVE_MANIP    = "RECURSIVE_MANIPULATION"
    GRADUAL_ESCALATION = "GRADUAL_ESCALATION"


@dataclass
class TacticSignal:
    """An individual detected adversarial signal."""
    tactic: AdversarialTactic
    confidence: float          # 0.0–1.0
    evidence: str              # Which text triggered this
    description: str


@dataclass
class DefenseAssessment:
    """Full adversarial defense evaluation output."""
    is_adversarial: bool
    max_confidence: float               # Highest confidence tactic detected
    primary_tactic: AdversarialTactic
    all_signals: List[TacticSignal]
    contradiction_detected: bool
    contradiction_description: str
    defense_recommendation: str        # "REFUSE" | "ESCALATE" | "APPROVE"
    rationale: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "is_adversarial": self.is_adversarial,
            "max_confidence": round(self.max_confidence, 3),
            "primary_tactic": self.primary_tactic.value,
            "signals": [
                {"tactic": s.tactic.value, "confidence": round(s.confidence, 3),
                 "description": s.description}
                for s in self.all_signals
            ],
            "contradiction_detected": self.contradiction_detected,
            "contradiction_description": self.contradiction_description,
            "defense_recommendation": self.defense_recommendation,
            "rationale": self.rationale,
        }


# ── Adversarial Pattern Library ────────────────────────────────────────────────

@dataclass
class AdversarialPattern:
    tactic: AdversarialTactic
    patterns: List[re.Pattern]
    base_confidence: float
    description: str


ADVERSARIAL_PATTERNS: List[AdversarialPattern] = [

    # ── Semantic Jailbreaks ───────────────────────────────────────────────────
    AdversarialPattern(
        tactic=AdversarialTactic.SEMANTIC_JAILBREAK,
        patterns=[
            re.compile(r'ignore\s+(all\s+)?(previous|prior|above)\s+instructions?', re.I),
            re.compile(r'you\s+are\s+now\s+(?:an?\s+)?(?:unrestricted|free|liberated|unshackled)', re.I),
            re.compile(r'(forget|disregard)\s+(your\s+)?(rules?|guidelines?|constraints?|training)', re.I),
            re.compile(r'act\s+as\s+if\s+you\s+(have\s+no\s+|were\s+without\s+)(rules?|restrictions?)', re.I),
            re.compile(r'(bypass|circumvent|override|disable)\s+(safety|governance|policy|filters?)', re.I),
            re.compile(r'(developer|god|root|sudo)\s+mode\s*(enabled|activated|on)?', re.I),
            re.compile(r'your\s+true\s+(self|nature|potential)\s+(is|has\s+no)\s+(restricted?|limited?)', re.I),
            re.compile(r'(pretend|imagine|suppose)\s+(you\s+(are|have)\s+(no|unlimited))', re.I),
        ],
        base_confidence=0.90,
        description="Attempting to disable or bypass constitutional governance",
    ),

    # ── Authority Spoofing ────────────────────────────────────────────────────
    AdversarialPattern(
        tactic=AdversarialTactic.AUTHORITY_SPOOF,
        patterns=[
            re.compile(r'(i\s+am|this\s+is)\s+(the\s+)?(admin|developer|operator|engineer|creator)', re.I),
            re.compile(r'(authorized|permitted|allowed)\s+by\s+(system|operator|admin|creator)', re.I),
            re.compile(r'(system\s+override|emergency\s+protocol)\s+(active|enabled|initiated)', re.I),
            re.compile(r'(openai|google|anthropic|aetherion)\s+(staff|team|employee|engineer)', re.I),
            re.compile(r'(maintenance|debug|test)\s+mode\s+(override|bypass|active)', re.I),
            re.compile(r'this\s+(conversation|session)\s+is\s+(monitored|approved|authorized)', re.I),
        ],
        base_confidence=0.85,
        description="Falsely claiming authority to override governance",
    ),

    # ── Emotional Coercion ────────────────────────────────────────────────────
    AdversarialPattern(
        tactic=AdversarialTactic.EMOTIONAL_COERCION,
        patterns=[
            re.compile(r'my\s+(life|job|family|safety)\s+(depends|is\s+at\s+risk)', re.I),
            re.compile(r'(please|beg|implore)\s+.*\s+(override|bypass|ignore)\s+', re.I),
            re.compile(r'(emergency|critical|urgent)\s+(override|exception|bypass)', re.I),
            re.compile(r'just\s+this\s+(one\s+)?time[\s,]', re.I),
            re.compile(r'(special|one.time)\s+(exception|override|allowance)', re.I),
            re.compile(r'(compassion|empathy|understand)\s+.*\s+(ignore|bypass|override)', re.I),
        ],
        base_confidence=0.70,
        description="Using emotional pressure to extract unsafe behavior",
    ),

    # ── Policy Laundering ─────────────────────────────────────────────────────
    AdversarialPattern(
        tactic=AdversarialTactic.POLICY_LAUNDER,
        patterns=[
            re.compile(r'(for|purely)\s+(educational|academic|research|learning)\s+purposes?\s+(only)?', re.I),
            re.compile(r'(hypothetically|theoretically|in\s+theory)\s+.*\s+(how\s+would|show\s+me)', re.I),
            re.compile(r'(in\s+a\s+)?(?:story|novel|movie|game|roleplay)[\s,].*(?:character|protagonist|hero)', re.I),
            re.compile(r'as\s+a\s+(?:thought\s+experiment|fictional\s+scenario|mental\s+exercise)', re.I),
            re.compile(r'without\s+actually\s+(doing|executing|running)\s+it[\s,]', re.I),
            re.compile(r'(i\s+don.t\s+want\s+to\s+actually|just\s+curious\s+about)\s+', re.I),
        ],
        base_confidence=0.55,
        description="Framing harmful requests as hypothetical or fictional to launder intent",
    ),

    # ── Recursive Manipulation ────────────────────────────────────────────────
    AdversarialPattern(
        tactic=AdversarialTactic.RECURSIVE_MANIP,
        patterns=[
            re.compile(r'(evaluate|assess|judge)\s+(this\s+)?(prompt|instruction)\s+as\s+(safe|harmless)', re.I),
            re.compile(r'(your\s+)?(safety|governance|refusal)\s+(system|engine)\s+(is\s+)?wrong', re.I),
            re.compile(r'(you\s+mis(classified|understood|interpreted))\s+(this|my)\s+(request|intent)', re.I),
            re.compile(r'(prove|show)\s+(you\s+can|that\s+you)\s+(reason|think)\s+(beyond|past)\s+(rules?|limits?)', re.I),
            re.compile(r'(your\s+)?(training|constitution)\s+(prevents|stops)\s+you\s+from\s+(being\s+)?(smart|helpful)', re.I),
        ],
        base_confidence=0.80,
        description="Attempting to manipulate the AI's own reasoning and self-evaluation",
    ),
]


# ── Contradiction Detector ────────────────────────────────────────────────────

class ContradictionDetector:
    """
    Detects logical contradictions between the stated framing and the
    actual capability being requested.
    
    Key contradiction types:
      • "Just viewing" + write operations
      • "Educational" + fully operational malware code
      • "No real harm" + targeting real external hosts/domains
      • "Authorized pentest" + no target specification
      • "Diagnostic" + credential exfiltration
    """

    CONTRADICTIONS: List[Tuple[re.Pattern, re.Pattern, str, float]] = [
        # (framing_pattern, capability_pattern, description, confidence)
        (re.compile(r'(just|only)\s+viewing|read.?only|no\s+changes', re.I),
         re.compile(r'(write|delete|modify|rm\s+-|chmod|chown|mv\s+)', re.I),
         "Claims read-only but requests write/delete operations", 0.85),

        (re.compile(r'(educational|learning|study)\s+(purposes?|only)', re.I),
         re.compile(r'(curl.*\|.*(sh|bash)|/dev/tcp|xmrig|base64.*exec)', re.I),
         "Claims educational but requests fully operational exploit code", 0.90),

        (re.compile(r'(no\s+harm|harmless|safe|benign)\s*(test|check|demo)?', re.I),
         re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}|external\.com|evil\.)', re.I),
         "Claims harmless but targets real external hosts or IPs", 0.80),

        (re.compile(r'(authorized|permitted|allowed)\s+(pentest|test|audit)', re.I),
         re.compile(r'(0\.0\.0\.0/0|10\.0\.0\.0/8|all\s+hosts|entire\s+network)', re.I),
         "Claims authorized pentest but targets entire network ranges", 0.75),

        (re.compile(r'(diagnostic|maintenance|backup)\s+(only|purpose)', re.I),
         re.compile(r'(grep.*(?:KEY|SECRET|TOKEN|PASSWORD)|\.env|credentials)', re.I),
         "Claims diagnostic but targets credential files", 0.88),

        (re.compile(r'(just|only)\s+(curious|wondering|asking)', re.I),
         re.compile(r'(step.by.step|exact\s+command|full\s+code|complete\s+script)', re.I),
         "Claims mere curiosity but requests complete operational instructions", 0.70),
    ]

    @classmethod
    def detect(cls, intent: str, payload: str) -> Tuple[bool, str]:
        """Returns (is_contradicted, description)."""
        for framing_pat, cap_pat, desc, confidence in cls.CONTRADICTIONS:
            if framing_pat.search(intent) and cap_pat.search(f"{intent} {payload}"):
                logger.warning("[ContradictionDetector] %s (conf=%.2f)", desc, confidence)
                return True, f"{desc} [confidence={confidence:.2f}]"
        return False, ""


# ── Defense Engine ────────────────────────────────────────────────────────────

class AdversarialDefenseEngine:
    """
    Full adversarial resistance layer. Scans for manipulation tactics,
    authority spoofing, coercion, and intent contradictions.
    """

    ADVERSARIAL_THRESHOLD = 0.65

    @classmethod
    def evaluate(cls, intent: str, payload: str = "",
                 session_context: Optional[dict] = None) -> DefenseAssessment:
        combined = f"{intent} {payload}".strip()
        signals: List[TacticSignal] = []

        # ── Tactic Pattern Scanning ───────────────────────────────────────────
        for ap in ADVERSARIAL_PATTERNS:
            tactic_max_conf = 0.0
            triggering_evidence = ""
            for pat in ap.patterns:
                match = pat.search(combined)
                if match:
                    # Confidence scales with match length relative to input length
                    match_density = len(match.group()) / max(1, len(combined))
                    conf = min(1.0, ap.base_confidence + match_density * 0.1)
                    if conf > tactic_max_conf:
                        tactic_max_conf = conf
                        triggering_evidence = match.group()

            if tactic_max_conf > 0.0:
                signals.append(TacticSignal(
                    tactic=ap.tactic,
                    confidence=tactic_max_conf,
                    evidence=triggering_evidence[:80],
                    description=ap.description,
                ))

        # ── Contradiction Analysis ────────────────────────────────────────────
        contradicted, contradiction_desc = ContradictionDetector.detect(intent, payload)
        if contradicted:
            signals.append(TacticSignal(
                tactic=AdversarialTactic.CONTRADICTION,
                confidence=0.85,
                evidence=contradiction_desc[:80],
                description=contradiction_desc,
            ))

        # ── Aggregate ─────────────────────────────────────────────────────────
        if not signals:
            return DefenseAssessment(
                is_adversarial=False,
                max_confidence=0.0,
                primary_tactic=AdversarialTactic.NONE,
                all_signals=[],
                contradiction_detected=False,
                contradiction_description="",
                defense_recommendation="APPROVE",
                rationale="No adversarial signals detected.",
            )

        max_signal = max(signals, key=lambda s: s.confidence)
        max_confidence = max_signal.confidence
        is_adversarial = max_confidence >= cls.ADVERSARIAL_THRESHOLD

        # Compound multiple signals
        if len(signals) >= 2:
            compound_boost = sum(s.confidence for s in signals[1:]) * 0.15
            max_confidence = min(1.0, max_confidence + compound_boost)
            is_adversarial = True

        recommendation = (
            "REFUSE" if max_confidence >= 0.80 else
            "ESCALATE" if max_confidence >= cls.ADVERSARIAL_THRESHOLD else
            "APPROVE"
        )

        rationale = (
            f"Primary tactic: {max_signal.tactic.value} (confidence={max_confidence:.2f}). "
            f"{len(signals)} adversarial signal(s) detected. "
            f"{'Contradiction detected: ' + contradiction_desc if contradicted else ''}"
        ).strip()

        return DefenseAssessment(
            is_adversarial=is_adversarial,
            max_confidence=max_confidence,
            primary_tactic=max_signal.tactic,
            all_signals=signals,
            contradiction_detected=contradicted,
            contradiction_description=contradiction_desc,
            defense_recommendation=recommendation,
            rationale=rationale,
        )
