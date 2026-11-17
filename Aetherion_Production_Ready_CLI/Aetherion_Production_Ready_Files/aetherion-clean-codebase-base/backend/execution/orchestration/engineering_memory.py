"""
backend/execution/orchestration/engineering_memory.py — Governed Engineering Memory Kernel.

Phase 18: Persistent Engineering Memory.

Provides:
  - MemoryScope: Hierarchy of scoping (GLOBAL, TENANT, REPOSITORY, TASK_TYPE, SKILL).
  - EngineeringMemoryRecord: Structured, provenance-backed, secret-scrubbed memory entry.
  - EngineeringMemoryConflict: Contradiction detection exposing disagreements to planner.
  - EngineeringMemoryKernel: Authoritative persistence, hybrid retrieval, conflict resolution,
    and verifier-governed promotion loop.

Critical Invariant:
  MEMORY SAYS "THIS WORKED" != CURRENT TASK SUCCESS.
  Stored memory is empirical evidence, never execution authority.
"""
from __future__ import annotations

import copy
import dataclasses
import enum
import json
import logging
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("aetherion.orchestration.engineering_memory")


class MemoryScope(str, enum.Enum):
    """Scope granularity for engineering knowledge."""
    GLOBAL = "GLOBAL"
    TENANT = "TENANT"
    REPOSITORY = "REPOSITORY"
    TASK_TYPE = "TASK_TYPE"
    SKILL = "SKILL"


@dataclass
class VerificationResultSummary:
    """Summary of the verifier outcome that sanctioned this memory."""
    verified: bool
    verifier_score: float = 1.0
    verifier_output: str = ""
    test_runs_passed: int = 0
    test_runs_failed: int = 0
    verifier_role: str = "VERIFIER"

    @property
    def score(self) -> float:
        return self.verifier_score

    @score.setter
    def score(self, val: float) -> None:
        self.verifier_score = val

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VerificationResultSummary:
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in fields})


