# PinUpload — Pinterest Pin Image Host

This repository is an **image host only**. It stores generated Pinterest pin
images (`images/*.png`) so they are publicly reachable at
`raw.githubusercontent.com` URLs — which Pinterest's bulk-upload CSV requires
as the `Media URL` column.

The app source code is **not** in this repository; it runs locally.

---

## 1. One-time setup

```bash
cd /path/to/pin-content-app

# Create + activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

Create a `.env` file in the project root (never commit it):

```
GEMINI_API_KEY=your_google_gemini_api_key
AMAZON_ASSOCIATE_TAG=yourtag-20
```

Check `config.yaml`:
- `image_base_url` must point at this repo, e.g.
  `https://raw.githubusercontent.com/<USER>/PinUpload/main/images`
- `boards` must exactly match your Pinterest board names.

## 2. Run the app

```bash
source .venv/bin/activate         # always run inside the venv
streamlit run app.py
```

Then open http://localhost:8501 — the dashboard sidebar shows pipeline totals
on every tab.

## 3. Workflow

### Tab 1 — Generate Pins
1. Select keyword file(s) from `keywords/`.
2. Confirm each keyword's Pinterest board.
3. Click **Generate Pins** → writes `pins/<slug>.json`, `pins/<slug>.md`,
   prompts manifest, and initializes `state/pipeline_status.json`.

### Tab 2 — Prepare Images
1. Generate/save the images into `images/` using the filenames from the pins JSON.
2. Click **Re-write manifest** if needed, then follow the git commands shown
   (see section 4 below) to push the images to this repo.
3. Click **Verify public URLs** — every URL must return HTTP 200 before Tab 3
   will accept the pin.

### Tab 3 — Export to Pinterest CSV
1. Pins that are pushed-and-not-exported are pre-selected; exclusions are listed
   with reasons.
2. **Verify Media URLs** → live HEAD check on every selected URL.
3. **Export** → writes `exports/pinterest_batch_<date>_<n>.csv`
   (max 200 rows per file, hourly UTC schedule slots).
4. Upload the CSV at Pinterest → **Settings → Bulk create pins**, then tick the
   "I uploaded ..." checkbox in Tab 3 to mark those pins confirmed.

## 4. Pushing images to GitHub

Images live flat in `images/`. This repo's `.gitignore` allows ONLY
`README.md`, `LICENSE`, `.gitignore`, and `images/*.png` — app source can never
be committed here by accident.

```bash
cd /path/to/pin-content-app

git add images/*.png
git status                        # sanity-check only PNGs are staged
git commit -m "Add pin images"
git push origin main
```

If the push is rejected, fetch first and inspect:

```bash
git pull --rebase origin main     # or fetch + compare before forcing
git push --force-with-lease origin main   # only when certain local is correct
```

## 5. Verify images are live

Each pushed file becomes available at:

```
https://raw.githubusercontent.com/<USER>/PinUpload/main/images/<filename>.png
```

Open one in a browser, or HEAD-check from the project venv:

```bash
.venv/bin/python tests/verify_after_push.py
```

## Notes

- Do **not** delete files via the GitHub web UI — it creates deletion commits
  that will conflict with local pushes.
- Image files should be ≤ ~20 MB each (GitHub limit); 1000×1500 PNGs (~3 MB)
  work well.

