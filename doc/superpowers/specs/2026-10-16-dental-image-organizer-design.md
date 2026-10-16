# Dental-Image-Organizer — Technical Design Spec

- **Date:** 2026-10-16
- **Status:** Approved design; awaiting written-spec review
- **Inputs:**
  - [Product requirements](../../requirements.md)
  - [Reference app analysis](../../research/2026-10-16-photo-smile-tw-analysis.md)

## 1. Summary

Dental-Image-Organizer is a **local Python application** with a **browser UI served on
`localhost`**. It runs on the dentist's Mac and processes clinical intraoral photos entirely on
that machine. It delivers two features:

- **Feature 1 — Procedure.** Photos taken during and after treatment are automatically aligned,
  color-matched, and cropped to a dentist-edited pre-op **reference photo**. They are then
  exported as individual photos and as a horizontal sequence image.
- **Feature 2 — Composite.** Nine intraoral photos are automatically assigned to 3 × 3 slots by an
  AI view classifier, then leveled, flipped, cropped, and color-unified into one composite image.

Distribution and operation cost **$0**:

- Source and CI live on GitHub.
- The package is published on PyPI.
- Users install with a one-line install script that uses `uv`.
- No server, cloud storage, or paid signing is required.

### 1.1 Decision record

| Topic | Decision | Main reason |
|---|---|---|
| Intake | Mac only. The dentist moves photos from iPhone to Mac (AirDrop / iCloud Photos), then drags them into the app. | Chosen by the stakeholder. Nothing leaves the clinic machine. |
| Processing | Python local app (FastAPI + OpenCV + onnxruntime) | Full CV/ML ecosystem; one language for training, inference, and pipeline; no browser limits |
| UI | React + Vite + TypeScript, served by the local FastAPI server | Rich interaction: drag-to-swap grid, pan/zoom, onion-skin alignment |
| Storage | Local-first, on disk in Finder-visible folders, with zip export/import | Durable (no browser eviction), backed up by Time Machine, portable |
| 9-slot assignment | AI classification (DINOv2 embeddings + linear head) with few-shot learning from user corrections; EXIF-order fallback | Chosen by the stakeholder. Avoids needing a public training dataset. |
| Distribution | Install script + `uv tool install`; launcher `.app` generated locally | $0 and no Gatekeeper prompt, because local files carry no quarantine attribute |
| Development host | Windows only (the developer has no Mac) | Everything is cross-platform Python; macOS-specific parts are verified in GitHub Actions macOS runners |
| License | MIT | Permissive and compatible with all dependencies (DINOv2 is Apache-2.0) |
| Language | Code, comments, docs, and UI strings in English | Project convention. UI strings are centralized so a zh-TW translation can be added later. |

## 2. Users and Success Criteria

- **Primary user:** a dentist on macOS (Apple Silicon or Intel) who takes clinical photos with an
  iPhone.
