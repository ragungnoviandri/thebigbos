"""Auto-updater — checks GitHub for new versions on startup."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional


REPO_OWNER = "ragungnoviandri"
REPO_NAME = "deBigBos"
CHECK_INTERVAL = 86400  # 24 hours between checks


class Updater:
    """Handles version checking and git-pull updates."""

    def __init__(self):
        self.repo_path = self._find_repo()
        self.cache_file = Path.home() / ".config" / "deBigBos" / ".update_cache"

    def _find_repo(self) -> Optional[Path]:
        """Find the git repository path."""
        candidates = [
            Path(os.environ.get("deBigBos_HOME", "")) / "repo",
            Path.home() / ".local" / "share" / "deBigBos" / "repo",
        ]
        for c in candidates:
            if (c / ".git").exists():
                return c
        return None

    def should_check(self) -> bool:
        """Check if enough time has passed since last check."""
        if not self.cache_file.exists():
            return True
        try:
            data = json.loads(self.cache_file.read_text())
            last_check = data.get("last_check", 0)
            return (time.time() - last_check) > CHECK_INTERVAL
        except Exception:
            return True

    def get_local_version(self) -> str:
        """Get local version from __init__.py."""
        try:
            if self.repo_path:
                init_file = self.repo_path / "deBigBos" / "__init__.py"
                if init_file.exists():
                    content = init_file.read_text()
                    for line in content.split("\n"):
                        if line.startswith("__version__"):
                            return line.split("=")[1].strip().strip('"').strip("'")
        except Exception:
            pass
        return "0.0.0"

    def get_remote_version(self) -> Optional[str]:
        """Get latest version from GitHub API."""
        try:
            import urllib.request
            url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "de BigBos-Updater")
            req.add_header("Accept", "application/vnd.github.v3+json")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data.get("tag_name", "").lstrip("v")
        except Exception:
            return None

    def version_newer(self, remote: str, local: str) -> bool:
        """Compare semantic versions."""
        def parse(v):
            try:
                return tuple(int(x) for x in v.split("."))
            except Exception:
                return (0, 0, 0)
        return parse(remote) > parse(local)

    def check(self, force: bool = False) -> Optional[str]:
        """Check for updates. Returns new version string or None."""
        if not self.repo_path:
            return None
        if not force and not self.should_check():
            return None

        # Save check time first
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(json.dumps({"last_check": time.time()}))

        # Try GitHub release first
        remote = self.get_remote_version()
        local = self.get_local_version()
        if remote and self.version_newer(remote, local):
            return remote

        # Fallback: check git commits (for dev updates without releases)
        return self.check_git_updates()

    def check_git_updates(self) -> Optional[str]:
        """Check if there are new commits to pull."""
        if not self.repo_path:
            return None
        try:
            # Fetch without merging
            subprocess.run(
                ["git", "-C", str(self.repo_path), "fetch", "origin"],
                capture_output=True, text=True, timeout=15
            )
            # Check if behind
            result = subprocess.run(
                ["git", "-C", str(self.repo_path), "rev-list", "--count", "HEAD..origin/main"],
                capture_output=True, text=True, timeout=10
            )
            behind = int(result.stdout.strip() or "0")
            if behind > 0:
                return f"{behind} new commit(s)"
        except Exception:
            pass
        return None

    def update(self, show_output: bool = True) -> bool:
        """Pull latest from git and reinstall deps. Returns True if updated."""
        if not self.repo_path:
            return False
        try:
            # Record pre-pull commit for dependency check
            pyproject_before = (self.repo_path / "pyproject.toml").read_text() if (self.repo_path / "pyproject.toml").exists() else ""

            # Show what's changed
            if show_output:
                subprocess.run(["git", "-C", str(self.repo_path), "fetch", "origin"], timeout=15)
                subprocess.run(["git", "-C", str(self.repo_path), "log", "HEAD..origin/main", "--oneline"], timeout=10)

            result = subprocess.run(
                ["git", "-C", str(self.repo_path), "pull", "origin", "main"],
                capture_output=not show_output, text=True, timeout=30
            )
            if not show_output and "Already up to date" in (result.stdout or ""):
                return False

            # Only reinstall deps if pyproject.toml changed
            pyproject_after = (self.repo_path / "pyproject.toml").read_text() if (self.repo_path / "pyproject.toml").exists() else ""
            if pyproject_before != pyproject_after:
                if show_output:
                    print("\n  Dependencies changed. Reinstalling...")
                venv = self.repo_path.parent / "venv"
                pip = venv / "bin" / "pip" if os.name != "nt" else venv / "Scripts" / "pip.exe"
                if pip.exists():
                    subprocess.run([str(pip), "install", "-e", str(self.repo_path), "--quiet"], timeout=60)
                if show_output:
                    print("  Done! Restart de BigBos to apply deps changes.")
            else:
                if show_output:
                    print("\n  Code-only update. Already applied (editable install).")

            # Sync skills to global config
            self._sync_skills(show_output)

            return True
        except Exception:
            return False

    def _sync_skills(self, show_output: bool = True) -> None:
        """Copy bundled skills from repo to global config directory."""
        repo_skills = self.repo_path / ".debigbos" / "skills"
        if not repo_skills.exists():
            return

        config_skills = Path.home() / ".config" / "deBigBos" / "skills"
        config_skills.mkdir(parents=True, exist_ok=True)

        new_count = 0
        updated_count = 0
        for skill_dir in repo_skills.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_name = skill_dir.name
            dest_dir = config_skills / skill_name
            if not dest_dir.exists():
                shutil.copytree(skill_dir, dest_dir)
                new_count += 1
            else:
                # Compare mtime of SKILL.md — copy if source is newer
                src_skill = skill_dir / "SKILL.md"
                dst_skill = dest_dir / "SKILL.md"
                if src_skill.exists() and dst_skill.exists():
                    if src_skill.stat().st_mtime > dst_skill.stat().st_mtime:
                        # Remove old & recopy
                        shutil.rmtree(dest_dir)
                        shutil.copytree(skill_dir, dest_dir)
                        updated_count += 1
                elif src_skill.exists() and not dst_skill.exists():
                    shutil.copytree(skill_dir, dest_dir)
                    updated_count += 1

        if show_output and (new_count or updated_count):
            print(f"\n  Skills synced: +{new_count} new, ~{updated_count} updated -> {config_skills}")
        elif show_output:
            print(f"\n  Skills: up to date ({config_skills})")

    def get_current_git_ref(self) -> str:
        """Get current git commit short hash."""
        if not self.repo_path:
            return "unknown"
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repo_path), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip()
        except Exception:
            return "unknown"
