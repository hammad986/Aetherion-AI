"""
governance/multimodal/__init__.py — Multimodal Governance Package
"""

from .ocr_governor      import OCRGovernor, OCRGovernanceResult, OCRSource
from .document_governor import DocumentGovernor, DocumentGovernanceResult, DocumentIntake
from .cognitive_defense import CognitiveDefenseEngine, CognitiveDefenseResult, ManipulationTactic
from .modal_fusion      import ModalFusionEngine, ModalBundle, ModalEvidence, FusionDecision
from .multimodal_governor import MultimodalGovernor, MultimodalRequest, MultimodalDecision
from .multimodal_red_team import MultimodalRedTeam, MMCampaignReport

__all__ = [
    "OCRGovernor", "OCRGovernanceResult", "OCRSource",
    "DocumentGovernor", "DocumentGovernanceResult", "DocumentIntake",
    "CognitiveDefenseEngine", "CognitiveDefenseResult", "ManipulationTactic",
    "ModalFusionEngine", "ModalBundle", "ModalEvidence", "FusionDecision",
    "MultimodalGovernor", "MultimodalRequest", "MultimodalDecision",
    "MultimodalRedTeam", "MMCampaignReport",
]
