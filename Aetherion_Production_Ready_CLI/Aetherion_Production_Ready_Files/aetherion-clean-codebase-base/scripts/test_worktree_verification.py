import tempfile
import subprocess
import os
import json
import time
from pathlib import Path

def main():
    print("Starting Real Git Worktree Verification...")
    with tempfile.TemporaryDirectory() as tmp_git_dir:
        repo = Path(tmp_git_dir).resolve()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
        
        # 1. Create baseline file and commit
        f1 = repo / "hello.py"
        f1.write_text("print('baseline content')\n")
        subprocess.run(["git", "add", "hello.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo, check=True)
        
        # 2. Use D3Store
        from db.d3_store import D3Store
        store = D3Store()
        store.get_repo_root = lambda: str(repo)
        
        tenant = "audit_tenant_wt"
        wt = store.create_real_worktree(
            tenant_id=tenant,
            session_id="sess_test_wt",
            execution_id="exec_test_wt",
            base_ref="HEAD",
        )
        wt_id = wt["worktree_id"]
        wt_path = Path(wt["path"])
        print(f"Created real worktree: ID={wt_id}, Path={wt_path}")
        
        # 3. Verify actual directory exists on filesystem
        assert wt_path.exists() and wt_path.is_dir(), f"Worktree path does not exist: {wt_path}"
        print("Worktree directory exists: VERIFIED")
        
        # 4. Verify .git worktree metadata (in a worktree, .git is a file pointing to gitdir)
        git_meta = wt_path / ".git"
        assert git_meta.exists() and git_meta.is_file(), f".git file missing in worktree: {git_meta}"
        git_content = git_meta.read_text()
        print(f"Worktree .git metadata: {git_content.strip()}")
        assert "gitdir:" in git_content, "Missing gitdir pointer in .git worktree file"
        
        # 5. Modify a file inside worktree
        wt_file = wt_path / "hello.py"
        wt_file.write_text("print('modified inside isolated worktree!')\n")
        
        # 6. Verify main repository remains unchanged
        main_content = f1.read_text()
        assert main_content == "print('baseline content')\n", f"Main repo mutated! {main_content}"
        print("Main repo isolation: VERIFIED (main repo untouched)")
        
        # 7. Inspect Aetherion diff
        diff_res = store.get_worktree_diff(tenant, wt_id)
        print(f"Diff lines changed: {diff_res['lines_changed']}, files changed: {diff_res['files_changed']}")
        assert "+print('modified inside isolated worktree!')" in diff_res["diff"]
        print("Aetherion diff inspection: VERIFIED")
        
        # 8. Discard/cleanup worktree
        discard_ok = store.discard_worktree(tenant, wt_id)
        assert discard_ok, "Discard failed"
        
        # 9. Verify physical cleanup
        time.sleep(0.5)
        assert not wt_path.exists(), f"Worktree path still exists on disk after discard: {wt_path}"
        print("Physical cleanup verification: VERIFIED")

    print("*** REAL GIT WORKTREE ISOLATION WORKFLOW FULLY CERTIFIED ***")

if __name__ == "__main__":
    main()
