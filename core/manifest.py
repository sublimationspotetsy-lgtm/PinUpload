"""
core/manifest.py

Phase 2 — Prepare Images for Codex.

Builds images/<slug>/manifest.json and images/<slug>/codex_instructions.md
from the source-of-truth pins/<slug>.json, and provides disk-scan helpers.

Design rules:
- The manifest is a static, reproducible record of what images SHOULD exist,
  their prompts, and their expected public URLs. It does NOT hold mutable
  flags (image_generated / pushed_to_github / github_url) — those live in
  state/pipeline_status.json so the manifest can be regenerated freely.
- Image files live FLAT inside images/ (naming: {slug}-{NN}.png), matching the
  Pin.image_filename contract and the config image_base_url that points at the
  repo's images/ directory on GitHub raw.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_pins_json(pins_json: Path) -> dict[str, Any]:
    """Read and return the pins/<slug>.json source of truth."""
    return json.loads(pins_json.read_text(encoding="utf-8"))


def public_url_for(image_base_url: str, filename: str) -> str:
    """Assemble the raw.githubusercontent.com URL for one image file.

    Images live flat in the repo's images/ dir, and image_base_url already
    points at that directory (e.g. https://raw.githubusercontent.com/USER/REPO/
    main/images), so we append just the filename.
    """
    base = image_base_url.rstrip("/")
    return f"{base}/{filename}"


def build_manifest_data(
    pins_data: dict[str, Any], image_base_url: str, image_store: str = "images"
) -> dict[str, Any]:
    """Build the manifest dict from pins data (no I/O, easily testable)."""
    images: list[dict[str, Any]] = []
    for pin in pins_data["pins"]:
        filename = pin["image_filename"]
        images.append(
            {
                "index": pin["index"],
                "image_filename": filename,
                "image_prompt": pin["image_prompt"],
                "title": pin["title"],
                "amazon_link": pin["amazon_link"],
                "public_url": public_url_for(image_base_url, filename),
            }
        )
    return {
        "slug": pins_data["slug"],
        "keyword": pins_data["keyword"],
        "board": pins_data["board"],
        "generated_at": pins_data["generated_at"],
        "manifest_created_at": utc_now_iso(),
        "image_store": image_store,
        "public_base_url": image_base_url,
        "images": images,
    }


def write_manifest(
    pins_json: Path, image_base_url: str, output_path: Path
) -> dict[str, Any]:
    """Write images/<slug>/manifest.json and return the manifest dict.

    Args:
        pins_json: Path to pins/<slug>.json (source of truth).
        image_base_url: Config value e.g. .../PinUpload/main/images.
        output_path: Where to write the manifest, e.g. images/<slug>/manifest.json.
    """
    pins_data = load_pins_json(pins_json)
    manifest = build_manifest_data(pins_data, image_base_url)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def write_codex_instructions(manifest: dict[str, Any], output_path: Path) -> None:
    """Write images/<slug>/codex_instructions.md for a human/agent to run.

    This file is intentionally self-contained: someone (or another agent)
    reads it and generates the images without needing to consult Streamlit.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    slug = manifest["slug"]
    keyword = manifest["keyword"]
    images = manifest["images"]

    lines: list[str] = [
        f"# Image Generation Instructions — {keyword}",
        "",
        f"Generate the {len(images)} Pinterest images for the keyword below, "
        "one per pin, using the prompts listed.",
        "",
        "## Output requirements",
        "- Format: PNG, portrait, 2:3 aspect ratio (~1000x1500 px; 1024x1536 ok).",
        "- Save all files flat inside `images/` with the EXACT filenames below.",
        "- Do not rename, renumber, or reorder the files.",
        "- No text, no watermarks, no logos, no brand names, no real/famous faces.",
        "- Fully-clothed, tasteful, editorial fashion photography — Pinterest-safe.",
        "- Follow each prompt's scene/pose/setting so the images look distinct.",
        "",
        "## Files",
        "",
    ]
    for im in images:
        lines.append(f"### images/{im['image_filename']}")
        lines.append(f"- Vibe: {im['title']}")
        lines.append(f"- Prompt: {im['image_prompt']}")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def scan_images(manifest: dict[str, Any], images_dir: Path) -> dict[str, bool]:
    """Return {filename: exists_on_disk} for every image in the manifest.

    Args:
        manifest: The manifest dict (must have 'images').
        images_dir: Path to the flat images/ directory.
    """
    return {
        im["image_filename"]: (images_dir / im["image_filename"]).is_file()
        for im in manifest["images"]
    }


def manifest_is_stale(pins_json: Path, manifest_path: Path) -> bool:
    """True if the manifest is missing or older than the pins JSON it renders.

    Callers use this to auto-refresh the manifest whenever pins/<slug>.json
    has been regenerated.
    """
    if not manifest_path.exists():
        return True
    pins_mtime = pins_json.stat().st_mtime
    manifest_mtime = manifest_path.stat().st_mtime
    return pins_mtime > manifest_mtime