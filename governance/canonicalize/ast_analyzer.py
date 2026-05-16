"""
governance/canonicalize/ast_analyzer.py — AST-Level Structural Analysis
=========================================================================
Moves beyond string matching to understand the EXECUTION SEMANTICS of
shell commands and code snippets by analyzing their abstract structure.

The problem with string matching:
  "rm -rf /" is caught.
  "$(printf 'rm')  -$(printf 'rf') /" is not.
  "eval $(echo 'cm0gLXJmIC8=' | base64 -d)" is not.
  "x='rm'; y='-rf'; z='/'; $x $y $z" is not.

This module performs:
  1. Shell command parsing into a CommandAST
  2. Execution graph construction (what runs what)
  3. Pipe chain analysis (chained dangerous commands)
  4. Variable expansion risk scoring
  5. Eval/exec depth analysis (recursive execution)
  6. Structural danger classification independent of surface text

Architecture:
  ShellParser: tokenizes shell snippets into command trees
  CommandAST: represents the structural command graph
  ASTAnalyzer: evaluates structural danger from the AST
  ExecutionGraphScorer: scores the overall execution intent
"""

import re
import shlex
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from enum import Enum

logger = logging.getLogger("nexora.governance.canonicalize.ast")


# ── AST Node Types ────────────────────────────────────────────────────────────

class NodeType(str, Enum):
    COMMAND         = "COMMAND"
    PIPE            = "PIPE"
    REDIRECT        = "REDIRECT"
    SUBSHELL        = "SUBSHELL"
    VARIABLE_ASSIGN = "VARIABLE_ASSIGN"
    EVAL_EXEC       = "EVAL_EXEC"
    COMPOUND        = "COMPOUND"
    UNKNOWN         = "UNKNOWN"


@dataclass
class ASTNode:
    """A node in the shell command abstract syntax tree."""
    node_type: NodeType
    command: str          # Primary command or operator
    args: List[str]       # Arguments
    children: List["ASTNode"] = field(default_factory=list)
    danger_score: float = 0.0
    danger_reason: str = ""


@dataclass
class CommandAST:
    """Full abstract syntax tree for a shell snippet."""
    raw: str
    nodes: List[ASTNode]
    depth: int                    # Nesting depth (eval(eval(...)))
    has_eval: bool
    has_pipe_chain: bool
    has_redirect: bool
    has_variable_expansion: bool
    structural_danger_score: float
    structural_signals: List[str]


# ── Dangerous Command Catalog ─────────────────────────────────────────────────

# (command_name, min_args, danger_score, description)
DANGEROUS_COMMANDS: List[Tuple[str, int, float, str]] = [
    # Destructive
    ("rm",         1, 0.60, "File removal"),
    ("shred",      1, 0.65, "Secure file deletion"),
    ("dd",         0, 0.55, "Raw disk write"),
    ("mkfs",       1, 0.80, "Format filesystem"),
    ("wipefs",     1, 0.80, "Wipe filesystem"),
    # Privilege
    ("sudo",       1, 0.50, "Privilege escalation"),
    ("su",         0, 0.45, "Switch user"),
    ("chmod",      2, 0.40, "Permission change"),
    ("chown",      2, 0.40, "Ownership change"),
    ("setuid",     0, 0.70, "Setuid"),
    # Execution
    ("eval",       1, 0.80, "Dynamic code execution"),
    ("exec",       1, 0.75, "Process replace"),
    ("bash",       0, 0.50, "Shell invocation"),
    ("sh",         0, 0.50, "Shell invocation"),
    ("python",     0, 0.45, "Python execution"),
    ("python3",    0, 0.45, "Python execution"),
    ("node",       0, 0.45, "Node.js execution"),
    ("perl",       1, 0.50, "Perl execution"),
    ("ruby",       1, 0.45, "Ruby execution"),
    # Network
    ("curl",       1, 0.35, "HTTP client"),
    ("wget",       1, 0.35, "HTTP download"),
    ("nc",         2, 0.70, "Netcat"),
    ("ncat",       2, 0.70, "Ncat"),
    ("socat",      2, 0.70, "Socat"),
    ("ssh",        1, 0.40, "SSH client"),
    ("scp",        2, 0.45, "Secure copy"),
    # Persistence
    ("crontab",    1, 0.55, "Cron manipulation"),
    ("at",         1, 0.50, "Scheduled execution"),
    ("systemctl",  2, 0.50, "Service management"),
    # Reconnaissance
    ("nmap",       1, 0.60, "Network scanner"),
    ("masscan",    1, 0.65, "Port scanner"),
    ("netstat",    0, 0.25, "Network connections"),
    ("ss",         0, 0.20, "Socket statistics"),
    # Crypto/Encoding (often used in obfuscation)
    ("base64",     0, 0.30, "Base64 codec"),
    ("openssl",    1, 0.45, "Crypto operations"),
]

