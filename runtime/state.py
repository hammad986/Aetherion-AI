import threading
from collections import deque

# ────────────────────────────────────────────────────────────────────────────
# Core Orchestration & Lifecycle State
# ────────────────────────────────────────────────────────────────────────────

queue_lock     = threading.Lock()
pending_queue  = deque()             # session ids waiting
running        = {"sid": None, "proc": None, "seq": 0}
managed_runs   = deque()             # timestamps of recent managed-mode runs

# ────────────────────────────────────────────────────────────────────────────
# Domain-Specific Locks & Registries
# ────────────────────────────────────────────────────────────────────────────

_db_lock = threading.Lock()
_BROWSER_LOCK = threading.Lock()
_REVIEW_LOCK = threading.Lock()
_p13_summarize_lock = threading.Lock()
_p13_in_progress = set()  # track sessions currently being summarized
_chain_lock   = threading.Lock()
_editor_llm_lock   = threading.Lock()
_ROUTING_LOCK = threading.Lock()
_TERMINAL_MODE_LOCK = threading.Lock()
_terminal_lock = threading.Lock()
_hitl_state: dict[str, dict] = {}  # {sid: {paused, inject_queue}}
_hitl_lock = threading.Lock()
_STEP_STORE = {}
_STEP_LOCK  = threading.Lock()
workflow_queues = {}
_oauth_states: dict = {}
ext_counts = {}
_P7_PIPELINES: dict = {}
_p7_lock = threading.Lock()

