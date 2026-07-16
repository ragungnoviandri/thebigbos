"""de BigBos - AI-powered CLI assistant with soul, memory, skills, and multi-model support."""

import subprocess
from pathlib import Path

__version__ = "0.1.1"
__repo__ = "https://github.com/ragungnoviandri/deBigBos"


def get_build_number() -> int:
    """Return git commit count as build number. Falls back to 0."""
    try:
        repo = Path(__file__).resolve().parent.parent  # workspace root
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return 0


def get_version_string() -> str:
    """Return full version string: v{major}.{minor}.{patch}.{build}"""
    build = get_build_number()
    if build:
        return f"v{__version__}.{build}"
    return f"v{__version__}"
