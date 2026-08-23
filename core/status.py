"""
core/status.py

Reads and writes state/pipeline_status.json with atomic write semantics
(write to .tmp, then os.replace) to prevent corruption on crash.

Tab 1 calls write_status_skeleton() immediately after pins/<slug>.json
is confirmed written. Tabs 2 and 3 extend this file with flip_* helpers
added in later phases.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Atomic write primitive
# ---------------------------------------------------------------------------

def atomic_write(path: Path, data: dict[str, Any]) -> None:
    """Write data as JSON to path atomically via a .tmp intermediate file.

    Guarantees that a crash mid-write cannot leave path in a corrupt state:
    the previous file (if any) is untouched until os.replace succeeds.

    Args:
        path: Target file path. Parent directory must exist.
        data: Dict to serialise as JSON.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp_path, path)
    except Exception:
        # Clean up the temp file if the write or replace failed.
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read_status(path: Path) -> dict[str, Any]:
    """Read pipeline_status.json and return its contents as a dict.

    Returns an empty dict if the file does not exist — callers treat this
    as "no pins have been generated yet".

    Args:
        path: Path to state/pipeline_status.json.

    Returns:
        Parsed JSON dict, or {} if file absent.

    Raises:
        json.JSONDecodeError: if the file exists but contains invalid JSON.
    """
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Per-pin-image status defaults (shared by skeleton writer + upsert helper)
# ---------------------------------------------------------------------------

PIN_IMAGE_DEFAULTS: dict[str, Any] = {
    "image_generated": False,
    "pushed_to_github": False,
    "github_url": None,
    "csv_exported": False,
    "csv_batch_file": None,
    "csv_exported_at": None,
    "scheduled_publish_utc": None,
    "pinterest_uploaded_confirmed": False,
}


# ---------------------------------------------------------------------------
# Tab 1: skeleton writer
# ---------------------------------------------------------------------------

def write_status_skeleton(
    slug: str,
    keyword: str,
    board: str,
    image_filenames: list[str],
    path: Path,
) -> None:
    """Write (or overwrite) the status skeleton for one slug immediately after
    pins/<slug>.json is written to disk.

    All status flags start as false/null. Existing entries for other slugs
    are preserved — this only touches the block for the given slug.

    The write uses atomic_write so a crash cannot corrupt other slugs' data.

    Args:
        slug: The keyword slug.
        keyword: Human-readable keyword string.
        board: Confirmed Pinterest board name (stored for reference).
        image_filenames: List of image filenames from the generated pins
                         (e.g. ["bedazzled-jean-shorts-outfit-01.png", ...]).
        path: Path to state/pipeline_status.json.
    """
    status = read_status(path)

    per_pin: dict[str, Any] = {
        filename: dict(PIN_IMAGE_DEFAULTS) for filename in image_filenames
    }

    status[slug] = {
        "keyword": keyword,
        "board": board,
        "pins": per_pin,
    }

    atomic_write(path, status)


# ---------------------------------------------------------------------------
# Phase 2: per-image status upsert
# ---------------------------------------------------------------------------

def update_pin_image_status(
    path: Path,
    slug: str,
    filename: str,
    *,
    image_generated: bool | None = None,
    pushed_to_github: bool | None = None,
    github_url: str | None = None,
) -> dict[str, Any]:
    """Upsert (create or update) the status row for one pin image atomically.

    Only the keyword/board block for ``slug`` is touched; other slugs' data
    is preserved. The write uses atomic_write so a crash cannot corrupt the
    file.

    Args:
        path: Path to state/pipeline_status.json.
        slug: The keyword slug owning this image.
        filename: Image filename, e.g. "bedazzled-jean-shorts-outfit-01.png".
        image_generated: Override value for the flag, or None to leave as-is.
        pushed_to_github: Override value, or None to leave as-is.
        github_url: Override value, or None to leave as-is.

    Returns:
        The updated per-image status row.
    """
    status = read_status(path)
    block = status.setdefault(slug, {"keyword": slug, "board": "", "pins": {}})
    pins_map = block.setdefault("pins", {})

    row = pins_map.setdefault(filename, {})
    for key, default in PIN_IMAGE_DEFAULTS.items():
        row.setdefault(key, default)

    if image_generated is not None:
        row["image_generated"] = bool(image_generated)
    if pushed_to_github is not None:
        row["pushed_to_github"] = bool(pushed_to_github)
    if github_url is not None:
        row["github_url"] = github_url

    atomic_write(path, status)
    return row
