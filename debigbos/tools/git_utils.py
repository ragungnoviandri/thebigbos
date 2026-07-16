"""Git utilities — status, commit, push for workspace repo."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitWorkspace:
    """Lightweight git wrapper for the user's workspace."""

    def __init__(self, workspace: str | Path) -> None:
        self.root = self._find_root(Path(workspace))

    @staticmethod
    def _find_root(path: Path) -> Path | None:
        """Find the git root directory from any path."""
        p = path.resolve()
        while True:
            if (p / ".git").exists():
                return p
            parent = p.parent
            if parent == p:
                return None
            p = parent

    @property
    def is_repo(self) -> bool:
        return self.root is not None

    def _run(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        """Run a git command, return CompletedProcess."""
        cmd = ["git", "-C", str(self.root), *args]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def status_summary(self) -> str:
        """Get short status output."""
        if not self.is_repo:
            return "not a git repo"
        r = self._run("status", "--porcelain")
        if r.returncode != 0:
            return f"git error: {r.stderr.strip()}"
        lines = r.stdout.strip().splitlines()
        if not lines or lines == [""]:
            return "clean"
        added = sum(1 for l in lines if l[0] in "AMR")
        modified = sum(1 for l in lines if l[1] == "M" or l[0] == "M")
        untracked = sum(1 for l in lines if l.startswith("??"))
        return f"{len(lines)} files ({added} staged, {modified} mod, {untracked} new)"

    def status_porcelain(self) -> list[str]:
        """Return list of porcelain status lines."""
        if not self.is_repo:
            return []
        r = self._run("status", "--porcelain")
        return r.stdout.strip().splitlines() if r.stdout.strip() else []

    def has_remote(self, remote: str = "origin") -> bool:
        """Check if a remote is configured."""
        if not self.is_repo:
            return False
        r = self._run("remote", "get-url", remote)
        return r.returncode == 0 and bool(r.stdout.strip())

    def get_remote_url(self, remote: str = "origin") -> str:
        """Get remote URL, empty string if none."""
        if not self.is_repo:
            return ""
        r = self._run("remote", "get-url", remote)
        return r.stdout.strip() if r.returncode == 0 else ""

    def set_remote(self, url: str, remote: str = "origin") -> tuple[bool, str]:
        """Add or set a remote URL."""
        if not self.is_repo:
            return False, "Not a git repository"
        if self.has_remote(remote):
            r = self._run("remote", "set-url", remote, url)
        else:
            r = self._run("remote", "add", remote, url)
        if r.returncode != 0:
            return False, r.stderr.strip()
        return True, f"Remote '{remote}' set to {url}"

    def has_changes(self) -> bool:
        """Check if there are any uncommitted changes."""
        return len(self.status_porcelain()) > 0

    def stage_all(self) -> tuple[bool, str]:
        """Git add --all. Returns (ok, message)."""
        if not self.is_repo:
            return False, "Not a git repository"
        r = self._run("add", "--all")
        if r.returncode != 0:
            return False, r.stderr.strip()
        return True, "Staged all changes"

    def commit(self, message: str) -> tuple[bool, str]:
        """Commit staged changes. Returns (ok, message)."""
        if not self.is_repo:
            return False, "Not a git repository"
        r = self._run("commit", "-m", message)
        if r.returncode != 0:
            return False, r.stderr.strip()
        return True, r.stdout.strip()

    def push(self, remote: str = "origin", branch: str | None = None) -> tuple[bool, str]:
        """Push to remote. Returns (ok, message)."""
        if not self.is_repo:
            return False, "Not a git repository"
        if branch is None:
            branch = self.current_branch()
        r = self._run("push", remote, branch, timeout=60)
        if r.returncode != 0:
            return False, r.stderr.strip() or r.stdout.strip()
        return True, r.stdout.strip() or "Pushed!"

    def diff_staged(self) -> str:
        """Get git diff of staged changes."""
        if not self.is_repo:
            return ""
        r = self._run("diff", "--staged")
        return r.stdout.strip() if r.returncode == 0 else ""

    def diff_unstaged(self) -> str:
        """Get git diff of unstaged changes."""
        if not self.is_repo:
            return ""
        r = self._run("diff")
        return r.stdout.strip() if r.returncode == 0 else ""

    def diff_all(self) -> str:
        """Get full diff (staged + unstaged) truncated to ~3KB for AI summarization."""
        if not self.is_repo:
            return ""
        staged = self.diff_staged()
        unstaged = self.diff_unstaged()
        combined = f"{'='*40}\nSTAGED:\n{'='*40}\n{staged}\n\n{'='*40}\nUNSTAGED:\n{'='*40}\n{unstaged}"
        if len(combined) > 4000:
            combined = combined[:4000] + "\n... [truncated]"
        return combined

    def diff_summary(self) -> str:
        """Return a compact summary for AI commit message generation.
        
        Includes: branch name, file list with status, followed by truncated diff.
        """
        if not self.is_repo:
            return ""
        parts = []
        branch = self.current_branch()
        parts.append(f"branch: {branch}")

        # File list with status codes
        porcelain = self.status_porcelain()
        if porcelain:
            parts.append("files:")
            for line in porcelain[:30]:
                parts.append(f"  {line}")
            if len(porcelain) > 30:
                parts.append(f"  ... and {len(porcelain) - 30} more files")

        # Diff content (smart truncate: keep first 2500 chars)
        staged = self.diff_staged()
        unstaged = self.diff_unstaged()
        diff = ""
        if staged:
            diff += f"--STAGED--\n{staged}\n"
        if unstaged:
            diff += f"--UNSTAGED--\n{unstaged}"
        if diff:
            if len(diff) > 2500:
                diff = diff[:2500] + "\n... [truncated]"
            parts.append(f"diff:\n{diff}")

        return "\n".join(parts)

    def current_branch(self) -> str:
        """Get current branch name."""
        if not self.is_repo:
            return "."
        r = self._run("rev-parse", "--abbrev-ref", "HEAD")
        return r.stdout.strip() if r.returncode == 0 else "main"

    def auto_commit_push(self, message: str) -> tuple[bool, str]:
        """Stage all, commit, and push. One-shot."""
        if not self.is_repo:
            return False, "Not a git repository"
        if not self.has_changes():
            return True, "Nothing to commit"

        ok, msg = self.stage_all()
        if not ok:
            return False, f"Stage failed: {msg}"

        ok, msg = self.commit(message)
        if not ok:
            return False, f"Commit failed: {msg}"

        ok, msg = self.push()
        if not ok:
            return False, f"Push failed: {msg}"

        return True, "Committed & pushed ✓"
