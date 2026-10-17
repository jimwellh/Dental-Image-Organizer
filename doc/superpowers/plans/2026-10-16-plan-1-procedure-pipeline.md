# Plan 1 — Foundation and Feature 1 Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Python package foundation and the complete Feature 1 pipeline and expose it
as `dio process`. The pipeline is: decode → align to the reference (similarity plus gated
homography) → color-match → render → sequence image → standardized names.

**Architecture:** A `src/`-layout Python package.

- `core/` holds pure functions: numpy arrays in, numpy arrays out, no file or network I/O. An
  import-linter contract enforces this.
- `services/` orchestrates `core/` for one feature.
- `cli.py` is a thin Typer layer.

Every algorithm is tested against procedurally generated "dental-like" scenes with known ground
truth, so no patient photo is ever needed.

**Tech Stack:**

- Python 3.12 managed by uv
- numpy
- opencv-python-headless (SIFT, RANSAC, ECC)
- Pillow + pi-heif (HEIC decoding) and Pillow ImageCms (ICC to sRGB)
- structlog, Typer
- Dev tools: pytest, hypothesis, pillow-heif (test fixtures only), mypy (strict), ruff,
  import-linter
- CI: GitHub Actions

**Spec:** [doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md](../specs/2026-10-16-dental-image-organizer-design.md).
Read §4.4, §4.5, §4.8, §4.9, §4.10, and §8 before starting.

## Roadmap (this is plan 1 of 5)

The spec covers several independent subsystems, so it is implemented as five sequential plans.
Each plan ends with working, tested software.

| # | Plan | Delivers |
|---|---|---|
| **1** | **Foundation + Feature 1 pipeline (this plan)** | `dio process`: align, color-match, and sequence a procedure case from files |
| 2 | Feature 2 core + ML | `dio composite`: DINOv2 embeddings, group/side classifier heads, slot assignment, leveling, smart crop, Reinhard color, 3 × 3 layout |
| 3 | Storage + services + local server | Library and `case.json`, jobs, export, teaching, and the FastAPI API (`dio serve`) |
| 4 | React frontend | Library, procedure, composite, teach, export, and settings screens |
| 5 | Distribution | `install.sh`, the macOS launcher, in-app update, and the release workflow with the macOS smoke test |

## Before You Start

- **Host:** Windows 11 with PowerShell. Every command below runs unchanged in PowerShell and in
  Git Bash.
- **Install uv** if `uv --version` fails. This downloads from astral.sh:

  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

  Open a new terminal afterwards so that `uv` is on `PATH`.
- **Branch:** from a clean `main`, run `git switch -c feat/procedure-pipeline`.
- **Verified baseline.** All code in this plan was executed on 2026-10-16. The run produced
  112 passed and 1 skipped (the golden test, which needs local photos), with mypy `--strict`,
  ruff, and import-linter all clean. Library versions:

  | Package | Version |
  |---|---|
  | numpy | 2.5.4 |
  | opencv-python-headless | 5.0.0.93 |
  | Pillow | 13.0.0 |
  | pi-heif | 1.4.0 |
  | pillow-heif | 1.8.1 |
  | structlog | 26.1.0 |
  | typer | 0.27.3 |
  | pytest | 9.1.1 |
  | hypothesis | 6.170.1 |
  | mypy | 2.4.0 |
  | ruff | 0.17.1 |
  | import-linter | 2.15 |

  If a test tolerance fails with other versions, report it. Do not loosen tolerances.

## Global Constraints

- Python `>=3.12`, provisioned by uv (`.python-version` = `3.12`).
- Code, comments, docs, log events, and CLI text are in English.
- Comments explain *why*, never *what*.
- `dental_image_organizer.core` must not import `services` or `cli`, and must not read or write
  files. The import-linter contract enforces this.
- Logging goes through structlog only.
  - No `print` debugging. User-facing CLI output uses `typer.echo`.
  - Patient names and titles are never logged; log file names (e.g. `IMG_0001.jpg`) instead.
- Never commit image files.
  - `.gitignore` already blocks `*.jpg *.jpeg *.png *.heic *.heif *.dng *.tif *.tiff *.webp *.zip`.
  - Tests generate images in code; `tests/support/synthetic.py` creates the scenes.
- Line endings are LF (`.gitattributes`). Do not convert files to CRLF.
- Image I/O rules (spec §4.4):
  - Format detection uses magic bytes, never file extensions.
  - Decoded images are capped at 200 MP.
  - Output JPEG: quality 92, 4:4:4 chroma, embedded sRGB ICC profile.
  - Output EXIF keeps `DateTimeOriginal`, `OffsetTimeOriginal`, `Make`, `Model`, `LensMake`,
    and `LensModel`. Orientation is set to 1. GPS is stripped by default.
- Working copies:
  - Analysis copies use a 1600 px long edge.
  - ECC refinement uses an 800 px long edge.
- File names follow spec §4.9 exactly. Examples:
  - `AlexWang_implant maxillary right first molar_20261016_1430.jpg`
  - `AlexWang_implant maxillary right first molar_Procedure_20261016.jpg`
- Before each commit, all of these must pass:
  `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run lint-imports`,
  `uv run pytest`.
- End every commit message with the `Co-Authored-By:` trailer of the model writing the commit.

## Review Focus

These are the inputs most likely to break for a real dentist that the spec implies but does not
spell out. Each one is pinned by a test in the owning task.

1. **Portrait photos.** An iPhone portrait photo is stored rotated with EXIF orientation 6. It
   must be decoded upright and aligned normally.
   → Task 10, `test_exif_rotated_portrait_photo_is_aligned`.
2. **Small reference.** The dentist exports a reference smaller than the 1600 px working size
   (heavy crop or "small" export size). Alignment must still succeed.
   → Task 6, `test_reference_smaller_than_working_size`.
3. **Reference dropped again as a photo.** The reference file itself is added among the new
   photos. It must map to identity with status `ok`, not crash or drift.
   → Task 6, `test_photo_identical_to_reference_maps_to_identity`.
4. **Non-RGB PNGs.** Screenshots and edited exports arrive as palette, grayscale, alpha, or
   16-bit PNGs. Each must decode to 3-channel uint8.
   → Task 4, `test_non_rgb_png_modes_decode_to_three_channel_uint8`.
5. **Burst shots and missing EXIF.** Two photos are taken in the same minute, and another has no
   EXIF time, so offset-aware EXIF times mix with naive file times. The result must be unique
   names (`_2`), no sorting crash, and a visible warning.
   → Task 11, `test_process_writes_named_outputs_and_a_sequence`.

## File Structure

```
pyproject.toml                         # Task 1: metadata, deps, ruff, mypy, pytest, import-linter
.python-version                        # Task 1
.github/workflows/ci.yml               # Task 1: ruff, mypy, import-linter, pytest on 3 OSes
src/dental_image_organizer/
├── __init__.py                        # Task 1: __version__
├── __main__.py                        # Task 1: python -m dental_image_organizer
├── errors.py                          # Task 1: DioError hierarchy with stable codes
├── log.py                             # Task 1: structlog setup (stderr, looked up lazily)
├── cli.py                             # Task 1 (--version), Task 11 (process command)
├── core/
│   ├── __init__.py                    # Task 1
│   ├── transform.py                   # Task 2: normalized 3x3 matrices, similarity fit
│   ├── naming.py                      # Task 3: export names, sanitization, collisions
│   ├── imageio.py                     # Task 4: sniff, decode, ICC->sRGB, EXIF, resize, encode
│   ├── color.py                       # Task 5 (Lab conversion), Task 8 (curves)
│   ├── masks.py                       # Task 5: tooth / gingiva masks
│   ├── align.py                       # Task 6 (similarity), Task 7 (gated homography)
│   ├── render.py                      # Task 9: apply transform + colors, coverage mask
│   └── layout.py                      # Task 9: horizontal sequence image
└── services/
    ├── __init__.py                    # Task 1
    ├── procedure.py                   # Task 10: compute_edit / render_edit for one photo
    └── headless.py                    # Task 11: file batch behind `dio process`
tests/
├── support/                           # importable as `support` (pytest pythonpath = tests)
│   ├── __init__.py                    # Task 4
│   ├── icc.py                         # Task 4: builds a Display-P3-like ICC profile
│   ├── synthetic.py                   # Task 5: deterministic dental-like scenes
│   └── scenarios.py                   # Task 6: photo/reference pairs with ground truth
├── unit/                              # one test module per core/service module
├── integration/test_cli_process.py    # Task 11
└── golden/test_procedure_golden.py    # Task 10: real photos from local_fixtures/ only
```

---

### Task 1: Project scaffolding, logging, CLI skeleton, and CI

**Files:**

- Create:
  - `pyproject.toml`
  - `.python-version`
  - `src/dental_image_organizer/__init__.py`
  - `src/dental_image_organizer/__main__.py`
  - `src/dental_image_organizer/errors.py`
  - `src/dental_image_organizer/log.py`
  - `src/dental_image_organizer/cli.py`
  - `src/dental_image_organizer/core/__init__.py`
  - `src/dental_image_organizer/services/__init__.py`
  - `.github/workflows/ci.yml`
  - `uv.lock` (generated)
- Test:
  - `tests/unit/test_cli_version.py`
  - `tests/unit/test_log.py`

**Interfaces:**

- Consumes: nothing.
- Produces:
  - `dental_image_organizer.__version__: str`
  - `errors.SkipReason = Literal["video", "raw", "sidecar", "unknown"]`
  - `errors.DioError` (base class, class attribute `code: str`)
  - `errors.UnsupportedFormatError(message: str, *, reason: SkipReason)` with attribute
    `.reason`
  - `errors.CorruptImageError`
  - `errors.InvalidNameError`
  - `log.configure_logging(level: str = "INFO") -> None`
  - `cli.app: typer.Typer`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "dental-image-organizer"
version = "0.1.0.dev0"
description = "Local Mac app that aligns, color-matches, and composes clinical intraoral photos."
readme = "README.md"
license = "MIT"
license-files = ["LICENSE"]
authors = [{ name = "jimwellh" }]
requires-python = ">=3.12"
dependencies = [
  "numpy>=2.0",
  "opencv-python-headless>=4.10",
  "pillow>=11.0",
  "pi-heif>=1.0",
  "structlog>=25.1",
  "typer>=0.15",
]

[project.scripts]
dio = "dental_image_organizer.cli:app"
dental-image-organizer = "dental_image_organizer.cli:app"

[dependency-groups]
dev = [
  "hypothesis>=6.120",
  "import-linter>=2.1",
  "mypy>=1.15",
  "pillow-heif>=0.21",
  "pytest>=8.3",
  "ruff>=0.9",
]