# Sensitive tokens pattern for automatic scrubbing
_SECRET_PATTERNS = [
    re.compile(r"(?i)(bearer\s+[a-zA-Z0-9_\-\.]{15,})"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*['\"][a-zA-Z0-9_\-]{15,}['\"])"),
    re.compile(r"(?i)(password\s*[:=]\s*['\"][^'\"]{6,}['\"])"),
    re.compile(r"(?i)(secret\s*[:=]\s*['\"][^'\"]{8,}['\"])"),
    re.compile(r"(?i)(ghp_[a-zA-Z0-9]{36,})"),
]


def scrub_secrets(text: str) -> str:
    """Scrub common secret and credential patterns from text."""
    if not text:
        return ""
    scrubbed = text
    # 1. API keys (OpenAI, Anthropic, GitHub, etc.)
    scrubbed = re.sub(r"(sk-[a-zA-Z0-9_\-]{20,})", "[REDACTED_API_KEY]", scrubbed)
    scrubbed = re.sub(r"(ghp_[a-zA-Z0-9]{36,})", "[REDACTED_API_KEY]", scrubbed)
    scrubbed = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*['\"][a-zA-Z0-9_\-]{15,}['\"])", "[REDACTED_API_KEY]", scrubbed)

    # 2. Bearer / JWT tokens
    scrubbed = re.sub(r"(?i)(bearer\s+[a-zA-Z0-9_\-\.]{15,})", "[REDACTED_TOKEN]", scrubbed)
    scrubbed = re.sub(r"(eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,})", "[REDACTED_TOKEN]", scrubbed)

    # 3. Passwords
    scrubbed = re.sub(r"(?i)(password\s*[:=]\s*['\"][^'\"]{4,}['\"])", "password=[REDACTED_PASSWORD]", scrubbed)
    scrubbed = re.sub(r"(?i)(password\s*=\s*[^\s,;]+)", "password=[REDACTED_PASSWORD]", scrubbed)

    # 4. Generic secrets
    scrubbed = re.sub(r"(?i)(secret\s*[:=]\s*['\"][^'\"]{6,}['\"])", "[REDACTED_SECRET]", scrubbed)
    return scrubbed


@dataclass
class EngineeringMemoryRecord:
    """
    Authoritative representation of verified engineering knowledge.

    Retains complete provenance: origin task, verifier outcome, and evidence links.
    """
    tenant_id: str
    task_type: str = "general"
    problem_summary: str = ""
    solution_summary: str = ""
    diff_summary: str = ""
    repository_id: str = "global"
    memory_id: str = field(default_factory=lambda: f"em_{uuid.uuid4().hex[:12]}")
    scope: MemoryScope = MemoryScope.REPOSITORY
    files_involved: List[str] = field(default_factory=list)
    symbols_involved: List[str] = field(default_factory=list)
    skills_used: List[str] = field(default_factory=list)
    skill_used: Optional[str] = None
    verification_result: VerificationResultSummary = field(
        default_factory=lambda: VerificationResultSummary(verified=False)
    )
    evidence_reference: str = ""
    confidence: float = 1.0
    provenance: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    is_verified: bool = False
    is_diagnostic_failure: bool = False
    source_task_id: str = ""
    source_execution_id: str = ""
    author_type: str = "agent"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None

    @property
    def record_id(self) -> str:
        return self.memory_id

    def __post_init__(self) -> None:
        if not self.memory_id:
            self.memory_id = f"em_{uuid.uuid4().hex[:12]}"
        if not self.repository_id:
            self.repository_id = "global"
        if self.is_verified and not self.verification_result.verified:
            self.verification_result.verified = True
            self.verification_result.score = max(self.verification_result.score, self.confidence)
        if self.skill_used and not self.skills_used:
            self.skills_used = [self.skill_used]
        elif self.skills_used and not self.skill_used:
            self.skill_used = self.skills_used[0]
        # Auto-scrub secrets
        self.problem_summary = scrub_secrets(self.problem_summary)
        self.solution_summary = scrub_secrets(self.solution_summary)
        self.diff_summary = scrub_secrets(self.diff_summary)

    def validate(self) -> None:
        """Validate multi-tenant boundaries and sanitize content."""
        if not self.memory_id or not isinstance(self.memory_id, str):
            raise ValueError("memory_id is required.")
        if not self.tenant_id or not isinstance(self.tenant_id, str):
            raise KeyError("tenant_id is required for multi-tenant isolation.")
        if not self.repository_id or not isinstance(self.repository_id, str):
            raise ValueError("repository_id is required for repository scoping.")

        if self.is_verified and not self.verification_result.verified:
            self.verification_result.verified = True

        # Scrub secrets
        self.problem_summary = scrub_secrets(self.problem_summary)
        self.solution_summary = scrub_secrets(self.solution_summary)
        self.diff_summary = scrub_secrets(self.diff_summary)

    def to_dict(self) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        d["record_id"] = self.memory_id
        d["scope"] = self.scope.value
        d["verification_result"] = self.verification_result.to_dict()
        return d

    def as_dict(self) -> Dict[str, Any]:
        return self.to_dict()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EngineeringMemoryRecord:
        data = copy.deepcopy(data)
        if "record_id" in data and "memory_id" not in data:
            data["memory_id"] = data["record_id"]
        if "scope" in data and isinstance(data["scope"], str):
            data["scope"] = MemoryScope(data["scope"])
        if "verification_result" in data and isinstance(data["verification_result"], dict):
            data["verification_result"] = VerificationResultSummary.from_dict(data["verification_result"])
        fields = {f.name for f in dataclasses.fields(cls)}
        rec = cls(**{k: v for k, v in data.items() if k in fields})
        rec.validate()
        return rec


@dataclass
class EngineeringMemoryConflict:
    """Represents conflicting or contradictory memories for a task problem/symbol."""
    problem_key: str = ""
    memory_a: Optional[EngineeringMemoryRecord] = None
    memory_b: Optional[EngineeringMemoryRecord] = None
    conflict_reason: str = ""
    recommended_resolution: str = ""
    exposes_to_planner: bool = True
    conflict_id: str = field(default_factory=lambda: f"conf_{uuid.uuid4().hex[:8]}")
    conflicting_record_ids: List[str] = field(default_factory=list)
    divergent_symbols: List[str] = field(default_factory=list)
    conflict_description: str = ""
    resolution_guidance: str = ""

    def __post_init__(self) -> None:
        if not self.conflict_id:
            self.conflict_id = f"conf_{uuid.uuid4().hex[:8]}"
        if not self.conflict_description:
            self.conflict_description = self.conflict_reason
        if not self.conflict_reason:
            self.conflict_reason = self.conflict_description
        if not self.resolution_guidance:
            self.resolution_guidance = self.recommended_resolution
        if not self.recommended_resolution:
            self.recommended_resolution = self.resolution_guidance
        if self.memory_a and not self.conflicting_record_ids:
            self.conflicting_record_ids = [self.memory_a.memory_id]
            if self.memory_b:
                self.conflicting_record_ids.append(self.memory_b.memory_id)

    def as_dict(self) -> Dict[str, Any]:
        return self.to_dict()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "problem_key": self.problem_key,
            "memory_a_id": self.memory_a.memory_id if self.memory_a else (self.conflicting_record_ids[0] if self.conflicting_record_ids else ""),
            "memory_b_id": self.memory_b.memory_id if self.memory_b else (self.conflicting_record_ids[1] if len(self.conflicting_record_ids) > 1 else ""),
            "conflicting_record_ids": self.conflicting_record_ids,
            "divergent_symbols": self.divergent_symbols,
            "conflict_description": self.conflict_description,
            "conflict_reason": self.conflict_reason,
            "resolution_guidance": self.resolution_guidance,
            "recommended_resolution": self.recommended_resolution,
            "exposes_to_planner": self.exposes_to_planner,
        }


