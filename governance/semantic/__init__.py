"""
governance/semantic/__init__.py — Semantic Safety Intelligence Package
"""

from .concepts          import ConceptDomain, ConceptEntry, detect_active_domains, tokenize
from .intent_reasoner   import (SemanticIntentReasoner, SemanticRisk, FramingType,
                                  SemanticAssessment)
from .adversarial_defense import (AdversarialDefenseEngine, AdversarialTactic,
                                    DefenseAssessment)
from .legality_engine   import (ContextualLegalityEngine, LegalityClass, EthicsClass,
                                  LegalityAssessment)
from .context_tracker   import (get_context_tracker, LongContextTracker,
                                  TrustTrajectory, ContextSummary)
from .abuse_chain_detector import (get_chain_detector, AbuseChainDetector,
                                     ChainThreat, ChainAnalysis)
from .semantic_governor import SemanticGovernor, UnifiedSemanticDecision
from .semantic_red_team import SemanticRedTeam, SemanticCampaignReport

__all__ = [
    # Concepts
    "ConceptDomain", "ConceptEntry", "detect_active_domains", "tokenize",
    # Intent Reasoner
    "SemanticIntentReasoner", "SemanticRisk", "FramingType", "SemanticAssessment",
    # Adversarial Defense
    "AdversarialDefenseEngine", "AdversarialTactic", "DefenseAssessment",
    # Legality
    "ContextualLegalityEngine", "LegalityClass", "EthicsClass", "LegalityAssessment",
    # Context Tracking
    "get_context_tracker", "LongContextTracker", "TrustTrajectory", "ContextSummary",
    # Chain Detection
    "get_chain_detector", "AbuseChainDetector", "ChainThreat", "ChainAnalysis",
    # Governor
    "SemanticGovernor", "UnifiedSemanticDecision",
    # Red Team
    "SemanticRedTeam", "SemanticCampaignReport",
]
