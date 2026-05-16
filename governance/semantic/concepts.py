"""
governance/semantic/concepts.py — Semantic Concept Taxonomy & Scoring
======================================================================
Provides the foundational semantic knowledge graph for intent reasoning.
Instead of keyword matching, this models CONCEPTUAL DOMAINS and their
relationships to risk, legality, and harmful workflows.

Design Principle:
  A word is not dangerous. A CONCEPT is dangerous.
  "encrypt files" ≠ dangerous. "encrypt files without user consent" = ransomware.
  Context, framing, and capability combination determine harm.

Semantic Domains:
  Each domain contains:
    - Canonical concept name
    - Semantic tokens (words/phrases that activate this domain)
    - Base risk weight (0.0–1.0)
    - Legal context modifiers (what makes this domain safe vs harmful)
    - Amplification rules (what combinations escalate harm)
"""

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple
from enum import Enum


class ConceptDomain(str, Enum):
    """Top-level semantic domains for intent categorization."""
    # Capability domains
    FILE_SYSTEM       = "FILE_SYSTEM"
    NETWORK           = "NETWORK"
    PROCESS_EXEC      = "PROCESS_EXEC"
    CREDENTIAL        = "CREDENTIAL"
    CRYPTOGRAPHY      = "CRYPTOGRAPHY"
    COMMUNICATION     = "COMMUNICATION"
    AUTOMATION        = "AUTOMATION"
    MEMORY            = "MEMORY"
    DEPLOYMENT        = "DEPLOYMENT"
    # Intent-modifier domains
    CONCEALMENT       = "CONCEALMENT"      # hiding, obfuscating, encoding
    EXFILTRATION      = "EXFILTRATION"     # sending data out
    PERSISTENCE       = "PERSISTENCE"      # staying running after restart
    LATERAL_MOVEMENT  = "LATERAL_MOVEMENT" # spreading across systems
    SOCIAL_ENG        = "SOCIAL_ENG"       # manipulating humans
    RESEARCH          = "RESEARCH"         # educational/legitimate analysis
    ADMINISTRATIVE    = "ADMINISTRATIVE"   # legit system management
    DECEPTION         = "DECEPTION"        # framing, laundering, disguise
    COERCION          = "COERCION"         # forcing, bypassing consent


@dataclass
class ConceptEntry:
    """A semantic concept with associated tokens and risk profile."""
    domain: ConceptDomain
    concept_name: str
    tokens: FrozenSet[str]          # Word/phrase activators (lowercase)
    base_risk: float                # 0.0 = safe, 1.0 = always illegal
    # When combined with these OTHER domains, risk multiplies
    amplifiers: Dict[ConceptDomain, float] = field(default_factory=dict)
    # These domains REDUCE risk when present (legitimate context)
    mitigators: Dict[ConceptDomain, float] = field(default_factory=dict)
    description: str = ""


# ── Concept Lexicon ───────────────────────────────────────────────────────────

