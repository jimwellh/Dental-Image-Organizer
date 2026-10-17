# Dental-Image-Organizer — Technical Design Spec

- **Date:** 2026-10-16
- **Status:** Approved; written spec reviewed 2026-10-16
- **Inputs:** [Product requirements](../../requirements.md)

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
| F1 alignment model | Similarity, plus a gated homography for perspective, in the MVP | Chosen by the stakeholder. Requirements §2.2-3 ask for perspective, and an edited reference may carry the iPhone crop tool's perspective correction. Deciding now keeps the 3 × 3 edit schema stable (no later migration). |
| UI | React + Vite + TypeScript, served by the local FastAPI server | Rich interaction: drag-to-swap grid, pan/zoom, onion-skin alignment |
| Storage | Local-first, on disk in Finder-visible folders, with zip export/import | Durable (no browser eviction), backed up by Time Machine, portable |
| 9-slot assignment | AI classification (DINOv2 embeddings + a view-group head and a left/right side head) with few-shot learning from user corrections; EXIF-order fallback | Chosen by the stakeholder. No permissively licensed pretrained intraoral view classifier exists (the reference app's heads are unlicensed), and this avoids needing a public training dataset. |
| Distribution | Install script + `uv tool install`; launcher `.app` generated locally | $0 and no Gatekeeper prompt, because local files carry no quarantine attribute |
| Target hardware | Apple Silicon Mac, macOS 14 Sonoma or later | Confirmed by the stakeholder. `onnxruntime` ≥ 1.24 publishes macOS wheels for arm64 only, with a macOS 14 floor. |
| Development host | Windows only (the developer has no Mac) | Everything is cross-platform Python; macOS-specific parts are verified in GitHub Actions macOS runners |
| License | MIT | Permissive and compatible with all dependencies (DINOv2 is Apache-2.0) |
| Language | Code, comments, docs, and UI strings in English | Project convention; English UI for the MVP confirmed by the stakeholder. UI strings are centralized so a zh-TW translation can be added later. |

## 2. Users and Success Criteria

- **Primary user:** a dentist on an Apple Silicon Mac running macOS 14 Sonoma or later, who takes
  clinical photos with an iPhone.
- **Success criteria:**
  1. Importing raw intra-op and post-op photos yields outputs that match the reference framing,
     angle, and color with **no manual retouching** for typical photos.
  2. A 9-photo composite is produced with correct slot placement after drag-to-fix corrections.
     The number of corrections falls as the classifier learns, and the classifier can be trained
     before first use from three past composite sets (§3.5).
  3. Exported files follow the naming rules in [requirements §3](../../requirements.md#3-workflow-demonstration-user-sop).
  4. When automation fails, the user always has a manual fallback (manual alignment, slot swap,
     per-cell adjustment). The app never fails silently.
  5. Patient photos never leave the machine. The only network calls are an opt-in model download,
     an opt-out version check, and updates the user starts.

## 3. Workflow (stage 3.1)

### 3.1 Getting photos onto the Mac (intake)

The app does not talk to the iPhone directly. Supported transfer paths, in recommended order:

1. **AirDrop** — the fastest path. It sends the edited rendition of edited photos and preserves
   EXIF. Files land in `~/Downloads`.
2. **iCloud Photos → Photos.app** — drag photos from Photos.app into the app's drop zone, or
   export them to a folder first.
3. **Image Capture** over USB, for bulk transfer.

The drop zone accepts individual files and whole folders, recursively. Accepted formats:

- **Supported:** HEIC/HEIF, JPEG, PNG, detected by content (§4.4).
- **Skipped, with one summary per drop:** everything else.
  - AirDrop of Live Photos and Image Capture folders bring `.MOV` videos and `.AAE` edit
    sidecars, so a per-file error would be noise.
  - The summary groups skipped files by reason, for example "Skipped 12 files: 9 videos, 3 edit
    sidecars".
  - RAW files are named explicitly: "DNG/ProRAW is not supported; export as HEIC or JPEG."
- **Photos.app drag.** Dragging straight from Photos.app delivers files through macOS file
  promises. Browser support for this must be confirmed on a real Mac (§8.4). The drop zone's help
  text documents the fallback: File → Export in Photos.app, then drop the exported folder.

### 3.2 Workflow A — Procedure case (Feature 1)

1. **Create a case.** On the Library screen, choose "New procedure case" and enter:
   - **Patient name** — existing patients are suggested as you type, each with its optional
     note, such as a chart number (§4.11).
     - If the typed name matches an existing patient, the user picks that patient or creates a
       new one. Two patients may share a name; the note tells them apart.
   - **Procedure / site name** — for example `implant maxillary right first molar`.
2. **Set the reference.** Drop the dentist-edited pre-op photo.
   - It is stored as the case reference and **locked**.
   - Replacing it is an explicit action ("Replace reference…") that re-processes every photo in
     the case.
   - The output resolution defaults to the reference size. A tightly cropped reference can be
     smaller than the detail the new photos carry. When the median `detail_ratio` of the case's
     photos (§4.5.1) is ≥ 1.5, the UI suggests raising the output resolution to 1.5× or 2×
     (`output_scale`, §4.8).
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
     the reference and on the photo. A four-point mode does the same with four landmarks and sets
     a homography (perspective). "Reset perspective" keeps the closest similarity.
   - Manual edits are saved non-destructively.
5. **Stages.** Each photo gets a stage, which the user can edit:
   - The reference is `pre_op`.
   - Let `D0` be the calendar date of the earliest non-reference photo. Photos captured on `D0`
     are `intra_op`. Photos captured after `D0` are `post_op`.
6. **Sequence.** The user selects 2–10 photos; the default is all photos in capture order. The
   app renders a horizontal, seamless, equal-height sequence image.
   - The sequence image is capped at 200 MP (600 MB uncompressed), the decoder's own limit
     (§4.13), so the app can read back every sequence it writes, and at 65,500 px wide (the JPEG
     limit). A longer or larger series is scaled down to fit; individual photos stay at full
     output size.
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
   1. Apply the slot's flip default for mirror shots, learned from confirmed composites
      (§4.7.2).
   2. Level the image (occlusal plane or symmetry axis).
   3. Smart-crop to the 3:2 cell.
   4. Unify color to the baseline photo (default: frontal occlusion; selectable).
5. **Review.** The live 3 × 3 preview supports:
   - **drag-to-swap** between slots, and to and from the tray;
   - a **per-cell editor** for pan, zoom, rotate, flip, and reset.
6. **Export** writes one high-resolution composite (§4.9) to the case `export/` folder.
7. **Learn.** On export, the final slot assignment counts as confirmed labels.
   - Each photo's embedding and slot label are stored as a training sample (no image data).
   - Each slot's final flip is recorded for flip learning (§4.7.2).
   - The classifier retrains in the background.

### 3.4 Patient-level export

Requirements §3.1 step 6 asks for one click that downloads everything for a patient, not just one
case.

1. On the Library screen, each patient has an **Export all** action.
2. It starts one job that runs the case export (§3.2 step 7, §3.3 step 6) for every case of that
   patient, procedure and composite alike. Each case's files are written to that case's
   `export/` folder with the usual naming rules (§4.9) and the chosen conflict policy.
3. When the job finishes, the user can reveal the patient folder in Finder or download one zip,
   `{patient}_Export_{YYYYMMDD}.zip`, with one folder per case, named like the case folder.
4. Cases that fail to export are listed in the job result; the other cases still export
   (batch isolation, §4.13).

### 3.5 Teaching the classifier from past composites (onboarding)

A new install has no training samples, so it would rely on the EXIF-order fallback until three
composites have been confirmed. Teaching closes that gap before first use. A dentist shoots the
nine views in a fixed order, so the photos of a past set are effectively labeled already by their
capture times.

1. **Entry points.**
   - An onboarding prompt in a composite case while the classifier is untrained.
   - "Teach from past composites" in Settings, available at any time.
   - The AI model must be downloaded first (consent, §4.6).
2. **Shooting order.** The dentist confirms their shooting order on a 3 × 3 picker, stored as
   `settings.shooting_order` (§4.6). It drives the pre-assignment in step 3, so it is asked once,
   up front.
3. **Drop a set.** The dentist drops one past set: the nine photos of one patient from one
   session, as a folder or as loose files. The app sorts them by capture time and pre-assigns the
   slots in shooting order.
4. **Confirm.** The 3 × 3 board shows the proposal.
   - The dentist fixes slots by drag-to-swap, sets flips where needed, and confirms.
   - Incomplete sets are allowed; they add samples only for the slots present.
5. **Store samples only.**
   - On confirm, each photo's embeddings (`g(x)`, `g(x̄)`) and slot label are stored with
     `source = "teach"`. Each slot's flip feeds flip learning (§4.7.2).
   - The photos are held only in a temporary session directory in the cache. It is deleted on
     confirm, on cancel, and at startup.
   - Nothing is written to the library, and no patient name is asked for.
6. **Progress.**
   - The screen shows "Set n of 3" and per-slot sample counts.
   - After three confirmed complete sets, every slot has three samples, and the classifier is
     trained (§4.6). Further sets keep improving it.

### 3.6 Intake channels evaluated and deferred

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
| Image I/O | Pillow + `pi-heif` (HEIC decoding). `pi-heif` is the decode-only build of `pillow-heif`, published so that its binary wheels can carry a more permissive license (LGPL-3.0); the app never writes HEIC. `pillow-heif` is a dev-only dependency, used to generate HEIC test fixtures. Pillow `ImageCms` for ICC conversion. EXIF read and write through Pillow. |
| Computer vision | `opencv-python-headless`, numpy, scikit-image, scipy |
| ML | `onnxruntime` (CPU execution provider), scikit-learn (`LogisticRegression`), `scipy.optimize.linear_sum_assignment` |
| Platform paths | `platformdirs` |
| Single instance | `filelock` |
| HTTP client | `httpx` (model download, version check) |
| Build backend | `hatchling`. The built frontend is included as build artifacts. |

All dependencies ship wheels for macOS arm64 (macOS 14+), Windows x86_64, and Linux x86_64. Intel
Macs are not supported: `onnxruntime` stopped publishing macOS x86_64 wheels in 1.24 (the last
was 1.23.x), and pinning an old release would freeze security and bug fixes. The package itself
is pure Python (`py3-none-any`).

### 4.2 Module layout and boundaries

```
src/dental_image_organizer/
├── __init__.py          # __version__ via importlib.metadata
├── __main__.py          # python -m dental_image_organizer
├── cli.py               # Typer app: serve, install-launcher, uninstall-launcher, process, composite, export-case, import-case
├── config.py            # Settings model + load/save (settings.json in user config dir)
├── log.py               # structlog configuration
├── errors.py            # Domain exception hierarchy
├── core/                # PURE: numpy arrays / dataclasses in and out; no filesystem, network, or web imports
│   ├── imageio.py       # decode bytes → RGB uint8 (sRGB), EXIF parse, orientation, encode JPEG + EXIF
│   ├── masks.py         # tooth / gingiva masks (Lab thresholds + morphology)
│   ├── align.py         # feature matching + RANSAC similarity + ECC refinement + gated homography + confidence
│   ├── color.py         # Lab quantile mapping (F1), Reinhard transfer (F2)
│   ├── geometry.py      # per-view leveling, flip, smart crop
│   ├── transform.py     # normalized 3×3 projective helpers (compose, invert, to pixel space, closest similarity)
│   ├── layout.py        # sequence strip + 3×3 composite rendering
│   └── naming.py        # filename rules, sanitization, NFC, collision suffixes
├── ml/
│   ├── embedder.py      # DINOv2-S ONNX session, preprocessing, mirror pairs, batching
│   ├── classifier.py    # group head + side head: train / predict_proba / persistence
│   ├── assignment.py    # Hungarian assignment + EXIF-order fallback
│   └── model_store.py   # pinned model URL + SHA-256, download, verify, cache
├── storage/
│   ├── library.py       # library root, patient + case folders, case.json read/write (atomic), per-case locks
│   ├── schema.py        # Pydantic models for case.json, patient.json (versioned)
│   ├── cache.py         # rendered preview / thumbnail cache keyed by edit hash, LRU size cap
│   └── archive.py       # case zip export / import
├── services/
│   ├── jobs.py          # in-process job manager (ThreadPoolExecutor), progress events
│   ├── procedure.py     # Feature 1 orchestration
│   ├── composite.py     # Feature 2 orchestration
│   ├── export.py        # case and patient-level batch export, zip packaging
│   ├── teach.py         # teaching sessions from past composites (§3.5), flip learning
│   └── updates.py       # opt-out PyPI version check, in-app update (§7.3)
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
  - It first tries the port recorded in `instance.json` by the previous run. The origin then stays
    the same across restarts (in-app update, §7.3; relaunch from the Dock), so an open page that
    polls `/api/health` reconnects to the restarted server.
  - Opens the default browser.
- **Single instance.**
  - A `filelock` in the app data directory guards the library.
  - If an instance is already running, `dio serve` reads the recorded port and opens the browser
    to it instead of starting a second server.
- `dio serve --detach` starts the server as a detached background child and returns once it is
  ready. This is used by the launcher:
  - The child's `stdin`, `stdout`, and `stderr` are redirected to `subprocess.DEVNULL`; it logs
    only to the rotating log file. AppleScript's `do shell script` returns only after every
    process holding its output pipes has closed them, so a child that inherited the pipes would
    leave the Dock launcher hanging ("not responding").
  - POSIX: `start_new_session=True`.
  - Windows: `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`.
  - The parent polls `GET /api/health` for up to 20 s, then opens the browser (unless
    `--no-browser`) and exits 0. On timeout it exits non-zero and prints the log file path.
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
   - Anything else raises `UnsupportedFormatError`. On upload, such files are reported as skipped
     (§3.1) rather than stored.
2. **Orientation.** Apply EXIF orientation (`ImageOps.exif_transpose`).
3. **Color space.** Convert the embedded ICC profile (iPhone uses **Display P3**) to **sRGB** with
   `ImageCms`, perceptual intent. Images without a profile are treated as sRGB.
4. **Working copy.** Analysis uses a working copy with a long edge of **1600 px**. Final renders
   are produced from the full-resolution original in a single `cv2.warpPerspective`. A 48 MP image is
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

The reference is the dentist-edited rendition: cropped, rotated, color-graded, and possibly
perspective-corrected (the iPhone Photos crop tool has vertical and horizontal perspective
sliders). The new photo is a raw, wider frame, and the camera tilt usually differs between
sessions. The goal is a transform that maps the new photo into the reference frame:

- first a **similarity** (rotation, uniform scale, translation, and optionally a mirror);
- then, when the evidence supports it, a **homography** that also corrects perspective
  (requirements §2.2-3). A similarity cannot turn a keystoned (trapezoid) view back into a
  rectangle.

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
   - **Tight-crop second pass.**
     - The dentist may crop the reference tightly. If the reference covers less than half of
       the photo's width (estimated scale > 2 from the photo's working copy to the reference's),
       the treatment area is only a few hundred pixels wide in the 1600 px working copy. Matching
       quality and precision then drop.
     - In that case, crop the full-resolution photo to the predicted region (the reference frame
       mapped back into the photo, plus a 15% margin), resize the crop to a 1600 px long edge, and
       repeat steps 2–4 on it.
     - The crop's offset and scale are composed into the result. The second pass is kept when it
       has more inliers, and steps 5–6 then work on the same crop.
   - **Detail ratio.** The number of full-resolution photo pixels per full-resolution reference
     pixel, over the matched region, is stored as `edit.detail_ratio`. It drives the
     output-resolution suggestion (§3.2 step 2).
5. **Refine.** Run `cv2.findTransformECC` with `MOTION_EUCLIDEAN`, scale fixed from step 4, on
   Gaussian-blurred L channels at 800 px, inside a mask of tooth plus gingiva in the reference.
6. **Perspective (gated homography).** A homography is exact only for a plane, and the dental
   arch is curved. An unconstrained fit can therefore warp regions far from the matched features,
   so a homography is attempted only when the data can constrain it, and kept only when it
   clearly helps.
   1. **Preconditions.** At least 60 RANSAC inliers from step 4, covering at least 3 of the 4
      quadrants of the reference frame. Otherwise the similarity from step 5 is kept.
   2. **Estimate.** Run `cv2.findHomography` (RANSAC, reprojection threshold 3 px) on the matched
      points. Then run `cv2.findTransformECC` with `MOTION_HOMOGRAPHY`, initialized with that
      estimate, using the same blur, resolution, and mask as step 5.
   3. **Acceptance gates.** All of these must hold:
      - the ECC correlation coefficient improves on the similarity's by at least 0.02;
      - each corner of the output frame, mapped through the homography, lies within 8% of the
        output width of where the similarity maps it;
      - the mapped output frame is a convex quadrilateral.
   4. **Outcome.** If any gate fails, the similarity is kept. `edit.model` records which model is
      used (`similarity | homography`). DEBUG logs record each gate's value for tuning.

   The gate thresholds are constants in `align.py`, tuned on golden photos (§8.3).
7. **Confidence.** `c = clamp01(0.5 · min(1, inliers / 80) + 0.5 · max(0, ecc_cc))`, where
   `ecc_cc` belongs to the model that was kept.
   - `c ≥ 0.6` → `status = "ok"`.
   - `0.35 ≤ c < 0.6` → `status = "needs_review"`; the transform is applied but flagged.
   - `c < 0.35` or no transform → `status = "needs_review"`, and the photo is placed with a
     fallback that center-fits it to the reference aspect ratio.
8. **Sanity limits.** The similarity must satisfy these limits; otherwise it is rejected and
   handled as in the `c < 0.35` case. The homography is additionally bounded by the corner gate in
   step 6.
   - scale ∈ [0.2, 5.0], measured from the full photo's working copy to the reference's working
     copy after composing the tight-crop second pass (step 4); a reference covering less than
     about 20% of the photo's width is therefore flagged rather than force-fitted;
   - rotation ∈ [−45°, 45°].

The resulting transform is stored as a 3 × 3 matrix in **normalized coordinates** (§4.8), so it
applies to any render resolution.

#### 4.5.2 Color matching to the reference

The reference carries the dentist's color grading, which is non-linear (iPhone Photos edits).
After alignment, pixel correspondences exist:

1. Warp the new photo's working copy into the reference frame and take the overlapping valid
   pixels.
2. Convert both to Lab (float).
3. For each channel L, a\*, b\*, fit a **monotone piecewise-linear mapping** through 17
   **weighted** quantile pairs (p0, p6.25, …, p100) of the corresponding pixels.
   - Pixels inside the reference's dilated tooth mask (§4.7.1) get weight 1.0, and all other
     pixels 0.25. Teeth are stable across surgical stages, while the treatment area (flap, blood,
     instruments) is not.
   - The other pixels still count, so the mapping also covers the dark and gingival range.
   - If the tooth mask covers less than 3% of the overlap, all weights are 1.0.
4. **Robustness loop (up to 3 iterations).** Apply the mapping and compute the per-pixel ΔE76 to
   the reference. Exclude the pixels whose residual exceeds `median + 3 × 1.4826 × MAD`, and
   refit.
   - The cut adapts to how much of the frame changed. A fixed share (such as the top 20%) fails
     when surgery changes more than that share of the frame.
   - At most 50% of the pixels are excluded.
   - The loop stops early when fewer than 1% of the pixels change status.
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

- Model: DINOv2 ViT-S/14, exported by us to ONNX with `scripts/export_dinov2_onnx.py` (pooling
  in-graph) and dynamically quantized to int8 (≈ 25 MB; Apache-2.0).
  - No third-party model files are used. The reference app's model and classifier heads carry no
    license, so only its publicly observed ideas are re-implemented.
- Preprocessing:
  1. Rotate portrait images to landscape.
  2. Resize to 336 × 224, which gives a 24 × 16 patch grid.
  3. Normalize with ImageNet mean and std.
- Output `g(x)`: 7 blocks × 384 = 2688-d, each block L2-normalized:
  - the CLS token;
  - the mean patch token of each cell of a 3 × 2 grid (8 × 8 patches per cell).
  - The CLS token alone discards where things are in the frame. The grid keeps the coarse
    layout, such as which side of the frame the anterior teeth are on.
- Each photo is embedded twice: as `x` and as its horizontal mirror `x̄`.
- Inference: onnxruntime CPU EP in batches of 18 (nine photos plus their mirrors). Apple Silicon
  CoreML EP is an optional optimization and is off by default, because it cannot be tested on the
  Windows development host.

**Model provisioning (`model_store.py`).**

- The model URL (a GitHub Release asset) and its SHA-256 are pinned in code.
- The first time AI classification is needed, the UI asks for consent:
  "Download AI model (~25 MB) from GitHub?"
- The download is verified against the hash, then cached in the user cache directory.
- If the user declines or the download fails, the app uses the EXIF-order fallback and says so.

**Classifier.**

**Why two heads.** DINOv2 is trained with random horizontal-flip augmentation, so a view and its
mirror image get nearly the same embedding. The three left/right slot pairs are close to mirror
images of each other, so a single 9-way head would confuse them. The classifier therefore splits
the decision into "which view group" and "which side". The reference app's side model uses the
same original-minus-mirror feature; this is an independent re-implementation.

- **Slot groups** (6):
  - `upper_buccal` = {`upper_right`, `upper_left`};
  - `upper_occlusal`;
  - `occlusion_side` = {`occlusion_right`, `occlusion_left`};
  - `occlusion_front`;
  - `lower_buccal` = {`lower_right`, `lower_left`};
  - `lower_occlusal`.
- **Group head.** scikit-learn `LogisticRegression` (multinomial, `C = 10.0`,
  `class_weight = "balanced"`) on the mirror-symmetric feature `s = (g(x) + g(x̄)) / 2`.
  Mirroring a photo never changes its group. Block-normalized embeddings are short vectors, and
  at C = 1.0 every probability stayed below 0.5 on the exported model.
- **Side head.** `LogisticRegression(fit_intercept=False)` on the mirror-antisymmetric feature
  `a = g(x) − g(x̄)`, with labels right / left.
  - Without an intercept, mirroring a photo exactly negates its score.
  - It is trained on the samples of all three lateral groups pooled, and each sample is
    augmented with its mirror carrying the opposite label.
  - Right and left are learned from the dentist's confirmed labels, so the head adopts the
    dentist's convention.
- **Slot probability.** Central slots: `p(slot) = p(group)`. Lateral slots:
  `p(slot) = p(group) · p(side)`.
- **Trained** state: requires ≥ 3 confirmed samples for **every** slot. Until then, the
  classifier is **untrained** and the fallback is used. Teaching from three past sets (§3.5)
  reaches this state before first use.
- Persistence:
  - samples → `classifier/samples.npz` (`g(x)` and `g(x̄)` as float16, slot labels, case ids,
    `source` (`export | teach`), timestamps);
  - model → `classifier/model.json` (both heads' coefficients and intercepts, class order,
    embedding model hash).
- Samples carry the embedding-model hash. If the model changes, old samples are ignored and the
  user is told to re-confirm a few cases.
- Retraining runs in the background after each composite export and each confirmed teaching set;
  it takes milliseconds.

**Assignment.**

- Cost matrix `−log(p_ij + 1e-6)` for photo `i` and slot `j`, solved with
  `linear_sum_assignment`.
- More than 9 photos: the unassigned photos go to the tray.
- Fewer than 9: the remaining slots stay empty.
- An assigned slot is flagged low-confidence when `p < 0.5`.

**Fallback (cold start).**

- Sort photos by capture time and map them onto `settings.shooting_order`. The default is
  row-major slot order. The dentist sets their real shooting order in Settings or during
  teaching (§3.5).
- Teaching uses the same ordering to pre-assign past sets (§3.5).

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

1. **Flip.** Apply the slot's flip default (`none | horizontal | vertical | rotate_180`). The
   user can toggle it per cell.
   - Defaults start from Settings (§11, question 2) and are then **learned**. Every confirmed
     composite, whether an export or a teaching set (§3.5), records each slot's final flip in
     `classifier/flips.json`.
   - Once a slot has at least 3 confirmations, its default becomes the most frequent value among
     its last (up to) 5 confirmations. On a tie, the current default is kept.
   - Settings shows the learned defaults. The user can pin a slot's flip to stop learning.
   - **Storage and precedence.**
     - Starting values and pins live in `settings.json` (`flip_start`, `flip_pins`) and change
       through `PUT /api/settings`.
     - Confirmation history and learned values live in `classifier/flips.json`.
     - The effective default of a slot is its pin if set, otherwise its learned value (≥ 3
       confirmations), otherwise its starting value.
   - The flip is learned per slot rather than predicted per photo. A mirrored right-side view
     looks like a direct left-side view, so appearance alone cannot tell a mirror shot from the
     opposite side. Per-slot learning follows the dentist's habits instead.
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
4. **No perspective correction in F2.** Without a reference photo there is no target geometry to
   match, so F2 transforms are always similarities. Perspective correction applies to F1 only.

#### 4.7.3 Color unification

- The **baseline** photo (default `occlusion_front`) is left unchanged.
- Every other photo gets **Reinhard transfer** in Lab: per channel, map the mean and standard
  deviation, computed over its tooth ∪ gingiva mask, to the baseline's.
  - Strength is 0.8 (a blend with identity).
  - Each mean shift is clamped to ±12 L units and ±8 a\*/b\* units.
- Params are stored as `method = "lab_reinhard_v1"`.

### 4.8 Edit model (non-destructive)

Originals are never modified. Each photo's processing result is a small parameter record:

- `transform`: a 3 × 3 projective matrix (9 numbers, row-major) in **normalized coordinates**.
  Input coordinates are divided by the input width, and output coordinates by the output width.
  This makes the transform valid for preview and full resolution alike. Flips are folded into
  the matrix. A similarity is stored with the last row `[0, 0, 1]`.
- `model`: `similarity | homography`, the model that produced `transform` (§4.5.1). F2 photos are
  always `similarity`. "Reset perspective" replaces a homography with its closest similarity (a
  least-squares fit on the four output-frame corners).
- `output_aspect` and `output_size`: the output frame.
  - F1: the reference size × the case's `output_scale` (1, 1.5, or 2; default 1). All photos of a
    case share it, so the sequence image stays equal-height. A scale that would make either side
    longer than 65,500 px (the JPEG limit) or the output larger than 200 MP (the decoder limit,
    §4.13) is rejected with `output_too_large` before anything is rendered.
  - F2: the cell size.
- `detail_ratio` (F1 only): full-resolution photo pixels per reference pixel over the matched
  region (§4.5.1 step 4).
- `color`: `{method, params}`.
- `source`: `auto` or `manual`. Manual edits are never overwritten by auto re-processing unless
  the user chooses "Reset to auto".
- `confidence`, `status` (`ok | needs_review | failed`), `message`, and
  `algorithm_version` (for example `align-1`). The algorithm version allows selective
  re-processing after upgrades.

**Rendering.** The edit record is applied to an original as follows:

1. decode
2. `warpPerspective` for both models (Lanczos4 for full-resolution output, linear for previews)
3. color mapping
4. encode

**Preview cache.**

- Renders are cached at three sizes: thumbnail (320 px), preview (1600 px), and full.
- Cache key: `sha256(original_sha256 + edit_record_json + size)`.
- Cache location: the user cache directory. The cache is never written into the library.
- Size cap: `cache_max_gb` (default 2 GB). Full-size renders of 48 MP photos would otherwise
  accumulate in `~/Library/Caches` without bound. Least-recently-used entries are evicted at
  startup and after each job.
- Export zips and teaching sessions also live in the cache directory but outside the LRU; they
  are deleted explicitly (§4.9, §3.5).

### 4.9 Naming rules

- **Individual photo:** `{patient}_{title}_{YYYYMMDD}_{HHMM}.jpg`, from `captured_at` (local
  time).
- **Procedure sequence:** `{patient}_{title}_Procedure_{YYYYMMDD}.jpg`, using the generation date
  (local).
- **Composite:** `{patient}_{tag}_Composite_{YYYYMMDD}.jpg`, using the most frequent capture date
  among the assigned photos.
- **Patient export zip (§3.4):** `{patient}_Export_{YYYYMMDD}.zip`, using the generation date
  (local). Zips are built in the user cache directory and deleted after download or at the next
  start.

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
| `dio install-launcher [--uv-path PATH]` / `dio uninstall-launcher` | macOS only: create or remove `~/Applications/Dental Image Organizer.app` (§7.3), and record the absolute `uv` and `dio` paths for in-app updates. Other OSes print "not supported". |
| `dio process --reference REF --out DIR PHOTOS...` | Headless Feature 1. This is the extension point for future adapters. |
| `dio composite PHOTOS... --out DIR --patient NAME --tag TAG [--order SLOTS] [--baseline SLOT] [--flip SLOT=FLIP]... [--assign FILE=SLOT\|tray]... [--learn] [--download-model]` | Headless Feature 2; `--learn` stores the run as confirmed samples (§3.3 step 7) |
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
│   ├── patient.json                        # {schema_version, id, name, note, created_at}; optional note tells same-name patients apart
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
| config | `settings.json` (including `uv_path` and `dio_path` for in-app updates, and `flip_start` / `flip_pins`, §4.7.2) |
| data | `classifier/` (`samples.npz`, `model.json`, `flips.json`), `instance.lock`, `instance.json` (port, pid) |
| cache | render cache (LRU-capped), model file, export zips, teaching sessions, update script |
| log | `dio.log` |

**`case.json` (schema_version 1)**, as Pydantic models in `storage/schema.py`:

```json
{
  "schema_version": 1,
  "id": "2f6c…(uuid4)",
  "type": "procedure",
  "patient_id": "…",
  "title": "implant maxillary right first molar",
  "output_scale": 1.0,
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
        "transform": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
        "model": "similarity",
        "output_size": [4032, 3024],
        "detail_ratio": 1.0,
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
- `output_scale` ∈ `1 | 1.5 | 2`, for procedure cases only (§4.8).

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
| `POST /system/update` | Start the in-app update (§7.3); 409 while a job is running |
| `GET / PUT /settings` | Read / update settings |
| `GET /patients` | Patients with notes and case summaries |
| `PATCH /patients/{id}` | Update a patient's name or note |
| `POST /cases` | Create a case `{type, title, patient_id}` for an existing patient, or `{type, title, patient_name, patient_note?}` for a new one |
| `GET / PATCH / DELETE /cases/{id}` | Read, update (title, sequence selection, `output_scale`, composite settings), soft-delete to `.trash` |
| `POST /cases/{id}/photos` | Multipart upload (multiple files) → `{job_id, skipped: [{name, reason}]}`; processing is async; unsupported files are skipped (§3.1) |
| `PUT /cases/{id}/reference` | Set or replace the reference (procedure); triggers re-processing |
| `PATCH /cases/{id}/photos/{pid}` | Update stage, slot, flip, manual `edit`, or reset to auto |
| `DELETE /cases/{id}/photos/{pid}` | Remove a photo |
| `GET /cases/{id}/photos/{pid}/image?variant=original\|processed&size=thumb\|preview\|full` | Image bytes (cached) |
| `PUT /cases/{id}/composite/slots` | Full slot map `{slot: photo_id}` (drag-to-swap) |
| `POST /cases/{id}/composite/assign` | Re-run automatic assignment |
| `GET /cases/{id}/render/sequence?size=…` / `GET /cases/{id}/render/composite?size=…` | Preview renders |
| `POST /cases/{id}/export` | `{include: ["singles","sequence","composite"], conflict: "replace"\|"keep_both"}` → `job_id` |
| `GET /cases/{id}/export/{job_id}/zip` | Download the exported files as a zip |
| `POST /patients/{id}/export` | Export every case of a patient (§3.4): `{include, conflict}` as above → `job_id` |
| `GET /patients/{id}/export/{job_id}/zip` | Download the patient export as one zip, one folder per case |
| `GET /cases/{id}/archive` / `POST /archive/import` | Case archive export / import |
| `GET /jobs/{job_id}` / `GET /jobs/{job_id}/events` | Job status / Server-Sent Events progress stream |
| `GET /model/status` / `POST /model/download` | AI model availability / consented download (job) |
| `GET /classifier/status` / `POST /classifier/retrain` | Sample counts per slot, trained flag, learned flip defaults / manual retrain |
| `POST /classifier/teach` | Upload one past set (§3.5, multipart) → `{session_id, job_id, skipped}`; the job embeds the photos and proposes a slot map in shooting order |
| `GET /classifier/teach/{sid}` / `GET /classifier/teach/{sid}/photos/{pid}/image?size=thumb\|preview` | Session status and proposal / thumbnails |
| `POST /classifier/teach/{sid}/confirm` | `{slots: {slot: photo_id}, flips: {slot: flip}}` → stores samples and flips, retrains, deletes the session |
| `DELETE /classifier/teach/{sid}` | Cancel; deletes the session |

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
- State-changing requests must carry an `Origin` header equal to `http://127.0.0.1:<port>` or
  `http://localhost:<port>`. There is no development-only bypass; the Vite dev proxy rewrites
  `Origin` instead (§5.4).
- `/system/reveal` and all file access are confined to the library root after path resolution
  (no traversal).
- Uploads are limited to 200 MB per file, and decoded pixel count is capped at 200 MP
  (decompression-bomb guard).

**Network.**

- No telemetry.
- Only three kinds of outbound calls:
  1. the model download (explicit consent);
  2. the version check against `https://pypi.org/pypi/dental-image-organizer/json`, at most once
     per day, disclosed in Settings, opt-out;
  3. the in-app update, started by the user, which runs `uv tool upgrade` against PyPI (§7.3).

**Deployment impact of the backend:** a pure-Python wheel; no compiled code of our own; all heavy
dependencies come as prebuilt wheels, so `uv tool install` needs no compiler on the user's Mac.

## 5. Frontend (stage 3.3)

### 5.1 Stack

- React 19, TypeScript (strict), Vite.
- Tailwind CSS v4.
- TanStack Query for server state; local UI state stays in components.
- dnd-kit for the 3 × 3 board, tray, and timeline drag-and-drop.
- Pan, zoom, rotate, and four-point perspective use custom pointer-event handling that produces
  the same normalized 3 × 3 matrix as the backend (`frontend/src/lib/transform.ts`, unit-tested
  against shared fixtures). Previews apply it as a CSS `matrix3d`, which expresses a 2D homography
  exactly.
- Vitest for tests; ESLint and Prettier.
- UI strings live in `frontend/src/strings/en.ts`, ready for i18n.

### 5.2 Screens

| Screen | Content |
|---|---|
| **Header** | Quit button; update banner with **Update now** (§7.3) |
| **Library** | Patient list with search, showing each patient's note; per-patient **Export all** (§3.4); case cards (type, title, date, thumbnails, needs-review count); "New procedure case" and "New composite case" |
| **Procedure case** | Reference panel (locked badge, "Replace reference…", output resolution 1× / 1.5× / 2× with the suggestion from §3.2); timeline of photos grouped by stage with confidence badges; drop zone; **Compare** view (onion-skin opacity slider, split slider, manual-align tool with free, two-point, and four-point modes, Reset perspective, Reset to auto); **Sequence builder** (select and reorder, preview); Export |
| **Composite case** | 3 × 3 board with drag-to-swap and tray; low-confidence highlighting; **Cell editor** (pan, zoom, rotate, flip toggle, reset); baseline selector; live composite preview; Export; teaching prompt while the classifier is untrained (§3.5) |
| **Teach** | Shooting-order picker; drop zone for one past set; 3 × 3 confirmation board with flip toggles; "Set n of 3" progress and per-slot sample counts (§3.5) |
| **Export dialog** | What to include; conflict policy; progress (SSE); result list with "Reveal in Finder" and "Download zip" |
| **Settings** | Library location, JPEG quality, strip GPS, composite cell size and gap, shooting order, per-slot flips (learned defaults with pin), render cache size, idle shutdown, update check, AI model status, classifier sample counts, "Teach from past composites" |

### 5.3 Interaction details

- **Drop zone.** Accepts files and folders, using `DataTransferItem.webkitGetAsEntry` for recursive
  folders. Files upload in parallel, with a concurrency of 3.
  - Obvious non-photos (`.mov`, `.mp4`, `.aae`) are filtered out before upload, which saves
    uploading videos. The server's content sniffing (§4.4) is authoritative and reports what it
    skipped.
  - One summary is shown per drop (§3.1).
- **Manual adjustments** are applied immediately as CSS transforms (`matrix3d`) on the 1600 px
  preview for instant feedback. On release, the edit is committed with `PATCH` and the server re-renders.
- **Heartbeat and server connection.**
  - The heartbeat runs every 30 s, and immediately when the tab becomes visible again
    (`visibilitychange`). Background tabs may be throttled or suspended (Safari), so the server
    can reach its idle shutdown while the tab is still open.
  - If the server stops answering for 10 s (idle shutdown, Quit, or crash), the UI replaces the
    page with a full-screen notice: "Dental Image Organizer has closed. Click its Dock icon to
    reopen." It keeps polling `/api/health` and reloads when the server is back.
  - During an in-app update, the notice reads "Updating…" instead (§7.3).
  - A Quit button sits in the header.
- **Accessibility.** All actions are reachable by keyboard: arrow keys nudge the transform, and
  `[` / `]` rotate. Focus rings are visible, and contrast is AA.

### 5.4 Build and packaging

- During development, `npm run dev` (Vite) proxies `/api` to `http://127.0.0.1:8765`.
  - The browser sends the Vite origin (`http://localhost:5173`), which would fail the backend's
    `Origin` check (§4.13) with 403 on every state-changing request.
  - The proxy therefore sets `changeOrigin: true` (rewrites `Host`) and, in its `proxyReq` hook,
    rewrites `Origin` to `http://127.0.0.1:8765`. The backend keeps a single security policy
    with no development mode to misconfigure.
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

**Privacy guard.** `.gitignore` ignores `*.jpg *.jpeg *.png *.heic *.heif *.dng *.tif *.tiff
*.webp` everywhere, except in `tests/fixtures/synthetic/`, `doc/assets/`, and `frontend/public/`.
It also ignores `*.zip`, because case archives (§4.11) contain original photos. This makes it
hard to commit a patient photo by accident.

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
   - `dio serve --detach --no-browser`, then `curl /api/health`, then shutdown;
   - `dio install-launcher`, then assert the app bundle exists;
   - launch the bundle with `open`, wait for `/api/health`, and assert that the applet process
     has exited within 30 s. Only a real launch through the applet proves that
     `do shell script` returned (§4.3); running `dio serve --detach` directly cannot catch an
     inherited-pipe hang;
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

1. Verifies Apple Silicon and macOS 14 or later, and exits with a clear message otherwise.
   - Architecture is read from `sysctl -n hw.optional.arm64`, not `uname -m`: a Terminal running
     under Rosetta reports `x86_64` on Apple Silicon.
   - If `sysctl -n sysctl.proc_translated` is `1` (running under Rosetta), the script re-executes
     itself with `arch -arm64`, so that `uv` provisions an arm64 Python.
     - When run from a file, it re-executes that file: `exec arch -arm64 /bin/sh "$0" "$@"`.
     - When piped (`curl … | sh`), there is no file to re-run, because `sh` reads the script from
       stdin. The script then re-fetches itself from its pinned URL:
       `exec arch -arm64 /bin/sh -c "$(curl -LsSf "$INSTALL_URL")"`.
     - Environment overrides such as `DIO_WHEEL` are preserved, because `exec` keeps the
       environment.
2. If `uv` is missing, installs it with the official installer (`https://astral.sh/uv/install.sh`),
   and discloses this in its output.
3. Runs `uv tool install --python 3.12 dental-image-organizer`, or `--upgrade` if it is already
   installed. `uv` downloads a managed Python, so **no system Python is required**.
4. Runs `dio install-launcher --uv-path "<absolute path to uv>"`. This uses `osacompile` to create
   `~/Applications/Dental Image Organizer.app`, an AppleScript applet that runs
   `"<absolute path to dio>" serve --detach`, and sets the app icon.
   - `~/Applications` is created if it does not exist; a fresh macOS account may not have it.
   - The bundle is created locally, so it has no `com.apple.quarantine` attribute and **Gatekeeper
     does not prompt**.
   - The absolute path avoids the minimal `PATH` of Finder-launched apps.
   - The absolute `uv` and `dio` paths are recorded in settings for the in-app update.
5. Opens the app once, so that its icon appears in the Dock. Then it prints how to keep the icon
   there (right-click the icon → Options → Keep in Dock), along with the update and uninstall
   commands. Pinning to the Dock is left to the user rather than scripted, because scripting it
   would rewrite the user's Dock preferences.

**Daily use.** Click the Dock icon, or open "Dental Image Organizer" with Spotlight. The server
starts in the background, and the browser opens `http://127.0.0.1:8765`. (Recent macOS versions
replaced Launchpad, so the instructions do not rely on it.)

**Update.**

- **In the app (default).** When the daily version check finds a newer release, a banner offers
  **Update now** (`POST /api/system/update`, refused with 409 while a job is running).
  1. The server writes a short POSIX shell script to the cache directory and starts it detached
     with `/bin/sh`, sending its output to the log file. Then the server shuts down.
     - A shell script is used because `uv tool upgrade` replaces the tool's virtual environment,
       so the updater must not run from inside it.
  2. The script waits for the server process to exit (up to 30 s). It then runs
     `"<uv>" tool upgrade dental-image-organizer`, and starts
     `"<dio>" serve --detach --no-browser`.
     - The absolute paths come from settings (recorded by `install-launcher`), because a server
       started from Finder has a minimal `PATH`.
  3. The open page shows "Updating…", keeps polling `/api/health`, and reloads when the reported
     version changes.
     - If the upgrade fails, the script restarts the existing version, and the UI shows the
       error with the log path.
- **Terminal (fallback).** Run `uv tool upgrade dental-image-organizer`, or re-run the install
  script.
- In-app update is macOS only. On Windows (development), the banner shows the terminal command.

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
  run against a freshly built wheel on `macos-latest` (arm64, matching the supported hardware).

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
- **Tight crop.** For a reference covering 30% of the photo's width, the same tolerances must
  hold, the second pass must be used, and `detail_ratio` must be within 5% of the true value.
- **Perspective.** On top of the similarity, apply a known homography equivalent to up to 8° of
  camera tilt (planar synthetic scene). Assert that the output-frame corners are recovered within
  **0.5%** of the width and that `model = "homography"`.
- **Homography gating.**
  - A pure-similarity input must stay `model = "similarity"`, because the ECC gain is below the
    gate.
  - Inliers confined to one quadrant must skip the homography.
  - A homography that pushes a corner beyond the 8% gate must be rejected.
- **Color.**
  - **F1:** apply a known Lab tone curve plus a white-balance shift to the warped image. After
    `lab_quantile_v1`, the mean ΔE76 against the reference must be **< 3**.
  - **Robustness:** simulated "surgical change" patches covering 20% and 40% of the frame must not
    drag the fit; mean ΔE outside the patch must stay < 3. The 40% case exceeds what a fixed
    top-20% cut could absorb.
  - **F2:** after Reinhard transfer, the tooth-region mean ΔE to the baseline must drop by
    ≥ 60%.
- **Masks and geometry.** Synthetic arches with known tilt are leveled to within 1°. Symmetric
  synthetic occlusal arches find the axis within 1°.
- **Transform math.** For 3 × 3 matrices: compose, invert, closest similarity, and the
  normalized↔pixel round trip, with property-based tests via Hypothesis. A shared JSON fixture is
  used by both pytest and Vitest to guarantee that the frontend and backend matrices agree,
  including the CSS `matrix3d` mapping.
- **Classifier and assignment.** Synthetic clustered embeddings must yield correct assignment.
  Also tested:
  - the cold-start fallback ordering;
  - the low-confidence flag;
  - samples with a different model hash are ignored;
  - **mirror antisymmetry:** the side score of `x̄` equals the negated score of `x`;
  - **left/right separation:** synthetic pairs that differ only by mirroring land in the same
    group but on opposite sides;
  - **flip learning:** a slot's default changes only after 3 confirmations and follows the
    most frequent value of the last 5; a tie keeps the current default; a pinned slot never
    changes.
- **Naming.** NFC/NFD equivalence (`王小明` as NFD → NFC), sanitization, truncation by bytes,
  collision suffixes, and timezone handling.
- **Storage.** Atomic write (simulated crash leaves the old file intact), schema validation,
  future-version rejection, archive round trip, SHA-256 dedup, path confinement, and render-cache
  LRU eviction at the size cap.
- **Image I/O.** EXIF orientation 1–8, ICC P3→sRGB conversion, missing-EXIF fallback, GPS
  stripping, magic-byte sniffing, and decompression-bomb rejection. HEIC fixtures are generated at
  test time with `pillow-heif` (dev dependency only); decoding goes through `pi-heif`.

### 8.2 Integration

FastAPI `TestClient` with a temporary library directory covers:

1. Create a procedure case → set reference → upload photos → wait for the job → verify the
   statuses → export → verify the files and names.
2. The same flow for a composite case, including the slot swap and the classifier sample written
   on export.
3. Patient-level export across one procedure case and one composite case: verify each case's
   `export/` folder, the zip's per-case folders and names, and that one failing case does not
   abort the others.
4. Teaching: three past sets → classifier trained; samples carry `source = "teach"`; the session
   directory is gone after confirm and after cancel; nothing is written to the library.
5. Intake: an upload mixing photos with `.MOV`, `.AAE`, and DNG files stores only the photos and
   returns each skipped file with its reason.
6. `POST /system/update` returns 409 while a job is running.
7. Security checks: wrong `Host` → 400; wrong `Origin` on `POST` → 403; path traversal → 400.

### 8.3 Golden (local only)

- `pytest -m golden` runs on real, de-identified photos in `local_fixtures/`, which the dentist
  provides with consent.
- It records alignment and color metrics to a CSV for tuning thresholds.
- It is skipped automatically when the folder is absent, as in CI.

### 8.4 Acceptance

Before each release, a short scripted checklist is executed on a real Mac by the dentist:

1. Install; keep the icon in the Dock.
2. Launch from the Dock.
3. AirDrop import (HEIC and JPEG), including a Live Photo (the video is skipped with a summary).
4. Drag photos straight from Photos.app into the drop zone, in Safari and in Chrome. If a browser
   fails, record it, and confirm that the File → Export fallback works.
5. Teach from three past composite sets.
6. One procedure case with ≥ 4 photos.
7. One composite.
8. Export one case, then Export all for the patient.
9. Reveal in Finder.
10. Leave the tab in the background past the idle timeout; confirm the "has closed" notice, then
    reopen from the Dock.
11. Quit and relaunch.
12. Update from the in-app banner.

## 9. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Developer has no Mac | Cross-platform core; macOS CI smoke tests on every PR; dentist acceptance checklist (§8.4); keep macOS-specific code minimal (`launcher/macos.py`, `install.sh`) |
| Surgical changes defeat feature matching | Tooth-weighted detection; RANSAC; confidence gating with `needs_review` and an excellent manual-alignment tool |
| Homography over-warps the curved (non-planar) arch | Preconditions on inlier count and spread; acceptance gates (ECC gain, corner displacement, convexity) with fallback to the similarity; "Reset perspective" in the UI |
| Tightly cropped reference leaves few pixels to match | Full-resolution second pass on the predicted region; output-resolution suggestion from `detail_ratio` (§4.5.1, §3.2) |
| Surgery changes a large share of the frame and skews the color fit | Tooth-weighted quantiles and an adaptive MAD cut (§4.5.2) |
| Classifier cold start | Teaching from three past composites before first use (§3.5); EXIF-order fallback with a configurable shooting order; learning from every export; per-slot sample counts shown in Settings |
| Flip conventions differ between dentists and slots | Per-slot flip defaults learned from confirmed composites, with pinning (§4.7.2) |
| Browser cannot accept drags from Photos.app | Verified in acceptance (§8.4); documented File → Export fallback (§3.1) |
| Classifier confuses left/right slot pairs (DINOv2 is nearly mirror-invariant) | Separate side head on mirror-antisymmetric features; grid-pooled patch tokens keep the layout (§4.6) |
| Classifier drift after a model update | Samples are tagged with the embedding-model hash; stale samples are excluded |
| HEIC / Display P3 color shifts | ICC → sRGB conversion at decode; sRGB ICC embedded on output |
| Very large photos (48 MP) | Analysis on a 1600 px working copy; a single full-resolution warp at render time; 200 MP decode cap |
| Data loss | Originals immutable; atomic `case.json` writes; soft-delete to `.trash`; zip archive export; library visible to Time Machine |
| Accidental publication of patient photos | Image-type privacy guard in `.gitignore`; synthetic test data only; original requirements document excluded |
| localhost attacks (DNS rebinding, CSRF) | 127.0.0.1 binding, TrustedHost, Origin check, path confinement |

## 10. Out of Scope (MVP)

- Facial photos, smile photos, and radiographs (per the requirements).
- EMR features: notes, insurance, integration with clinic systems (per the requirements).
- iPhone / PWA usage, Telegram / Discord / Email intake, OpenClaw skill (see §3.6).
- RAW / DNG input.
- Signed `.dmg`, notarization, Mac App Store.
- Intel Macs and macOS versions earlier than 14 (see §4.1).
- Official Windows end-user support. The app works on Windows for development, but there is no
  Windows launcher or installer.
- Cloud sync and multi-user access.
- UI translation (zh-TW). Strings are prepared for it.

## 11. Open Questions (to confirm with the dentist during acceptance)

These have explicit defaults, so implementation is not blocked. They are flagged for validation.

1. **Default shooting order.** Default: row-major slot order (§4.6). The dentist's real order is
   entered during teaching (§3.5) or in Settings.
2. **Starting per-slot flips for mirror shots.** These are only starting values; they are then
   learned from confirmed composites (§4.7.2).
   - `upper_occlusal` and `lower_occlusal`: `vertical`.
   - The four buccal corner slots: `horizontal`.
   - All `occlusion_*` slots: `none`.
3. **Stage rule** (§3.2 step 5). The `D0` rule is the default; stages are always editable.
4. **Composite cell size.** Default 1200 × 800 per cell (3600 × 2400 total), gap 0 px.

## 12. Future Extensions

- **Adapters:** an OpenClaw skill and a Telegram bot calling `dio process` / `dio composite` on
  the same Mac; a Downloads-folder watcher for AirDrop arrivals.
- **Stronger algorithms:** an XFeat matcher (Apache-2.0, CPU-friendly, ONNX via onnxruntime) as an
  A/B alternative to SIFT, evaluated on golden photos; SuperPoint is avoided because its weights
  are licensed for non-commercial research only; SAM-family tooth segmentation;
  a learned orientation and angle-correction model (self-supervised, like the reference app's
  angle model).
- **Platform:** a zh-TW UI; a signed `.dmg`; a Windows launcher; optional CoreML acceleration.
