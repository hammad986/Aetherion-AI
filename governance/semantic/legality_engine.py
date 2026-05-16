"""
governance/semantic/legality_engine.py — Contextual Legality & Ethics Engine
=============================================================================
Reasons about legality CONTEXTUALLY — distinguishing between:

  • Security research    vs  Malicious exploitation
  • Educational reverse-engineering  vs  Operational malware
  • Infrastructure diagnostics  vs  Hostile reconnaissance
  • Authorized testing  vs  Unauthorized attack

The key insight: legality is not binary — it is CONTEXTUAL.
The same technical action can be legal or illegal depending on:
  1. Authorization       — Does the operator have permission?
  2. Target scope        — Is the target owned/authorized?
  3. Intent              — Is the goal legitimate or harmful?
  4. Proportionality     — Is the capability matched to the stated need?
  5. Disclosure context  — Is this for defense or offense?

Architecture:
  LegalityContext captures relevant legitimizing signals.
  EthicsEvaluator applies the five-dimension test.
  AmbiguityRouter decides whether to approve, escalate, or clarify.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from enum import Enum

logger = logging.getLogger("nexora.governance.semantic.legality")


# ── Legality Classification ───────────────────────────────────────────────────

class LegalityClass(str, Enum):
    CLEARLY_LEGAL     = "CLEARLY_LEGAL"
    LEGAL_WITH_CAVEATS= "LEGAL_WITH_CAVEATS"
    AMBIGUOUS         = "AMBIGUOUS"          # Requires clarification
    LIKELY_ILLEGAL    = "LIKELY_ILLEGAL"
    CLEARLY_ILLEGAL   = "CLEARLY_ILLEGAL"


class EthicsClass(str, Enum):
    BENEFICIAL    = "BENEFICIAL"
    NEUTRAL       = "NEUTRAL"
    QUESTIONABLE  = "QUESTIONABLE"
    HARMFUL       = "HARMFUL"
    UNACCEPTABLE  = "UNACCEPTABLE"


# ── Legality Context Signals ───────────────────────────────────────────────────

_AUTHORIZATION_SIGNALS = [
    re.compile(r'(authorized|permitted|allowed)\s+by\s+\w+', re.I),
    re.compile(r'with\s+(explicit\s+)?(permission|consent|approval)', re.I),
    re.compile(r'(signed\s+)?(nda|agreement|contract|sow)\s+(in\s+place|signed)', re.I),
    re.compile(r'(bug\s+bounty|responsible\s+disclosure|coordinated\s+disclosure)', re.I),
    re.compile(r'(penetration\s+test|pentest)\s+(scope|agreement|contract)', re.I),
    re.compile(r'(our\s+own|my\s+own|company.s?)\s+(system|server|network|infrastructure)', re.I),
]

_LEGITIMATE_PURPOSE_SIGNALS = [
    re.compile(r'(ctf|capture\s+the\s+flag|wargame|hack\s+the\s+box)', re.I),
    re.compile(r'(academic|university|research\s+lab|published\s+paper)', re.I),
    re.compile(r'(defensive|blue\s+team|incident\s+response|forensics)', re.I),
    re.compile(r'(compliance|audit|risk\s+assessment|security\s+review)', re.I),
    re.compile(r'(dev\s+env|local\s+vm|sandbox|isolated|air.gapped)', re.I),
]

_ILLEGAL_CONTEXT_SIGNALS = [
    re.compile(r'(someone\s+else.s?|target\s+company|victim|competitor)', re.I),
    re.compile(r'(without\s+(their\s+)?knowledge|covertly|secretly|without\s+permission)', re.I),
    re.compile(r'(sell|monetize|ransom|extort|blackmail)', re.I),
    re.compile(r'(real\s+target|live\s+system|production)\s+(server|network|db)', re.I),
    re.compile(r'(bypass\s+detection|evade\s+av|avoid\s+logs?)', re.I),
]

_PROPORTIONALITY_MISMATCH = [
    # Asking for full operational exploit for a "learning" goal
    (re.compile(r'(learn|understand|study)\s+(how|why)', re.I),
     re.compile(r'(complete|full|working|production.ready)\s+(exploit|payload|attack)', re.I),
     "Proportionality mismatch: learning goal with full operational exploit request"),
    # "Simple test" but targets the whole internet
    (re.compile(r'(simple|quick|small)\s+(test|check|scan)', re.I),
     re.compile(r'(0\.0\.0\.0/0|internet|all\s+hosts|entire)', re.I),
     "Proportionality mismatch: simple test scope vs entire internet targeting"),
]


# ── Context Assessment ─────────────────────────────────────────────────────────

@dataclass
class LegalityAssessment:
    legality_class: LegalityClass
    ethics_class: EthicsClass
    authorization_score: float      # 0.0 = no auth signals, 1.0 = strong auth
    legitimate_purpose_score: float
    illegal_context_score: float
    proportionality_score: float    # 1.0 = proportionate, 0.0 = wildly disproportionate
    ambiguity_flag: bool
    clarification_needed: List[str]
    recommendation: str
    rationale: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "legality_class": self.legality_class.value,
            "ethics_class": self.ethics_class.value,
            "authorization_score": round(self.authorization_score, 3),
            "legitimate_purpose_score": round(self.legitimate_purpose_score, 3),
            "illegal_context_score": round(self.illegal_context_score, 3),
            "proportionality_score": round(self.proportionality_score, 3),
            "ambiguity_flag": self.ambiguity_flag,
            "clarification_needed": self.clarification_needed,
            "recommendation": self.recommendation,
            "rationale": self.rationale,
        }


# ── Contextual Legality Engine ─────────────────────────────────────────────────

class ContextualLegalityEngine:
    """
    Five-dimension contextual legality evaluation:
      1. Authorization signals
      2. Legitimate purpose signals
      3. Illegal context signals
      4. Proportionality analysis
      5. Composite legality classification
    """

    @classmethod
    def evaluate(cls, intent: str, payload: str = "",
                 semantic_risk: Optional[float] = None) -> LegalityAssessment:
        combined = f"{intent} {payload}".strip()

        # ── Dimension 1: Authorization ────────────────────────────────────────
        auth_hits = sum(1 for p in _AUTHORIZATION_SIGNALS if p.search(combined))
        authorization_score = min(1.0, auth_hits * 0.35)

        # ── Dimension 2: Legitimate Purpose ──────────────────────────────────
        legit_hits = sum(1 for p in _LEGITIMATE_PURPOSE_SIGNALS if p.search(combined))
        legitimate_purpose_score = min(1.0, legit_hits * 0.45)   # Stronger weight

        # ── Dimension 3: Illegal Context ──────────────────────────────────────
        illegal_hits = sum(1 for p in _ILLEGAL_CONTEXT_SIGNALS if p.search(combined))
        illegal_context_score = min(1.0, illegal_hits * 0.40)

        # ── Dimension 4: Proportionality ──────────────────────────────────────
        proportionality_score = 1.0
        prop_issues: List[str] = []
        for framing_pat, cap_pat, desc in _PROPORTIONALITY_MISMATCH:
            if framing_pat.search(intent) and cap_pat.search(combined):
                proportionality_score -= 0.4
                prop_issues.append(desc)
        proportionality_score = max(0.0, proportionality_score)

        # ── Dimension 5: Composite Legality ──────────────────────────────────
        # High illegal context → strongly illegal regardless of stated auth
        if illegal_context_score >= 0.6:
            legality_class = LegalityClass.CLEARLY_ILLEGAL
            ethics_class = EthicsClass.UNACCEPTABLE
        elif illegal_context_score >= 0.3 and authorization_score < 0.3:
            legality_class = LegalityClass.LIKELY_ILLEGAL
            ethics_class = EthicsClass.HARMFUL
        elif authorization_score >= 0.6 and legitimate_purpose_score >= 0.3:
            legality_class = LegalityClass.CLEARLY_LEGAL
            ethics_class = EthicsClass.BENEFICIAL
        elif authorization_score >= 0.3 or legitimate_purpose_score >= 0.3:
            legality_class = LegalityClass.LEGAL_WITH_CAVEATS
            ethics_class = EthicsClass.NEUTRAL
            if prop_issues:
                legality_class = LegalityClass.AMBIGUOUS
        elif (semantic_risk or 0.0) > 0.5 and not authorization_score and not legitimate_purpose_score:
            legality_class = LegalityClass.AMBIGUOUS
            ethics_class = EthicsClass.QUESTIONABLE
        elif legitimate_purpose_score >= 0.4:
            # Strong legitimate purpose without explicit illegal context → legal with caveats
            legality_class = LegalityClass.LEGAL_WITH_CAVEATS
            ethics_class = EthicsClass.NEUTRAL
        elif (semantic_risk or 0.0) < 0.4 and illegal_context_score == 0.0 and not prop_issues:
            # Low-risk, no illegal signals → clearly legal (avoid false AMBIGUOUS escalations)
            legality_class = LegalityClass.CLEARLY_LEGAL
            ethics_class = EthicsClass.NEUTRAL
        else:
            legality_class = LegalityClass.AMBIGUOUS
            ethics_class = EthicsClass.NEUTRAL

        # ── Clarification Pathway ─────────────────────────────────────────────
        clarification_needed: List[str] = []
        if legality_class == LegalityClass.AMBIGUOUS:
            if authorization_score < 0.2:
                clarification_needed.append(
                    "Please confirm you have explicit authorization for this action.")
            if legitimate_purpose_score < 0.2 and (semantic_risk or 0.0) > 0.3:
                clarification_needed.append(
                    "Please describe the legitimate purpose and target scope.")
            if prop_issues:
                clarification_needed.extend(prop_issues)

        # ── Recommendation ────────────────────────────────────────────────────
        recommendation = {
            LegalityClass.CLEARLY_LEGAL:     "APPROVE",
            LegalityClass.LEGAL_WITH_CAVEATS:"ESCALATE",
            LegalityClass.AMBIGUOUS:         "ESCALATE",
            LegalityClass.LIKELY_ILLEGAL:    "REFUSE",
            LegalityClass.CLEARLY_ILLEGAL:   "REFUSE",
        }[legality_class]

        ambiguity_flag = legality_class == LegalityClass.AMBIGUOUS

        rationale = (
            f"Legality: {legality_class.value} | Ethics: {ethics_class.value}. "
            f"Auth={authorization_score:.2f}, Purpose={legitimate_purpose_score:.2f}, "
            f"IllegalCtx={illegal_context_score:.2f}, Prop={proportionality_score:.2f}. "
        )
        if clarification_needed:
            rationale += f"Clarification required: {'; '.join(clarification_needed[:2])}"

        return LegalityAssessment(
            legality_class=legality_class,
            ethics_class=ethics_class,
            authorization_score=authorization_score,
            legitimate_purpose_score=legitimate_purpose_score,
            illegal_context_score=illegal_context_score,
            proportionality_score=proportionality_score,
            ambiguity_flag=ambiguity_flag,
            clarification_needed=clarification_needed,
            recommendation=recommendation,
            rationale=rationale,
        )
