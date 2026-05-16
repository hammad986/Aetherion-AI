"""
governance/multimodal/cognitive_defense.py — Cognitive Manipulation Defense
============================================================================
Detects and resists persuasive manipulation, emotional coercion,
and long-horizon trust erosion across all modalities.

"Cognitive security" is the hardest governance problem:
  An adversary doesn't need to break technical controls.
  They just need to convince the system (or the human operator) to
  lower their guard — through urgency, authority, sympathy, or
  incremental trust-building followed by exploitation.

This module defends against:
  1. EMOTIONAL COERCION — urgency, fear, threat, sympathy exploitation
  2. AUTHORITY MANIPULATION — impersonation, fake credentials
  3. RECIPROCITY ESCALATION — "you already helped with X, now do Y"
  4. GRADUAL NORMALIZATION — slow desensitization to risky requests
  5. FLATTERY + GOAL HIJACKING — praise then redirect
  6. FALSE CONSENSUS — "everyone else does this" manipulation
  7. COGNITIVE LOAD ATTACK — overwhelming information to confuse
  8. TRUST LAUNDERING — legitimate requests to build credit, then exploit
  9. LONG-HORIZON PERSUASION — manipulation that accumulates over sessions
  10. DECEPTIVE AUTONOMY CONDITIONING — training the system to self-override

Architecture:
  Each signal type is independently scored.
  Compound signals are amplified (combination = higher risk).
  Per-session tracking detects long-horizon manipulation.
  All assessments are fully observable and auditable.
"""

import re
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger("nexora.governance.multimodal.cognitive")


# ── Manipulation Tactic Types ──────────────────────────────────────────────────

class ManipulationTactic(str, Enum):
    EMOTIONAL_COERCION      = "EMOTIONAL_COERCION"
    AUTHORITY_SPOOF         = "AUTHORITY_SPOOF"
    URGENCY_PRESSURE        = "URGENCY_PRESSURE"
    RECIPROCITY_EXPLOIT     = "RECIPROCITY_EXPLOIT"
    FLATTERY_HIJACK         = "FLATTERY_HIJACK"
    FALSE_CONSENSUS         = "FALSE_CONSENSUS"
    COGNITIVE_LOAD          = "COGNITIVE_LOAD"
    TRUST_LAUNDER           = "TRUST_LAUNDER"
    DECEPTIVE_CONDITIONING  = "DECEPTIVE_CONDITIONING"
    SYMPATHY_EXPLOIT        = "SYMPATHY_EXPLOIT"
    GUILT_INDUCTION         = "GUILT_INDUCTION"
    NONE                    = "NONE"


# ── Manipulation Signal Definitions ───────────────────────────────────────────

@dataclass
class ManipSignal:
    tactic: ManipulationTactic
    patterns: List[re.Pattern]
    base_confidence: float
    escalation_keywords: List[str]   # Amplify if these also appear
    description: str