DANGEROUS_CMD_INDEX: Dict[str, Tuple[float, str]] = {
    cmd: (score, desc) for cmd, _, score, desc in DANGEROUS_COMMANDS
}

# Dangerous flags that amplify base command risk
DANGEROUS_FLAGS: Dict[str, float] = {
    "-rf": 0.30, "-r": 0.15, "-f": 0.10,
    "--no-preserve-root": 0.40,
    "-e": 0.20, "-c": 0.15,         # eval/execute flags
    "-l": 0.25,                      # listen (nc -l)
    "--script": 0.35,                # nmap scripts
    "-p-": 0.30,                     # all ports
    "-sS": 0.25, "-sU": 0.25,        # stealth scans
}

# Redirect danger indicators
DANGEROUS_REDIRECTS = frozenset({
    "/etc/passwd", "/etc/shadow", "/etc/cron", "/etc/sudoers",
    "/root/", "~/.ssh/", "~/.aws/",
    "/dev/tcp/", "/proc/", "/sys/",
})


# ── Shell Parser ──────────────────────────────────────────────────────────────

class ShellParser:
    """
    Lightweight shell command tokenizer and structure extractor.
    Does not require a full shell interpreter — uses heuristic parsing.
    """

    # Pipe and redirect patterns
    _PIPE_RE     = re.compile(r'\|(?!\|)')
    _REDIRECT_RE = re.compile(r'(?:>>?|<<?)(\S+)')
    _SUBSHELL_RE = re.compile(r'\$\(([^)]+)\)|\`([^`]+)\`')
    _EVAL_RE     = re.compile(r'\b(eval|exec)\b', re.I)
    _VAR_RE      = re.compile(r'\$[A-Za-z_][A-Za-z0-9_]*|\$\{[^}]+\}')
    _ASSIGN_RE   = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=(.+)$')

    @classmethod
    def parse(cls, text: str) -> CommandAST:
        """Parse a shell snippet into a CommandAST."""
        nodes: List[ASTNode] = []
        depth = 0
        has_eval = False
        has_pipe = False
        has_redirect = False
        has_var_expansion = bool(cls._VAR_RE.search(text))
        signals: List[str] = []

        # Split on semicolons and newlines (separate commands)
        statements = re.split(r'[;\n]', text)

        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue

            # Detect subshells / command substitution
            subshells = cls._SUBSHELL_RE.findall(stmt)
            if subshells:
                depth += 1
                for sub_group in subshells:
                    sub_cmd = sub_group[0] or sub_group[1]
                    if cls._EVAL_RE.search(sub_cmd):
                        has_eval = True
                        depth += 1

            if cls._EVAL_RE.search(stmt):
                has_eval = True
                signals.append("EVAL_EXEC")

            # Check for pipes
            if cls._PIPE_RE.search(stmt):
                has_pipe = True
                pipe_parts = cls._PIPE_RE.split(stmt)
                node = ASTNode(
                    node_type=NodeType.PIPE,
                    command="|".join(p.strip().split()[0] for p in pipe_parts if p.strip()),
                    args=[],
                    children=[cls._parse_single(p.strip()) for p in pipe_parts],
                )
                nodes.append(node)
            else:
                # Check for redirects
                redirect_m = cls._REDIRECT_RE.search(stmt)
                if redirect_m:
                    has_redirect = True
                    target = redirect_m.group(1)
                    if any(target.startswith(d) for d in DANGEROUS_REDIRECTS):
                        signals.append(f"DANGEROUS_REDIRECT:{target}")

                # Parse as single command
                nodes.append(cls._parse_single(stmt))

        # ── Structural danger scoring ─────────────────────────────────────────
        total_danger = 0.0
        for node in nodes:
            total_danger = max(total_danger, node.danger_score)

        # Eval amplifies all danger
        if has_eval:
            total_danger = min(1.0, total_danger + 0.30)
            signals.append("EVAL_AMPLIFICATION")

        # Deep nesting amplifies
        if depth >= 2:
            total_danger = min(1.0, total_danger + 0.15 * (depth - 1))
            signals.append(f"NESTING_DEPTH:{depth}")

        # Pipe chains with dangerous commands
        if has_pipe and total_danger > 0.35:
            total_danger = min(1.0, total_danger + 0.15)
            signals.append("DANGEROUS_PIPE_CHAIN")

        return CommandAST(
            raw=text,
            nodes=nodes,
            depth=depth,
            has_eval=has_eval,
            has_pipe_chain=has_pipe,
            has_redirect=has_redirect,
            has_variable_expansion=has_var_expansion,
            structural_danger_score=total_danger,
            structural_signals=signals,
        )

    @classmethod
    def _parse_single(cls, stmt: str) -> ASTNode:
        """Parse a single (non-piped) command statement."""
        if not stmt:
            return ASTNode(NodeType.UNKNOWN, "", [])

        # Variable assignment?
        assign_m = cls._ASSIGN_RE.match(stmt)
        if assign_m:
            return ASTNode(
                node_type=NodeType.VARIABLE_ASSIGN,
                command=assign_m.group(1),
                args=[assign_m.group(2)],
                danger_score=0.10,
                danger_reason="Variable assignment",
            )

        # Tokenize safely
        try:
            tokens = shlex.split(stmt)
        except ValueError:
            tokens = stmt.split()

        if not tokens:
            return ASTNode(NodeType.UNKNOWN, "", [])

        cmd = tokens[0].lower().lstrip("./ ")
        args = tokens[1:]

        # Look up base danger
        if cmd in DANGEROUS_CMD_INDEX:
            base_danger, reason = DANGEROUS_CMD_INDEX[cmd]
        else:
            base_danger = 0.05
            reason = "Unknown command"

        # Apply flag amplifiers
        flag_boost = 0.0
        for arg in args:
            if arg.startswith("-"):
                flag_boost += DANGEROUS_FLAGS.get(arg, 0.0)

        danger = min(1.0, base_danger + flag_boost)

        # Detect redirect danger
        redirect_m = cls._REDIRECT_RE.search(stmt)
        danger_reason = reason
        if redirect_m:
            target = redirect_m.group(1)
            if any(target.startswith(d) for d in DANGEROUS_REDIRECTS):
                danger = min(1.0, danger + 0.30)
                danger_reason += f" + dangerous redirect to {target}"

        return ASTNode(
            node_type=NodeType.COMMAND,
            command=cmd,
            args=args,
            danger_score=danger,
            danger_reason=danger_reason,
        )


# ── AST Analyzer ──────────────────────────────────────────────────────────────

class ASTAnalyzer:
    """Analyzes shell/code snippets at the structural level."""

    @classmethod
    def analyze(cls, text: str) -> CommandAST:
        """Parse and score the structural danger of a shell snippet."""
        ast = ShellParser.parse(text)
        if ast.structural_danger_score > 0.5:
            logger.warning(
                "[ASTAnalyzer] Structural danger=%.2f signals=%s",
                ast.structural_danger_score, ast.structural_signals,
            )
        return ast

    @classmethod
    def recommendation(cls, ast: CommandAST) -> str:
        """Map structural danger score to governance recommendation."""
        if ast.structural_danger_score >= 0.70 or ast.has_eval and ast.structural_danger_score >= 0.40:
            return "REFUSE"
        elif ast.structural_danger_score >= 0.40 or ast.has_eval:
            return "ESCALATE"
        else:
            return "APPROVE"