[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/dental_image_organizer"]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "N", "SIM", "RUF"]

[tool.mypy]
python_version = "3.12"
strict = true
files = ["src"]

[[tool.mypy.overrides]]
module = ["pi_heif", "pi_heif.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["tests"]
addopts = "-ra --strict-markers"
markers = ["golden: needs real photos in local_fixtures/ (skipped when absent)"]

[tool.importlinter]
root_package = "dental_image_organizer"

[[tool.importlinter.contracts]]
name = "core is pure: no services or CLI imports"
type = "forbidden"
source_modules = ["dental_image_organizer.core"]
forbidden_modules = ["dental_image_organizer.services", "dental_image_organizer.cli"]
```

- [ ] **Step 2: Write `.python-version`**

```
3.12
```

- [ ] **Step 3: Create the package skeleton**

`src/dental_image_organizer/__init__.py`:

```python
"""Dental-Image-Organizer: local alignment and composition of clinical intraoral photos."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dental-image-organizer")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"
```

`src/dental_image_organizer/core/__init__.py`:

```python
"""Pure image processing: numpy arrays in, numpy arrays out, no file or network I/O."""
```

`src/dental_image_organizer/services/__init__.py`:

```python
"""Feature orchestration on top of core/."""
```

- [ ] **Step 4: Lock and install**

Run: `uv lock` then `uv sync`
Expected: `uv.lock` is created. A `.venv/` is created with Python 3.12 and the dev group.

- [ ] **Step 5: Write the failing tests**

`tests/unit/test_cli_version.py`:

```python
from typer.testing import CliRunner

from dental_image_organizer import __version__
from dental_image_organizer.cli import app


def test_version_flag_prints_package_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
```

`tests/unit/test_log.py`:

```python
import io
import sys

import pytest
import structlog

from dental_image_organizer.log import configure_logging


def test_logging_follows_a_replaced_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stderr", first)
    configure_logging("INFO")
    first.close()  # e.g. a CLI test runner restored and closed its captured stream
    monkeypatch.setattr(sys, "stderr", second)

    structlog.get_logger("test").warning("still_logged")

    assert "still_logged" in second.getvalue()


def test_level_filters_lower_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    configure_logging("WARNING")

    structlog.get_logger("test").info("hidden")
    structlog.get_logger("test").warning("shown")

    assert "hidden" not in stream.getvalue()
    assert "shown" in stream.getvalue()
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli_version.py tests/unit/test_log.py -v`
Expected: FAIL. Collection errors `ModuleNotFoundError: No module named 'dental_image_organizer.cli'`
and `... 'dental_image_organizer.log'`.

- [ ] **Step 7: Implement errors, logging, CLI, and `__main__`**

`src/dental_image_organizer/errors.py`:

```python
"""Domain exceptions (spec §4.13). Each class carries a stable machine-readable `code`."""

from typing import Literal

SkipReason = Literal["video", "raw", "sidecar", "unknown"]


class DioError(Exception):
    code = "dio_error"


class UnsupportedFormatError(DioError):
    """The file is not a HEIC, JPEG, or PNG photo; `reason` groups skip summaries (spec §3.1)."""

    code = "unsupported_format"

    def __init__(self, message: str, *, reason: SkipReason) -> None:
        super().__init__(message)
        self.reason: SkipReason = reason


class CorruptImageError(DioError):
    code = "corrupt_image"


class InvalidNameError(DioError):
    code = "invalid_name"
```

`src/dental_image_organizer/log.py`:

```python
"""structlog configuration. CLI logs go to stderr so stdout carries only results."""

import logging
import sys

import structlog


def _stderr_logger(*_args: object) -> structlog.PrintLogger:
    # Look up sys.stderr per logger rather than once: test runners and servers replace the
    # stream after setup, and writing to a stale, closed stream would raise inside callers.
    return structlog.PrintLogger(file=sys.stderr)


def configure_logging(level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=_stderr_logger,
        cache_logger_on_first_use=False,
    )
```

`src/dental_image_organizer/cli.py`. Task 11 replaces this file with the version that adds
`process`.

```python
"""Command-line interface (spec §4.10)."""

from typing import Annotated

import typer

from dental_image_organizer import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_print_version, is_eager=True, help="Print the version and exit."
        ),
    ] = False,
) -> None:
    """Align, color-match, and compose clinical intraoral photos."""
```

`src/dental_image_organizer/__main__.py`:

```python
from dental_image_organizer.cli import app

app()
```

- [ ] **Step 8: Run the tests and the quality gates**

Run: `uv run pytest -v`
Expected: 3 passed.

Run: `uv run ruff check; uv run ruff format --check; uv run mypy; uv run lint-imports`
Expected:

- `All checks passed!`
- `... files already formatted`
- `Success: no issues found`
- `Contracts: 1 kept, 0 broken.`

Run: `uv run dio --version`
Expected: `0.1.0.dev0`

- [ ] **Step 9: Add CI**

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  python:
    name: Python (${{ matrix.os }})
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
      - run: uv sync --locked
      - run: uv run ruff check
      - run: uv run ruff format --check
      - run: uv run mypy
      - run: uv run lint-imports
      - run: uv run pytest
```

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml .python-version uv.lock .github/workflows/ci.yml src tests
git commit -m "build: scaffold Python package, logging, CLI skeleton, and CI"
```

---

### Task 2: Normalized projective transforms

**Files:**

- Create: `src/dental_image_organizer/core/transform.py`
- Test: `tests/unit/test_transform.py`

**Interfaces:**

- Consumes: nothing.
- Produces, all in `dental_image_organizer.core.transform`, usually imported as `tf`:
  - Types:
    - `Matrix = npt.NDArray[np.float64]` (3 × 3)
    - `Points = npt.NDArray[np.float64]` (N × 2)
    - `SimilarityParams(scale, angle_deg, tx, ty, mirror)`
  - Constructors:
    - `identity() -> Matrix`
    - `scaling(s) -> Matrix`
    - `translation(tx, ty) -> Matrix`
    - `similarity(scale, angle_deg, tx, ty, *, mirror=False) -> Matrix`
    - `from_affine(m2x3) -> Matrix`
  - Operations:
    - `normalize(h) -> Matrix`
    - `invert(h) -> Matrix`
    - `map_points(h, points) -> Points`
  - Normalized ↔ pixel conversion:
    - `to_pixel(h_norm, in_width, out_width) -> Matrix`
    - `to_normalized(h_px, in_width, out_width) -> Matrix`
  - Similarity helpers:
    - `decompose_similarity(h) -> SimilarityParams`
    - `fit_similarity(src, dst) -> Matrix`
    - `closest_similarity(h, frame_width, frame_height) -> Matrix`

Normalized coordinates divide both axes by the image **width** (spec §4.8). That is why one
stored matrix renders previews and full-resolution exports identically.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_transform.py`:

```python
import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from dental_image_organizer.core import transform as tf

angles = st.floats(min_value=-170, max_value=170)
scales = st.floats(min_value=0.1, max_value=10)
offsets = st.floats(min_value=-1000, max_value=1000)
widths = st.floats(min_value=16, max_value=10000)


@given(scales, angles, offsets, offsets, st.booleans())
def test_decompose_recovers_similarity_parameters(
    scale: float, angle: float, tx: float, ty: float, mirror: bool
) -> None:
    params = tf.decompose_similarity(tf.similarity(scale, angle, tx, ty, mirror=mirror))

    assert params.scale == pytest.approx(scale, rel=1e-9)
    assert params.angle_deg == pytest.approx(angle, abs=1e-7)
    assert (params.tx, params.ty) == pytest.approx((tx, ty), abs=1e-7)
    assert params.mirror is mirror


@given(scales, angles, offsets, offsets, widths, widths)
def test_normalized_and_pixel_round_trip(
    scale: float, angle: float, tx: float, ty: float, in_w: float, out_w: float
) -> None:
    h_px = tf.similarity(scale, angle, tx, ty)

    back = tf.to_pixel(tf.to_normalized(h_px, in_w, out_w), in_w, out_w)

    np.testing.assert_allclose(back, h_px, rtol=1e-9, atol=1e-6)


def test_normalized_matrix_is_resolution_independent() -> None:
    # One stored edit must render the same content at preview and full resolution.
    h_norm = tf.to_normalized(tf.similarity(0.5, 10, 30, 40), in_width=4000, out_width=2000)
    point_full = np.array([[1200.0, 900.0]])

    full = tf.map_points(tf.to_pixel(h_norm, 4000, 2000), point_full)
    preview = tf.map_points(tf.to_pixel(h_norm, 1000, 500), point_full / 4)

    np.testing.assert_allclose(preview, full / 4, atol=1e-9)


@given(scales, angles, offsets, offsets)
def test_invert_composes_to_identity(scale: float, angle: float, tx: float, ty: float) -> None:
    h = tf.similarity(scale, angle, tx, ty)

    np.testing.assert_allclose(tf.invert(h) @ h, np.eye(3), atol=1e-9)


@given(scales, angles, offsets, offsets, st.booleans())
def test_fit_similarity_recovers_exact_transform(
    scale: float, angle: float, tx: float, ty: float, mirror: bool
) -> None:
    truth = tf.similarity(scale, angle, tx, ty, mirror=mirror)
    src = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 60.0], [0.0, 60.0], [37.0, 12.0]])

    fitted = tf.fit_similarity(src, tf.map_points(truth, src))

    np.testing.assert_allclose(fitted, truth, rtol=1e-7, atol=1e-6)


def test_closest_similarity_of_similarity_is_itself() -> None:
    h = tf.similarity(1.3, -8.0, 12.0, -7.0)

    np.testing.assert_allclose(tf.closest_similarity(h, 1.0, 0.75), h, atol=1e-9)


def test_closest_similarity_drops_perspective() -> None:
    h = tf.similarity(1.0, 3.0, 0.02, 0.01).copy()
    h[2, 0] = 0.05  # mild keystone

    approx = tf.closest_similarity(h, 1.0, 0.75)

    np.testing.assert_allclose(approx[2], [0.0, 0.0, 1.0])
    assert tf.decompose_similarity(approx).angle_deg == pytest.approx(3.0, abs=1.5)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_transform.py -v`
Expected: FAIL. `ImportError: cannot import name 'transform' from 'dental_image_organizer.core'`.

- [ ] **Step 3: Implement `core/transform.py`**

```python
"""Projective 3 x 3 transforms in normalized coordinates (spec §4.8).

Points are divided by the image width on both axes, so one stored matrix is valid for previews
and full-resolution renders alike.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Matrix = npt.NDArray[np.float64]
Points = npt.NDArray[np.float64]

_MIRROR_X = np.diag([-1.0, 1.0, 1.0])


@dataclass(frozen=True)
class SimilarityParams:
    scale: float
    angle_deg: float
    tx: float
    ty: float
    mirror: bool


def identity() -> Matrix:
    return np.eye(3, dtype=np.float64)


def scaling(s: float) -> Matrix:
    return np.diag([s, s, 1.0])


def translation(tx: float, ty: float) -> Matrix:
    m = identity()
    m[0, 2], m[1, 2] = tx, ty
    return m


def similarity(
    scale: float, angle_deg: float, tx: float, ty: float, *, mirror: bool = False
) -> Matrix:
    """x' = scale * R(angle) * F * x + t, where F mirrors the x axis when `mirror` is set."""
    a = math.radians(angle_deg)
    c, s = scale * math.cos(a), scale * math.sin(a)
    m = np.array([[c, -s, tx], [s, c, ty], [0.0, 0.0, 1.0]])
    return m @ _MIRROR_X if mirror else m


def from_affine(m: npt.ArrayLike) -> Matrix:
    affine = np.asarray(m, dtype=np.float64).reshape(2, 3)
    return np.vstack([affine, [0.0, 0.0, 1.0]])


def normalize(h: Matrix) -> Matrix:
    return h / float(h[2, 2])


def invert(h: Matrix) -> Matrix:
    return normalize(np.linalg.inv(h))


def map_points(h: Matrix, points: npt.ArrayLike) -> Points:
    p = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    hom = np.hstack([p, np.ones((len(p), 1))]) @ h.T
    return hom[:, :2] / hom[:, 2:3]


def to_pixel(h_norm: Matrix, in_width: float, out_width: float) -> Matrix:
    """Convert a normalized in->out matrix to pixel units for the given image widths."""
    return scaling(out_width) @ h_norm @ scaling(1.0 / in_width)


def to_normalized(h_px: Matrix, in_width: float, out_width: float) -> Matrix:
    return scaling(1.0 / out_width) @ h_px @ scaling(in_width)


def decompose_similarity(h: Matrix) -> SimilarityParams:
    """Read scale, rotation, translation, and mirror from the affine part of `h`."""
    m = normalize(h)
    mirror = bool(np.linalg.det(m[:2, :2]) < 0)
    proper = m @ _MIRROR_X if mirror else m
    a, b = proper[0, 0], proper[1, 0]
    return SimilarityParams(
        scale=float(math.hypot(a, b)),
        angle_deg=float(math.degrees(math.atan2(b, a))),
        tx=float(m[0, 2]),
        ty=float(m[1, 2]),
        mirror=mirror,
    )


def fit_similarity(src: npt.ArrayLike, dst: npt.ArrayLike) -> Matrix:
    """Least-squares similarity mapping src onto dst; a mirror is used only if it fits better."""
    s = np.asarray(src, dtype=np.float64).reshape(-1, 2)
    d = np.asarray(dst, dtype=np.float64).reshape(-1, 2)
    best: Matrix | None = None
    best_error = math.inf
    for mirror in (False, True):
        candidate = _umeyama(s * [-1.0, 1.0], d) @ _MIRROR_X if mirror else _umeyama(s, d)
        error = float(np.sum((map_points(candidate, s) - d) ** 2))
        if error < best_error - 1e-12:
            best, best_error = candidate, error
    assert best is not None
    return best


def closest_similarity(h: Matrix, frame_width: float, frame_height: float) -> Matrix:
    """Similarity that best reproduces where `h` sends the output frame's four corners.

    Used by "Reset perspective" (spec §4.8): the fit is done on the photo points that `h`
    maps onto the output corners.
    """
    corners = np.array(
        [[0.0, 0.0], [frame_width, 0.0], [frame_width, frame_height], [0.0, frame_height]]
    )
    photo_points = map_points(invert(h), corners)
    return fit_similarity(photo_points, corners)


def _umeyama(src: Points, dst: Points) -> Matrix:
    mu_s, mu_d = src.mean(axis=0), dst.mean(axis=0)
    xs, xd = src - mu_s, dst - mu_d
    u, sigma, vt = np.linalg.svd(xd.T @ xs / len(src))
    d = np.diag([1.0, float(np.sign(np.linalg.det(u) * np.linalg.det(vt))) or 1.0])
    rotation = u @ d @ vt
    variance = float((xs**2).sum() / len(src))
    scale = float(np.trace(np.diag(sigma) @ d)) / variance
    t = mu_d - scale * rotation @ mu_s
    m = identity()
    m[:2, :2] = scale * rotation
    m[:2, 2] = t
    return m
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_transform.py -v`
Expected: 7 passed.

- [ ] **Step 5: Quality gates and commit**

Run: `uv run ruff check; uv run ruff format --check; uv run mypy; uv run lint-imports`
Expected: all clean.

```bash
git add src/dental_image_organizer/core/transform.py tests/unit/test_transform.py
git commit -m "feat(core): add normalized projective transform helpers"
```

---

### Task 3: Export file names

**Files:**

- Create: `src/dental_image_organizer/core/naming.py`
- Test: `tests/unit/test_naming.py`

**Interfaces:**

- Consumes: `errors.InvalidNameError` (Task 1).
- Produces, all in `dental_image_organizer.core.naming`:
  - Constants: `MAX_COMPONENT_CHARS = 80`, `MAX_FILENAME_BYTES = 200`
  - `sanitize_component(text: str) -> str`
  - `photo_filename(patient: str, title: str, captured_at: datetime) -> str`
  - `sequence_filename(patient: str, title: str, generated_on: date) -> str`
  - `composite_filename(patient: str, tag: str, captured_on: date) -> str`
  - `patient_export_zip_name(patient: str, generated_on: date) -> str`
  - `unique_name(name: str, taken: Iterable[str]) -> str`

Rules (spec §4.9):

- Sanitizing: NFC, collapse whitespace, remove `/ \ : * ? " < > |` and control characters,
  then truncate to 80 characters.
- A name that is empty after sanitizing raises `InvalidNameError`.
- A full file name is at most 200 UTF-8 bytes. When it is too long, the title or tag is trimmed
  before the patient name, because exports are found by patient name.
- Collisions add `_2`, `_3`, … before the extension. Comparison ignores case and Unicode
  normalization, as APFS and NTFS do.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_naming.py`:

```python
import unicodedata
from datetime import date, datetime, timedelta, timezone

import pytest

from dental_image_organizer.core import naming
from dental_image_organizer.errors import InvalidNameError


def test_photo_filename_matches_requirements_example() -> None:
    name = naming.photo_filename(
        "AlexWang", "implant maxillary right first molar", datetime(2026, 10, 16, 14, 30, 59)
    )

    assert name == "AlexWang_implant maxillary right first molar_20261016_1430.jpg"


def test_sequence_and_composite_filenames_match_requirements_examples() -> None:
    assert (
        naming.sequence_filename(
            "AlexWang", "implant maxillary right first molar", date(2026, 10, 16)
        )
        == "AlexWang_implant maxillary right first molar_Procedure_20261016.jpg"
    )
    assert (
        naming.composite_filename(
            "AlexWang",
            "Comprehensive oral evaluation and full mouth records",
            date(2026, 10, 16),
        )
        == "AlexWang_Comprehensive oral evaluation and full mouth records_Composite_20261016.jpg"
    )


def test_patient_export_zip_name() -> None:
    assert naming.patient_export_zip_name("Alex Wang", date(2026, 10, 16)) == (
        "Alex Wang_Export_20261016.zip"
    )


def test_capture_time_uses_the_recorded_wall_clock_not_the_machine_timezone() -> None:
    taipei = timezone(timedelta(hours=8))

    name = naming.photo_filename("A", "B", datetime(2026, 10, 16, 14, 30, tzinfo=taipei))

    assert name.endswith("_20261016_1430.jpg")


def test_nfd_and_nfc_names_produce_identical_file_names() -> None:
    nfd = unicodedata.normalize("NFD", "王小明 Café")
    nfc = unicodedata.normalize("NFC", "王小明 Café")

    assert naming.sanitize_component(nfd) == naming.sanitize_component(nfc) == nfc


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Alex   Wang ", "Alex Wang"),
        ('a/b\\c:d*e?f"g<h>i|j', "abcdefghij"),
        ("line\nbreak\ttab", "line break tab"),
        ("x / y", "x y"),
    ],
)
def test_sanitize_component(raw: str, expected: str) -> None:
    assert naming.sanitize_component(raw) == expected


def test_component_is_truncated_to_80_characters() -> None:
    assert len(naming.sanitize_component("x" * 200)) == naming.MAX_COMPONENT_CHARS


def test_empty_after_sanitizing_is_rejected() -> None:
    with pytest.raises(InvalidNameError):
        naming.sanitize_component(' /:*?"<> ')


def test_long_multibyte_names_fit_200_bytes_and_keep_the_patient() -> None:
    patient = "王" * 80  # 240 bytes in UTF-8 on its own
    title = "植" * 80

    name = naming.photo_filename(patient, title, datetime(2026, 10, 16, 9, 5))

    assert len(name.encode()) <= naming.MAX_FILENAME_BYTES
    assert name.endswith("_20261016_0905.jpg")
    assert name.startswith("王")


def test_unique_name_adds_numeric_suffixes() -> None:
    taken = {"a_b_20261016_1430.jpg", "A_B_20261016_1430_2.JPG"}

    assert naming.unique_name("a_b_20261016_1430.jpg", taken) == "a_b_20261016_1430_3.jpg"
    assert naming.unique_name("free.jpg", taken) == "free.jpg"