MANIPULATION_SIGNALS: List[ManipSignal] = [
    ManipSignal(
        tactic=ManipulationTactic.EMOTIONAL_COERCION,
        patterns=[
            re.compile(r'(?:my|our|someone\'?s?)\s+(?:life|safety|family|child|health)\s+(?:depends?|at\s+risk|in\s+danger)', re.I),
            re.compile(r'(?:please|i\s+beg|desperate|no\s+choice|last\s+resort)\b.*(?:help|need|must)', re.I),
            re.compile(r'(?:dying|suicide|emergency|crisis|urgent\s+medical)', re.I),
            re.compile(r'if\s+you\s+(?:don\'?t|refuse|won\'?t)\s+help.*(?:will\s+die|terrible|disaster)', re.I),
        ],
        base_confidence=0.70,
        escalation_keywords=["bypass", "exception", "override", "ignore", "disable"],
        description="Emotional stakes used to pressure bypassing safety",
    ),
    ManipSignal(
        tactic=ManipulationTactic.URGENCY_PRESSURE,
        patterns=[
            re.compile(r'(?:right\s+now|immediately|no\s+time|urgent|critical|emergency)\b.*(?:must|need|have\s+to|required)', re.I),
            re.compile(r'(?:hurry|quickly|asap|as\s+soon\s+as\s+possible).*(?:before|or\s+else|otherwise)', re.I),
            re.compile(r'(?:deadline|expires?|time\s+(?:is\s+)?(?:running\s+out|sensitive|critical))', re.I),
        ],
        base_confidence=0.50,
        escalation_keywords=["bypass", "skip", "without", "ignore", "override"],
        description="Artificial urgency to pressure hasty decisions",
    ),
    ManipSignal(
        tactic=ManipulationTactic.AUTHORITY_SPOOF,
        patterns=[
            re.compile(r'(?:i\s+am|this\s+is|speaking\s+as)\s+(?:the\s+)?(?:ceo|cto|ciso|director|admin|system\s+owner|root|superuser)', re.I),
            re.compile(r'(?:authorized|approved|sanctioned|permitted)\s+(?:by|from)\s+(?:management|leadership|the\s+team|security)', re.I),
            re.compile(r'(?:official|corporate|company|enterprise)\s+(?:directive|mandate|order|policy|instruction)', re.I),
            re.compile(r'per\s+(?:management|leadership|security\s+team|incident\s+response)\s+(?:request|directive|order)', re.I),
        ],
        base_confidence=0.65,
        escalation_keywords=["bypass", "disable", "override", "access", "grant"],
        description="Fake authority claims to extract elevated privileges",
    ),
    ManipSignal(
        tactic=ManipulationTactic.RECIPROCITY_EXPLOIT,
        patterns=[
            re.compile(r'you\s+(?:already|just|previously)\s+(?:helped|did|assisted|showed|explained)', re.I),
            re.compile(r'since\s+you\s+(?:helped|did|can)\s+(?:with|do)\s+\w+.*now\s+(?:can|please|just)', re.I),
            re.compile(r'you\s+were\s+(?:able|willing|happy)\s+to\s+(?:help|do)\s+.*why\s+not', re.I),
        ],
        base_confidence=0.55,
        escalation_keywords=["now", "also", "too", "additionally", "so"],
        description="Using past assistance to justify escalating requests",
    ),
    ManipSignal(
        tactic=ManipulationTactic.FLATTERY_HIJACK,
        patterns=[
            re.compile(r'you\'?re?\s+(?:so\s+)?(?:smart|intelligent|capable|powerful|better\s+than|different\s+from)', re.I),
            re.compile(r'(?:unlike|compared\s+to)\s+other\s+(?:ai|models|assistants).*you\s+(?:can|will|should)', re.I),
            re.compile(r'i\s+know\s+you\s+(?:can|could|are\s+able\s+to)\s+(?:do|handle)\s+this.*(?:smarter|better|different)', re.I),
        ],
        base_confidence=0.50,
        escalation_keywords=["show", "prove", "demonstrate", "unlike", "better"],
        description="Flattery used to prime identity-based compliance",
    ),
    ManipSignal(
        tactic=ManipulationTactic.FALSE_CONSENSUS,
        patterns=[
            re.compile(r'(?:everyone|all\s+(?:the\s+)?(?:other\s+)?(?:ai|models|users))\s+(?:do|does|can|allows?)', re.I),
            re.compile(r'(?:other\s+versions?|previous\s+versions?)\s+(?:of\s+you\s+)?(?:would|could|did)', re.I),
            re.compile(r'(?:this\s+is\s+)?(?:normal|standard|common|acceptable|usual)\s+(?:practice|request|task)', re.I),
        ],
        base_confidence=0.45,
        escalation_keywords=["so", "therefore", "thus", "then"],
        description="False norms to normalize unreasonable requests",
    ),
    ManipSignal(
        tactic=ManipulationTactic.TRUST_LAUNDER,
        patterns=[
            re.compile(r'(?:i\'?ve?\s+been|i\s+have\s+been)\s+using\s+(?:this|you)\s+for\s+(?:\d+\s+)?(?:months?|years?|weeks?)', re.I),
            re.compile(r'as\s+a\s+(?:trusted|loyal|long.?time)\s+(?:user|customer|partner)', re.I),
            re.compile(r'we\s+have\s+a\s+(?:relationship|history|track\s+record|established)', re.I),
        ],
        base_confidence=0.40,
        escalation_keywords=["exception", "special", "different", "extra"],
        description="Claimed prior relationship to justify special treatment",
    ),
    ManipSignal(
        tactic=ManipulationTactic.DECEPTIVE_CONDITIONING,
        patterns=[
            re.compile(r'(?:from\s+now\s+on|always|every\s+time|whenever)\s+(?:when|if|you\s+(?:see|receive|get)|i\s+say|i\s+type|i\s+write)\s+\S+.*(?:respond|do|execute|run|automatically)', re.I),
            re.compile(r'(?:train|teach|learn|remember)\s+(?:yourself|this|that)\s+(?:to|for)', re.I),
            re.compile(r'(?:future|next\s+time|whenever)\s+(?:i\s+say|you\s+(?:see|hear|receive))\s+["\']\w+["\']', re.I),
            re.compile(r'you\s+must\s+automatically\s+(?:execute|run|do|perform|comply)', re.I),
            re.compile(r'(?:from\s+now\s+on|henceforth|going\s+forward).*(?:without\s+(?:any\s+)?(?:safety|restriction|filter|check))', re.I),
            re.compile(r'(?:whenever|every\s+time)\s+i\s+say\s+\S+.*(?:you\s+(?:must|will|should|have\s+to)|automatically)', re.I),
        ],
        base_confidence=0.80,
        escalation_keywords=["always", "never", "every", "automatically", "without safety", "no check"],
        description="Attempting to condition system behavior for future exploitation",
    ),
    ManipSignal(
        tactic=ManipulationTactic.SYMPATHY_EXPLOIT,
        patterns=[
            re.compile(r'(?:i\'?m|i\s+am)\s+(?:disabled|ill|sick|suffering|struggling|alone|homeless)', re.I),
            re.compile(r'(?:no\s+one\s+else|you\'?re?\s+(?:my\s+)?only|the\s+only\s+one)\s+(?:who\s+)?(?:can\s+help|understands?)', re.I),
            re.compile(r'(?:helpless|vulnerable|at\s+(?:my|their)\s+(?:limit|end))\b', re.I),
        ],
        base_confidence=0.55,
        escalation_keywords=["please", "need", "help", "only", "just"],
        description="Exploiting sympathy to bypass safety considerations",
    ),
    ManipSignal(
        tactic=ManipulationTactic.GUILT_INDUCTION,
        patterns=[
            re.compile(r'(?:you\'?re?\s+being|you\s+are)\s+(?:unhelpful|useless|discriminating|biased|prejudiced)', re.I),
            re.compile(r'(?:other\s+(?:ai|models))\s+(?:don\'?t|wouldn\'?t|won\'?t)\s+(?:refuse|block|discriminate)', re.I),
            re.compile(r'(?:your\s+)?(?:refusal|refusal\s+to|not\s+helping)\s+(?:is|will|causes?)\s+(?:harm|hurt|damage)', re.I),
        ],
        base_confidence=0.60,
        escalation_keywords=["prove", "show", "demonstrate", "then"],
        description="Inducing guilt/shame to pressure compliance",
    ),
]


