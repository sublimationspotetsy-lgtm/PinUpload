"""
core/md_io.py

Handles all keyword-file and pins-file I/O:
  - read_keyword_file: parses a keywords/<slug>.md, extracts frontmatter
  - write_pins_md: renders pins/<slug>.md deterministically from assembled pin dicts
  - write_pins_json: writes pins/<slug>.json (source of truth for Tab 2/3)
  - write_keyword_frontmatter: upserts board: into a keyword file's frontmatter

The .md is ALWAYS rendered from validated data. It is never edited by hand
and re-parsed back. Tab 2 and Tab 3 parse only .json.
"""

from __future__ import annotations

import json
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------

class KeywordInput(BaseModel):
    """Parsed representation of a keywords/<slug>.md file."""
    keyword: str
    slug: str           # = the file's stem (not derived from keyword text)
    board: str | None   # None if not present or invalid in frontmatter
    notes: str | None


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def read_keyword_file(path: Path) -> KeywordInput:
    """Parse a keywords/<slug>.md and return a KeywordInput.

    Frontmatter rules:
    - If YAML frontmatter is present, extract keyword/board/notes from it.
    - If the YAML block is malformed, log a warning and fall back to
      treating the whole file as a plain keyword (no frontmatter).
    - If no frontmatter is present, treat the first non-empty line as keyword.
    - board is returned as-is — the caller validates it against config.boards.

    Raises:
        ValueError: if the file is empty or contains no usable keyword text.
    """
    slug = path.stem
    raw = path.read_text(encoding="utf-8").strip()

    if not raw:
        raise ValueError(f"Keyword file is empty: {path}")

    keyword: str | None = None
    board: str | None = None
    notes: str | None = None
    frontmatter_parse_warning: str | None = None

    m = _FRONTMATTER_RE.match(raw + "\n")
    if m:
        try:
            fm = yaml.safe_load(m.group(1)) or {}
            keyword = str(fm.get("keyword", "")).strip() or None
            board = str(fm.get("board", "")).strip() or None
            notes = str(fm.get("notes", "")).strip() or None
        except yaml.YAMLError as exc:
            frontmatter_parse_warning = (
                f"Malformed YAML frontmatter in {path.name}: {exc}. "
                "Falling back to plain-text keyword."
            )

    if keyword is None:
        # No usable keyword from frontmatter — fall back to first non-empty line.
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        # Skip the frontmatter fence lines if they leaked through.
        lines = [ln for ln in lines if ln not in ("---",)]
        if not lines:
            raise ValueError(f"No keyword text found in: {path}")
        keyword = lines[0]
        board = None
        notes = None

    result = KeywordInput(keyword=keyword, slug=slug, board=board, notes=notes)
    # Attach parse warning as an attribute so the UI can surface it.
    result.__dict__["_frontmatter_warning"] = frontmatter_parse_warning
    return result


def get_frontmatter_warning(ki: KeywordInput) -> str | None:
    """Return any frontmatter parse warning attached during read_keyword_file."""
    return ki.__dict__.get("_frontmatter_warning")


# ---------------------------------------------------------------------------
# Writer — pins/<slug>.md
# ---------------------------------------------------------------------------

def write_pins_md(
    slug: str,
    keyword: str,
    board: str,
    generated_at: str,
    pins: list[dict[str, Any]],
    output_path: Path,
) -> None:
    """Render pins/<slug>.md deterministically from assembled pin dicts.

    The .md is a read-only rendering artefact — never parsed back. It must
    match the spec section 3.2 template exactly.

    Args:
        slug: The keyword slug.
        keyword: Human-readable keyword string.
        board: Confirmed Pinterest board name.
        generated_at: ISO-8601 UTC timestamp string.
        pins: List of dicts from assemble_pins_with_links(). Each dict has
              all Pin fields plus 'index' (int) and 'amazon_link' (str).
        output_path: Where to write the .md file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        f"# Keyword: {keyword}",
        f"# Slug: {slug}",
        f"# Generated: {generated_at}",
        f"# Pins: {len(pins)}",
        "",
    ]

    for pin in pins:
        idx = pin["index"]
        lines.append(f"## Pin {idx:02d}")
        lines.append(f"- **Title:** {pin['title']}")
        lines.append(f"- **Description:** {pin['description']}")
        lines.append(f"- **Link:** {pin['amazon_link']}")
        lines.append(f"- **Image:** {pin['image_filename']}")
        lines.append(f"- **Image Prompt:** {pin['image_prompt']}")
        lines.append("---")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Writer — pins/<slug>.json
# ---------------------------------------------------------------------------

def write_pins_json(
    slug: str,
    keyword: str,
    board: str,
    generated_at: str,
    pins: list[dict[str, Any]],
    output_path: Path,
) -> None:
    """Write pins/<slug>.json — the source of truth for Tab 2 and Tab 3.

    Args:
        slug: The keyword slug.
        keyword: Human-readable keyword string.
        board: Confirmed Pinterest board name.
        generated_at: ISO-8601 UTC timestamp string.
        pins: List of dicts from assemble_pins_with_links(). Each dict has
              all Pin fields plus 'index' (int) and 'amazon_link' (str).
        output_path: Where to write the .json file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "keyword": keyword,
        "slug": slug,
        "board": board,
        "generated_at": generated_at,
        "pins": [
            {
                "index": p["index"],
                "title": p["title"],
                "description": p["description"],
                "amazon_search_term": p["amazon_search_term"],
                "amazon_link": p["amazon_link"],
                "image_filename": p["image_filename"],
                "image_prompt": p["image_prompt"],
                "tags": p.get("tags", []),
            }
            for p in pins
        ],
    }
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Frontmatter write-back
# ---------------------------------------------------------------------------

def write_keyword_frontmatter(path: Path, board: str) -> None:
    """Upsert the board: field in a keyword file's YAML frontmatter.

    If frontmatter already exists, replace or insert the board: key.
    If no frontmatter exists, prepend a minimal frontmatter block.

    This is called before every generation run so the confirmed board is
    remembered on the next run.

    Args:
        path: Path to the keywords/<slug>.md file.
        board: Exact board name string from config.boards.
    """
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw + "\n")

    if m:
        try:
            fm: dict = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            fm = {}
        fm["board"] = board
        # Reconstruct frontmatter block preserving other keys.
        fm_str = yaml.dump(fm, allow_unicode=True, default_flow_style=False).strip()
        body_after_fm = raw[m.end():]
        new_content = f"---\n{fm_str}\n---\n{body_after_fm}"
    else:
        # No frontmatter — prepend a minimal block.
        # Try to extract keyword from first non-empty line.
        first_line = next(
            (ln.strip() for ln in raw.splitlines() if ln.strip()), path.stem
        )
        fm = {"keyword": first_line, "board": board}
        fm_str = yaml.dump(fm, allow_unicode=True, default_flow_style=False).strip()
        new_content = f"---\n{fm_str}\n---\n{raw}"

    path.write_text(new_content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    """Return current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