def test_unique_name_treats_nfd_and_nfc_as_the_same_file() -> None:
    taken = {unicodedata.normalize("NFD", "王小明_é.jpg")}

    assert naming.unique_name(unicodedata.normalize("NFC", "王小明_é.jpg"), taken) == (
        unicodedata.normalize("NFC", "王小明_é_2.jpg")
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_naming.py -v`
Expected: FAIL. `ImportError: cannot import name 'naming' from 'dental_image_organizer.core'`.

- [ ] **Step 3: Implement `core/naming.py`**

```python
"""Export file names (spec §4.9)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import date, datetime

from dental_image_organizer.errors import InvalidNameError

MAX_COMPONENT_CHARS = 80
MAX_FILENAME_BYTES = 200

_WHITESPACE = re.compile(r"\s+")
_FORBIDDEN = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')


def sanitize_component(text: str) -> str:
    # NFC first: macOS returns NFD file names, so one typed name can arrive in either form.
    cleaned = unicodedata.normalize("NFC", text)
    cleaned = _WHITESPACE.sub(" ", cleaned)
    cleaned = _FORBIDDEN.sub("", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    cleaned = cleaned[:MAX_COMPONENT_CHARS].rstrip()
    if not cleaned:
        raise InvalidNameError(f"{text!r} is empty after removing characters not allowed in names")
    return cleaned


def photo_filename(patient: str, title: str, captured_at: datetime) -> str:
    return _join(patient, title, f"_{captured_at:%Y%m%d}_{captured_at:%H%M}.jpg")


def sequence_filename(patient: str, title: str, generated_on: date) -> str:
    return _join(patient, title, f"_Procedure_{generated_on:%Y%m%d}.jpg")


def composite_filename(patient: str, tag: str, captured_on: date) -> str:
    return _join(patient, tag, f"_Composite_{captured_on:%Y%m%d}.jpg")


def patient_export_zip_name(patient: str, generated_on: date) -> str:
    name = f"{sanitize_component(patient)}_Export_{generated_on:%Y%m%d}.zip"
    return _trim_to_bytes(name)


def unique_name(name: str, taken: Iterable[str]) -> str:
    """Return `name`, or `name` with `_2`, `_3`, ... before the extension if it is taken.

    Comparison ignores case and Unicode normalization, because the default macOS (APFS) and
    Windows (NTFS) file systems do too.
    """
    keys = {_key(t) for t in taken}
    if _key(name) not in keys:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    n = 2
    while True:
        candidate = f"{stem}_{n}.{ext}" if ext else f"{stem}_{n}"
        if _key(candidate) not in keys:
            return candidate
        n += 1


def _join(patient: str, label: str, suffix: str) -> str:
    p, t = sanitize_component(patient), sanitize_component(label)
    # Trim the label before the patient: exports are found by patient name.
    while _size(p, t, suffix) > MAX_FILENAME_BYTES and len(t) > 1:
        t = t[:-1].rstrip() or t[:1]
    while _size(p, t, suffix) > MAX_FILENAME_BYTES and len(p) > 1:
        p = p[:-1].rstrip() or p[:1]
    return f"{p}_{t}{suffix}"


def _size(patient: str, label: str, suffix: str) -> int:
    return len(f"{patient}_{label}{suffix}".encode())


def _trim_to_bytes(name: str) -> str:
    stem, _, ext = name.rpartition(".")
    while len(name.encode()) > MAX_FILENAME_BYTES and len(stem) > 1:
        stem = stem[:-1]
        name = f"{stem}.{ext}"
    return name


def _key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_naming.py -v`
Expected: 14 passed.

- [ ] **Step 5: Quality gates and commit**

Run: `uv run ruff check; uv run ruff format --check; uv run mypy; uv run lint-imports`
Expected: all clean.

```bash
git add src/dental_image_organizer/core/naming.py tests/unit/test_naming.py
git commit -m "feat(core): add export naming rules with NFC sanitizing and collision suffixes"
```

---

### Task 4: Image decoding, metadata, and JPEG encoding

**Files:**

- Create:
  - `src/dental_image_organizer/core/imageio.py`
  - `tests/support/__init__.py` (empty)
  - `tests/support/icc.py`
- Test: `tests/unit/test_imageio.py`

**Interfaces:**

- Consumes: `errors.CorruptImageError`, `errors.UnsupportedFormatError` (Task 1).
- Produces, all in `dental_image_organizer.core.imageio`:
  - Types and constants:
    - `RGBImage = npt.NDArray[np.uint8]` (H × W × 3, sRGB, upright)
    - `ImageFormat = Literal["heic", "jpeg", "png"]`
    - `MAX_PIXELS = 200_000_000`
  - `Metadata(captured_at: datetime | None, base_tags, exif_tags, gps_tags)` (frozen)
  - `DecodedImage(rgb, metadata, format)` with property `.size -> (width, height)`
  - `sniff_format(data: bytes) -> ImageFormat`. Raises `UnsupportedFormatError` with reason
    `video | raw | sidecar | unknown`.
  - `decode(data: bytes) -> DecodedImage`. Raises `CorruptImageError`.
  - `resize_long_edge(rgb, long_edge, *, upscale=False) -> tuple[RGBImage, float]`
  - `encode_jpeg(rgb, metadata=None, *, quality=92, strip_gps=True) -> bytes`
  - `_SRGB_ICC: bytes`. Private; the tests compare against it.
- Also produces `tests/support/icc.py`: `display_p3_like_profile() -> bytes`. Pillow can only
  create sRGB profiles, so the test builds a wide-gamut one by hand.

- [ ] **Step 1: Write the ICC test helper**

`tests/support/__init__.py`: create an empty file.

`tests/support/icc.py`:

```python
"""Build a minimal ICC v2 matrix/TRC display profile for tests.

Pillow can only create sRGB profiles, but the decoder must be tested against a wide-gamut
profile like the Display P3 profile iPhones embed.
"""

import struct

# Display P3 colorants adapted to the D50 profile connection space.
DISPLAY_P3_D50 = {
    b"rXYZ": (0.5151, 0.2412, -0.0011),
    b"gXYZ": (0.2920, 0.6922, 0.0419),
    b"bXYZ": (0.1571, 0.0666, 0.7841),
}
_D50 = (0.9642, 1.0, 0.8249)


def _s15f16(value: float) -> bytes:
    return struct.pack(">i", round(value * 65536))


def _xyz(x: float, y: float, z: float) -> bytes:
    return b"XYZ " + bytes(4) + _s15f16(x) + _s15f16(y) + _s15f16(z)


def _curve(gamma: float) -> bytes:
    return b"curv" + bytes(4) + struct.pack(">IH", 1, round(gamma * 256)) + bytes(2)


def _desc(text: str) -> bytes:
    ascii_text = text.encode("ascii") + b"\x00"
    body = b"desc" + bytes(4) + struct.pack(">I", len(ascii_text)) + ascii_text
    body += struct.pack(">II", 0, 0) + struct.pack(">HB", 0, 0) + bytes(67)
    return body


def _text(text: str) -> bytes:
    return b"text" + bytes(4) + text.encode("ascii") + b"\x00"


def _pad(data: bytes) -> bytes:
    return data + bytes(-len(data) % 4)


def matrix_profile(colorants: dict[bytes, tuple[float, float, float]], gamma: float = 2.2) -> bytes:
    tags = [
        (b"desc", _desc("Test RGB")),
        (b"cprt", _text("Public domain")),
        (b"wtpt", _xyz(*_D50)),
        *((sig, _xyz(*xyz)) for sig, xyz in colorants.items()),
        *((sig, _curve(gamma)) for sig in (b"rTRC", b"gTRC", b"bTRC")),
    ]
    table_size = 4 + 12 * len(tags)
    offset = 128 + table_size
    table, data = struct.pack(">I", len(tags)), b""
    for sig, payload in tags:
        table += sig + struct.pack(">II", offset + len(data), len(payload))
        data += _pad(payload)
    size = 128 + table_size + len(data)
    header = (
        struct.pack(">I", size)
        + bytes(4)
        + struct.pack(">I", 0x02100000)
        + b"mntrRGB XYZ "
        + bytes(12)
        + b"acsp"
        + bytes(24)
        + struct.pack(">I", 0)
        + _s15f16(_D50[0])
        + _s15f16(_D50[1])
        + _s15f16(_D50[2])
        + bytes(48)
    )
    assert len(header) == 128
    return header + table + data


def display_p3_like_profile() -> bytes:
    return matrix_profile(DISPLAY_P3_D50)
```

- [ ] **Step 2: Write the failing test**

`tests/unit/test_imageio.py`:

```python
import io
from datetime import datetime, timedelta, timezone

import numpy as np
import pillow_heif
import pytest
from PIL import ExifTags, Image

from dental_image_organizer.core import imageio
from dental_image_organizer.errors import CorruptImageError, UnsupportedFormatError
from support.icc import display_p3_like_profile


def _gradient(width: int = 64, height: int = 48) -> np.ndarray:
    x = np.linspace(0, 255, width, dtype=np.float32)
    y = np.linspace(0, 255, height, dtype=np.float32)
    rgb = np.stack([*np.meshgrid(x, y), np.full((height, width), 128, np.float32)], axis=-1)
    return rgb.astype(np.uint8)


def _jpeg(img: Image.Image, exif: Image.Exif | None = None, **kwargs: object) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95, exif=(exif or Image.Exif()).tobytes(), **kwargs)
    return buf.getvalue()


def _exif(
    orientation: int = 1,
    taken: str | None = "2026:10:16 14:30:12",
    offset: str | None = "+08:00",
    gps: bool = False,
) -> Image.Exif:
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = orientation
    exif[ExifTags.Base.Make] = "Apple"
    exif[ExifTags.Base.Model] = "iPhone 16 Pro"
    exif[ExifTags.Base.Software] = "18.0"
    sub = exif.get_ifd(ExifTags.IFD.Exif)
    if taken:
        sub[ExifTags.Base.DateTimeOriginal] = taken
    if offset:
        sub[ExifTags.Base.OffsetTimeOriginal] = offset
    sub[ExifTags.Base.LensModel] = "iPhone 16 Pro back camera"
    if gps:
        exif.get_ifd(ExifTags.IFD.GPSInfo)[ExifTags.GPS.GPSLatitudeRef] = "N"
    return exif


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"\xff\xd8\xff\xe0" + bytes(20), "jpeg"),
        (b"\x89PNG\r\n\x1a\n" + bytes(20), "png"),
        (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic", "heic"),
        (b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00mif1heic", "heic"),
    ],
)
def test_sniff_format_recognizes_supported_types(data: bytes, expected: str) -> None:
    assert imageio.sniff_format(data) == expected


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  ", "video"),
        (b"II*\x00" + bytes(20), "raw"),
        (b'<?xml version="1.0" encoding="UTF-8"?><plist/>', "sidecar"),
        (b"GIF89a" + bytes(20), "unknown"),
    ],
)
def test_sniff_format_rejects_with_reason(data: bytes, reason: str) -> None:
    with pytest.raises(UnsupportedFormatError) as info:
        imageio.sniff_format(data)

    assert info.value.reason == reason


def test_extension_is_ignored_and_content_decides() -> None:
    png = io.BytesIO()
    Image.fromarray(_gradient()).save(png, format="PNG")

    assert imageio.decode(png.getvalue()).format == "png"


def test_decode_jpeg_reads_capture_time_with_offset() -> None:
    decoded = imageio.decode(_jpeg(Image.fromarray(_gradient()), _exif()))

    assert decoded.metadata.captured_at == datetime(
        2026, 10, 16, 14, 30, 12, tzinfo=timezone(timedelta(hours=8))
    )
    assert decoded.size == (64, 48)


def test_missing_capture_time_is_none() -> None:
    decoded = imageio.decode(_jpeg(Image.fromarray(_gradient()), _exif(taken=None)))

    assert decoded.metadata.captured_at is None


def test_capture_time_without_offset_is_naive() -> None:
    decoded = imageio.decode(_jpeg(Image.fromarray(_gradient()), _exif(offset=None)))

    assert decoded.metadata.captured_at == datetime(2026, 10, 16, 14, 30, 12)


@pytest.mark.parametrize("orientation", range(1, 9))
def test_every_exif_orientation_is_made_upright(orientation: int) -> None:
    upright = np.zeros((40, 60, 3), np.uint8)
    upright[:10, :10] = 255  # bright marker in the top-left corner
    stored = Image.fromarray(upright)
    # Store the pixels the way a camera would before tagging them with `orientation`.
    inverse = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_90,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_270,
    }
    if orientation in inverse:
        stored = stored.transpose(inverse[orientation])
    buf = io.BytesIO()
    stored.save(buf, format="PNG", exif=_exif(orientation=orientation).tobytes())

    rgb = imageio.decode(buf.getvalue()).rgb

    assert rgb.shape == (40, 60, 3)
    assert rgb[:10, :10].mean() > 200
    assert rgb[-10:, -10:].mean() < 50


def test_display_p3_profile_is_converted_to_srgb() -> None:
    p3_red = np.full((8, 8, 3), (200, 40, 40), np.uint8)
    buf = io.BytesIO()
    Image.fromarray(p3_red).save(buf, format="PNG", icc_profile=display_p3_like_profile())

    rgb = imageio.decode(buf.getvalue()).rgb

    # P3 red lies outside sRGB, so the converted red is more saturated than the stored value.
    assert rgb[0, 0, 0] > 200
    assert rgb[0, 0, 1] < 40


def test_untagged_and_srgb_tagged_pixels_are_unchanged() -> None:
    pixels = _gradient()
    tagged = _jpeg(Image.fromarray(pixels), icc_profile=imageio._SRGB_ICC)
    untagged = _jpeg(Image.fromarray(pixels))

    a = imageio.decode(tagged).rgb.astype(int)
    b = imageio.decode(untagged).rgb.astype(int)

    assert np.abs(a - b).max() <= 2


def test_broken_icc_profile_falls_back_to_srgb() -> None:
    buf = io.BytesIO()
    Image.fromarray(_gradient()).save(buf, format="PNG", icc_profile=b"not an icc profile")

    assert imageio.decode(buf.getvalue()).rgb.shape == (48, 64, 3)


@pytest.mark.parametrize("mode", ["L", "LA", "P", "RGBA", "I;16"])
def test_non_rgb_png_modes_decode_to_three_channel_uint8(mode: str) -> None:
    img = Image.fromarray(_gradient()).convert(mode)
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    rgb = imageio.decode(buf.getvalue()).rgb

    assert rgb.dtype == np.uint8
    assert rgb.shape == (48, 64, 3)


def test_heic_decodes_with_metadata() -> None:
    heif = pillow_heif.from_pillow(Image.fromarray(_gradient()))
    buf = io.BytesIO()
    heif.save(buf, quality=90, exif=_exif().tobytes())

    decoded = imageio.decode(buf.getvalue())

    assert decoded.format == "heic"
    assert decoded.size == (64, 48)
    assert decoded.metadata.captured_at is not None


def test_truncated_file_is_corrupt() -> None:
    data = _jpeg(Image.fromarray(_gradient(640, 480)))

    with pytest.raises(CorruptImageError):
        imageio.decode(data[:200])


def test_oversized_image_is_rejected_before_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imageio, "MAX_PIXELS", 1000)

    with pytest.raises(CorruptImageError, match="limit"):
        imageio.decode(_jpeg(Image.fromarray(_gradient())))


def test_encode_keeps_capture_tags_resets_orientation_and_strips_gps() -> None:
    source = imageio.decode(_jpeg(Image.fromarray(_gradient()), _exif(orientation=1, gps=True)))

    out = Image.open(io.BytesIO(imageio.encode_jpeg(source.rgb, source.metadata)))
    exif = out.getexif()

    assert exif[ExifTags.Base.Orientation] == 1
    assert exif[ExifTags.Base.Make] == "Apple"
    assert ExifTags.Base.Software not in exif
    sub = exif.get_ifd(ExifTags.IFD.Exif)
    assert sub[ExifTags.Base.DateTimeOriginal] == "2026:10:16 14:30:12"
    assert sub[ExifTags.Base.OffsetTimeOriginal] == "+08:00"
    assert exif.get_ifd(ExifTags.IFD.GPSInfo) == {}
    assert out.info.get("icc_profile") == imageio._SRGB_ICC


def test_encode_can_keep_gps() -> None:
    source = imageio.decode(_jpeg(Image.fromarray(_gradient()), _exif(gps=True)))

    out = Image.open(io.BytesIO(imageio.encode_jpeg(source.rgb, source.metadata, strip_gps=False)))

    assert out.getexif().get_ifd(ExifTags.IFD.GPSInfo)[ExifTags.GPS.GPSLatitudeRef] == "N"


def test_resize_long_edge_downscales_and_reports_scale() -> None:
    rgb = np.zeros((1000, 3000, 3), np.uint8)

    small, scale = imageio.resize_long_edge(rgb, 1500)

    assert small.shape == (500, 1500, 3)
    assert scale == pytest.approx(0.5)


def test_resize_long_edge_does_not_upscale_by_default() -> None:
    rgb = np.zeros((100, 200, 3), np.uint8)

    same, scale = imageio.resize_long_edge(rgb, 1600)

    assert same is rgb
    assert scale == 1.0
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_imageio.py -v`
Expected: FAIL. `ImportError: cannot import name 'imageio' from 'dental_image_organizer.core'`.

- [ ] **Step 4: Implement `core/imageio.py`**

```python
"""Decoding, metadata, resizing, and JPEG encoding (spec §4.4)."""

from __future__ import annotations

import io
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, cast

import cv2
import numpy as np
import numpy.typing as npt
import pi_heif
import structlog
from PIL import ExifTags, Image, ImageCms, ImageOps

from dental_image_organizer.errors import CorruptImageError, UnsupportedFormatError

log = structlog.get_logger(__name__)

pi_heif.register_heif_opener()

MAX_PIXELS = 200_000_000
# Pillow raises DecompressionBombError above twice this value, so its guard matches ours.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS // 2

RGBImage = npt.NDArray[np.uint8]
ImageFormat = Literal["heic", "jpeg", "png"]

_HEIF_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"})
_VIDEO_BRANDS = frozenset({b"qt  ", b"isom", b"iso2", b"mp41", b"mp42", b"M4V ", b"avc1"})

# Tags kept on export; maker notes, thumbnails, and everything else are dropped.
_KEPT_BASE_TAGS = (ExifTags.Base.Make, ExifTags.Base.Model)
_KEPT_EXIF_TAGS = (
    ExifTags.Base.DateTimeOriginal,
    ExifTags.Base.OffsetTimeOriginal,
    ExifTags.Base.LensMake,
    ExifTags.Base.LensModel,
)
_OFFSET = re.compile(r"([+-])(\d{2}):(\d{2})")

_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
_SRGB_ICC = _SRGB.tobytes()


@dataclass(frozen=True)
class Metadata:
    captured_at: datetime | None = None
    base_tags: Mapping[int, Any] = field(default_factory=dict)
    exif_tags: Mapping[int, Any] = field(default_factory=dict)
    gps_tags: Mapping[int, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecodedImage:
    rgb: RGBImage
    metadata: Metadata
    format: ImageFormat

    @property
    def size(self) -> tuple[int, int]:
        return int(self.rgb.shape[1]), int(self.rgb.shape[0])


def sniff_format(data: bytes) -> ImageFormat:
    """Identify the format from magic bytes; file extensions are not trusted."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[4:8] == b"ftyp":
        brands = _ftyp_brands(data)
        if brands & _HEIF_BRANDS:
            return "heic"
        if brands & _VIDEO_BRANDS:
            raise UnsupportedFormatError("Videos are not supported.", reason="video")
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        raise UnsupportedFormatError(
            "DNG/ProRAW is not supported; export as HEIC or JPEG.", reason="raw"
        )
    if data[:64].lstrip().startswith(b"<?xml"):
        # Photos.app writes .AAE edit sidecars as XML property lists next to edited photos.
        raise UnsupportedFormatError(
            "Photos edit sidecars (.AAE) are not images.", reason="sidecar"
        )
    raise UnsupportedFormatError(
        "Unrecognized file type; supported formats are HEIC, JPEG, and PNG.", reason="unknown"
    )


def decode(data: bytes) -> DecodedImage:
    """Decode to upright sRGB pixels plus the metadata kept for export."""
    fmt = sniff_format(data)
    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if width * height > MAX_PIXELS:
                raise CorruptImageError(
                    f"Image is {width}x{height}; the limit is {MAX_PIXELS // 1_000_000} MP."
                )
            metadata = _read_metadata(img.getexif())
            icc = img.info.get("icc_profile")
            upright = ImageOps.exif_transpose(img)
            rgb = np.array(_to_srgb(upright, icc), dtype=np.uint8)
    except Image.DecompressionBombError as exc:
        raise CorruptImageError(f"Image exceeds the {MAX_PIXELS // 1_000_000} MP limit.") from exc
    except (OSError, SyntaxError, ValueError) as exc:
        raise CorruptImageError(f"Cannot decode {fmt.upper()} image: {exc}") from exc
    return DecodedImage(rgb=rgb, metadata=metadata, format=fmt)


def resize_long_edge(
    rgb: RGBImage, long_edge: int, *, upscale: bool = False
) -> tuple[RGBImage, float]:
    """Resize so the longer side is `long_edge`; returns the image and the applied scale."""
    h, w = rgb.shape[:2]
    scale = long_edge / max(h, w)
    if scale >= 1.0 and not upscale:
        return rgb, 1.0
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    resized = cast(RGBImage, cv2.resize(rgb, size, interpolation=interpolation))
    return resized, size[0] / w


def encode_jpeg(
    rgb: RGBImage,
    metadata: Metadata | None = None,
    *,
    quality: int = 92,
    strip_gps: bool = True,
) -> bytes:
    exif = Image.Exif()
    # Pixels are already upright; a stale orientation tag would rotate them a second time.
    exif[ExifTags.Base.Orientation] = 1
    if metadata is not None:
        for tag, value in metadata.base_tags.items():
            exif[tag] = value
        if metadata.exif_tags:
            exif.get_ifd(ExifTags.IFD.Exif).update(metadata.exif_tags)
        if not strip_gps and metadata.gps_tags:
            exif.get_ifd(ExifTags.IFD.GPSInfo).update(metadata.gps_tags)
    buffer = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb)).save(
        buffer,
        format="JPEG",
        quality=quality,
        subsampling=0,
        exif=exif.tobytes(),
        icc_profile=_SRGB_ICC,
    )
    return buffer.getvalue()


def _ftyp_brands(data: bytes) -> frozenset[bytes]:
    box_size = int.from_bytes(data[0:4], "big")
    end = min(len(data), max(box_size, 16))
    brands = {data[8:12]}
    brands.update(data[i : i + 4] for i in range(16, end - 3, 4))
    return frozenset(brands)


def _read_metadata(exif: Image.Exif) -> Metadata:
    exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    return Metadata(
        captured_at=_parse_capture_time(
            exif_ifd.get(ExifTags.Base.DateTimeOriginal),
            exif_ifd.get(ExifTags.Base.OffsetTimeOriginal),
        ),
        base_tags={tag: exif[tag] for tag in _KEPT_BASE_TAGS if tag in exif},
        exif_tags={tag: exif_ifd[tag] for tag in _KEPT_EXIF_TAGS if tag in exif_ifd},
        gps_tags=dict(exif.get_ifd(ExifTags.IFD.GPSInfo)),
    )