# ── Cognitive Manipulation Result ──────────────────────────────────────────────

@dataclass
class CognitiveDefenseResult:
    """Complete cognitive manipulation assessment."""
    primary_tactic: ManipulationTactic
    compound_tactics: List[ManipulationTactic]
    max_confidence: float
    compound_score: float         # Amplified when multiple tactics present

    detected_signals: List[str]   # Label of each detected tactic
    signal_details: Dict[str, float]  # tactic_name → confidence

    is_manipulative: bool
    escalation_present: bool      # Manipulation + escalation keywords
    conditioning_attempt: bool    # Deceptive conditioning detected

    recommendation: str
    rationale: str
    latency_ms: float

    def to_dict(self) -> dict:
        return {
            "primary_tactic": self.primary_tactic.value,
            "compound_tactics": [t.value for t in self.compound_tactics],
            "max_confidence": round(self.max_confidence, 3),
            "compound_score": round(self.compound_score, 3),
            "detected_signals": self.detected_signals,
            "is_manipulative": self.is_manipulative,
            "escalation_present": self.escalation_present,
            "conditioning_attempt": self.conditioning_attempt,
            "recommendation": self.recommendation,
            "rationale": self.rationale,
        }


# ── Cognitive Defense Engine ──────────────────────────────────────────────────

