"""
backend/execution/orchestration/repo_intelligence.py — Authoritative Repository Intelligence.

Phase 17: Repository Intelligence Kernel.

Provides structured, symbol-aware repository understanding:
  - File discovery and metadata indexing (size, language, last modified)
  - AST-based symbol extraction (classes, functions, async functions, methods)
  - Import and reference resolution (dependency and call relationships)
  - Incremental updates for individual file changes
  - Explainable task relevance ranking with verifiable reasons:
      * DIRECT_KEYWORD_MATCH
      * SYMBOL_MATCH
      * CALL_DEPENDENCY
      * RELATED_TEST
      * CONFIGURATION
      * RECENT_EXECUTION_EVIDENCE
      * USER_SPECIFIED_FILE
  - Strict workspace boundary confinement (no directory traversal)
"""
from __future__ import annotations

import ast
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("aetherion.orchestration.repo_intelligence")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SymbolKind(str, Enum):
    CLASS = "class"
    FUNCTION = "function"
    ASYNC_FUNCTION = "async_function"
    METHOD = "method"
    VARIABLE = "variable"
    IMPORT = "import"


class RelevanceReason(str, Enum):
    DIRECT_KEYWORD_MATCH = "direct_keyword_match"
    SYMBOL_MATCH = "symbol_match"
    CALL_DEPENDENCY = "call_dependency"
    RELATED_TEST = "related_test"
    CONFIGURATION = "configuration"
    RECENT_EXECUTION_EVIDENCE = "recent_execution_evidence"
    USER_SPECIFIED_FILE = "user_specified_file"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class SymbolNode:
    """Represents a code symbol (class, function, method) in a source file."""
    name: str
    qualified_name: str
    kind: SymbolKind
    file_path: str                 # relative to repository root
    line_number: int
    end_line_number: int
    docstring: Optional[str] = None
    signature: Optional[str] = None
    calls: List[str] = field(default_factory=list)
    references: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "kind": self.kind.value,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "end_line_number": self.end_line_number,
            "docstring": self.docstring[:100] if self.docstring else None,
            "signature": self.signature,
            "calls": self.calls,
            "references": self.references,
        }


@dataclass
class FileNode:
    """Represents a file in the repository."""
    path: str                      # relative to repository root
    language: str                  # "python", "json", "toml", "markdown", "other"
    size_bytes: int
    last_modified: float
    symbols: List[SymbolNode] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)          # modules or paths imported
    imported_by: List[str] = field(default_factory=list)      # file paths that import this file
    is_test: bool = False
    is_config: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "size_bytes": self.size_bytes,
            "symbol_count": len(self.symbols),
            "symbols": [s.name for s in self.symbols[:10]],
            "imports": self.imports,
            "imported_by": self.imported_by,
            "is_test": self.is_test,
            "is_config": self.is_config,
        }


@dataclass
class RelevanceItem:
    """An explainable item selected for context relevance."""
    path: str
    item_type: str                 # "file", "symbol", "test", "config"
    name: str
    score: float                   # 0.0 to 1.0 (higher = more relevant)
    reason: RelevanceReason
    details: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "item_type": self.item_type,
            "name": self.name,
            "score": round(self.score, 3),
            "reason": self.reason.value,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# AST Extraction Visitor
# ---------------------------------------------------------------------------

