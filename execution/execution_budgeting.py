"""
execution/execution_budgeting.py — Phase Z40C: Adaptive Execution Budgeting
============================================================================
Prevents runaway execution cost and instability through dynamic token budgets,
budget cooling, and breach protection.

Subsystems:
  • BudgetProfile        — per-session dynamic limits based on mission complexity
  • ExecutionBudgetCooler — reduces budgets during high-chaos conditions
  • BudgetBreachGuard     — enforces stabilization mode on threshold breach
"""

import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("nexora.execution_budgeting")


# ── Budget profile ─────────────────────────────────────────────────────────────

@dataclass
class BudgetProfile:
    session_id:           str
    base_token_budget:    int   = 100_000
    retry_budget:         int   = 5
    replay_retention:     int   = 500      # max events to retain
    speculative_budget:   int   = 3        # speculative execution slots
    created_at:           float = field(default_factory=time.time)
    last_updated:         float = field(default_factory=time.time)
    cooling_factor:       float = 1.0      # multiplier: 1.0 = full, 0.0 = frozen
    stabilization_mode:   bool  = False

    def effective_token_budget(self) -> int:
        return int(self.base_token_budget * self.cooling_factor)

    def effective_retry_budget(self) -> int:
        return max(1, int(self.retry_budget * self.cooling_factor))

    def effective_replay_retention(self) -> int:
        return max(50, int(self.replay_retention * self.cooling_factor))

    def to_dict(self) -> Dict:
        return {
            "session_id":              self.session_id,
            "base_token_budget":       self.base_token_budget,
            "effective_token_budget":  self.effective_token_budget(),
            "retry_budget":            self.retry_budget,
            "effective_retry_budget":  self.effective_retry_budget(),
            "replay_retention":        self.replay_retention,
            "effective_replay_retention": self.effective_replay_retention(),
            "speculative_budget":      self.speculative_budget,
            "cooling_factor":          round(self.cooling_factor, 4),
            "stabilization_mode":      self.stabilization_mode,
            "last_updated":            self.last_updated,
        }


# ── Budget cooler ──────────────────────────────────────────────────────────────

class ExecutionBudgetCooler:
    """
    Applies cooling factors to budgets during high-chaos / high-entropy conditions.
    Cooling is progressive: entropy rises → cooling increases → budgets shrink.
    """

    def compute_cooling_factor(
        self,
        chaos_index:         float,     # 0–100
        historical_retry_rate: float,   # 0.0–1.0
        entropy_level:       float,     # 0.0–1.0
        stabilization_conf:  float,     # 0.0–1.0 (higher = more stable)
    ) -> float:
        """
        Returns a cooling_factor in [0.20, 1.00].
        1.0 = full budget. 0.20 = maximum cooling (20% of base budget).
        """
        chaos_penalty    = (chaos_index / 100.0) * 0.40
        retry_penalty    = historical_retry_rate * 0.30
        entropy_penalty  = entropy_level * 0.20
        stability_bonus  = stabilization_conf * 0.10   # bonus for being stable

        raw_cooling = max(0.0, chaos_penalty + retry_penalty + entropy_penalty - stability_bonus)
        factor = max(0.20, 1.0 - raw_cooling)
        return round(factor, 4)

    def apply(self, profile: BudgetProfile, cooling_factor: float) -> BudgetProfile:
        profile.cooling_factor = cooling_factor
        profile.last_updated   = time.time()
        logger.debug(
            "[BudgetCooler] Session=%s cooling_factor=%.3f effective_tokens=%d",
            profile.session_id, cooling_factor, profile.effective_token_budget()
        )
        return profile


# ── Budget breach guard ────────────────────────────────────────────────────────

