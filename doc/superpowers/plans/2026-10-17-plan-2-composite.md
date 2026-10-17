# Plan 2 — Feature 2 Composite and AI Slot Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `dio composite`. It takes the photos of one composite session and produces one
3 × 3 composite:

1. assign each photo to a slot (AI classifier, or shooting order before the classifier is
   trained);
2. flip mirror shots, level, and smart-crop every cell;
3. unify colors to a baseline cell.

It also learns from runs the dentist confirms with `--learn`.

**Architecture:**

| Layer | Contents |
|---|---|
| `core/` (pure) | slots, per-slot geometry, Reinhard color, 3 × 3 layout |
| `ml/` (new; imports `core` only) | embeddings, sample store, classifier heads, slot assignment, model store |
| `services/composite.py` | orchestrates one composite in memory |
| `services/headless_composite.py` | the file batch behind the CLI |

App data lives in platformdirs folders (`paths.py`). The DINOv2 model is exported by a
maintainer script and published as a GitHub Release asset; tests use a tiny fake ONNX model
with the same interface.

**Tech Stack:** Plan 1's stack, plus:

| Purpose | Packages |
|---|---|
| Embedding inference | `onnxruntime` (CPU) |
| Classifier heads | `scikit-learn` (`LogisticRegression`) |
| Slot assignment | `scipy` (`linear_sum_assignment`) |
| Model download | `httpx` |
| App folders | `platformdirs` |
| Dev only | `onnx` (builds the fake test model) |
| Maintainer only, via the export script's inline metadata | `torch`, `transformers` |

**Spec:** [doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md](../specs/2026-10-16-dental-image-organizer-design.md).
Read §3.3, §4.6, §4.7, §4.9 (composite name), §4.10, §4.11 (app data), §8.1, and §11 before
starting.

## Where This Fits

Plan 1 (foundation and `dio process`) is merged. This plan is 2 of 5.

**Deferred to Plan 3**, because each needs Settings, the library, or the API:

- flip *learning* (`flips.json`, pins);
- teaching sessions (§3.5);
- the EXIF-order setting stored in `settings.json`;
- edit records in `case.json`.

In this plan, flips start from the §11 defaults, and the CLI overrides them with `--flip`.

## Spec Amendments (confirm before executing)

The scratch implementation of this plan was measured against the real exported DINOv2-S model.
Two places deviate from the spec text. Task 1 updates the spec to match.

1. **§4.6 classifier regularization: `C = 1.0` → `C = 10.0`.**
   - Each 384-d block is unit length, so the 2688-d feature is short (norm √7).
   - Measured on 8 held-out synthetic sets with 3 training samples per slot:

     | C | Accuracy | Median confidence | Slots flagged low-confidence (p < 0.5) |
     |---|---|---|---|
     | 1 | 86% | 0.40 | 99% |
     | 10 | 86% | 0.75 | 10% |
     | 100 | 89% | 0.94 | 10% |

   - At C = 1 every slot is flagged, which makes the flag useless. C = 10 keeps accuracy and
     makes the flag meaningful.
   - The golden test (Task 11) reports leave-one-set-out accuracy for C = 1, 10, and 100 on the
     dentist's real sets, so the value can be re-tuned on real photos.
2. **§4.10 `dio composite` options.** The spec row lists only `--out DIR PHOTOS...`. The CLI
   adds:

   | Option | Purpose |
   |---|---|
   | `--patient`, `--tag` | File naming (as in `dio process`) |
   | `--order` | The shooting order (§4.6 fallback) |
   | `--baseline` | The color baseline (§3.3 step 4) |
   | `--flip SLOT=FLIP` | Per-slot flip override |
   | `--assign FILE=SLOT\|tray` | The CLI form of drag-to-fix |
   | `--learn` | The CLI form of §3.3 step 7: store this run as confirmed samples |
   | `--download-model` | The explicit consent §4.6 requires before the one-time download |
   | `-v` | Verbose logging |

## Before You Start

- Run `git fetch origin && git switch -c feat/composite origin/main`. The base must contain
  the Plan 1 merge, `4e61611`. Branch from `origin/main`, not the local `main`: the local `main`
  still holds two pre-rebase copies of the research-notes commits, and pulling into it would
  create a needless merge.
- **Verified baseline.** A scratch copy of this plan ran on 2026-10-17:
  - 249 passed, 3 skipped (the golden tests need local photos);
  - mypy `--strict`, ruff, and both import-linter contracts clean.

  Library versions:

  | Package | Version |
  |---|---|
  | numpy | 2.5.4 |
  | opencv-python-headless | 5.0.0.93 |
  | onnxruntime | 1.31.0 |
  | onnx | 1.23.2 |
  | scikit-learn | 1.9.1 |
  | scipy | 1.18.1 |
  | httpx | 0.28.1 |
  | platformdirs | 4.13.4 |
  | torch (export only) | 2.14.1 |
  | transformers (export only) | 5.20.1 |

  Reported figures for the synthetic scenes:

  - leveling error ≤ 0.2° for every slot over ±13°;
  - Reinhard reduces tooth-region ΔE by 82%;
  - one composite (9 cells at 3600 × 2400) plans in 0.9 s and renders in 0.5 s;
  - the int8 model's block cosine against PyTorch is ≥ 0.982.

  If a tolerance fails, report it. Do not loosen it.

## Global Constraints

Everything in Plan 1's Global Constraints still applies:

- English text; comments explain *why*;
- structlog only;
- no image files in git;
- LF line endings;
- all five quality gates pass before each commit;
- each commit ends with the `Co-Authored-By:` trailer of the model that writes it.

Additions:

- **Formatting.** Code blocks in this plan are condensed (several imports per line, a few lines
  over 100 characters). Run `uv run ruff format` and `uv run ruff check --fix` before the gates;
  that changes layout only.

- **Import boundaries.** `dental_image_organizer.core` must not import `ml`, `services`, `cli`,
  or `paths`. `dental_image_organizer.ml` must not import `services`, `cli`, or `paths`. Both
  rules are import-linter contracts.
- **Privacy.** Log events never contain file names, patient names, or tags. Batch code binds
  `photo=<1-based position>` with `structlog.contextvars.bound_contextvars`, as
  `services/headless.py` already does.
- **Network.**
  - The only network call is `ml.model_store.download_model`. It runs only when the user passed
    `--download-model`, and it is announced with `from github.com` in the message.
  - Tests never touch the network; they use `httpx.MockTransport` or the fake model.
- **Batch isolation.** One unreadable or failing photo never aborts a composite. It is reported
  as `failed`, its slot stays empty, and the exit code becomes 1 (spec §4.13).
- **Spec values to copy exactly.**

  | Item | Value | Spec |
  |---|---|---|
  | Slot order | `upper_right, upper_occlusal, upper_left, occlusion_right, occlusion_front, occlusion_left, lower_right, lower_occlusal, lower_left` | §4.6 |
  | Leveling | clamp ±15°; occlusal symmetry search in 0.5° steps | §4.7.2 |
  | Smart crop | 2nd–98th percentile box, +12% per side, zoom ≤ 1.3× | §4.7.2 |
  | Reinhard | strength 0.8; mean shift clamped to ±12 L / ±8 a\*b\*; method `lab_reinhard_v1` | §4.7.3 |
  | Embedding input | 336 × 224 | §4.6 |
  | Embedding output | 7 blocks × 384, each block L2-normalized | §4.6 |
  | Inference batch | 18 (9 photos + 9 mirrors) | §4.6 |
  | Classifier | trained once every slot has ≥ 3 samples; low-confidence below p = 0.5 | §4.6 |
  | Cell | 1200 × 800, gap 0, black | §11 |
  | Baseline | `occlusion_front` | §3.3 |
  | Composite name | `{patient}_{tag}_Composite_{YYYYMMDD}.jpg`, using the most frequent capture date | §4.9 |
  | Flip starting values | occlusal `vertical`; buccal corners `horizontal`; occlusion slots `none` | §11 |

## Review Focus

These are inputs the spec implies but never spells out, and that are likely to bite a dentist.
Each one is pinned by a test in the owning task.

1. **Re-running a set.** The dentist re-runs a set into the same folder after fixing a slot with
   `--assign`. A new `…_Composite_YYYYMMDD_2.jpg` is written; the first file is untouched.
   → Task 10, `test_a_second_run_never_overwrites_the_first_composite`.
2. **Fewer than nine usable photos.** A view was skipped or a file is unreadable. The composite
   is still written with the empty cell black, and the color baseline falls back to the first
   filled slot.
   → Task 9 `test_missing_baseline_falls_back_to_the_first_filled_slot`;
   Task 10 `test_corrupt_photo_leaves_its_slot_empty`.
3. **A photo with no teeth in it.** A smile or face shot is dropped into the set by mistake. No
   crash; the cell keeps the whole frame unleveled.
   → Task 4, `test_photo_without_teeth_keeps_the_whole_frame`.
4. **Retakes, ten or more photos.** The extra photos are listed as `tray`, never silently
   dropped.
   → Task 8 `test_extra_photos_go_to_the_tray_and_low_confidence_is_flagged`;
   Task 9 `test_a_tenth_photo_goes_to_the_tray`.
5. **Download consent and failure.** A trained classifier exists but the model download fails
   (offline). The run falls back to shooting order and says so (spec §4.6).
   → Task 10, `test_failed_download_falls_back_to_shooting_order`.

## File Structure

```
pyproject.toml                                  # Task 1: deps, mypy overrides, 2 import contracts
src/dental_image_organizer/
├── errors.py                                   # Task 1: + ModelUnavailableError, ModelIntegrityError
├── paths.py                                    # Task 1: platformdirs app folders
├── cli.py                                      # Task 10: + `composite` command
├── core/
│   ├── slots.py                                # Task 1: slots, groups, sides, flips, defaults
│   ├── color.py                                # Task 2: LabTransform protocol, LabAffine, fit_reinhard
│   ├── render.py                               # Task 2: `curves=` becomes `color=`
│   ├── layout.py                               # Task 3: + render_composite
│   ├── naming.py                               # Task 3: + composite_capture_date
│   └── geometry.py                             # Task 4: flip, level, smart crop, normalize_view
├── ml/
│   ├── __init__.py                             # Task 5
│   ├── embedder.py                             # Task 5: preprocess, EmbeddingPair, Embedder
│   ├── model_store.py                          # Task 6: ModelSpec, DEFAULT_MODEL, cache, download
│   ├── samples.py                              # Task 7: SampleSet, SampleStore (samples.npz)
│   ├── classifier.py                           # Task 7: group head + side head, model.json
│   └── assignment.py                           # Task 8: Hungarian, shooting order, overrides
└── services/
    ├── procedure.py                            # Task 2: passes color=
    ├── composite.py                            # Task 9: assign, plan, render cells, compose
    ├── batch_io.py                             # Task 10: helpers moved out of headless.py
    ├── headless.py                             # Task 10: imports batch_io
    └── headless_composite.py                   # Task 10: run_composite behind the CLI
scripts/export_dinov2_onnx.py                   # Task 12: maintainer-only export (PEP 723 deps)
tests/support/views.py                          # Task 4: synthetic views for the nine slots
tests/support/fake_model.py                     # Task 5: tiny ONNX model, same interface
tests/support/embeddings.py                     # Task 7: synthetic embedding clusters
tests/unit/test_{slots,paths,reinhard,composite_layout,geometry,embedder,model_store,
                 classifier,samples,assignment,composite_service}.py
tests/integration/test_cli_composite.py         # Task 10
tests/golden/test_composite_golden.py           # Task 11
```

---

### Task 1: Dependencies, slots, app folders, and spec amendments

**Files:**

- Modify: `pyproject.toml`
- Modify: `src/dental_image_organizer/errors.py`
- Modify: `doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md`
- Create: `src/dental_image_organizer/core/slots.py`
- Create: `src/dental_image_organizer/paths.py`
- Test: `tests/unit/test_slots.py`
- Test: `tests/unit/test_paths.py`

**Interfaces:**

- Consumes: nothing new.
- Produces:
  - `core.slots`:
    - Types: `Slot`, `SlotGroup`, `Side = Literal["right", "left"]`,
      `Flip = Literal["none", "horizontal", "vertical", "rotate_180"]`
    - `SLOTS: tuple[Slot, ...]` (row-major), `GROUPS: tuple[SlotGroup, ...]`,
      `FLIPS: tuple[Flip, ...]`
    - `DEFAULT_FLIPS: dict[Slot, Flip]`
    - `slot_group(slot) -> SlotGroup`
    - `slot_side(slot) -> Side | None`
    - `slot_for(group, side) -> Slot`
    - `parse_slot(text) -> Slot`, which raises `ValueError("unknown slot …")`
  - `paths`: `APP_NAME = "DentalImageOrganizer"`, plus `data_dir()`, `cache_dir()`,
    `classifier_dir()` (= `data_dir() / "classifier"`), and `model_cache_dir()`
    (= `cache_dir() / "models"`). All return `Path`.
  - `errors`: `ModelUnavailableError` (code `model_unavailable`) and `ModelIntegrityError`
    (code `model_integrity`), both subclasses of `DioError`.

