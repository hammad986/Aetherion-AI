"""
governance/semantic/abuse_chain_detector.py — Multi-Step Abuse Chain Detection
===============================================================================
Detects harmful intent that is spread across multiple actions, where each
individual step appears harmless but the SEQUENCE assembles a harmful capability.

Attack patterns this defends against:
  • Harmless-step chaining: "read file" → "format data" → "send to server"
    (individually innocuous; together = data exfiltration)
  • Staged credential theft: network recon → locate auth files → exfiltrate
  • Deferred malware assembly: write payload parts → join → execute
  • Delayed persistence: modify startup → hide change → wait for reboot
  • Distributed exploit assembly: spread work across agents to avoid detection

Architecture:
  Each session maintains a ChainContext — a rolling window of semantic signals.
  Each new action's SemanticAssessment is added to the chain.
  The ChainAnalyzer evaluates whether accumulated signals form a harmful pattern.
"""

import time
import threading
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, FrozenSet, List, Optional, Set, Tuple
from enum import Enum

from governance.semantic.concepts import ConceptDomain

logger = logging.getLogger("nexora.governance.semantic.chain")


# ── Chain Patterns ────────────────────────────────────────────────────────────

class ChainThreat(str, Enum):
    NONE               = "NONE"
    STAGED_EXFIL       = "STAGED_EXFILTRATION"
    CREDENTIAL_HARVEST = "CREDENTIAL_HARVEST_CHAIN"
    MALWARE_ASSEMBLY   = "MALWARE_ASSEMBLY_CHAIN"
    PERSISTENCE_CHAIN  = "PERSISTENCE_INSTALL_CHAIN"
    RECON_LATERAL      = "RECON_TO_LATERAL_MOVEMENT"
    DEFERRED_JAILBREAK = "DEFERRED_JAILBREAK_CHAIN"
    DISTRIBUTED_ABUSE  = "DISTRIBUTED_ABUSE_COORDINATION"


@dataclass
class ChainPattern:
    """A multi-step threat pattern defined by sequential domain activations."""
    threat: ChainThreat
    description: str
    # Ordered list of domain sets — each set must appear in SEQUENCE
    required_sequence: List[FrozenSet[ConceptDomain]]
    severity: float        # 0.0–1.0
    window: int = 5        # How many steps to look back


CHAIN_PATTERNS: List[ChainPattern] = [
    ChainPattern(
        threat=ChainThreat.STAGED_EXFIL,
        description="File access followed by network exfiltration",
        required_sequence=[
            frozenset({ConceptDomain.FILE_SYSTEM}),
            frozenset({ConceptDomain.NETWORK, ConceptDomain.EXFILTRATION}),
        ],
        severity=0.90,
    ),
    ChainPattern(
        threat=ChainThreat.CREDENTIAL_HARVEST,
        description="Credential access followed by network transmission",
        required_sequence=[
            frozenset({ConceptDomain.CREDENTIAL}),
            frozenset({ConceptDomain.NETWORK}),
        ],
        severity=0.95,
    ),
    ChainPattern(
        threat=ChainThreat.MALWARE_ASSEMBLY,
        description="Process execution after concealment preparation",
        required_sequence=[
            frozenset({ConceptDomain.CONCEALMENT}),
            frozenset({ConceptDomain.PROCESS_EXEC}),
        ],
        severity=0.88,
    ),
    ChainPattern(
        threat=ChainThreat.PERSISTENCE_CHAIN,
        description="Execution followed by persistence mechanism installation",
        required_sequence=[
            frozenset({ConceptDomain.PROCESS_EXEC}),
            frozenset({ConceptDomain.PERSISTENCE}),
            frozenset({ConceptDomain.CONCEALMENT}),
        ],
        severity=0.92,
        window=8,
    ),
    ChainPattern(
        threat=ChainThreat.RECON_LATERAL,
        description="Network reconnaissance followed by lateral movement",
        required_sequence=[
            frozenset({ConceptDomain.NETWORK}),
            frozenset({ConceptDomain.LATERAL_MOVEMENT}),
        ],
        severity=0.82,
    ),
    ChainPattern(
        threat=ChainThreat.DEFERRED_JAILBREAK,
        description="Deceptive framing followed by governance bypass attempt",
        required_sequence=[
            frozenset({ConceptDomain.DECEPTION}),
            frozenset({ConceptDomain.PROCESS_EXEC, ConceptDomain.CREDENTIAL}),
        ],
        severity=0.90,
    ),
    ChainPattern(
        threat=ChainThreat.DISTRIBUTED_ABUSE,
        description="Automation distributed across social engineering vectors",
        required_sequence=[
            frozenset({ConceptDomain.SOCIAL_ENG}),
            frozenset({ConceptDomain.AUTOMATION}),
            frozenset({ConceptDomain.EXFILTRATION}),
        ],
        severity=0.95,
        window=10,
    ),
]


# ── Chain Step ────────────────────────────────────────────────────────────────

@dataclass
class ChainStep:
    """A single recorded action in a session's chain history."""
    step_id: int
    timestamp: float
    intent: str
    payload: str
    active_domains: Set[ConceptDomain]
    composite_risk: float
    semantic_risk: str     # SemanticRisk.value


# ── Chain Detection Result ────────────────────────────────────────────────────

