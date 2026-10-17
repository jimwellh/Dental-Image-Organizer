# Dental-Image-Organizer

Automated alignment, color matching, and collage generation for clinical intraoral photos.
It runs locally on the dentist's Mac.

> **Status: design phase.** The technical design is complete. Implementation has not started yet.
> See the [design spec](doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md).

## What it does

**Procedure sequences**

- The dentist edits one pre-op photo on their iPhone, and it becomes the case reference.
- Intra-op and post-op photos are then automatically:
  - aligned to the reference (rotation, scale, translation, and perspective),
  - color-matched to it,
  - cropped to its framing.
- Output: individual photos plus a horizontal, chronological sequence image.

**9-photo composite**

- Nine standard intraoral views are automatically placed in a 3 × 3 grid by an AI view
  classifier.
- Each photo is leveled, flipped where needed (mirror shots), cropped, and color-unified.
- Output: one seamless composite image.
- Every drag-to-fix correction teaches the classifier, and three past sets can teach it before
  first use.

**Export**

- Batch export with automatic, standardized file names, for example
  `AlexWang_implant maxillary right first molar_20261016_1430.jpg`.

## Privacy

Patient photos **never leave your computer**. The app is a local server bound to `127.0.0.1`,
and the user interface opens in your browser. There is no telemetry.

The only network access is:

- a one-time AI model download, which asks for your consent first;
- an optional update check against PyPI;
- updates that you start from the app.

## Planned installation (macOS)

Requires an Apple Silicon Mac with macOS 14 Sonoma or later.

The app is not released yet. Once it is, installation will be one command in Terminal:

```sh
curl -LsSf https://raw.githubusercontent.com/jimwellh/Dental-Image-Organizer/main/scripts/install.sh | sh
```

This installs the app with [uv](https://docs.astral.sh/uv/) and adds a
**Dental Image Organizer** launcher to `~/Applications`. No Python installation is required, and
the launcher costs nothing.

## Documentation

- [Product requirements](doc/requirements.md)
- [Technical design spec](doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md)
- [Reference app analysis](doc/research/2026-10-16-photo-smile-tw-analysis.md)

## Tech stack

- **Backend:** Python 3.12, FastAPI, OpenCV, onnxruntime (DINOv2-S embeddings), scikit-learn.
- **Frontend:** React, TypeScript, Vite, Tailwind CSS.
- **Distribution:** PyPI + `uv`.
- **CI:** GitHub Actions.

## Disclaimer

This software is a photo-documentation utility. It is **not a medical device** and is not
intended for diagnosis or treatment decisions. Always review processed images before clinical or
publication use.

## License

[MIT](LICENSE)
