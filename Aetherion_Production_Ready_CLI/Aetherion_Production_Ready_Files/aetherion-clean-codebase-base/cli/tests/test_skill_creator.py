"""
tests/test_skill_creator.py
Comprehensive tests for the Aetherion workflow-skill-creator.

Covers:
  - slug sanitisation
  - trigger extraction
  - message summarisation
  - SKILL.md content correctness
  - file system write with temp dir
  - graceful failure on invalid name
  - thread safety (multiple concurrent creates)
  - list_local_skills round-trip
  - CLI create command (Click test runner)
"""
from __future__ import annotations

import importlib
import importlib.util
import py_compile
import re
import sys
import threading
import time
from pathlib import Path

import pytest


# ── Loaders ──────────────────────────────────────────────────────────────────

def _load_module(rel: str, name: str):
    base = Path(__file__).parent.parent / "aetherion_cli" / "tui"
    path = base / rel
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def sc():
    """skill_creator module."""
    return _load_module("skill_creator.py", "aetherion_cli.tui.skill_creator")


@pytest.fixture(scope="session")
def state_mod():
    return _load_module("state.py", "aetherion_cli.tui.state")


# ── Slug sanitisation ─────────────────────────────────────────────────────────

def test_slugify_basic(sc):
    assert sc._slugify("Git Workflow") == "git-workflow"


def test_slugify_special_chars(sc):
    s = sc._slugify("My  Skill!! 2024")
    assert re.fullmatch(r"[a-z0-9\-]+", s)


def test_slugify_empty(sc):
    assert sc._slugify("") == ""


def test_slugify_max_length(sc):
    long = "a" * 200
    assert len(sc._slugify(long)) <= 48


def test_slugify_unicode(sc):
    # Non-ASCII should be stripped, not crash
    result = sc._slugify("code-révision")
    assert isinstance(result, str)


def test_slugify_hyphens_collapsed(sc):
    assert sc._slugify("a - b - c") == "a-b-c"


# ── Trigger extraction ────────────────────────────────────────────────────────

def test_extract_triggers_includes_name(sc):
    triggers = sc._extract_triggers("Automate git push", "git-workflow")
    assert "git workflow" in triggers


def test_extract_triggers_max_6(sc):
    desc = "long word " * 20
    triggers = sc._extract_triggers(desc, "test-skill")
    assert len(triggers) <= 6


def test_extract_triggers_deduped(sc):
    triggers = sc._extract_triggers("test test test", "test-skill")
    assert len(triggers) == len(set(triggers))


def test_extract_triggers_no_stopwords(sc):
    """Common stop words should not appear in triggers."""
    stopwords = {"with", "from", "that", "this", "into", "when", "your", "using"}
    triggers = sc._extract_triggers(
        "Using this tool with your workflow", "my-skill"
    )
    for t in triggers:
        assert t not in stopwords


# ── Message summarisation ─────────────────────────────────────────────────────

def test_summarize_empty(sc):
    summary, examples = sc._summarize_messages([])
    assert "No conversation" in summary
    assert examples == []


def test_summarize_user_messages(sc, state_mod):
    msgs = [
        state_mod.Message.user("How do I commit to git?"),
        state_mod.Message.assistant("Run: git commit -m 'message'"),
        state_mod.Message.user("And push?"),
        state_mod.Message.assistant("Run: git push origin main"),
    ]
    summary, examples = sc._summarize_messages(msgs)
    assert "How do I commit" in summary
    assert len(examples) >= 1


def test_summarize_max_20_steps(sc, state_mod):
    msgs = [state_mod.Message.user(f"Step {i}") for i in range(30)]
    summary, _ = sc._summarize_messages(msgs)
    lines = [l for l in summary.splitlines() if l.strip()]
    assert len(lines) <= 20


def test_summarize_examples_capped(sc, state_mod):
    msgs = [state_mod.Message.user(f"Question {i}") for i in range(20)]
    _, examples = sc._summarize_messages(msgs)
    assert len(examples) <= 5


def test_summarize_skips_empty_content(sc, state_mod):
    msgs = [
        state_mod.Message.user(""),
        state_mod.Message.assistant(""),
        state_mod.Message.user("Real message"),
    ]
    summary, _ = sc._summarize_messages(msgs)
    assert "Real message" in summary