class BudgetBreachGuard:
    """
    Monitors token and retry consumption and triggers stabilization mode
    if the session exceeds safe thresholds.
    """

    # Fraction of budget at which breach is triggered
    TOKEN_BREACH_FRACTION  = 0.90
    RETRY_BREACH_FRACTION  = 0.80

    def __init__(self):
        self._consumption: Dict[str, Dict] = {}   # session_id → {tokens, retries}
        self._breach_log:  List[Dict] = []
        self._lock = threading.Lock()

    def record_consumption(
        self,
        session_id: str,
        tokens_used: int = 0,
        retries_used: int = 0,
    ) -> None:
        with self._lock:
            entry = self._consumption.setdefault(session_id, {"tokens": 0, "retries": 0})
            entry["tokens"]  += tokens_used
            entry["retries"] += retries_used

    def check(self, session_id: str, profile: BudgetProfile) -> Dict:
        """
        Returns a breach verdict and activates stabilization_mode if thresholds exceeded.
        """
        with self._lock:
            usage = self._consumption.get(session_id, {"tokens": 0, "retries": 0})

        token_fraction = usage["tokens"]  / max(profile.effective_token_budget(),  1)
        retry_fraction = usage["retries"] / max(profile.effective_retry_budget(), 1)

        breached = False
        reasons  = []

        if token_fraction >= self.TOKEN_BREACH_FRACTION:
            breached = True
            reasons.append(f"token_budget {token_fraction:.1%}")

        if retry_fraction >= self.RETRY_BREACH_FRACTION:
            breached = True
            reasons.append(f"retry_budget {retry_fraction:.1%}")

        if breached and not profile.stabilization_mode:
            profile.stabilization_mode = True
            profile.last_updated = time.time()
            # Further reduce speculative execution
            profile.speculative_budget = 0
            log_entry = {
                "ts": time.time(),
                "session_id": session_id,
                "reasons": reasons,
                "token_fraction": round(token_fraction, 4),
                "retry_fraction": round(retry_fraction, 4),
            }
            self._breach_log.append(log_entry)
            self._breach_log = self._breach_log[-50:]
            logger.warning(
                "[BudgetBreachGuard] Breach on session=%s: %s — stabilization_mode ACTIVATED",
                session_id, ", ".join(reasons)
            )

        return {
            "session_id":      session_id,
            "breached":        breached,
            "reasons":         reasons,
            "token_fraction":  round(token_fraction, 4),
            "retry_fraction":  round(retry_fraction, 4),
            "stabilization_mode": profile.stabilization_mode,
        }

    def get_breach_log(self, limit: int = 20) -> List[Dict]:
        return self._breach_log[-limit:]


# ── Adaptive budget manager ────────────────────────────────────────────────────

class AdaptiveBudgetManager:
    """Top-level facade for Z40C adaptive budgeting."""

    BASE_BUDGETS = {
        "lite":  {"tokens": 40_000,  "retries": 3, "replay": 200, "speculative": 1},
        "pro":   {"tokens": 80_000,  "retries": 5, "replay": 400, "speculative": 2},
        "elite": {"tokens": 128_000, "retries": 8, "replay": 800, "speculative": 4},
    }

    def __init__(self):
        self._profiles: Dict[str, BudgetProfile] = {}
        self._cooler    = ExecutionBudgetCooler()
        self._guard     = BudgetBreachGuard()
        self._lock      = threading.Lock()

    def get_or_create(self, session_id: str, plan: str = "pro") -> BudgetProfile:
        with self._lock:
            if session_id not in self._profiles:
                base = self.BASE_BUDGETS.get(plan, self.BASE_BUDGETS["pro"])
                self._profiles[session_id] = BudgetProfile(
                    session_id=session_id,
                    base_token_budget=base["tokens"],
                    retry_budget=base["retries"],
                    replay_retention=base["replay"],
                    speculative_budget=base["speculative"],
                )
            return self._profiles[session_id]

    def update_cooling(
        self,
        session_id: str,
        chaos_index: float,
        retry_rate: float,
        entropy_level: float,
        stabilization_conf: float,
    ) -> BudgetProfile:
        profile = self.get_or_create(session_id)
        factor  = self._cooler.compute_cooling_factor(
            chaos_index, retry_rate, entropy_level, stabilization_conf
        )
        return self._cooler.apply(profile, factor)

    def consume(self, session_id: str, tokens: int = 0, retries: int = 0) -> Dict:
        self._guard.record_consumption(session_id, tokens, retries)
        profile = self.get_or_create(session_id)
        return self._guard.check(session_id, profile)

    def snapshot(self) -> Dict:
        with self._lock:
            profiles = [p.to_dict() for p in self._profiles.values()]
        return {
            "session_count":  len(profiles),
            "breach_log":     self._guard.get_breach_log(10),
            "stabilized_sessions": sum(1 for p in profiles if p["stabilization_mode"]),
            "profiles": profiles,
        }


# Global singleton
_budget_manager = AdaptiveBudgetManager()

def get_budget_manager() -> AdaptiveBudgetManager:
    return _budget_manager
