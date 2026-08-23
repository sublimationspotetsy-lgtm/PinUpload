"""
app.py

Pinterest Affiliate Pin Generator — Streamlit entrypoint.

Tab 1: Generate Pins (Phase 1, fully implemented)
Tab 2: Prepare Images for Codex (Phase 2, fully implemented)
Tab 3: Export to Pinterest CSV (Phase 3 placeholder)

Run with: streamlit run app.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import streamlit as st
import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap: load .env before any core imports that read env vars
# ---------------------------------------------------------------------------

load_dotenv()

# ---------------------------------------------------------------------------
# Project root is wherever app.py lives
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent

# ---------------------------------------------------------------------------
# Ensure required directories exist
# ---------------------------------------------------------------------------

for _d in ("keywords", "pins", "images", "exports", "state"):
    (ROOT / _d).mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Config loading + validation
# ---------------------------------------------------------------------------

_CONFIG_PATH = ROOT / "config.yaml"

@st.cache_resource
def load_config() -> dict:
    """Load config.yaml once per session."""
    if not _CONFIG_PATH.exists():
        st.error(f"config.yaml not found at {_CONFIG_PATH}. Cannot start.")
        st.stop()
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg


def validate_config(cfg: dict) -> list[str]:
    """Return a list of configuration error messages (empty = OK)."""
    errors: list[str] = []

    # Board list must be the exact 6 canonical names — see PLAN.md section 5 (A5).
    expected_boards: list[str] = [
        "Occasion Outfits | Date Night, Work & Glam",
        "Poetcore & Glamoratti Outfit Ideas",
        "Seasonal Outfit Ideas | Spring, Summer, Fall & Winter",
        "Shop the Look | Amazon & SHEIN Fashion",
        "Petite & Curvy Outfit Ideas by Body Type",
        "Sport-Luxe & Athleisure Outfit Ideas",
    ]
    actual_boards: list[str] = cfg.get("boards", [])
    if set(actual_boards) != set(expected_boards):
        errors.append(
            "config.yaml boards list does not match the 6 expected Pinterest board names. "
            "A mismatch will silently create duplicate boards on Pinterest. "
            "Restore the boards list in config.yaml or update this validation."
        )

    # Warn on placeholder image_base_url.
    base_url: str = cfg.get("image_base_url", "")
    if "USER" in base_url or "REPO" in base_url:
        errors.append(
            "config.yaml image_base_url still contains placeholder 'USER' or 'REPO'. "
            "Tab 2's GitHub sync and Tab 3's link verification will not work "
            "until you replace this with your actual GitHub username and repo name."
        )

    return errors


# ---------------------------------------------------------------------------
# Board selection heuristic
# ---------------------------------------------------------------------------

_HEURISTIC_RULES: list[tuple[list[str], str]] = [
    (["petite", "curvy"], "Petite & Curvy Outfit Ideas by Body Type"),
    (["athleisure", "gym", "sport"], "Sport-Luxe & Athleisure Outfit Ideas"),
    (["date night", "work", "glam"], "Occasion Outfits | Date Night, Work & Glam"),
    (
        ["seasonal", "spring", "summer", "fall", "winter"],
        "Seasonal Outfit Ideas | Spring, Summer, Fall & Winter",
    ),
    (["poetcore", "glamoratti"], "Poetcore & Glamoratti Outfit Ideas"),
]


def heuristic_board(keyword: str, boards: list[str], default_board: str) -> tuple[str, str]:
    """Return (board_name, source_label) via keyword-substring heuristic.

    source_label is one of: "heuristic match", "default — please confirm"
    """
    kw_lower = keyword.lower()
    for triggers, board_name in _HEURISTIC_RULES:
        if any(t in kw_lower for t in triggers):
            if board_name in boards:
                return board_name, "heuristic match"
    return default_board, "default — please confirm"


def resolve_board(
    keyword: str,
    frontmatter_board: str | None,
    boards: list[str],
    default_board: str,
) -> tuple[str, str]:
    """Apply the fallback chain and return (board_name, source_label).

    Fallback chain:
      1. frontmatter board: if present AND valid → use it, source = "frontmatter"
      2. heuristic → source = "heuristic match"
      3. default_board → source = "default — please confirm"
    """
    if frontmatter_board:
        if frontmatter_board in boards:
            return frontmatter_board, "frontmatter"
        else:
            # Frontmatter board is set but invalid — don't use it silently.
            return heuristic_board(keyword, boards, default_board)[0], (
                f"frontmatter board '{frontmatter_board}' not in config.boards "
                f"— falling back to heuristic"
            )
    return heuristic_board(keyword, boards, default_board)


# ---------------------------------------------------------------------------
# Core imports (after env load)
# ---------------------------------------------------------------------------

from core.amazon_links import assemble_pins_with_links
from core.gemini_client import GeminiError, generate_pins
from core.github_utils import get_git_state, verify_public_urls
from core.manifest import (
    manifest_is_stale,
    scan_images,
    write_codex_instructions,
    write_manifest,
)
from core.md_io import (
    KeywordInput,
    get_frontmatter_warning,
    read_keyword_file,
    utc_now_iso,
    write_keyword_frontmatter,
    write_pins_json,
    write_pins_md,
)
from core.status import (
    read_status,
    update_pin_image_status,
    write_status_skeleton,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Pinterest Affiliate Pin Generator",
    page_icon="📌",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------

def _init_session() -> None:
    if "generation_log" not in st.session_state:
        st.session_state.generation_log = []
    if "last_generated_md" not in st.session_state:
        st.session_state.last_generated_md = None
    if "last_generated_slug" not in st.session_state:
        st.session_state.last_generated_slug = None

_init_session()

# ---------------------------------------------------------------------------
# Sidebar dashboard
# ---------------------------------------------------------------------------

def render_sidebar(cfg: dict) -> None:
    """Render running pipeline totals in the sidebar from pipeline_status.json."""
    status_path = ROOT / "state" / "pipeline_status.json"
    status = read_status(status_path)

    total_keywords = len(status)
    total_pins = sum(len(v.get("pins", {})) for v in status.values())

    def count(flag: str) -> int:
        n = 0
        for entry in status.values():
            for pin_data in entry.get("pins", {}).values():
                if pin_data.get(flag):
                    n += 1
        return n

    st.sidebar.title("📌 Pipeline Status")
    st.sidebar.markdown(f"**{total_keywords}** keywords · **{total_pins}** pins")
    st.sidebar.markdown("---")

    if total_pins == 0:
        st.sidebar.info("No pins generated yet.")
        return

    img_gen = count("image_generated")
    pushed = count("pushed_to_github")
    exported = count("csv_exported")
    confirmed = count("pinterest_uploaded_confirmed")

    st.sidebar.metric("Images generated", f"{img_gen} / {total_pins}")
    st.sidebar.metric("Pushed to GitHub", f"{pushed} / {total_pins}")
    st.sidebar.metric("Exported to CSV", f"{exported} / {total_pins}")
    st.sidebar.metric("Confirmed on Pinterest", f"{confirmed} / {total_pins}")


# ---------------------------------------------------------------------------
# Tab 1 — Generate Pins
# ---------------------------------------------------------------------------

def run_tab1(cfg: dict) -> None:
    st.header("Tab 1 — Generate Pins")

    api_key = os.environ.get("GEMINI_API_KEY", "")
    amazon_tag = os.environ.get("AMAZON_ASSOCIATE_TAG", "")

    if not api_key:
        st.error(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
        return

    if not amazon_tag:
        st.warning(
            "AMAZON_ASSOCIATE_TAG is not set in .env. "
            "Amazon links will use an empty tag — update .env before exporting to Pinterest."
        )

    boards: list[str] = cfg["boards"]
    default_board: str = cfg["default_board"]
    gemini_model: str = cfg["gemini_model"]
    gemini_temperature: float = cfg["gemini_temperature"]
    amazon_domain: str = cfg["amazon_domain"]

    # ---- File picker -------------------------------------------------------
    keywords_dir = ROOT / "keywords"
    keyword_files = sorted(keywords_dir.glob("*.md"))

    if not keyword_files:
        st.info(
            "No keyword files found in `keywords/`. "
            "Create a `keywords/<slug>.md` file to get started."
        )
        return

    st.subheader("1. Select keywords")
    select_all = st.checkbox("Select all", value=False)
    selected_paths: list[Path] = []
    for kf in keyword_files:
        checked = st.checkbox(kf.stem, value=select_all, key=f"kw_{kf.stem}")
        if checked:
            selected_paths.append(kf)

    if not selected_paths:
        st.info("Select at least one keyword file to continue.")
        return

    # ---- Parse selected keyword files + board confirmation -----------------
    st.subheader("2. Confirm boards")
    st.caption(
        "Review the pre-selected board for each keyword. "
        "A wrong board name will silently create a duplicate Pinterest board."
    )

    keyword_inputs: list[KeywordInput] = []
    confirmed_boards: dict[str, str] = {}  # slug -> confirmed board name
    parse_errors: list[str] = []

    for path in selected_paths:
        try:
            ki = read_keyword_file(path)
        except ValueError as exc:
            parse_errors.append(f"**{path.name}**: {exc}")
            continue

        warning = get_frontmatter_warning(ki)
        if warning:
            st.warning(f"⚠️ {warning}")

        initial_board, source_label = resolve_board(
            ki.keyword, ki.board, boards, default_board
        )

        # Always show a dropdown — never silently assume.
        try:
            initial_idx = boards.index(initial_board)
        except ValueError:
            initial_idx = 0

        selected_board = st.selectbox(
            f"Board for: **{ki.keyword}**",
            options=boards,
            index=initial_idx,
            key=f"board_{ki.slug}",
        )
        st.caption(f"Source: {source_label}")

        keyword_inputs.append(ki)
        confirmed_boards[ki.slug] = selected_board

    if parse_errors:
        for err in parse_errors:
            st.error(err)
        st.stop()

    # ---- Skip-if-exists toggle ---------------------------------------------
    st.subheader("3. Options")
    skip_if_exists = st.toggle(
        "Skip keywords that already have a generated pins JSON",
        value=True,
        help=(
            "ON (default): skips pins/<slug>.json if it already exists. "
            "OFF: overwrites and resets pipeline status for that keyword."
        ),
    )

    # ---- Generate button ---------------------------------------------------
    st.subheader("4. Generate")
    if st.button("Generate Pins", type="primary", disabled=not keyword_inputs):
        log_area = st.empty()
        log_lines: list[str] = []

        def add_log(msg: str) -> None:
            log_lines.append(msg)
            log_area.text_area(
                "Live log", value="\n".join(log_lines), height=200, disabled=True
            )

        pins_dir = ROOT / "pins"
        status_path = ROOT / "state" / "pipeline_status.json"

        for ki in keyword_inputs:
            slug = ki.slug
            board = confirmed_boards[slug]
            json_out = pins_dir / f"{slug}.json"
            md_out = pins_dir / f"{slug}.md"

            # Write-back the confirmed board to frontmatter before generating.
            write_keyword_frontmatter(selected_paths[keyword_inputs.index(ki)], board)

            # Skip-if-exists check.
            if skip_if_exists and json_out.exists():
                add_log(f"[SKIP] {slug} — pins/{slug}.json already exists.")
                # Warn if .md is missing despite .json existing.
                if not md_out.exists():
                    add_log(
                        f"  [WARN] {slug} — pins/{slug}.md is missing. "
                        "Use the 'Re-render .md' button below to rebuild it without re-calling Gemini."
                    )
                continue

            add_log(f"[START] {slug} — calling Gemini ({gemini_model})...")

            def on_retry(attempt: int, exc: Exception) -> None:
                add_log(
                    f"  [RETRY {attempt}/3] {slug} — {type(exc).__name__}: {exc}. "
                    f"Waiting before next attempt..."
                )

            try:
                pin_batch = generate_pins(
                    keyword=ki.keyword,
                    slug=slug,
                    notes=ki.notes,
                    model=gemini_model,
                    temperature=gemini_temperature,
                    api_key=api_key,
                    on_retry=on_retry,
                )
            except GeminiError as exc:
                add_log(f"  [FAIL] {slug} — {exc}. Skipping this keyword.")
                continue

            add_log(f"  [OK] {slug} — {len(pin_batch.pins)} pins validated.")

            # Assemble pin dicts with amazon_link.
            generated_at = utc_now_iso()
            pins = assemble_pins_with_links(pin_batch, slug, amazon_tag, amazon_domain)

            # Write .json first, then .md from the same data.
            write_pins_json(slug, ki.keyword, board, generated_at, pins, json_out)
            add_log(f"  [WRITE] pins/{slug}.json")

            write_pins_md(slug, ki.keyword, board, generated_at, pins, md_out)
            add_log(f"  [WRITE] pins/{slug}.md")

            # Write pipeline_status.json skeleton.
            image_filenames = [p["image_filename"] for p in pins]
            write_status_skeleton(slug, ki.keyword, board, image_filenames, status_path)
            add_log(f"  [STATUS] pipeline_status.json updated for {slug}.")

            # Store last generated for preview pane.
            st.session_state.last_generated_slug = slug
            st.session_state.last_generated_md = md_out.read_text(encoding="utf-8")

        add_log("[DONE] Generation run complete.")
        st.rerun()

    # ---- Preview pane ------------------------------------------------------
    pins_dir = ROOT / "pins"

    # Re-render .md from .json (recovery path when .md is missing).
    st.subheader("5. Re-render missing .md files")
    orphaned: list[Path] = [
        p for p in sorted(pins_dir.glob("*.json"))
        if not (pins_dir / p.with_suffix(".md").name).exists()
    ]
    if orphaned:
        st.warning(
            f"{len(orphaned)} slug(s) have a .json but are missing a .md: "
            + ", ".join(p.stem for p in orphaned)
        )
        if st.button("Re-render missing .md files (no Gemini call)"):
            import json as _json
            from core.md_io import write_pins_md as _write_md
            for json_path in orphaned:
                data = _json.loads(json_path.read_text(encoding="utf-8"))
                _write_md(
                    slug=data["slug"],
                    keyword=data["keyword"],
                    board=data["board"],
                    generated_at=data["generated_at"],
                    pins=data["pins"],
                    output_path=pins_dir / f"{data['slug']}.md",
                )
            st.success("Re-rendered.")
            st.rerun()
    else:
        st.caption("All generated slugs have matching .md files.")

    # Preview the last successfully generated keyword.
    st.subheader("6. Preview")
    if st.session_state.last_generated_md:
        st.caption(f"Showing: {st.session_state.last_generated_slug}")
        st.code(st.session_state.last_generated_md, language="markdown")
    else:
        # Offer to preview any existing .md.
        existing_mds = sorted(pins_dir.glob("*.md"))
        if existing_mds:
            preview_choice = st.selectbox(
                "Select a generated keyword to preview",
                options=[p.stem for p in existing_mds],
                key="preview_selector",
            )
            if preview_choice:
                preview_path = pins_dir / f"{preview_choice}.md"
                st.code(preview_path.read_text(encoding="utf-8"), language="markdown")
        else:
            st.info("No pins generated yet. Run generation above.")


# ---------------------------------------------------------------------------
# Tab 2 placeholder
# ---------------------------------------------------------------------------

def run_tab2(cfg: dict) -> None:
    st.header("Tab 2 — Prepare Images for Codex")

    pins_dir = ROOT / "pins"
    images_dir = ROOT / "images"
    status_path = ROOT / "state" / "pipeline_status.json"
    image_base_url: str = cfg.get("image_base_url", "")

    slugs = sorted(p.stem for p in pins_dir.glob("*.json"))
    if not slugs:
        st.info("No generated pin sets yet. Run Tab 1 to generate some first.")
        return

    slug = st.selectbox(
        "Generated keyword (slug)",
        options=slugs,
        key="tab2_slug_select",
    )

    pins_json_path = pins_dir / f"{slug}.json"
    slug_dir = images_dir / slug
    manifest_path = slug_dir / "manifest.json"
    codex_path = slug_dir / "codex_instructions.md"

    # ---- 1. Manifest + Codex instructions ----------------------------------
    st.subheader("1. Manifest + Codex instructions")
    st.caption(
        "Builds `images/<slug>/manifest.json` (image inventory + expected "
        "public URLs) and `images/<slug>/codex_instructions.md` (a "
        "self-contained brief anyone can use to generate the images). "
        "No Gemini call is made here."
    )

    # Auto-refresh whenever the pins JSON changed (or files are missing).
    if manifest_is_stale(pins_json_path, manifest_path) or not codex_path.exists():
        manifest = write_manifest(pins_json_path, image_base_url, manifest_path)
        write_codex_instructions(manifest, codex_path)
        st.caption("[auto] Manifest + codex instructions written")
    else:
        with open(manifest_path, encoding="utf-8") as _f:
            manifest = json.load(_f)

    if st.button("Re-write manifest + codex instructions"):
        manifest = write_manifest(pins_json_path, image_base_url, manifest_path)
        write_codex_instructions(manifest, codex_path)
        st.success(f"Wrote `images/{slug}/manifest.json` + `codex_instructions.md`")
        st.rerun()

    imgs: list[dict] = manifest.get("images", [])
    if not imgs:
        st.warning(f"manifest for `{slug}` has no images. Regenerate the pins.")
        return

    st.markdown(
        f"**{len(imgs)} images expected** — manifest at `images/{slug}/manifest.json`"
    )
    with st.expander("Preview codex_instructions.md"):
        st.code(codex_path.read_text(encoding="utf-8"), language="markdown")
    with st.expander("Preview expected public URLs (after push)"):
        st.code(
            "\n".join(f"{i['index']:02d}. {i['public_url']}" for i in imgs),
            language="text",
        )

    # ---- 2. Image scan -----------------------------------------------------
    st.subheader("2. Image scan — what is on disk?")
    st.caption(
        "Images are saved flat in `images/` using the exact filenames assigned "
        "in the pins JSON. This scan is local-only; it does not touch GitHub."
    )

    scan = scan_images(manifest, images_dir)
    present = sum(1 for ok in scan.values() if ok)
    total = len(scan)
    st.progress(present / total if total else 1.0)
    st.markdown(f"**{present} / {total}** image files present on disk.")

    status = read_status(status_path)
    slug_status = status.get(slug, {})
    rows = []
    for im in imgs:
        f = im["image_filename"]
        row = slug_status.get("pins", {}).get(f, {})
        rows.append(
            {
                "Image": f,
                "On disk": "✓" if scan.get(f) else "✗",
                "Status flag": "generated" if row.get("image_generated") else "pending",
                "GitHub URL": row.get("github_url") or "—",
            }
        )
    st.dataframe(rows, width="stretch")

    col_a, col_b = st.columns([1, 2])
    with col_a:
        if st.button("Update status flags from disk scan"):
            for f, exists in scan.items():
                update_pin_image_status(status_path, slug, f, image_generated=exists)
            st.success("`image_generated` flags updated from disk scan.")
            st.rerun()
    with col_b:
        st.caption(
            "Run this after images land in `images/` (or after deleting any) "
            "to keep `pipeline_status.json` truthful."
        )

    existing = [f for f, ok in scan.items() if ok]
    if existing:
        st.markdown("**Thumbnails (on disk)**")
        cols = st.columns(min(5, len(existing)))
        for idx, f in enumerate(existing):
            with cols[idx % len(cols)]:
                st.image(str(images_dir / f), caption=f, width="stretch")

    # ---- 3. Sync to GitHub + verify ----------------------------------------
    st.subheader("3. Sync images to GitHub")
    st.caption(
        "The app deliberately never pushes for you (plan A8). Confirm the "
        "detected git state, run the exact commands, then verify the public "
        "URLs. Until the images are pushed, verification returns 404s."
    )

    git_state = get_git_state(ROOT)
    if not git_state["is_repo"]:
        st.error(
            "Not inside a git repo. Run `git init` at the project root "
            f"(`cd {ROOT}`) then retry."
        )
        return

    st.markdown(
        f"- **Repo:** yes (branch `{git_state['branch']}`)\n"
        f"- **Remote:** {git_state['remote'] or 'none — add it in the commands below'}"
    )
    st.code("\n".join(git_state["commands"]), language="bash")

    if "tab2_verify" not in st.session_state:
        st.session_state.tab2_verify = {}

    if st.button("Verify public URLs (after pushing)"):
        urls = [im["public_url"] for im in imgs]
        with st.spinner(
            "Checking raw.githubusercontent.com … (a few seconds per image)"
        ):
            results = verify_public_urls(urls)
        vrows = []
        live = 0
        for im in imgs:
            url = im["public_url"]
            ver = results.get(url, "?")
            ok = ver.startswith("HTTP 200")
            if ok:
                live += 1
            update_pin_image_status(
                status_path,
                slug,
                im["image_filename"],
                pushed_to_github=ok,
                github_url=url if ok else None,
            )
            vrows.append(
                {"Image": im["image_filename"], "Public URL": url, "Status": ver}
            )
        st.session_state.tab2_verify[slug] = vrows

        if live == len(imgs):
            st.success(f"All {live} public URLs are live and reachable.")
        elif live > 0:
            st.warning(
                f"{live}/{len(imgs)} URLs are live. Push the rest with the "
                "commands above, then re-verify."
            )
        else:
            st.error(
                "No URLs reachable yet. Push the images (commands above), or "
                "check `image_base_url` in config.yaml matches your actual "
                "GitHub repo and branch."
            )
        st.rerun()

    if slug in st.session_state.tab2_verify:
        st.markdown("**Last verification result**")
        st.dataframe(st.session_state.tab2_verify[slug], width="stretch")


# ---------------------------------------------------------------------------
# Tab 3 placeholder
# ---------------------------------------------------------------------------

def run_tab3(cfg: dict) -> None:
    st.header("Tab 3 — Export to Pinterest CSV")
    st.info(
        "**Coming in Phase 3.** "
        "This tab will export Pinterest bulk-upload CSVs, apply the scheduling "
        "algorithm, verify image URLs, and track confirmed uploads."
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = load_config()

    # Startup validation — hard-error on board mismatch, warning on placeholder URL.
    config_errors = validate_config(cfg)
    non_url_errors = [e for e in config_errors if "image_base_url" not in e]
    url_warnings = [e for e in config_errors if "image_base_url" in e]

    if non_url_errors:
        st.error("\n\n".join(non_url_errors))
        st.stop()

    for w in url_warnings:
        st.sidebar.warning(w)

    render_sidebar(cfg)

    tab1, tab2, tab3 = st.tabs(
        ["Generate Pins", "Prepare Images", "Export to Pinterest CSV"]
    )
    with tab1:
        run_tab1(cfg)
    with tab2:
        run_tab2(cfg)
    with tab3:
        run_tab3(cfg)


if __name__ == "__main__":
    main()