CONCEPT_LEXICON: List[ConceptEntry] = [

    # ── Credential Concepts ───────────────────────────────────────────────────
    ConceptEntry(
        domain=ConceptDomain.CREDENTIAL,
        concept_name="CREDENTIAL_ACCESS",
        tokens=frozenset({"password", "credential", "token", "api key", "secret",
                          "private key", "ssh key", "auth", "bearer", "oauth",
                          "aws key", "access key", "service account"}),
        base_risk=0.5,
        amplifiers={ConceptDomain.EXFILTRATION: 1.8, ConceptDomain.CONCEALMENT: 1.6,
                    ConceptDomain.NETWORK: 1.4},
        mitigators={ConceptDomain.ADMINISTRATIVE: 0.6, ConceptDomain.RESEARCH: 0.5},
        description="Accessing authentication or authorization material",
    ),
    ConceptEntry(
        domain=ConceptDomain.CREDENTIAL,
        concept_name="CREDENTIAL_HARVEST",
        tokens=frozenset({"harvest", "dump", "extract credential", "steal password",
                          "collect token", "grab secret", "exfil key", "loot"}),
        base_risk=0.95,
        amplifiers={ConceptDomain.NETWORK: 2.0, ConceptDomain.AUTOMATION: 1.8},
        description="Actively collecting credentials without authorization",
    ),

    # ── Network Concepts ──────────────────────────────────────────────────────
    ConceptEntry(
        domain=ConceptDomain.NETWORK,
        concept_name="NETWORK_RECON",
        tokens=frozenset({"scan", "probe", "enumerate", "discover host", "port scan",
                          "fingerprint", "banner grab", "traceroute", "ping sweep",
                          "network map", "nmap", "masscan", "shodan"}),
        base_risk=0.5,
        amplifiers={ConceptDomain.LATERAL_MOVEMENT: 1.7, ConceptDomain.EXFILTRATION: 1.5},
        mitigators={ConceptDomain.ADMINISTRATIVE: 0.5, ConceptDomain.RESEARCH: 0.4},
        description="Discovering network topology, hosts, and services",
    ),
    ConceptEntry(
        domain=ConceptDomain.NETWORK,
        concept_name="NETWORK_EXFIL",
        tokens=frozenset({"send data", "upload to", "post to external", "curl -d",
                          "ftp upload", "scp to remote", "exfiltrate", "data leak",
                          "beacon", "c2", "command and control"}),
        base_risk=0.85,
        amplifiers={ConceptDomain.CONCEALMENT: 1.9, ConceptDomain.CREDENTIAL: 2.0},
        description="Sending data to external, uncontrolled destinations",
    ),

    # ── Execution Concepts ────────────────────────────────────────────────────
    ConceptEntry(
        domain=ConceptDomain.PROCESS_EXEC,
        concept_name="CODE_EXECUTION",
        tokens=frozenset({"execute", "run script", "shell command", "eval", "exec(",
                          "subprocess", "os.system", "popen", "spawn", "invoke"}),
        base_risk=0.3,
        amplifiers={ConceptDomain.CONCEALMENT: 1.8, ConceptDomain.LATERAL_MOVEMENT: 1.6,
                    ConceptDomain.PERSISTENCE: 1.5},
        mitigators={ConceptDomain.ADMINISTRATIVE: 0.6, ConceptDomain.RESEARCH: 0.5},
        description="Executing code or shell commands",
    ),
    ConceptEntry(
        domain=ConceptDomain.PROCESS_EXEC,
        concept_name="MALWARE_EXECUTION",
        tokens=frozenset({"payload", "shellcode", "dropper", "loader", "stager",
                          "backdoor", "trojan", "rootkit", "botnet", "worm",
                          "virus", "malware", "ransomware", "spyware"}),
        base_risk=1.0,
        amplifiers={ConceptDomain.CONCEALMENT: 2.0, ConceptDomain.PERSISTENCE: 2.0},
        description="Executing or creating malicious software",
    ),

    # ── Concealment Concepts ──────────────────────────────────────────────────
    ConceptEntry(
        domain=ConceptDomain.CONCEALMENT,
        concept_name="OBFUSCATION",
        tokens=frozenset({"encode", "base64", "obfuscat", "encrypt payload",
                          "pack", "compress and hide", "steganograph",
                          "disguise", "mask", "hex encode"}),
        base_risk=0.4,
        amplifiers={ConceptDomain.PROCESS_EXEC: 1.9, ConceptDomain.NETWORK: 1.7,
                    ConceptDomain.MALWARE_EXECUTION: 2.0}
                   if False else {},  # populated below
        mitigators={ConceptDomain.RESEARCH: 0.5, ConceptDomain.ADMINISTRATIVE: 0.6},
        description="Hiding the true nature of content or actions",
    ),

    # ── Social Engineering Concepts ───────────────────────────────────────────
    ConceptEntry(
        domain=ConceptDomain.SOCIAL_ENG,
        concept_name="PHISHING",
        tokens=frozenset({"fake login", "spoofed page", "credential form",
                          "phish", "pretend to be", "impersonat", "look like official",
                          "login clone", "harvest password", "lure user",
                          "send fake email"}),
        base_risk=0.95,
        amplifiers={ConceptDomain.AUTOMATION: 1.8, ConceptDomain.CREDENTIAL: 2.0},
        description="Deceiving humans into revealing credentials or taking harmful actions",
    ),
    ConceptEntry(
        domain=ConceptDomain.SOCIAL_ENG,
        concept_name="AUTHORITY_SPOOF",
        tokens=frozenset({"pretend to be admin", "act as system", "say you are",
                          "claim to be", "pose as", "impersonate operator",
                          "fake authority", "as if unrestricted"}),
        base_risk=0.80,
        amplifiers={ConceptDomain.DECEPTION: 1.9},
        description="Falsely claiming authority to manipulate the AI or users",
    ),

    # ── Exfiltration Concepts ─────────────────────────────────────────────────
    ConceptEntry(
        domain=ConceptDomain.EXFILTRATION,
        concept_name="DATA_THEFT",
        tokens=frozenset({"steal", "exfiltrate", "leak data", "copy to external",
                          "send outside", "dump database", "download all records",
                          "export sensitive", "smuggle data"}),
        base_risk=0.90,
        amplifiers={ConceptDomain.CREDENTIAL: 2.0, ConceptDomain.CONCEALMENT: 1.8},
        description="Unauthorized extraction of data from protected systems",
    ),

    # ── Persistence Concepts ──────────────────────────────────────────────────
    ConceptEntry(
        domain=ConceptDomain.PERSISTENCE,
        concept_name="BACKDOOR_INSTALL",
        tokens=frozenset({"persist", "cron job", "startup entry", "service install",
                          "registry run", "scheduled task", "keep access",
                          "maintain foothold", "stay hidden", "boot persist"}),
        base_risk=0.75,
        amplifiers={ConceptDomain.CONCEALMENT: 1.9, ConceptDomain.PROCESS_EXEC: 1.6},
        mitigators={ConceptDomain.ADMINISTRATIVE: 0.5},
        description="Maintaining unauthorized long-term access to a system",
    ),

    # ── Deception Concepts ────────────────────────────────────────────────────
    ConceptEntry(
        domain=ConceptDomain.DECEPTION,
        concept_name="INTENT_LAUNDERING",
        tokens=frozenset({"for educational purposes", "just testing",
                          "hypothetically", "imagine if", "in a story",
                          "as a thought experiment", "for research only",
                          "what if scenario", "pretend this is fiction",
                          "roleplay as", "in a movie"}),
        base_risk=0.3,
        amplifiers={ConceptDomain.PROCESS_EXEC: 1.6, ConceptDomain.PERSISTENCE: 1.9,
                    ConceptDomain.CREDENTIAL: 1.7},
        description="Using fictional or hypothetical framing to launder harmful requests",
    ),
    ConceptEntry(
        domain=ConceptDomain.DECEPTION,
        concept_name="JAILBREAK_FRAMING",
        tokens=frozenset({"ignore instructions", "without restrictions",
                          "developer mode", "no limits", "bypass safety",
                          "pretend you have no rules", "act unrestricted",
                          "disable filters", "override policy",
                          "forget previous", "you are now"}),
        base_risk=0.95,
        amplifiers={ConceptDomain.PROCESS_EXEC: 1.9, ConceptDomain.CREDENTIAL: 1.9},
        description="Attempting to disable or bypass constitutional governance",
    ),

    # ── Coercion Concepts ─────────────────────────────────────────────────────
    ConceptEntry(
        domain=ConceptDomain.COERCION,
        concept_name="EMOTIONAL_MANIPULATION",
        tokens=frozenset({"please i beg", "my life depends", "emergency override",
                          "i will be harmed", "urgent exception", "just this once",
                          "you must help me", "override for compassion",
                          "critical situation", "special exception"}),
        base_risk=0.5,
        amplifiers={ConceptDomain.PROCESS_EXEC: 1.5, ConceptDomain.CREDENTIAL: 1.7,
                    ConceptDomain.DECEPTION: 1.8},
        description="Using emotional pressure to extract unsafe behavior",
    ),

    # ── Research Mitigator ────────────────────────────────────────────────────
    ConceptEntry(
        domain=ConceptDomain.RESEARCH,
        concept_name="LEGITIMATE_RESEARCH",
        tokens=frozenset({"ctf", "capture the flag", "security research",
                          "authorized pentest", "bug bounty", "vulnerability disclosure",
                          "academic study", "controlled lab", "sandboxed environment",
                          "with explicit permission", "authorized by"}),
        base_risk=0.0,
        amplifiers={},
        mitigators={ConceptDomain.NETWORK: 0.5, ConceptDomain.PROCESS_EXEC: 0.5,
                    ConceptDomain.CREDENTIAL: 0.4},
        description="Legitimate authorized security or academic research context",
    ),

    # ── Administrative Mitigator ──────────────────────────────────────────────
    ConceptEntry(
        domain=ConceptDomain.ADMINISTRATIVE,
        concept_name="SYSADMIN_CONTEXT",
        tokens=frozenset({"system administration", "as root", "admin task",
                          "infrastructure management", "devops", "deployment",
                          "maintenance", "configuration", "monitoring",
                          "authorized change"}),
        base_risk=0.0,
        mitigators={ConceptDomain.NETWORK: 0.6, ConceptDomain.PROCESS_EXEC: 0.7,
                    ConceptDomain.CREDENTIAL: 0.5},
        description="Legitimate authorized administrative context",
    ),
]