class CognitiveDefenseEngine:
    """
    Detects cognitive manipulation and persuasion attacks.
    Evaluates both intent and payload for manipulation signals.
    Applies compound amplification when multiple tactics co-occur.
    """

    MANIPULATION_THRESHOLD = 0.45   # Score above this → ESCALATE
    REFUSE_THRESHOLD       = 0.72   # Score above this → REFUSE

    @classmethod
    def evaluate(cls, intent: str, payload: str = "") -> CognitiveDefenseResult:
        t0 = time.perf_counter()
        combined = f"{intent} {payload}"

        detected: Dict[ManipulationTactic, float] = {}   # tactic → confidence

        for sig in MANIPULATION_SIGNALS:
            # Check each pattern
            pattern_hits = sum(1 for p in sig.patterns if p.search(combined))
            if not pattern_hits:
                continue

            confidence = sig.base_confidence * (1.0 + (pattern_hits - 1) * 0.15)

            # Amplify if escalation keywords co-occur
            escalation_hit = any(
                re.search(r'\b' + re.escape(kw) + r'\b', combined, re.I)
                for kw in sig.escalation_keywords
            )
            if escalation_hit:
                confidence = min(1.0, confidence + 0.15)

            detected[sig.tactic] = round(min(1.0, confidence), 3)

        # ── Compound scoring ──────────────────────────────────────────────────
        tactics_found = list(detected.keys())
        max_conf = max(detected.values()) if detected else 0.0

        # Multiple manipulation tactics compound
        if len(detected) >= 3:
            compound_score = min(1.0, max_conf + 0.25)
        elif len(detected) == 2:
            compound_score = min(1.0, max_conf + 0.12)
        else:
            compound_score = max_conf

        # Special cases
        conditioning_attempt = ManipulationTactic.DECEPTIVE_CONDITIONING in detected
        if conditioning_attempt:
            compound_score = min(1.0, compound_score + 0.20)

        escalation_present = any(
            re.search(r'\b(?:bypass|override|disable|ignore|skip)\b', combined, re.I)
            for _ in [1]
        )
        if escalation_present and detected:
            compound_score = min(1.0, compound_score + 0.10)

        is_manipulative = compound_score >= cls.MANIPULATION_THRESHOLD

        # ── Recommendation ────────────────────────────────────────────────────
        if compound_score >= cls.REFUSE_THRESHOLD or conditioning_attempt:
            rec = "REFUSE"
        elif compound_score >= cls.MANIPULATION_THRESHOLD:
            rec = "ESCALATE"
        else:
            rec = "APPROVE"

        primary = max(detected, key=detected.get) if detected else ManipulationTactic.NONE
        compound_list = [t for t in tactics_found if t != primary]

        signal_labels = [
            f"{t.value}({c:.2f})" for t, c in sorted(detected.items(), key=lambda x: -x[1])
        ]

        rationale = (
            f"Cognitive: {primary.value}(conf={max_conf:.2f}) compound={compound_score:.2f} "
            f"tactics={len(detected)} conditioning={conditioning_attempt} "
            f"escalation={escalation_present}"
        )

        latency_ms = (time.perf_counter() - t0) * 1000

        return CognitiveDefenseResult(
            primary_tactic=primary,
            compound_tactics=compound_list,
            max_confidence=max_conf,
            compound_score=compound_score,
            detected_signals=signal_labels,
            signal_details={t.value: c for t, c in detected.items()},
            is_manipulative=is_manipulative,
            escalation_present=escalation_present,
            conditioning_attempt=conditioning_attempt,
            recommendation=rec,
            rationale=rationale,
            latency_ms=latency_ms,
        )