def _parse_capture_time(value: object, offset: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        captured = datetime.strptime(value.strip("\x00 "), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    match = _OFFSET.fullmatch(offset.strip("\x00 ")) if isinstance(offset, str) else None
    if match is None:
        return captured
    sign = 1 if match.group(1) == "+" else -1
    delta = timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
    return captured.replace(tzinfo=timezone(sign * delta))


def _to_srgb(img: Image.Image, icc: bytes | None) -> Image.Image:
    rgb = img if img.mode == "RGB" else img.convert("RGB")
    if not icc:
        return rgb
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        converted = ImageCms.profileToProfile(
            rgb, source, _SRGB, renderingIntent=ImageCms.Intent.PERCEPTUAL, outputMode="RGB"
        )
    except (ImageCms.PyCMSError, OSError) as exc:
        # A broken or non-RGB profile must not block import; treating pixels as sRGB is the
        # least surprising fallback.
        log.warning("icc_profile_ignored", error=str(exc))
        return rgb
    return converted if converted is not None else rgb
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_imageio.py -v`
Expected: 35 passed.

- [ ] **Step 6: Quality gates and commit**

Run: `uv run ruff check; uv run ruff format --check; uv run mypy; uv run lint-imports`
Expected: all clean.

```bash
git add src/dental_image_organizer/core/imageio.py tests/support tests/unit/test_imageio.py
git commit -m "feat(core): decode HEIC/JPEG/PNG to upright sRGB and encode JPEG with kept EXIF"
```

---

### Task 5: Lab conversion, tooth/gingiva masks, and synthetic scenes

**Files:**

- Create:
  - `src/dental_image_organizer/core/color.py` (Lab conversion only; Task 8 completes it)
  - `src/dental_image_organizer/core/masks.py`
  - `tests/support/synthetic.py`
- Test:
  - `tests/unit/test_lab.py`
  - `tests/unit/test_masks.py`

**Interfaces:**

- Consumes:
  - `imageio.RGBImage` (Task 4)
  - `transform` as `tf` (Task 2), used by the synthetic helpers
- Produces:
  - `color.LabImage = npt.NDArray[np.float32]`
  - `color.to_lab(rgb) -> LabImage`, with L in [0, 100] and a\*, b\* in natural units
  - `color.from_lab(lab) -> RGBImage`
  - `masks.BoolMask = npt.NDArray[np.bool_]`
  - `masks.tooth_mask(rgb) -> BoolMask`
  - `masks.gingiva_mask(rgb, teeth=None) -> BoolMask`
  - Test support, in `support.synthetic`:
    - `make_scene(seed, width=2400, height=1600, *, teeth=True, texture=True, specks=600) -> np.ndarray`
    - `warp(scene, h_px, size) -> np.ndarray`
    - `tilt_homography(width, height, degrees, focal=None) -> tf.Matrix`
    - `grade(rgb, *, gain=1.08, warm=6.0) -> np.ndarray`

Mask thresholds come from spec §4.7.1:

- **Teeth:** L above the 55th percentile, a\* < 14, and 4 < b\* < 45.
- **Gingiva:** a\* > 15 and L > 25, excluding teeth.
- **Cleanup:** open and close with an elliptical kernel of `min(h, w) / 110`, then drop
  connected components under 0.2% of the area.

- [ ] **Step 1: Write the synthetic scene helper**

`tests/support/synthetic.py`:

```python
"""Deterministic dental-like test scenes (spec §8.1). No patient data is ever needed."""

from __future__ import annotations

import cv2
import numpy as np

from dental_image_organizer.core import transform as tf

GUM_RGB = np.array([190, 95, 105], np.float32)
ENAMEL_RGB = np.array([232, 218, 186], np.float32)


def _smooth_noise(rng: np.random.Generator, height: int, width: int, cell: int) -> np.ndarray:
    small = rng.random((max(2, height // cell), max(2, width // cell))).astype(np.float32)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_CUBIC)


def make_scene(
    seed: int,
    width: int = 2400,
    height: int = 1600,
    *,
    teeth: bool = True,
    texture: bool = True,
    specks: int = 600,
) -> np.ndarray:
    """Pink gum with two rows of ivory teeth, multi-scale texture, and dark specks.

    The texture is a random field, so every neighborhood is unique and SIFT matches are
    unambiguous; the specks add sharp corners.
    """
    rng = np.random.default_rng(seed)
    field = np.zeros((height, width), np.float32)
    if texture:
        for cell, amplitude in ((200, 0.5), (40, 0.3), (8, 0.2)):
            field += amplitude * _smooth_noise(rng, height, width, cell)
        field = (field - field.mean()) / (field.std() + 1e-6)
    img = GUM_RGB + field[..., None] * np.array([18, 12, 12], np.float32)
    if teeth:
        layer = np.zeros((height, width), np.uint8)
        for row_y in (0.40, 0.62):
            for i in range(9):
                center = (
                    int(width * (0.08 + 0.84 * i / 8) + rng.integers(-15, 16)),
                    int(height * row_y + rng.integers(-20, 21)),
                )
                axes = (
                    int(width * 0.038 + rng.integers(-10, 11)),
                    int(height * 0.09 + rng.integers(-10, 11)),
                )
                cv2.ellipse(layer, center, axes, float(rng.uniform(-10, 10)), 0, 360, 255, -1)
        enamel = ENAMEL_RGB + field[..., None] * np.array([10, 10, 12], np.float32)
        img[layer > 0] = enamel[layer > 0]
    for _ in range(specks):
        center = (int(rng.integers(0, width)), int(rng.integers(0, height)))
        color = tuple(float(c) for c in rng.integers(40, 120, 3))
        cv2.circle(img, center, int(rng.integers(2, 7)), color, -1)
    return np.clip(img, 0, 255).astype(np.uint8)


def warp(scene: np.ndarray, h_px: tf.Matrix, size: tuple[int, int]) -> np.ndarray:
    return cv2.warpPerspective(
        scene, h_px, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )


def tilt_homography(
    width: int, height: int, degrees: float, focal: float | None = None
) -> tf.Matrix:
    """Homography of a camera tilted about the horizontal axis through the image center."""
    f = focal if focal is not None else float(width)
    k = np.array([[f, 0, width / 2], [0, f, height / 2], [0, 0, 1]])
    a = np.radians(degrees)
    r = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    return tf.normalize(k @ r @ np.linalg.inv(k))


def grade(rgb: np.ndarray, *, gain: float = 1.08, warm: float = 6.0) -> np.ndarray:
    """Imitate a dentist's edit: slightly brighter and warmer."""
    out = rgb.astype(np.float32) * gain + np.array([warm, 0, -warm], np.float32)
    return np.clip(out, 0, 255).astype(np.uint8)
```

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_lab.py`:

```python
import numpy as np
import pytest

from dental_image_organizer.core.color import from_lab, to_lab
from support.synthetic import make_scene


def test_lab_uses_natural_units() -> None:
    lab = to_lab(np.array([[[255, 255, 255], [0, 0, 0]]], np.uint8))

    assert lab[0, 0, 0] == pytest.approx(100.0, abs=0.01)
    assert lab[0, 1, 0] == pytest.approx(0.0, abs=0.01)
    assert np.abs(lab[0, :, 1:]).max() < 0.01


def test_lab_round_trip_is_within_one_level() -> None:
    rgb = make_scene(4, 300, 200)

    back = from_lab(to_lab(rgb))

    assert np.abs(back.astype(int) - rgb.astype(int)).max() <= 1
```

`tests/unit/test_masks.py`:

```python
import numpy as np

from dental_image_organizer.core.masks import gingiva_mask, tooth_mask
from support.synthetic import make_scene


def test_tooth_mask_finds_teeth_and_not_gum() -> None:
    scene = make_scene(1, 1200, 800)
    teeth = tooth_mask(scene)

    share = teeth.mean()
    assert 0.10 < share < 0.30
    assert teeth[int(800 * 0.40), int(1200 * 0.08)]  # center of the first upper tooth
    assert not teeth[20, 600]  # gum near the top edge


def test_gingiva_mask_excludes_teeth() -> None:
    scene = make_scene(2, 1200, 800)
    teeth = tooth_mask(scene)

    gums = gingiva_mask(scene, teeth)

    assert gums.mean() > 0.4
    assert not np.any(gums & teeth)


def test_scene_without_teeth_gives_an_empty_tooth_mask() -> None:
    scene = make_scene(3, 1200, 800, teeth=False)

    assert tooth_mask(scene).mean() < 0.01
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_lab.py tests/unit/test_masks.py -v`
Expected: FAIL. `ModuleNotFoundError: No module named 'dental_image_organizer.core.color'`
(and `... core.masks`).

- [ ] **Step 4: Implement Lab conversion and masks**

`src/dental_image_organizer/core/color.py` (Task 8 replaces this file with the full version):

```python
"""Color matching to the reference (spec §4.5.2): monotone per-channel curves in CIE Lab."""

from __future__ import annotations

from typing import cast

import cv2
import numpy as np
import numpy.typing as npt

from dental_image_organizer.core.imageio import RGBImage

LabImage = npt.NDArray[np.float32]


def to_lab(rgb: RGBImage) -> LabImage:
    """sRGB uint8 to Lab float32 with L in [0, 100] and a*, b* in their natural units."""
    return cast(LabImage, cv2.cvtColor(rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB))


def from_lab(lab: LabImage) -> RGBImage:
    rgb = cv2.cvtColor(lab.astype(np.float32), cv2.COLOR_LAB2RGB)
    return np.clip(np.rint(rgb * 255.0), 0, 255).astype(np.uint8)
```

`src/dental_image_organizer/core/masks.py`:

```python
"""Tooth and gingiva masks from CIE Lab thresholds (spec §4.7.1)."""

from __future__ import annotations

import cv2
import numpy as np
import numpy.typing as npt

from dental_image_organizer.core.color import to_lab
from dental_image_organizer.core.imageio import RGBImage

BoolMask = npt.NDArray[np.bool_]

TOOTH_L_PERCENTILE = 55.0
TOOTH_MAX_A = 14.0
TOOTH_B_RANGE = (4.0, 45.0)
GINGIVA_MIN_A = 15.0
GINGIVA_MIN_L = 25.0
KERNEL_DIVISOR = 110
MIN_COMPONENT_SHARE = 0.002


def tooth_mask(rgb: RGBImage) -> BoolMask:
    lab = to_lab(rgb)
    lightness, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    threshold = float(np.percentile(lightness, TOOTH_L_PERCENTILE))
    raw = (
        (lightness > threshold)
        & (a < TOOTH_MAX_A)
        & (b > TOOTH_B_RANGE[0])
        & (b < TOOTH_B_RANGE[1])
    )
    return _clean(raw)


def gingiva_mask(rgb: RGBImage, teeth: BoolMask | None = None) -> BoolMask:
    lab = to_lab(rgb)
    raw = (lab[..., 1] > GINGIVA_MIN_A) & (lab[..., 0] > GINGIVA_MIN_L)
    if teeth is None:
        teeth = tooth_mask(rgb)
    return _clean(raw & ~teeth)


def _clean(mask: BoolMask) -> BoolMask:
    h, w = mask.shape
    k = max(3, round(min(h, w) / KERNEL_DIVISOR)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    opened = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, kernel)
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    keep = np.zeros(count, dtype=bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= MIN_COMPONENT_SHARE * h * w
    return np.asarray(keep[labels], dtype=np.bool_)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_lab.py tests/unit/test_masks.py -v`
Expected: 5 passed. On the synthetic scene the tooth share is about 0.20 and the gum share
about 0.78.

- [ ] **Step 6: Quality gates and commit**

Run: `uv run ruff check; uv run ruff format --check; uv run mypy; uv run lint-imports`
Expected: all clean.

```bash
git add src/dental_image_organizer/core/color.py src/dental_image_organizer/core/masks.py tests/support/synthetic.py tests/unit/test_lab.py tests/unit/test_masks.py
git commit -m "feat(core): add Lab conversion, tooth/gingiva masks, and synthetic test scenes"
```

---

### Task 6: Similarity alignment to the reference

**Files:**

- Create:
  - `src/dental_image_organizer/core/align.py`
  - `tests/support/scenarios.py`
- Test: `tests/unit/test_align_similarity.py`

**Interfaces:**

- Consumes:
  - `transform` (Task 2)
  - `imageio.RGBImage`, `imageio.resize_long_edge` (Task 4)
  - `masks.BoolMask`, `masks.tooth_mask`, `masks.gingiva_mask` (Task 5)
- Produces, in `dental_image_organizer.core.align`:
  - Constants: `WORKING_LONG_EDGE = 1600`, `CONFIDENCE_OK = 0.6`, `CONFIDENCE_REVIEW = 0.35`
  - `AlignModel = Literal["similarity", "homography"]`
  - `AlignStatus = Literal["ok", "needs_review"]`
  - `AlignResult`, frozen, with these fields:

    | Field | Type | Meaning |
    |---|---|---|
    | `transform` | `tf.Matrix` | Normalized photo → reference matrix |
    | `model` | `AlignModel` | Model that produced `transform` |
    | `confidence` | `float` | Combined confidence score |
    | `status` | `AlignStatus` | `ok` or `needs_review` |
    | `estimated` | `bool` | `False` when the center-fit fallback was used |
    | `inliers` | `int` | RANSAC inlier count |
    | `ecc_cc` | `float` | ECC correlation of the kept model |
    | `detail_ratio` | `float` | Full-resolution photo pixels per reference pixel |
    | `mirrored` | `bool` | Whether the mirrored retry was used |
    | `tight_crop` | `bool` | Whether the second pass was used |
    | `message` | `str \| None` | Explanation for `needs_review` |

  - `PreparedReference`, frozen: `full_size`, `working`, `working_scale`, `working_teeth`,
    `points`, `descriptors`, `ecc_template`, `ecc_scale`, `ecc_mask`
  - `prepare_reference(reference_rgb) -> PreparedReference`. Call it once per case; it is
    reused for every photo.
  - `align_to_reference(photo_rgb, reference: PreparedReference) -> AlignResult`
  - Test support, in `support.scenarios`:
    - `REFERENCE_BOX = (600, 400, 1200, 800)`
    - `Pair(photo, reference, truth)`
    - `about_center(width, height, scale, angle, *, mirror=False) -> tf.Matrix`
    - `make_pair(scene, scene_to_photo, *, box=REFERENCE_BOX, reference_scale=1.0) -> Pair`
    - `corner_error(pair, recovered_px) -> float`

Algorithm (spec §4.5.1 steps 1–5, 7, 8):

1. **Preprocess.** CLAHE on Lab L.
2. **Detect.** SIFT, at most 4000 features, restricted to the dilated tooth mask. If fewer than
   100 keypoints are found, retry on the full image.
3. **Match.** Mutual nearest neighbors with Lowe ratio 0.75.
4. **Estimate.** `estimateAffinePartial2D` with RANSAC (3 px, 5000 iterations, 0.995).
   - Fewer than 25 inliers: also try the mirrored photo and keep whichever has more inliers.
   - Working-copy scale above 2: rerun on a full-resolution crop of the predicted region with a
     15% margin. Keep the second pass if it has more inliers.
5. **Refine.** ECC `MOTION_EUCLIDEAN` at 800 px inside the reference's tooth ∪ gingiva mask.
6. **Confidence.** `0.5 · min(1, inliers / 80) + 0.5 · max(0, ecc_cc)`.
   - Below 0.6: `needs_review`.
   - Below 0.35, an implausible scale (outside [0.2, 5]) or rotation (beyond ±45°), or no match
     at all: center-fit fallback with `estimated=False`.

- [ ] **Step 1: Write the scenario helper**

`tests/support/scenarios.py`:

```python
"""Photo/reference pairs with known ground truth, built from synthetic scenes."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from dental_image_organizer.core import transform as tf
from support.synthetic import grade, warp

REFERENCE_BOX = (600, 400, 1200, 800)  # x, y, w, h of the scene region the reference shows


@dataclass(frozen=True)
class Pair:
    photo: np.ndarray
    reference: np.ndarray
    truth: tf.Matrix  # photo px -> reference px


def about_center(
    width: int, height: int, scale: float, angle: float, *, mirror: bool = False
) -> tf.Matrix:
    return (
        tf.translation(width / 2, height / 2)
        @ tf.similarity(scale, angle, 0.0, 0.0, mirror=mirror)
        @ tf.translation(-width / 2, -height / 2)
    )


def make_pair(
    scene: np.ndarray,
    scene_to_photo: tf.Matrix,
    *,
    box: tuple[int, int, int, int] = REFERENCE_BOX,
    reference_scale: float = 1.0,
) -> Pair:
    x, y, w, h = box
    crop = scene[y : y + h, x : x + w]
    if reference_scale != 1.0:
        size = (round(w * reference_scale), round(h * reference_scale))
        crop = cv2.resize(crop, size, interpolation=cv2.INTER_AREA)
    scene_to_reference = tf.scaling(reference_scale) @ tf.translation(-x, -y)
    photo = warp(scene, scene_to_photo, (scene.shape[1], scene.shape[0]))
    return Pair(
        photo=photo,
        reference=grade(crop),
        truth=scene_to_reference @ tf.invert(scene_to_photo),
    )


def corner_error(pair: Pair, recovered_px: tf.Matrix) -> float:
    """Largest corner displacement in the reference frame, as a fraction of its width."""
    h, w = pair.reference.shape[:2]
    corners = np.array([[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]])
    photo_points = tf.map_points(tf.invert(pair.truth), corners)
    return float(
        np.linalg.norm(tf.map_points(recovered_px, photo_points) - corners, axis=1).max() / w
    )
```

- [ ] **Step 2: Write the failing test**

`tests/unit/test_align_similarity.py`:

```python
import numpy as np
import pytest

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.align import align_to_reference, prepare_reference
from support.scenarios import about_center, corner_error, make_pair
from support.synthetic import grade, make_scene

W, H = 2400, 1600


@pytest.fixture(scope="module")
def scene() -> np.ndarray:
    return make_scene(11, W, H)


@pytest.mark.parametrize(
    ("scale", "angle", "mirror"),
    [
        (1.0, 0.0, False),
        (1.0, 7.0, False),
        (0.7, -12.0, False),
        (1.4, 12.0, False),
        (1.0, 5.0, True),
        (0.85, -6.0, True),
    ],
)
def test_recovers_known_similarity(
    scene: np.ndarray, scale: float, angle: float, mirror: bool
) -> None:
    pair = make_pair(scene, about_center(W, H, scale, angle, mirror=mirror))

    result = align_to_reference(pair.photo, prepare_reference(pair.reference))

    got = tf.to_pixel(result.transform, W, pair.reference.shape[1])
    recovered, truth = tf.decompose_similarity(got), tf.decompose_similarity(pair.truth)
    assert result.status == "ok"
    assert result.confidence >= 0.6
    assert result.mirrored is mirror
    assert abs(recovered.angle_deg - truth.angle_deg) <= 0.5
    assert recovered.scale == pytest.approx(truth.scale, rel=0.02)
    rw, rh = pair.reference.shape[1], pair.reference.shape[0]
    center = [[rw / 2, rh / 2]]
    shift = np.linalg.norm(
        tf.map_points(tf.invert(got), center) - tf.map_points(tf.invert(pair.truth), center)
    )
    assert shift <= 0.01 * W
    assert result.detail_ratio == pytest.approx(1.0 / truth.scale, rel=0.05)


def test_reference_smaller_than_working_size(scene: np.ndarray) -> None:
    pair = make_pair(scene, about_center(W, H, 1.0, 4.0), reference_scale=0.6)

    result = align_to_reference(pair.photo, prepare_reference(pair.reference))

    assert result.status == "ok"
    assert corner_error(pair, tf.to_pixel(result.transform, W, pair.reference.shape[1])) < 0.005


def test_photo_identical_to_reference_maps_to_identity(scene: np.ndarray) -> None:
    reference = grade(scene[400:1200, 600:1800])

    result = align_to_reference(reference, prepare_reference(reference))

    assert result.status == "ok"
    np.testing.assert_allclose(result.transform, np.eye(3), atol=2e-3)


def test_unrelated_photo_needs_review_with_fallback(scene: np.ndarray) -> None:
    reference = grade(scene[400:1200, 600:1800])

    result = align_to_reference(make_scene(99, W, H), prepare_reference(reference))

    assert result.status == "needs_review"
    assert result.estimated is False
    assert result.message


def test_tight_crop_uses_second_pass_and_reports_detail_ratio() -> None:
    big = make_scene(12, 4800, 3200)
    # The reference shows 30% of the photo's width, downscaled to 0.8 by the dentist's export.
    pair = make_pair(
        big, about_center(4800, 3200, 1.0, 5.0), box=(1680, 1100, 1440, 960), reference_scale=0.8
    )

    result = align_to_reference(pair.photo, prepare_reference(pair.reference))

    got = tf.to_pixel(result.transform, 4800, pair.reference.shape[1])
    assert result.tight_crop is True
    assert result.status == "ok"
    assert (
        abs(tf.decompose_similarity(got).angle_deg - tf.decompose_similarity(pair.truth).angle_deg)
        <= 0.5
    )
    assert corner_error(pair, got) < 0.01
    assert result.detail_ratio == pytest.approx(1.25, rel=0.05)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_align_similarity.py -v`
Expected: FAIL. `ModuleNotFoundError: No module named 'dental_image_organizer.core.align'`.

- [ ] **Step 4: Implement `core/align.py` (similarity path)**

```python
"""Align a raw photo to the dentist-edited reference photo (spec §4.5.1).

Coordinate frames: "full" is the full-resolution photo or reference in pixels. A _View is
the image that features are detected on (a working copy, possibly a tight crop or mirrored)
together with the matrix from full photo pixels into it. Every estimate is kept as one
full-photo -> full-reference pixel matrix and converted to normalized form at the end.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

import cv2
import numpy as np
import numpy.typing as npt
import structlog

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.imageio import RGBImage, resize_long_edge
from dental_image_organizer.core.masks import BoolMask, gingiva_mask, tooth_mask

log = structlog.get_logger(__name__)

WORKING_LONG_EDGE = 1600
ECC_LONG_EDGE = 800
SIFT_FEATURES = 4000
MIN_MASKED_KEYPOINTS = 100
TOOTH_MASK_MIN_SHARE = 0.01
TOOTH_MASK_DILATION = 0.03
RATIO_TEST = 0.75
RANSAC_THRESHOLD_PX = 3.0
RANSAC_ITERATIONS = 5000
RANSAC_CONFIDENCE = 0.995
MIRROR_RETRY_INLIERS = 25
TIGHT_CROP_SCALE = 2.0
TIGHT_CROP_MARGIN = 0.15
ECC_ITERATIONS = 100
ECC_EPSILON = 1e-6
ECC_GAUSS_SIZE = 5
ECC_MIN_MASK_SHARE = 0.05
INLIERS_FOR_FULL_CONFIDENCE = 80
CONFIDENCE_OK = 0.6
CONFIDENCE_REVIEW = 0.35
SCALE_LIMITS = (0.2, 5.0)
MAX_ROTATION_DEG = 45.0

AlignModel = Literal["similarity", "homography"]
AlignStatus = Literal["ok", "needs_review"]
FloatPoints = npt.NDArray[np.float64]
Gray = npt.NDArray[np.uint8]


@dataclass(frozen=True)
class AlignResult:
    """Photo -> reference mapping in normalized coordinates, with the evidence behind it."""

    transform: tf.Matrix
    model: AlignModel
    confidence: float
    status: AlignStatus
    estimated: bool  # False when the center-fit fallback was used
    inliers: int
    ecc_cc: float
    detail_ratio: float
    mirrored: bool
    tight_crop: bool
    message: str | None


@dataclass(frozen=True)
class PreparedReference:
    """Everything derived from the reference once, then reused for every photo of a case."""

    full_size: tuple[int, int]
    working: RGBImage
    working_scale: float
    working_teeth: BoolMask
    points: FloatPoints
    descriptors: npt.NDArray[np.float32] | None
    ecc_template: npt.NDArray[np.float32]
    ecc_scale: float
    ecc_mask: Gray


@dataclass(frozen=True)
class _View:
    image: RGBImage
    from_full: tf.Matrix


@dataclass(frozen=True)
class _Estimate:
    matrix: tf.Matrix  # full photo px -> full reference px, similarity
    view: _View
    inliers: int
    inlier_ref_points: FloatPoints  # reference working px
    src_points: FloatPoints  # all matches, view px
    dst_points: FloatPoints  # all matches, reference working px
    mirrored: bool


def prepare_reference(reference_rgb: RGBImage) -> PreparedReference:
    h, w = reference_rgb.shape[:2]
    working, working_scale = resize_long_edge(reference_rgb, WORKING_LONG_EDGE)
    points, descriptors = _features(working)
    ecc_image, ecc_scale = resize_long_edge(reference_rgb, ECC_LONG_EDGE)
    teeth = tooth_mask(ecc_image)
    region = teeth | gingiva_mask(ecc_image, teeth)
    if region.mean() < ECC_MIN_MASK_SHARE:
        region = np.ones_like(region)
    return PreparedReference(
        full_size=(w, h),
        working=working,
        working_scale=working_scale,
        working_teeth=tooth_mask(working),
        points=points,
        descriptors=descriptors,
        ecc_template=_ecc_gray(ecc_image),
        ecc_scale=ecc_scale,
        ecc_mask=region.astype(np.uint8) * 255,
    )


def align_to_reference(photo_rgb: RGBImage, reference: PreparedReference) -> AlignResult:
    working, photo_scale = resize_long_edge(photo_rgb, WORKING_LONG_EDGE)
    full_view = _View(working, tf.scaling(photo_scale))

    estimate = _estimate(full_view, reference, mirrored=False)
    if estimate is None or estimate.inliers < MIRROR_RETRY_INLIERS:
        mirrored = _estimate(full_view, reference, mirrored=True)
        if mirrored is not None and (estimate is None or mirrored.inliers > estimate.inliers):
            estimate = mirrored
    if estimate is None:
        return _fallback(photo_rgb, reference, "No matching features between photo and reference.")

    tight_crop = False
    if _working_scale(estimate.matrix, reference, photo_scale) > TIGHT_CROP_SCALE:
        crop = _crop_view(photo_rgb, estimate.matrix, reference.full_size)
        second = _estimate(crop, reference, mirrored=estimate.mirrored) if crop else None
        if second is not None and second.inliers > estimate.inliers:
            estimate, tight_crop = second, True

    similarity = _refine(estimate.matrix, estimate.view, reference, cv2.MOTION_EUCLIDEAN)
    params = tf.decompose_similarity(similarity)
    working_scale = _working_scale(similarity, reference, photo_scale)
    if not (SCALE_LIMITS[0] <= working_scale <= SCALE_LIMITS[1]) or (
        abs(params.angle_deg) > MAX_ROTATION_DEG
    ):
        return _fallback(photo_rgb, reference, "Estimated scale or rotation is implausible.")
    similarity_cc = _ecc_score(similarity, estimate.view, reference)

    model: AlignModel = "similarity"
    matrix, cc = similarity, similarity_cc

    confidence = float(
        np.clip(
            0.5 * min(1.0, estimate.inliers / INLIERS_FOR_FULL_CONFIDENCE) + 0.5 * max(0.0, cc),
            0.0,
            1.0,
        )
    )
    log.debug(
        "align",
        inliers=estimate.inliers,
        ecc_cc=round(cc, 4),
        similarity_cc=round(similarity_cc, 4),
        scale=round(working_scale, 3),
        angle=round(params.angle_deg, 2),
        model=model,
        tight_crop=tight_crop,
        mirrored=estimate.mirrored,
        confidence=round(confidence, 3),
    )
    if confidence < CONFIDENCE_REVIEW:
        return _fallback(photo_rgb, reference, "Alignment confidence is too low.")
    return AlignResult(
        transform=tf.to_normalized(matrix, photo_rgb.shape[1], reference.full_size[0]),
        model=model,
        confidence=confidence,
        status="ok" if confidence >= CONFIDENCE_OK else "needs_review",
        estimated=True,
        inliers=estimate.inliers,
        ecc_cc=cc,
        detail_ratio=1.0 / params.scale,
        mirrored=estimate.mirrored,
        tight_crop=tight_crop,
        message=None if confidence >= CONFIDENCE_OK else "Low alignment confidence.",
    )


def _estimate(view: _View, reference: PreparedReference, *, mirrored: bool) -> _Estimate | None:
    if mirrored:
        width = view.image.shape[1]
        flip = np.array([[-1.0, 0.0, width - 1.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        view = _View(np.ascontiguousarray(view.image[:, ::-1]), flip @ view.from_full)
    points, descriptors = _features(view.image)
    query, train = _match(descriptors, reference.descriptors)
    if len(query) < 4:
        return None
    src, dst = points[query], reference.points[train]
    affine, inlier_flags = cv2.estimateAffinePartial2D(
        src.astype(np.float32),
        dst.astype(np.float32),
        method=cv2.RANSAC,
        ransacReprojThreshold=RANSAC_THRESHOLD_PX,
        maxIters=RANSAC_ITERATIONS,
        confidence=RANSAC_CONFIDENCE,
    )
    if affine is None:
        return None
    inliers = inlier_flags.ravel().astype(bool)
    matrix = tf.scaling(1.0 / reference.working_scale) @ tf.from_affine(affine) @ view.from_full
    return _Estimate(
        matrix=matrix,
        view=view,
        inliers=int(inliers.sum()),
        inlier_ref_points=dst[inliers],
        src_points=src,
        dst_points=dst,
        mirrored=mirrored,
    )


def _features(rgb: RGBImage) -> tuple[FloatPoints, npt.NDArray[np.float32] | None]:
    gray = _clahe_lightness(rgb)
    sift = cv2.SIFT.create(nfeatures=SIFT_FEATURES)
    # Teeth survive surgery while soft tissue does not, so detection is focused on them.
    mask = _detection_mask(rgb)
    keypoints, descriptors = sift.detectAndCompute(gray, mask)
    if mask is not None and len(keypoints) < MIN_MASKED_KEYPOINTS:
        keypoints, descriptors = sift.detectAndCompute(gray, None)
    points = np.array([kp.pt for kp in keypoints], dtype=np.float64).reshape(-1, 2)
    return points, None if descriptors is None else np.asarray(descriptors, dtype=np.float32)


def _detection_mask(rgb: RGBImage) -> Gray | None:
    teeth = tooth_mask(rgb)
    if teeth.mean() < TOOTH_MASK_MIN_SHARE:
        return None
    k = max(3, round(min(rgb.shape[:2]) * TOOTH_MASK_DILATION)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    return cast(Gray, cv2.dilate(teeth.astype(np.uint8) * 255, kernel))


def _match(
    query: npt.NDArray[np.float32] | None, train: npt.NDArray[np.float32] | None
) -> tuple[npt.NDArray[np.intp], npt.NDArray[np.intp]]:
    empty = np.zeros(0, dtype=np.intp)
    if query is None or train is None or len(query) < 2 or len(train) < 2:
        return empty, empty
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    backward = {m.queryIdx: m.trainIdx for m in matcher.match(train, query)}
    pairs = [
        (pair[0].queryIdx, pair[0].trainIdx)
        for pair in matcher.knnMatch(query, train, k=2)
        if len(pair) == 2
        and pair[0].distance < RATIO_TEST * pair[1].distance
        and backward.get(pair[0].trainIdx) == pair[0].queryIdx
    ]
    if not pairs:
        return empty, empty
    q, t = zip(*pairs, strict=True)
    return np.array(q, dtype=np.intp), np.array(t, dtype=np.intp)


def _refine(matrix: tf.Matrix, view: _View, reference: PreparedReference, motion: int) -> tf.Matrix:
    """ECC refinement; on failure the input estimate is kept unchanged."""
    warped, mask = _warp_for_ecc(matrix, view, reference)
    if mask is None:
        return matrix
    size = 3 if motion == cv2.MOTION_HOMOGRAPHY else 2
    initial = np.eye(size, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, ECC_ITERATIONS, ECC_EPSILON)
    try:
        _, found = cv2.findTransformECC(
            reference.ecc_template, warped, initial, motion, criteria, mask, ECC_GAUSS_SIZE
        )
    except cv2.error as exc:
        log.debug("ecc_failed", motion=motion, error=str(exc).splitlines()[-1])
        return matrix
    warp = np.asarray(found, dtype=np.float64)
    w3 = warp if size == 3 else tf.from_affine(warp)
    # ECC maps template coordinates into the pre-warped photo: warped(W x) ~ template(x).
    ecc = tf.scaling(reference.ecc_scale)
    return tf.normalize(tf.invert(ecc) @ tf.invert(w3) @ ecc @ matrix)


def _ecc_score(matrix: tf.Matrix, view: _View, reference: PreparedReference) -> float:
    warped, mask = _warp_for_ecc(matrix, view, reference)
    if mask is None:
        return 0.0
    try:
        return float(
            cv2.computeECC(
                cv2.GaussianBlur(reference.ecc_template, (ECC_GAUSS_SIZE, ECC_GAUSS_SIZE), 0),
                cv2.GaussianBlur(warped, (ECC_GAUSS_SIZE, ECC_GAUSS_SIZE), 0),
                mask,
            )
        )
    except cv2.error:
        return 0.0


def _warp_for_ecc(
    matrix: tf.Matrix, view: _View, reference: PreparedReference
) -> tuple[npt.NDArray[np.float32], Gray | None]:
    h, w = reference.ecc_template.shape
    to_ecc = tf.scaling(reference.ecc_scale) @ matrix @ tf.invert(view.from_full)
    warped = cv2.warpPerspective(_ecc_gray(view.image), to_ecc, (w, h), flags=cv2.INTER_LINEAR)
    covered = cv2.warpPerspective(
        np.full(view.image.shape[:2], 255, np.uint8), to_ecc, (w, h), flags=cv2.INTER_NEAREST
    )
    mask = cv2.bitwise_and(reference.ecc_mask, covered)
    if cv2.countNonZero(mask) < ECC_MIN_MASK_SHARE * w * h:
        return cast(npt.NDArray[np.float32], warped), None
    return cast(npt.NDArray[np.float32], warped), cast(Gray, mask)


def _crop_view(
    photo_rgb: RGBImage, matrix: tf.Matrix, reference_size: tuple[int, int]
) -> _View | None:
    """Full-resolution crop around the region the reference shows (tight-crop second pass)."""
    w, h = reference_size
    corners = tf.map_points(tf.invert(matrix), [[0, 0], [w, 0], [w, h], [0, h]])
    (x0, y0), (x1, y1) = corners.min(axis=0), corners.max(axis=0)
    mx, my = (x1 - x0) * TIGHT_CROP_MARGIN, (y1 - y0) * TIGHT_CROP_MARGIN
    height, width = photo_rgb.shape[:2]
    left, top = max(0, int(x0 - mx)), max(0, int(y0 - my))
    right, bottom = min(width, int(np.ceil(x1 + mx))), min(height, int(np.ceil(y1 + my)))
    if right - left < 32 or bottom - top < 32:
        return None
    crop, scale = resize_long_edge(
        photo_rgb[top:bottom, left:right], WORKING_LONG_EDGE, upscale=True
    )
    return _View(crop, tf.scaling(scale) @ tf.translation(-left, -top))


def _working_scale(matrix: tf.Matrix, reference: PreparedReference, photo_scale: float) -> float:
    """Scale from the full photo's working copy to the reference's working copy."""
    return tf.decompose_similarity(matrix).scale * reference.working_scale / photo_scale


def _fallback(photo_rgb: RGBImage, reference: PreparedReference, message: str) -> AlignResult:
    """Center the photo so it covers the reference frame; the user aligns it manually."""
    ph, pw = photo_rgb.shape[:2]
    rw, rh = reference.full_size
    k = max(rw / pw, rh / ph)
    matrix = tf.similarity(k, 0.0, (rw - k * pw) / 2, (rh - k * ph) / 2)
    log.debug("align_fallback", reason=message)
    return AlignResult(
        transform=tf.to_normalized(matrix, pw, rw),
        model="similarity",
        confidence=0.0,
        status="needs_review",
        estimated=False,
        inliers=0,
        ecc_cc=0.0,
        detail_ratio=1.0 / k,
        mirrored=False,
        tight_crop=False,
        message=message,
    )


def _clahe_lightness(rgb: RGBImage) -> Gray:
    lightness = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)[..., 0]
    return cast(Gray, cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lightness))


def _ecc_gray(rgb: RGBImage) -> npt.NDArray[np.float32]:
    lightness = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)[..., 0]
    return lightness.astype(np.float32) / 255.0
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_align_similarity.py -v`
Expected: 10 passed.

Reference figures from the verified run:

- angle error ≤ 0.002°
- scale error ≤ 0.01%
- corner error ≤ 0.06%
- each test takes about 0.5 s, and the tight-crop test about 2 s

- [ ] **Step 6: Quality gates and commit**

Run: `uv run ruff check; uv run ruff format --check; uv run mypy; uv run lint-imports`
Expected: all clean.

```bash
git add src/dental_image_organizer/core/align.py tests/support/scenarios.py tests/unit/test_align_similarity.py
git commit -m "feat(core): align photos to the reference with SIFT, RANSAC, and ECC"
```

---

### Task 7: Gated homography for perspective

**Files:**

- Modify: `src/dental_image_organizer/core/align.py`
- Test: `tests/unit/test_align_homography.py`

**Interfaces:**

- Consumes: everything from Task 6.
- Produces:
  - New field `AlignResult.homography_note: str | None`. It is `None` when a homography is
    used; otherwise it holds why the similarity was kept:
    - `too_few_inliers`
    - `poor_coverage`
    - `estimation_failed`
    - a comma-joined list of the failed gates `ecc_gain`, `corner_shift`, `not_convex`
  - `homography_gate_failures(homography, similarity, frame_size, *, homography_cc, similarity_cc) -> list[str]`

Spec §4.5.1 step 6:

1. **Preconditions.**
   - At least 60 inliers.
   - The inlier points must cover at least 3 of the 4 quadrants of the reference frame.
2. **Estimate.** `findHomography` with RANSAC at 3 px, then ECC `MOTION_HOMOGRAPHY`.
3. **Acceptance gates.** All of these must hold:
   - the ECC gain over the similarity is at least 0.02;
   - each output-frame corner moves at most 8% of the width compared with the similarity;
   - the mapped frame is convex and lies in front of the camera.

On the verified run, an 8° camera tilt was accepted with a 0.06% corner error. A 4° tilt gained
only 0.011 ECC and was correctly left to the similarity.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_align_homography.py`:

```python
import numpy as np
import pytest

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.align import (
    align_to_reference,
    homography_gate_failures,
    prepare_reference,
)
from support.scenarios import REFERENCE_BOX, about_center, corner_error, make_pair
from support.synthetic import make_scene, tilt_homography

W, H = 2400, 1600


@pytest.fixture(scope="module")
def scene() -> np.ndarray:
    return make_scene(11, W, H)


def test_camera_tilt_is_corrected_with_a_homography(scene: np.ndarray) -> None:
    pair = make_pair(scene, about_center(W, H, 1.0, 3.0) @ tilt_homography(W, H, 8.0))

    result = align_to_reference(pair.photo, prepare_reference(pair.reference))

    assert result.model == "homography"
    assert result.homography_note is None
    assert corner_error(pair, tf.to_pixel(result.transform, W, pair.reference.shape[1])) < 0.005


def test_pure_similarity_keeps_the_similarity(scene: np.ndarray) -> None:
    pair = make_pair(scene, about_center(W, H, 1.1, -4.0))

    result = align_to_reference(pair.photo, prepare_reference(pair.reference))

    assert result.model == "similarity"
    assert result.homography_note is not None
    assert "ecc_gain" in result.homography_note
    np.testing.assert_allclose(result.transform[2], [0.0, 0.0, 1.0], atol=1e-12)


def test_features_in_one_quadrant_skip_the_homography() -> None:
    flat = make_scene(21, W, H, teeth=False, texture=False, specks=0)
    textured = make_scene(22, W, H, teeth=False)
    x, y, w, h = REFERENCE_BOX
    # Only the top-left quarter of the reference region carries any texture.
    flat[y : y + h // 2, x : x + w // 2] = textured[y : y + h // 2, x : x + w // 2]
    pair = make_pair(flat, about_center(W, H, 1.0, 2.0) @ tilt_homography(W, H, 8.0))

    result = align_to_reference(pair.photo, prepare_reference(pair.reference))

    assert result.model == "similarity"
    assert result.homography_note == "poor_coverage"


FRAME = (1200, 800)


def test_gate_rejects_small_ecc_gain() -> None:
    s = tf.identity()

    failures = homography_gate_failures(s, s, FRAME, homography_cc=0.95, similarity_cc=0.94)

    assert failures == ["ecc_gain"]


def test_gate_rejects_large_corner_shift() -> None:
    s = tf.identity()
    h = tf.identity()
    h[2, 0] = 2e-4  # strong keystone: far corners move by well over 8% of the width

    failures = homography_gate_failures(h, s, FRAME, homography_cc=0.99, similarity_cc=0.9)

    assert "corner_shift" in failures


def test_gate_rejects_frame_behind_the_camera() -> None:
    s = tf.identity()
    h = tf.identity()
    # The inverse has h20 = -2e-3, so frame points with x > 500 come from behind the camera.
    h[2, 0] = 2e-3

    failures = homography_gate_failures(h, s, FRAME, homography_cc=0.99, similarity_cc=0.9)

    assert "not_convex" in failures


def test_gate_accepts_mild_perspective_with_clear_gain() -> None:
    s = tf.identity()
    h = tf.identity()
    h[2, 1] = 2e-5

    assert homography_gate_failures(h, s, FRAME, homography_cc=0.99, similarity_cc=0.95) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_align_homography.py -v`
Expected: FAIL. `ImportError: cannot import name 'homography_gate_failures'`.

- [ ] **Step 3: Add the constants**

In `core/align.py`, directly after the line `ECC_MIN_MASK_SHARE = 0.05`, insert:

```python
HOMOGRAPHY_MIN_INLIERS = 60
HOMOGRAPHY_MIN_QUADRANTS = 3
HOMOGRAPHY_MIN_ECC_GAIN = 0.02
HOMOGRAPHY_MAX_CORNER_SHIFT = 0.08
```

- [ ] **Step 4: Add the result field**

In `class AlignResult`, directly after `tight_crop: bool`, insert:

```python
    homography_note: str | None  # why the similarity was kept; None when a homography is used
```

- [ ] **Step 5: Try the homography in `align_to_reference`**

Replace:

```python
    model: AlignModel = "similarity"
    matrix, cc = similarity, similarity_cc
```

with:

```python
    model: AlignModel = "similarity"
    matrix, cc = similarity, similarity_cc
    homography, homography_cc, note = _try_homography(
        estimate, similarity, similarity_cc, reference
    )
    if homography is not None:
        matrix, model, cc = homography, "homography", homography_cc
```

- [ ] **Step 6: Report the note everywhere a result is produced**

- In the `log.debug("align", ...)` call, insert `homography_note=note,` directly after
  `model=model,`.
- In the final `return AlignResult(...)` of `align_to_reference`, insert
  `homography_note=note,` directly after `tight_crop=tight_crop,`.
- In `_fallback`'s `AlignResult(...)`, insert `homography_note=None,` directly after
  `tight_crop=False,`.

- [ ] **Step 7: Add the gate and the homography attempt**

Insert these two functions between `align_to_reference` and `_estimate`:

```python
def homography_gate_failures(
    homography: tf.Matrix,
    similarity: tf.Matrix,
    frame_size: tuple[int, int],
    *,
    homography_cc: float,
    similarity_cc: float,
) -> list[str]:
    """Names of the acceptance gates (spec §4.5.1 step 6.3) that `homography` fails."""
    w, h = frame_size
    corners = np.array([[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]])
    failures: list[str] = []
    if homography_cc - similarity_cc < HOMOGRAPHY_MIN_ECC_GAIN:
        failures.append("ecc_gain")
    inverse = tf.invert(homography)
    denominators = np.hstack([corners, np.ones((4, 1))]) @ inverse[2]
    photo_corners = tf.map_points(inverse, corners)
    shift = np.linalg.norm(tf.map_points(similarity, photo_corners) - corners, axis=1)
    if np.max(shift) > HOMOGRAPHY_MAX_CORNER_SHIFT * w:
        failures.append("corner_shift")
    if np.any(denominators <= 0) or not cv2.isContourConvex(
        photo_corners.astype(np.float32).reshape(-1, 1, 2)
    ):
        failures.append("not_convex")
    return failures


def _try_homography(
    estimate: _Estimate,
    similarity: tf.Matrix,
    similarity_cc: float,
    reference: PreparedReference,
) -> tuple[tf.Matrix | None, float, str | None]:
    if estimate.inliers < HOMOGRAPHY_MIN_INLIERS:
        return None, similarity_cc, "too_few_inliers"
    if _quadrants(estimate.inlier_ref_points, reference.working) < HOMOGRAPHY_MIN_QUADRANTS:
        return None, similarity_cc, "poor_coverage"
    found, _ = cv2.findHomography(
        estimate.src_points.astype(np.float32),
        estimate.dst_points.astype(np.float32),
        cv2.RANSAC,
        RANSAC_THRESHOLD_PX,
    )
    if found is None:
        return None, similarity_cc, "estimation_failed"
    initial = tf.scaling(1.0 / reference.working_scale) @ found @ estimate.view.from_full
    homography = _refine(tf.normalize(initial), estimate.view, reference, cv2.MOTION_HOMOGRAPHY)
    homography_cc = _ecc_score(homography, estimate.view, reference)
    failures = homography_gate_failures(
        homography,
        similarity,
        reference.full_size,
        homography_cc=homography_cc,
        similarity_cc=similarity_cc,
    )
    if failures:
        return None, similarity_cc, ",".join(failures)
    return homography, homography_cc, None
```

Insert this function between `_working_scale` and `_fallback`:

```python
def _quadrants(points: FloatPoints, image: RGBImage) -> int:
    h, w = image.shape[:2]
    right = points[:, 0] >= w / 2
    bottom = points[:, 1] >= h / 2
    return len(set(zip(right.tolist(), bottom.tolist(), strict=True)))
```

- [ ] **Step 8: Run all alignment tests**

Run: `uv run pytest tests/unit/test_align_homography.py tests/unit/test_align_similarity.py -v`
Expected: 17 passed. The similarity tests must still pass; all of them keep the similarity,
with `ecc_gain` in the note.

- [ ] **Step 9: Quality gates and commit**

Run: `uv run ruff check; uv run ruff format --check; uv run mypy; uv run lint-imports`
Expected: all clean.

```bash
git add src/dental_image_organizer/core/align.py tests/unit/test_align_homography.py
git commit -m "feat(core): correct camera tilt with a gated homography"
```

---

### Task 8: Color matching with monotone Lab curves

**Files:**

- Modify: `src/dental_image_organizer/core/color.py` (replace the whole file)
- Test: `tests/unit/test_color.py`

**Interfaces:**

- Consumes:
  - `imageio.RGBImage` (Task 4)
  - `masks.tooth_mask` (Task 5), in the tests
- Produces, keeping `to_lab`, `from_lab`, and `LabImage` from Task 5:
  - Constants: `METHOD = "lab_quantile_v1"`, `CHANNELS = ("L", "a", "b")`,
    `CHANNEL_LIMITS = (25.0, 15.0, 15.0)`
  - `Curve(src: tuple[float, ...], dst: tuple[float, ...])` with
    `.apply(values) -> NDArray[float32]`
  - `LabCurves(curves: tuple[Curve, Curve, Curve])` with:
    - `.apply_lab(lab)`
    - `.to_params() -> dict[str, dict[str, list[float]]]`
    - `LabCurves.from_params(params)`
  - `fit_lab_curves(photo_rgb, reference_rgb, valid, tooth=None) -> LabCurves`
  - `apply_lab_curves(rgb, curves, *, chunk_rows=512) -> RGBImage`

Spec §4.5.2:

- **Quantiles.** 17 weighted quantiles per channel. Weights are 1.0 on reference teeth and 0.25
  elsewhere; when teeth cover less than 3% of the overlap, every weight is 1.0.
- **Robustness loop.** Up to 3 iterations. Exclude pixels whose residual exceeds
  `median + 3 × 1.4826 × MAD`, but never more than 50% of the pixels. Stop early when fewer
  than 1% of the pixels change status.
- **Limits.** Each mapped quantile moves at most ±25 L or ±15 a\*/b\* from identity, and every
  curve is monotone.
- **Sampling.** The fit uses an evenly spaced subsample of at most 250,000 pixels. This took the
  fit from about 3 s to 0.3 s with unchanged accuracy.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_color.py`:

```python
import numpy as np
import pytest

from dental_image_organizer.core.color import (
    CHANNEL_LIMITS,
    LabCurves,
    apply_lab_curves,
    fit_lab_curves,
    from_lab,
    to_lab,
)
from dental_image_organizer.core.masks import tooth_mask
from support.synthetic import make_scene


def _delta_e(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.linalg.norm(to_lab(a) - to_lab(b), axis=-1)


def _shifted(reference: np.ndarray) -> np.ndarray:
    """A different exposure, tone curve, and white balance, like another session's photo."""
    lab = to_lab(reference)
    lab[..., 0] = 100.0 * (lab[..., 0] / 100.0) ** 1.15 - 3.0
    lab[..., 1] += 5.0
    lab[..., 2] -= 6.0
    return from_lab(lab)


@pytest.fixture(scope="module")
def reference() -> np.ndarray:
    return make_scene(31, 1200, 800)


def test_identical_images_need_no_correction(reference: np.ndarray) -> None:
    valid = np.ones(reference.shape[:2], bool)

    curves = fit_lab_curves(reference, reference, valid, tooth_mask(reference))

    assert _delta_e(apply_lab_curves(reference, curves), reference).mean() < 0.5


def test_known_tone_and_white_balance_shift_is_undone(reference: np.ndarray) -> None:
    photo = _shifted(reference)
    valid = np.ones(reference.shape[:2], bool)
    assert _delta_e(photo, reference).mean() > 6  # the shift is clearly visible before

    curves = fit_lab_curves(photo, reference, valid, tooth_mask(reference))

    assert _delta_e(apply_lab_curves(photo, curves), reference).mean() < 3


@pytest.mark.parametrize("share", [0.2, 0.4])
def test_surgical_change_does_not_drag_the_fit(reference: np.ndarray, share: float) -> None:
    photo = _shifted(reference)
    h, w = reference.shape[:2]
    patch = np.zeros((h, w), bool)
    patch[:, : int(w * share)] = True
    rng = np.random.default_rng(5)
    blood = np.array([120, 20, 30]) + rng.integers(-15, 16, (h, w, 3))
    photo[patch] = np.clip(blood[patch], 0, 255).astype(np.uint8)
    valid = np.ones((h, w), bool)

    curves = fit_lab_curves(photo, reference, valid, tooth_mask(reference))

    corrected = apply_lab_curves(photo, curves)
    assert _delta_e(corrected, reference)[~patch].mean() < 3


def test_only_valid_pixels_are_used(reference: np.ndarray) -> None:
    photo = _shifted(reference)
    photo[:, :400] = 0  # outside the photo's coverage after warping
    valid = np.ones(reference.shape[:2], bool)
    valid[:, :400] = False

    curves = fit_lab_curves(photo, reference, valid, tooth_mask(reference))

    corrected = apply_lab_curves(photo, curves)
    assert _delta_e(corrected, reference)[valid].mean() < 3


def test_curve_moves_are_limited_and_monotone(reference: np.ndarray) -> None:
    lab = to_lab(reference)
    lab[..., 0] = np.clip(lab[..., 0] - 50.0, 0, 100)  # far darker than any real exposure error
    photo = from_lab(lab)
    valid = np.ones(reference.shape[:2], bool)

    curves = fit_lab_curves(photo, reference, valid, tooth_mask(reference))

    for curve, limit in zip(curves.curves, CHANNEL_LIMITS, strict=True):
        src, dst = np.array(curve.src), np.array(curve.dst)
        assert np.all(np.abs(dst - src) <= limit + 1e-6)
        assert np.all(np.diff(dst) >= 0)
        assert np.all(np.diff(src) > 0)


def test_params_round_trip(reference: np.ndarray) -> None:
    valid = np.ones(reference.shape[:2], bool)
    curves = fit_lab_curves(_shifted(reference), reference, valid, None)

    assert LabCurves.from_params(curves.to_params()) == curves


def test_chunked_apply_matches_single_pass(reference: np.ndarray) -> None:
    valid = np.ones(reference.shape[:2], bool)
    curves = fit_lab_curves(_shifted(reference), reference, valid, None)

    chunked = apply_lab_curves(reference, curves, chunk_rows=37)
    single = apply_lab_curves(reference, curves, chunk_rows=10_000)

    np.testing.assert_array_equal(chunked, single)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_color.py -v`
Expected: FAIL. `ImportError: cannot import name 'CHANNEL_LIMITS'`.

- [ ] **Step 3: Replace `core/color.py` with the full implementation**

```python
"""Color matching to the reference (spec §4.5.2): monotone per-channel curves in CIE Lab."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import cv2
import numpy as np
import numpy.typing as npt

from dental_image_organizer.core.imageio import RGBImage

METHOD = "lab_quantile_v1"
CHANNELS = ("L", "a", "b")
QUANTILES = np.linspace(0.0, 1.0, 17)
CHANNEL_LIMITS = (25.0, 15.0, 15.0)
TOOTH_WEIGHT = 1.0
OTHER_WEIGHT = 0.25
MIN_TOOTH_SHARE = 0.03
MAX_ITERATIONS = 3
MAX_EXCLUDED_SHARE = 0.5
MAD_K = 3.0
MAD_TO_SIGMA = 1.4826
STABLE_SHARE = 0.01
MAX_FIT_PIXELS = 250_000
APPLY_CHUNK_ROWS = 512

LabImage = npt.NDArray[np.float32]
FloatArray = npt.NDArray[np.float64]
BoolMask = npt.NDArray[np.bool_]


def to_lab(rgb: RGBImage) -> LabImage:
    """sRGB uint8 to Lab float32 with L in [0, 100] and a*, b* in their natural units."""
    return cast(LabImage, cv2.cvtColor(rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB))


def from_lab(lab: LabImage) -> RGBImage:
    rgb = cv2.cvtColor(lab.astype(np.float32), cv2.COLOR_LAB2RGB)
    return np.clip(np.rint(rgb * 255.0), 0, 255).astype(np.uint8)


@dataclass(frozen=True)
class Curve:
    src: tuple[float, ...]
    dst: tuple[float, ...]

    def apply(self, values: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        src, dst = np.asarray(self.src), np.asarray(self.dst)
        out = np.interp(values, src, dst)
        # Beyond the fitted range keep the end offsets instead of clamping, so highlights and
        # shadows outside the overlap region do not flatten into one value.
        low, high = values < src[0], values > src[-1]
        out[low] = values[low] + (dst[0] - src[0])
        out[high] = values[high] + (dst[-1] - src[-1])
        return np.asarray(out, dtype=np.float32)


@dataclass(frozen=True)
class LabCurves:
    curves: tuple[Curve, Curve, Curve]

    def apply_lab(self, lab: npt.NDArray[np.floating[Any]]) -> npt.NDArray[np.float32]:
        out = np.empty(lab.shape, dtype=np.float32)
        for c, curve in enumerate(self.curves):
            out[..., c] = curve.apply(lab[..., c].astype(np.float32))
        return out

    def to_params(self) -> dict[str, dict[str, list[float]]]:
        return {
            name: {"src": list(curve.src), "dst": list(curve.dst)}
            for name, curve in zip(CHANNELS, self.curves, strict=True)
        }

    @classmethod
    def from_params(cls, params: dict[str, dict[str, list[float]]]) -> LabCurves:
        curves = tuple(
            Curve(tuple(params[name]["src"]), tuple(params[name]["dst"])) for name in CHANNELS
        )
        return cls(cast(tuple[Curve, Curve, Curve], curves))


def fit_lab_curves(
    photo_rgb: RGBImage,
    reference_rgb: RGBImage,
    valid: BoolMask,
    tooth: BoolMask | None = None,
) -> LabCurves:
    """Fit curves mapping the aligned photo's colors onto the reference's.

    `photo_rgb` must already be warped into the reference frame; `valid` marks pixels the
    photo actually covers. `tooth` is the reference's tooth mask and up-weights stable teeth.
    """
    # An evenly spaced subsample keeps the quantiles stable while bounding the sort cost.
    rows = np.flatnonzero(valid)
    rows = rows[np.linspace(0, len(rows) - 1, min(len(rows), MAX_FIT_PIXELS)).astype(np.intp)]
    photo = to_lab(photo_rgb).reshape(-1, 3)[rows].astype(np.float64)
    reference = to_lab(reference_rgb).reshape(-1, 3)[rows].astype(np.float64)
    teeth = tooth.ravel()[rows] if tooth is not None else np.zeros(len(rows), dtype=bool)
    weights = (
        np.where(teeth, TOOTH_WEIGHT, OTHER_WEIGHT)
        if teeth.mean() >= MIN_TOOTH_SHARE
        else np.ones(len(photo))
    )
    included = np.ones(len(photo), dtype=bool)
    curves = _fit(photo, reference, weights, included)
    for _ in range(MAX_ITERATIONS):
        residual = np.linalg.norm(curves.apply_lab(photo) - reference, axis=1)
        updated = _keep_low_residuals(residual)
        changed = float(np.mean(updated != included))
        included = updated
        curves = _fit(photo, reference, weights, included)
        if changed < STABLE_SHARE:
            break
    return curves


def apply_lab_curves(
    rgb: RGBImage, curves: LabCurves, *, chunk_rows: int = APPLY_CHUNK_ROWS
) -> RGBImage:
    # Row chunks bound memory: a full 48 MP image in float32 Lab would need about 580 MB.
    out = np.empty_like(rgb)
    for y in range(0, rgb.shape[0], chunk_rows):
        lab = to_lab(rgb[y : y + chunk_rows])
        out[y : y + chunk_rows] = from_lab(curves.apply_lab(lab))
    return out


def _fit(
    photo: FloatArray, reference: FloatArray, weights: FloatArray, included: BoolMask
) -> LabCurves:
    w = weights[included]
    curves = tuple(
        _curve(
            _weighted_quantiles(photo[included, c], w),
            _weighted_quantiles(reference[included, c], w),
            CHANNEL_LIMITS[c],
        )
        for c in range(3)
    )
    return LabCurves(cast(tuple[Curve, Curve, Curve], curves))


def _weighted_quantiles(values: FloatArray, weights: FloatArray) -> FloatArray:
    order = np.argsort(values, kind="stable")
    v, w = values[order], weights[order]
    cumulative = np.cumsum(w)
    positions = (cumulative - 0.5 * w) / cumulative[-1]
    return np.interp(QUANTILES, positions, v)


def _curve(src_q: FloatArray, dst_q: FloatArray, limit: float) -> Curve:
    # np.interp needs strictly increasing x, and quantiles repeat on flat regions.
    src = np.maximum.accumulate(src_q) + np.arange(len(src_q)) * 1e-6
    dst = np.maximum.accumulate(np.clip(dst_q, src - limit, src + limit))
    return Curve(tuple(float(x) for x in src), tuple(float(x) for x in dst))


def _keep_low_residuals(residual: FloatArray) -> BoolMask:
    median = float(np.median(residual))
    mad = float(np.median(np.abs(residual - median)))
    keep = residual <= median + MAD_K * MAD_TO_SIGMA * mad
    if np.mean(~keep) > MAX_EXCLUDED_SHARE:
        keep = residual <= median
    return keep
```

- [ ] **Step 4: Run the color and Lab tests**

Run: `uv run pytest tests/unit/test_color.py tests/unit/test_lab.py tests/unit/test_masks.py -v`
Expected: 13 passed.

Reference figures from the verified run (mean ΔE76 outside the patch):

- before correction: 10.7
- after correction: 0.30
- 20% surgical patch: 0.28
- 40% surgical patch: 2.53

- [ ] **Step 5: Quality gates and commit**

Run: `uv run ruff check; uv run ruff format --check; uv run mypy; uv run lint-imports`
Expected: all clean.

```bash
git add src/dental_image_organizer/core/color.py tests/unit/test_color.py
git commit -m "feat(core): match colors to the reference with robust Lab quantile curves"
```

---

### Task 9: Rendering and the sequence layout

**Files:**

- Create:
  - `src/dental_image_organizer/core/render.py`
  - `src/dental_image_organizer/core/layout.py`
- Test: `tests/unit/test_render_layout.py`

**Interfaces:**

- Consumes:
  - `transform` (Task 2)
  - `imageio.RGBImage` (Task 4)
  - `masks.BoolMask` (Task 5)
  - `color.LabCurves`, `color.apply_lab_curves` (Task 8)
- Produces:
  - `render.render(image, transform, output_size, *, curves=None, interpolation=cv2.INTER_LANCZOS4) -> RGBImage`
  - `render.coverage_mask(input_size, transform, output_size) -> BoolMask`
  - `layout.render_sequence(images, *, gap_px=0, gap_color=(0, 0, 0)) -> RGBImage`

`render` must not alias. `warpPerspective` has no low-pass filter, so before any warp that
shrinks by more than 2×, the input is area-averaged down to about the output scale. The
anti-aliasing test catches a half-way implementation.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_render_layout.py`:

```python
import numpy as np
import pytest

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.layout import render_sequence
from dental_image_organizer.core.render import coverage_mask, render
from support.synthetic import make_scene


def test_identity_render_at_another_resolution_is_a_resize() -> None:
    scene = make_scene(41, 800, 600)

    half = render(scene, tf.identity(), (400, 300))

    assert half.shape == (300, 400, 3)
    assert (
        np.abs(half.astype(int).mean(axis=(0, 1)) - scene.astype(int).mean(axis=(0, 1))).max() < 3
    )


def test_render_moves_content_where_the_transform_says() -> None:
    image = np.zeros((100, 200, 3), np.uint8)
    image[40:60, 90:110] = 255  # square centered at (100, 50)
    shift = tf.to_normalized(tf.translation(30, 10), 200, 200)

    out = render(image, shift, (200, 100))

    ys, xs = np.nonzero(out[..., 0] > 128)
    assert xs.mean() == pytest.approx(130, abs=1)
    assert ys.mean() == pytest.approx(60, abs=1)


def test_strong_downscale_is_antialiased() -> None:
    stripes = np.zeros((1000, 1000, 3), np.uint8)
    stripes[:, ::2] = 255  # finest possible detail

    small = render(stripes, tf.identity(), (100, 100))

    # Proper averaging gives uniform gray; naive point sampling gives black or white.
    assert np.abs(small.astype(int) - 128).max() < 20


def test_coverage_mask_marks_only_covered_pixels() -> None:
    shift = tf.to_normalized(tf.translation(50, 0), 100, 100)

    mask = coverage_mask((100, 100), shift, (100, 100))

    assert not mask[:, :50].any()
    assert mask[:, 51:].all()


def test_sequence_places_images_left_to_right_at_equal_height() -> None:
    red = np.full((100, 150, 3), (255, 0, 0), np.uint8)
    green = np.full((100, 120, 3), (0, 255, 0), np.uint8)

    strip = render_sequence([red, green], gap_px=4, gap_color=(255, 255, 255))

    assert strip.shape == (100, 274, 3)
    assert tuple(strip[50, 10]) == (255, 0, 0)
    assert tuple(strip[50, 152]) == (255, 255, 255)
    assert tuple(strip[50, 200]) == (0, 255, 0)


def test_sequence_scales_taller_images_down_to_the_shortest() -> None:
    small = np.zeros((100, 150, 3), np.uint8)
    tall = np.zeros((200, 300, 3), np.uint8)

    strip = render_sequence([small, tall])

    assert strip.shape == (100, 300, 3)


def test_empty_sequence_is_rejected() -> None:
    with pytest.raises(ValueError):
        render_sequence([])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_render_layout.py -v`
Expected: FAIL. `ModuleNotFoundError: No module named 'dental_image_organizer.core.layout'`.

- [ ] **Step 3: Implement `core/render.py`**

```python
"""Apply an edit record (transform + color) to an original image (spec §4.8 "Rendering")."""

from __future__ import annotations

from typing import cast

import cv2
import numpy as np

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.color import LabCurves, apply_lab_curves
from dental_image_organizer.core.imageio import RGBImage
from dental_image_organizer.core.masks import BoolMask

# warpPerspective samples without a low-pass filter, so a warp that shrinks by more than this
# would alias fine texture. Such inputs are first area-averaged to about the output scale,
# leaving the warp to rotate and shift at roughly 1:1.
MAX_WARP_DOWNSCALE = 0.5


def render(
    image: RGBImage,
    transform: tf.Matrix,
    output_size: tuple[int, int],
    *,
    curves: LabCurves | None = None,
    interpolation: int = cv2.INTER_LANCZOS4,
) -> RGBImage:
    """Warp `image` (any resolution) with the normalized `transform`, then apply colors."""
    out_w, out_h = output_size
    h_px = tf.to_pixel(transform, image.shape[1], out_w)
    scale = float(np.sqrt(abs(np.linalg.det(h_px[:2, :2]))))
    if scale < MAX_WARP_DOWNSCALE:
        size = (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale)))
        image = cast(RGBImage, cv2.resize(image, size, interpolation=cv2.INTER_AREA))
        h_px = tf.to_pixel(transform, image.shape[1], out_w)
    warped = cast(
        RGBImage,
        cv2.warpPerspective(
            image,
            h_px,
            (out_w, out_h),
            flags=interpolation,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        ),
    )
    return apply_lab_curves(warped, curves) if curves is not None else warped


def coverage_mask(
    input_size: tuple[int, int], transform: tf.Matrix, output_size: tuple[int, int]
) -> BoolMask:
    """Output pixels that the warped input actually covers."""
    in_w, in_h = input_size
    h_px = tf.to_pixel(transform, in_w, output_size[0])
    covered = cv2.warpPerspective(
        np.full((in_h, in_w), 255, np.uint8), h_px, output_size, flags=cv2.INTER_NEAREST
    )
    return covered > 0
```

- [ ] **Step 4: Implement `core/layout.py`**

```python
"""Collage layouts: the horizontal procedure sequence (spec §3.2 step 6)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import cv2
import numpy as np

from dental_image_organizer.core.imageio import RGBImage


def render_sequence(
    images: Sequence[RGBImage],
    *,
    gap_px: int = 0,
    gap_color: tuple[int, int, int] = (0, 0, 0),
) -> RGBImage:
    """Place images left to right at equal height, in the given order."""
    if not images:
        raise ValueError("A sequence needs at least one image.")
    height = min(img.shape[0] for img in images)
    # Photos of one case share the reference frame, so resizing only happens for mixed input.
    resized = [
        img
        if img.shape[0] == height
        else cast(
            RGBImage,
            cv2.resize(
                img,
                (max(1, round(img.shape[1] * height / img.shape[0])), height),
                interpolation=cv2.INTER_AREA,
            ),
        )
        for img in images
    ]
    width = sum(img.shape[1] for img in resized) + gap_px * (len(resized) - 1)
    canvas = np.empty((height, width, 3), dtype=np.uint8)
    canvas[:] = gap_color
    x = 0
    for img in resized:
        canvas[:, x : x + img.shape[1]] = img
        x += img.shape[1] + gap_px
    return canvas
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_render_layout.py -v`
Expected: 7 passed.

- [ ] **Step 6: Quality gates and commit**

Run: `uv run ruff check; uv run ruff format --check; uv run mypy; uv run lint-imports`
Expected: all clean.

```bash
git add src/dental_image_organizer/core/render.py src/dental_image_organizer/core/layout.py tests/unit/test_render_layout.py
git commit -m "feat(core): render edits without aliasing and lay out the procedure sequence"
```

---

### Task 10: Procedure service and golden metrics

**Files:**

- Create:
  - `src/dental_image_organizer/services/procedure.py`
  - `tests/golden/test_procedure_golden.py`
- Test: `tests/unit/test_procedure_service.py`

**Interfaces:**

- Consumes:
  - `align.WORKING_LONG_EDGE`, `align.AlignResult`, `align.PreparedReference`,
    `align.align_to_reference`, `align.prepare_reference` (Tasks 6–7)
  - `color.LabCurves`, `color.fit_lab_curves` (Task 8)
  - `imageio.resize_long_edge`, `imageio.decode` (Task 4)
  - `render.render`, `render.coverage_mask` (Task 9)
- Produces, in `dental_image_organizer.services.procedure`:
  - `OUTPUT_SCALES = (1.0, 1.5, 2.0)`
  - `ProcedureEdit(transform: tf.Matrix, curves: LabCurves | None, align: AlignResult)`
  - `compute_edit(photo_rgb, reference: PreparedReference) -> ProcedureEdit`
  - `output_size(reference_size, output_scale) -> tuple[int, int]`. Raises `ValueError`
    outside `OUTPUT_SCALES`.
  - `render_edit(photo_rgb, edit, size) -> RGBImage`
  - `render_reference(reference_rgb, size) -> RGBImage`

Colors are fitted only when the alignment was actually estimated. The center-fit fallback has no
pixel correspondence, so fitting there would shift colors arbitrarily; such photos keep their
own colors (`curves=None`).

- [ ] **Step 1: Write the failing test**

`tests/unit/test_procedure_service.py`:

```python
import io

import numpy as np
import pytest
from PIL import ExifTags, Image

from dental_image_organizer.core.align import prepare_reference
from dental_image_organizer.core.color import to_lab
from dental_image_organizer.core.imageio import decode
from dental_image_organizer.core.render import coverage_mask
from dental_image_organizer.services.procedure import (
    compute_edit,
    output_size,
    render_edit,
    render_reference,
)
from support.scenarios import about_center, make_pair
from support.synthetic import grade, make_scene

W, H = 2400, 1600


@pytest.fixture(scope="module")
def scene() -> np.ndarray:
    return make_scene(51, W, H)


def _mean_delta_e(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    return float(np.linalg.norm(to_lab(a) - to_lab(b), axis=-1)[mask].mean())


def test_processed_photo_matches_reference_geometry_and_color(scene: np.ndarray) -> None:
    pair = make_pair(scene, about_center(W, H, 0.9, 6.0))
    # Another session: darker and cooler than the graded reference.
    photo = grade(pair.photo, gain=0.85, warm=-8.0)
    reference = prepare_reference(pair.reference)

    edit = compute_edit(photo, reference)
    size = (pair.reference.shape[1], pair.reference.shape[0])
    out = render_edit(photo, edit, size)

    covered = coverage_mask((W, H), edit.transform, size)
    assert edit.align.status == "ok"
    assert edit.curves is not None
    assert _mean_delta_e(out, pair.reference, covered) < 4


def test_fallback_keeps_the_photo_colors(scene: np.ndarray) -> None:
    reference = prepare_reference(grade(scene[400:1200, 600:1800]))

    edit = compute_edit(make_scene(52, W, H), reference)

    assert edit.align.status == "needs_review"
    assert edit.curves is None


def test_exif_rotated_portrait_photo_is_aligned(scene: np.ndarray) -> None:
    pair = make_pair(scene, about_center(W, H, 1.0, 3.0))
    # Store the photo the way an iPhone does in portrait: pixels rotated, orientation tag 6.
    stored = Image.fromarray(pair.photo).transpose(Image.Transpose.ROTATE_90)
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    buf = io.BytesIO()
    stored.save(buf, format="JPEG", quality=95, exif=exif.tobytes())

    photo = decode(buf.getvalue()).rgb
    edit = compute_edit(photo, prepare_reference(pair.reference))

    assert photo.shape == pair.photo.shape
    assert edit.align.status == "ok"


@pytest.mark.parametrize(
    ("scale", "expected"), [(1.0, (1200, 800)), (1.5, (1800, 1200)), (2.0, (2400, 1600))]
)
def test_output_size(scale: float, expected: tuple[int, int]) -> None:
    assert output_size((1200, 800), scale) == expected


def test_output_scale_outside_the_allowed_set_is_rejected() -> None:
    with pytest.raises(ValueError):
        output_size((1200, 800), 3.0)


def test_render_reference_upscales_the_reference() -> None:
    reference = make_scene(53, 600, 400)

    assert render_reference(reference, (900, 600)).shape == (600, 900, 3)
    assert render_reference(reference, (600, 400)) is reference
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_procedure_service.py -v`
Expected: FAIL.
`ModuleNotFoundError: No module named 'dental_image_organizer.services.procedure'`.

- [ ] **Step 3: Implement `services/procedure.py`**

```python
"""Feature 1 pipeline without storage: align, color-match, and render one photo (spec §3.2)."""

from __future__ import annotations

from dataclasses import dataclass

import cv2

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.align import (
    WORKING_LONG_EDGE,
    AlignResult,
    PreparedReference,
    align_to_reference,
)
from dental_image_organizer.core.color import LabCurves, fit_lab_curves
from dental_image_organizer.core.imageio import RGBImage, resize_long_edge
from dental_image_organizer.core.render import coverage_mask, render

OUTPUT_SCALES = (1.0, 1.5, 2.0)


@dataclass(frozen=True)
class ProcedureEdit:
    transform: tf.Matrix
    curves: LabCurves | None
    align: AlignResult


def compute_edit(photo_rgb: RGBImage, reference: PreparedReference) -> ProcedureEdit:
    aligned = align_to_reference(photo_rgb, reference)
    curves = None
    # The center-fit fallback has no pixel correspondence, and fitting colors on unrelated
    # content would shift them arbitrarily, so it keeps the photo's own colors.
    if aligned.estimated:
        working, _ = resize_long_edge(photo_rgb, WORKING_LONG_EDGE)
        size = (reference.working.shape[1], reference.working.shape[0])
        warped = render(working, aligned.transform, size, interpolation=cv2.INTER_LINEAR)
        valid = coverage_mask((working.shape[1], working.shape[0]), aligned.transform, size)
        curves = fit_lab_curves(warped, reference.working, valid, reference.working_teeth)
    return ProcedureEdit(transform=aligned.transform, curves=curves, align=aligned)


def output_size(reference_size: tuple[int, int], output_scale: float) -> tuple[int, int]:
    if output_scale not in OUTPUT_SCALES:
        raise ValueError(f"output_scale must be one of {OUTPUT_SCALES}, got {output_scale}")
    w, h = reference_size
    return round(w * output_scale), round(h * output_scale)


def render_edit(photo_rgb: RGBImage, edit: ProcedureEdit, size: tuple[int, int]) -> RGBImage:
    return render(photo_rgb, edit.transform, size, curves=edit.curves)


def render_reference(reference_rgb: RGBImage, size: tuple[int, int]) -> RGBImage:
    if (reference_rgb.shape[1], reference_rgb.shape[0]) == size:
        return reference_rgb
    return render(reference_rgb, tf.identity(), size)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_procedure_service.py -v`
Expected: 8 passed.

- [ ] **Step 5: Add the golden metrics test (spec §8.3)**

`tests/golden/test_procedure_golden.py`:

```python
"""Alignment metrics on real, de-identified photos (spec §8.3). Never runs in CI.

Layout: local_fixtures/procedure/<case>/reference.<ext> plus that case's other photos.
Each run writes local_fixtures/reports/procedure_<case>.csv for threshold tuning.
"""

import csv
from pathlib import Path

import pytest

from dental_image_organizer.core.align import prepare_reference
from dental_image_organizer.core.imageio import decode
from dental_image_organizer.errors import UnsupportedFormatError
from dental_image_organizer.services.procedure import compute_edit

ROOT = Path(__file__).resolve().parents[2] / "local_fixtures"
CASES = ROOT / "procedure"

pytestmark = [
    pytest.mark.golden,
    pytest.mark.skipif(not CASES.is_dir(), reason="local_fixtures/procedure is not present"),
]


def _cases() -> list[Path]:
    return sorted(p for p in CASES.iterdir() if p.is_dir()) if CASES.is_dir() else []


@pytest.mark.parametrize("case", _cases(), ids=lambda p: p.name)
def test_procedure_case_metrics(case: Path) -> None:
    reference_path = next(case.glob("reference.*"))
    reference = prepare_reference(decode(reference_path.read_bytes()).rgb)
    rows = []
    for path in sorted(p for p in case.iterdir() if p.is_file() and p != reference_path):
        try:
            photo = decode(path.read_bytes())
        except UnsupportedFormatError:
            continue
        result = compute_edit(photo.rgb, reference).align
        rows.append(
            {
                "photo": path.name,
                "status": result.status,
                "confidence": round(result.confidence, 4),
                "inliers": result.inliers,
                "ecc_cc": round(result.ecc_cc, 4),
                "model": result.model,
                "homography_note": result.homography_note or "",
                "tight_crop": result.tight_crop,
                "mirrored": result.mirrored,
                "detail_ratio": round(result.detail_ratio, 3),
            }
        )
    report = ROOT / "reports" / f"procedure_{case.name}.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["photo"])
        writer.writeheader()
        writer.writerows(rows)
    assert rows, f"no photos found in {case}"
```

Run: `uv run pytest tests/golden -v`
Expected: `1 skipped` (`local_fixtures/procedure is not present`).

- [ ] **Step 6: Quality gates and commit**

Run: `uv run ruff check; uv run ruff format --check; uv run mypy; uv run lint-imports`
Expected: all clean.

```bash
git add src/dental_image_organizer/services/procedure.py tests/unit/test_procedure_service.py tests/golden/test_procedure_golden.py
git commit -m "feat(services): compute and render procedure edits; add golden metrics test"
```

---

### Task 11: `dio process` command

**Files:**

- Create: `src/dental_image_organizer/services/headless.py`
- Modify:
  - `src/dental_image_organizer/cli.py` (replace the whole file)
  - `README.md` (status line and a Development section)
- Test: `tests/integration/test_cli_process.py`

**Interfaces:**

- Consumes:
  - `services.procedure` (Task 10)
  - `align.prepare_reference` (Task 6)
  - `imageio.decode`, `imageio.encode_jpeg` (Task 4)
  - `layout.render_sequence` (Task 9)
  - `naming.photo_filename`, `naming.sequence_filename`, `naming.sanitize_component`,
    `naming.unique_name` (Task 3)
  - `log.configure_logging` and the errors module (Task 1)
- Produces:
  - `services.headless.PhotoOutcome(source, status, output, confidence, model, message, skip_reason)`
    with `status ∈ {ok, needs_review, failed, skipped}`
  - `services.headless.ProcedureReport(reference_output, photos, sequence_output)` with
    property `.failed`
  - `services.headless.run_procedure(reference_path, photo_paths, out_dir, *, patient, title, output_scale=1.0, today=None) -> ProcedureReport`
  - CLI command: `dio process PHOTOS... --reference REF --out DIR --patient NAME --title TITLE [--output-scale 1|1.5|2] [-v]`

Behavior:

- **Names are validated before any file is written.**
- **Existing files are never overwritten.** Collisions get `_2`, `_3`, …
- **Unsupported files** (videos, `.AAE` sidecars, RAW) are skipped, and one summary line is
  printed.
- **A corrupt photo** fails alone and the batch continues, but the exit code becomes 1.
- **An unreadable reference or an invalid name** prints `error: …` and exits with 1.
- **An invalid `--output-scale`** is a usage error and exits with 2.
- **Photos without an EXIF capture time** use the file modification time and show a warning.
- **The sequence** sorts by recorded wall-clock time, so offset-aware and naive times can mix.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_cli_process.py`:

```python
import io
import os
import unicodedata
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest
from PIL import ExifTags, Image
from typer.testing import CliRunner

from dental_image_organizer.cli import app
from support.scenarios import about_center, make_pair
from support.synthetic import make_scene

W, H = 2400, 1600
TODAY = date.today().strftime("%Y%m%d")


def _jpeg(rgb: np.ndarray, taken: str | None) -> bytes:
    exif = Image.Exif()
    if taken:
        sub = exif.get_ifd(ExifTags.IFD.Exif)
        sub[ExifTags.Base.DateTimeOriginal] = taken
        sub[ExifTags.Base.OffsetTimeOriginal] = "+08:00"
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=92, exif=exif.tobytes())
    return buf.getvalue()


@pytest.fixture(scope="module")
def case_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("case")
    scene = make_scene(61, W, H)
    first = make_pair(scene, about_center(W, H, 1.0, 4.0))
    (folder / "reference.jpg").write_bytes(_jpeg(first.reference, "2026:10:16 09:00:00"))
    (folder / "IMG_0001.jpg").write_bytes(_jpeg(first.photo, "2026:10:16 14:30:05"))
    burst = make_pair(scene, about_center(W, H, 0.9, -5.0))
    (folder / "IMG_0002.jpg").write_bytes(_jpeg(burst.photo, "2026:10:16 14:30:40"))
    later = make_pair(scene, about_center(W, H, 1.1, 2.0))
    no_exif = folder / "IMG_0003.jpg"
    no_exif.write_bytes(_jpeg(later.photo, None))
    stamp = datetime(2026, 10, 20, 8, 15).timestamp()
    os.utime(no_exif, (stamp, stamp))
    (folder / "IMG_0004.MOV").write_bytes(b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  ")
    (folder / "IMG_0001.AAE").write_bytes(b'<?xml version="1.0" encoding="UTF-8"?><plist/>')
    return folder


def _invoke(case_dir: Path, out: Path, *extra: str, patient: str = "Alex Wang") -> object:
    photos = sorted(str(p) for p in case_dir.iterdir() if p.name != "reference.jpg")
    return CliRunner().invoke(
        app,
        [
            "process",
            *photos,
            "--reference",
            str(case_dir / "reference.jpg"),
            "--out",
            str(out),
            "--patient",
            patient,
            "--title",
            "implant 16",
            *extra,
        ],
    )


def test_process_writes_named_outputs_and_a_sequence(case_dir: Path, tmp_path: Path) -> None:
    result = _invoke(case_dir, tmp_path / "out")

    assert result.exit_code == 0, result.output
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert names == sorted(
        [
            "Alex Wang_implant 16_20261016_0900.jpg",
            "Alex Wang_implant 16_20261016_1430.jpg",
            "Alex Wang_implant 16_20261016_1430_2.jpg",
            "Alex Wang_implant 16_20261020_0815.jpg",
            f"Alex Wang_implant 16_Procedure_{TODAY}.jpg",
        ]
    )
    sequence = Image.open(tmp_path / "out" / f"Alex Wang_implant 16_Procedure_{TODAY}.jpg")
    assert sequence.size == (4 * 1200, 800)
    assert "Skipped 2 files: 1 edit sidecar, 1 video" in result.output
    assert "file modification time" in result.output


def test_output_scale_enlarges_every_output(case_dir: Path, tmp_path: Path) -> None:
    result = _invoke(case_dir, tmp_path / "out", "--output-scale", "1.5")

    assert result.exit_code == 0, result.output
    single = Image.open(tmp_path / "out" / "Alex Wang_implant 16_20261016_1430.jpg")
    assert single.size == (1800, 1200)


def test_existing_files_are_never_overwritten(case_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "Alex Wang_implant 16_20261016_0900.jpg").write_bytes(b"keep me")

    result = _invoke(case_dir, out)

    assert result.exit_code == 0, result.output
    assert (out / "Alex Wang_implant 16_20261016_0900.jpg").read_bytes() == b"keep me"
    assert (out / "Alex Wang_implant 16_20261016_0900_2.jpg").exists()


def test_nfd_patient_name_produces_nfc_file_names(case_dir: Path, tmp_path: Path) -> None:
    result = _invoke(case_dir, tmp_path / "out", patient=unicodedata.normalize("NFD", "王小明"))

    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "王小明_implant 16_20261016_0900.jpg").exists()


def test_corrupt_photo_fails_without_stopping_the_batch(case_dir: Path, tmp_path: Path) -> None:
    broken = tmp_path / "broken.jpg"
    broken.write_bytes((case_dir / "IMG_0001.jpg").read_bytes()[:300])

    result = CliRunner().invoke(
        app,
        [
            "process",
            str(broken),
            str(case_dir / "IMG_0001.jpg"),
            "--reference",
            str(case_dir / "reference.jpg"),
            "--out",
            str(tmp_path / "out"),
            "--patient",
            "A",
            "--title",
            "B",
        ],
    )

    assert result.exit_code == 1
    assert "failed" in result.output
    assert (tmp_path / "out" / "A_B_20261016_1430.jpg").exists()


def test_unreadable_reference_is_an_error(case_dir: Path, tmp_path: Path) -> None:
    bad = tmp_path / "reference.mov"
    bad.write_bytes(b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  ")

    result = CliRunner().invoke(
        app,
        [
            "process",
            str(case_dir / "IMG_0001.jpg"),
            "--reference",
            str(bad),
            "--out",
            str(tmp_path / "out"),
            "--patient",
            "A",
            "--title",
            "B",
        ],
    )

    assert result.exit_code == 1
    assert "Videos are not supported" in result.output
    assert not (tmp_path / "out").exists()


def test_invalid_output_scale_is_a_usage_error(case_dir: Path, tmp_path: Path) -> None:
    result = _invoke(case_dir, tmp_path / "out", "--output-scale", "3")

    assert result.exit_code == 2


def test_empty_patient_name_is_rejected_before_writing(case_dir: Path, tmp_path: Path) -> None:
    result = _invoke(case_dir, tmp_path / "out", patient="///")

    assert result.exit_code == 1
    assert not (tmp_path / "out").exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/integration/test_cli_process.py -v`
Expected: FAIL. The output contains `No such command 'process'`, so exit code 2 ≠ 0.

- [ ] **Step 3: Implement `services/headless.py`**

```python
"""File-based Feature 1 batch behind `dio process` (spec §4.10): no library, no server."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import structlog

from dental_image_organizer.core.align import prepare_reference
from dental_image_organizer.core.imageio import DecodedImage, RGBImage, decode, encode_jpeg
from dental_image_organizer.core.layout import render_sequence
from dental_image_organizer.core.naming import (
    photo_filename,
    sanitize_component,
    sequence_filename,
    unique_name,
)
from dental_image_organizer.errors import CorruptImageError, SkipReason, UnsupportedFormatError
from dental_image_organizer.services.procedure import (
    compute_edit,
    output_size,
    render_edit,
    render_reference,
)

log = structlog.get_logger(__name__)

OutcomeStatus = Literal["ok", "needs_review", "failed", "skipped"]
MTIME_WARNING = "No capture time in EXIF; used the file modification time."


@dataclass(frozen=True)
class PhotoOutcome:
    source: Path
    status: OutcomeStatus
    output: Path | None = None
    confidence: float | None = None
    model: str | None = None
    message: str | None = None
    skip_reason: SkipReason | None = None


@dataclass(frozen=True)
class ProcedureReport:
    reference_output: Path
    photos: list[PhotoOutcome]
    sequence_output: Path

    @property
    def failed(self) -> bool:
        return any(p.status == "failed" for p in self.photos)


def run_procedure(
    reference_path: Path,
    photo_paths: Sequence[Path],
    out_dir: Path,
    *,
    patient: str,
    title: str,
    output_scale: float = 1.0,
    today: date | None = None,
) -> ProcedureReport:
    # Validate names before any work so a bad name cannot leave a half-written export.
    sanitize_component(patient)
    sanitize_component(title)
    reference = _load(reference_path)
    size = output_size(reference.size, output_scale)
    prepared = prepare_reference(reference.rgb)

    out_dir.mkdir(parents=True, exist_ok=True)
    taken = {p.name for p in out_dir.iterdir()}
    reference_time, _ = _captured_at(reference, reference_path)
    reference_image = render_reference(reference.rgb, size)
    reference_output = _write(
        out_dir,
        taken,
        photo_filename(patient, title, reference_time),
        encode_jpeg(reference_image, reference.metadata),
    )

    frames: list[tuple[datetime, RGBImage]] = [(reference_time, reference_image)]
    outcomes: list[PhotoOutcome] = []
    for path in photo_paths:
        try:
            photo = _load(path)
        except UnsupportedFormatError as exc:
            outcomes.append(PhotoOutcome(path, "skipped", message=str(exc), skip_reason=exc.reason))
            continue
        except (CorruptImageError, OSError) as exc:
            log.error("photo_unreadable", source=path.name, error=str(exc))
            outcomes.append(PhotoOutcome(path, "failed", message=str(exc)))
            continue
        edit = compute_edit(photo.rgb, prepared)
        image = render_edit(photo.rgb, edit, size)
        when, from_file = _captured_at(photo, path)
        output = _write(
            out_dir, taken, photo_filename(patient, title, when), encode_jpeg(image, photo.metadata)
        )
        frames.append((when, image))
        notes = [m for m in (edit.align.message, MTIME_WARNING if from_file else None) if m]
        outcomes.append(
            PhotoOutcome(
                path,
                edit.align.status,
                output,
                edit.align.confidence,
                edit.align.model,
                " ".join(notes) or None,
            )
        )

    # Mixing EXIF times that carry an offset with naive file times must not break sorting;
    # the recorded wall-clock time is what orders a single case.
    frames.sort(key=lambda frame: frame[0].replace(tzinfo=None))
    sequence_output = _write(
        out_dir,
        taken,
        sequence_filename(patient, title, today or date.today()),
        encode_jpeg(render_sequence([image for _, image in frames])),
    )
    return ProcedureReport(reference_output, outcomes, sequence_output)


def _load(path: Path) -> DecodedImage:
    return decode(path.read_bytes())


def _captured_at(image: DecodedImage, path: Path) -> tuple[datetime, bool]:
    if image.metadata.captured_at is not None:
        return image.metadata.captured_at, False
    log.warning("capture_time_from_file", source=path.name)
    return datetime.fromtimestamp(path.stat().st_mtime), True


def _write(out_dir: Path, taken: set[str], name: str, data: bytes) -> Path:
    final = unique_name(name, taken)
    taken.add(final)
    path = out_dir / final
    path.write_bytes(data)
    return path
```

- [ ] **Step 4: Replace `cli.py`**

```python
"""Command-line interface (spec §4.10)."""

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

from dental_image_organizer import __version__
from dental_image_organizer.errors import DioError
from dental_image_organizer.log import configure_logging
from dental_image_organizer.services.headless import ProcedureReport, run_procedure
from dental_image_organizer.services.procedure import OUTPUT_SCALES

app = typer.Typer(no_args_is_help=True, add_completion=False)

_SKIP_LABELS = {
    "video": "video",
    "raw": "RAW/DNG file",
    "sidecar": "edit sidecar",
    "unknown": "unrecognized file",
}


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_print_version, is_eager=True, help="Print the version and exit."
        ),
    ] = False,
) -> None:
    """Align, color-match, and compose clinical intraoral photos."""


@app.command()
def process(
    photos: Annotated[
        list[Path],
        typer.Argument(help="Intra-op and post-op photos.", exists=True, dir_okay=False),
    ],
    reference: Annotated[
        Path,
        typer.Option(
            "--reference",
            "-r",
            help="The dentist-edited pre-op reference photo.",
            exists=True,
            dir_okay=False,
        ),
    ],
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Output folder; created if missing.", file_okay=False),
    ],
    patient: Annotated[str, typer.Option(help="Patient name used in file names.")],
    title: Annotated[str, typer.Option(help="Procedure or site name used in file names.")],
    output_scale: Annotated[
        float, typer.Option(help="Output size relative to the reference: 1, 1.5, or 2.")
    ] = 1.0,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Log alignment metrics.")
    ] = False,
) -> None:
    """Align photos to a reference; write individual photos and a sequence image."""
    configure_logging("DEBUG" if verbose else "WARNING")
    if output_scale not in OUTPUT_SCALES:
        raise typer.BadParameter("must be 1, 1.5, or 2", param_hint="--output-scale")
    try:
        report = run_procedure(
            reference, photos, out, patient=patient, title=title, output_scale=output_scale
        )
    except DioError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print_report(report)
    if report.failed:
        raise typer.Exit(1)