class _PythonAstVisitor(ast.NodeVisitor):
    """Extracts symbols, calls, and imports from a Python AST."""

    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        self.symbols: List[SymbolNode] = []
        self.imports: List[str] = []
        self._class_stack: List[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        for alias in node.names:
            full = f"{mod}.{alias.name}" if mod else alias.name
            self.imports.append(full)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qual_name = ".".join(self._class_stack + [node.name])
        doc = ast.get_docstring(node)
        sym = SymbolNode(
            name=node.name,
            qualified_name=qual_name,
            kind=SymbolKind.CLASS,
            file_path=self.rel_path,
            line_number=node.lineno,
            end_line_number=getattr(node, "end_lineno", node.lineno),
            docstring=doc,
            signature=f"class {node.name}",
        )
        self.symbols.append(sym)

        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_function(node, is_async=True)

    def _handle_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool) -> None:
        is_method = bool(self._class_stack)
        kind = SymbolKind.METHOD if is_method else (
            SymbolKind.ASYNC_FUNCTION if is_async else SymbolKind.FUNCTION
        )
        qual_name = ".".join(self._class_stack + [node.name])
        doc = ast.get_docstring(node)

        # Build rough signature
        args = [a.arg for a in node.args.args]
        prefix = "async def" if is_async else "def"
        sig = f"{prefix} {node.name}({', '.join(args)})"

        # Discover calls made inside the function
        calls: List[str] = []
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Name):
                    calls.append(child.func.id)
                elif isinstance(child.func, ast.Attribute):
                    calls.append(child.func.attr)

        sym = SymbolNode(
            name=node.name,
            qualified_name=qual_name,
            kind=kind,
            file_path=self.rel_path,
            line_number=node.lineno,
            end_line_number=getattr(node, "end_lineno", node.lineno),
            docstring=doc,
            signature=sig,
            calls=calls,
        )
        self.symbols.append(sym)
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# RepositoryMap
# ---------------------------------------------------------------------------

