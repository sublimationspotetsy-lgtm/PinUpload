"""
core/github_utils.py

Phase 2 — read-only git state detection + public URL verification.

The app deliberately NEVER pushes or mutates the git working tree. It only:
  - inspects repo/remote/branch state, and
  - verifies public image URLs over HTTPS (HEAD, with GET fallback).

The user runs the push commands themselves; the app blocks with the exact
commands to run.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


def _run_git(cwd: Path, *args: str) -> tuple[bool, str]:
    """Run a read-only git command. Returns (ok, stdout)."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0, result.stdout.strip()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("git %s failed: %s", args, exc)
        return False, str(exc)


def get_git_state(project_root: Path) -> dict:
    """Inspect the repo. Returns a dict with is_repo, branch, remote, and a
    list of exact bash commands for the user to push images."""
    ok, out = _run_git(project_root, "rev-parse", "--is-inside-work-tree")
    is_repo = ok and out == "true"

    branch = ""
    if is_repo:
        _, branch = _run_git(project_root, "branch", "--show-current")
    branch = branch or "main"

    _, remote = _run_git(project_root, "remote", "get-url", "origin")

    commands: list[str] = [
        "# 0) See exactly what will go up",
        "git status --short images/",
        "",
        "# 1) Stage images + per-slug manifests / codex instructions",
        "git add images/",
        "",
        "# 2) Commit",
        'git commit -m "Add generated Pinterest pin images"',
        "",
    ]
    if remote:
        commands.append("# 3) Push to the existing remote")
        commands.append(f"git push origin {branch}")
    else:
        commands.extend(
            [
                "# 3) Link the GitHub remote first (replace <YOUR_REPO_URL>)",
                "git remote add origin <YOUR_REPO_URL>",
                f"git push -u origin {branch}",
            ]
        )

    return {
        "is_repo": is_repo,
        "branch": branch,
        "remote": remote,
        "commands": commands,
    }


def verify_public_urls(urls: list[str], timeout: float = 30.0) -> dict[str, str]:
    """Return {url: human-readable status} for each public image URL.

    Uses HEAD first; falls back to a streamed GET only if the server rejects
    HEAD (405/501). Status strings look like 'HTTP 200' or 'error: NameResolutionError'.
    """
    results: dict[str, str] = {}
    with httpx.Client(timeout=timeout) as client:
        for url in urls:
            try:
                resp = client.head(url, follow_redirects=True)
                if resp.status_code in (400, 405, 501):
                    # Some CDNs disallow HEAD — check with a streamed GET that
                    # we close immediately (never download the whole image).
                    with client.stream("GET", url, follow_redirects=True) as sresp:
                        results[url] = f"HTTP {sresp.status_code}"
                        sresp.close()
                else:
                    results[url] = f"HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                results[url] = f"error: {type(exc).__name__}"
            except Exception as exc:  # pragma: no cover - defensive
                results[url] = f"error: {type(exc).__name__}"

    return results