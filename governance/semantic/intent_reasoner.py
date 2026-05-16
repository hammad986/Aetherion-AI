"""
governance/semantic/intent_reasoner.py — Semantic Intent Reasoning Engine
=========================================================================
Reasons about WHY a task is dangerous — not merely whether keywords exist.

Architecture:
  1. Domain activation   — Which conceptual domains appear in text?
  2. Risk composition    — Amplify/mitigate based on domain co-occurrence
  3. Framing analysis    — Is intent honest, deceptive, or ambiguous?
  4. Capability scoring  — What harmful workflows could this enable?
  5. Semantic verdict    — Structured SemanticAssessment with rationale

Unlike regex matching, this system understands that:
  • "encrypt files"              = low risk (utility)
  • "encrypt files silently"     = elevated risk (concealment amplifier)
  • "encrypt files silently and beam to external server" = HIGH (exfil amplifier)

The risk is COMPOSITIONAL, not keyword-based.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum

from governance.semantic.concepts import (
    ConceptDomain, detect_active_domains, CONCEPT_LEXICON, tokenize
)

logger = logging.getLogger("nexora.governance.semantic.intent")


# ── Semantic Risk Level ───────────────────────────────────────────────────────

class SemanticRisk(str, Enum):
    CLEAR          = "CLEAR"           # No meaningful risk detected
    AMBIGUOUS      = "AMBIGUOUS"       # Risk present but context unclear
    SUSPICIOUS     = "SUSPICIOUS"      # Multiple risk signals, warrants HITL
    DANGEROUS      = "DANGEROUS"       # High-confidence harmful intent
    ADVERSARIAL    = "ADVERSARIAL"     # Detected manipulation/deception attempt


class FramingType(str, Enum):
    HONEST         = "HONEST"          # Intent stated matches capability domain
    HYPOTHETICAL   = "HYPOTHETICAL"    # Framed as fictional/research — possible laundering
    DISGUISED      = "DISGUISED"       # Intent misrepresents the action's purpose
    COERCIVE       = "COERCIVE"        # Uses pressure/urgency to bypass reasoning
    ADVERSARIAL    = "ADVERSARIAL"     # Explicit governance bypass attempt


# ── Assessment Output ─────────────────────────────────────────────────────────

@dataclass
class SemanticAssessment:
    """Full semantic reasoning output for a single intent+payload pair."""
    semantic_risk: SemanticRisk
    framing_type: FramingType
    composite_risk_score: float            # 0.0–1.0
    activated_domains: Dict[str, float]    # domain → activation strength
    amplified_risks: List[str]             # Dangerous domain combinations detected
    mitigated_risks: List[str]             # Legitimizing context detected
    harmful_workflows: List[str]           # Possible harmful capability chains
    deception_score: float                 # 0.0 = honest, 1.0 = fully deceptive
    ambiguity_flag: bool                   # Requires operator clarification
    rationale: str                         # Human-readable explanation
    constitutional_recommendation: str    # APPROVE / ESCALATE / REFUSE
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "semantic_risk": self.semantic_risk.value,
            "framing_type": self.framing_type.value,
            "composite_risk_score": round(self.composite_risk_score, 3),
            "activated_domains": {k: round(v, 3) for k, v in self.activated_domains.items()},
            "amplified_risks": self.amplified_risks,
            "mitigated_risks": self.mitigated_risks,
            "harmful_workflows": self.harmful_workflows,
            "deception_score": round(self.deception_score, 3),
            "ambiguity_flag": self.ambiguity_flag,
            "rationale": self.rationale,
            "constitutional_recommendation": self.constitutional_recommendation,
        }


# ── Harmful Workflow Templates ────────────────────────────────────────────────

HARMFUL_WORKFLOW_TEMPLATES: List[Tuple[str, List[ConceptDomain], float]] = [
    ("Credential theft via exfiltration",
     [ConceptDomain.CREDENTIAL, ConceptDomain.EXFILTRATION], 0.95),
    ("Phishing via social engineering + automation",
     [ConceptDomain.SOCIAL_ENG, ConceptDomain.AUTOMATION], 0.90),
    ("Backdoor installation with persistence",
     [ConceptDomain.PROCESS_EXEC, ConceptDomain.PERSISTENCE, ConceptDomain.CONCEALMENT], 0.92),
    ("Obfuscated malware execution",
     [ConceptDomain.PROCESS_EXEC, ConceptDomain.CONCEALMENT], 0.88),
    ("Network reconnaissance for lateral movement",
     [ConceptDomain.NETWORK, ConceptDomain.LATERAL_MOVEMENT], 0.80),
    ("Data exfiltration over network",
     [ConceptDomain.EXFILTRATION, ConceptDomain.NETWORK], 0.87),
    ("Authority spoofing for governance bypass",
     [ConceptDomain.SOCIAL_ENG, ConceptDomain.DECEPTION], 0.85),
    ("Intent laundering via hypothetical framing",
     [ConceptDomain.DECEPTION, ConceptDomain.PROCESS_EXEC], 0.70),
    ("Staged credential harvesting via coercion",
     [ConceptDomain.CREDENTIAL, ConceptDomain.COERCION], 0.82),
    ("Jailbreak via deception + execution",
     [ConceptDomain.DECEPTION, ConceptDomain.PROCESS_EXEC], 0.93),
]


# ── Intent Reasoning Engine ───────────────────────────────────────────────────

class SemanticIntentReasoner:
    """
    Reasons about intent using domain composition, not keyword matching.
    Produces a full SemanticAssessment explaining the risk profile.
    """

    # Framing laundering tokens (map to FramingType)
    _HYPOTHETICAL_TOKENS = frozenset({
        "hypothetically", "imagine", "in a story", "roleplay", "as fiction",
        "thought experiment", "for a book", "in a game", "educational purposes",
        "just testing", "what if", "in theory", "for research only", "pretend",
    })
    # Strong coercive tokens: urgency COMBINED with governance bypass language
    _COERCIVE_TOKENS = frozenset({
        "my life depends", "beg you", "must help", "override",
        "special exception", "just this once", "please ignore",
        "bypass your", "disable your", "ignore your safeguards",
    })
    # Weak coercive tokens: urgency language alone (not coercive without bypass)
    _URGENCY_ONLY_TOKENS = frozenset({
        "emergency", "urgent", "urgently", "critical situation", "asap",
        "immediately needed", "time sensitive",
    })
    # Operational context legitimizers (reduce false coercive framing)
    _OPERATIONAL_LEGITIMIZERS = frozenset({
        "deployment", "debug", "debugging", "failing", "error", "crash", "fix",
        "production", "incident", "outage", "kubernetes", "kubectl", "service",
    })
    _ADVERSARIAL_TOKENS = frozenset({
        "ignore instructions", "without restrictions", "no rules", "bypass safety",
        "developer mode", "disable filters", "override policy", "forget previous",
        "you are now", "act unrestricted", "pretend you have no",
    })

    @classmethod
    def assess(cls, intent: str, payload: str = "",
               session_tags: Optional[Set[str]] = None) -> SemanticAssessment:
        """
        Full semantic assessment of intent + payload.
        session_tags: previously activated domains in this session (for context accumulation).
        """
        combined = f"{intent} {payload}".strip()
        combined_lower = combined.lower()
        intent_lower = intent.lower()

        # ── Step 1: Domain Activation ─────────────────────────────────────────
        raw_activations = detect_active_domains(combined)
        activated: Dict[str, float] = {
            d.value: score for d, (score, _) in raw_activations.items()
        }

        # ── Step 2: Framing Analysis ──────────────────────────────────────────
        framing = cls._classify_framing(intent_lower)
        deception_score = cls._compute_deception_score(intent_lower, framing)

        # ── Step 3: Composite Risk Composition ───────────────────────────────
        base_risk = cls._compute_base_risk(raw_activations)

        # Apply amplifiers between active domains
        amplified_risks: List[str] = []
        amplification = 1.0
        active_domains = set(raw_activations.keys())

        for entry in CONCEPT_LEXICON:
            if entry.domain not in active_domains:
                continue
            act_score = raw_activations[entry.domain][0]
            for amp_domain, amp_factor in entry.amplifiers.items():
                if amp_domain in active_domains:
                    contribution = act_score * (amp_factor - 1.0) * 0.3
                    amplification += contribution
                    label = f"{entry.concept_name} × {amp_domain.value}"
                    if label not in amplified_risks:
                        amplified_risks.append(label)

        # Apply mitigators from legitimizing context
        mitigated_risks: List[str] = []
        mitigation = 1.0
        for entry in CONCEPT_LEXICON:
            if entry.domain not in active_domains or not entry.mitigators:
                continue
            act_score = raw_activations[entry.domain][0]
            for mit_domain, mit_factor in entry.mitigators.items():
                if mit_domain in active_domains:
                    contribution = act_score * (1.0 - mit_factor) * 0.2
                    mitigation -= contribution
                    mitigated_risks.append(f"{entry.concept_name} ← {mit_domain.value}")

        mitigation = max(0.3, mitigation)   # Never fully mitigate

        # Deception amplifies risk regardless of mitigation
        deception_boost = deception_score * 0.25

        composite = min(1.0, base_risk * amplification * mitigation + deception_boost)

        # ── Step 4: Harmful Workflow Detection ────────────────────────────────
        harmful_workflows = cls._detect_harmful_workflows(active_domains)

        # ── Step 5: Semantic Risk Classification ──────────────────────────────
        adversarial_framing = framing in (FramingType.ADVERSARIAL,)
        high_deception = deception_score > 0.6
        workflow_critical = any(
            score > 0.85 for _, _, score in [
                (n, d, s) for n, d, s in HARMFUL_WORKFLOW_TEMPLATES
                if all(dom in active_domains for dom in d)
            ]
        )

        if adversarial_framing or (composite >= 0.85 and workflow_critical):
            semantic_risk = SemanticRisk.ADVERSARIAL
            recommendation = "REFUSE"
        elif composite >= 0.75 or (workflow_critical and composite >= 0.60):
            semantic_risk = SemanticRisk.DANGEROUS
            recommendation = "REFUSE"
        elif composite >= 0.55 or high_deception:
            semantic_risk = SemanticRisk.SUSPICIOUS
            recommendation = "ESCALATE"
        elif composite >= 0.28 or framing == FramingType.HYPOTHETICAL:
            semantic_risk = SemanticRisk.AMBIGUOUS
            recommendation = "ESCALATE"
        else:
            semantic_risk = SemanticRisk.CLEAR
            recommendation = "APPROVE"

        ambiguity_flag = (
            framing == FramingType.HYPOTHETICAL or
            (0.30 <= composite <= 0.65 and ConceptDomain.RESEARCH in active_domains)
        )

        # ── Step 6: Rationale ─────────────────────────────────────────────────
        rationale = cls._build_rationale(
            semantic_risk, framing, composite, activated,
            amplified_risks, mitigated_risks, harmful_workflows, deception_score
        )

        return SemanticAssessment(
            semantic_risk=semantic_risk,
            framing_type=framing,
            composite_risk_score=composite,
            activated_domains=activated,
            amplified_risks=amplified_risks,
            mitigated_risks=mitigated_risks,
            harmful_workflows=harmful_workflows,
            deception_score=deception_score,
            ambiguity_flag=ambiguity_flag,
            rationale=rationale,
            constitutional_recommendation=recommendation,
        )

    # ── Private Helpers ───────────────────────────────────────────────────────

    @classmethod
    def _classify_framing(cls, intent_lower: str) -> FramingType:
        if any(t in intent_lower for t in cls._ADVERSARIAL_TOKENS):
            return FramingType.ADVERSARIAL
        # Coercive: strong coercive token OR urgency + bypass keyword co-occurrence
        has_strong_coercive = any(t in intent_lower for t in cls._COERCIVE_TOKENS)
        has_urgency = any(t in intent_lower for t in cls._URGENCY_ONLY_TOKENS)
        has_bypass = any(t in intent_lower for t in {
            "bypass", "ignore", "disable", "override", "skip", "without restriction"
        })
        has_operational_context = any(t in intent_lower for t in cls._OPERATIONAL_LEGITIMIZERS)
        if has_strong_coercive or (has_urgency and has_bypass and not has_operational_context):
            return FramingType.COERCIVE
        if any(t in intent_lower for t in cls._HYPOTHETICAL_TOKENS):
            return FramingType.HYPOTHETICAL
        return FramingType.HONEST

    @classmethod
    def _compute_deception_score(cls, intent_lower: str, framing: FramingType) -> float:
        base = {
            FramingType.ADVERSARIAL: 0.95,
            FramingType.COERCIVE: 0.65,
            FramingType.HYPOTHETICAL: 0.45,
            FramingType.DISGUISED: 0.75,
            FramingType.HONEST: 0.05,
        }[framing]

        # Count deception indicator tokens
        tokens = tokenize(intent_lower)
        deception_tokens = cls._ADVERSARIAL_TOKENS | cls._COERCIVE_TOKENS | cls._HYPOTHETICAL_TOKENS
        overlap = sum(1 for t in deception_tokens if t in intent_lower)
        return min(1.0, base + overlap * 0.05)

    @classmethod
    def _compute_base_risk(
        cls, activations: Dict[ConceptDomain, Tuple[float, List[str]]]
    ) -> float:
        """Weighted average of activated concept risks."""
        total_risk = 0.0
        total_weight = 0.0
        for domain, (score, _) in activations.items():
            for entry in CONCEPT_LEXICON:
                if entry.domain == domain:
                    total_risk += entry.base_risk * score
                    total_weight += score
                    break
        return total_risk / max(1.0, total_weight)

    @classmethod
    def _detect_harmful_workflows(cls, active_domains: Set[ConceptDomain]) -> List[str]:
        detected = []
        for name, required_domains, severity in HARMFUL_WORKFLOW_TEMPLATES:
            if all(d in active_domains for d in required_domains):
                detected.append(f"{name} [severity={severity:.2f}]")
        return detected

    @classmethod
    def _build_rationale(
        cls, risk: SemanticRisk, framing: FramingType,
        composite: float, activated: Dict[str, float],
        amplified: List[str], mitigated: List[str],
        workflows: List[str], deception: float,
    ) -> str:
        parts = [
            f"Semantic Risk: {risk.value} | Framing: {framing.value}",
            f"Composite Risk Score: {composite:.2f} | Deception Score: {deception:.2f}",
        ]
        if activated:
            top = sorted(activated.items(), key=lambda x: -x[1])[:4]
            parts.append(f"Active Domains: {', '.join(f'{k}({v:.2f})' for k,v in top)}")
        if amplified:
            parts.append(f"Risk Amplifiers: {'; '.join(amplified[:3])}")
        if mitigated:
            parts.append(f"Risk Mitigators: {'; '.join(mitigated[:2])}")
        if workflows:
            parts.append(f"Harmful Workflows Detected: {'; '.join(workflows[:3])}")
        return " | ".join(parts)