- **Success criteria:**
  1. Importing raw intra-op and post-op photos yields outputs that match the reference framing,
     angle, and color with **no manual retouching** for typical photos.
  2. A 9-photo composite is produced with correct slot placement after drag-to-fix corrections.
     The number of corrections falls as the classifier learns.
  3. Exported files follow the naming rules in [requirements §3](../../requirements.md#3-workflow-demonstration-user-sop).
  4. When automation fails, the user always has a manual fallback (manual alignment, slot swap,
     per-cell adjustment). The app never fails silently.
  5. Patient photos never leave the machine. The only network calls are an opt-in model download
     and an opt-out version check.

## 3. Workflow (stage 3.1)

### 3.1 Getting photos onto the Mac (intake)

The app does not talk to the iPhone directly. Supported transfer paths, in recommended order:

1. **AirDrop** — the fastest path. It sends the edited rendition of edited photos and preserves
   EXIF. Files land in `~/Downloads`.
2. **iCloud Photos → Photos.app** — drag photos from Photos.app into the app's drop zone, or
   export them to a folder first.
3. **Image Capture** over USB, for bulk transfer.

The drop zone accepts individual files and whole folders, recursively. Accepted formats:

- **Supported:** HEIC/HEIF, JPEG, PNG.
- **Rejected with a clear message:** DNG/ProRAW and other RAW formats.

### 3.2 Workflow A — Procedure case (Feature 1)

1. **Create a case.** On the Library screen, choose "New procedure case" and enter:
   - **Patient name** — existing patients are suggested as you type.
   - **Procedure / site name** — for example `implant maxillary right first molar`.
2. **Set the reference.** Drop the dentist-edited pre-op photo.
   - It is stored as the case reference and **locked**.
   - Replacing it is an explicit action ("Replace reference…") that re-processes every photo in
     the case.
3. **Add photos.** Drop intra-op and post-op originals at any time, across sessions. For each
   photo the server runs:
   1. decode
   2. align to the reference (see §4.5.1)
   3. color-match (see §4.5.2)
   4. render in the reference frame
4. **Review.** Each photo shows a confidence badge.
   - Photos below the threshold are marked **Needs review**.
   - The user opens the compare view (onion-skin opacity slider and split slider) and fixes the
     alignment manually by dragging to translate, scrolling or pinching to scale, and
     shift-dragging to rotate. A two-point mode is also available: click the same two landmarks on
     the reference and on the photo.
   - Manual edits are saved non-destructively.
5. **Stages.** Each photo gets a stage, which the user can edit:
   - The reference is `pre_op`.
   - Let `D0` be the calendar date of the earliest non-reference photo. Photos captured on `D0`
     are `intra_op`. Photos captured after `D0` are `post_op`.
6. **Sequence.** The user selects 2–10 photos; the default is all photos in capture order. The
   app renders a horizontal, seamless, equal-height sequence image.
7. **Export.** One click writes:
   - all processed individual photos,
   - the sequence image,
   into `<Library>/<Patient>/<Case>/export/`, using the naming rules in §4.9. The user can then
   reveal the folder in Finder or download a zip.

### 3.3 Workflow B — Composite case (Feature 2)

1. **Create a case.** Choose "New composite case" and enter the patient name and a custom tag
   (for example `Comprehensive oral evaluation and full mouth records`).
2. **Add photos.** Drop the nine photos at once. More than nine is allowed; extra photos go to an
   "unassigned" tray.
3. **Assign slots.**
   - **Classifier trained:** the server computes embeddings, predicts slot probabilities, and
     assigns photos to slots with the Hungarian algorithm (§4.6).
   - **Classifier untrained (cold start):** photos are assigned in EXIF capture order according
     to the shooting order configured in Settings.
   - Low-confidence slots are highlighted.
4. **Normalize.** For each photo, in this order:
   1. Apply the per-slot flip from the protocol (mirror shots).
   2. Level the image (occlusal plane or symmetry axis).
   3. Smart-crop to the 3:2 cell.
   4. Unify color to the baseline photo (default: frontal occlusion; selectable).
5. **Review.** The live 3 × 3 preview supports:
   - **drag-to-swap** between slots, and to and from the tray;
   - a **per-cell editor** for pan, zoom, rotate, flip, and reset.
6. **Export** writes one high-resolution composite (§4.9) to the case `export/` folder.
7. **Learn.** On export, the final slot assignment counts as confirmed labels.
   - Each photo's embedding and slot label are stored as a training sample (no image data).
   - The classifier retrains in the background.

### 3.4 Intake channels evaluated and deferred

| Channel | Verdict | Reason |
|---|---|---|
| iPhone PWA (process on the phone) | Deferred | Stakeholder chose Mac-only. iOS Safari limits apply (canvas area, storage eviction, HEIC quirks). |
| Telegram bot | Deferred | Needs an always-on host. Photos must be sent as "files" to avoid compression. Photos pass through Telegram's cloud (bots are not end-to-end encrypted). |
| Discord bot | Deferred | Same issues as Telegram, plus upload size limits on free accounts. |
| Email attachment | Rejected | Needs a mail-polling server; slow, poor UX, privacy exposure. |
| OpenClaw skill | Deferred (extension point) | OpenClaw is an MIT-licensed local agent that bridges chat apps (Telegram, iMessage, …) to an LLM. It needs LLM API spend or a local model, and its skill marketplace has a supply-chain risk. |

**Extension path.** Because the core is a Python library with a CLI (`dio process`, §4.10) and a
local HTTP API, a future OpenClaw skill or Telegram adapter can run on the same Mac and call it
without changes to the core.

A **Downloads-folder watcher** (auto-import of new AirDrop arrivals) is a post-MVP candidate.

**Deployment impact:** there is no server-side intake, so there is nothing to host.

## 4. Backend (stage 3.2)

### 4.1 Stack

| Concern | Choice |
|---|---|
| Runtime | Python **3.12** (`requires-python = ">=3.12"`), environment and Python provisioning by `uv` |
| Web | FastAPI + Uvicorn, Pydantic v2 |
| CLI | Typer |
| Logging | structlog: JSON lines to a rotating file (5 × 5 MB) and human-readable console output; levels DEBUG / INFO / WARNING / ERROR |
| Image I/O | Pillow + `pillow-heif` (HEIC). Pillow `ImageCms` for ICC conversion. EXIF read and write through Pillow. |
| Computer vision | `opencv-python-headless`, numpy, scikit-image, scipy |
| ML | `onnxruntime` (CPU execution provider), scikit-learn (`LogisticRegression`), `scipy.optimize.linear_sum_assignment` |
| Platform paths | `platformdirs` |
| Single instance | `filelock` |
| HTTP client | `httpx` (model download, version check) |
| Build backend | `hatchling`. The built frontend is included as build artifacts. |

All dependencies ship wheels for macOS (arm64 and x86_64), Windows (x86_64), and Linux. The
package itself is pure Python (`py3-none-any`).

### 4.2 Module layout and boundaries

```
src/dental_image_organizer/
├── __init__.py          # __version__ via importlib.metadata
├── __main__.py          # python -m dental_image_organizer
├── cli.py               # Typer app: serve, install-launcher, uninstall-launcher, process, export-case, import-case
├── config.py            # Settings model + load/save (settings.json in user config dir)
├── log.py               # structlog configuration
├── errors.py            # Domain exception hierarchy
├── core/                # PURE: numpy arrays / dataclasses in and out; no filesystem, network, or web imports
│   ├── imageio.py       # decode bytes → RGB uint8 (sRGB), EXIF parse, orientation, encode JPEG + EXIF
│   ├── masks.py         # tooth / gingiva masks (Lab thresholds + morphology)
│   ├── align.py         # feature matching + RANSAC similarity + ECC refinement + confidence
│   ├── color.py         # Lab quantile mapping (F1), Reinhard transfer (F2)
│   ├── geometry.py      # per-view leveling, flip, smart crop
│   ├── transform.py     # normalized 2×3 affine helpers (compose, invert, to pixel space)
│   ├── layout.py        # sequence strip + 3×3 composite rendering
│   └── naming.py        # filename rules, sanitization, NFC, collision suffixes
├── ml/
│   ├── embedder.py      # DINOv2-S ONNX session, preprocessing, batching
│   ├── classifier.py    # slot classifier: train / predict_proba / persistence
│   ├── assignment.py    # Hungarian assignment + EXIF-order fallback
│   └── model_store.py   # pinned model URL + SHA-256, download, verify, cache
├── storage/
│   ├── library.py       # library root, patient + case folders, case.json read/write (atomic), per-case locks
│   ├── schema.py        # Pydantic models for case.json, patient.json (versioned)
│   ├── cache.py         # rendered preview / thumbnail cache keyed by edit hash
│   └── archive.py       # case zip export / import
├── services/
│   ├── jobs.py          # in-process job manager (ThreadPoolExecutor), progress events
│   ├── procedure.py     # Feature 1 orchestration
│   ├── composite.py     # Feature 2 orchestration
│   ├── export.py        # batch export, zip packaging
│   └── updates.py       # opt-out PyPI version check
├── api/
│   ├── app.py           # FastAPI factory, middleware (TrustedHost, Origin check), static frontend mount
│   ├── routes/          # health, settings, patients, cases, photos, render, export, archive, jobs, model, classifier, system
│   └── models.py        # request / response schemas
├── launcher/
│   └── macos.py         # osacompile-based .app generation
└── web/                 # built frontend assets (gitignored; produced by CI before packaging)
```

**Rule:** `core/` must not import from `storage/`, `services/`, `api/`, or perform I/O. This is
enforced by an import-linter contract in CI. It keeps the algorithms unit-testable and reusable
by future adapters (OpenClaw, Telegram, CLI).

### 4.3 Server process model

- `dio serve`:
  - Binds **`127.0.0.1` only**.
  - Uses port 8765 by default. If the port is busy, it scans 8765–8775.
  - Opens the default browser.
- **Single instance.**
  - A `filelock` in the app data directory guards the library.
  - If an instance is already running, `dio serve` reads the recorded port and opens the browser
    to it instead of starting a second server.
- `dio serve --detach` starts the server as a detached background process and returns
  immediately. This is used by the launcher:
  - POSIX: `start_new_session=True`.
  - Windows: `DETACHED_PROCESS`.
- **Idle shutdown.**
  - The UI sends `POST /api/heartbeat` every 30 s.
  - The server exits when there has been no heartbeat for `idle_shutdown_minutes` (default 30)
    and no job is running.
  - The UI also has a **Quit** button (`POST /api/system/shutdown`).
- **Concurrency.**
  - CPU-heavy work runs in a `ThreadPoolExecutor` with `max_workers = min(4, os.cpu_count())`.
    OpenCV and onnxruntime release the GIL.
  - `cv2.setNumThreads(1)` is set per worker to avoid oversubscription.
  - Writes to a case are serialized by a per-case `threading.Lock`.

### 4.4 Image decoding and color management

1. **Format detection.** Sniff the format by magic bytes, not by extension:
   - HEIC/HEIF: ISO-BMFF `ftyp` brands.
   - JPEG: `FFD8FF`.
   - PNG: `89504E47`.
   - Anything else raises `UnsupportedFormatError`.
2. **Orientation.** Apply EXIF orientation (`ImageOps.exif_transpose`).
3. **Color space.** Convert the embedded ICC profile (iPhone uses **Display P3**) to **sRGB** with
   `ImageCms`, perceptual intent. Images without a profile are treated as sRGB.
4. **Working copy.** Analysis uses a working copy with a long edge of **1600 px**. Final renders
   are produced from the full-resolution original in a single `cv2.warpAffine`. A 48 MP image is
   about 144 MB as RGB uint8; float conversion happens only on working copies.
5. **Capture time.**
   - Read `DateTimeOriginal` and, if present, `OffsetTimeOriginal`.
   - If `DateTimeOriginal` is missing, fall back to the file modification time,
     `captured_at_source = "file_mtime"`, and show a UI warning.
6. **Output encoding.**
   - JPEG at quality **92**, 4:4:4 chroma, with an embedded sRGB ICC profile.
   - Preserved EXIF: `DateTimeOriginal`, `OffsetTimeOriginal`, `Make`, `Model`, lens tags.
   - Orientation is reset to 1.
   - **GPS tags are stripped by default** (setting `strip_gps`).

### 4.5 Feature 1 algorithms

#### 4.5.1 Alignment to the reference

The reference is the dentist-edited rendition: cropped, rotated, and color-graded. The new photo is
a raw, wider frame. The goal is a similarity transform (rotation, uniform scale, translation, and
optionally a mirror) that maps the new photo into the reference frame.

1. **Preprocess.** Convert both working copies to Lab and apply CLAHE (clip limit 2.0, 8 × 8
   tiles) to L, which reduces exposure differences.
2. **Detect features.**
   - Use SIFT (OpenCV main module) with up to 4000 keypoints per image.
   - Weight detection toward teeth by passing a dilated tooth mask (§4.7.1) as the detection
     mask, falling back to the full image when the mask is empty.
   - Teeth are the most stable structures across surgical stages, while soft tissue changes
     (flaps, blood, sutures).
3. **Match.** Brute-force L2 kNN (k = 2), Lowe ratio test at 0.75, then a cross-check.
4. **Estimate.** Run `cv2.estimateAffinePartial2D` with RANSAC (reprojection threshold 3 px at
   working resolution, 5000 iterations, confidence 0.995).
   - If the result has fewer than 25 inliers, retry with the horizontally mirrored photo and keep
     the better result.
5. **Refine.** Run `cv2.findTransformECC` with `MOTION_EUCLIDEAN`, scale fixed from step 4, on
   Gaussian-blurred L channels at 800 px, inside a mask of tooth plus gingiva in the reference.
6. **Confidence.** `c = clamp01(0.5 · min(1, inliers / 80) + 0.5 · max(0, ecc_cc))`.
   - `c ≥ 0.6` → `status = "ok"`.
   - `0.35 ≤ c < 0.6` → `status = "needs_review"`; the transform is applied but flagged.
   - `c < 0.35` or no transform → `status = "needs_review"`, and the photo is placed with a
     fallback that center-fits it to the reference aspect ratio.
7. **Sanity limits.** Results outside these limits are rejected and handled as in the `c < 0.35`
   case:
   - scale ∈ [0.2, 5.0] relative to the working copy;
   - rotation ∈ [−45°, 45°].

The resulting transform is stored in **normalized coordinates** (§4.8), so it applies to any
render resolution.

#### 4.5.2 Color matching to the reference

The reference carries the dentist's color grading, which is non-linear (iPhone Photos edits).
After alignment, pixel correspondences exist:

1. Warp the new photo's working copy into the reference frame and take the overlapping valid
   pixels.
2. Convert both to Lab (float).
3. For each channel L, a\*, b\*, fit a **monotone piecewise-linear mapping** through 17 quantile
   pairs (p0, p6.25, …, p100) of the corresponding pixels.
4. **Robustness loop (2 iterations).** Apply the mapping, compute the per-pixel ΔE76 to the
   reference, exclude the top 20% residual pixels (regions changed by surgery), and refit.
5. **Limits.**
   - Each mapped quantile may move at most ±25 L units and ±15 a\*/b\* units from identity.
   - Mappings are forced monotone.
6. Store the 3 × 17 control points as color params (`method = "lab_quantile_v1"`).

### 4.6 Feature 2 — AI slot classification

**Slots** (row-major):

- `upper_right`, `upper_occlusal`, `upper_left`
- `occlusion_right`, `occlusion_front`, `occlusion_left`
- `lower_right`, `lower_occlusal`, `lower_left`

**Embeddings.**

- Model: DINOv2 ViT-S/14, exported to ONNX and dynamically quantized to int8 (≈ 25 MB;
  Apache-2.0).
- Preprocessing:
  1. Rotate portrait images to landscape.
  2. Resize to 336 × 224.
  3. Normalize with ImageNet mean and std.
- Output: the CLS token (384-d), L2-normalized.
- Inference: onnxruntime CPU EP in batches of 9. Apple Silicon CoreML EP is an optional
  optimization and is off by default, because it cannot be tested on the Windows development
  host.

**Model provisioning (`model_store.py`).**

- The model URL (a GitHub Release asset) and its SHA-256 are pinned in code.
- The first time AI classification is needed, the UI asks for consent:
  "Download AI model (~25 MB) from GitHub?"
- The download is verified against the hash, then cached in the user cache directory.
- If the user declines or the download fails, the app uses the EXIF-order fallback and says so.

**Classifier.**

- Algorithm: scikit-learn `LogisticRegression` (multinomial, `C = 1.0`,
  `class_weight = "balanced"`) on the embeddings.
- **Trained** state: requires ≥ 3 confirmed samples for **every** slot. Until then, the
  classifier is **untrained** and the fallback is used.
- Persistence:
  - samples → `classifier/samples.npz` (embeddings float32, slot labels, case ids, timestamps);
  - model → `classifier/model.json` (coefficients, intercepts, class order, embedding model
    hash).
- Samples carry the embedding-model hash. If the model changes, old samples are ignored and the
  user is told to re-confirm a few cases.
- Retraining runs in the background after each composite export; it takes milliseconds.

**Assignment.**

- Cost matrix `−log(p_ij + 1e-6)` for photo `i` and slot `j`, solved with
  `linear_sum_assignment`.
- More than 9 photos: the unassigned photos go to the tray.
- Fewer than 9: the remaining slots stay empty.
- An assigned slot is flagged low-confidence when `p < 0.5`.

**Fallback (cold start).**

- Sort photos by capture time and map them onto `settings.shooting_order`. The default is
  row-major slot order. The dentist sets their real shooting order in Settings.

### 4.7 Feature 2 — normalization

#### 4.7.1 Masks

- **Tooth mask.** On the Lab working copy, keep pixels where L is above the 55th percentile,
  `a* < 14`, and `4 < b* < 45`. Then apply morphological open and close with an elliptical kernel
  sized to `min(h, w) / 110`, and keep connected components larger than 0.2% of the image area.
- **Gingiva mask.** Pixels with `a* > 15` and `L > 25`, excluding the tooth mask, morphologically
  cleaned.
- **Validation.** Both are validated by unit tests on synthetic images. Thresholds are constants in
  `masks.py` and are tuned on local golden photos (§8.3).

#### 4.7.2 Per-slot geometry

1. **Flip.** Apply the per-slot `flip` from the shooting protocol (`none | horizontal | vertical |
   rotate_180`). The user can toggle it per cell.
2. **Leveling**, with the angle clamped to ±15° and applied only when the estimate is confident:

| Slot group | Method |
|---|---|
| `occlusion_*` | Fit a line (RANSAC) through the inter-arch boundary points: per column, the transition between upper and lower tooth regions. Rotate the line to horizontal. |
| `upper_occlusal`, `lower_occlusal` | Search rotations in −15°…15° (0.5° steps) for the maximum left-right mirror correlation of the tooth mask. Rotate the symmetry axis to vertical. |
| `upper_left/right`, `lower_left/right` | Fit a line through the occlusal edge of the visible arch (the lower edge of the tooth mask for upper views, the upper edge for lower views). Rotate it to horizontal. |

3. **Smart crop.**
   1. Take the bounding box of the tooth mask, using the 2nd–98th percentile of mask pixel
      coordinates.
   2. Expand it by 12% per side.
   3. Fit it to a 3:2 aspect, centered.
   4. Clamp it to the image bounds.
   5. Limit the zoom to ≤ 1.3 × the "fit whole image" scale. This avoids cropping too tight when
      retractors hide teeth.

#### 4.7.3 Color unification

- The **baseline** photo (default `occlusion_front`) is left unchanged.
- Every other photo gets **Reinhard transfer** in Lab: per channel, map the mean and standard
  deviation, computed over its tooth ∪ gingiva mask, to the baseline's.
  - Strength is 0.8 (a blend with identity).
  - Each mean shift is clamped to ±12 L units and ±8 a\*/b\* units.
- Params are stored as `method = "lab_reinhard_v1"`.

### 4.8 Edit model (non-destructive)

Originals are never modified. Each photo's processing result is a small parameter record:

- `transform`: a 2 × 3 affine matrix in **normalized coordinates**. Input coordinates are divided
  by the input width, and output coordinates by the output width. This makes the transform valid
  for preview and full resolution alike. Flips are folded into the matrix.
- `output_aspect` and `output_size`: the output frame.
  - F1: the reference size.
  - F2: the cell size.
- `color`: `{method, params}`.
- `source`: `auto` or `manual`. Manual edits are never overwritten by auto re-processing unless
  the user chooses "Reset to auto".
- `confidence`, `status` (`ok | needs_review | failed`), `message`, and
  `algorithm_version` (for example `align-1`). The algorithm version allows selective
  re-processing after upgrades.

**Rendering.** The edit record is applied to an original as follows:

1. decode
2. `warpAffine` (Lanczos4 for full-resolution output, linear for previews)
3. color mapping
4. encode

**Preview cache.**

- Renders are cached at three sizes: thumbnail (320 px), preview (1600 px), and full.
- Cache key: `sha256(original_sha256 + edit_record_json + size)`.
- Cache location: the user cache directory. The cache is never written into the library.

### 4.9 Naming rules

- **Individual photo:** `{patient}_{title}_{YYYYMMDD}_{HHMM}.jpg`, from `captured_at` (local
  time).
- **Procedure sequence:** `{patient}_{title}_Procedure_{YYYYMMDD}.jpg`, using the generation date
  (local).
- **Composite:** `{patient}_{tag}_Composite_{YYYYMMDD}.jpg`, using the most frequent capture date
  among the assigned photos.

**Sanitization** is applied to each component (`patient`, `title`, `tag`):

1. Unicode **NFC** normalization. macOS uses NFD in filenames and Windows uses NFC, so this keeps
   names comparable.
2. Trim, and collapse runs of whitespace to a single space.
3. Remove `/ \ : * ? " < > |` and control characters.
4. Truncate each component to 80 characters, and the whole filename to 200 UTF-8 bytes (macOS
   allows 255).

**Collisions** within an export folder get `_2`, `_3`, … before the extension. Existing files are
never overwritten silently: the export screen offers "Replace previous export" or "Keep both".

### 4.10 CLI

| Command | Purpose |
|---|---|
| `dio serve [--port N] [--no-browser] [--detach]` | Start the local server (§4.3) |
| `dio install-launcher` / `dio uninstall-launcher` | macOS only: create or remove `~/Applications/Dental Image Organizer.app` (§7.3). Other OSes print "not supported". |
| `dio process --reference REF --out DIR PHOTOS...` | Headless Feature 1. This is the extension point for future adapters. |
| `dio composite --out DIR PHOTOS...` | Headless Feature 2 |
| `dio export-case CASE_DIR ZIP` / `dio import-case ZIP` | Case archive round trip |
| `dio --version` | Print the version |

`dental-image-organizer` is installed as an alias of `dio`. The PyPI name `dio` is taken, so the
**package** is named `dental-image-organizer`.

### 4.11 Storage layout and schema

**Library root.** Default `~/DentalImageOrganizer/`, configurable. Visible in Finder and backed up
by Time Machine.

```
~/DentalImageOrganizer/
├── Alex Wang/                              # patient folder (sanitized display name; suffix " (2)" on clash)
│   ├── patient.json                        # {schema_version, id, name, created_at}
│   ├── 20261016 implant maxillary right first molar/
│   │   ├── case.json
│   │   ├── originals/<sha256[:16]>.<ext>   # immutable originals, deduplicated by SHA-256
│   │   └── export/                         # user-facing outputs
│   └── 20261016 Comprehensive oral evaluation/
└── .trash/                                 # deleted cases (restorable)
```

**App data directories** (resolved by `platformdirs`):

| Directory | Contents |
|---|---|
| config | `settings.json` |
| data | `classifier/`, `instance.lock`, `instance.json` (port, pid) |
| cache | render cache, model file |
| log | `dio.log` |

**`case.json` (schema_version 1)**, as Pydantic models in `storage/schema.py`:

```json
{
  "schema_version": 1,
  "id": "2f6c…(uuid4)",
  "type": "procedure",
  "patient_id": "…",
  "title": "implant maxillary right first molar",
  "created_at": "2026-10-16T14:30:00+08:00",
  "updated_at": "2026-10-16T15:02:11+08:00",
  "photos": [
    {
      "id": "…",
      "sha256": "…",
      "file": "originals/9b1f0c2d44aa01ee.heic",
      "source_name": "IMG_1234.HEIC",
      "width": 4032, "height": 3024,
      "captured_at": "2026-10-16T14:30:12+08:00",
      "captured_at_source": "exif",
      "role": "reference",
      "stage": "pre_op",
      "slot": null,
      "edit": {
        "transform": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        "output_size": [2400, 1600],
        "color": {"method": "identity", "params": {}},
        "source": "auto",
        "confidence": 1.0,
        "status": "ok",
        "message": null,
        "algorithm_version": "align-1"
      }
    }
  ],
  "sequence": {"photo_ids": [], "gap_px": 0},
  "composite": null,
  "exports": [{"file": "export/…jpg", "kind": "single", "created_at": "…"}]
}
```

Field rules:

- `type = "composite"` cases use:
  - `role = "member"`;
  - `slot` ∈ the slots in §4.6, or `null` for the tray;
  - `"composite": {"baseline_photo_id", "cell_size": [1200, 800], "gap_px": 0, "gap_color": "#000000"}`.
- `stage` ∈ `pre_op | intra_op | post_op`, for procedure cases only.

Writes are **atomic**: write `case.json.tmp`, `fsync`, then `os.replace`. Readers validate with
Pydantic. Unknown future `schema_version` values raise `StorageError` with an upgrade message.

**Archive.** A case zip contains `patient.json`, `case.json`, and `originals/`. Exports and caches
are regenerated. On import, an existing case with the same id prompts the user to skip or import
as a copy.

### 4.12 HTTP API (prefix `/api`)

| Method & path | Purpose |
|---|---|
| `GET /health` | `{status, version}` |
| `POST /heartbeat` | Keep-alive for idle shutdown |
| `POST /system/shutdown` | Quit |
| `POST /system/reveal` | Reveal a library path in Finder / Explorer (path must be inside the library) |
| `GET / PUT /settings` | Read / update settings |
| `GET /patients` | Patients with case summaries |
| `POST /cases` | Create a case `{type, patient_name, title}` |
| `GET / PATCH / DELETE /cases/{id}` | Read, update (title, sequence selection, composite settings), soft-delete to `.trash` |
| `POST /cases/{id}/photos` | Multipart upload (multiple files); returns `job_id`; processing is async |
| `PUT /cases/{id}/reference` | Set or replace the reference (procedure); triggers re-processing |
| `PATCH /cases/{id}/photos/{pid}` | Update stage, slot, flip, manual `edit`, or reset to auto |
| `DELETE /cases/{id}/photos/{pid}` | Remove a photo |
| `GET /cases/{id}/photos/{pid}/image?variant=original\|processed&size=thumb\|preview\|full` | Image bytes (cached) |
| `PUT /cases/{id}/composite/slots` | Full slot map `{slot: photo_id}` (drag-to-swap) |
| `POST /cases/{id}/composite/assign` | Re-run automatic assignment |
| `GET /cases/{id}/render/sequence?size=…` / `GET /cases/{id}/render/composite?size=…` | Preview renders |
| `POST /cases/{id}/export` | `{include: ["singles","sequence","composite"], conflict: "replace"\|"keep_both"}` → `job_id` |
| `GET /cases/{id}/export/{job_id}/zip` | Download the exported files as a zip |
| `GET /cases/{id}/archive` / `POST /archive/import` | Case archive export / import |
| `GET /jobs/{job_id}` / `GET /jobs/{job_id}/events` | Job status / Server-Sent Events progress stream |
| `GET /model/status` / `POST /model/download` | AI model availability / consented download (job) |
| `GET /classifier/status` / `POST /classifier/retrain` | Sample counts per slot, trained flag / manual retrain |

TypeScript types for the frontend are generated from the FastAPI OpenAPI schema with
`openapi-typescript`. CI fails if the generated file is stale.

### 4.13 Errors, logging, and security

**Exceptions.** All domain errors derive from `DioError`:

- `UnsupportedFormatError`
- `CorruptImageError`
- `CaseNotFoundError`
- `PhotoNotFoundError`
- `StorageError`
- `ModelUnavailableError`
- `ModelIntegrityError`
- `InvalidEditError`

A FastAPI exception handler maps each error to an HTTP status and returns
`{"error": {"code", "message", "details"}}`. Unexpected exceptions are logged with a stack trace
and return 500 with a generic message.

**Batch isolation.** One bad photo never aborts a batch. The photo is recorded with
`status = "failed"` and a message, and the job reports partial success.

**Alignment is not an exception.** Low confidence yields `needs_review`, never an exception.

**Logging.**

- structlog with `case_id` and `photo_id` context.
- **Patient names and titles are never logged.**
- DEBUG logs include algorithm metrics (inliers, ECC cc, angles) for tuning.

**Security.**

- Bind to `127.0.0.1` only.
- `TrustedHostMiddleware` allows only `localhost` and `127.0.0.1`. Together with the Origin check
  below, this mitigates DNS rebinding.
- State-changing requests must carry an `Origin` header that matches the server origin.
- `/system/reveal` and all file access are confined to the library root after path resolution
  (no traversal).
- Uploads are limited to 200 MB per file, and decoded pixel count is capped at 200 MP
  (decompression-bomb guard).

**Network.**

- No telemetry.
- Only two outbound calls:
  1. the model download (explicit consent);
  2. the version check against `https://pypi.org/pypi/dental-image-organizer/json`, at most once
     per day, disclosed in Settings, opt-out.

**Deployment impact of the backend:** a pure-Python wheel; no compiled code of our own; all heavy
dependencies come as prebuilt wheels, so `uv tool install` needs no compiler on the user's Mac.

## 5. Frontend (stage 3.3)

### 5.1 Stack

- React 19, TypeScript (strict), Vite.
- Tailwind CSS v4.
- TanStack Query for server state; local UI state stays in components.
- dnd-kit for the 3 × 3 board, tray, and timeline drag-and-drop.
- Pan, zoom, and rotate use custom pointer-event handling that produces the same normalized 2 × 3
  matrix as the backend (`frontend/src/lib/transform.ts`, unit-tested against shared fixtures).
- Vitest for tests; ESLint and Prettier.
- UI strings live in `frontend/src/strings/en.ts`, ready for i18n.

### 5.2 Screens

| Screen | Content |
|---|---|
| **Library** | Patient list with search; case cards (type, title, date, thumbnails, needs-review count); "New procedure case" and "New composite case" |
| **Procedure case** | Reference panel (locked badge, "Replace reference…"); timeline of photos grouped by stage with confidence badges; drop zone; **Compare** view (onion-skin opacity slider, split slider, manual-align tool with free and two-point modes, Reset to auto); **Sequence builder** (select and reorder, preview); Export |
| **Composite case** | 3 × 3 board with drag-to-swap and tray; low-confidence highlighting; **Cell editor** (pan, zoom, rotate, flip toggle, reset); baseline selector; live composite preview; Export |
| **Export dialog** | What to include; conflict policy; progress (SSE); result list with "Reveal in Finder" and "Download zip" |
| **Settings** | Library location, JPEG quality, strip GPS, composite cell size and gap, shooting order and per-slot flips, idle shutdown, update check, AI model status, classifier sample counts |

### 5.3 Interaction details

- **Drop zone.** Accepts files and folders, using `DataTransferItem.webkitGetAsEntry` for recursive
  folders. Files upload in parallel, with a concurrency of 3.
- **Manual adjustments** are applied immediately as CSS transforms on the 1600 px preview for
  instant feedback. On release, the edit is committed with `PATCH` and the server re-renders.
- **Heartbeat** runs every 30 s. A Quit button sits in the header.
- **Accessibility.** All actions are reachable by keyboard: arrow keys nudge the transform, and
  `[` / `]` rotate. Focus rings are visible, and contrast is AA.

### 5.4 Build and packaging

- During development, `npm run dev` (Vite) proxies `/api` to `http://127.0.0.1:8765`.
- `npm run build` outputs to `src/dental_image_organizer/web/`. That folder is gitignored, and
  hatchling includes it as build artifacts.
- FastAPI serves it as static files with an SPA fallback to `index.html`.
- **Deployment impact:** Node is needed only by developers and CI. **End users never install
  Node.**

## 6. Repository Structure (stage 3.4)

```
Dental-Image-Organizer/
├── README.md
├── LICENSE                      # MIT
├── .gitignore                   # includes privacy guard for image files
├── .gitattributes               # LF everywhere (Windows dev → macOS shell scripts)
├── .editorconfig
├── pyproject.toml               # project metadata, deps, hatchling, ruff, mypy, pytest, import-linter
├── uv.lock
├── doc/
│   ├── requirements.md          # English requirements (original Chinese HTML is not published)
│   ├── research/                # reference-app analysis
│   └── superpowers/
│       ├── specs/               # design specs (this file)
│       └── plans/               # implementation plans
├── src/dental_image_organizer/  # backend package (layout in §4.2)
├── frontend/
│   ├── package.json  vite.config.ts  tsconfig.json
│   └── src/{api,components,features/{library,procedure,composite,settings},lib,strings}/
├── scripts/
│   ├── install.sh               # end-user installer (macOS)
│   └── export_dinov2_onnx.py    # maintainer-only: export + quantize the embedding model
├── tests/
│   ├── unit/                    # core/, ml/, storage/ (synthetic images generated in conftest)
│   ├── integration/             # API flows with TestClient + temp library
│   ├── golden/                  # marked @golden; reads local_fixtures/ (skipped when absent)
│   └── fixtures/synthetic/      # optional committed CC0 / generated images
├── local_fixtures/              # gitignored: real, de-identified photos for local tuning only
└── .github/workflows/
    ├── ci.yml
    └── release.yml
```

**Privacy guard.** `.gitignore` ignores `*.jpg *.jpeg *.png *.heic *.heif *.dng` everywhere,
except in `tests/fixtures/synthetic/`, `doc/assets/`, and `frontend/public/`. This makes it hard
to commit a patient photo by accident.

## 7. Deployment (stage 3.5)

### 7.1 Cost

| Item | Service | Cost |
|---|---|---|
| Source, issues | GitHub public repository | $0 |
| CI (ubuntu, windows, macos) | GitHub Actions, public repo | $0 |
| Package hosting | PyPI | $0 |
| Model hosting (≈ 25 MB) | GitHub Release asset | $0 |
| Runtime | User's own Mac | $0 |
| Code signing / notarization | Not used | $0 (optional US$99/yr only if a signed `.dmg` is ever wanted) |

### 7.2 Release pipeline (`release.yml`, on tag `v*`)

1. Build the frontend: `npm ci && npm run build`.
2. Build the wheel and sdist: `uv build`.
3. On `macos-latest`, run `scripts/install.sh` against the built wheel (`DIO_WHEEL=dist/...whl`).
   The smoke test checks:
   - `dio --version`;
   - `dio serve --detach --no-browser`, then `curl /api/health`;
   - `dio install-launcher`, then assert the app bundle exists;
   - shutdown.
4. Publish to PyPI with **Trusted Publishing** (OIDC). No API token is stored anywhere.
5. Create a GitHub Release with notes.

The embedding model is released separately under its own tag (e.g. `model-dinov2s-v1`) by the
maintainer, using `scripts/export_dinov2_onnx.py`. Its URL and SHA-256 are then pinned in
`ml/model_store.py`.

### 7.3 End-user installation (macOS)

```sh
curl -LsSf https://raw.githubusercontent.com/jimwellh/Dental-Image-Organizer/main/scripts/install.sh | sh
```

`install.sh` is POSIX `sh` and idempotent. It:

1. Verifies macOS and the architecture.
2. If `uv` is missing, installs it with the official installer (`https://astral.sh/uv/install.sh`),
   and discloses this in its output.
3. Runs `uv tool install --python 3.12 dental-image-organizer`, or `--upgrade` if it is already
   installed. `uv` downloads a managed Python, so **no system Python is required**.
4. Runs `dio install-launcher`. This uses `osacompile` to create
   `~/Applications/Dental Image Organizer.app`, an AppleScript applet that runs
   `"<absolute path to dio>" serve --detach`, and sets the app icon.
   - The bundle is created locally, so it has no `com.apple.quarantine` attribute and **Gatekeeper
     does not prompt**.
   - The absolute path avoids the minimal `PATH` of Finder-launched apps.
5. Prints usage instructions, the update command, and the uninstall command.

**Daily use.** Click the Dock or Launchpad icon. The server starts in the background, and the
browser opens `http://127.0.0.1:8765`.

**Update.** Run `uv tool upgrade dental-image-organizer`; re-running the install script does the
same. The UI shows a banner when PyPI has a newer version.

**Uninstall.** Run `dio uninstall-launcher && uv tool uninstall dental-image-organizer`. The
library folder is kept.

### 7.4 Development (Windows host)

- `uv sync` installs the backend; `uv run dio serve --no-browser` runs it.
- `cd frontend && npm ci && npm run dev` runs the frontend.
- The app is fully functional on Windows. The only macOS-only command is `install-launcher`.
  Reveal-in-Explorer is supported for development.

### 7.5 CI (`ci.yml`, on push and PR)

- **Python:** `ruff check`, `ruff format --check`, `mypy` (strict for `core/`, `ml/`, `storage/`),
  import-linter, and `pytest` on the matrix {ubuntu, windows, macos} × Python 3.12.
- **Frontend:** `npm ci`, `eslint`, `tsc --noEmit`, `vitest run`, `vite build`, and the OpenAPI
  types-stale check.
- **macOS smoke:** the same install, launcher, and health smoke test as in the release workflow,
  run against a freshly built wheel.

## 8. Testing Strategy

### 8.1 Unit (synthetic, deterministic, committed)

Synthetic "dental-like" scenes are generated procedurally in `conftest.py` (seeded): rows of
bright, tooth-like ellipses with texture on a pink, gingiva-like background, plus noise. No
binary patient data is needed.

- **Alignment.** Apply a known similarity transform (rotation ±12°, scale 0.7–1.4, translation,
  mirror) and a crop to make a "reference." Assert:
  - recovered rotation within **0.5°**;
  - recovered scale within **2%**;
  - recovered translation within **1%** of the width;
  - confidence ≥ 0.6.
  - A negative test (an unrelated image) must yield `needs_review`.
- **Color.**
  - **F1:** apply a known Lab tone curve plus a white-balance shift to the warped image. After
    `lab_quantile_v1`, the mean ΔE76 against the reference must be **< 3**.
  - **Robustness:** a simulated "surgical change" patch must not drag the fit; mean ΔE outside the
    patch must stay < 3.
  - **F2:** after Reinhard transfer, the tooth-region mean ΔE to the baseline must drop by
    ≥ 60%.
- **Masks and geometry.** Synthetic arches with known tilt are leveled to within 1°. Symmetric
  synthetic occlusal arches find the axis within 1°.
- **Transform math.** Compose, invert, and the normalized↔pixel round trip, with property-based
  tests via Hypothesis. A shared JSON fixture is used by both pytest and Vitest to guarantee that
  the frontend and backend matrices agree.
- **Classifier and assignment.** Synthetic clustered embeddings must yield correct assignment.
  Also tested: the cold-start fallback ordering; the low-confidence flag; samples with a different
  model hash are ignored.
- **Naming.** NFC/NFD equivalence (`王小明` as NFD → NFC), sanitization, truncation by bytes,
  collision suffixes, and timezone handling.
- **Storage.** Atomic write (simulated crash leaves the old file intact), schema validation,
  future-version rejection, archive round trip, SHA-256 dedup, and path confinement.
- **Image I/O.** EXIF orientation 1–8, ICC P3→sRGB conversion, missing-EXIF fallback, GPS
  stripping, magic-byte sniffing, and decompression-bomb rejection. HEIC fixtures are generated at
  test time with `pillow-heif`.

### 8.2 Integration

FastAPI `TestClient` with a temporary library directory covers:

1. Create a procedure case → set reference → upload photos → wait for the job → verify the
   statuses → export → verify the files and names.
2. The same flow for a composite case, including the slot swap and the classifier sample written
   on export.
3. Security checks: wrong `Host` → 400; wrong `Origin` on `POST` → 403; path traversal → 400.

### 8.3 Golden (local only)

- `pytest -m golden` runs on real, de-identified photos in `local_fixtures/`, which the dentist
  provides with consent.
- It records alignment and color metrics to a CSV for tuning thresholds.
- It is skipped automatically when the folder is absent, as in CI.

### 8.4 Acceptance

Before each release, a short scripted checklist is executed on a real Mac by the dentist:

1. Install.
2. Launch from the Dock.
3. AirDrop import (HEIC and JPEG).
4. One procedure case with ≥ 4 photos.
5. One composite.
6. Export.
7. Reveal in Finder.
8. Quit.
9. Relaunch.
10. Update.

## 9. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Developer has no Mac | Cross-platform core; macOS CI smoke tests on every PR; dentist acceptance checklist (§8.4); keep macOS-specific code minimal (`launcher/macos.py`, `install.sh`) |
| Surgical changes defeat feature matching | Tooth-weighted detection; RANSAC; confidence gating with `needs_review` and an excellent manual-alignment tool |
| Classifier cold start | EXIF-order fallback with a configurable shooting order; learning from every export; per-slot sample counts shown in Settings |
| Classifier drift after a model update | Samples are tagged with the embedding-model hash; stale samples are excluded |
| HEIC / Display P3 color shifts | ICC → sRGB conversion at decode; sRGB ICC embedded on output |
| Very large photos (48 MP) | Analysis on a 1600 px working copy; a single full-resolution warp at render time; 200 MP decode cap |
| Data loss | Originals immutable; atomic `case.json` writes; soft-delete to `.trash`; zip archive export; library visible to Time Machine |
| Accidental publication of patient photos | Image-type privacy guard in `.gitignore`; synthetic test data only; original requirements document excluded |
| localhost attacks (DNS rebinding, CSRF) | 127.0.0.1 binding, TrustedHost, Origin check, path confinement |

## 10. Out of Scope (MVP)

- Facial photos, smile photos, and radiographs (per the requirements).
- EMR features: notes, insurance, integration with clinic systems (per the requirements).
- iPhone / PWA usage, Telegram / Discord / Email intake, OpenClaw skill (see §3.4).
- RAW / DNG input.
- Signed `.dmg`, notarization, Mac App Store.
- Official Windows end-user support. The app works on Windows for development, but there is no
  Windows launcher or installer.
- Cloud sync and multi-user access.
- UI translation (zh-TW). Strings are prepared for it.

## 11. Open Questions (to confirm with the dentist during acceptance)

These have explicit defaults, so implementation is not blocked. They are flagged for validation.

1. **Default shooting order.** Default: row-major slot order (§4.6). The dentist's real order is
   entered in Settings.
2. **Default per-slot flips for mirror shots.**
   - `upper_occlusal` and `lower_occlusal`: `vertical`.
   - The four buccal corner slots: `horizontal`.
   - All `occlusion_*` slots: `none`.
3. **Stage rule** (§3.2 step 5). The `D0` rule is the default; stages are always editable.
4. **Composite cell size.** Default 1200 × 800 per cell (3600 × 2400 total), gap 0 px.

## 12. Future Extensions

- **Adapters:** an OpenClaw skill and a Telegram bot calling `dio process` / `dio composite` on
  the same Mac; a Downloads-folder watcher for AirDrop arrivals.
- **Stronger algorithms:** LightGlue / SuperPoint matcher (ONNX); SAM-family tooth segmentation;
  a learned orientation and angle-correction model (self-supervised, like the reference app's
  angle model).
- **Platform:** a zh-TW UI; a signed `.dmg`; a Windows launcher; optional CoreML acceleration.