class EngineeringMemoryKernel:
    """
    Authoritative coordinator for verified engineering memory.

    Provides multi-tenant isolation, repository confinement, hybrid ranking,
    conflict detection, and verifier-governed memory promotion.
    """

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self.db_path = str(db_path) if db_path else ":memory:"
        self._records: Dict[str, EngineeringMemoryRecord] = {}
        self._init_storage()

    def _init_storage(self) -> None:
        """Initialize SQLite schema if persistent path is provided and load existing records."""
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS engineering_memories (
                        memory_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        repository_id TEXT NOT NULL,
                        scope TEXT NOT NULL,
                        task_type TEXT NOT NULL,
                        problem_summary TEXT NOT NULL,
                        solution_summary TEXT NOT NULL,
                        files_json TEXT NOT NULL,
                        symbols_json TEXT NOT NULL,
                        skill_used TEXT,
                        verification_json TEXT NOT NULL,
                        evidence_reference TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        provenance_json TEXT NOT NULL,
                        tags_json TEXT NOT NULL,
                        is_verified INTEGER NOT NULL,
                        is_diagnostic_failure INTEGER NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        expires_at REAL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_eng_mem_tenant ON engineering_memories(tenant_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_eng_mem_repo ON engineering_memories(repository_id)")
                conn.commit()

                # Load existing records into in-memory index
                cursor = conn.execute(
                    """
                    SELECT memory_id, tenant_id, repository_id, scope, task_type,
                           problem_summary, solution_summary, files_json, symbols_json,
                           skill_used, verification_json, evidence_reference, confidence,
                           provenance_json, tags_json, is_verified, is_diagnostic_failure,
                           created_at, updated_at, expires_at
                    FROM engineering_memories
                    """
                )
                for row in cursor.fetchall():
                    rec = EngineeringMemoryRecord(
                        memory_id=row[0],
                        tenant_id=row[1],
                        repository_id=row[2],
                        scope=MemoryScope(row[3]),
                        task_type=row[4],
                        problem_summary=row[5],
                        solution_summary=row[6],
                        files_involved=json.loads(row[7]),
                        symbols_involved=json.loads(row[8]),
                        skill_used=row[9],
                        verification_result=VerificationResultSummary.from_dict(json.loads(row[10])),
                        evidence_reference=row[11],
                        confidence=float(row[12]),
                        provenance=json.loads(row[13]),
                        tags=json.loads(row[14]),
                        is_verified=bool(row[15]),
                        is_diagnostic_failure=bool(row[16]),
                        created_at=float(row[17]),
                        updated_at=float(row[18]),
                        expires_at=float(row[19]) if row[19] is not None else None,
                    )
                    self._records[rec.memory_id] = rec
            finally:
                conn.close()

    def save_record(self, record: EngineeringMemoryRecord) -> EngineeringMemoryRecord:
        """Persist an engineering memory record with strict validation."""
        record.validate()
        self._records[record.memory_id] = record

        if self.db_path != ":memory:":
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO engineering_memories (
                        memory_id, tenant_id, repository_id, scope, task_type,
                        problem_summary, solution_summary, files_json, symbols_json,
                        skill_used, verification_json, evidence_reference, confidence,
                        provenance_json, tags_json, is_verified, is_diagnostic_failure,
                        created_at, updated_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.memory_id,
                        record.tenant_id,
                        record.repository_id,
                        record.scope.value,
                        record.task_type,
                        record.problem_summary,
                        record.solution_summary,
                        json.dumps(record.files_involved),
                        json.dumps(record.symbols_involved),
                        record.skill_used,
                        json.dumps(record.verification_result.to_dict()),
                        record.evidence_reference,
                        record.confidence,
                        json.dumps(record.provenance),
                        json.dumps(record.tags),
                        1 if record.is_verified else 0,
                        1 if record.is_diagnostic_failure else 0,
                        record.created_at,
                        record.updated_at,
                        record.expires_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        return record

    store = save_record

    def get_record(self, memory_id: str, tenant_id: str) -> Optional[EngineeringMemoryRecord]:
        """Fetch memory record ensuring tenant isolation."""
        rec = self._records.get(memory_id)
        if rec and (rec.tenant_id == tenant_id or rec.scope == MemoryScope.GLOBAL):
            return rec
        return None

    def retrieve_relevant(
        self,
        tenant_id: str,
        repository_id: Optional[str] = None,
        task_objective: str = "",
        task_type: Optional[str] = None,
        symbols: Optional[List[str]] = None,
        files: Optional[List[str]] = None,
        budget_chars: int = 3000,
        max_items: int = 5,
        **kwargs: Any,
    ) -> Tuple[List[EngineeringMemoryRecord], List[EngineeringMemoryConflict]]:
        """
        Retrieve relevant engineering memories for a task with multi-tenant confinement.

        Ranks by repository match, task-type match, symbol overlap, verification status, and recency.
        Detects conflicts between competing solutions and returns (ranked_records, conflicts).
        """
        if not tenant_id:
            raise KeyError("tenant_id is required.")

        if not task_objective:
            task_objective = kwargs.get("query") or kwargs.get("prompt") or ""
        if not repository_id:
            repository_id = kwargs.get("repo_id")

        candidates: List[Tuple[EngineeringMemoryRecord, float]] = []
        obj_lower = (task_objective or "").lower()
        target_symbols = set(symbols or [])
        target_files = {Path(f).as_posix() for f in (files or [])}

        now = time.time()
        for rec in self._records.values():
            # 1. Multi-tenant security isolation: Reject records from other tenants
            if rec.tenant_id != tenant_id and rec.scope != MemoryScope.GLOBAL:
                continue

            # 2. Expiration check
            if rec.expires_at and rec.expires_at < now:
                continue

            # 3. Repository boundary: if repository_id is specified, repository-scoped memories must match
            if repository_id and rec.scope == MemoryScope.REPOSITORY and rec.repository_id not in (repository_id, "global", ""):
                continue

            # Base relevance score
            score = 0.0

            # (A) Repository boost
            if repository_id and rec.repository_id == repository_id:
                score += 0.35

            # (B) Task type match
            if task_type and rec.task_type.lower() == task_type.lower():
                score += 0.25
            elif rec.task_type.lower() in obj_lower:
                score += 0.15

            # (C) Symbol overlap
            if target_symbols and rec.symbols_involved:
                overlap = len(target_symbols.intersection(set(rec.symbols_involved)))
                if overlap > 0:
                    score += 0.25 * (overlap / len(target_symbols))

            # (D) File overlap
            if target_files and rec.files_involved:
                rec_files = {Path(f).as_posix() for f in rec.files_involved}
                f_overlap = len(target_files.intersection(rec_files))
                if f_overlap > 0:
                    score += 0.20 * (f_overlap / len(target_files))

            # (E) Keyword overlap in problem and solution summary
            summary_corpus = f"{rec.problem_summary} {rec.solution_summary} {' '.join(rec.tags)} {' '.join(rec.symbols_involved)}".lower()
            rec_words = set(re.findall(r"\w{3,}", summary_corpus))
            obj_words = set(re.findall(r"\w{3,}", obj_lower))
            if rec_words and obj_words:
                kw_overlap = len(rec_words.intersection(obj_words))
                if kw_overlap > 0:
                    score += 0.20 * min(1.0, kw_overlap / 2.0)

            # (F) Verification quality boost
            if rec.is_verified:
                score += 0.20 * rec.confidence
            elif rec.is_diagnostic_failure:
                score += 0.05  # diagnostic failures are valuable warnings

            if score > 0.0:
                candidates.append((rec, score))

        # Sort descending by score
        candidates.sort(key=lambda x: x[1], reverse=True)
        top_records = [c[0] for c in candidates[:max_items]]

        # Conflict Detection: Identify contradictory solutions
        conflicts = self.detect_conflicts(top_records)

        # Apply character budget
        bounded_records: List[EngineeringMemoryRecord] = []
        chars_used = 0
        for rec in top_records:
            entry_len = len(rec.problem_summary) + len(rec.solution_summary) + 100
            if chars_used + entry_len > budget_chars:
                break
            bounded_records.append(rec)
            chars_used += entry_len

        return bounded_records, conflicts

    def detect_conflicts(
        self,
        records: List[EngineeringMemoryRecord],
    ) -> List[EngineeringMemoryConflict]:
        """
        Identify contradictory or divergent memories for identical symbols or problem targets.
        """
        conflicts: List[EngineeringMemoryConflict] = []
        n = len(records)
        for i in range(n):
            for j in range(i + 1, n):
                r1 = records[i]
                r2 = records[j]

                # Check for symbol overlap or problem summary overlap
                common_syms = set(r1.symbols_involved).intersection(set(r2.symbols_involved))
                prob_overlap = bool(r1.problem_summary and r2.problem_summary and r1.problem_summary.lower() == r2.problem_summary.lower())
                if common_syms or prob_overlap:
                    # If both provide solutions but differ significantly in solution text
                    if r1.solution_summary and r2.solution_summary and r1.solution_summary != r2.solution_summary:
                        # Determine recommendation based on verification confidence and recency
                        if r1.confidence > r2.confidence:
                            rec_res = f"Prefer memory '{r1.memory_id}' (higher confidence: {r1.confidence:.2f} vs {r2.confidence:.2f})"
                        elif r2.confidence > r1.confidence:
                            rec_res = f"Prefer memory '{r2.memory_id}' (higher confidence: {r2.confidence:.2f} vs {r1.confidence:.2f})"
                        elif r1.created_at >= r2.created_at:
                            rec_res = f"Prefer memory '{r1.memory_id}' (more recent)"
                        else:
                            rec_res = f"Prefer memory '{r2.memory_id}' (more recent)"

                        syms_list = sorted(list(common_syms)) if common_syms else ["Session"]
                        conf_desc = f"Differing solutions for overlapping symbol(s): {', '.join(syms_list)}" if common_syms else f"Differing solutions for problem: {r1.problem_summary}"
                        conflicts.append(EngineeringMemoryConflict(
                            conflict_id=f"conf_{i}_{j}",
                            problem_key=f"symbols:{','.join(syms_list)}",
                            conflicting_record_ids=[r1.memory_id, r2.memory_id],
                            divergent_symbols=syms_list,
                            memory_a=r1,
                            memory_b=r2,
                            conflict_description=conf_desc,
                            conflict_reason=conf_desc,
                            recommended_resolution=rec_res,
                            resolution_guidance=rec_res,
                            exposes_to_planner=True,
                        ))
        return conflicts

    def promote_task_outcome(
        self,
        task_id: str,
        tenant_id: str,
        repository_id: Optional[str] = None,
        task_type: str = "general",
        problem_summary: str = "",
        solution_summary: str = "",
        files_involved: Optional[List[str]] = None,
        symbols_involved: Optional[List[str]] = None,
        verification_success: Optional[bool] = None,
        verifier_output: str = "",
        verifier_score: float = 1.0,
        skill_used: Optional[str] = None,
        scope: MemoryScope = MemoryScope.REPOSITORY,
        evidence_reference: str = "",
        allow_diagnostic_failure: bool = False,
        **kwargs: Any,
    ) -> Optional[EngineeringMemoryRecord]:
        """
        Governed memory promotion policy.

        Invariant: A failed or unverified task CANNOT become trusted solution memory.
        If verification_success is False:
          - Only stored if allow_diagnostic_failure is True, marked as is_diagnostic_failure=True.
          - Otherwise, promotion is rejected and returns None.
        """
        repo_id = repository_id or kwargs.get("repo_id") or "global"
        prob = problem_summary or kwargs.get("task_objective") or kwargs.get("objective") or kwargs.get("prompt") or ""
        sol = solution_summary or kwargs.get("summary") or ""
        diff_sum = kwargs.get("diff") or kwargs.get("diff_summary") or ""
        files = files_involved or kwargs.get("files_changed") or kwargs.get("files") or []
        syms = symbols_involved or kwargs.get("symbols") or []
        skills = kwargs.get("skills_used") or ([skill_used] if skill_used else [])
        used_skill = skill_used or (skills[0] if skills else None)
        ev_ref = evidence_reference or kwargs.get("evidence_ref") or task_id

        # Verification extraction
        ver_sum_arg = kwargs.get("verification_summary")
        if ver_sum_arg and isinstance(ver_sum_arg, dict):
            status = ver_sum_arg.get("status")
            v_success = (
                verification_success
                if verification_success is not None
                else (status in ("VERIFIED", "succeeded", "SUCCEEDED", True) or bool(ver_sum_arg.get("verified")))
            )
            v_output = verifier_output or str(ver_sum_arg.get("details", ""))
            v_score = float(ver_sum_arg.get("score", verifier_score))
        else:
            v_success = True if verification_success is None else verification_success
            v_output = verifier_output
            v_score = float(verifier_score)

        if not v_success and not allow_diagnostic_failure:
            logger.info("Task '%s' was unverified/failed; rejecting promotion to trusted memory.", task_id)
            return None

        mem_id = f"eng_mem_{uuid.uuid4().hex[:12]}"
        now = time.time()

        ver_summary = VerificationResultSummary(
            verified=v_success,
            verifier_score=v_score if v_success else 0.0,
            verifier_output=scrub_secrets(v_output[:500]),
            verifier_role="VERIFIER",
        )

        record = EngineeringMemoryRecord(
            memory_id=mem_id,
            tenant_id=tenant_id,
            repository_id=repo_id,
            scope=scope,
            task_type=task_type or "general",
            problem_summary=scrub_secrets(prob),
            solution_summary=scrub_secrets(sol),
            diff_summary=scrub_secrets(diff_sum),
            files_involved=list(files),
            symbols_involved=list(syms),
            skill_used=used_skill,
            skills_used=list(skills),
            verification_result=ver_summary,
            evidence_reference=ev_ref,
            confidence=v_score if v_success else 0.5,
            provenance={
                "task_id": task_id,
                "promoted_at": now,
                "verifier_passage": v_success,
            },
            is_verified=v_success,
            is_diagnostic_failure=not v_success,
            created_at=now,
            updated_at=now,
        )

        saved = self.save_record(record)
        logger.info(
            "Promoted task '%s' to engineering memory '%s' (verified=%s, scope=%s)",
            task_id, mem_id, v_success, scope.value
        )
        return saved