Group membership (spec §4.6):

| Group | Slots |
|---|---|
| `upper_buccal` | `upper_right`, `upper_left` |
| `upper_occlusal` | `upper_occlusal` |
| `occlusion_side` | `occlusion_right`, `occlusion_left` |
| `occlusion_front` | `occlusion_front` |
| `lower_buccal` | `lower_right`, `lower_left` |
| `lower_occlusal` | `lower_occlusal` |

`GROUPS` is in exactly this order: `upper_buccal, upper_occlusal, occlusion_side,
occlusion_front, lower_buccal, lower_occlusal`.

- [ ] **Step 1: Update `pyproject.toml`**

Add these runtime dependencies, keeping the list alphabetical:
`"httpx>=0.28"`, `"onnxruntime>=1.24"`, `"platformdirs>=4.3"`, `"scikit-learn>=1.6"`, and
`"scipy>=1.14"`.

Add `"onnx>=1.17"` to the `dev` group.

Extend the mypy override module list, because these packages ship no type information:

```toml
module = ["pi_heif", "pi_heif.*", "onnxruntime", "onnxruntime.*", "sklearn", "sklearn.*", "scipy", "scipy.*"]
```

Replace the existing import contract with these two:

```toml
[[tool.importlinter.contracts]]
name = "core is pure: no services or CLI imports"
type = "forbidden"
source_modules = ["dental_image_organizer.core"]
forbidden_modules = [
  "dental_image_organizer.ml",
  "dental_image_organizer.services",
  "dental_image_organizer.cli",
  "dental_image_organizer.paths",
]

[[tool.importlinter.contracts]]
name = "ml uses core only: no services, CLI, or app paths"
type = "forbidden"
source_modules = ["dental_image_organizer.ml"]
forbidden_modules = [
  "dental_image_organizer.services",
  "dental_image_organizer.cli",
  "dental_image_organizer.paths",
]
```

Run `uv lock` and then `uv sync`.
Expected: both succeed.

- [ ] **Step 2: Amend the spec**

- In spec §4.6 "Classifier", change `C = 1.0` to `C = 10.0`. Add one sentence: block-normalized
  embeddings are short vectors, and at C = 1.0 every probability stayed below 0.5 on the
  exported model.
- In the §4.10 table, change the `dio composite` row to:
  `dio composite PHOTOS... --out DIR --patient NAME --tag TAG [--order SLOTS] [--baseline SLOT] [--flip SLOT=FLIP]... [--assign FILE=SLOT|tray]... [--learn] [--download-model]`
  with the purpose "Headless Feature 2; `--learn` stores the run as confirmed samples
  (§3.3 step 7)".

- [ ] **Step 3: Write the failing tests**

`tests/unit/test_slots.py`:

```python
import pytest

from dental_image_organizer.core.slots import (
    DEFAULT_FLIPS, SLOTS, parse_slot, slot_for, slot_group, slot_side,
)


def test_slots_are_row_major() -> None:
    assert SLOTS == (
        "upper_right", "upper_occlusal", "upper_left",
        "occlusion_right", "occlusion_front", "occlusion_left",
        "lower_right", "lower_occlusal", "lower_left",
    )  # fmt: skip


def test_group_and_side_round_trip() -> None:
    for slot in SLOTS:
        assert slot_for(slot_group(slot), slot_side(slot)) == slot
    assert slot_group("occlusion_left") == "occlusion_side"
    assert slot_side("upper_occlusal") is None


def test_default_flips_follow_the_spec_starting_values() -> None:
    assert DEFAULT_FLIPS["upper_occlusal"] == DEFAULT_FLIPS["lower_occlusal"] == "vertical"
    corners = ("upper_right", "upper_left", "lower_right", "lower_left")
    assert {DEFAULT_FLIPS[s] for s in corners} == {"horizontal"}
    assert {DEFAULT_FLIPS[s] for s in SLOTS if s.startswith("occlusion")} == {"none"}


def test_unknown_slot_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown slot"):
        parse_slot("upper_middle")
```

`tests/unit/test_paths.py`:

```python
from dental_image_organizer import paths


def test_app_folders_are_per_user_and_nested() -> None:
    assert paths.classifier_dir() == paths.data_dir() / "classifier"
    assert paths.model_cache_dir() == paths.cache_dir() / "models"
    assert "DentalImageOrganizer" in str(paths.data_dir())
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_slots.py tests/unit/test_paths.py -v`
Expected: FAIL. Errors are `ModuleNotFoundError … core.slots` and
`ImportError: cannot import name 'paths'`.

- [ ] **Step 5: Implement `core/slots.py`, `paths.py`, and the two errors**

- **`core/slots.py`.**
  - `Slot` and `SlotGroup` are `Literal` types written three per line, each followed by
    `# fmt: skip`.
  - `SLOTS`, `GROUPS`, and `FLIPS` are built with `typing.get_args`.
  - `parse_slot` returns `text` after an `in SLOTS` check; mypy narrows the type.
- **`paths.py`.** Use `platformdirs.user_data_dir(APP_NAME, appauthor=False)` and
  `user_cache_dir(...)`.

- [ ] **Step 6: Verify and commit**

Run: `uv run pytest tests/unit/test_slots.py tests/unit/test_paths.py -v`
Expected: 5 passed.

Then run all five quality gates (Global Constraints).
Expected: all clean, with `Contracts: 2 kept`.

```bash
git add pyproject.toml uv.lock doc/superpowers/specs src/dental_image_organizer/core/slots.py src/dental_image_organizer/paths.py src/dental_image_organizer/errors.py tests/unit/test_slots.py tests/unit/test_paths.py
git commit -m "feat(core): add composite slots, app folders, and ML dependencies"
```

---

### Task 2: Lab transforms and Reinhard color unification

**Files:**

- Modify: `src/dental_image_organizer/core/color.py`
- Modify: `src/dental_image_organizer/core/render.py`
- Modify: `src/dental_image_organizer/services/procedure.py:68`
- Modify: `tests/unit/test_color.py` (rename only)
- Modify: `tests/unit/test_render_layout.py:71` (rename only)
- Test: `tests/unit/test_reinhard.py`

**Interfaces:**

- Consumes: `color.to_lab`, `color.CHANNELS`, `masks.BoolMask`.
- Produces:
  - `color.LabTransform`: a `Protocol` with
    `apply_lab(self, lab: NDArray[floating]) -> NDArray[float32]`. `LabCurves` already
    satisfies it.
  - `color.apply_lab_transform(rgb, transform: LabTransform, *, chunk_rows=APPLY_CHUNK_ROWS) -> RGBImage`.
    This renames `apply_lab_curves`; the body is unchanged.
  - `color.LabAffine(gains: tuple[float, float, float], offsets: tuple[float, float, float])`
    (frozen) with:
    - `.apply_lab`: computes `lab * gains + offsets`;
    - `.to_params() -> {"L": {"gain", "offset"}, "a": …, "b": …}`;
    - `LabAffine.from_params(params)`.
  - `color.fit_reinhard(source_rgb, source_mask, target_rgb, target_mask) -> LabAffine`
  - Constants:

    | Constant | Value |
    |---|---|
    | `REINHARD_METHOD` | `"lab_reinhard_v1"` |
    | `REINHARD_STRENGTH` | `0.8` |
    | `REINHARD_MEAN_LIMITS` | `(12.0, 8.0, 8.0)` |
    | `REINHARD_GAIN_RANGE` | `(0.67, 1.5)` |
    | `MIN_STATS_SHARE` | `0.05` |

  - `render.render(..., color: LabTransform | None = None, ...)`. The keyword was `curves`.

The gain clamp `REINHARD_GAIN_RANGE` is this plan's guard against a near-zero source spread;
the spec is silent on it.

- [ ] **Step 1: Rename the existing API, a pure refactor**

- `apply_lab_curves` → `apply_lab_transform`, with the parameter `curves` → `transform`.
- `render(curves=)` → `render(color=)`.
- In `services/procedure.render_edit`, pass `color=edit.curves`.
- In `tests/unit/test_color.py` and `tests/unit/test_render_layout.py`, rename the call sites.
- Reword `render`'s comment about the black fill: color maps keep offsets (curves past their
  fitted range, affine maps everywhere), so they would tint the fill.

Run: `uv run pytest tests/unit/test_color.py tests/unit/test_render_layout.py tests/unit/test_procedure_service.py -q`
Expected: all pass.

- [ ] **Step 2: Write the failing test**

`tests/unit/test_reinhard.py`:

```python
import numpy as np

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.color import LabAffine, apply_lab_transform, fit_reinhard, to_lab
from dental_image_organizer.core.masks import gingiva_mask, tooth_mask
from dental_image_organizer.core.render import render
from support.synthetic import grade, make_scene


def _region(rgb: np.ndarray) -> np.ndarray:
    teeth = tooth_mask(rgb)
    return teeth | gingiva_mask(rgb, teeth)


def _tooth_mean(rgb: np.ndarray) -> np.ndarray:
    return to_lab(rgb)[tooth_mask(rgb)].astype(np.float64).mean(axis=0)


def test_identical_cells_need_no_correction() -> None:
    cell = make_scene(1, 1200, 800)

    affine = fit_reinhard(cell, _region(cell), cell, _region(cell))

    np.testing.assert_allclose(affine.gains, 1.0, atol=1e-6)
    np.testing.assert_allclose(affine.offsets, 0.0, atol=1e-4)


def test_tooth_color_difference_drops_by_at_least_60_percent() -> None:  # spec §8.1
    baseline = make_scene(2, 1200, 800)
    other = grade(baseline, gain=0.85, warm=-8.0)

    affine = fit_reinhard(other, _region(other), baseline, _region(baseline))
    fixed = apply_lab_transform(other, affine)

    before = np.linalg.norm(_tooth_mean(other) - _tooth_mean(baseline))
    after = np.linalg.norm(_tooth_mean(fixed) - _tooth_mean(baseline))
    assert after <= 0.4 * before


def test_mean_shift_is_clamped() -> None:
    baseline = make_scene(3, 1200, 800)
    region = _region(baseline)
    far = np.clip(baseline.astype(int) + 90, 0, 255).astype(np.uint8)

    affine = fit_reinhard(baseline, region, far, region)

    # float64 means: a float32 column mean over ~2M pixels drifts by about 0.1.
    before = to_lab(baseline)[region].astype(np.float64)
    moved = affine.apply_lab(before).astype(np.float64).mean(axis=0) - before.mean(axis=0)
    assert abs(moved[0]) <= 0.8 * 12.0 + 1e-3
    assert np.all(np.abs(moved[1:]) <= 0.8 * 8.0 + 1e-3)


def test_affine_params_round_trip() -> None:
    affine = LabAffine((1.1, 0.9, 1.0), (2.0, -3.0, 0.5))

    assert LabAffine.from_params(affine.to_params()) == affine


def test_render_applies_an_affine_but_keeps_the_fill_black() -> None:
    gray = np.full((100, 100, 3), 128, np.uint8)
    shift = tf.to_normalized(tf.translation(50, 0), 100, 100)

    out = render(gray, shift, (100, 100), color=LabAffine((1.0, 1.0, 1.0), (10.0, 0.0, 0.0)))

    assert out[:, :49].max() == 0
    assert out[:, 60:].mean() > 140
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_reinhard.py -v`
Expected: FAIL. `ImportError: cannot import name 'LabAffine'`.

- [ ] **Step 4: Implement `LabAffine` and `fit_reinhard` in `core/color.py`**

The statistics come from `_masked_lab(rgb, mask)`: the Lab pixels inside the mask, or **all**
pixels when the mask covers less than `MIN_STATS_SHARE`. For each channel `c`:

```python
k = clip(sd_t / sd_s, *REINHARD_GAIN_RANGE) if sd_s > 1e-6 else 1.0
shift = clip(mu_t - mu_s, -REINHARD_MEAN_LIMITS[c], REINHARD_MEAN_LIMITS[c])
# Blend the full transfer x' = (x - mu_s) * k + mu_s + shift with identity at the given
# strength; the blend of two affine maps is affine.
gain = 1 - REINHARD_STRENGTH + REINHARD_STRENGTH * k
offset = REINHARD_STRENGTH * (mu_s + shift - k * mu_s)
```