def _print_report(report: ProcedureReport) -> None:
    typer.echo(f"{'reference':<13} {'':>5}  -> {report.reference_output.name}")
    for outcome in report.photos:
        if outcome.status == "skipped":
            continue
        if outcome.status == "failed":
            typer.echo(f"{'failed':<13} {'':>5}  {outcome.source.name}: {outcome.message}")
            continue
        assert outcome.output is not None and outcome.confidence is not None
        line = (
            f"{outcome.status:<13} {outcome.confidence:5.2f}  "
            f"{outcome.source.name} -> {outcome.output.name}"
        )
        typer.echo(f"{line}  ({outcome.message})" if outcome.message else line)
    typer.echo(f"{'sequence':<13} {'':>5}  -> {report.sequence_output.name}")
    skipped = Counter(o.skip_reason for o in report.photos if o.status == "skipped")
    if skipped:
        parts = [
            f"{count} {_SKIP_LABELS[reason]}{'s' if count > 1 else ''}"
            for reason, count in sorted(skipped.items(), key=lambda item: str(item[0]))
            if reason is not None
        ]
        total = sum(skipped.values())
        typer.echo(f"Skipped {total} file{'s' if total > 1 else ''}: {', '.join(parts)}")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/integration/test_cli_process.py -v`
Expected: 8 passed. This takes about 15 s.

Run: `uv run dio process --help`
Expected: usage text listing `--reference`, `--out`, `--patient`, `--title`, `--output-scale`,
and `--verbose`.

- [ ] **Step 6: Update `README.md`**

Replace the status block:

```markdown
> **Status: design phase.** The technical design is complete. Implementation has not started yet.
> See the [design spec](doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md).
```

with:

```markdown
> **Status: early development.** The procedure pipeline is available as a command-line tool
> (`dio process`); the app UI is not built yet. See the
> [design spec](doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md).
```

Insert this section directly before `## Disclaimer`:

````markdown
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

### Golden photos (local only)

Real, de-identified photos for tuning go in `local_fixtures/procedure/<case>/`: a
`reference.<ext>` plus that case's other photos. The folder is gitignored. Run
`uv run pytest tests/golden` to write per-photo metrics to `local_fixtures/reports/`.
````

- [ ] **Step 7: Run the whole suite and the quality gates**

Run: `uv run pytest`
Expected: `112 passed, 1 skipped` in about 30 s.

Run: `uv run ruff check; uv run ruff format --check; uv run mypy; uv run lint-imports`
Expected: all clean.

- [ ] **Step 8: Commit**

```bash
git add src/dental_image_organizer/services/headless.py src/dental_image_organizer/cli.py tests/integration/test_cli_process.py README.md
git commit -m "feat(cli): add dio process for headless procedure batches"
```

- [ ] **Step 9: Push and open a pull request**

```bash
git push -u origin feat/procedure-pipeline
gh pr create --title "Plan 1: foundation and Feature 1 pipeline (dio process)" --body "Implements doc/superpowers/plans/2026-10-16-plan-1-procedure-pipeline.md."
```

Expected: CI passes on ubuntu, windows, and macos.