# ── SKILL.md content ──────────────────────────────────────────────────────────

def test_build_skill_md_has_frontmatter(sc):
    md = sc._build_skill_md(
        name="git-workflow",
        description="Automate git tasks",
        triggers=["git workflow", "commit"],
        conversation_summary="1. User: commit\n   → Done",
        examples=["how to commit?"],
    )
    assert md.startswith("---")
    assert "name: git-workflow" in md
    assert "description: Automate git tasks" in md


def test_build_skill_md_has_triggers(sc):
    md = sc._build_skill_md(
        name="n", description="d",
        triggers=["foo", "bar"],
        conversation_summary="steps",
        examples=[],
    )
    assert "- foo" in md
    assert "- bar" in md


def test_build_skill_md_has_timestamp(sc):
    md = sc._build_skill_md("n", "d", [], "s", [])
    assert "Created at:" in md


def test_build_skill_md_has_workflow_section(sc):
    md = sc._build_skill_md("n", "d", [], "step 1\nstep 2", [])
    assert "Workflow steps" in md
    assert "step 1" in md


# ── File system write ─────────────────────────────────────────────────────────

def test_do_create_writes_skill_md(sc, tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "SKILLS_DIR", tmp_path)
    result = sc._do_create("my-skill", "A test skill", messages=[])
    assert result.startswith("✓")
    skill_file = tmp_path / "my-skill" / "SKILL.md"
    assert skill_file.exists()
    content = skill_file.read_text()
    assert "name: my-skill" in content


def test_do_create_invalid_name(sc, tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "SKILLS_DIR", tmp_path)
    result = sc._do_create("", "description", messages=[])
    assert result.startswith("✗")


def test_do_create_special_name(sc, tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "SKILLS_DIR", tmp_path)
    result = sc._do_create("My Skill!!!", "desc", messages=[])
    assert result.startswith("✓")
    # slug should be my-skill
    created = list(tmp_path.iterdir())
    assert len(created) == 1
    assert created[0].name == "my-skill"


def test_do_create_with_messages(sc, tmp_path, monkeypatch, state_mod):
    monkeypatch.setattr(sc, "SKILLS_DIR", tmp_path)
    msgs = [
        state_mod.Message.user("How do I use git?"),
        state_mod.Message.assistant("Use git commit -m 'msg'"),
    ]
    result = sc._do_create("git-help", "Git helper", messages=msgs)
    assert result.startswith("✓")
    content = (tmp_path / "git-help" / "SKILL.md").read_text()
    assert "How do I use git" in content


def test_do_create_idempotent(sc, tmp_path, monkeypatch):
    """Creating same skill twice updates the file, doesn't crash."""
    monkeypatch.setattr(sc, "SKILLS_DIR", tmp_path)
    sc._do_create("dup-skill", "v1", [])
    result = sc._do_create("dup-skill", "v2", [])
    assert result.startswith("✓")
    content = (tmp_path / "dup-skill" / "SKILL.md").read_text()
    assert "v2" in content


# ── list_local_skills ─────────────────────────────────────────────────────────

def test_list_local_empty(sc, tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "SKILLS_DIR", tmp_path)
    assert sc.list_local_skills() == []


def test_list_local_returns_created(sc, tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "SKILLS_DIR", tmp_path)
    sc._do_create("skill-a", "First skill", [])
    sc._do_create("skill-b", "Second skill", [])
    items = sc.list_local_skills()
    assert len(items) == 2
    names = [i["name"] for i in items]
    # At least the slugs should appear
    assert any("skill-a" in n or "skill" in n for n in names)


def test_list_local_parses_description(sc, tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "SKILLS_DIR", tmp_path)
    sc._do_create("parse-test", "My description here", [])
    items = sc.list_local_skills()
    assert any("My description here" in i["description"] for i in items)


def test_list_local_missing_dir_bootstraps_bundled_skills(sc, tmp_path, monkeypatch):
    """A fresh install copies bundled skills so they remain visible offline."""
    absent = tmp_path / "nonexistent"
    monkeypatch.setattr(sc, "SKILLS_DIR", absent)
    items = sc.list_local_skills()
    assert items
    assert all(Path(item["path"]).is_file() for item in items)