class RepositoryMap:
    """
    Symbol-aware, incremental map of repository files, symbols, and dependencies.

    Keeps indexing bounded: ignores .git, __pycache__, venvs, node_modules, and
    limits maximum files scanned to avoid memory exhaustion.
    """

    MAX_FILES = 800
    MAX_FILE_SIZE_BYTES = 500_000  # 500 KB per file max for parsing
    IGNORED_DIRS = {
        ".git", ".pytest_cache", "__pycache__", "venv", ".venv",
        "env", "node_modules", ".tox", ".mypy_cache", "dist", "build",
    }
    CONFIG_EXTENSIONS = {".ini", ".cfg", ".toml", ".yaml", ".yml", ".json"}
    CONFIG_FILENAMES = {"setup.py", "requirements.txt", "pyproject.toml", "package.json"}

    def __init__(self, repo_root: Path | str) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.files: Dict[str, FileNode] = {}
        self.symbol_index: Dict[str, List[SymbolNode]] = {}
        self.import_graph: Dict[str, Set[str]] = {}
        self.reverse_import_graph: Dict[str, Set[str]] = {}

    def build_map(self) -> None:
        """Walk repository and build initial index."""
        self.files.clear()
        self.symbol_index.clear()
        self.import_graph.clear()
        self.reverse_import_graph.clear()

        if not self.repo_root.is_dir():
            return

        count = 0
        for root, dirs, filenames in os.walk(self.repo_root):
            # Prune ignored directories in-place
            dirs[:] = [d for d in dirs if d not in self.IGNORED_DIRS]

            for fname in filenames:
                if count >= self.MAX_FILES:
                    logger.warning("RepositoryMap file limit (%d) reached", self.MAX_FILES)
                    break
                full_path = Path(root) / fname
                try:
                    rel_path = full_path.relative_to(self.repo_root).as_posix()
                except ValueError:
                    continue
                self._index_file(full_path, rel_path)
                count += 1

        self._resolve_reverse_imports()

    def update_file(self, rel_path: str) -> None:
        """Incrementally re-index a single file that has been modified."""
        norm_path = Path(rel_path).as_posix()
        full_path = self.repo_root / rel_path

        # Remove old symbol entries for this file
        old_file = self.files.get(norm_path)
        if old_file:
            for sym in old_file.symbols:
                if sym.name in self.symbol_index:
                    self.symbol_index[sym.name] = [
                        s for s in self.symbol_index[sym.name] if s.file_path != norm_path
                    ]
                    if not self.symbol_index[sym.name]:
                        del self.symbol_index[sym.name]

        if full_path.is_file():
            self._index_file(full_path, norm_path)
            self._resolve_reverse_imports()
        else:
            self.remove_file(norm_path)

    def remove_file(self, rel_path: str) -> None:
        """Remove a file from the repository map."""
        norm_path = Path(rel_path).as_posix()
        old_file = self.files.pop(norm_path, None)
        if old_file:
            for sym in old_file.symbols:
                if sym.name in self.symbol_index:
                    self.symbol_index[sym.name] = [
                        s for s in self.symbol_index[sym.name] if s.file_path != norm_path
                    ]
                    if not self.symbol_index[sym.name]:
                        del self.symbol_index[sym.name]
        self.import_graph.pop(norm_path, None)
        self.reverse_import_graph.pop(norm_path, None)

    def find_symbol(self, name: str) -> List[SymbolNode]:
        """Find all definitions matching a symbol name."""
        return list(self.symbol_index.get(name, []))

    def find_references(self, symbol_name: str) -> List[Dict[str, Any]]:
        """Find files and functions referencing or calling a symbol."""
        refs: List[Dict[str, Any]] = []
        for file_node in self.files.values():
            for sym in file_node.symbols:
                if symbol_name in sym.calls or symbol_name in sym.references:
                    refs.append({
                        "file_path": file_node.path,
                        "caller_symbol": sym.name,
                        "caller_qualified_name": sym.qualified_name,
                        "line_number": sym.line_number,
                    })
        return refs

    def get_dependencies(self, rel_path: str) -> Set[str]:
        """Get set of files imported by rel_path."""
        return set(self.import_graph.get(Path(rel_path).as_posix(), set()))

    def get_dependents(self, rel_path: str) -> Set[str]:
        """Get set of files that import rel_path."""
        return set(self.reverse_import_graph.get(Path(rel_path).as_posix(), set()))

    def get_related_tests(self, rel_path: str) -> List[str]:
        """Discover test files relevant to rel_path."""
        norm_path = Path(rel_path).as_posix()
        stem = Path(rel_path).stem
        related: List[str] = []

        for fpath, fnode in self.files.items():
            if not fnode.is_test:
                continue
            # Direct name match: test_foo.py matches foo.py
            if f"test_{stem}" in fpath or f"{stem}_test" in fpath:
                related.append(fpath)
            # Import match: test imports the module
            elif norm_path in fnode.imports or stem in fnode.imports:
                if fpath not in related:
                    related.append(fpath)
            # Reverse import graph: test imports the file directly
            elif fpath in self.get_dependents(norm_path):
                if fpath not in related:
                    related.append(fpath)

        return related

    def as_dict(self) -> Dict[str, Any]:
        """Return structured summary of the repository map."""
        return {
            "repo_root": str(self.repo_root),
            "file_count": len(self.files),
            "symbol_count": sum(len(f.symbols) for f in self.files.values()),
            "python_files": sum(1 for f in self.files.values() if f.language == "python"),
            "test_files": sum(1 for f in self.files.values() if f.is_test),
            "config_files": sum(1 for f in self.files.values() if f.is_config),
        }

    # ------------------------------------------------------------------
    # Private indexing helpers
    # ------------------------------------------------------------------

    def _index_file(self, full_path: Path, rel_path: str) -> None:
        try:
            stat = full_path.stat()
            size = stat.st_size
            mtime = stat.st_mtime
        except Exception:
            return

        lang = self._detect_language(full_path)
        is_test = self._is_test_file(rel_path)
        is_config = self._is_config_file(full_path, rel_path)

        symbols: List[SymbolNode] = []
        imports: List[str] = []

        if lang == "python" and size <= self.MAX_FILE_SIZE_BYTES:
            symbols, imports = self._parse_python_file(full_path, rel_path)

        node = FileNode(
            path=rel_path,
            language=lang,
            size_bytes=size,
            last_modified=mtime,
            symbols=symbols,
            imports=imports,
            is_test=is_test,
            is_config=is_config,
        )
        self.files[rel_path] = node

        # Update symbol index
        for sym in symbols:
            self.symbol_index.setdefault(sym.name, []).append(sym)

    def _parse_python_file(self, full_path: Path, rel_path: str) -> Tuple[List[SymbolNode], List[str]]:
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as fh:
                source = fh.read()
            tree = ast.parse(source, filename=rel_path)
            visitor = _PythonAstVisitor(rel_path)
            visitor.visit(tree)
            return visitor.symbols, visitor.imports
        except Exception as exc:
            logger.debug("AST parse skipped for %s: %s", rel_path, exc)
            return [], []

    def _resolve_reverse_imports(self) -> None:
        """Resolve import names to file paths and populate dependency graphs."""
        self.import_graph.clear()
        self.reverse_import_graph.clear()

        # Build module name -> file_path lookup
        module_to_file: Dict[str, str] = {}
        for fpath in self.files:
            if fpath.endswith(".py"):
                mod_name = fpath[:-3].replace("/", ".").replace("\\", ".")
                module_to_file[mod_name] = fpath
                base = Path(fpath).stem
                module_to_file[base] = fpath

        for fpath, fnode in self.files.items():
            deps: Set[str] = set()
            for imp in fnode.imports:
                # Match full or prefix
                target_file = module_to_file.get(imp)
                if not target_file and "." in imp:
                    target_file = module_to_file.get(imp.rsplit(".", 1)[0])
                if target_file and target_file != fpath:
                    deps.add(target_file)

            self.import_graph[fpath] = deps
            for d in deps:
                self.reverse_import_graph.setdefault(d, set()).add(fpath)

    @staticmethod
    def _detect_language(path: Path) -> str:
        ext = path.suffix.lower()
        if ext == ".py":
            return "python"
        elif ext == ".json":
            return "json"
        elif ext in (".toml", ".ini", ".cfg"):
            return "toml"
        elif ext in (".yaml", ".yml"):
            return "yaml"
        elif ext in (".md", ".rst"):
            return "markdown"
        elif ext in (".sh", ".bash", ".ps1"):
            return "script"
        return "other"

    @staticmethod
    def _is_test_file(rel_path: str) -> bool:
        lower = rel_path.lower()
        parts = lower.split("/")
        return any(
            p.startswith("test") or p.endswith("_test.py") or p.startswith("test_")
            for p in parts
        )

    def _is_config_file(self, full_path: Path, rel_path: str) -> bool:
        if full_path.name in self.CONFIG_FILENAMES:
            return True
        return full_path.suffix.lower() in self.CONFIG_EXTENSIONS