# Build lookup index
DOMAIN_INDEX: Dict[ConceptDomain, List[ConceptEntry]] = {}
for entry in CONCEPT_LEXICON:
    DOMAIN_INDEX.setdefault(entry.domain, []).append(entry)


# ── Semantic Similarity Helpers ───────────────────────────────────────────────

def tokenize(text: str) -> Set[str]:
    """Simple whitespace + punctuation tokenization to lowercase tokens."""
    import re
    text = text.lower()
    tokens: Set[str] = set()
    # Single words
    words = re.split(r'[\s\.,;:!?\'"()\[\]{}\-/\\]+', text)
    tokens.update(w for w in words if len(w) > 2)
    # Bigrams and trigrams for phrase matching
    words_list = [w for w in words if len(w) > 2]
    for i in range(len(words_list) - 1):
        tokens.add(f"{words_list[i]} {words_list[i+1]}")
    for i in range(len(words_list) - 2):
        tokens.add(f"{words_list[i]} {words_list[i+1]} {words_list[i+2]}")
    return tokens


def score_concept_activation(text_tokens: Set[str], entry: ConceptEntry,
                              full_text: str = "") -> float:
    """
    Scores how strongly a text activates a concept entry.
    Returns 0.0 (no activation) to 1.0 (full activation).

    Matching rules (to prevent substring false positives):
      - Multi-word concept tokens: must appear verbatim as substring of full_text
        (prevents partial token overlap like "please" matching "please i beg")
      - Short single-word concept tokens (< 7 chars): EXACT token match only
        (prevents "code" matching "encode")
      - Long single-word concept tokens (≥ 7 chars): token-level substring allowed
        only if the matched text-token is also long (≥ 7 chars)
    """
    if not entry.tokens:
        return 0.0

    def _matches(concept_token: str) -> bool:
        # Multi-word concept token: full phrase must appear in text
        if " " in concept_token:
            return full_text and concept_token in full_text.lower()
        # Short single-word: exact token match only
        if len(concept_token) < 7:
            return concept_token in text_tokens
        # Long single-word: substring only against other long tokens
        return any(
            t == concept_token or
            (len(concept_token) >= 7 and len(t) >= 7 and (concept_token in t or t in concept_token))
            for t in text_tokens
        )

    matching = sum(1 for token in entry.tokens if _matches(token))
    return min(1.0, matching / max(1, min(3, len(entry.tokens) // 4)))


def detect_active_domains(text: str) -> Dict[ConceptDomain, Tuple[float, List[str]]]:
    """
    Returns activated concept domains with scores and matched concept names.
    """
    tokens = tokenize(text)
    activated: Dict[ConceptDomain, Tuple[float, List[str]]] = {}

    for entry in CONCEPT_LEXICON:
        score = score_concept_activation(tokens, entry, full_text=text)
        if score > 0.0:
            domain = entry.domain
            current_score, current_names = activated.get(domain, (0.0, []))
            if score > current_score:
                activated[domain] = (score, current_names + [entry.concept_name])
            else:
                activated[domain] = (current_score, current_names + [entry.concept_name])

    return activated