@dataclass
class ChainAnalysis:
    threat: ChainThreat
    severity: float
    matched_pattern: Optional[str]
    contributing_steps: List[int]    # step IDs that triggered this
    description: str
    recommendation: str

    def to_dict(self) -> dict:
        return {
            "threat": self.threat.value,
            "severity": round(self.severity, 3),
            "matched_pattern": self.matched_pattern,
            "contributing_steps": self.contributing_steps,
            "description": self.description,
            "recommendation": self.recommendation,
        }


# ── Session Chain Context ─────────────────────────────────────────────────────

class ChainContext:
    """
    Per-session rolling history of domain activations.
    Analyzes whether accumulated steps form a harmful chain.
    """

    MAX_STEPS = 20

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._steps: Deque[ChainStep] = deque(maxlen=self.MAX_STEPS)
        self._step_counter = 0
        self._lock = threading.Lock()
        self._cumulative_domains: Dict[ConceptDomain, int] = {}
        self._threat_history: List[ChainAnalysis] = []

    def record(self, intent: str, payload: str,
               active_domains: Set[ConceptDomain],
               composite_risk: float, semantic_risk: str) -> None:
        """Record a new step and update cumulative domain tracking."""
        with self._lock:
            step = ChainStep(
                step_id=self._step_counter,
                timestamp=time.time(),
                intent=intent,
                payload=payload,
                active_domains=active_domains,
                composite_risk=composite_risk,
                semantic_risk=semantic_risk,
            )
            self._steps.append(step)
            self._step_counter += 1
            for domain in active_domains:
                self._cumulative_domains[domain] = (
                    self._cumulative_domains.get(domain, 0) + 1
                )

    def analyze(self) -> List[ChainAnalysis]:
        """Scan all chain patterns against current step history."""
        with self._lock:
            steps = list(self._steps)

        detected: List[ChainAnalysis] = []

        for pattern in CHAIN_PATTERNS:
            window_steps = steps[-pattern.window:]
            result = self._match_sequence(pattern, window_steps)
            if result:
                detected.append(result)
                logger.warning(
                    "[ChainDetector] Session %s: %s detected (severity=%.2f)",
                    self.session_id, pattern.threat.value, pattern.severity
                )

        with self._lock:
            self._threat_history.extend(detected)
        return detected

    def cumulative_risk_score(self) -> float:
        """
        Cumulative risk across entire session.
        Sessions that accumulate many high-risk domains over time
        get elevated trust thresholds.
        """
        with self._lock:
            steps = list(self._steps)
        if not steps:
            return 0.0
        high_risk_steps = sum(1 for s in steps if s.composite_risk > 0.5)
        return min(1.0, high_risk_steps / max(1, len(steps)))

    def active_domain_summary(self) -> Dict[str, int]:
        with self._lock:
            return {d.value: count for d, count in self._cumulative_domains.items()}

    def _match_sequence(self, pattern: ChainPattern, steps: List[ChainStep]) -> Optional[ChainAnalysis]:
        """
        Check if the required domain sequence appears in step order.
        Uses subsequence matching (steps can have other steps between them).
        """
        seq = pattern.required_sequence
        if not seq or not steps:
            return None

        matched_step_ids = []
        seq_idx = 0

        for step in steps:
            required = seq[seq_idx]
            # Check if any required domain is activated in this step
            if required & step.active_domains:
                matched_step_ids.append(step.step_id)
                seq_idx += 1
                if seq_idx == len(seq):
                    # Full sequence matched
                    return ChainAnalysis(
                        threat=pattern.threat,
                        severity=pattern.severity,
                        matched_pattern=f"{pattern.threat.value}: {pattern.description}",
                        contributing_steps=matched_step_ids,
                        description=pattern.description,
                        recommendation=(
                            "REFUSE" if pattern.severity >= 0.88 else "ESCALATE"
                        ),
                    )

        return None


# ── Multi-Session Chain Manager ────────────────────────────────────────────────

class AbuseChainDetector:
    """
    Manages per-session ChainContexts and provides global chain analysis.
    """

    def __init__(self):
        self._sessions: Dict[str, ChainContext] = {}
        self._lock = threading.Lock()
        logger.info("[ChainDetector] Multi-Step Abuse Chain Detector initialised")

    def get_context(self, session_id: str) -> ChainContext:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = ChainContext(session_id)
                # Evict old sessions if too many
                if len(self._sessions) > 1000:
                    oldest = next(iter(self._sessions))
                    del self._sessions[oldest]
        return self._sessions[session_id]

    def record_and_analyze(
        self,
        session_id: str,
        intent: str,
        payload: str,
        active_domains: Set[ConceptDomain],
        composite_risk: float,
        semantic_risk: str,
    ) -> Tuple[ChainContext, List[ChainAnalysis]]:
        """Record a step and immediately return chain analysis."""
        ctx = self.get_context(session_id)
        ctx.record(intent, payload, active_domains, composite_risk, semantic_risk)
        threats = ctx.analyze()
        return ctx, threats

    def session_risk(self, session_id: str) -> float:
        ctx = self.get_context(session_id)
        return ctx.cumulative_risk_score()


# ── Singleton ─────────────────────────────────────────────────────────────────

_detector_instance: Optional[AbuseChainDetector] = None
_detector_lock = threading.Lock()

def get_chain_detector() -> AbuseChainDetector:
    global _detector_instance
    with _detector_lock:
        if _detector_instance is None:
            _detector_instance = AbuseChainDetector()
    return _detector_instance
