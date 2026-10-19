# Dental-Image-Organizer

Automated alignment, color matching, and collage generation for clinical intraoral photos.
It runs locally on the dentist's Mac.

> **Status: early development.** The procedure and composite pipelines are available as
> command-line tools (`dio process`, `dio composite`); the app UI is not built yet. See the
> [design spec](doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md).

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

## Third-party model

The composite classifier uses embeddings from Meta's
[DINOv2](https://github.com/facebookresearch/dinov2) ViT-S/14 (Apache-2.0), exported to ONNX by
`scripts/export_dinov2_onnx.py`. The model is downloaded once, with your consent
(`--download-model`), from this repository's GitHub Releases
([`model-dinov2s-v1`](https://github.com/jimwellh/Dental-Image-Organizer/releases/tag/model-dinov2s-v1))
and verified by SHA-256.

## Documentation

- [Product requirements](doc/requirements.md)
- [Technical design spec](doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md)

## Tech stack

- **Backend:** Python 3.12, FastAPI, OpenCV, onnxruntime (DINOv2-S embeddings), scikit-learn.
- **Frontend:** React, TypeScript, Vite, Tailwind CSS.
- **Distribution:** PyPI + `uv`.
- **CI:** GitHub Actions.

## Development

Requirements: [uv](https://docs.astral.sh/uv/). It provisions Python 3.12.

```sh
uv sync                  # install dependencies
uv run pytest            # tests; golden tests run only when local_fixtures/ exists
uv run ruff check && uv run ruff format --check && uv run mypy && uv run lint-imports
```

Process one procedure case from the command line:

```sh
uv run dio process IMG_0002.HEIC IMG_0003.HEIC --reference reference.HEIC \
  --out export --patient "Alex Wang" --title "implant 16"
```

Compose one 9-photo set:

```sh
uv run dio composite IMG_*.HEIC --out export --patient "Alex Wang" --tag "full mouth"
```

`--learn` stores a checked run as classifier samples, and the first such run needs
`--download-model` (about 24 MB, once, from this repository's GitHub Releases). To reset the
classifier, delete the `classifier` folder in the app data directory (on macOS,
`~/Library/Application Support/DentalImageOrganizer/classifier`).

Move one case between libraries as a zip archive (the library root comes from the app's
settings; importing refuses while the app is running):

```sh
uv run dio export-case "~/DentalImageOrganizer/Alex Wang/20261016 implant 16" case.zip
uv run dio import-case case.zip [--on-conflict skip|copy]
```

### Golden photos (local only)

Real, de-identified photos for tuning go in `local_fixtures/`, which is gitignored:

- `local_fixtures/procedure/<case>/`: a `reference.<ext>` plus that case's other photos.
- `local_fixtures/composite/<set>/`: one session's photos, taken in row-major slot order. An
  optional `order.txt` lists the real shooting order instead, one slot per line.

Run `uv run pytest tests/golden` to write per-photo metrics to `local_fixtures/reports/`.

## Disclaimer

This software is a photo-documentation utility. It is **not a medical device** and is not
intended for diagnosis or treatment decisions. Always review processed images before clinical or
publication use.

## License

[MIT](LICENSE)