def test_packaged_skill_python_assets_compile():
    """Every Python asset included in the CLI wheel must be executable syntax."""
    skills_root = Path(__file__).parent.parent / "aetherion_cli" / "skills"
    for script in skills_root.rglob("*.py"):
        py_compile.compile(str(script), doraise=True)


def test_list_local_ignores_non_skill_dirs(sc, tmp_path, monkeypatch):
    """Directories without SKILL.md are skipped."""
    monkeypatch.setattr(sc, "SKILLS_DIR", tmp_path)
    (tmp_path / "stale-dir").mkdir()
    items = sc.list_local_skills()
    assert items == []


def test_list_local_returns_path(sc, tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "SKILLS_DIR", tmp_path)
    sc._do_create("pathed", "With a path", [])
    items = sc.list_local_skills()
    assert any("SKILL.md" in i["path"] for i in items)


# ── create_skill_interactive ──────────────────────────────────────────────────

def test_create_skill_interactive(sc, tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "SKILLS_DIR", tmp_path)
    result = sc.create_skill_interactive("interactive-skill", "CLI test")
    assert result.startswith("✓")


# ── Thread safety ─────────────────────────────────────────────────────────────

def test_concurrent_create(sc, tmp_path, monkeypatch):
    """Multiple threads creating different skills simultaneously."""
    monkeypatch.setattr(sc, "SKILLS_DIR", tmp_path)
    N = 10
    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(N)

    def worker(i):
        barrier.wait()
        r = sc._do_create(f"concurrent-{i}", f"Concurrent skill {i}", [])
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == N
    assert all(r.startswith("✓") for r in results)
    assert len(list(tmp_path.iterdir())) == N


def test_concurrent_same_skill(sc, tmp_path, monkeypatch):
    """Multiple threads creating the same skill — last one wins, no crash."""
    monkeypatch.setattr(sc, "SKILLS_DIR", tmp_path)
    N = 5
    errors = []
    barrier = threading.Barrier(N)

    def worker(i):
        barrier.wait()
        try:
            sc._do_create("shared-skill", f"Version {i}", [])
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors   # No exceptions
    assert (tmp_path / "shared-skill" / "SKILL.md").exists()


# ── CLI integration ───────────────────────────────────────────────────────────

def test_cli_skills_local_empty(tmp_path, monkeypatch):
    """aetherion skills local with no skills prints a helpful message."""
    from click.testing import CliRunner
    from aetherion_cli.tui import skill_creator as sc_mod
    monkeypatch.setattr(sc_mod, "SKILLS_DIR", tmp_path)

    from aetherion_cli.main import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["skills", "local"], obj={})
    assert result.exit_code == 0
    assert "No local skills" in result.output


def test_cli_skills_create(tmp_path, monkeypatch):
    """aetherion skills create creates a SKILL.md."""
    from click.testing import CliRunner
    from aetherion_cli.tui import skill_creator as sc_mod
    monkeypatch.setattr(sc_mod, "SKILLS_DIR", tmp_path)

    from aetherion_cli.main import cli
    runner = CliRunner()
    result = runner.invoke(cli, [
        "skills", "create",
        "--name", "test-cli-skill",
        "--description", "Test description from CLI"
    ], obj={})
    assert result.exit_code == 0
    assert "✓" in result.output or "test-cli-skill" in result.output
    assert (tmp_path / "test-cli-skill" / "SKILL.md").exists()


def test_cli_skills_local_lists(tmp_path, monkeypatch):
    """After creating, aetherion skills local shows the skill."""
    from click.testing import CliRunner
    from aetherion_cli.tui import skill_creator as sc_mod
    monkeypatch.setattr(sc_mod, "SKILLS_DIR", tmp_path)

    sc_mod._do_create("listed-skill", "Show in list", [])

    from aetherion_cli.main import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["skills", "local"], obj={})
    assert result.exit_code == 0
    assert "listed-skill" in result.output or "Show in list" in result.output


def test_cli_skills_local_json(tmp_path, monkeypatch):
    """--output json mode emits valid JSON."""
    import json
    from click.testing import CliRunner
    from aetherion_cli.tui import skill_creator as sc_mod
    monkeypatch.setattr(sc_mod, "SKILLS_DIR", tmp_path)
    sc_mod._do_create("json-skill", "JSON test", [])

    from aetherion_cli.main import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["-o", "json", "skills", "local"], obj={"output": "json"})
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