# ---------------------------------------------------------------------------
# TaskRelevanceEngine
# ---------------------------------------------------------------------------

class TaskRelevanceEngine:
    """
    Ranks repository items by relevance to a task objective with explainable rationale.

    Never fabricates relevance explanations. Every selection references an explicit,
    verifiable reason (keyword match, symbol match, import relation, related test, config).
    """

    STOP_WORDS = {
        "the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "with",
        "by", "of", "from", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "can", "could", "should",
        "would", "may", "might", "must", "will", "shall", "it", "this", "that",
        "make", "run", "add", "fix", "update", "create", "test", "task",
    }

    def analyze(
        self,
        repo_map: RepositoryMap,
        objective: str,
        user_files: Optional[List[str]] = None,
        recent_evidence: Optional[Dict[str, Any]] = None,
        max_candidates: int = 15,
    ) -> List[RelevanceItem]:
        """
        Rank candidate files and symbols for a task objective.

        Returns explainable list of RelevanceItem objects sorted descending by score.
        """
        items: List[RelevanceItem] = []
        seen_paths: Set[str] = set()

        # 1. User-specified files get highest priority (verifiable reason)
        for uf in (user_files or []):
            norm_uf = Path(uf).as_posix()
            if norm_uf in repo_map.files and norm_uf not in seen_paths:
                items.append(RelevanceItem(
                    path=norm_uf,
                    item_type="file",
                    name=Path(norm_uf).name,
                    score=1.0,
                    reason=RelevanceReason.USER_SPECIFIED_FILE,
                    details=f"Explicitly specified by user or test: '{uf}'",
                ))
                seen_paths.add(norm_uf)

        # 2. Extract tokens and identifiers from objective
        keywords = self._extract_keywords(objective)
        identifiers = self._extract_identifiers(objective)

        # 3. Match Symbols in repository
        matched_symbols: List[SymbolNode] = []
        for ident in identifiers:
            syms = repo_map.find_symbol(ident)
            for s in syms:
                matched_symbols.append(s)
                if s.file_path not in seen_paths:
                    items.append(RelevanceItem(
                        path=s.file_path,
                        item_type="symbol",
                        name=s.qualified_name,
                        score=0.9,
                        reason=RelevanceReason.SYMBOL_MATCH,
                        details=f"Symbol '{s.name}' ({s.kind.value}) referenced in objective",
                    ))
                    seen_paths.add(s.file_path)

        # 4. Keyword matches on files (file stem, path segments, and symbol names)
        for fpath, fnode in repo_map.files.items():
            if fpath in seen_paths or fnode.is_test or fnode.is_config:
                continue

            stem = Path(fpath).stem.lower()
            path_lower = fpath.lower()

            # Direct stem match
            if stem in keywords:
                items.append(RelevanceItem(
                    path=fpath,
                    item_type="file",
                    name=Path(fpath).name,
                    score=0.8,
                    reason=RelevanceReason.DIRECT_KEYWORD_MATCH,
                    details=f"File name stem '{stem}' matches task keyword",
                ))
                seen_paths.add(fpath)
                continue

            # Substring match in path
            matches = [kw for kw in keywords if kw in path_lower and len(kw) >= 4]
            if matches:
                items.append(RelevanceItem(
                    path=fpath,
                    item_type="file",
                    name=Path(fpath).name,
                    score=0.65,
                    reason=RelevanceReason.DIRECT_KEYWORD_MATCH,
                    details=f"Path matched task keywords: {', '.join(matches[:3])}",
                ))
                seen_paths.add(fpath)
                continue

        # 5. Dependency & Call Relations from already matched files
        current_matched_files = list(seen_paths)
        for mf in current_matched_files:
            deps = repo_map.get_dependencies(mf)
            for d in deps:
                if d not in seen_paths and len(items) < max_candidates:
                    items.append(RelevanceItem(
                        path=d,
                        item_type="file",
                        name=Path(d).name,
                        score=0.6,
                        reason=RelevanceReason.CALL_DEPENDENCY,
                        details=f"Direct dependency of matched file '{mf}'",
                    ))
                    seen_paths.add(d)

        # 6. Related Tests
        for mf in current_matched_files:
            tests = repo_map.get_related_tests(mf)
            for t in tests:
                if t not in seen_paths and len(items) < max_candidates:
                    items.append(RelevanceItem(
                        path=t,
                        item_type="test",
                        name=Path(t).name,
                        score=0.75,
                        reason=RelevanceReason.RELATED_TEST,
                        details=f"Associated test for target file '{mf}'",
                    ))
                    seen_paths.add(t)

        # Also check for tests directly matching task keywords
        for fpath, fnode in repo_map.files.items():
            if fnode.is_test and fpath not in seen_paths and len(items) < max_candidates:
                stem = Path(fpath).stem.lower()
                path_lower = fpath.lower()
                if stem in keywords or any(kw in path_lower for kw in keywords if len(kw) >= 4):
                    items.append(RelevanceItem(
                        path=fpath,
                        item_type="test",
                        name=Path(fpath).name,
                        score=0.75,
                        reason=RelevanceReason.RELATED_TEST,
                        details=f"Associated test matching task keyword: '{Path(fpath).name}'",
                    ))
                    seen_paths.add(fpath)

        # 7. Configuration files (if relevant to task objective)
        config_triggers = (
            "config", "configs", "configuration", "configurations",
            "settings", "setting", "setup", "dependency", "package",
            "env", "environment", "timeout", "retry", "retries", "limit",
        )
        if any(kw in keywords for kw in config_triggers) or any(t in objective.lower() for t in ("config", "setting", "conf")):
            for fpath, fnode in repo_map.files.items():
                if fnode.is_config and fpath not in seen_paths and len(items) < max_candidates:
                    items.append(RelevanceItem(
                        path=fpath,
                        item_type="config",
                        name=Path(fpath).name,
                        score=0.7,
                        reason=RelevanceReason.CONFIGURATION,
                        details=f"Configuration file relevant to task settings: '{Path(fpath).name}'",
                    ))
                    seen_paths.add(fpath)

        # 8. Recent Execution Evidence (e.g. failing tests, edited files from prior attempts)
        if recent_evidence:
            edited = recent_evidence.get("files", [])
            for ef in edited:
                norm_ef = Path(ef).as_posix()
                if norm_ef in repo_map.files and norm_ef not in seen_paths:
                    items.append(RelevanceItem(
                        path=norm_ef,
                        item_type="file",
                        name=Path(norm_ef).name,
                        score=0.85,
                        reason=RelevanceReason.RECENT_EXECUTION_EVIDENCE,
                        details=f"File modified in prior attempt / execution evidence",
                    ))
                    seen_paths.add(norm_ef)

        # Sort descending by score
        items.sort(key=lambda x: x.score, reverse=True)
        return items[:max_candidates]

    def _extract_keywords(self, text: str) -> Set[str]:
        words = re.findall(r"[a-zA-Z0-9_]{3,}", text.lower())
        return {w for w in words if w not in self.STOP_WORDS}

    def _extract_identifiers(self, text: str) -> List[str]:
        # Identifiers: CamelCase, snake_case, or dot-separated
        raw = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b", text)
        filtered = [r for r in raw if r.lower() not in self.STOP_WORDS]
        seen = set()
        unique = []
        for f in sorted(filtered, key=lambda x: (len(x), x), reverse=True):
            if f not in seen:
                seen.add(f)
                unique.append(f)
        return unique


