# Dental-Image-Organizer

Automated alignment, color matching, and collage generation for clinical intraoral photos.
It runs locally on the dentist's Mac.

> **Status: early release (0.1.0).** Review processed images before relying on them, and
> report problems in the issue tracker. See the
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

## Installation (macOS)

Requires an Apple Silicon Mac with macOS 14 Sonoma or later. Installation is one command in
Terminal:

```sh
curl -LsSf https://raw.githubusercontent.com/jimwellh/Dental-Image-Organizer/main/scripts/install.sh | sh
```

This installs the app with [uv](https://docs.astral.sh/uv/) (installing uv first if needed) and
adds a **Dental Image Organizer** app to `~/Applications`, then opens it. No Python installation
is required. To keep it in the Dock, right-click its Dock icon while it is open, then choose
Options > Keep in Dock. You can also open it with Spotlight.

**Update.** When a new version is out, the app shows a banner with **Update now**. The app checks
PyPI for the newest version number once a day; turn this off in Settings. You can also update in
Terminal with `uv tool upgrade dental-image-organizer`, or run the install command again.

**Uninstall.** Run `dio uninstall-launcher && uv tool uninstall dental-image-organizer`. Your
library (`~/DentalImageOrganizer`) is kept.

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

Run the local server. It listens on `127.0.0.1` only, on port 8765 or the next free one up to
8775, and a restart keeps the last run's port:

```sh
uv run dio serve --no-browser   # Ctrl+C stops it; -v logs debug details
```

The API's schema is at `http://127.0.0.1:8765/api/openapi.json`. State-changing requests need
an `Origin` header naming the server, for example:

```sh
curl -s http://127.0.0.1:8765/api/health
curl -s -X POST -H "Origin: http://127.0.0.1:8765" http://127.0.0.1:8765/api/system/shutdown
```

`dio serve --detach`, for the macOS launcher, starts the server in the background and returns
once it answers. The server logs JSON lines to `dio.log` in the app's log folder (on macOS,
`~/Library/Logs/DentalImageOrganizer`).

To work with a throw-away set of app folders instead of your own, set `DIO_APP_DIR`. Settings,
data, cache, and logs then go to its `config`, `data`, `cache`, and `log` subfolders, and the
default library to its `library` subfolder:

```sh
DIO_APP_DIR=/tmp/dio uv run dio serve --no-browser
```

In Windows PowerShell:

```powershell
$env:DIO_APP_DIR = "$env:TEMP\dio"; uv run dio serve --no-browser
```

The frontend needs Node 24.11 or later. Install its packages once, then run the Vite dev
server, which proxies `/api` to the running `dio serve`:

```sh
npm --prefix frontend ci
npm --prefix frontend run dev   # open http://localhost:5173, not 127.0.0.1
```

Open `http://localhost:5173` and not `http://127.0.0.1:5173`: the dev server listens on `::1`
only, so the `127.0.0.1` address refuses connections.

`npm --prefix frontend run build` puts the UI into the package (`src/dental_image_organizer/web/`,
gitignored), so `dio serve` serves it at `http://127.0.0.1:8765`.

After an API change, regenerate the typed client with `npm --prefix frontend run gen:api`. After
a change to `core.transform`, regenerate the shared test fixture with
`uv run python tests/support/transform_fixture.py`. `npm --prefix frontend run check` runs ESLint,
`tsc`, Prettier, and the frontend tests.

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

### Distribution

`scripts/install.sh` is the end-user installer; `dio install-launcher` makes the macOS app with
`osacompile`. Neither runs on Windows, so the tests run `install.sh` under `sh` (Git's `sh` on
Windows) with the macOS commands faked, and CI's **macOS smoke** job installs a freshly built
wheel on a real Mac:

```sh
npm --prefix frontend run build && uv build --wheel
sh scripts/smoke_macos.sh dist/dental_image_organizer-<version>-py3-none-any.whl   # macOS only
```

The smoke test keeps everything in a temporary folder (`DIO_APP_DIR` and its own Applications
folder), so it touches neither your library nor `~/Applications`.

**Releasing.** Pushing a tag `v<version>` that matches `version` in `pyproject.toml` runs
`.github/workflows/release.yml`: it builds the UI and the package, runs the macOS smoke test,
publishes to PyPI with Trusted Publishing, and creates the GitHub Release. Before the first
release, add the Trusted Publisher on PyPI (workflow `release.yml`, environment `pypi`) and
create the `pypi` environment in the repository settings.

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
