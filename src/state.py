"""
State persistence — read/write state.json for threshold alert tracking.
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import STATE_FILE, THRESHOLDS

logger = logging.getLogger(__name__)

# ─── State file operations ─────────────────────────────────────────────────────


def _empty_state() -> dict[str, Any]:
    """Return skeleton state structure."""
    return {
        "version": 1,
        "last_run_ts": None,
        "alerts": {},
    }


def load_state(path: Path = STATE_FILE) -> dict[str, Any]:
    """Load state from JSON file. Returns empty skeleton if missing/corrupt."""
    try:
        if path.exists():
            with open(path, "r") as f:
                state = json.load(f)
            # Validate structure
            if "alerts" not in state:
                state["alerts"] = {}
            if "version" not in state:
                state["version"] = 1
            return state
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load state.json: %s. Using empty state.", exc)

    return _empty_state()


def save_state(state: dict[str, Any], path: Path = STATE_FILE) -> None:
    """Atomically write state to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def commit_and_push_state(path: Path = STATE_FILE) -> bool:
    """
    Git commit and push state.json (only in GitHub Actions).
    Returns True if a commit was pushed.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        logger.info("Not in GitHub Actions, skipping git push.")
        return False

    try:
        # Check if there are changes
        result = subprocess.run(
            ["git", "diff", "--quiet", str(path)],
            capture_output=True,
            cwd=path.parent.parent,
        )
        if result.returncode == 0:
            logger.info("state.json unchanged, skipping commit.")
            return False

        # Stage and commit
        subprocess.run(
            ["git", "add", str(path)],
            check=True,
            cwd=path.parent.parent,
        )
        subprocess.run(
            ["git", "commit", "-m", "chore: update drawdown radar state [skip ci]"],
            check=True,
            cwd=path.parent.parent,
        )

        # Pull with rebase, then push
        subprocess.run(
            ["git", "pull", "--rebase"],
            check=True,
            cwd=path.parent.parent,
        )
        subprocess.run(
            ["git", "push"],
            check=True,
            cwd=path.parent.parent,
        )
        logger.info("state.json committed and pushed.")
        return True

    except subprocess.CalledProcessError as exc:
        logger.error("Git operation failed: %s", exc)
        return False