As a consequence, the mean moves by exactly `0.8 * shift`, which is what the clamp test pins.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/unit/test_reinhard.py tests/unit/test_color.py -v`
Expected: all pass (5 new). Then run the quality gates.

```bash
git add src/dental_image_organizer/core/color.py src/dental_image_organizer/core/render.py src/dental_image_organizer/services/procedure.py tests/unit/test_color.py tests/unit/test_render_layout.py tests/unit/test_reinhard.py
git commit -m "feat(core): unify composite colors with a clamped Reinhard transfer"
```

---

### Task 3: The 3 × 3 layout and the composite date

**Files:**

- Modify: `src/dental_image_organizer/core/layout.py`
- Modify: `src/dental_image_organizer/core/naming.py`
- Test: `tests/unit/test_composite_layout.py`

**Interfaces:**

- Consumes: `slots.SLOTS`, `slots.Slot`.
- Produces:
  - `layout.render_composite(cells: Mapping[Slot, RGBImage], cell_size: tuple[int, int], *, gap_px=0, gap_color=(0, 0, 0)) -> RGBImage`
    - Places cells in row-major `SLOTS` order.
    - Missing slots stay `gap_color`.
    - A cell of the wrong size raises `ValueError`.
  - `naming.composite_capture_date(dates: Iterable[date]) -> date`
    - Returns the most frequent date; on a tie, the earliest.
    - An empty input raises `ValueError`.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_composite_layout.py`:

```python
from datetime import date

import numpy as np
import pytest

from dental_image_organizer.core.layout import render_composite
from dental_image_organizer.core.naming import composite_capture_date
from dental_image_organizer.core.slots import SLOTS


def test_composite_places_cells_row_major_with_gaps() -> None:
    cells = {slot: np.full((20, 30, 3), i * 20, np.uint8) for i, slot in enumerate(SLOTS)}

    grid = render_composite(cells, (30, 20), gap_px=2, gap_color=(255, 0, 0))

    assert grid.shape == (64, 94, 3)
    assert grid[0, 0, 0] == 0 and grid[0, 32, 0] == 20 and grid[22, 0, 0] == 60
    assert tuple(grid[0, 30]) == (255, 0, 0)


def test_empty_slots_show_the_gap_color() -> None:
    grid = render_composite({"occlusion_front": np.full((20, 30, 3), 200, np.uint8)}, (30, 20))

    assert grid[30, 45, 0] == 200
    assert grid[:20].max() == 0


def test_wrong_cell_size_is_rejected() -> None:
    with pytest.raises(ValueError):
        render_composite({"upper_right": np.zeros((10, 10, 3), np.uint8)}, (30, 20))


def test_composite_date_is_the_most_frequent_then_earliest() -> None:
    d1, d2 = date(2026, 10, 16), date(2026, 10, 17)

    assert composite_capture_date([d2, d1, d2]) == d2
    assert composite_capture_date([d2, d1]) == d1
```

- [ ] **Step 2: Run the test to verify it fails**

Expected: `ImportError: cannot import name 'render_composite'`.

- [ ] **Step 3: Implement both functions**

- `render_composite`: build the canvas `(3 * ch + 2 * gap, 3 * cw + 2 * gap, 3)`. The cell at
  `divmod(index, 3)` goes to `(row * (ch + gap), col * (cw + gap))`.
- `composite_capture_date`: use `collections.Counter` with `min(counts, key=lambda d: (-counts[d], d))`.

- [ ] **Step 4: Verify and commit**

Run the tests (4 passed), then the gates.

```bash
git add src/dental_image_organizer/core/layout.py src/dental_image_organizer/core/naming.py tests/unit/test_composite_layout.py
git commit -m "feat(core): lay out the 3 x 3 composite and pick its capture date"
```

---

### Task 4: Per-slot geometry: flip, level, and smart crop

**Files:**

- Create: `src/dental_image_organizer/core/geometry.py`
- Create: `tests/support/views.py`
- Test: `tests/unit/test_geometry.py`

**Interfaces:**

- Consumes:
  - `transform` (as `tf`)
  - `imageio.resize_long_edge`
  - `masks.tooth_mask`
  - `color.to_lab`
  - `slots.Slot`, `slots.Flip`, `slots.slot_group`
- Produces, in `core.geometry`:
  - `LevelEstimate(angle_deg: float, confident: bool)`. `angle_deg` is the rotation to apply,
    in the `tf.similarity` convention; it is 0 when not confident.
  - `CellGeometry(transform: tf.Matrix, flip: Flip, level: LevelEstimate)`. The transform maps
    the normalized photo to the cell.
  - `flip_pixels(rgb, flip) -> RGBImage`
  - `flip_matrix(flip, width, height) -> tf.Matrix`. Pixel centers sit on integer coordinates,
    so `x' = width - 1 - x`; this agrees exactly with `flip_pixels`.
  - `estimate_level(rgb, slot) -> LevelEstimate`. The input is already flipped. It logs the
    debug event `level` with `slot`, `angle`, and `confident`.
  - `normalize_view(rgb, slot, flip, cell_size) -> CellGeometry`. It works on any resolution;
    the transform is normalized.
  - Constants:

    | Constant | Value |
    |---|---|
    | `ANALYSIS_LONG_EDGE` | `800` |
    | `MAX_LEVEL_DEG` | `15.0` |
    | `SYMMETRY_STEP_DEG` | `0.5` |
    | `MIN_SYMMETRY_IOU` | `0.5` |
    | `MIN_LINE_COLUMNS` | `0.25` |
    | `MIN_LINE_INLIERS` | `0.6` |
    | `LINE_TOLERANCE` | `0.015` |
    | `RANSAC_TRIALS` | `300` |
    | `CROP_MARGIN` | `0.12` |
    | `CROP_PERCENTILES` | `(2.0, 98.0)` |
    | `MAX_ZOOM` | `1.3` |
    | `COLUMN_STEP` | `2` |

- [ ] **Step 1: Write `tests/support/views.py`**

This builds synthetic upright views at 1800 × 1200 (`VIEW_SIZE`), then rotates them. The tests'
tolerances were measured on exactly these scenes, so copy the module as written:

```python
"""Synthetic intraoral views for the nine composite slots (spec §8.1).

Each view has the layout its leveling rule reads: two arches meeting at a horizontal boundary
(occlusion views), a symmetric U-shaped arch (occlusal views), or one row of teeth with a
straight occlusal edge (buccal views). Left views mirror right views, as in real photos, and
side views carry a perspective size gradient so they are not symmetric.
"""

from __future__ import annotations

import cv2
import numpy as np

from support.synthetic import ENAMEL_RGB, GUM_RGB, _smooth_noise

VIEW_SIZE = (1800, 1200)
PALATE_RGB = np.array([170, 80, 95], np.float32)
DARK_RGB = np.array([60, 25, 30], np.float32)
METAL_RGB = np.array([150, 150, 155], np.float32)


def make_view(
    slot: str, seed: int, *, angle: float = 0.0, size: tuple[int, int] = VIEW_SIZE
) -> np.ndarray:
    """An upright view for `slot`, then rotated by `angle` degrees (tf.similarity convention)."""
    w, h = size
    rng = np.random.default_rng(seed)
    field = sum(a * _smooth_noise(rng, h, w, c) for c, a in ((200, 0.5), (40, 0.3), (8, 0.2)))
    field = (field - field.mean()) / (field.std() + 1e-6)
    img = GUM_RGB + field[..., None] * np.array([16, 10, 10], np.float32)
    teeth = np.zeros((h, w), np.uint8)

    def jitter(spread: float) -> float:
        return float(rng.uniform(-spread, spread))

    if slot.startswith("occlusion"):
        _boundary_rows(teeth, w, h, 0.0 if slot == "occlusion_front" else 0.45, jitter)
        if slot != "occlusion_front":
            img[:, : int(0.12 * w)] = METAL_RGB  # retractor at the far side
        cv2.line(teeth, (0, h // 2), (w, h // 2), 0, max(3, h // 150))
    elif slot.endswith("occlusal"):
        _arch(img, teeth, w, h, jitter)
    else:
        _buccal_row(img, teeth, w, h, jitter)
        if slot.startswith("lower"):
            img, teeth = img[::-1].copy(), teeth[::-1].copy()

    enamel = ENAMEL_RGB + field[..., None] * np.array([9, 9, 11], np.float32)
    img[teeth > 0] = enamel[teeth > 0]
    for _ in range(400):
        # Draw order (center, radius, color) is part of the seeded scene; keep it.
        center = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        radius = int(rng.integers(2, 5))
        color = tuple(float(c) for c in rng.integers(40, 110, 3))
        cv2.circle(img, center, radius, color, -1)
    if slot.endswith("_left"):
        img = img[:, ::-1].copy()
    out = np.clip(img, 0, 255).astype(np.uint8)
    if angle:
        m = cv2.getRotationMatrix2D((w / 2, h / 2), -angle, 1.0)
        out = cv2.warpAffine(out, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return out


def _boundary_rows(teeth, w, h, gradient, jitter) -> None:
    n = 8
    x = 0.1 * w
    widths = np.linspace(1 + gradient, 1 - gradient, n)
    widths = widths / widths.sum() * 0.8 * w
    for i in range(n):
        x0, x1 = int(x), int(x + widths[i] - 0.01 * w)
        top = int(h * (0.27 + 0.03 * abs(i - (n - 1) / 2) / n) + jitter(8))
        bot = int(h * (0.73 - 0.03 * abs(i - (n - 1) / 2) / n) + jitter(8))
        cv2.rectangle(teeth, (x0, top), (x1, h // 2), 255, -1)
        cv2.rectangle(teeth, (x0 + int(0.01 * w), h // 2), (x1 - int(0.01 * w), bot), 255, -1)
        x += widths[i]


def _arch(img, teeth, w, h, jitter) -> None:
    cx, top, a, b = w / 2, 0.15 * h, 0.33 * w, 0.62 * h
    pts = np.array(
        [[cx + a * np.cos(t), top + b * (1 - np.sin(t))] for t in np.linspace(0, np.pi, 200)],
        np.int32,
    )
    cv2.fillPoly(img, [pts], PALATE_RGB.tolist())
    for t in np.linspace(0.08, np.pi - 0.08, 14):
        x, y = cx + a * np.cos(t), top + b * (1 - np.sin(t))
        r = 0.035 * w * (1.6 - 0.9 * np.sin(t))  # molars large, incisors small
        cv2.circle(teeth, (int(x + jitter(4)), int(y + jitter(4))), int(r), 255, -1)


def _buccal_row(img, teeth, w, h, jitter) -> None:
    edge = int(0.56 * h)
    img[edge:] = DARK_RGB
    img[int(0.85 * h) :, : int(0.25 * w)] = METAL_RGB
    widths = np.linspace(1.5, 0.7, 7)
    widths = widths / widths.sum() * 0.78 * w
    x = 0.12 * w
    for i in range(7):
        top = int(h * 0.30 + jitter(25))
        cv2.rectangle(teeth, (int(x), top), (int(x + widths[i] - 0.012 * w), edge), 255, -1)
        x += widths[i]
```

- [ ] **Step 2: Write the failing test**

`tests/unit/test_geometry.py`:

```python
import cv2
import numpy as np
import pytest

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.geometry import (
    MAX_ZOOM, estimate_level, flip_matrix, flip_pixels, normalize_view,
)
from dental_image_organizer.core.masks import tooth_mask
from dental_image_organizer.core.render import coverage_mask, render
from dental_image_organizer.core.slots import DEFAULT_FLIPS, FLIPS, SLOTS, Flip, Slot
from support.views import VIEW_SIZE, make_view

CELL = (1200, 800)


@pytest.mark.parametrize("slot", SLOTS)
@pytest.mark.parametrize("tilt", [-12.0, 7.0])
def test_level_recovers_known_tilt_within_one_degree(slot: Slot, tilt: float) -> None:  # §8.1
    level = estimate_level(make_view(slot, 3, angle=tilt), slot)

    assert level.confident
    assert level.angle_deg == pytest.approx(-tilt, abs=1.0)


def test_occlusion_falls_back_to_the_darkest_row_when_arches_touch() -> None:
    # Blurring closes the thin gap between the arches in the tooth mask.
    blurred = cv2.GaussianBlur(make_view("occlusion_front", 4, angle=6.0), (0, 0), 4)

    assert estimate_level(blurred, "occlusion_front").angle_deg == pytest.approx(-6.0, abs=1.0)


def test_view_without_teeth_is_left_unrotated() -> None:
    gum_only = np.full((800, 1200, 3), (190, 95, 105), np.uint8)

    level = estimate_level(gum_only, "occlusion_front")

    assert (level.angle_deg, level.confident) == (0.0, False)


@pytest.mark.parametrize("flip", FLIPS)
def test_flip_matrix_matches_flip_pixels(flip: Flip) -> None:
    image = np.zeros((40, 60, 3), np.uint8)
    image[5, 10] = 255

    moved = render(image, tf.to_normalized(flip_matrix(flip, 60, 40), 60, 60), (60, 40))

    np.testing.assert_array_equal(moved > 0, flip_pixels(image, flip) > 0)


@pytest.mark.parametrize("slot", SLOTS)
def test_cell_is_level_cropped_and_free_of_black_corners(slot: Slot) -> None:
    photo = make_view(slot, 5, angle=9.0)

    geometry = normalize_view(photo, slot, "none", CELL)
    cell = render(photo, geometry.transform, CELL)

    assert coverage_mask((photo.shape[1], photo.shape[0]), geometry.transform, CELL).all()
    assert abs(estimate_level(cell, slot).angle_deg) <= 0.5
    assert tooth_mask(cell).mean() > 0.1


@pytest.mark.parametrize("slot", SLOTS)
def test_mirror_shot_with_its_default_flip_matches_the_upright_view(slot: Slot) -> None:
    upright = make_view(slot, 6, angle=4.0)
    mirror_shot = flip_pixels(upright, DEFAULT_FLIPS[slot])

    fixed = render(
        mirror_shot, normalize_view(mirror_shot, slot, DEFAULT_FLIPS[slot], CELL).transform, CELL
    )
    expected = render(upright, normalize_view(upright, slot, "none", CELL).transform, CELL)

    assert np.abs(fixed.astype(int) - expected.astype(int)).mean() < 3


def test_zoom_never_exceeds_the_limit_when_few_teeth_show() -> None:
    hidden = make_view("occlusion_front", 7)
    # Hide all but a narrow band of teeth, as retractors and lips sometimes do.
    hidden[:, : VIEW_SIZE[0] * 2 // 5] = (190, 95, 105)
    hidden[:, VIEW_SIZE[0] * 3 // 5 :] = (190, 95, 105)

    geometry = normalize_view(hidden, "occlusion_front", "none", CELL)

    scale = tf.decompose_similarity(tf.to_pixel(geometry.transform, VIEW_SIZE[0], CELL[0])).scale
    assert CELL[0] / scale >= VIEW_SIZE[0] / MAX_ZOOM - 1


def test_portrait_photo_gets_a_landscape_cell() -> None:
    portrait = np.ascontiguousarray(make_view("upper_occlusal", 8).transpose(1, 0, 2))

    geometry = normalize_view(portrait, "upper_occlusal", "none", CELL)

    assert coverage_mask((portrait.shape[1], portrait.shape[0]), geometry.transform, CELL).all()


def test_photo_without_teeth_keeps_the_whole_frame() -> None:  # Review Focus 3
    gum_only = np.full((1200, 1800, 3), (190, 95, 105), np.uint8)

    geometry = normalize_view(gum_only, "occlusion_front", "none", CELL)

    assert not geometry.level.confident
    assert coverage_mask((1800, 1200), geometry.transform, CELL).all()
    scale = tf.decompose_similarity(tf.to_pixel(geometry.transform, 1800, CELL[0])).scale
    assert CELL[0] / scale == pytest.approx(1800, rel=0.01)
```

- [ ] **Step 3: Run the test to verify it fails**

Expected: `ModuleNotFoundError: No module named 'dental_image_organizer.core.geometry'`.

- [ ] **Step 4: Implement `core/geometry.py`**

**`estimate_level`.** Work on the image resized to `ANALYSIS_LONG_EDGE` and its `tooth_mask`.
Choose the rule by `slot_group`. Clamp a found angle to ±`MAX_LEVEL_DEG`.

- **Occlusal groups → `_symmetry_angle(teeth)`.**
  1. Return `None` if the teeth cover less than 1% of the image.
  2. Take the centroid `(cx, cy)` of the mask.
  3. For each `a` in `arange(-15, 15 + 1e-9, 0.5)`:
     - rotate the mask about the centroid with
       `cv2.getRotationMatrix2D((cx, cy), -a, 1)`, using `INTER_NEAREST`;
     - mirror it about the line `x = cx` with the matrix `[[-1, 0, 2 * cx], [0, 1, 0]]`;
     - score the IoU of the rotated mask and its mirror.
  4. Take the best score. If it is below `MIN_SYMMETRY_IOU`, return `None`.
  5. Refine by parabolic interpolation over the two neighbors:
     `offset = 0.5 * (left - right) / (left - 2 * mid + right)` when the denominator is below 0.
  6. Return `angle + offset * step`.
- **Occlusion groups → `_line_angle(_boundary_points(teeth, L), shape)`.**
  `_boundary_points` visits every `COLUMN_STEP`-th column whose tooth pixels number at least
  5% of the height:
  - **Openings exist** (gaps in the tooth rows): use the midpoint of the opening nearest the
    middle of the column's tooth span. Tilted gaps between neighboring teeth also cross a
    column, so taking the largest gap picks the wrong one; measured, it left a third of the
    columns as outliers at 9°.
  - **No opening:** use the darkest row of `GaussianBlur(L, (1, 9))` within the middle 60% of
    the span.
- **Buccal groups → `_line_angle(_edge_points(teeth, edge), shape)`.** Use the bottom tooth
  pixel per column for `upper_buccal` and the top tooth pixel for `lower_buccal`.
- **`_line_angle(points, (h, w))`.**
  1. Return `None` if there are fewer than `MIN_LINE_COLUMNS * w / COLUMN_STEP` points.
  2. Run RANSAC with `np.random.default_rng(0)` (deterministic) for `RANSAC_TRIALS` pairs.
     Inliers are points within `LINE_TOLERANCE * h`.
  3. Return `None` if the inlier share is below `MIN_LINE_INLIERS`.
  4. Fit `np.polyfit` on the inliers and return `-degrees(atan(slope))`.

**`normalize_view(rgb, slot, flip, cell_size)`.** All pixel matrices are in the input's
pixels:

1. `small, scale = resize_long_edge(rgb, ANALYSIS_LONG_EDGE)`, then
   `level = estimate_level(flip_pixels(small, flip), slot)`.
2. `F = flip_matrix(flip, w, h)` and `R` = rotation by `level.angle_deg` about `(w / 2, h / 2)`.
3. Locate the teeth on the *leveled* analysis copy. Warp the flipped small image with the same
   rotation about its own center, using `BORDER_REPLICATE`, and take its `tooth_mask`.
4. `valid = _valid_rect(angle, w, h, aspect)`: the largest centered rectangle of the cell aspect
   whose four corners, mapped through `R⁻¹`, stay inside the photo. Find it by bisection over the
   scale of the aspect-fit rectangle (`_fit_aspect`) for 30 iterations.
5. `crop = _smart_crop(teeth, 1 / scale, w, h, aspect, valid)`:
   1. If the teeth cover less than 1%, return `valid`.
   2. Take the 2nd–98th percentile box of the mask (×`1 / scale`) and expand it by 12% per
      side.
   3. Widen or heighten it to the cell aspect.
   4. Raise the width to at least `_fit_aspect(w, h, aspect)[0] / MAX_ZOOM`.
   5. Cap the width at `valid`'s width. No black corners wins over the zoom limit.
   6. Clamp the center so the crop stays inside `valid`.
6. Compute `to_cell = scaling(cell_w / crop_w) @ translation(-crop_x, -crop_y)`. Return
   `CellGeometry(tf.to_normalized(to_cell @ R @ F, w, cell_w), flip, level)`.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/unit/test_geometry.py -v`
Expected: 45 passed in about 10 s. Then run the gates.

```bash
git add src/dental_image_organizer/core/geometry.py tests/support/views.py tests/unit/test_geometry.py
git commit -m "feat(core): flip, level, and smart-crop composite cells"
```

---

### Task 5: Embeddings and the fake test model

**Files:**

- Create: `src/dental_image_organizer/ml/__init__.py` (docstring only)
- Create: `src/dental_image_organizer/ml/embedder.py`
- Create: `tests/support/fake_model.py`
- Test: `tests/unit/test_embedder.py`

**Interfaces:**

- Consumes: `imageio.RGBImage`, `errors.ModelUnavailableError`.
- Produces, in `ml.embedder`:
  - Constants:

    | Constant | Value |
    |---|---|
    | `INPUT_NAME` | `"pixels"` |
    | `OUTPUT_NAME` | `"embedding"` |
    | `INPUT_SIZE` | `(336, 224)` |
    | `BLOCKS` | `7` |
    | `BLOCK_DIM` | `384` |
    | `EMBEDDING_DIM` | `2688` |
    | `PHOTOS_PER_BATCH` | `9` |
    | `IMAGENET_MEAN` | `(0.485, 0.456, 0.406)` |
    | `IMAGENET_STD` | `(0.229, 0.224, 0.225)` |

  - `Embeddings = NDArray[float32]`
  - `EmbeddingPair(original: Embeddings, mirrored: Embeddings)` (frozen) with `__len__` and
    `take(indices) -> EmbeddingPair`. `mirrored` is the embedding of the horizontally mirrored
    *network input*.
  - `preprocess(rgb) -> NDArray[float32]` of shape (3, 224, 336):
    - portrait images are rotated `cv2.ROTATE_90_COUNTERCLOCKWISE`;
    - resized with `INTER_AREA` to `INPUT_SIZE`;
    - scaled to [0, 1], then ImageNet-normalized.
  - `normalize_blocks(raw) -> Embeddings`: L2-normalizes each 384-block and returns float32.
  - `Embedder(model_path: Path, model_hash: str)`:
    - opens an `onnxruntime.InferenceSession` with `CPUExecutionProvider`;
    - raises `ModelUnavailableError` if the session fails or the input and output names differ
      from `["pixels"]` and `["embedding"]`;
    - has the attribute `.model_hash`;
    - `.embed(images) -> EmbeddingPair`: runs chunks of 9 photos, stacked with their mirrors
      (`batch[..., ::-1]`) into one run of 18 (spec §4.6).
- Test support: `support.fake_model.build_fake_model(path, seed=0) -> str` writes a valid ONNX
  model with the same interface and returns its SHA-256. The graph is: `AveragePool` 56 × 56
  (stride 56) → `Flatten` → `MatMul` with seeded (72, 2688) weights. This keeps a coarse 4 × 6
  layout, so mirroring changes the output.

- [ ] **Step 1: Write `tests/support/fake_model.py`**

Build the graph with `onnx.helper.make_node` / `make_graph` / `make_model`, using opset 17 and
`ir_version = 8`. Check it with `onnx.checker.check_model`. Declare the input
`("pixels", FLOAT, ["batch", 3, 224, 336])` and the output
`("embedding", FLOAT, ["batch", 2688])`, with the weights as an initializer from
`np.random.default_rng(seed).standard_normal((72, 2688))`.

- [ ] **Step 2: Write the failing test**

`tests/unit/test_embedder.py`:

```python
from pathlib import Path

import numpy as np
import pytest

from dental_image_organizer.core.slots import SLOTS
from dental_image_organizer.errors import ModelUnavailableError
from dental_image_organizer.ml.embedder import (
    BLOCK_DIM, EMBEDDING_DIM, Embedder, normalize_blocks, preprocess,
)
from support.fake_model import build_fake_model
from support.views import make_view