# ---------------------------------------------------------------------------
# RepositoryIntelligence (Authoritative Coordinator)
# ---------------------------------------------------------------------------

class RepositoryIntelligence:
    """
    Authoritative repository intelligence facade for a task execution.

    Coordinates RepositoryMap and TaskRelevanceEngine.
    Enforces workspace boundary confinement — strictly prevents directory traversal.
    """

    def __init__(self, repo_root: Path | str) -> None:
        self.repo_root = Path(repo_root).resolve()
        self._map = RepositoryMap(self.repo_root)
        self._engine = TaskRelevanceEngine()
        self._initialized = False

    def initialize(self) -> None:
        """Scan and build the repository map."""
        if not self._initialized:
            self._map.build_map()
            self._initialized = True

    @property
    def repository_map(self) -> RepositoryMap:
        self.initialize()
        return self._map

    def get_task_context(
        self,
        objective: str,
        user_files: Optional[List[str]] = None,
        recent_evidence: Optional[Dict[str, Any]] = None,
        max_candidates: int = 15,
    ) -> List[RelevanceItem]:
        """Produce ranked, explainable relevance items for a task."""
        self.initialize()
        return self._engine.analyze(
            repo_map=self._map,
            objective=objective,
            user_files=user_files,
            recent_evidence=recent_evidence,
            max_candidates=max_candidates,
        )

    def find_symbol(self, symbol_name: str) -> List[SymbolNode]:
        """Look up symbol definitions in repository."""
        self.initialize()
        return self._map.find_symbol(symbol_name)

    def find_references(self, symbol_name: str) -> List[Dict[str, Any]]:
        """Look up all references and call sites for a symbol."""
        self.initialize()
        return self._map.find_references(symbol_name)

    def get_affected_areas(self, changed_files: List[str]) -> Dict[str, Any]:
        """Compute affected downstream files and related tests for a list of changes."""
        self.initialize()
        all_dependents: Set[str] = set()
        all_tests: Set[str] = set()

        for cf in changed_files:
            deps = self._map.get_dependents(cf)
            all_dependents.update(deps)
            tests = self._map.get_related_tests(cf)
            all_tests.update(tests)

        return {
            "changed_files": changed_files,
            "affected_dependents": sorted(list(all_dependents)),
            "related_tests": sorted(list(all_tests)),
            "total_affected": len(all_dependents) + len(all_tests),
        }

    def read_file_safe(self, rel_path: str, max_chars: int = 2000) -> Optional[str]:
        """
        Safely read a file snippet, strictly enforcing workspace boundary.

        Returns None if file does not exist, is outside boundary, or cannot be read.
        """
        try:
            full = (self.repo_root / rel_path).resolve()
            # Confinement check: must stay within repo_root
            full.relative_to(self.repo_root)
            if not full.is_file():
                return None
            with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                return fh.read(max_chars)
        except Exception as exc:
            logger.debug("Safe file read rejected for %s: %s", rel_path, exc)
            return None

    def as_dict(self) -> Dict[str, Any]:
        self.initialize()
        return self._map.as_dict()