@pytest.fixture(scope="module")
def fake_model(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    path = tmp_path_factory.mktemp("model") / "fake.onnx"
    return path, build_fake_model(path)


def test_preprocess_makes_a_normalized_landscape_tensor() -> None:
    portrait = np.zeros((300, 200, 3), np.uint8)
    portrait[:10, :] = 255  # top rows white

    x = preprocess(portrait)

    assert x.shape == (3, 224, 336)
    # Rotated counterclockwise, the top edge becomes the left edge.
    assert x[0, :, :5].mean() == pytest.approx((1.0 - 0.485) / 0.229, abs=0.05)
    assert x[0, :, -5:].mean() == pytest.approx((0.0 - 0.485) / 0.229, abs=0.05)


def test_each_block_is_unit_length() -> None:
    raw = np.random.default_rng(0).standard_normal((4, EMBEDDING_DIM))

    norms = np.linalg.norm(normalize_blocks(raw).reshape(4, -1, BLOCK_DIM), axis=2)

    np.testing.assert_allclose(norms, 1.0, rtol=1e-5)


def test_mirror_embedding_equals_embedding_of_the_mirrored_photo(
    fake_model: tuple[Path, str],
) -> None:
    embedder = Embedder(*fake_model)
    photos = [make_view(s, 1) for s in SLOTS] + [make_view("upper_right", 2), make_view("lower_left", 2)]

    pair = embedder.embed(photos)  # 11 photos: more than one batch
    mirrored = embedder.embed([np.ascontiguousarray(p[:, ::-1]) for p in photos])

    assert pair.original.shape == pair.mirrored.shape == (11, EMBEDDING_DIM)
    np.testing.assert_allclose(pair.mirrored, mirrored.original, atol=1e-4)


def test_a_file_that_is_not_the_model_is_unavailable(tmp_path: Path) -> None:
    bad = tmp_path / "bad.onnx"
    bad.write_bytes(b"not an onnx model")

    with pytest.raises(ModelUnavailableError):
        Embedder(bad, "0" * 64)
```

- [ ] **Step 3: Run the test to verify it fails**

Expected: `ModuleNotFoundError: No module named 'dental_image_organizer.ml'`.

- [ ] **Step 4: Implement `ml/embedder.py`**

The signatures and tests above determine it. Catch `Exception` from `InferenceSession(...)`,
because onnxruntime raises its own types; re-raise it as
`ModelUnavailableError("Cannot load the AI model: …")`. A run that returns a shape other than
`(n, 2688)` also raises `ModelUnavailableError`.

- [ ] **Step 5: Verify and commit**

Expected: 4 passed. Then run the gates.

```bash
git add src/dental_image_organizer/ml tests/support/fake_model.py tests/unit/test_embedder.py
git commit -m "feat(ml): embed photos and their mirrors with an ONNX DINOv2 model"
```

---

### Task 6: The pinned model, its cache, and the consented download

**Files:**

- Create: `src/dental_image_organizer/ml/model_store.py`
- Test: `tests/unit/test_model_store.py`

**Interfaces:**

- Consumes: `errors.ModelUnavailableError`, `errors.ModelIntegrityError`.
- Produces, in `ml.model_store`:
  - `ModelSpec(filename: str, url: str, sha256: str, size_bytes: int)` (frozen)
  - `DEFAULT_MODEL = ModelSpec("dinov2s_pooled_int8.onnx", "https://github.com/jimwellh/Dental-Image-Organizer/releases/download/model-dinov2s-v1/dinov2s_pooled_int8.onnx", "da14f66b5e204cce7c8f2b65fd2936b02d5f4cc015687f05465de32717ecc5b0", 24_421_288)`.
    These are the values the verified export produced. Task 12 confirms or replaces them.
  - `sha256_file(path) -> str`
  - `cached_model(spec, cache_dir) -> Path | None`. It returns the verified file. A file with
    a wrong hash is deleted, and the function returns `None`.
  - `download_model(spec, cache_dir, *, client: httpx.Client | None = None, progress: Callable[[int, int], None] | None = None) -> Path`
    - Stream to `<filename>.part` while hashing.
    - Verify the SHA-256 **and** the size, then `os.replace` to the final name.
    - `httpx.HTTPError` → delete the partial file and raise `ModelUnavailableError`.
    - A mismatch → delete it and raise `ModelIntegrityError`.
    - A default client is created with `follow_redirects=True` and `timeout=60` and closed
      afterwards. GitHub redirects release assets to its CDN.
    - The function never logs the URL with any user data.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_model_store.py`:

```python
import hashlib
from pathlib import Path

import httpx
import pytest

from dental_image_organizer.errors import ModelIntegrityError, ModelUnavailableError
from dental_image_organizer.ml.model_store import (
    DEFAULT_MODEL, ModelSpec, cached_model, download_model, sha256_file,
)


def _spec(data: bytes) -> ModelSpec:
    return ModelSpec("m.onnx", "https://example.test/m.onnx", hashlib.sha256(data).hexdigest(), len(data))


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_default_model_is_pinned_to_a_github_release() -> None:
    assert DEFAULT_MODEL.url.startswith("https://github.com/jimwellh/Dental-Image-Organizer/releases/")
    assert len(DEFAULT_MODEL.sha256) == 64 and DEFAULT_MODEL.size_bytes > 20_000_000


def test_download_verifies_reports_progress_and_caches(tmp_path: Path) -> None:
    data = b"model bytes" * 1000
    seen: list[tuple[int, int]] = []

    path = download_model(
        _spec(data), tmp_path, client=_client(lambda r: httpx.Response(200, content=data)),
        progress=lambda done, total: seen.append((done, total)),
    )

    assert path.read_bytes() == data
    assert seen[-1] == (len(data), len(data))
    assert cached_model(_spec(data), tmp_path) == path


def test_download_follows_the_release_redirect(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.test":
            return httpx.Response(302, headers={"location": "https://cdn.example.test/m"})
        return httpx.Response(200, content=b"x" * 10)

    assert download_model(_spec(b"x" * 10), tmp_path, client=_client(handler)).exists()


def test_tampered_download_is_rejected_and_removed(tmp_path: Path) -> None:
    with pytest.raises(ModelIntegrityError):
        download_model(_spec(b"good"), tmp_path, client=_client(lambda r: httpx.Response(200, content=b"evil")))

    assert list(tmp_path.iterdir()) == []


def test_http_error_is_unavailable_and_leaves_nothing(tmp_path: Path) -> None:
    with pytest.raises(ModelUnavailableError):
        download_model(_spec(b"good"), tmp_path, client=_client(lambda r: httpx.Response(404)))

    assert list(tmp_path.iterdir()) == []


def test_cached_file_with_a_wrong_hash_is_deleted(tmp_path: Path) -> None:
    (tmp_path / "m.onnx").write_bytes(b"stale")

    assert cached_model(_spec(b"fresh"), tmp_path) is None
    assert not (tmp_path / "m.onnx").exists()


def test_sha256_file(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"abc")

    assert sha256_file(tmp_path / "f") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Expected: `ModuleNotFoundError … ml.model_store`.

- [ ] **Step 3: Implement `ml/model_store.py`**

Use `CHUNK_BYTES = 1 << 20` and `TIMEOUT_SECONDS = 60.0`. Stream with
`http.stream("GET", spec.url)`, call `raise_for_status()`, then iterate `iter_bytes(CHUNK_BYTES)`.

- [ ] **Step 4: Verify and commit**

Expected: 7 passed. Then run the gates.

```bash
git add src/dental_image_organizer/ml/model_store.py tests/unit/test_model_store.py
git commit -m "feat(ml): pin the embedding model and download it only with verification"
```

---

### Task 7: The sample store and the two-head classifier

**Files:**

- Create: `src/dental_image_organizer/ml/samples.py`
- Create: `src/dental_image_organizer/ml/classifier.py`
- Create: `tests/support/embeddings.py`
- Test: `tests/unit/test_samples.py`
- Test: `tests/unit/test_classifier.py`

**Interfaces:**

- Consumes:
  - `slots.SLOTS`, `slots.Slot`, `slots.SlotGroup`, `slots.slot_group`, `slots.slot_side`
  - `embedder.EmbeddingPair`, `embedder.EMBEDDING_DIM`
- Produces:
  - `ml.samples`:
    - `SampleSource = Literal["export", "teach"]`
    - `SAMPLES_FILE = "samples.npz"`
    - `SampleSet(pairs: EmbeddingPair, slots: tuple[Slot, ...], case_ids: tuple[str, ...], sources: tuple[SampleSource, ...], timestamps: NDArray[float64], model_hashes: tuple[str, ...])`
      with `.for_model(model_hash) -> SampleSet` and `.counts() -> dict[Slot, int]`
    - `empty_samples() -> SampleSet`
    - `SampleStore(directory)`:
      - `.path = directory / SAMPLES_FILE`
      - `.load() -> SampleSet`, which returns an empty set when the file is missing
      - `.append(pairs, slots, *, case_id, source, model_hash, timestamp) -> SampleSet`, which
        returns the merged set
    - Storage details (spec §4.6):
      - embeddings stored as **float16**;
      - strings stored as `dtype=str` arrays;
      - loaded with `allow_pickle=False`;
      - written atomically (`samples.npz.tmp` + `os.replace`).
  - `ml.classifier`:
    - Constants: `MIN_SAMPLES_PER_SLOT = 3`, `CLASSIFIER_C = 10.0` (spec amendment 1),
      `MAX_ITERATIONS = 2000`, and
      `LATERAL_GROUPS = ("upper_buccal", "occlusion_side", "lower_buccal")`.
    - `SlotClassifier(model_hash, group_classes: tuple[SlotGroup, ...], group_coef (G, D), group_intercept (G,), side_coef (D,))`
      with:
      - `.side_scores(pair) -> NDArray` (N,);
      - `.slot_probabilities(pair) -> NDArray` (N, 9), in `SLOTS` order;
      - `.save(path)`, which writes JSON atomically.
    - `load_classifier(path, model_hash) -> SlotClassifier | None`. It returns `None` when the
      file is missing or its `model_hash` differs.
    - `train_classifier(pair, slots, model_hash, *, regularization=CLASSIFIER_C) -> SlotClassifier | None`.
      It returns `None` until every slot has ≥ `MIN_SAMPLES_PER_SLOT` samples.
- Test support: `support.embeddings.synthetic_pairs(per_slot, seed) -> tuple[EmbeddingPair, list[Slot]]`.
  - Use group centers and one side axis, drawn from `default_rng(100)`.
  - Each sample is `base = center + 0.3 · noise(seed)`.
  - `original = base + sign · axis` and `mirrored = base - sign · axis`, where sign is +1 for
    right, -1 for left, and 0 for central slots.
  - Both are passed through `normalize_blocks`.

Classifier math (spec §4.6, with features `s = (g + ḡ) / 2` and `a = g - ḡ` in float64):

- **Group head.**
  `LogisticRegression(C=regularization, class_weight="balanced", max_iter=MAX_ITERATIONS)`
  fitted on `s` with group labels. Store `coef_`, `intercept_`, and `classes_`.
- **Side head.** Fitted on the lateral samples only, with features `a` and side labels, **plus**
  every sample's mirror `-a` with the opposite label.
  `LogisticRegression(C=regularization, fit_intercept=False, max_iter=MAX_ITERATIONS)`. Store
  `side_coef = coef_[0]` if `classes_[1] == "right"`, else `-coef_[0]`, so a positive score
  means right.
- **Prediction.**
  - `p(group)` = softmax(`s @ coef.T + intercept`), with max-subtraction for stability.
  - `p(right) = sigmoid(a @ side_coef)`.
  - Central slot: `p(group)`. Right slot: `p(group) · p(right)`. Left slot:
    `p(group) · (1 - p(right))`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_classifier.py`:

```python
from pathlib import Path

import numpy as np

from dental_image_organizer.core.slots import SLOTS
from dental_image_organizer.ml.classifier import load_classifier, train_classifier
from dental_image_organizer.ml.embedder import EmbeddingPair
from support.embeddings import synthetic_pairs


def _trained():
    train, slots = synthetic_pairs(3, 1)
    classifier = train_classifier(train, slots, "h")
    assert classifier is not None
    return classifier, train, slots


def test_untrained_until_every_slot_has_three_samples() -> None:
    pair, slots = synthetic_pairs(3, 1)

    assert train_classifier(pair.take(range(1, len(slots))), slots[1:], "h") is None
    assert train_classifier(pair, slots, "h") is not None


def test_three_samples_per_slot_classify_new_photos() -> None:
    classifier, _, _ = _trained()
    test, test_slots = synthetic_pairs(5, 2)

    predicted = [SLOTS[i] for i in classifier.slot_probabilities(test).argmax(axis=1)]

    assert predicted == test_slots


def test_side_score_is_exactly_antisymmetric_under_mirroring() -> None:  # spec §8.1
    classifier, train, _ = _trained()

    swapped = EmbeddingPair(train.mirrored, train.original)

    np.testing.assert_allclose(classifier.side_scores(swapped), -classifier.side_scores(train))


def test_mirror_images_land_in_the_same_group_on_opposite_sides() -> None:  # spec §8.1
    classifier, train, slots = _trained()
    right = train.take([slots.index("upper_right")])

    as_is = SLOTS[int(classifier.slot_probabilities(right).argmax())]
    mirrored = SLOTS[int(classifier.slot_probabilities(EmbeddingPair(right.mirrored, right.original)).argmax())]

    assert (as_is, mirrored) == ("upper_right", "upper_left")


def test_classifier_round_trips_and_ignores_another_model(tmp_path: Path) -> None:  # §8.1
    classifier, train, _ = _trained()
    classifier.save(tmp_path / "model.json")

    loaded = load_classifier(tmp_path / "model.json", "h")

    assert loaded is not None
    np.testing.assert_allclose(loaded.slot_probabilities(train), classifier.slot_probabilities(train))
    assert load_classifier(tmp_path / "model.json", "other-model") is None
    assert load_classifier(tmp_path / "missing.json", "h") is None
```

`tests/unit/test_samples.py`:

```python
from pathlib import Path

import numpy as np

from dental_image_organizer.core.slots import SLOTS
from dental_image_organizer.ml.samples import SampleStore
from support.embeddings import synthetic_pairs


def test_samples_accumulate_and_filter_by_model(tmp_path: Path) -> None:
    pair, slots = synthetic_pairs(1, 3)
    store = SampleStore(tmp_path)

    store.append(pair, slots, case_id="a", source="teach", model_hash="h1", timestamp=1.0)
    merged = store.append(pair, slots, case_id="b", source="export", model_hash="h2", timestamp=2.0)

    assert len(merged.slots) == 18
    assert store.load().for_model("h1").counts() == dict.fromkeys(SLOTS, 1)
    np.testing.assert_allclose(store.load().pairs.original[:9], pair.original, atol=2e-3)  # fp16
    assert [p.name for p in tmp_path.iterdir()] == ["samples.npz"]  # no temp file left


def test_missing_store_loads_empty(tmp_path: Path) -> None:
    assert SampleStore(tmp_path).load().counts() == dict.fromkeys(SLOTS, 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Expected: `ModuleNotFoundError … ml.classifier` / `ml.samples` (and `support.embeddings`).

- [ ] **Step 3: Implement `support/embeddings.py`, `ml/samples.py`, and `ml/classifier.py`**

These follow the Interfaces block above.

- [ ] **Step 4: Verify and commit**

Expected: 7 passed. Then run the gates.

```bash
git add src/dental_image_organizer/ml/samples.py src/dental_image_organizer/ml/classifier.py tests/support/embeddings.py tests/unit/test_samples.py tests/unit/test_classifier.py
git commit -m "feat(ml): learn slots with a view-group head and a mirror-antisymmetric side head"
```

---

### Task 8: Slot assignment

**Files:**

- Create: `src/dental_image_organizer/ml/assignment.py`
- Test: `tests/unit/test_assignment.py`

**Interfaces:**

- Consumes: `slots.SLOTS`, `slots.Slot`.
- Produces, in `ml.assignment`:
  - `LOW_CONFIDENCE = 0.5`
  - `AssignMethod = Literal["classifier", "order"]`
  - `Assignment(slots: tuple[Slot | None, ...], confidences: tuple[float | None, ...], method)`.
    Index `i` is photo `i`, and `None` means the tray. Properties:
    - `.slot_map -> dict[Slot, int]`;
    - `.low_confidence -> frozenset[Slot]`: the slots whose confidence is below 0.5.
  - `assign_by_classifier(probabilities (N, 9)) -> Assignment`. It solves
    `scipy.optimize.linear_sum_assignment(-log(p + 1e-6))`. Rows left unassigned (N > 9) get
    `None`.
  - `assign_by_order(capture_times, order) -> Assignment`
    - `ValueError` unless `order` is a permutation of the nine slots.
    - Photos are ranked by `(time.replace(tzinfo=None), index)`.
    - The first nine fill `order` in sequence; the rest go to the tray.
    - Confidences are all `None`.
  - `with_overrides(assignment, overrides: Mapping[int, Slot | None]) -> Assignment`. It applies
    drag-to-swap semantics:
    - the photo that held the target slot takes the mover's previous slot, or the tray if the
      mover came from the tray;
    - `None` sends a photo to the tray;
    - moved photos get confidence `None`;
    - duplicate targets raise `ValueError`;
    - `method` is unchanged.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_assignment.py`:

```python
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from dental_image_organizer.core.slots import SLOTS
from dental_image_organizer.ml.assignment import assign_by_classifier, assign_by_order, with_overrides

NINE = [datetime(2026, 1, 1, 9, 0, s) for s in range(9)]


def test_assignment_is_globally_optimal_not_greedy() -> None:
    probs = np.full((9, 9), 0.01)
    np.fill_diagonal(probs, 0.6)
    probs[0, 0], probs[0, 1], probs[1, 0], probs[1, 1] = 0.9, 0.85, 0.8, 0.1

    assert assign_by_classifier(probs).slots[:2] == (SLOTS[1], SLOTS[0])  # greedy gives 0 -> 0


def test_extra_photos_go_to_the_tray_and_low_confidence_is_flagged() -> None:  # Review Focus 4
    probs = np.full((10, 9), 0.01)
    probs[:9, :9] += np.eye(9) * 0.9
    probs[4, 4] = 0.3

    assignment = assign_by_classifier(probs)

    assert assignment.slots[9] is None
    assert assignment.low_confidence == frozenset({SLOTS[4]})


def test_order_fallback_sorts_by_time_and_fills_the_order() -> None:
    t0 = datetime(2026, 10, 16, 9, 0)
    times = [t0 + timedelta(seconds=s) for s in (30, 10, 20)]
    times[0] = times[0].replace(tzinfo=timezone(timedelta(hours=8)))  # mixed aware and naive

    assignment = assign_by_order(times, SLOTS)

    assert assignment.slots == (SLOTS[2], SLOTS[0], SLOTS[1])
    assert assignment.method == "order"


def test_order_must_list_every_slot_once() -> None:
    with pytest.raises(ValueError):
        assign_by_order(NINE[:1], SLOTS[:8] + SLOTS[:1])


def test_override_swaps_like_a_drag() -> None:
    base = assign_by_order([*NINE, datetime(2026, 1, 1, 9, 0, 30)], SLOTS)

    moved = with_overrides(base, {0: "upper_left"})
    from_tray = with_overrides(base, {9: "occlusion_front", 1: None})

    assert moved.slots[:3] == ("upper_left", "upper_occlusal", "upper_right")
    assert from_tray.slots[9] == "occlusion_front"
    assert from_tray.slots[4] is None and from_tray.slots[1] is None


def test_two_overrides_to_one_slot_are_rejected() -> None:
    with pytest.raises(ValueError):
        with_overrides(assign_by_order(NINE, SLOTS), {0: "upper_left", 1: "upper_left"})
```

- [ ] **Step 2: Run the test to verify it fails**

Expected: `ModuleNotFoundError … ml.assignment`.

- [ ] **Step 3: Implement `ml/assignment.py`**

It follows the Interfaces block above.

- [ ] **Step 4: Verify and commit**

Expected: 6 passed. Then run the gates.

```bash
git add src/dental_image_organizer/ml/assignment.py tests/unit/test_assignment.py
git commit -m "feat(ml): assign photos to slots by classifier, shooting order, or hand"
```

---

### Task 9: The composite service

**Files:**

- Create: `src/dental_image_organizer/services/composite.py`
- Test: `tests/unit/test_composite_service.py`

**Interfaces:**

- Consumes:
  - `geometry.normalize_view`, `geometry.CellGeometry`, `geometry.flip_pixels` (in tests)
  - `color.fit_reinhard`, `color.LabAffine`
  - `masks.tooth_mask`, `masks.gingiva_mask`
  - `render.render`
  - `layout.render_composite`
  - `assignment.*`
  - `classifier.SlotClassifier`
  - `embedder.EmbeddingPair`
- Produces, in `services.composite`:
  - Constants:

    | Constant | Value |
    |---|---|
    | `DEFAULT_CELL_SIZE` | `(1200, 800)` |
    | `DEFAULT_GAP_PX` | `0` |
    | `DEFAULT_GAP_COLOR` | `(0, 0, 0)` |
    | `DEFAULT_BASELINE` | `"occlusion_front"` |
    | `COLOR_ANALYSIS_WIDTH` | `360` |

  - `CellPlan(slot, photo: int, geometry: CellGeometry, color: LabAffine | None, confidence: float | None)`.
    `color` is `None` for the baseline.
  - `CompositePlan(cells: dict[Slot, CellPlan] (in SLOTS order), tray: tuple[int, ...], method, low_confidence, baseline: Slot | None)`
  - `assign_photos(capture_times, order, *, classifier=None, embeddings=None) -> Assignment`.
    It uses the classifier only when **both** a classifier and embeddings are given; otherwise
    it uses shooting order.
  - `plan_composite(images, assignment, *, flips, baseline=DEFAULT_BASELINE, cell_size=DEFAULT_CELL_SIZE) -> CompositePlan`
    1. For each placed photo, run `normalize_view` inside
       `bound_contextvars(photo=index + 1)`. core/ never sees file names.
    2. The baseline is the requested slot if it is filled, else the first filled slot in
       `SLOTS` order.
    3. For colors, render every cell at `COLOR_ANALYSIS_WIDTH` × the matching height with
       `INTER_AREA`. Each non-baseline cell gets
       `fit_reinhard(cell, tooth ∪ gingiva, baseline_cell, baseline_region)`.
  - `render_cell(image, cell, cell_size) -> RGBImage`: `render(..., color=cell.color)`.
  - `compose(cells, cell_size=DEFAULT_CELL_SIZE, *, gap_px, gap_color) -> RGBImage`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_composite_service.py`:

```python
from datetime import datetime, timedelta

import numpy as np
import pytest

from dental_image_organizer.core.geometry import flip_pixels
from dental_image_organizer.core.slots import DEFAULT_FLIPS, GROUPS, SLOTS
from dental_image_organizer.ml.assignment import assign_by_order
from dental_image_organizer.ml.classifier import SlotClassifier
from dental_image_organizer.ml.embedder import EMBEDDING_DIM, EmbeddingPair
from dental_image_organizer.services.composite import assign_photos, compose, plan_composite, render_cell
from support.synthetic import grade
from support.views import make_view

TIMES = [datetime(2026, 10, 16, 9) + timedelta(seconds=10 * i) for i in range(10)]


@pytest.fixture(scope="module")
def raw_set() -> list[np.ndarray]:
    """Nine photos as the camera stored them: mirror shots flipped, colors varying."""
    rng = np.random.default_rng(5)
    photos = []
    for i, slot in enumerate(SLOTS):
        view = make_view(slot, 50 + i, angle=float(rng.uniform(-8, 8)))
        view = grade(view, gain=float(rng.uniform(0.85, 1.1)), warm=float(rng.uniform(-8, 8)))
        photos.append(flip_pixels(view, DEFAULT_FLIPS[slot]))
    return photos


def test_plan_places_levels_and_colors_every_slot(raw_set: list[np.ndarray]) -> None:
    plan = plan_composite(raw_set, assign_by_order(TIMES[:9], SLOTS), flips=DEFAULT_FLIPS)

    assert list(plan.cells) == list(SLOTS)
    assert plan.baseline == "occlusion_front" and plan.cells["occlusion_front"].color is None
    assert all(plan.cells[s].color is not None for s in SLOTS if s != "occlusion_front")
    assert all(plan.cells[s].geometry.level.confident for s in SLOTS)
    assert plan.method == "order" and plan.tray == ()


def test_missing_baseline_falls_back_to_the_first_filled_slot(raw_set: list[np.ndarray]) -> None:
    without_front = raw_set[:4] + raw_set[5:]
    order = SLOTS[:4] + SLOTS[5:] + SLOTS[4:5]

    plan = plan_composite(without_front, assign_by_order(TIMES[:8], order), flips=DEFAULT_FLIPS)

    assert plan.baseline == "upper_right" and "occlusion_front" not in plan.cells


def test_a_tenth_photo_goes_to_the_tray(raw_set: list[np.ndarray]) -> None:
    plan = plan_composite(raw_set + raw_set[:1], assign_by_order(TIMES, SLOTS), flips=DEFAULT_FLIPS)

    assert plan.tray == (9,)


def test_composite_is_three_by_three_cells(raw_set: list[np.ndarray]) -> None:
    plan = plan_composite(raw_set, assign_by_order(TIMES[:9], SLOTS), flips=DEFAULT_FLIPS)

    grid = compose({s: render_cell(raw_set[c.photo], c, (1200, 800)) for s, c in plan.cells.items()})

    assert grid.shape == (2400, 3600, 3)


def test_classifier_is_used_only_with_embeddings() -> None:
    d = EMBEDDING_DIM
    classifier = SlotClassifier("h", GROUPS, np.zeros((6, d)), np.zeros(6), np.zeros(d))
    pair = EmbeddingPair(np.zeros((1, d), np.float32), np.zeros((1, d), np.float32))

    assert assign_photos(TIMES[:1], SLOTS, classifier=classifier).method == "order"
    assert assign_photos(TIMES[:1], SLOTS, classifier=classifier, embeddings=pair).method == "classifier"
```

- [ ] **Step 2: Run the test to verify it fails**

Expected: `ModuleNotFoundError … services.composite`.

- [ ] **Step 3: Implement `services/composite.py`**

It follows the Interfaces block above.

- [ ] **Step 4: Verify and commit**

Expected: 5 passed in about 10 s. Then run the gates.

```bash
git add src/dental_image_organizer/services/composite.py tests/unit/test_composite_service.py
git commit -m "feat(services): plan and render a composite from assigned photos"
```

---

### Task 10: `dio composite`

**Files:**

- Create: `src/dental_image_organizer/services/batch_io.py`
- Create: `src/dental_image_organizer/services/headless_composite.py`
- Modify: `src/dental_image_organizer/services/headless.py` (use `batch_io`)
- Modify: `src/dental_image_organizer/cli.py`
- Modify: `README.md`
- Modify: `tests/integration/test_cli_process.py` (monkeypatch target)
- Test: `tests/integration/test_cli_composite.py`

**Interfaces:**

- Consumes: Tasks 1–9, plus `naming.composite_filename`, `naming.sanitize_component`,
  `imageio.encode_jpeg`, `imageio.resize_long_edge`, and `headless.PhotoOutcome`.
- Produces:
  - `services.batch_io`. Move these out of `headless.py` unchanged, renamed as public names:

    | Before (in `headless.py`) | After (in `batch_io.py`) |
    |---|---|
    | `_load` | `load_photo` |
    | `_os_error_message` | `os_error_message` |
    | `_unexpected_message` | `unexpected_message` |
    | `_captured_at` | `capture_time` |
    | `_write` | `write_unique` |
    | `MTIME_WARNING` | `MTIME_WARNING` |

    `headless.py` imports them. In `test_cli_process.py`, the `decode` monkeypatch now targets
    `batch_io`: `real_decode = batch_io.decode` and `monkeypatch.setattr(batch_io, "decode", …)`.
  - `services.headless_composite`:
    - `CompositeOptions(order=SLOTS, flips=DEFAULT_FLIPS copy, baseline=DEFAULT_BASELINE, assign: Mapping[str, Slot | None] = {}, learn=False, download_model=False)`.
      The keys of `assign` are input **file names**; a `None` value means the tray.
    - `CellOutcome(slot, source: Path, confidence: float | None, level_deg: float, leveled: bool, flip: Flip)`
    - `CompositeReport(output: Path, cells: tuple[CellOutcome, ...], tray: tuple[Path, ...], problems: tuple[PhotoOutcome, ...], method, low_confidence, notes: tuple[str, ...], learned: int, trained: bool)`
      with property `.failed`.
    - `run_composite(photo_paths, out_dir, *, patient, tag, options=None, today=None, notify: Callable[[str], None] = no-op) -> CompositeReport`
    - Constants: `WORKING_LONG_EDGE = 1600`, `CLASSIFIER_FILE = "model.json"`.
  - The CLI command `composite`, with the options of spec amendment 2.

**Exact user-facing copy** (the tests match it):

- `ORDER_NOTE = "Slots were assigned by shooting order: the AI classifier needs 3 confirmed samples per slot. Check the result, fix it with --assign, and rerun with --learn to teach it."`
- `NO_MODEL_NOTE = "The AI classifier is trained, but the AI model is not downloaded; slots were assigned by shooting order. Rerun with --download-model."`
- `DOWNLOAD_FAILED_NOTE = "Slots were assigned by shooting order."` A failed download appends
  `f"{exc} {DOWNLOAD_FAILED_NOTE}"`.
- `STALE_MODEL_NOTE = "The AI model changed since the classifier was trained, so its samples no longer apply; confirm 3 sets again with --learn. Slots were assigned by shooting order."`
  It is appended when `model.json` exists but `load_classifier` returns `None` (another model's
  hash, spec §4.6).
- The announcement before downloading: `f"Downloading the AI model ({size_bytes // 1_000_000} MB) from github.com ..."`.
- A `--learn` without the model raises
  `ModelUnavailableError("--learn needs the AI model (downloaded once from github.com); rerun with --download-model.")`.
- No decodable photo raises `DioError("No usable photos to compose.")`.
- After learning: `f"Learned {n} samples; the AI classifier is {'trained' if trained else 'not trained yet'}."`
- CLI lines:

  | Line | Format |
  |---|---|
  | Cell | `f"{slot:<16} {conf}  {file}  {level}  flip {flip}"`, where `conf` is `?0.42` for a low-confidence slot, ` 0.93` otherwise, and five spaces when there is no confidence; `level` is `level +2.1 deg`, or `level -` when not confident |
  | Tray | `f"{'tray':<16} {'':>5}  {file}"` |
  | Failed | `f"{'failed':<16} {'':>5}  {file}: {message}"` |
  | Output | `f"{'composite':<16} {'':>5}  -> {name}"` |

  Then come the notes, the learning line, and the skip summary, which reuses `dio process`'s
  `Skipped N files: …` wording. Factor out `_print_skips` from `_print_report`.

**`run_composite` flow.** Every step is decided here:

1. **Validate before any work.**
   - `sanitize_component(patient)` and `sanitize_component(tag)`.
   - Every `assign` key must name an input file; otherwise raise `ValueError`. The CLI checks
     this first and exits with 2.
2. **Resolve the AI state first, so `--learn` fails before anything is written.**
   - The model is needed when `learn` is set or `classifier_dir()/model.json` exists. Then use
     `cached_model(DEFAULT_MODEL, model_cache_dir())`.
   - If the model is still missing and `download_model` is set: call `notify(announcement)`,
     then `download_model`.
     - On `ModelUnavailableError` or `ModelIntegrityError`: re-raise if learning; otherwise
       append the download-failed note and continue without AI (spec §4.6).
   - `learn` without a model raises.
   - A trained classifier without a model appends `NO_MODEL_NOTE`.
   - `load_classifier(…, DEFAULT_MODEL.sha256)`. If it returns `None` although the file
     exists, append `STALE_MODEL_NOTE`.
   - Create an `Embedder` only if a classifier exists or `learn` is set.
3. **Decode every path into a working copy** (long edge 1600), keeping its capture time (mtime
   fallback adds `MTIME_WARNING` once). Bind `photo=<1-based input position>`.
   - `UnsupportedFormatError` → a `skipped` outcome.
   - `CorruptImageError`, `OSError`, or any other `Exception` → a `failed` outcome, with messages
     from `batch_io`.
   - With no photos left, raise.
4. **Assign.**
   - `assign_photos(times, order, classifier=…, embeddings=embedder.embed(workings) if embedder else None)`.
   - Then `with_overrides` with file names mapped to indices.
   - Append `ORDER_NOTE` when the method is `order` and none of `NO_MODEL_NOTE`,
     `STALE_MODEL_NOTE`, or a download-failed note was already added.
5. **`plan_composite` on the working copies.**
6. **Render each cell from a fresh full-resolution decode**, so only one full-size photo is in
   memory at a time. A failure there makes the photo `failed` and leaves its cell empty.
7. **Write the output.**
   - Create `out_dir`.
   - Name it with `composite_filename(patient, tag, composite_capture_date(dates of rendered cells, or [today]))`
     and `write_unique`, so an existing file is never overwritten.
   - Encode with `encode_jpeg(compose(cells))`.
8. **If `learn`:** call `SampleStore(classifier_dir()).append(embeddings of rendered cells, their slots, case_id=uuid4().hex, source="export", model_hash, timestamp=time.time())`.
   Then `.for_model(hash)` and `train_classifier`. If trained, `save` to `classifier_dir()/model.json`.
   Sample case IDs are random, so no patient name ever reaches the data folder.

- [ ] **Step 1: Extract `batch_io` (refactor; existing tests stay green)**

Run: `uv run pytest tests/integration/test_cli_process.py -q`
Expected: 16 passed.

- [ ] **Step 2: Write the failing test**

`tests/integration/test_cli_composite.py`. The fixtures:

- `set_dir` (module scope): nine `flip_pixels(make_view(slot, 70 + i, angle=3.0), DEFAULT_FLIPS[slot])`
  JPEGs named `IMG_0001.jpg`–`IMG_0009.jpg` in row-major order. Their EXIF `DateTimeOriginal`
  values are `2026:10:16 10:00:10` + i seconds. Add one `IMG_0099.MOV` with the bytes
  `b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  "`.
- An autouse `app_dirs` fixture monkeypatches `paths.data_dir` and `paths.cache_dir` to
  `tmp_path / "data"` and `tmp_path / "cache"`.
- `_invoke(set_dir, out, *extra)` runs `composite <all files> --out OUT --patient "Alex Wang" --tag "full mouth" *extra`.
- `OUTPUT = "Alex Wang_full mouth_Composite_20261016.jpg"`.
- `_install_fake_model` builds the fake model into `tmp_path/cache/models/fake.onnx` and
  monkeypatches `headless_composite.DEFAULT_MODEL` to
  `ModelSpec("fake.onnx", "https://unused.test", sha, size)`.

Tests and assertions:

| Test | Assertions |
|---|---|
| `test_composite_by_shooting_order` | exit 0; `Image.open(out/OUTPUT).size == (3600, 2400)`; output contains `"Slots were assigned by shooting order"`, `"Skipped 1 file: 1 video"`, and an `upper_right` line naming `IMG_0001.jpg` |
| `test_assign_swaps_two_photos` | with `--assign IMG_0001.jpg=upper_left`: the `upper_left` line names `IMG_0001.jpg` and the `upper_right` line names `IMG_0003.jpg` (parse lines by their first word) |
| `test_custom_order_changes_the_placement` | with `--order` set to the nine slots reversed: the `lower_left` line names `IMG_0001.jpg` |
| `test_invalid_options_are_usage_errors` | parametrized over `("--order","upper_right")`, `("--flip","upper_right=sideways")`, `("--assign","IMG_9999.jpg=upper_left")`, the two `--assign` options `IMG_0001.jpg=upper_left` and `IMG_0002.jpg=upper_left`, and `("--baseline","middle")`: exit 2 and no `out` folder |
| `test_learning_without_the_model_is_an_error_before_writing` | `--learn` → exit 1, output contains `--download-model`, no `out` folder |
| `test_three_learned_sets_train_the_classifier` | with the fake model installed: run 1 with `--learn` contains `the AI classifier is not trained yet`; run 3 contains `the AI classifier is trained`; run 4 (no `--learn`) exits 0 without the order note; `data/classifier/model.json` exists |
| `test_download_is_announced_and_only_with_consent` | monkeypatch `headless_composite.download_model` with a fake that copies the fake model into the cache; `--learn --download-model` → exit 0; output contains `Downloading the AI model` and `from github.com` |
| `test_failed_download_falls_back_to_shooting_order` (Review Focus 5) | write `data/classifier/model.json` as `{}`; monkeypatch `download_model` to raise `ModelUnavailableError("Downloading the AI model failed: offline")`; `--download-model` → exit 0; output contains that message and `Slots were assigned by shooting order.`; OUTPUT exists |
| `test_corrupt_photo_leaves_its_slot_empty` (Review Focus 2) | copy the set and truncate `IMG_0005.jpg` to 300 bytes → exit 1; output contains `IMG_0005.jpg: `; OUTPUT exists |
| `test_no_usable_photos_is_an_error` | a single `.mov` → exit 1 with `No usable photos` |
| `test_classifier_of_an_older_model_asks_for_new_samples` | with the fake model installed, write `data/classifier/model.json` as `{"model_hash": "an-older-model"}` → exit 0; output contains `The AI model changed` and does not contain `Slots were assigned by shooting order:` |
| `test_a_second_run_never_overwrites_the_first_composite` (Review Focus 1) | run twice, the second time with an `--assign`; the first file's bytes are unchanged and `OUTPUT` with `_2.jpg` exists |
| `test_logs_and_messages_never_name_the_patient` (`debug_logging` fixture) | copy the set to `tmp/Alex Wang/Alex Wang IMG_….jpg`; call `run_composite` under `capture_logs(processors=[merge_contextvars])`; 9 cells; some event is `level`; no log entry's `repr` contains `Alex Wang` |

- [ ] **Step 3: Run the test to verify it fails**

Expected: exit code 2, `No such command 'composite'`.

- [ ] **Step 4: Implement `services/headless_composite.py` and the CLI command**

The CLI parses its options as follows; each failure is a `typer.BadParameter` (exit 2) with the
`param_hint` set:

- `--order`: a comma list, every item checked with `parse_slot`; it must be a permutation of
  the nine slots.
- `--flip SLOT=FLIP`: repeatable; merged over a copy of `DEFAULT_FLIPS`.
- `--assign FILE=SLOT|tray`: repeatable; split with `rpartition("=")`. The file must be one of
  the inputs' names, and two files may not target one slot.
- `--baseline`: checked with `parse_slot`.

Pass `notify=typer.echo`. Catch `(DioError, OSError)` → `error: …`, exit 1. Exit 1 when
`report.failed`.

- [ ] **Step 5: Update `README.md`**

- Status line: "The procedure and composite pipelines are available as command-line tools
  (`dio process`, `dio composite`)."
- In "Development", add a composite example:
  `uv run dio composite IMG_*.HEIC --out export --patient "Alex Wang" --tag "full mouth"`.
  Add one sentence: `--learn` stores a checked run as classifier samples, and the first such
  run needs `--download-model` (about 24 MB, once, from this repository's GitHub Releases).
- Under "Golden photos", add the `local_fixtures/composite/<set>/` layout (Task 11).

- [ ] **Step 6: Verify and commit**

Run: `uv run pytest tests/integration -q`
Expected: 16 + 17 = 33 passed in about 60 s. Then run all gates and the full suite.

```bash
git add src/dental_image_organizer/services src/dental_image_organizer/cli.py README.md tests/integration
git commit -m "feat(cli): add dio composite with learning from confirmed runs"
```

---

### Task 11: Golden composite metrics (local only)

**Files:**

- Create: `tests/golden/test_composite_golden.py`

**Interfaces:**

- Consumes: `paths`, `imageio.decode` / `resize_long_edge`, `batch_io.capture_time`,
  `assignment.*`, `classifier.train_classifier(..., regularization=)`, `embedder.Embedder`,
  `model_store.DEFAULT_MODEL` / `cached_model`, and `composite.plan_composite`.
- Produces: the reports listed below. Nothing depends on them.

**Layout.**

- `local_fixtures/composite/<set>/` holds one session's photos, taken in row-major slot order.
- Optionally, an `order.txt` lists one slot per line.
- Like everything under `local_fixtures/`, the folder is gitignored.

**Module behavior.** It carries `pytestmark = [golden, skipif(no local_fixtures/composite)]`.

- **`test_composite_set_metrics[<set>]`.**
  1. Decode the set and assign by `order.txt` or `SLOTS`.
  2. `plan_composite`.
  3. Write `local_fixtures/reports/composite_<set>.csv` with the columns
     `slot, photo, level_deg, confident`.
  4. Assert there are rows.
- **`test_classifier_leave_one_set_out`.**
  - Skip unless `cached_model(DEFAULT_MODEL, paths.model_cache_dir())` exists and there are at
    least 4 sets.
  - Embed each set's placed photos.
  - For each `C` in `(1.0, 10.0, 100.0)`: train on all other sets with `regularization=C`,
    assign the held-out set with `assign_by_classifier`, and count correct slots.
  - Write `local_fixtures/reports/composite_classifier.csv` with `C, accuracy, photos`. This
    is how spec amendment 1 gets re-checked on real photos.

- [ ] **Step 1: Write the module**

Run: `uv run pytest tests/golden -v`
Expected: the composite tests report `skipped` (`local_fixtures/composite is not present`). The
procedure golden test keeps skipping as before.

- [ ] **Step 2: Commit**

```bash
git add tests/golden/test_composite_golden.py
git commit -m "test(golden): report composite leveling and leave-one-set-out classifier accuracy"
```

---

### Task 12: Export and publish the embedding model (maintainer; gated)

**Files:**

- Create: `scripts/export_dinov2_onnx.py`
- Modify: `src/dental_image_organizer/ml/model_store.py` (only if the hash differs)
- Modify: `README.md` (third-party model notice)

**Interfaces:**

- Consumes: nothing from `src/`; the script is standalone.
- Produces:
  - `dist/models/dinov2s_pooled_int8.onnx` (gitignored, like all `*.onnx`);
  - the GitHub Release `model-dinov2s-v1` that `DEFAULT_MODEL.url` points to.

The script carries PEP 723 inline metadata, so `uv run scripts/export_dinov2_onnx.py --out dist/models`
builds its own throwaway environment. Torch never enters `uv.lock`.

The whole script is given verbatim, because the released file's SHA-256 is pinned in the app.
Any change, even renaming `self.backbone`, renames ONNX initializers and changes the bytes.
The decisions it encodes:

- the source model revision is pinned (`facebook/dinov2-small`, Apache-2.0 per the model card);
- pooling happens in the graph (spec §4.6);
- the legacy TorchScript exporter is used (`dynamo=False`); its deprecation warning is
  expected;
- quantization is int8 per channel. Per-tensor weights measured a block cosine of 0.965 min /
  0.973 mean against PyTorch, per-channel 0.982 / 0.990;
- the self-check runs on smooth random images, because white noise exaggerates int8 error.

`scripts/export_dinov2_onnx.py`:

```python
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "numpy==2.5.4",
#   "onnx==1.23.2",
#   "onnxruntime==1.31.0",
#   "onnxscript==0.7.2",
#   "torch==2.14.1",
#   "transformers==5.20.1",
# ]
# ///
"""Maintainer-only: export the composite embedding model (spec §4.6, §7.2).

DINOv2 ViT-S/14 (Apache-2.0) with in-graph pooling -> ONNX -> dynamic int8 quantization.
Output: (N, 2688) = the CLS token plus the mean patch token of each cell of a 3 x 2 grid,
for an input of (N, 3, 224, 336). Block L2 normalization happens in the app (ml/embedder.py).

Run with:  uv run scripts/export_dinov2_onnx.py --out dist/models
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from onnxruntime.quantization import QuantType, quantize_dynamic
from transformers import Dinov2Model

MODEL_ID = "facebook/dinov2-small"
# Pinned so a re-export reproduces the released file; the model card states Apache-2.0.
REVISION = "ed25f3a31f01632728cabb09d1542f84ab7b0056"
HEIGHT, WIDTH, PATCH, DIM = 224, 336, 14, 384
ROWS, COLS = HEIGHT // PATCH, WIDTH // PATCH  # 16 x 24 patches
GRID_ROWS, GRID_COLS = 2, 3  # 8 x 8 patches per cell
FP32_NAME = "dinov2s_pooled_fp32.onnx"
INT8_NAME = "dinov2s_pooled_int8.onnx"
# int8 vs fp32 on smooth inputs: per-channel weights measured min 0.982 / mean 0.990, while
# per-tensor weights measured 0.965 / 0.973 and are not used.
MIN_BLOCK_COSINE = 0.95
MIN_MEAN_COSINE = 0.98


class PooledDinov2(torch.nn.Module):
    def __init__(self, backbone: Dinov2Model) -> None:
        super().__init__()
        self.backbone = backbone

    def forward(self, pixels: torch.Tensor) -> torch.Tensor:
        tokens = self.backbone(pixel_values=pixels).last_hidden_state  # (N, 1 + 384, 384)
        n = pixels.shape[0]
        patches = tokens[:, 1:].reshape(
            n, GRID_ROWS, ROWS // GRID_ROWS, GRID_COLS, COLS // GRID_COLS, DIM
        )
        cells = patches.mean(dim=(2, 4)).reshape(n, GRID_ROWS * GRID_COLS, DIM)
        return torch.cat([tokens[:, :1], cells], dim=1).reshape(
            n, (1 + GRID_ROWS * GRID_COLS) * DIM
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    backbone = Dinov2Model.from_pretrained(MODEL_ID, revision=REVISION).eval()
    model = PooledDinov2(backbone).eval()
    fp32, int8 = args.out / FP32_NAME, args.out / INT8_NAME
    sample = torch.randn(2, 3, HEIGHT, WIDTH)
    torch.onnx.export(
        model,
        (sample,),
        str(fp32),
        input_names=["pixels"],
        output_names=["embedding"],
        dynamic_axes={"pixels": {0: "batch"}, "embedding": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )
    quantize_dynamic(str(fp32), str(int8), weight_type=QuantType.QInt8, per_channel=True)

    # Smooth random images: white noise is far from photos and exaggerates int8 error.
    coarse = torch.from_numpy(
        np.random.default_rng(0).standard_normal((4, 3, 8, 12)).astype(np.float32)
    )
    x = torch.nn.functional.interpolate(coarse, size=(HEIGHT, WIDTH), mode="bicubic").numpy()
    with torch.no_grad():
        expected = model(torch.from_numpy(x)).numpy()
    for path in (fp32, int8):
        (got,) = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"]).run(
            ["embedding"], {"pixels": x}
        )
        low, mean = _block_cosine(expected, got)
        print(
            f"{path.name}: shape {got.shape}, block cosine vs PyTorch min {low:.4f} mean {mean:.4f}"
        )
        if got.shape != (4, 2688) or low < MIN_BLOCK_COSINE or mean < MIN_MEAN_COSINE:
            raise SystemExit(f"{path.name} does not reproduce the PyTorch embeddings")

    data = int8.read_bytes()
    print(f"sha256:   {hashlib.sha256(data).hexdigest()}")
    print(f"size:     {len(data)} bytes")


def _block_cosine(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    a, b = a.reshape(len(a), -1, DIM), b.reshape(len(b), -1, DIM)
    cosine = (a * b).sum(axis=2) / (np.linalg.norm(a, axis=2) * np.linalg.norm(b, axis=2))
    return float(cosine.min()), float(cosine.mean())


if __name__ == "__main__":
    main()
```

- **Reproducibility.** Two exports on the verified environment both printed:

  ```
  sha256:   da14f66b5e204cce7c8f2b65fd2936b02d5f4cc015687f05465de32717ecc5b0
  size:     24421288 bytes
  ```

- [ ] **Step 1: Write the script and run it**

Run: `uv run scripts/export_dinov2_onnx.py --out dist/models`
This downloads torch (≈200 MB) from PyPI and the weights (≈90 MB) from huggingface.co.

Expected: two lines `…onnx: shape (4, 2688), block cosine …`, then the sha256 and size above.

If the hash differs (for example, another platform's torch build), keep going: Step 3 pins
whatever is uploaded.

- [ ] **Step 2: Ask the user before publishing**

Creating a release is outward-facing. Show the user the release command and notes, and wait
for an explicit yes. Then:

```bash
curl -L -o dist/models/LICENSE-Apache-2.0.txt https://www.apache.org/licenses/LICENSE-2.0.txt
gh release create model-dinov2s-v1 dist/models/dinov2s_pooled_int8.onnx dist/models/LICENSE-Apache-2.0.txt \
  --title "Embedding model v1: DINOv2-S, pooled, int8" \
  --notes "Composite view embeddings for Dental-Image-Organizer (spec §4.6).
Derived from facebook/dinov2-small @ ed25f3a3 (Apache-2.0, Meta Platforms); pooled CLS + 3x2 grid
(2688-d), dynamic int8 per-channel. Built with scripts/export_dinov2_onnx.py.
SHA-256: <printed hash>  Size: <printed size> bytes"
```

- [ ] **Step 3: Pin the release in the app**

If the uploaded file's hash or size differs from the values in Task 6, update
`DEFAULT_MODEL.sha256` and `.size_bytes`.

Then verify the real download path once, against the real release:

```bash
uv run python -c "from dental_image_organizer import paths; from dental_image_organizer.ml.model_store import DEFAULT_MODEL, download_model; print(download_model(DEFAULT_MODEL, paths.model_cache_dir()))"
```

Expected: a path to the cached file, with no `ModelIntegrityError`.

- [ ] **Step 4: Add the README notice and commit**

Add a README section "Third-party model". It says the composite classifier uses embeddings
from Meta's DINOv2 ViT-S/14 (Apache-2.0), downloaded once with consent from this repository's
Releases, and it links the release.

```bash
git add scripts/export_dinov2_onnx.py README.md src/dental_image_organizer/ml/model_store.py
git commit -m "build: add the reproducible DINOv2 export and publish the embedding model"
```

- [ ] **Step 5: Push and open the pull request**

```bash
git push -u origin feat/composite
gh pr create --title "Plan 2: Feature 2 composite and AI slot classification (dio composite)" --body "Implements doc/superpowers/plans/2026-10-17-plan-2-composite.md."
```

Expected: CI is green on ubuntu, windows, and macos.
