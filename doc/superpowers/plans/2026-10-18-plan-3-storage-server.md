# Plan 3 — Storage, Case Services, and the Local Server API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `dio serve`, a local server on `127.0.0.1` that keeps patients and cases in the
library folder and serves both features over the HTTP API of spec §4.12 (all of it except the
in-app update). After this plan the Plan 4 frontend has everything it needs:

1. a library of patients and cases on disk (`patient.json`, `case.json`, immutable originals,
   `.trash`);
2. background jobs that process uploads, export cases and patients, and teach the classifier,
   with progress over Server-Sent Events;
3. single-instance start, port reuse, `--detach` for the launcher, and idle shutdown.

It also adds `dio export-case` and `dio import-case`.

**Architecture:**

| Layer | Contents |
|---|---|
| `paths.py`, `config.py` | app folders (`AppDirs`); settings in `settings.json` |
| `storage/` (new; imports `core` only) | atomic writes, the `case.json` schema, library folders and locks, the render cache, case archives |
| `services/` | jobs; AI runtime and flip learning; intake; procedure and composite cases; renders; export; teaching |
| `api/` (new) | FastAPI app: security middleware, error mapping, routes, the SPA mount |
| `instance.py`, `server.py` | single-instance lock, port choice, uvicorn, `--detach`, idle shutdown |

`services.context.AppContext` is the composition root. Every service function takes it, and
tests build one over temporary folders.

**Tech Stack:** Plans 1–2, plus:

| Purpose | Packages |
|---|---|
| HTTP API | `fastapi`, `pydantic` |
| ASGI server | `uvicorn` |
| Multipart uploads | `python-multipart` |
| Single instance | `filelock` |
| Dev only | `httpx2` (the client Starlette 1.x's `TestClient` expects) |

**Spec:** [doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md](../specs/2026-10-16-dental-image-organizer-design.md).
Read §3.1–§3.5, §4.3, §4.4 step 5, §4.7.2 step 1, §4.8, §4.9, §4.11, §4.12, §4.13, §8.1
(storage and flip learning), and §8.2 before starting.

## Where This Fits

Plans 1 and 2 are merged (`4e61611`, `71c23a2`). This plan is 3 of 5.

- **Plan 4 (frontend)** builds on this API. It also:
  - generates the TypeScript types from the OpenAPI schema and adds the CI stale check (§4.12);
  - computes every manual matrix (free, two-point, four-point, and "Reset perspective", the
    closest similarity) in `frontend/src/lib/transform.ts`, and sends it with
    `PATCH …/photos/{pid}` as `edit`. This plan validates and stores those matrices; it adds no
    endpoint for them.
- **Plan 5 (distribution)** adds:
  - `POST /system/update`, the daily version check, and `services/updates.py`;
  - `dio install-launcher` and the `uv_path` / `dio_path` settings;
  - `install.sh`, the release workflow, and the macOS smoke test;
  - spec §8.2 item 6 (`/system/update` returns 409 while a job runs).
- `dio process` and `dio composite` stay file-based; they never touch the library.

## Spec Amendments (confirm before executing)

The spec leaves these points open or gets them slightly wrong. Task 1 writes them into the spec.

1. **Photo state (§4.8, §4.11).** Each photo gains:
   - `state`: `pending | ready | failed`, plus `error`. A photo that fails to decode has no edit
     record, so its failure lives on the photo. `edit.status` is therefore only `ok |
     needs_review`.
   - `edit`: `null` until the photo has a placement. In a procedure case that means it was
     aligned to a reference. In a composite case it means the photo is in a slot.
   - `stage_source`: `auto | manual`, so the D0 rule (§3.2 step 5) never overrides a stage the
     dentist set.
   - `role`: `photo` for the non-reference photos of a procedure case. The spec does not name
     this role.

   Composite edits gain `flip`. The composite block gains `slots_source`: `auto | manual`.
   `output_aspect` is dropped from §4.8, because `output_size` already fixes the aspect.
2. **Uploads (§4.12, §4.13).**
   - The response is `{job_id, added, skipped: [{name, reason, message}]}`. `reason` is one of
     `video | raw | sidecar | unknown | duplicate | too_large`.
   - An optional `last_modified` form field (milliseconds since the epoch, one per file, from
     the browser's `File.lastModified`) feeds the modification-time fallback of §4.4 step 5. The
     server would otherwise only know the upload time.
   - Multipart requests must carry a `Content-Length`: missing → 411, over 2 GiB → 413. Each
     file is limited to 200 MB, and a larger file is reported as `too_large`.
3. **Archive import (§4.11, §4.12).** The endpoint is `POST /archive/import?on_conflict=ask|skip|copy`.
   With `ask` (the default), a case id that is already in the library answers
   `409 case_exists` with the existing case's id and title, so the UI can ask the user and send
   the archive again.
4. **Export conflicts (§4.9).**
   - "Replace previous export" deletes the files that this case's earlier exports wrote, which
     are recorded in `case.exports`. Then it writes the new files.
   - Files the app did not write are never deleted or overwritten. A new file whose name they
     already use gets `_2`.
   - A single-case export zip is named `{patient}_{title}_Export_{YYYYMMDD}.zip`. A case archive
     is named `{patient}_{title}_Archive_{YYYYMMDD}.zip`, with the case's creation date.
5. **Flip learning (§4.7.2).**
   - Confirmations are keyed by case, or by teaching session. Exporting the same case again
     replaces that case's earlier confirmation instead of counting it twice. Samples already
     follow this rule (§4.6).
   - A pinned slot records nothing.
6. **`--detach` readiness (§4.3).**
   - The parent passes a one-time token in `DIO_READY_TOKEN`, and the child writes it into
     `instance.json`.
   - The parent waits for that token and then for `/api/health`.
   - The child's process id cannot be used: on Windows the venv's `python.exe` is a launcher, so
     the server runs under another pid (measured, see Before You Start).
7. **`DIO_APP_DIR` (§4.11).** When this environment variable is set, the four app folders are
   `$DIO_APP_DIR/{config,data,cache,log}`, and the default library is `$DIO_APP_DIR/library`.
   Tests and the Plan 5 macOS smoke test use it, so they never touch the real folders.
8. **Exceptions (§4.13).** The domain errors also include:

   | Error | Purpose |
   |---|---|
   | `PatientNotFoundError`, `JobNotFoundError`, `TeachSessionNotFoundError` | not-found cases the spec does not list |
   | `JobRunningError`, `CaseExistsError`, `NotReadyError` | conflicts (409) |
   | `InvalidRequestError` | semantic validation (422) |
   | `InvalidArchiveError` | a damaged or foreign case archive |
   | `PathOutsideLibraryError` | path confinement (400) |
9. **Library problems (§4.12 `GET /patients`).** The response also lists the folders it could
   not read: damaged, saved by a newer version, or duplicated. A damaged case is then visible
   instead of silently missing.

## Before You Start

- Run `git fetch origin && git switch -c feat/storage-server origin/main`. The base must contain
  the Plan 2 merge, `71c23a2`.
- **Prototypes (2026-10-18).** These ran on a scratch copy of `origin/main`. They are not a full
  implementation of this plan.
  - The existing suite: 322 passed, 3 skipped (the golden tests need local photos).
  - Library versions:

    | Package | Version |
    |---|---|
    | fastapi | 0.146.0 |
    | starlette | 1.9.0 |
    | uvicorn | 0.54.0 |
    | pydantic | 2.14.1 |
    | python-multipart | 0.0.32 |
    | filelock | 4.2.3 |
    | httpx2 (dev) | 2.14.0 |

  - Facts measured on the Windows development host, which this plan relies on:

    | Fact | Consequence in this plan |
    |---|---|
    | `os.replace` onto a file another thread has open fails with `PermissionError` (1917 of 2000 replaces, and 487 reads failed too) | one library I/O lock for every `case.json` read and write; `write_atomic` retries |
    | The venv's `python.exe` is a launcher: the server's pid differs from the `Popen` pid | the readiness token (amendment 6) |
    | A server shut down after serving requests can bind the same port again at once (`SO_EXCLUSIVEADDRUSE` on Windows, `SO_REUSEADDR` elsewhere) | the origin survives restarts (§4.3) |
    | uvicorn serves a pre-bound socket (`Server.run(sockets=[sock])`) and stops on `should_exit = True` | no gap between choosing and binding the port |
    | Starlette 1.9's `TestClient` streams SSE events as they are produced; without `httpx2` it warns that `httpx` is deprecated | SSE is testable in-process; `httpx2` is a dev dependency |
    | `TrustedHostMiddleware` answers a wrong `Host` with 400 `Invalid host header` | spec §8.2 item 7 |
    | structlog's `ProcessorFormatter` writes JSON lines to a rotating file and readable lines to the console, and also picks up uvicorn's stdlib logs; `format_exc_info` keeps local variables out | the server log (§4.1) |

  - Timings on the scratch copy (synthetic 12 MP photos):

    | Operation | Time |
    |---|---|
    | Plan all nine composite cells from 1600 px working copies | 0.6 s |
    | One `normalize_view` | 48 ms |
    | 1600 px preview from a 12 MP original | 0.1 s |
    | Decode a 12 MP JPEG | 0.23 s |
    | Refit the F1 colors after a manual alignment | 0.3 s |
    | `prepare_reference` | 0.19 s |
    | Save and load a 1600 px working copy as `.npy` | 5 ms |

  If a fact or a timing differs on your machine, report it.

## Global Constraints

Everything in the Global Constraints of Plans 1 and 2 still applies:

- English text; comments explain *why*;
- structlog only;
- no image files in git;
- LF line endings;
- all five quality gates (`ruff check`, `ruff format --check`, `mypy`, `lint-imports`,
  `pytest`) pass before each commit;
- each commit ends with the `Co-Authored-By:` trailer of the model that writes it;
- batch isolation: one bad photo never aborts a batch (§4.13).

Additions:

- **Formatting.** Code blocks in this plan are condensed. Run `uv run ruff format` and
  `uv run ruff check --fix` before the gates; that changes layout only.
- **Import boundaries.** These are the import-linter contracts once the plan is done.
  import-linter rejects a contract that names a module that does not exist yet ("Module … does
  not exist"). Each module is therefore added to the contracts by the task that creates it:
  - Task 1 adds `storage` and `config`;
  - Task 5 adds `instance`;
  - Task 14 adds `api`;
  - Task 17 adds `server`.

  | Package | Must not import |
  |---|---|
  | `core` | `ml`, `storage`, `services`, `api`, `cli`, `paths`, `config`, `instance`, `server` |
  | `ml` | `storage`, `services`, `api`, `cli`, `paths`, `config`, `instance`, `server` |
  | `storage` | `ml`, `services`, `api`, `cli`, `paths`, `config`, `instance`, `server` |
  | `services` | `api`, `cli`, `server` |
  | `api` | `cli`, `server` |

- **Privacy (§4.13).**
  - Log events never contain patient names, titles, file names, or library paths; case folders
    carry patient names.
  - Services bind `case_id`, `photo_id`, and `job_id` (random hex ids) with
    `structlog.contextvars.bound_contextvars`.
  - Error messages may name things, because they go only to the user, never to the log. The API
    logs an error's `code`, never its message.
  - An `OSError` is logged with `strerror` only, because its message carries the path.
- **Concurrency.** These four rules prevent lost updates and deadlocks:
  1. Every read and write of `patient.json` and `case.json` goes through `Library`, which holds
     one I/O lock around file operations.
  2. A compound change of one case runs inside `Library.update_case`, which holds that case's
     re-entrant lock from the read to the write.
  3. CPU-heavy work (decoding, aligning, rendering, embedding) runs in `ctx.executor`, never on
     the event loop. Code that holds a case lock never waits for the executor or a job.
  4. Processing, assignment, and export jobs of one case share the job key `case_id`, so they
     run one at a time in submission order.
- **Security (§4.13).**
  - Bind `127.0.0.1` only.
  - `TrustedHostMiddleware` allows `localhost` and `127.0.0.1`.
  - Every `POST`, `PUT`, `PATCH`, and `DELETE` needs `Origin` equal to
    `http://127.0.0.1:<port>` or `http://localhost:<port>`. There is no development bypass.
  - Every path the API is given, and every path read from a record, resolves inside the library
    root.
- **Network.** This plan adds no outbound call. The model download runs only on
  `POST /model/download`. Tests use `httpx.MockTransport` or the fake model.
- **Error body.** Every error response is `{"error": {"code", "message", "details"}}`.
- **Atomic writes.** All JSON state goes through `storage.atomic.write_atomic` (temporary file,
  `fsync`, `os.replace`).
- **Spec values to copy exactly.**

  | Item | Value | Spec |
  |---|---|---|
  | Ports | 8765 default; scan 8765–8775; try the port in `instance.json` first | §4.3 |
  | `--detach` health wait | 20 s | §4.3 |
  | Idle shutdown | `idle_shutdown_minutes` (default 30), and only with no job running | §4.3 |
  | Workers | `min(4, os.cpu_count())`, `cv2.setNumThreads(1)` per worker | §4.3 |
  | Library root | `~/DentalImageOrganizer/` | §4.11 |
  | Patient folder | sanitized name; ` (2)` on a clash | §4.11 |
  | Case folder | `YYYYMMDD title` | §4.11 |
  | Originals | `originals/<sha256[:16]>.<ext>`, deduplicated by SHA-256 | §4.11 |
  | Render sizes | thumbnail 320 px, preview 1600 px, full | §4.8 |
  | Render cache | key `sha256(original_sha256 + edit + size)`; `cache_max_gb` 2; evicted at startup and after each job | §4.8 |
  | Upload limits | 200 MB per file; 200 MP decode cap | §4.13 |
  | JPEG | quality 92, GPS stripped by default | §4.4 |
  | Flip learning | ≥ 3 confirmations; most frequent of the last 5; a tie keeps the current default; pin > learned > start | §4.7.2 |
  | Stage rule | reference `pre_op`; on D0 `intra_op`; after D0 `post_op` | §3.2 |
  | Sequence | 2–10 photos selected; default all in capture order | §3.2 |
  | Output resolution suggestion | median `detail_ratio` ≥ 1.5 | §3.2 |
  | Log | `dio.log`, JSON lines, 5 × 5 MB | §4.1 |

## Review Focus

These are inputs the spec implies but never spells out, and that are likely to bite a dentist.
Each one is pinned by a test in the owning task.

1. **Dropping the same photos again.** The dentist re-drops a whole folder after adding two
   photos to it. Only the two new photos are added. Each repeat is reported as `duplicate`,
   and no photo appears twice.
   → Task 8, `test_dropping_the_same_photo_again_is_reported_as_duplicate`.
2. **Exporting the same composite again.** The dentist fixes a cell and exports a second time.
   The classifier must not count the set twice:
   - each photo keeps one sample;
   - each slot keeps one flip confirmation for that case.

   → Task 12, `test_exporting_a_composite_again_does_not_count_it_twice`.
3. **A second launch while the app starts or runs.** The dentist double-clicks the Dock icon.
   There is still one server, and the browser opens to it.
   → Task 17, `test_a_second_launch_opens_the_running_server`.
4. **The library changed in Finder.** The library is a plain folder, so the dentist may:
   - rename a case folder;
   - duplicate one;
   - delete one;
   - leave a damaged or hand-edited `case.json`.

   None of this may crash the Library screen. A duplicated or damaged folder is reported, and a
   renamed case is still found.
   → Task 3, `test_folders_changed_in_finder_are_reported_not_fatal` and
   `test_a_case_folder_renamed_in_finder_is_found_again`.
5. **Patient names that are unsafe as folder names.** Examples:
   - a name that macOS hands over decomposed (NFD), such as `王小明 Café`;
   - `/` or `:` in a name;
   - a name of only dots;
   - two patients with the same name;
   - names that differ only in case.

   Each becomes a safe, unique folder.
   → Task 3, `test_folder_names_are_safe_and_unique`.

## File Structure

```
src/dental_image_organizer/
├── paths.py                 # Modify: AppDirs, DIO_APP_DIR, config and log folders
├── config.py                # Create: Settings, SettingsStore
├── errors.py                # Modify: new domain errors; DioError.details
├── log.py                   # Modify: optional JSON log file next to the console
├── instance.py              # Create: single-instance lock and instance.json
├── server.py                # Create: dio serve: ports, uvicorn, --detach, browser
├── cli.py                   # Modify: serve, export-case, import-case
├── core/naming.py           # Modify: case export and archive zip names
├── storage/
│   ├── __init__.py
│   ├── atomic.py            # write_atomic
│   ├── schema.py            # PatientRecord, CaseRecord, PhotoRecord, EditRecord, ...
│   ├── library.py           # Library: folders, index, I/O lock, case locks
│   ├── cache.py             # BlobCache: content-addressed LRU in the cache folder
│   └── archive.py           # case zip export and import
├── ml/training.py           # Create: store samples and retrain (moved from headless_composite)
├── services/
│   ├── procedure.py         # Modify: fit_colors extracted from compute_edit
│   ├── composite.py         # Modify: fit_cell_colors made public
│   ├── headless_composite.py# Modify: use shooting_order and ml.training
│   ├── jobs.py              # JobManager
│   ├── flips.py             # FlipStore and learned / effective flips
│   ├── ai.py                # AiRuntime: model, embedder, classifier, learning
│   ├── context.py           # AppContext, create_context
│   ├── intake.py            # upload staging and skip reasons
│   ├── originals.py         # decoding, working copies, embeddings, prepared references
│   ├── procedure_case.py    # Feature 1 in the library
│   ├── shooting_order.py    # shooting-order placement with holes (moved)
│   ├── composite_case.py    # Feature 2 in the library
│   ├── renders.py           # image variants, sequence and composite previews
│   ├── export.py            # case and patient export, zips, learning on export
│   ├── teach.py             # teaching sessions
│   ├── cases.py             # case and patient changes across both types
│   └── reveal.py            # reveal a path in Finder / Explorer
└── api/
    ├── __init__.py
    ├── app.py               # create_app
    ├── security.py          # LocalRequestGuard (Origin, request size)
    ├── errors.py            # error → status mapping and handlers
    ├── deps.py              # context dependency, run_cpu
    ├── lifecycle.py         # IdleWatch and the lifespan loop
    ├── models.py            # request and response schemas
    └── routes/              # system, settings, jobs, patients, cases, photos,
                             # composite, render, export, archive, model, classifier
tests/
├── conftest.py              # Modify: ctx fixture
├── support/app.py           # Create: make_context, JPEG and set builders, wait
├── support/fake_model.py    # Modify: install_fake_model
├── unit/                    # test_config, test_atomic, test_schema, test_library, test_cache,
│                            # test_archive, test_instance, test_jobs, test_flips,
│                            # test_ai_runtime, test_context, test_intake,
│                            # test_procedure_case, test_shooting_order,
│                            # test_composite_case, test_renders, test_export, test_teach,
│                            # test_idle, test_server
└── integration/             # test_cli_archive, test_api_foundation, test_api_library,
                             # test_api_procedure, test_api_composite, test_api_export,
                             # test_api_ai, test_serve_process
```

---

### Task 1: Dependencies, app folders, settings, errors, and spec amendments

**Files:**

- Modify: `pyproject.toml`, `uv.lock`
- Modify: `src/dental_image_organizer/paths.py`
- Create: `src/dental_image_organizer/config.py`
- Create: `src/dental_image_organizer/storage/__init__.py`, `src/dental_image_organizer/storage/atomic.py`
- Modify: `src/dental_image_organizer/errors.py`
- Modify: `doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md`
- Test: `tests/unit/test_paths.py`, `tests/unit/test_config.py`, `tests/unit/test_atomic.py`

**Interfaces:**

- Produces:
  - `paths`:
    - `APP_DIR_ENV = "DIO_APP_DIR"`.
    - `config_dir()` and `log_dir()` (platformdirs `user_config_dir` / `user_log_dir`).
    - `data_dir()`, `cache_dir()`, `config_dir()`, and `log_dir()` each return
      `Path(os.environ[APP_DIR_ENV]) / "data"` (or `"cache"`, `"config"`, `"log"`) when the
      variable is set.
    - `AppDirs(config: Path, data: Path, cache: Path, log: Path)`, a frozen dataclass with these
      properties:

      | Property | Path |
      |---|---|
      | `settings_file` | `config / "settings.json"` |
      | `classifier` | `data / "classifier"` |
      | `instance_lock` | `data / "instance.lock"` |
      | `instance_file` | `data / "instance.json"` |
      | `models` | `cache / "models"` |
      | `renders` | `cache / "renders"` |
      | `exports` | `cache / "exports"` |
      | `teach` | `cache / "teach"` |
      | `log_file` | `log / "dio.log"` |

    - `app_dirs() -> AppDirs`. It is built from the four functions at call time, so the
      existing tests that monkeypatch `paths.data_dir` and `paths.cache_dir` keep working.
  - `storage.atomic`:
    - `write_atomic(path: Path, data: bytes) -> None`;
    - `REPLACE_ATTEMPTS = 20` and `REPLACE_DELAY_S = 0.05`.
  - `config`:
    - `default_library_root() -> Path`: `Path(os.environ[APP_DIR_ENV]) / "library"` when the
      variable is set, else `Path.home() / "DentalImageOrganizer"`. It is read at call time.
    - `Settings`, a Pydantic model with `ConfigDict(extra="forbid", frozen=True)`:

      | Field | Type | Default | Rule |
      |---|---|---|---|
      | `schema_version` | `Literal[1]` | `1` | |
      | `library_root` | `Path` | `default_library_root()` | absolute |
      | `jpeg_quality` | `int` | `92` | 50–100 |
      | `strip_gps` | `bool` | `True` | |
      | `cell_size` | `tuple[int, int]` | `(1200, 800)` | each side 100–4000 |
      | `gap_px` | `int` | `0` | 0–200 |
      | `gap_color` | `str` | `"#000000"` | `#RRGGBB` |
      | `shooting_order` | `tuple[Slot, ...]` | `SLOTS` | every slot exactly once |
      | `flip_start` | `dict[Slot, Flip]` | `DEFAULT_FLIPS` | a slot left out takes its `DEFAULT_FLIPS` value |
      | `flip_pins` | `dict[Slot, Flip]` | `{}` | |
      | `cache_max_gb` | `float` | `2.0` | > 0 and ≤ 100 |
      | `idle_shutdown_minutes` | `int` | `30` | 0–1440; 0 never shuts down |

    - `SettingsStore(path: Path)`, which loads on construction, with `get() -> Settings` and
      `put(settings: Settings) -> Settings`.
  - `errors`:
    - `DioError.__init__(self, message: str = "", *, details: Mapping[str, Any] | None = None)`
      stores `self.details`. `UnsupportedFormatError` keeps its signature.
    - New classes:

      | Class | Base | `code` |
      |---|---|---|
      | `NotFoundError` | `DioError` | `not_found` |
      | `PatientNotFoundError`, `CaseNotFoundError`, `PhotoNotFoundError`, `JobNotFoundError`, `TeachSessionNotFoundError` | `NotFoundError` | `patient_not_found`, `case_not_found`, `photo_not_found`, `job_not_found`, `teach_session_not_found` |
      | `ConflictError` | `DioError` | `conflict` |
      | `JobRunningError` | `ConflictError` | `job_running` |
      | `CaseExistsError` | `ConflictError` | `case_exists` |
      | `NotReadyError` | `ConflictError` | `not_ready` |
      | `InvalidRequestError` | `DioError` | `invalid_request` |
      | `InvalidEditError` | `InvalidRequestError` | `invalid_edit` |
      | `InvalidArchiveError` | `DioError` | `invalid_archive` |
      | `PathOutsideLibraryError` | `DioError` | `path_outside_library` |

**Rules:**

- **`write_atomic`.**
  1. Create the parent folder.
  2. Write `data` to `path.with_name(f".{path.name}.{uuid4().hex[:8]}.tmp")`. The unique name
     lets two writers of one file never share a temporary file.
  3. Flush and `os.fsync`, then close.
  4. `os.replace` onto `path`. On `PermissionError`, sleep `REPLACE_DELAY_S` and retry, up to
     `REPLACE_ATTEMPTS` attempts. On Windows another program (an antivirus, an indexer) may hold
     the target open for a moment.
  5. On any failure, remove the temporary file and re-raise the original `OSError`. Callers word
     the message.
- **`SettingsStore`.**
  - A missing file gives the defaults; nothing is written until `put`.
  - A file whose `schema_version` is above 1 raises
    `StorageError("The settings were saved by a newer version of Dental Image Organizer; update the app.")`.
  - Any other unreadable or invalid file raises
    `StorageError(f"The settings file is damaged; move {path} aside to start with default settings.")`.
    That path is in the app's config folder, never in the library.
  - `put` writes `settings.model_dump_json(indent=2)` with `write_atomic`, then swaps the
    in-memory copy under a lock.

- [ ] **Step 1: Add the dependencies**

```bash
uv add "fastapi>=0.146" "filelock>=4.2" "pydantic>=2.14" "python-multipart>=0.0.32" "uvicorn>=0.54"
uv add --dev "httpx2>=2.14"
```

In `pyproject.toml`, replace both import-linter contracts with these four. Task 5 adds
`instance`, Task 14 adds `api`, and Task 17 adds `server` to the forbidden lists.

```toml
[[tool.importlinter.contracts]]
name = "core is pure"
type = "forbidden"
source_modules = ["dental_image_organizer.core"]
forbidden_modules = [
  "dental_image_organizer.ml", "dental_image_organizer.storage",
  "dental_image_organizer.services", "dental_image_organizer.cli",
  "dental_image_organizer.paths", "dental_image_organizer.config",
]

[[tool.importlinter.contracts]]
name = "ml uses core only"
type = "forbidden"
source_modules = ["dental_image_organizer.ml"]
forbidden_modules = [
  "dental_image_organizer.storage", "dental_image_organizer.services",
  "dental_image_organizer.cli", "dental_image_organizer.paths", "dental_image_organizer.config",
]

[[tool.importlinter.contracts]]
name = "storage uses core only and takes its folders as arguments"
type = "forbidden"
source_modules = ["dental_image_organizer.storage"]
forbidden_modules = [
  "dental_image_organizer.ml", "dental_image_organizer.services",
  "dental_image_organizer.cli", "dental_image_organizer.paths", "dental_image_organizer.config",
]

[[tool.importlinter.contracts]]
name = "services never import the CLI"
type = "forbidden"
source_modules = ["dental_image_organizer.services"]
forbidden_modules = ["dental_image_organizer.cli"]
```

- [ ] **Step 2: Write the failing tests**

| File | Test | Assertions |
|---|---|---|
| `test_paths.py` | `test_app_dir_env_puts_every_folder_under_it` | with `DIO_APP_DIR=tmp`: `app_dirs() == AppDirs(tmp/"config", tmp/"data", tmp/"cache", tmp/"log")`; `settings_file`, `log_file`, `instance_lock`, and `renders` are the paths in the table; `config.default_library_root() == tmp/"library"` |
| | `test_app_dirs_follow_the_platform_folders` | with the variable unset: `app_dirs().data == paths.data_dir()`, `.classifier == paths.classifier_dir()`, `.models == paths.model_cache_dir()` |
| `test_config.py` | `test_defaults_match_the_spec` | with `Path.home` monkeypatched to `tmp`: `library_root == tmp/"DentalImageOrganizer"`, `jpeg_quality == 92`, `strip_gps is True`, `cell_size == (1200, 800)`, `gap_px == 0`, `shooting_order == SLOTS`, `flip_start == DEFAULT_FLIPS`, `flip_pins == {}`, `cache_max_gb == 2.0`, `idle_shutdown_minutes == 30` |
| | `test_missing_file_gives_defaults_and_writes_nothing` | `SettingsStore(p).get() == Settings()` and `not p.exists()` |
| | `test_settings_round_trip` | `put` a copy with `jpeg_quality=80` and `flip_pins={"upper_left": "none"}`; a new `SettingsStore(p).get()` equals it |
| | `test_invalid_settings_are_rejected` | parametrized: relative `library_root`; a `shooting_order` with a repeated slot; `jpeg_quality=101`; `gap_color="red"`; `cell_size=(50, 800)`; an unknown field → `ValidationError` |
| | `test_partial_flip_start_keeps_the_other_defaults` | `Settings(flip_start={"upper_right": "none"}).flip_start == DEFAULT_FLIPS \| {"upper_right": "none"}` |
| | `test_damaged_settings_file_is_a_storage_error` | file text `{` → `StorageError` whose message contains `move` |
| | `test_settings_from_a_newer_version_ask_for_an_update` | `{"schema_version": 2}` → `StorageError` containing `newer version` |
| `test_atomic.py` | `test_write_replaces_the_file` | old bytes → new bytes; no `*.tmp` left in the folder |
| | `test_a_crash_before_the_rename_keeps_the_old_file` | monkeypatch `os.fsync` to raise `OSError`; the call raises; the old bytes are intact; no `*.tmp` left |
| | `test_replace_is_retried_while_the_target_is_held` | monkeypatch `REPLACE_DELAY_S = 0` and `os.replace` to raise `PermissionError` twice, then call the real one; the new bytes are written |
| | `test_replace_gives_up_after_the_last_attempt` | `os.replace` always raises `PermissionError` → `PermissionError`; the old bytes are intact; no `*.tmp` left |

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_paths.py tests/unit/test_config.py tests/unit/test_atomic.py -q`
Expected: FAIL with `ImportError` (no `config`, no `storage.atomic`, no `AppDirs`).

- [ ] **Step 4: Implement `paths`, `config`, `storage/atomic.py`, and the errors**

- [ ] **Step 5: Apply the spec amendments**

Edit the spec so that it states amendments 1–9 of this plan:

| Amendment | Spec sections to edit |
|---|---|
| 1 (photo state) | the §4.8 field list; the §4.11 JSON example (`state`, `error`, `stage_source`) and its field rules |
| 2 (uploads) | the §4.12 upload row; §4.13 security (request limit) |
| 3 (archive conflicts) | the §4.11 "Archive" paragraph; the §4.12 archive row |
| 4 (export conflicts) | the §4.9 "Collisions" paragraph and the zip names |
| 5 (flip learning) | §4.7.2 step 1 |
| 6 (readiness token) | the §4.3 `--detach` list |
| 7 (`DIO_APP_DIR`) | the §4.11 app data directories table |
| 8 (exceptions) | the §4.13 exception list |
| 9 (library problems) | the §4.12 `GET /patients` row |

- [ ] **Step 6: Run the tests and all gates**

Run: `uv run pytest -q && uv run ruff check && uv run ruff format --check && uv run mypy && uv run lint-imports`
Expected: all pass. The existing 322 tests are still green.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/dental_image_organizer/paths.py src/dental_image_organizer/config.py src/dental_image_organizer/storage src/dental_image_organizer/errors.py doc/superpowers/specs tests/unit/test_paths.py tests/unit/test_config.py tests/unit/test_atomic.py
git commit -m "feat: add settings, app folders, atomic writes, and server dependencies"
```

---

### Task 2: The `case.json` and `patient.json` schema

**Files:**

- Create: `src/dental_image_organizer/storage/schema.py`
- Modify: `src/dental_image_organizer/services/procedure.py` (import `OUTPUT_SCALES` from the schema)
- Test: `tests/unit/test_schema.py`

**Interfaces:**

- Consumes: `core.slots` (`Slot`, `Flip`), `core.align.AlignModel`, `core.transform`,
  `core.color` (`LabCurves`, `LabAffine`, `LabTransform`), and `errors.StorageError`,
  `errors.PhotoNotFoundError`.
- Produces (all models use `ConfigDict(extra="forbid", frozen=True)`):
  - Constants:

    | Name | Value |
    |---|---|
    | `SCHEMA_VERSION` | `1` |
    | `OUTPUT_SCALES` | `(1.0, 1.5, 2.0)`, moved here; `services.procedure` imports it, so `cli.py` keeps working |
    | `ID_PATTERN` | `r"^[0-9a-f]{32}$"` |
    | `SHA256_PATTERN` | `r"^[0-9a-f]{64}$"` |
    | `ORIGINAL_FILE_PATTERN` | `r"^originals/[0-9a-f]{16}\.(heic\|jpg\|png)$"` |
    | `EXPORT_FILE_PATTERN` | `r"^export/[^/\\]+$"` |

  - `new_id() -> str`, which returns `uuid.uuid4().hex`.
  - Literals:

    | Literal | Values |
    |---|---|
    | `CaseType` | `procedure`, `composite` |
    | `Role` | `reference`, `photo`, `member` |
    | `Stage` | `pre_op`, `intra_op`, `post_op` |
    | `PhotoState` | `pending`, `ready`, `failed` |
    | `EditStatus` | `ok`, `needs_review` |
    | `EditSource` | `auto`, `manual` |
    | `CaptureSource` | `exif`, `file_mtime` |
    | `ColorMethod` | `identity`, `lab_quantile_v1`, `lab_reinhard_v1` |
    | `ExportKind` | `single`, `sequence`, `composite` |

  - Models:
    - `PatientRecord`:
      - `schema_version: Literal[1] = 1`;
      - `id` (`ID_PATTERN`);
      - `name: str` (non-empty);
      - `note: str | None = None`;
      - `created_at: AwareDatetime`.
    - `ColorRecord`: `method: ColorMethod` and `params: dict[str, Any] = {}`.
    - `EditRecord`:

      | Field | Type | Rule |
      |---|---|---|
      | `transform` | `list[float]` | 9 finite values |
      | `model` | `AlignModel` | |
      | `output_size` | `tuple[int, int]` | both ≥ 1 |
      | `detail_ratio` | `float \| None = None` | |
      | `color` | `ColorRecord` | |
      | `source` | `EditSource = "auto"` | |
      | `confidence` | `float \| None = None` | |
      | `status` | `EditStatus` | |
      | `message` | `str \| None = None` | |
      | `algorithm_version` | `str` | |
      | `flip` | `Flip \| None = None` | composite cells only |

      Methods:
      - `matrix() -> tf.Matrix`;
      - `color_transform() -> LabTransform | None`, built with `LabCurves.from_params` /
        `LabAffine.from_params` (or `None` for `identity`);
      - `render_key() -> str`, the canonical JSON (`sort_keys`, no spaces) of `transform`,
        `output_size`, and `color` only. These are the fields that change pixels.
    - `PhotoRecord`:

      | Field | Type | Rule |
      |---|---|---|
      | `id` | `str` | `ID_PATTERN` |
      | `sha256` | `str` | `SHA256_PATTERN` |
      | `file` | `str` | `ORIGINAL_FILE_PATTERN` |
      | `source_name` | `str` | |
      | `width`, `height` | `int \| None = None` | |
      | `captured_at` | `datetime \| None = None` | naive or aware |
      | `captured_at_source` | `CaptureSource \| None = None` | |
      | `role` | `Role` | |
      | `stage` | `Stage \| None = None` | |
      | `stage_source` | `EditSource = "auto"` | |
      | `slot` | `Slot \| None = None` | |
      | `state` | `PhotoState = "pending"` | |
      | `error` | `str \| None = None` | |
      | `edit` | `EditRecord \| None = None` | |

    - `SequenceRecord`: `photo_ids: list[str] = []` and `gap_px: int = 0` (0–200).
    - `CompositeRecord`, with no defaults, because the service fills it from `Settings`:
      - `baseline_photo_id: str | None`;
      - `cell_size: tuple[int, int]`;
      - `gap_px: int`;
      - `gap_color: str`;
      - `slots_source: EditSource`.
    - `ExportRecord`:
      - `file: str` (`EXPORT_FILE_PATTERN`);
      - `kind: ExportKind`;
      - `created_at: AwareDatetime`.
    - `CaseRecord`:
      - `schema_version: Literal[1] = 1`;
      - `id`, `type: CaseType`, `patient_id`, `title`;
      - `output_scale: float | None`;
      - `created_at`, `updated_at: AwareDatetime`;
      - `photos: list[PhotoRecord] = []`;
      - `sequence: SequenceRecord | None`;
      - `composite: CompositeRecord | None`;
      - `exports: list[ExportRecord] = []`.

      Methods:
      - `photo(photo_id) -> PhotoRecord`, which raises `PhotoNotFoundError`;
      - `reference() -> PhotoRecord | None`;
      - `replace_photo(photo: PhotoRecord) -> CaseRecord`, matched by id;
      - `validated() -> CaseRecord`, which is `CaseRecord.model_validate(self.model_dump())`.
        `model_copy(update=…)` skips validation, so every write calls it.
  - Functions:
    - `parse_patient(data: bytes) -> PatientRecord`;
    - `parse_case(data: bytes) -> CaseRecord`;
    - `dump(record: BaseModel) -> bytes`, which is `model_dump_json(indent=2).encode()`.

**`CaseRecord` validation** (a model validator):

| Rule | Procedure | Composite |
|---|---|---|
| `output_scale` | in `OUTPUT_SCALES` | `None` |
| `sequence` / `composite` | `sequence` set, `composite` `None` | `composite` set, `sequence` `None` |
| Roles | `reference` (at most one) or `photo` | `member` only |
| `stage` | allowed | `None` |
| `slot` | `None` | each slot used at most once |
| References | every `sequence.photo_ids` entry is a photo | `baseline_photo_id` is `None` or a photo |
| Both | photo ids are unique; `edit.flip` is `None` outside composite cases | |

**Parse errors.** `parse_*` reads the JSON object first.

- `schema_version` above 1 raises
  `StorageError("This file was saved by a newer version of Dental Image Organizer; update the app to open it.")`.
- Anything else that is not a valid record raises `StorageError("The file is damaged: <reason>")`,
  where `<reason>` is the first Pydantic error's `loc` and `msg`.
- Messages never contain a path. The library adds the folder in user-facing problems.

- [ ] **Step 1: Write the failing tests**

| Test | Assertions |
|---|---|
| `test_the_spec_example_parses` | the §4.11 JSON (ids, hash, and `file` filled with valid values; `state: "ready"`) parses; its photo is a `reference` at `pre_op` with an identity `edit` |
| `test_records_round_trip` | `parse_case(dump(case)) == case` for a procedure and a composite case built in the test |
| `test_future_schema_version_asks_for_an_update` | `{"schema_version": 2, …}` → `StorageError` containing `newer version` |
| `test_damaged_json_is_a_storage_error` | parametrized: `b"{"`, `b"[]"`, a case without `title` → `StorageError` containing `damaged` |
| `test_unknown_fields_are_rejected` | an extra top-level `"notes"` field → `StorageError` |
| `test_invalid_cases_are_rejected` | parametrized, each → `StorageError`: a procedure with a composite block; a composite with `output_scale`; two references; duplicate photo ids; a sequence id that is no photo; an unknown baseline id; two photos in one slot; `file` set to `../x.jpg` or `originals/../../a.jpg`; `output_scale` 3 |
| `test_render_key_ignores_status_and_message` | two edits that differ only in `status`, `message`, `confidence`, and `source` have equal keys; a different `transform[2]` gives a different key |
| `test_edit_color_transform_round_trips` | a `LabAffine` stored as `lab_reinhard_v1` params gives a `color_transform()` whose `apply_lab` output equals the original's on a random Lab array |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_schema.py -q`
Expected: FAIL with `ModuleNotFoundError: dental_image_organizer.storage.schema`.

- [ ] **Step 3: Implement `storage/schema.py`, and import `OUTPUT_SCALES` in `services/procedure.py`**

- [ ] **Step 4: Run the tests and all gates**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dental_image_organizer/storage/schema.py src/dental_image_organizer/services/procedure.py tests/unit/test_schema.py
git commit -m "feat(storage): add the versioned case and patient schema"
```

---

### Task 3: The library folder

**Files:**

- Create: `src/dental_image_organizer/storage/library.py`
- Test: `tests/unit/test_library.py`

**Interfaces:**

- Consumes: Task 1 (`write_atomic`, errors), Task 2 (schema), and `core.naming.sanitize_component`.
- Produces:
  - Constants: `PATIENT_FILE = "patient.json"`, `CASE_FILE = "case.json"`,
    `ORIGINALS_DIR = "originals"`, `EXPORT_DIR = "export"`, `TRASH_DIR = ".trash"`.
  - `local_now() -> datetime`, which is `datetime.now().astimezone()`.
  - `folder_name(text: str) -> str`. It applies `sanitize_component`, then strips leading and
    trailing dots and spaces, and raises `InvalidNameError` if nothing is left. A leading dot
    hides a folder in Finder and could collide with `.trash`, and `..` must never become a path.
  - `CaseLocks`, with `hold(case_id: str) -> AbstractContextManager[None]`: one
    `threading.RLock` per case id, created on first use.
  - `PatientEntry(record: PatientRecord, folder: Path, cases: tuple[CaseRecord, ...])`.
  - `LibraryProblem(folder: str, message: str)`. `folder` is relative to the root and
    `/`-separated.
  - `LibraryListing(patients: tuple[PatientEntry, ...], problems: tuple[LibraryProblem, ...])`.
  - `Library(root: Path, locks: CaseLocks, *, clock: Callable[[], datetime] = local_now)`:

    | Method | Behavior |
    |---|---|
    | `root` | the library folder |
    | `listing() -> LibraryListing` | patients sorted by name (NFC, casefolded), cases newest first |
    | `patient(patient_id) -> PatientEntry` | `PatientNotFoundError` |
    | `create_patient(name: str, note: str \| None) -> PatientRecord` | new folder and `patient.json` |
    | `update_patient(patient_id, *, name: str, note: str \| None) -> PatientRecord` | renames the folder when its name changes |
    | `create_case(patient_id, kind: CaseType, title: str, *, composite: CompositeRecord \| None = None) -> CaseRecord` | a procedure gets `output_scale=1.0` and an empty sequence; a composite requires `composite` |
    | `case_dir(case_id) -> Path` | `CaseNotFoundError` |
    | `read_case(case_id) -> CaseRecord` | |
    | `update_case(case_id, change: Callable[[CaseRecord], CaseRecord]) -> CaseRecord` | holds the case lock from the read to the write; sets `updated_at`; writes `change(...).validated()` |
    | `rename_case(case_id, title: str) -> CaseRecord` | updates `title` and renames the folder, keeping its date prefix |
    | `trash_case(case_id) -> Path` | moves the case to `.trash` |
    | `originals_dir(case_id) -> Path`, `export_dir(case_id) -> Path` | created on demand |
    | `original_path(case_id, photo: PhotoRecord) -> Path` | `resolve(case_dir / photo.file)` |
    | `resolve(path: str \| Path) -> Path` | `PathOutsideLibraryError("The path is outside the library.")` |

**Rules:**

- **Folder names.**
  - A patient folder is `folder_name(name)`.
  - A case folder is `f"{created_at:%Y%m%d} {folder_name(title)}"`.
  - A name that a sibling already uses gets ` (2)`, ` (3)`, … The comparison is
    NFC-normalized and casefolded, because APFS and NTFS are case-insensitive.
- **Index.**
  - Scan `root/*/patient.json` and `root/*/*/case.json`, skipping names that start with `.`.
  - Parse only files whose `(st_mtime_ns, st_size)` changed since the last scan.
  - `case_dir` uses the index and rescans once on a miss or a stale entry, for example a folder
    renamed in Finder.
  - A folder without `patient.json` is not the app's, so it is ignored silently.
- **Problems.** A problem is reported, never raised:
  - a damaged or newer-version file: `LibraryProblem(folder, message)` with the parse message;
  - a second folder that carries an id already seen, for example a folder duplicated in Finder.
    The first in sorted order wins. The message is
    `f"This folder duplicates the case in “{other}”; remove one copy, or export it and import it as a copy."`
- **Locks.**
  - One `threading.RLock` guards every file read, write, rename, and scan. This is the Windows
    `os.replace` failure measured in Before You Start.
  - `update_case` holds the case lock for the whole change, but the I/O lock only around its
    read and its write. A slow change of one case, such as a composite replan, never blocks
    reads of another.
- **Failures.** An `OSError` while writing raises `StorageError(f"Saving the case failed: {reason}")`.
  A rename raises `StorageError(f"Renaming the folder failed: {reason}. Close any window that shows it and try again.")`.
  `reason` is `exc.strerror or type(exc).__name__`, so the message never carries the path.
- **Trash.**
  - The case folder moves to `root/.trash/<patient folder>/<case folder>`, with the clash
    suffix.
  - `patient.json` is copied into `.trash/<patient folder>/` when it is missing there, so the
    trashed case can be restored by moving it back in Finder.
- **Confinement.** `resolve` treats a relative path as relative to the root. It calls
  `Path.resolve()` and requires `is_relative_to(root.resolve())`, so a symlink that leaves the
  library is rejected too.

- [ ] **Step 1: Write the failing tests**

Use a fixed clock at `2026-10-16 14:30 +08:00`.

| Test | Assertions |
|---|---|
| `test_create_patient_and_case` | `create_patient("Alex Wang", "chart 12")` makes `root/"Alex Wang"/patient.json` with that note; `create_case(…, "procedure", "implant 16")` makes `20261016 implant 16/case.json` with `output_scale == 1.0` and an empty sequence; `listing()` holds the patient with that one case |
| `test_folder_names_are_safe_and_unique` (Review Focus 5) | parametrized `(names, expected folders)`: NFD `王小明 Café` → NFC `王小明 Café`; `"Alex/Wang"` → `"AlexWang"`; `"a:b"` → `"ab"`; `".trash"` → `"trash"`; two `"Alex Wang"` → `"Alex Wang"` and `"Alex Wang (2)"`; `"alex wang"` after `"Alex Wang"` → `"alex wang (2)"`; `".."` → `InvalidNameError` |
| `test_update_case_refreshes_the_timestamp` | `update_case` with a title change: `updated_at` equals the clock's new value and the file holds the change |
| `test_an_invalid_change_leaves_the_file_unchanged` | a change that adds a second reference raises; `case.json` bytes are unchanged |
| `test_concurrent_updates_lose_nothing` | 8 threads each `update_case` to append one photo record → 8 photos |
| `test_rename_case_and_patient_move_their_folders` | `rename_case(id, "implant 26")` → folder `20261016 implant 26`, `read_case` works; `update_patient(name="Alex Wong")` → patient folder `Alex Wong`, its cases still resolve |
| `test_trash_case_keeps_it_restorable` | the folder is at `.trash/Alex Wang/20261016 implant 16` beside a copy of `patient.json`; `read_case` → `CaseNotFoundError`; `listing()` no longer shows it |
| `test_folders_changed_in_finder_are_reported_not_fatal` (Review Focus 4) | with two good cases, plus: a third case whose `case.json` is `b"{"`; a fourth whose `schema_version` is 2; a copy of the first case folder (same id); a folder `Downloads stuff` without `patient.json` → `listing()` shows the two good cases and three problems (damaged, newer version, duplicates), and nothing for `Downloads stuff` |
| `test_a_case_folder_renamed_in_finder_is_found_again` (Review Focus 4) | `os.rename` the case folder to `20261016 renamed` → `read_case(id)` works and `case_dir(id)` is the new path |
| `test_a_case_folder_deleted_in_finder_is_not_found` | `shutil.rmtree` the case folder → `read_case` → `CaseNotFoundError` |
| `test_resolve_confines_paths_to_the_library` | a relative path inside → its absolute path; `"../outside"`, an absolute path outside, and (when `os.symlink` is permitted; skip otherwise) a symlink inside that points outside → `PathOutsideLibraryError` |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_library.py -q`
Expected: FAIL with `ModuleNotFoundError: dental_image_organizer.storage.library`.

- [ ] **Step 3: Implement `storage/library.py`**

- [ ] **Step 4: Run the tests and all gates**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dental_image_organizer/storage/library.py tests/unit/test_library.py
git commit -m "feat(storage): keep patients and cases in the library folder"
```

---

### Task 4: The render cache

**Files:**

- Create: `src/dental_image_organizer/storage/cache.py`
- Test: `tests/unit/test_cache.py`

**Interfaces:**

- Produces:
  - `cache_key(*parts: str) -> str`: the SHA-256 hex of the parts joined with `"\x00"`.
  - `BlobCache(directory: Path, max_bytes: int)` with these methods:

    | Method | Behavior |
    |---|---|
    | `get(key) -> bytes \| None` | refreshes the entry's `mtime` on a hit, which is the LRU clock |
    | `put(key, data) -> None` | stores the entry |
    | `evict() -> int` | removes the oldest entries until the total is ≤ `max_bytes`; returns the bytes removed |
    | `set_limit(max_bytes) -> None` | changes the cap |
    | `clear() -> None` | removes every entry |

  - `encode_array(array: np.ndarray) -> bytes` and `decode_array(data: bytes) -> np.ndarray`.
    Both use `np.save` / `np.load` with `allow_pickle=False`.

**Rules:**

- An entry lives at `directory/<key[:2]>/<key>`.
- `put` writes a unique temporary file and then calls `os.replace`. A `PermissionError` from the
  replace is ignored when the target exists: another thread just stored the same content.
- `evict` sorts by `mtime`. A file that cannot be deleted is skipped; on Windows a reader may
  still have it open.
- `atime` is not used: macOS and Linux mount with `relatime` or `noatime`.

- [ ] **Step 1: Write the failing tests**

| Test | Assertions |
|---|---|
| `test_put_get_and_miss` | round trip; an unknown key → `None` |
| `test_eviction_drops_the_least_recently_used_first` | three 400-byte entries with `os.utime` mtimes 1, 2, and 3; `get` the oldest; `max_bytes=1000`; `evict()` returns 400, and only the entry with mtime 2 is gone |
| `test_eviction_skips_entries_it_cannot_delete` | monkeypatch `Path.unlink` to raise `PermissionError` for one entry → `evict()` does not raise and removes the others it needs |
| `test_concurrent_puts_of_one_key` | 8 threads `put` the same key and bytes → no exception; `get` returns the bytes |
| `test_arrays_round_trip_without_pickle` | a `uint8` array of shape (1200, 1600, 3) round trips equal; an `object` array → `ValueError` |
| `test_clear_empties_the_cache` | after `clear()` every key misses |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_cache.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `storage/cache.py`**

- [ ] **Step 4: Run the tests and all gates**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dental_image_organizer/storage/cache.py tests/unit/test_cache.py
git commit -m "feat(storage): add the size-capped render cache"
```

---

### Task 5: Case archives, the instance lock, and `dio export-case` / `dio import-case`

**Files:**

- Create: `src/dental_image_organizer/storage/archive.py`
- Modify: `src/dental_image_organizer/storage/library.py` (`adopt_patient`, `install_case`, `clean_staging`)
- Create: `src/dental_image_organizer/instance.py`
- Modify: `src/dental_image_organizer/core/naming.py` (`case_export_zip_name`, `case_archive_name`)
- Modify: `src/dental_image_organizer/cli.py`
- Modify: `pyproject.toml` (add `dental_image_organizer.instance` to the `core`, `ml`, and `storage` forbidden lists)
- Modify: `README.md` (the two commands under Development)
- Test: `tests/unit/test_archive.py`, `tests/unit/test_instance.py`, `tests/unit/test_naming.py`, `tests/integration/test_cli_archive.py`

**Interfaces:**

- Consumes: Tasks 1–3, plus `filelock.FileLock`.
- Produces:
  - `core.naming`:
    - `case_export_zip_name(patient, title, generated_on: date) -> str`, which returns
      `{patient}_{title}_Export_{YYYYMMDD}.zip`;
    - `case_archive_name(patient, title, created_on: date) -> str`, which returns
      `{patient}_{title}_Archive_{YYYYMMDD}.zip`.

    Both trim like `_join`: the title first, then the patient, to 200 bytes.
  - `storage.library.Library`, new methods:
    - `adopt_patient(record: PatientRecord) -> PatientEntry`: creates a patient folder that
      keeps the record's id, name, note, and `created_at`.
    - `install_case(staged: Path, record: CaseRecord) -> CaseRecord`: moves a fully written case
      folder (`case.json` plus `originals/`) into its patient folder, under the folder rules, by
      `os.replace`.
    - `staging_dir() -> Path`: a new `root/.importing-<uuid>` folder. Staging inside the root
      keeps the final move on one file system, so it is a rename.
    - `clean_staging() -> None`: removes leftover `.importing-*` folders.
  - `storage.archive`:
    - `ConflictPolicy = Literal["ask", "skip", "copy"]`;
    - `MAX_ORIGINAL_BYTES = 200 * 1024 * 1024`;
    - `MAX_JSON_BYTES = 16 * 1024 * 1024`;
    - `write_case_archive(case_dir: Path, out: Path) -> None`;
    - `ImportOutcome(status: Literal["imported", "skipped"], case_id: str, patient_id: str, title: str)`;
    - `import_case_archive(library: Library, archive: Path, *, on_conflict: ConflictPolicy = "ask") -> ImportOutcome`.
  - `instance`:
    - `InstanceInfo(port: int, pid: int, version: str, token: str | None)`, frozen;
    - `InstanceLock(path: Path)`, with `try_acquire() -> bool` (a `FileLock` with timeout 0),
      `release()`, and `held: bool`;
    - `read_instance(path: Path) -> InstanceInfo | None` (a missing or damaged file gives
      `None`);
    - `write_instance(path: Path, info: InstanceInfo) -> None`, through `write_atomic`.

**Rules:**

- **`write_case_archive`.**
  - Members: `patient.json` from `case_dir.parent`, `case.json`, and each original that a photo
    references, once.
  - The JSON is deflated. Originals are stored, because they are already compressed.
  - The zip is written to a temporary file beside `out` and then replaced, so a crash never
    leaves half a zip.
- **`import_case_archive` checks**, in this order. Any failure raises `InvalidArchiveError` and
  writes nothing:
  1. the file is a zip;
  2. every member is `patient.json`, `case.json`, or matches `ORIGINAL_FILE_PATTERN`. This also
     rejects `../`, absolute paths, and folders. The message names the first other member;
  3. no name repeats, and no member's header `file_size` exceeds its limit;
  4. both JSON files parse. A newer `schema_version` keeps the "newer version" wording;
  5. `case.patient_id` equals the patient's id;
  6. every photo's file is present, and its SHA-256 equals `photo.sha256`. Otherwise the message
     is `"The archive is damaged: a photo does not match its record."`
- **Conflicts.** When the case id is already in the library:
  - `ask` raises `CaseExistsError("This case is already in the library.", details={"case_id": …, "title": …})`;
  - `skip` returns `status="skipped"`;
  - `copy` imports under `new_id()`; the photos keep their ids.
- **Patient.** A patient id that is already in the library is reused. Otherwise
  `adopt_patient`.
- **Install.**
  1. Extract into `staging_dir()`.
  2. Write `case.json` with `exports=[]`. Exports are regenerated, and their files are not in
     the archive.
  3. Call `install_case`.
  4. On any failure, remove the staging folder.
- **CLI.**
  - `dio export-case CASE_DIR ZIP`:
    - `CASE_DIR` must hold `case.json`, and its parent `patient.json`. Otherwise exit 1 with
      `error: <CASE_DIR> is not a case folder.`
    - An existing `ZIP` is never overwritten: exit 1 with `error: <ZIP> already exists.`
    - Success prints `Wrote <ZIP>`.
  - `dio import-case ZIP [--on-conflict skip|copy]`:
    - Reads `SettingsStore(app_dirs().settings_file).get().library_root`.
    - Holds `InstanceLock(app_dirs().instance_lock)` while it imports. If another process holds
      the lock, exit 1 with
      `error: Dental Image Organizer is running; import the case from the app, or quit it first.`
      The lock guards the library (§4.3).
    - `CaseExistsError` without the option exits 1 with
      `error: This case is already in the library: <title>. Rerun with --on-conflict skip or --on-conflict copy.`
    - Success prints `Imported "<title>"` or `Skipped "<title>": it is already in the library.`
    - `DioError` and `OSError` exit 1 with `error: <message>`.

- [ ] **Step 1: Write the failing tests**

`test_archive.py` builds a case with two JPEG originals in one library. It writes their bytes
into `originals/` and the records directly.

| Test | Assertions |
|---|---|
| `test_archive_round_trip` | `write_case_archive` → `import_case_archive` into a second, empty library → `imported`; `read_case` equals the original except `exports == []` and `updated_at`; original bytes are equal; the patient keeps its id, name, and note |
| `test_archive_holds_only_referenced_originals` | an extra unreferenced file in `originals/` is not a member |
| `test_conflict_policies` | importing twice: `ask` → `CaseExistsError` with `details["case_id"]` and `details["title"]`; `skip` → `skipped`, still one case; `copy` → a second case with a new id and the folder `… (2)` |
| `test_invalid_archives_change_nothing` | parametrized, each → `InvalidArchiveError`, after which the second library has no patient and no `.importing-*` folder: not a zip; an extra member `../evil.jpg`; an extra `notes.txt`; an original whose bytes were changed; a missing original; `case.patient_id` of another patient; `schema_version` 2 (message contains `newer version`) |
| `test_clean_staging_removes_leftovers` | a leftover `.importing-x` folder is removed |

`test_instance.py`:

- `test_the_lock_is_exclusive`: a second `InstanceLock` on the same path → `try_acquire()` is
  `False` until the first `release()`.
- `test_instance_info_round_trips_and_tolerates_damage`: round trip; `b"{"` → `None`.

`test_naming.py`:

- `test_case_zip_names`: `case_export_zip_name("Alex Wang", "implant 16", date(2026, 10, 16)) == "Alex Wang_implant 16_Export_20261016.zip"`.
- The archive name equivalent, and a 300-byte title is trimmed to ≤ 200 bytes with the patient
  kept.

`test_cli_archive.py` sets `DIO_APP_DIR=tmp_path/"app"` and runs `CliRunner`:

- `test_export_and_import_case_round_trip`.
- `test_export_case_never_overwrites`.
- `test_import_conflict_names_the_options`: the second import exits 1 with `--on-conflict skip`
  in the output.
- `test_import_refuses_while_the_app_runs`: an `InstanceLock` held by the test → exit 1, output
  contains `quit it first`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_archive.py tests/unit/test_instance.py tests/unit/test_naming.py tests/integration/test_cli_archive.py -q`
Expected: FAIL with `ModuleNotFoundError` and `No such command 'export-case'`.

- [ ] **Step 3: Implement the library additions, `archive.py`, `instance.py`, the names, and the two commands**

- [ ] **Step 4: Run the tests and all gates**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dental_image_organizer/storage src/dental_image_organizer/instance.py src/dental_image_organizer/core/naming.py src/dental_image_organizer/cli.py pyproject.toml README.md tests/unit/test_archive.py tests/unit/test_instance.py tests/unit/test_naming.py tests/integration/test_cli_archive.py
git commit -m "feat: export and import case archives, and guard the library with an instance lock"
```

---

### Task 6: Background jobs

**Files:**

- Create: `src/dental_image_organizer/services/jobs.py`
- Test: `tests/unit/test_jobs.py`

**Interfaces:**

- Consumes: `errors` (`DioError`, `JobNotFoundError`) and `storage.schema.new_id`.
- Produces:
  - `JobState = Literal["queued", "running", "done", "failed"]`.
  - `JobError(code: str, message: str)`, frozen.
  - `JobSnapshot`, frozen, with these fields:
    - `id`, `kind: str`, `key: str | None`, `state: JobState`;
    - `done: int`, `total: int`, `message: str | None`;
    - `result: Mapping[str, Any] | None`, `error: JobError | None`;
    - `version: int`.

    It has a property `finished`.
  - `JobProgress`, a Protocol: `__call__(self, done: int, total: int, message: str | None = None) -> None`.
  - `JobFn = Callable[[JobProgress], Mapping[str, Any] | None]`.
  - `JobManager(executor: Executor, *, keep_finished: int = 200, on_finish: Callable[[JobSnapshot], None] | None = None)`:

    | Method | Behavior |
    |---|---|
    | `submit(kind, fn, *, key=None, total=0) -> JobSnapshot` | queues the job; returns its first snapshot |
    | `get(job_id) -> JobSnapshot` | `JobNotFoundError` for an unknown or forgotten id |
    | `active(key: str \| None = None) -> bool` | a queued or running job, of that key or any |
    | `active_any(keys: Iterable[str]) -> bool` | one of those keys is active |
    | `wait(job_id, timeout: float \| None = None) -> JobSnapshot` | blocks until finished; `TimeoutError` |
    | `async watch(job_id, *, interval=0.1) -> AsyncIterator[JobSnapshot]` | yields each new version until finished, the last one included |
    | `cancel_queued() -> int` | fails every queued job with `JobError("cancelled", "The app was closing.")` |

**Rules:**

- **Keys.** Jobs with one key run one at a time, in submission order. When one finishes, the
  manager hands the next of that key to the executor, so a worker never blocks waiting for its
  turn. Jobs without a key, or with different keys, run in parallel.
- **Versions.** `version` increases on every change: queued, running, progress, and finished.
- **Failures.**

  | Exception from `fn` | `JobError` | Logged |
  |---|---|---|
  | `DioError` | its `code` and `str(exc)` | `job_failed` with `code` only |
  | `OSError` | `io_error` and `exc.strerror or type(exc).__name__`, never the path | `job_failed` with `error=strerror` |
  | any other | `internal_error` and `"An unexpected error occurred; see the log file."` | `log.exception("job_failed")` |

- **Log context.** The body runs inside `bound_contextvars(job_id=…, job_kind=…)`.
- **`on_finish`** runs after every job; the context uses it to evict the render cache. An
  exception from it is logged and never changes the job.
- **Retention.** Only the newest `keep_finished` finished jobs are kept.

- [ ] **Step 1: Write the failing tests**

Use a `ThreadPoolExecutor(max_workers=2)`.

| Test | Assertions |
|---|---|
| `test_a_job_reports_progress_and_its_result` | `fn` calls `progress(1, 2)`, then `progress(2, 2, "two")`, and returns `{"n": 2}` → `wait` gives `done`, `done == total == 2`, `message == "two"`, `result == {"n": 2}` |
| `test_domain_errors_keep_their_code` | `CaseNotFoundError("gone")` → `failed`, `error == JobError("case_not_found", "gone")` |
| `test_unexpected_errors_are_logged_but_not_shown` | `RuntimeError("Alex Wang")` → `internal_error` with the generic message; `capture_logs` has a `job_failed` entry |
| `test_os_errors_never_carry_the_path` | `FileNotFoundError(2, "No such file", "/lib/Alex Wang/x.jpg")` → `JobError("io_error", "No such file")`; no captured log entry contains `Alex Wang` |
| `test_jobs_of_one_key_run_in_order` | A (key `c1`) waits on an `Event`; B (key `c1`) is `queued` while A runs; after `set()`, both are `done` and B started after A finished |
| `test_jobs_of_different_keys_run_together` | two jobs with keys `c1` and `c2` both wait on one `threading.Barrier(2, timeout=5)` → both `done` |
| `test_active_reports_queued_and_running_jobs` | `active("c1")` while A runs; `active_any(["c2", "c1"])`; both `False` after it finishes |
| `test_watch_yields_every_state_until_finished` | in `asyncio.run`, collect `watch(id)`: versions strictly increase; the last snapshot is `done` |
| `test_finished_jobs_are_forgotten_beyond_the_limit` | `keep_finished=2`, three jobs → `get(first)` → `JobNotFoundError` |
| `test_on_finish_never_breaks_a_job` | an `on_finish` that raises → the job is still `done`; it was called once per job |
| `test_cancel_queued_fails_waiting_jobs` | B queued behind a blocked A → `cancel_queued() == 1`; B is `failed` with code `cancelled` |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_jobs.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/jobs.py`**

- [ ] **Step 4: Run the tests and all gates**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dental_image_organizer/services/jobs.py tests/unit/test_jobs.py
git commit -m "feat(services): run background jobs with per-case ordering and progress"
```

---

### Task 7: The AI runtime and flip learning

**Files:**

- Create: `src/dental_image_organizer/ml/training.py`
- Modify: `src/dental_image_organizer/services/headless_composite.py` (use `ml.training`)
- Create: `src/dental_image_organizer/services/flips.py`, `src/dental_image_organizer/services/ai.py`
- Modify: `tests/support/fake_model.py` (`install_fake_model`)
- Test: `tests/unit/test_flips.py`, `tests/unit/test_ai_runtime.py`

**Interfaces:**

- Consumes: Tasks 1 (`AppDirs`, `Settings`, `write_atomic`); `ml.samples`, `ml.classifier`,
  `ml.embedder`, and `ml.model_store`.
- Produces:
  - `ml.training`:
    - `CLASSIFIER_FILE = "model.json"`, moved here; `headless_composite` imports it;
    - `learn(directory: Path, pairs: EmbeddingPair, slots: Sequence[Slot], *, photo_ids: Sequence[str], case_id: str, source: SampleSource, model_hash: str, timestamp: float) -> bool`.
      This is the body of `headless_composite._learn`, which now calls it: append, keep this
      model's samples, train, then save, or unlink a stale `model.json`;
    - `retrain(directory: Path, model_hash: str) -> bool`: the same without appending.
  - `services.flips`:
    - `FLIPS_FILE = "flips.json"`, `MIN_CONFIRMATIONS = 3`, `RECENT_CONFIRMATIONS = 5`.
    - `FlipConfirmation(case_id: str, slot: Slot, flip: Flip, timestamp: float)`, frozen.
    - `FlipStore(path: Path)`:
      - `load() -> tuple[FlipConfirmation, ...]`;
      - `record(case_id: str, flips: Mapping[Slot, Flip], *, pins: Mapping[Slot, Flip], timestamp: float) -> tuple[FlipConfirmation, ...]`.
    - `learned_flips(history: Sequence[FlipConfirmation], start: Mapping[Slot, Flip]) -> dict[Slot, Flip]`.
    - `effective_flips(settings: Settings, history: Sequence[FlipConfirmation]) -> dict[Slot, Flip]`:
      pin, else learned, else `settings.flip_start`.
    - `FlipStatus(effective: Flip, start: Flip, learned: Flip | None, pinned: bool, confirmations: int)`.
    - `flip_status(settings, history) -> dict[Slot, FlipStatus]`.
  - `services.ai`:
    - `AssignReason = Literal["untrained", "no_model", "stale_model", "damaged_classifier", "embedding_failed"]`.
    - `ClassifierStatus(trained: bool, counts: dict[Slot, int], stale_samples: int, damaged: str | None, model_available: bool)`.
    - `AiRuntime(dirs: AppDirs, spec: ModelSpec = DEFAULT_MODEL, *, http_client: Callable[[], httpx.Client] | None = None)`:

      | Member | Behavior |
      |---|---|
      | `spec`, `flips: FlipStore` | `FlipStore(dirs.classifier / FLIPS_FILE)` |
      | `lock: threading.RLock` | every write under `dirs.classifier` holds it |
      | `model_path() -> Path \| None` | see below |
      | `embedder() -> Embedder \| None` | one shared instance per verified file; onnxruntime's `run` is thread-safe |
      | `download(progress: Callable[[int, int], None] \| None = None) -> None` | `download_model(spec, dirs.models, client=…, progress=…)`; then verifies again |
      | `classifier() -> tuple[SlotClassifier \| None, AssignReason \| None]` | see below |
      | `status() -> ClassifierStatus` | see below |
      | `learn(pairs, slots, *, photo_ids, case_id, source) -> bool` | `training.learn(dirs.classifier, …, model_hash=spec.sha256, timestamp=time.time())` under `lock` |
      | `retrain() -> bool` | under `lock` |
      | `record_flips(case_id: str, flips: Mapping[Slot, Flip], *, pins: Mapping[Slot, Flip]) -> None` | `flips.record(…, timestamp=time.time())` under `lock`; export and teaching call this, never `FlipStore.record` directly |

  - `tests/support/fake_model.py`: `install_fake_model(models_dir: Path, seed: int = 0) -> ModelSpec`.
    It writes `models_dir/"fake.onnx"` and returns
    `ModelSpec("fake.onnx", "https://unused.test", sha, size)`.

**Rules:**

- **`FlipStore`.**
  - The file is `{"schema_version": 1, "confirmations": [{"case_id", "slot", "flip", "timestamp"}, …]}`,
    written with `write_atomic`.
  - `record` first drops every entry of `case_id` (amendment 5). Then it appends one entry per
    slot of `flips` that is not in `pins`.
  - A damaged file raises
    `StorageError("The flip history is damaged; move flips.json out of the classifier folder to start over.")`.
- **`learned_flips`** replays the history per slot. This is the one algorithm that a signature
  cannot pin down:

  ```python
  learned: dict[Slot, Flip] = {}
  for slot in SLOTS:
      current, seen = start[slot], []
      for confirmation in (c for c in history if c.slot == slot):
          seen.append(confirmation.flip)
          if len(seen) < MIN_CONFIRMATIONS:
              continue
          counts = Counter(seen[-RECENT_CONFIRMATIONS:])
          top = max(counts.values())
          winners = [flip for flip, n in counts.items() if n == top]
          if len(winners) == 1:  # a tie keeps the current default (spec §4.7.2)
              current = winners[0]
      if len(seen) >= MIN_CONFIRMATIONS:
          learned[slot] = current
  return learned
  ```

- **`model_path`** calls `cached_model` once. It remembers the verified file's
  `(st_size, st_mtime_ns)` and verifies again only when they change; hashing 24 MB on every job
  would be wasted work. A `ModelUnavailableError` (an unreadable cache) gives `None` and a
  warning log.
- **`classifier()`** checks these in order:
  1. no `model.json` → `(None, "untrained")`;
  2. no verified model → `(None, "no_model")`;
  3. `load_classifier` raises `StorageError` → `(None, "damaged_classifier")`;
  4. it returns `None` (another model's hash) → `(None, "stale_model")`;
  5. otherwise `(classifier, None)`.

  The loaded classifier is reused while `model.json`'s `(st_size, st_mtime_ns)` stay the same.
- **`status()`.**
  - `counts` are this model's samples; `stale_samples` are the rest.
  - A `StorageError` from the sample store gives zero counts, `trained=False`, and
    `damaged=<message>`.
  - `trained` is whether `classifier()` returned one.

- [ ] **Step 1: Move `_learn` into `ml/training.py` (refactor; existing tests stay green)**

Run: `uv run pytest tests/integration/test_cli_composite.py -q`
Expected: PASS, unchanged.

- [ ] **Step 2: Write the failing tests**

`test_flips.py` (spec §8.1 "flip learning", amendment 5). Each confirmation uses its own
`case_id` unless stated otherwise.

| Test | Assertions |
|---|---|
| `test_default_changes_only_after_three_confirmations` | start `upper_right=horizontal`; two `none` → `learned_flips` has no `upper_right`; a third → `none` |
| `test_learned_value_follows_the_last_five` | `none` ×3, then `vertical` ×3 → `vertical`, since the last five hold three `vertical` |
| `test_a_tie_keeps_the_current_default` | start `horizontal`; `none`, `vertical`, `horizontal` → `horizontal` (a three-way tie); then `none` → `none`; then `vertical` → `none` (a `none`/`vertical` tie keeps `none`) |
| `test_a_pinned_slot_never_changes_and_records_nothing` | with `pins={"upper_right": "vertical"}`, record `none` five times → `effective_flips` gives `vertical`; the store holds no `upper_right` entry |
| `test_recording_a_case_again_replaces_it` | record case `c1` three times → one entry per slot |
| `test_precedence_is_pin_then_learned_then_start` | `upper_right`: start `horizontal`, three `none` confirmations, pinned `vertical` → `vertical`; without the pin → `none`; `lower_left`, with no history → its start value |
| `test_damaged_flip_history_is_a_storage_error` | `b"{"` → `StorageError` |

`test_ai_runtime.py` builds `AppDirs` under `tmp_path`.

| Test | Assertions |
|---|---|
| `test_no_model_means_no_embedder` | `model_path() is None` and `embedder() is None` |
| `test_the_model_is_verified_once` | after `install_fake_model`: monkeypatch `services.ai.cached_model` with a counting wrapper; three `model_path()` calls → counted once; `embedder()` is the same object twice |
| `test_a_changed_model_file_is_verified_again` | overwrite the file with shorter bytes → `model_path() is None` |
| `test_download_reports_progress_and_verifies` | `http_client` returns an `httpx.Client(transport=httpx.MockTransport(…))` serving the fake model's bytes → `download(progress)` reaches `(size, size)`; `model_path()` is not `None` |
| `test_three_learned_sets_train_the_classifier` | `learn` `synthetic_pairs(3, 1)` (27 samples, random photo ids) → `True`; `classifier()[0]` is not `None`; `status()` is trained with every count 3 |
| `test_classifier_reasons` | parametrized: no `model.json` → `untrained`; `model.json` present but the model missing → `no_model`; `{"model_hash": "older"}` → `stale_model`; `b"{"` → `damaged_classifier` |
| `test_concurrent_learning_keeps_every_sample` | two threads each `learn` nine samples with distinct photo ids → 18 samples stored |

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_flips.py tests/unit/test_ai_runtime.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement `services/flips.py`, `services/ai.py`, and `install_fake_model`**

- [ ] **Step 5: Run the tests and all gates**

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/dental_image_organizer/ml/training.py src/dental_image_organizer/services tests/support/fake_model.py tests/unit/test_flips.py tests/unit/test_ai_runtime.py
git commit -m "feat(services): learn per-slot flips and share the AI model and classifier"
```

---

### Task 8: The app context, intake, and originals

**Files:**

- Create: `src/dental_image_organizer/services/context.py`
- Create: `src/dental_image_organizer/services/intake.py`
- Create: `src/dental_image_organizer/services/originals.py`
- Create: `tests/support/app.py`
- Modify: `tests/conftest.py` (the `ctx` fixture)
- Test: `tests/unit/test_context.py`, `tests/unit/test_intake.py`

**Interfaces:**

- Consumes: Tasks 1–7.
- Produces:
  - `services.context`:
    - `WORKERS = min(4, os.cpu_count() or 1)`.
    - `AppContext`, a dataclass with these fields:

      | Field | Type |
      |---|---|
      | `dirs` | `AppDirs` |
      | `settings` | `SettingsStore` |
      | `case_locks` | `CaseLocks` |
      | `cache` | `BlobCache` (at `dirs.renders`) |
      | `jobs` | `JobManager` (`on_finish` evicts the cache) |
      | `ai` | `AiRuntime` |
      | `references` | `originals.ReferenceCache` |
      | `executor` | `ThreadPoolExecutor` |

      Methods:
      - `library() -> Library`;
      - `apply_settings(new: Settings) -> Settings`;
      - `close() -> None`.
    - `create_context(dirs: AppDirs, *, model_spec: ModelSpec = DEFAULT_MODEL, workers: int = WORKERS, http_client: Callable[[], httpx.Client] | None = None) -> AppContext`.
  - `services.intake`:
    - `MAX_UPLOAD_BYTES = 200 * 1024 * 1024`, `SNIFF_BYTES = 4096`, `COPY_CHUNK = 1 << 20`.
    - `UploadSkipReason = Literal["video", "raw", "sidecar", "unknown", "duplicate", "too_large"]`.
    - `Upload(name: str, stream: BinaryIO, last_modified: datetime | None = None)`.
    - `Skipped(name: str, reason: UploadSkipReason, message: str)`.
    - `Staged(name: str, sha256: str, fmt: ImageFormat, file_name: str, last_modified: datetime | None)`.
      `file_name` is `<sha256[:16]>.<ext>`.
    - `UploadOutcome(job_id: str | None, added: tuple[str, ...], skipped: tuple[Skipped, ...])`.
    - `display_name(raw: str) -> str`.
    - `stage_upload(upload: Upload, directory: Path) -> Staged | Skipped`.
    - `add_photos(ctx, case_id, uploads: Sequence[Upload], *, role: Role) -> tuple[list[str], list[Skipped]]`.
  - `services.originals`:
    - `WORKING_LONG_EDGE = 1600`.
    - `decode_original(ctx, case_id, photo) -> DecodedImage`.
    - `working_copy(ctx, case_id, photo) -> RGBImage`: cache key `("working", sha256)`; a miss
      decodes and calls `remember_working`.
    - `remember_working(ctx, sha256, rgb: RGBImage) -> RGBImage`.
    - `photo_embedding(ctx, case_id, photo, embedder: Embedder) -> EmbeddingPair`: cache key
      `("embedding", sha256, embedder.model_hash)`.
    - `ReferenceCache(capacity: int = 2)`, with
      `get(ctx, case_id, photo) -> PreparedReference`, keyed by `(case_id, photo.sha256)`.
      It holds about 10 MB per reference.
  - `tests/support/app.py`:
    - `ORIGIN = "http://127.0.0.1:8765"`;
    - `make_context(root: Path, *, model_spec: ModelSpec | None = None, http_client: Callable[[], httpx.Client] | None = None, **settings: Any) -> AppContext`.
      It uses `AppDirs(root/"config", root/"data", root/"cache", root/"log")`, writes the
      settings with `library_root=root/"library"` plus the overrides, and uses two workers. A
      fake model for it goes to `install_fake_model(root/"cache"/"models")`;
    - `jpeg(rgb, taken: str | None = None, *, gps: bool = False) -> bytes`. `taken` is the EXIF
      `DateTimeOriginal` (`"2026:10:16 10:00:00"`). `gps` adds a GPS IFD with a latitude;
    - `upload(name: str, data: bytes, last_modified: datetime | None = None) -> Upload`;
    - `wait(ctx, job_id: str) -> JobSnapshot`: waits up to 120 s and asserts `done`;
    - `view_set(seed: int = 70, count: int = 9) -> list[tuple[str, bytes]]`. These are
      `IMG_0001.jpg`, … built from `flip_pixels(make_view(slot, seed + i, angle=3.0), DEFAULT_FLIPS[slot])`
      in row-major order, taken at `2026:10:16 10:00:10` + i seconds. A tenth photo repeats
      slot 1 with seed `seed + 50`;
    - `procedure_set(seed: int = 61, *, reference_scale: float = 1.0, gps: bool = False) -> ProcedureSet`,
      with `.reference`, `.intra`, `.post` (JPEG bytes) and `.truth` (the `intra` photo's
      transform, normalized, as 9 floats):
      - the scene is `make_scene(seed, 2400, 1600)`;
      - `intra` is `make_pair(scene, about_center(2400, 1600, 1.0, 4.0), reference_scale=reference_scale)`
        taken `2026:10:16 10:00:00`; `reference` is its graded crop, taken `2026:10:16 09:00:00`;
      - `post` is `make_pair(scene, about_center(2400, 1600, 0.9, -6.0))` taken
        `2026:10:20 10:00:00`;
      - `gps` gives every JPEG a GPS IFD.
  - `tests/conftest.py`: a fixture `ctx(tmp_path)` that yields `make_context(tmp_path)` and
    calls `close()` afterwards.

**Rules:**

- **Imports.** `context.py` imports `originals` (and, from Task 13, `teach`). Every service
  module therefore imports `AppContext` only under `TYPE_CHECKING`, with
  `from __future__ import annotations`.
- **Wording.** In the rules of Tasks 8–16, `settings` means `ctx.settings.get()` at the moment
  of the call.
- **`create_context`.**
  1. Load the settings; a `StorageError` propagates.
  2. Create the library root.
  3. Empty `dirs.exports` and `dirs.teach`. Zips and teaching sessions never outlive a run
     (§3.5, §4.9).
  4. Call `library.clean_staging()` and evict the cache.
  5. The executor's `initializer` calls `cv2.setNumThreads(1)`.
- **`apply_settings`.**
  - When `library_root` changes while `jobs.active()` →
    `JobRunningError("Wait for the running jobs to finish before moving the library.")`.
  - Otherwise save, create a `Library` on the new root (and the folder), then call
    `cache.set_limit(...)` and `cache.evict()`.
- **`close`** calls `jobs.cancel_queued()`, then `executor.shutdown(wait=True)`.
- **`display_name`.**
  - Keep the last path component after `/` or `\`.
  - Normalize to NFC, remove control characters, and keep at most 255 characters.
  - An empty result becomes `"photo"`. Upload names never become paths.
- **`stage_upload`.**
  1. Read `SNIFF_BYTES` and call `sniff_format`. `UnsupportedFormatError` gives
     `Skipped(name, exc.reason, str(exc))`. Nothing is written for a skipped file, and the rest
     of a video is never read.
  2. Copy the head and the rest to `directory/.<uuid>.part`, hashing and counting as it goes.
  3. Past `MAX_UPLOAD_BYTES`, delete the part and return
     `Skipped(name, "too_large", "Files over 200 MB are not accepted.")`.
  4. `os.replace` the part to `file_name`; the extensions are `heic`, `jpg`, and `png`. An
     existing file of that name holds the same content.
- **`add_photos`.**
  1. Stage each upload into `library.originals_dir(case_id)`.
  2. One `update_case` adds a `pending` `PhotoRecord` for each new SHA-256:
     - `captured_at` is the upload's `last_modified`, or the local time now;
     - `captured_at_source="file_mtime"`, until decoding finds EXIF;
     - `stage` and `slot` are `None`.
  3. A SHA-256 that is already in the case, or earlier in the same batch, gives
     `Skipped(name, "duplicate", "This photo is already in the case.")`.

- [ ] **Step 1: Write the failing tests**

`test_intake.py`:

| Test | Assertions |
|---|---|
| `test_photos_are_stored_by_content_and_recorded_pending` | two JPEGs, with `last_modified` given for one → two `pending` records whose `file` is `originals/<sha[:16]>.jpg`, whose bytes match, and whose `source_name` is kept; that one's `captured_at` equals `last_modified` with `captured_at_source == "file_mtime"` |
| `test_unsupported_files_are_skipped_with_their_reason` | `.MOV` (`ftypqt`), `.AAE` (`<?xml`), DNG (`II*\0`), and text → reasons `video`, `sidecar`, `raw`, `unknown`; `originals/` stays empty, with no `.part` file either |
| `test_dropping_the_same_photo_again_is_reported_as_duplicate` (Review Focus 1) | add A and B; then add A, C, and C again → only C is added; A and the second C are `duplicate`; the case holds three photos with distinct SHA-256 |
| `test_files_over_the_limit_are_skipped` | monkeypatch `MAX_UPLOAD_BYTES = 1000` → `too_large`; no `.part` file left |
| `test_upload_names_never_become_paths` | names `../../evil.jpg` and `C:\\x\\y.jpg` → `source_name` `evil.jpg` and `y.jpg`; files only under `originals/` |

`test_context.py`:

| Test | Assertions |
|---|---|
| `test_startup_cleans_temporary_folders` | files left in `cache/exports`, in `cache/teach/<sid>`, and a `library/.importing-x` folder → all gone after `create_context`; the library root exists |
| `test_moving_the_library_waits_for_jobs` | a job blocked on an `Event` → `apply_settings(library_root=other)` → `JobRunningError`; after the job ends, it succeeds and `library().root == other` |
| `test_cache_limit_follows_the_settings` | `apply_settings(cache_max_gb=…)` changes the cache's limit and evicts |
| `test_working_copies_and_references_are_cached` | `working_copy` twice decodes once (monkeypatch `services.originals.decode` with a counting wrapper), and its long edge is 1600; `ctx.references.get` twice calls `prepare_reference` once (the same kind of wrapper on `services.originals.prepare_reference`) |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_intake.py tests/unit/test_context.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `context.py`, `intake.py`, `originals.py`, and the test support**

- [ ] **Step 4: Run the tests and all gates**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dental_image_organizer/services tests/support/app.py tests/conftest.py tests/unit/test_intake.py tests/unit/test_context.py
git commit -m "feat(services): add the app context and content-addressed photo intake"
```

---

### Task 9: Procedure cases in the library

**Files:**

- Modify: `src/dental_image_organizer/services/procedure.py` (extract `fit_colors`)
- Create: `src/dental_image_organizer/services/procedure_case.py`
- Test: `tests/unit/test_procedure_case.py`

**Interfaces:**

- Consumes: Task 6 (`JobProgress`), Task 8 (`AppContext`, `add_photos`, `stage_upload`,
  `originals.*`), and
  `services.procedure` (`compute_edit`, `output_size`).
- Produces:
  - `services.procedure.fit_colors(photo_working: RGBImage, transform: tf.Matrix, reference: PreparedReference) -> LabCurves`.
    This is the color-fitting half of `compute_edit`, which now calls it.
  - `services.procedure_case`:
    - Constants: `PROCEDURE_ALGORITHM = "align-1"`, `SUGGEST_AT_DETAIL_RATIO = 1.5`,
      `SEQUENCE_SIZE = (2, 10)`, `MAX_GAP_PX = 200`.
    - Case operations:

      | Function | Returns |
      |---|---|
      | `upload(ctx, case_id, uploads)` | `UploadOutcome` |
      | `set_reference(ctx, case_id, upload)` | `UploadOutcome` |
      | `process_photos(ctx, case_id, photo_ids: Sequence[str], progress: JobProgress)` | `dict[str, Any]` (the job body) |
      | `apply_manual_edit(ctx, case_id, photo_id, transform: Sequence[float], model: AlignModel)` | `CaseRecord` |
      | `reset_edit(ctx, case_id, photo_id)` | `CaseRecord` |
      | `set_stage(ctx, case_id, photo_id, stage: Stage)` | `CaseRecord` |
      | `set_output_scale(ctx, case_id, scale: float)` | `CaseRecord` |
      | `set_sequence(ctx, case_id, photo_ids: Sequence[str], gap_px: int)` | `CaseRecord` |

    - Pure helpers, which later tasks reuse:

      | Function | Returns |
      |---|---|
      | `auto_stages(case)` | `dict[str, Stage \| None]` |
      | `suggested_output_scale(case)` | `float \| None` |
      | `validate_transform(values: Sequence[float], model: AlignModel, output_size: tuple[int, int])` | `tf.Matrix` |
      | `sequence_photos(case)` | `list[PhotoRecord]` |
      | `edit_from_alignment(edit: ProcedureEdit, size: tuple[int, int])` | `EditRecord` |
      | `reference_edit(size: tuple[int, int])` | `EditRecord` |

**Rules:**

- Every operation first checks `case.type == "procedure"`. Otherwise it raises
  `InvalidRequestError("This is a composite case.")`.
- **`upload`.** Call `add_photos(role="photo")`. When anything was added, submit
  `process_photos` as kind `process` with key `case_id` and `total=len(added)`.
- **`set_reference`.**
  1. Stage the file. A skip returns `UploadOutcome(None, (), (skipped,))`.
  2. In one `update_case`:
     - if a photo with that SHA-256 exists, it becomes the reference (`role`, `stage="pre_op"`,
       `stage_source="auto"`). Otherwise a new `pending` reference record is added;
     - a previous, different reference is removed. Its original is deleted when no record uses
       the file;
     - every other photo gets `edit=None`. Manual edits were made against the old reference
       frame, so they no longer apply.
  3. Submit kind `reference`, key `case_id`, for the reference plus every other photo, the
     failed ones included.
- **`process_photos`.** Photos deleted meanwhile are skipped.
  1. **The reference first,** if it is in `photo_ids` or still `pending`:
     - decode it and call `remember_working`;
     - set `width`, `height`, and `captured_at`. EXIF gives `captured_at_source="exif"`;
       otherwise the provisional time stays;
     - set `edit=reference_edit(output_size(size, case.output_scale))`.
     - `OutputTooLargeError` or a decode failure makes it `failed`, and the other photos stay
       without an edit.
  2. **The other photos,** each under `bound_contextvars(photo_id=…)`:
     - decode, set the metadata the same way, and `remember_working`;
     - if the reference is ready, `compute_edit` against `ctx.references.get(...)` and store
       `edit_from_alignment(...)`;
     - apply each photo with its own `update_case`, which also re-applies `auto_stages` to every
       photo whose `stage_source` is `auto`;
     - catch `CorruptImageError`, `OSError`, and any other `Exception` as `headless._process_photo`
       does. Such a photo becomes `failed` with `error`, and the batch goes on.
  3. Call `progress(i, n)` after each photo.

  The result is `{"photos": [{"photo_id", "state", "status", "error"}, …]}`.
- **`edit_from_alignment`.**
  - `transform` is the flattened 3 × 3 matrix; `model`, `confidence`, `status`, `message`, and
    `detail_ratio` come from `edit.align`.
  - `color` is `lab_quantile_v1` with `curves.to_params()`, or `identity` without curves.
- **`reference_edit`.** The identity transform, `similarity`, `detail_ratio=1.0`, `identity`
  color, `confidence=1.0`, and `ok`.
- **`auto_stages`.**
  - The reference is `pre_op`.
  - D0 is the earliest `captured_at.date()` of the other ready photos. That date is the wall date
    as recorded, like the file names (§4.9).
  - On D0 → `intra_op`; later → `post_op`. A photo without a time → `None`.
- **`validate_transform`** raises `InvalidEditError` when any of these holds:
  - there are not 9 values, or a value is not finite;
  - `abs(det) < 1e-12`;
  - `model == "similarity"` and the last row is not `[0, 0, 1]` (tolerance 1e-9);
  - mapping the output frame's normalized corners `(0, 0)`, `(1, 0)`, `(1, h/w)`, `(0, h/w)`
    (with `(w, h) = output_size`) through the inverse matrix gives a homogeneous `w ≤ 0`, or a
    quadrilateral that is not convex: the cross products of consecutive edges do not all share
    one sign.
- **`apply_manual_edit`.**
  - The photo must be a `ready` non-reference photo, and the reference must be ready. Otherwise
    `NotReadyError("Set the reference first.")`.
  - Fit the colors outside the case lock:
    `fit_colors(working_copy(photo), matrix, ctx.references.get(...))`.
  - Then update the edit: `transform`, `model`, `color`, `source="manual"`, `status="ok"`, and
    `message=None`. `confidence` and `detail_ratio` keep the automatic values.
- **`reset_edit`** decodes the full original and recomputes the automatic edit. It refuses the
  reference with `InvalidRequestError`.
- **`set_stage`** sets `stage` and `stage_source="manual"`, for any photo.
- **`set_output_scale`.**
  - The scale must be in `OUTPUT_SCALES`; otherwise `InvalidRequestError`.
  - With a ready reference, `output_size(reference size, scale)` may raise
    `OutputTooLargeError`; nothing changes then. Otherwise every edit, the reference's included,
    gets the new `output_size`.
  - Normalized transforms stay valid, so nothing is re-aligned.
- **`set_sequence`.** `photo_ids` must be empty (the default), or 2–10 distinct ids of ready
  photos that have an edit. `gap_px` must be 0–`MAX_GAP_PX`. Otherwise `InvalidRequestError`.
- **`sequence_photos`.**
  - With explicit ids: those still ready with an edit, in the given order.
  - Without: every ready photo with an edit, the reference included, sorted by `captured_at`
    with its offset dropped, then by id. Mixed aware and naive times must still compare, as in
    `headless.run_procedure`.
- **`suggested_output_scale`.**
  - The reference must be ready.
  - Take the median `detail_ratio` of the other edited photos. If it is ≥ 1.5, return the largest
    of `2.0` and `1.5` that is ≤ the median, above the current scale, and accepted by
    `output_size`. Otherwise `None`.

- [ ] **Step 1: Extract `fit_colors` (refactor; `tests/unit/test_procedure_service.py` stays green)**

- [ ] **Step 2: Write the failing tests**

Use `procedure_set()` and a case of patient `"Alex Wang"` titled `"implant 16"`.

| Test | Assertions |
|---|---|
| `test_photos_wait_for_the_reference_then_align` | upload `intra` and `post` → `wait` → both `ready`, `edit is None`, stages `intra_op` / `post_op`; then `set_reference(reference)` → `wait` → the reference is `pre_op` with `reference_edit((1200, 800))`; both photos have `status == "ok"`, `confidence >= 0.6`, and `output_size == (1200, 800)` |
| `test_manual_stages_survive_new_photos` | `set_stage(post, "intra_op")`; upload a third photo taken `2026:10:25` → `post` is still `intra_op` and the new photo is `post_op` |
| `test_replacing_the_reference_reprocesses_and_drops_manual_edits` | `apply_manual_edit(intra, …)`; then `set_reference` with a second reference (the same scene cropped at `box=(500, 300, 1200, 800)`) → after `wait`, `intra`'s edit is `auto`; the old reference record and its file are gone |
| `test_a_photo_dropped_as_reference_becomes_the_reference` | upload `reference` as a photo, then `set_reference` with the same bytes → one record, `role == "reference"` |
| `test_an_unrelated_photo_needs_review` | a photo of `make_scene(99)` → `status == "needs_review"` with an edit (the center-fit fallback) |
| `test_a_corrupt_photo_fails_alone` | `post` cut to 300 bytes → `failed` with `error`; `intra` is `ok` |
| `test_manual_edit_refits_colors_and_is_kept` | `apply_manual_edit(intra, truth, "similarity")` → `source == "manual"`, `status == "ok"`, `color.method == "lab_quantile_v1"`; uploading another photo leaves that edit unchanged; `reset_edit` → `source == "auto"` |
| `test_invalid_manual_edits_are_rejected` | parametrized, each → `InvalidEditError`: 8 values; a `NaN`; all zeros; a similarity with last row `[0.001, 0, 1]`; `inv([[1, 0, 0], [0, 1, 0], [-1.5, 0, 1]])`, which puts the frame's right edge behind the camera |
| `test_output_scale_updates_every_edit_and_rejects_oversize` | `set_output_scale(2.0)` → every edit has `output_size == (2400, 1600)`; back to 1.0; then with `services.procedure.MAX_JPEG_DIMENSION` monkeypatched to 2000, `set_output_scale(2.0)` → `OutputTooLargeError`, and every edit is still (1200, 800); `set_output_scale(3.0)` → `InvalidRequestError` |
| `test_a_small_reference_suggests_a_larger_output` | `procedure_set(reference_scale=0.4)` (a detail ratio near 2.5) → `suggested_output_scale == 2.0`; `procedure_set()` → `None` |
| `test_sequence_selection` | default = reference, `intra`, `post`; `set_sequence([post, reference], 0)` keeps that order; one id, an unknown id, or `gap_px=201` → `InvalidRequestError` |
| `test_composite_operations_are_refused` | `upload` into a composite case → `InvalidRequestError` |
| `test_processing_logs_never_name_the_patient` (`debug_logging`) | photos named `Alex Wang 1.jpg`; processing under `capture_logs(processors=[merge_contextvars])` → no entry's `repr` contains `Alex Wang` or `implant`; some entry has `photo_id` |

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_procedure_case.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement `services/procedure_case.py`**

- [ ] **Step 5: Run the tests and all gates**

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/dental_image_organizer/services tests/unit/test_procedure_case.py
git commit -m "feat(services): align procedure cases in the library with stages and manual edits"
```

---

### Task 10: Composite cases in the library

**Files:**

- Create: `src/dental_image_organizer/services/shooting_order.py`
- Modify: `src/dental_image_organizer/services/headless_composite.py` (use it)
- Modify: `src/dental_image_organizer/services/composite.py` (`fit_cell_colors`)
- Create: `src/dental_image_organizer/services/composite_case.py`
- Test: `tests/unit/test_shooting_order.py`, `tests/unit/test_composite_case.py`

**Interfaces:**

- Consumes: Tasks 7–9 (`AiRuntime`, `effective_flips`, `originals.*`,
  `procedure_case.validate_transform`), `services.composite`, `ml.assignment`, and
  `batch_io.header_capture_time`.
- Produces:
  - `services.shooting_order`:
    - `OrderedPhoto(key: int, name: str, taken: datetime | None, usable: bool = True)`, frozen;
    - `shooting_slots(photos: Sequence[OrderedPhoto], order: Sequence[Slot]) -> dict[int, Slot]`.

    The ranking moves unchanged from `headless_composite._order_keeping_holes`, with
    `_name_position` and `_name_key`, and so does its docstring. An unusable photo (a hole)
    takes its rank but gets no slot. `headless_composite` builds `OrderedPhoto`s from its
    `_Working` and `_Hole` entries.
  - `services.composite.fit_cell_colors(images, slot_map, transforms: Mapping[Slot, tf.Matrix], baseline: Slot, cell_size, labels) -> tuple[Slot | None, dict[Slot, LabAffine], dict[int, Exception]]`.
    This is `_fit_colors`, made public and given transforms instead of `CellGeometry`, so it can
    use a manual cell's matrix. `plan_composite` calls it.
  - `services.composite_case`:
    - `COMPOSITE_ALGORITHM = "compose-1"`.
    - Functions:

      | Function | Returns |
      |---|---|
      | `upload(ctx, case_id, uploads)` | `UploadOutcome` |
      | `process_photos(ctx, case_id, photo_ids, progress)` | `dict[str, Any]` (the job body) |
      | `start_auto_assign(ctx, case_id)` | `str`, the job id |
      | `set_slots(ctx, case_id, slots: Mapping[Slot, str])` | `CaseRecord` |
      | `move_photo(ctx, case_id, photo_id, slot: Slot \| None)` | `CaseRecord` |
      | `set_flip(ctx, case_id, photo_id, flip: Flip)` | `CaseRecord` |
      | `apply_manual_edit(ctx, case_id, photo_id, transform: Sequence[float])` | `CaseRecord` |
      | `reset_edit(ctx, case_id, photo_id)` | `CaseRecord` |
      | `set_layout(ctx, case_id, layout: CompositeRecord)` | `CaseRecord` |
      | `replan(ctx, case: CaseRecord, *, confidences: Mapping[str, float \| None] = {})` | `CaseRecord` |
      | `composite_flips(ctx)` | `dict[Slot, Flip]`: `effective_flips(settings, ai.flips.load())` |
      | `composite_block(settings: Settings)` | `CompositeRecord` |

    `composite_block` makes a new case's block from the settings, with `slots_source="auto"` and
    `baseline_photo_id=None`.

**Rules:**

- Every operation first checks `case.type == "composite"`. Otherwise it raises
  `InvalidRequestError("This is a procedure case.")`.
- **`process_photos`** (kind `process`, key `case_id`):
  1. Decode each photo: metadata as in Task 9, plus `remember_working`. A failure makes it
     `failed`.
  2. If `slots_source == "auto"`, run the assignment over every ready member. Otherwise the new
     photos stay in the tray, and the assignment is `None`.
  3. Apply the slots and replan in one `update_case`.

  The result is `{"photos": […], "assignment": {"method": "classifier" | "order", "reason": AssignReason | None} | None}`.
- **Assignment.**
  - **Classifier.** Use it when `ctx.ai.classifier()` returns one:
    - embed the ready members with `photo_embedding`, outside the case lock;
    - call `assign_by_classifier(classifier.slot_probabilities(pairs))`;
    - an embedding exception falls back to the shooting order with reason `embedding_failed`,
      and the trace is logged (as in `headless_composite._embed`).
  - **Shooting order.**
    - Call `shooting_slots` with `settings.shooting_order`. Ready members are usable, and their
      `name` is `source_name`.
    - Failed members are holes. Their `taken` is `header_capture_time(original_path)`.
    - Pending members are left out.
    - The reason is the one `classifier()` gave.
  - **Placement changes.** A photo whose slot changes, or that goes to or from the tray, gets
    `edit=None`. Its next plan then starts from that slot's default flip and automatic geometry.
- **`replan`** runs inside `update_case`, so it holds the case lock for about 0.6 s:
  1. **Geometry.** For each ready member that has a slot:
     - a `manual` edit keeps its transform;
     - otherwise call `normalize_view(working_copy, slot, flip, cell_size)`, where `flip` is
       `edit.flip` or else `composite_flips(ctx)[slot]`.
  2. **Colors.** Call `fit_cell_colors` on the working copies. The baseline slot is the slot of
     `baseline_photo_id` when that photo is placed, else `DEFAULT_BASELINE`. The chosen baseline
     gets `identity` color; the others get `lab_reinhard_v1`.
  3. **Edits.** Each is `similarity` with `output_size=cell_size` and `COMPOSITE_ALGORITHM`, and
     keeps its `source` and `flip`. `confidence` comes from `confidences`, else the previous
     edit. `status` is `needs_review` when the confidence is below `LOW_CONFIDENCE` (0.5).
  4. **Failures.** A photo whose geometry or color fit raises becomes `failed`, with `error`,
     `slot=None`, and `edit=None`. Its cell stays empty, as in the CLI.
  5. Tray photos, and photos that are not ready, get `edit=None`.
- **`set_slots`.**
  - Every id must be a ready member, at most once. Otherwise `InvalidRequestError`.
  - Members not in the map go to the tray. Set `slots_source="manual"`, then replan.
- **`move_photo`** follows the drag-to-swap rule of `with_overrides`: the photo in the target
  slot takes the mover's old slot, or the tray. Set `slots_source="manual"`, then replan.
- **`set_flip`, `apply_manual_edit`, and `reset_edit`** need a placed photo. Otherwise
  `NotReadyError("Place the photo in a slot first.")`.
  - `set_flip` sets `edit.flip` and `source="auto"`. The geometry is recomputed, so manual
    pan and zoom on that cell are reset.
  - `apply_manual_edit` calls `validate_transform(values, "similarity", cell_size)` (§4.7.2
    step 4: composite transforms are similarities). It sets `source="manual"`, then replans, so the
    colors are refit.
  - `reset_edit` sets `edit=None`, then replans.
- **`set_layout`** validates the block:
  - `baseline_photo_id` is `None` or a member;
  - each side of `cell_size` is 100–4000;
  - `gap_px` is 0–200;
  - `gap_color` is `#RRGGBB`.

  It keeps the current `slots_source`, then replans.
- **`start_auto_assign`** submits kind `assign` with key `case_id`. The job sets
  `slots_source="auto"`, clears every edit, assigns, and replans. Its result is
  `{"assignment": …}`.

- [ ] **Step 1: Move the shooting order and make `fit_cell_colors` public (refactor; existing tests stay green)**

Run: `uv run pytest tests/integration/test_cli_composite.py tests/unit/test_composite_service.py -q`
Expected: PASS, unchanged.

- [ ] **Step 2: Write the failing tests**

`test_shooting_order.py`:

- `test_photos_take_slots_in_capture_order`.
- `test_a_hole_keeps_its_rank`: a hole with a time; and a hole without one, ranked after the
  photo whose name precedes it.
- `test_equal_times_rank_by_file_number`: `IMG_99` before `IMG_100`.

`test_composite_case.py` uses `view_set()`.

| Test | Assertions |
|---|---|
| `test_upload_assigns_by_shooting_order_and_plans_every_cell` | nine photos → `wait` → `IMG_000k` sits in `SLOTS[k-1]`; every edit has `output_size == (1200, 800)` and `flip == DEFAULT_FLIPS[slot]`; `occlusion_front` has `identity` color and the others `lab_reinhard_v1`; the result's assignment is `{"method": "order", "reason": "untrained"}` |
| `test_the_settings_shooting_order_is_used` | `shooting_order` reversed → `IMG_0001` in `lower_left` |
| `test_extra_photos_go_to_the_tray` | `view_set(count=10)` → exactly one photo has `slot is None` and `edit is None` |
| `test_an_unreadable_photo_keeps_its_rank` | `IMG_0005` cut to 300 bytes (its EXIF header survives) → it is `failed`; `occlusion_front` is empty; `IMG_0006` is in `occlusion_left` |
| `test_swapped_slots_are_kept_when_photos_are_added` | `set_slots` swapping `IMG_0001` and `IMG_0003` → `slots_source == "manual"`; both flips are the new slots' defaults; uploading a tenth photo puts it in the tray and keeps the swap; `start_auto_assign` → back to row-major, `slots_source == "auto"` |
| `test_a_flip_change_recomputes_the_cell` | `set_flip(upper_right photo, "none")` → `edit.flip == "none"` and a different transform |
| `test_a_manual_cell_is_kept_by_replans` | `apply_manual_edit` on the `upper_left` cell → `manual`; swapping two other photos leaves its transform unchanged; `reset_edit` → `auto` with the default flip |
| `test_the_baseline_photo_gets_identity_color` | `set_layout` with the `lower_left` photo as baseline → that edit has `identity` color, and `occlusion_front` has `lab_reinhard_v1` |
| `test_learned_flips_are_the_defaults` | `ctx.ai.record_flips` for three cases with `upper_right: none` → a new case's `upper_right` cell has `flip == "none"` |
| `test_a_trained_classifier_assigns` | `make_context(tmp_path, model_spec=install_fake_model(tmp_path/"cache"/"models"))`; `ctx.ai.learn` with `synthetic_pairs(3, 1)` and random photo ids → upload nine → assignment `{"method": "classifier", "reason": None}`; every placed edit has a `confidence` |
| `test_invalid_slot_maps_are_rejected` | one id in two slots; an unknown id; a pending photo → `InvalidRequestError` |
| `test_manual_cell_edits_must_be_similarities` | a matrix with last row `[0.001, 0, 1]` → `InvalidEditError` |

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_shooting_order.py tests/unit/test_composite_case.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement `services/shooting_order.py` and `services/composite_case.py`**

- [ ] **Step 5: Run the tests and all gates**

Expected: all pass, the existing composite tests included.

- [ ] **Step 6: Commit**

```bash
git add src/dental_image_organizer/services tests/unit/test_shooting_order.py tests/unit/test_composite_case.py
git commit -m "feat(services): assign, normalize, and edit composite cases in the library"
```

---

### Task 11: Renders and previews

**Files:**

- Create: `src/dental_image_organizer/services/renders.py`
- Test: `tests/unit/test_renders.py`

**Interfaces:**

- Consumes: Tasks 8–10 (`originals.*`, `procedure_case.sequence_photos`), `core.render.render`,
  `core.layout` (`render_sequence`, `fit_strip_height`, `MAX_SEQUENCE_PIXELS`),
  `services.composite.compose`, and `storage.cache.cache_key`.
- Produces:
  - Constants: `THUMB_EDGE = 320`, `PREVIEW_EDGE = 1600`, `PREVIEW_QUALITY = 85`.
  - Literals:
    - `ImageVariant = Literal["original", "processed"]`;
    - `ImageSize = Literal["thumb", "preview", "full"]`;
    - `RenderSize = Literal["preview", "full"]`.
  - Functions that return JPEG bytes:
    - `photo_image(ctx, case_id, photo_id, *, variant: ImageVariant, size: ImageSize) -> bytes`;
    - `sequence_image(ctx, case_id, *, size: RenderSize) -> bytes`;
    - `composite_image(ctx, case_id, *, size: RenderSize) -> bytes`.
  - Functions that export reuses:
    - `render_full(photo: PhotoRecord, rgb: RGBImage) -> RGBImage`, which renders an edit at its
      `output_size`;
    - `sequence_strip(ctx, case: CaseRecord, *, full: bool) -> RGBImage`;
    - `composite_grid(ctx, case: CaseRecord, *, full: bool) -> RGBImage`;
    - `fit_long_edge(size: tuple[int, int], long_edge: int) -> tuple[int, int]`, which never
      upscales.

**Rules:**

- **Photo images** (`interpolation` per §4.8):

  | Variant | Size | Source | Output size | Interpolation |
  |---|---|---|---|---|
  | original | thumb | working copy | long edge 320 | `INTER_AREA` |
  | original | preview | working copy | the working copy, long edge ≤ 1600 | — |
  | original | full | full decode | the decoded size | — |
  | processed | thumb | working copy | `fit_long_edge(output_size, 320)` | `INTER_LINEAR` |
  | processed | preview | full decode | `fit_long_edge(output_size, 1600)` | `INTER_LINEAR` |
  | processed | full | full decode | `output_size` | `INTER_LANCZOS4` |

  - The processed preview uses the full original, because a tightly cropped reference leaves
    few working-copy pixels in the frame. The compare view must show real detail.
  - A reference renders with `BORDER_REPLICATE`, like `render_reference`, so no black edge
    appears at 1.5× and 2×.
  - The JPEG quality is `PREVIEW_QUALITY` for thumbnails and previews, and
    `settings.jpeg_quality` for full size. No EXIF is embedded.
  - `processed` for a photo that is not `ready`, or has no edit, raises
    `NotReadyError("This photo has not been processed yet.")`.
- **Cache keys** (`cache_key`):
  - a photo: `("photo", variant, size, sha256, edit.render_key() or "", quality, role)`;
  - a sequence: `("sequence", size, gap_px, quality, *frames)`;
  - a composite: `("composite", size, cell_size, gap_px, gap_color, quality, *cells)`.

  Each frame is `sha256 + render_key`, and each cell is `slot + sha256 + render_key`. A status
  change therefore never re-renders, while any pixel-changing edit does.
- **Sequence.**
  - The frames are `sequence_photos(case)`. With none, raise
    `NotReadyError("The case has no processed photos yet.")`.
  - Full: the strip height is `fit_strip_height([output_size] * n, gap_px=…, max_width=MAX_JPEG_DIMENSION, max_pixels=MAX_SEQUENCE_PIXELS)`.
    Each frame is rendered from a full decode and scaled to that height as it arrives, as
    `headless.run_procedure` does.
  - Preview: the same, with `max_width=PREVIEW_EDGE`, rendered from working copies straight at
    the frame size.
- **Composite.**
  - Every placed cell is rendered at `cell_size`: from a full decode with Lanczos for full size,
    or from the working copy with `INTER_LINEAR` for the preview.
  - Call `compose(cells, cell_size, gap_px=…, gap_color=<#RRGGBB as RGB>)`.
  - The preview is then resized to a long edge of `PREVIEW_EDGE`.
  - With no placed cell, raise `NotReadyError("No photo is placed yet.")`.

- [ ] **Step 1: Write the failing tests**

| Test | Assertions |
|---|---|
| `test_original_variants` | a 1800 × 1200 member: thumb (320, 213), preview (1600, 1067), full (1800, 1200) |
| `test_processed_variants_follow_the_edit` | `procedure_set` after processing: full (1200, 800); preview (1200, 800), not upscaled; thumb (320, 213) |
| `test_renders_are_cached` | a second `photo_image` call does not decode (spy on `originals.decode`) and returns equal bytes |
| `test_a_changed_edit_renders_anew` | after `set_output_scale(2.0)`, the full size is (2400, 1600) |
| `test_unprocessed_photos_are_not_ready` | a pending photo, a failed photo, and a tray photo → `processed` raises `NotReadyError`; `original` of the pending photo works |
| `test_the_reference_has_no_black_edge` | the reference at `output_scale` 1.5, full: the last pixel column is not all zeros |
| `test_composite_preview_and_full` | full (3600, 2400); preview (1600, 1067); with `gap_px=10`, full (3620, 2420) |
| `test_sequence_preview_and_full` | three 1200 × 800 frames: full (3600, 800); preview width ≤ 1600 |
| `test_empty_cases_are_not_ready` | a new procedure case's sequence and a new composite case's grid → `NotReadyError` |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_renders.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/renders.py`**

- [ ] **Step 4: Run the tests and all gates**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dental_image_organizer/services/renders.py tests/unit/test_renders.py
git commit -m "feat(services): render cached photo, sequence, and composite previews"
```

---

### Task 12: Case and patient export

**Files:**

- Create: `src/dental_image_organizer/services/export.py`
- Test: `tests/unit/test_export.py`

**Interfaces:**

- Consumes: Tasks 7–11, and `core.naming` (`photo_filename`, `sequence_filename`,
  `composite_filename`, `composite_capture_date`, `unique_name`, `case_export_zip_name`,
  `patient_export_zip_name`).
- Produces:
  - `ExportInclude = Literal["singles", "sequence", "composite"]`.
  - `ConflictMode = Literal["replace", "keep_both"]`.
  - `DEFAULT_INCLUDE: dict[CaseType, tuple[ExportInclude, ...]] = {"procedure": ("singles", "sequence"), "composite": ("composite",)}`.
  - Functions:

    | Function | Returns |
    |---|---|
    | `start_case_export(ctx, case_id, include: Sequence[ExportInclude] \| None, conflict: ConflictMode)` | `str`, the job id; kind `export`, key `case_id` |
    | `start_patient_export(ctx, patient_id, include, conflict)` | `str`; kind `export_patient`, key `f"patient:{patient_id}"` |
    | `export_case(ctx, case_id, include, conflict, *, today: date \| None = None)` | `dict[str, Any]`, the body both jobs run |
    | `export_zip(ctx, job_id, *, case_id: str \| None = None, patient_id: str \| None = None)` | `tuple[Path, str]`: the zip in `dirs.exports`, and its download name |

**Rules:**

- **Include.** `None` means `DEFAULT_INCLUDE[type]`.
  - For a case export, a value that does not apply to the case type raises
    `InvalidRequestError`.
  - A patient export applies to each case the values that fit its type.
- **Queueing.** The case export's key is the case id, so it runs after the processing jobs
  queued before it. It exports the photos those jobs process.
- **Rendering**, outside any lock. Each file is written to `export/.<uuid>.part` as soon as it is
  encoded, so encoded JPEGs never pile up in memory:
  - **Singles.**
    - Every ready photo with an edit, the reference included, is rendered with `render_full`
      from a fresh full decode.
    - It is encoded with `encode_jpeg(image, decoded.metadata, quality=settings.jpeg_quality, strip_gps=settings.strip_gps)`
      and named `photo_filename(patient.name, case.title, photo.captured_at)`.
    - A photo that fails adds `{photo_id, message}` to `failures`, and the export goes on
      (§4.13).
    - Pending and failed photos are counted in `skipped`.
  - **Sequence.** `sequence_strip(full=True)`, named
    `sequence_filename(patient.name, case.title, today)`.
  - **Composite.** `composite_grid(full=True)`, named
    `composite_filename(patient.name, case.title, composite_capture_date(capture dates of the placed photos))`.
  - Nothing to render adds a `failures` entry with `photo_id: None`:
    `"No processed photos for the sequence."` or `"No photo is placed yet."`.
- **Naming and conflicts**, under `ctx.case_locks.hold(case_id)`:
  - **`replace`.** Delete every file in `case.exports` that still exists. Each path goes
    through `library.resolve`. Only then choose the new names.
  - **Both modes.** Each name is `unique_name(name, names now in export/)`. `os.replace` moves
    each part file to its name. Then `update_case` sets `exports`:
    - `replace` keeps only the new records;
    - `keep_both` appends them.
  - A file the app did not write is never deleted, and never overwritten (amendment 4).
- **Learning on a composite export** (§3.3 step 7). It runs after the files are written, outside
  the case lock, and it never fails the export:
  - Flips: `ai.record_flips(case.id, {slot: edit.flip}, pins=settings.flip_pins)`.
  - Samples, if `ai.embedder()` returns one: the placed photos' `photo_embedding`s go to
    `ai.learn(…, photo_ids=[photo.sha256 …], case_id=case.id, source="export")`, which retrains.
  - The result's `learning` is `{"samples": n, "trained": bool}`. Without the model it is
    `{"error": "no_model", "message": None}`. When it raises, the trace is logged and it is
    `{"error": "failed", "message": …}`.
- **Results.**
  - A case export:

    ```json
    {"case_id": "…", "files": ["export/<name>", …],
     "failures": [{"photo_id": "…" | null, "message": "…"}],
     "skipped": {"pending": 0, "failed": 0},
     "learning": null | {"samples": 9, "trained": true} | {"error": "no_model" | "failed", "message": null | "…"}}
    ```

  - A patient export:

    ```json
    {"patient_id": "…", "cases": [<case result>, …], "failed_cases": [{"case_id": "…", "message": "…"}]}
    ```

    A case that raises is listed in `failed_cases`, and the other cases still export (§3.4
    step 4).
- **`export_zip`.**
  - The job must be `done`, of kind `export` for that case or `export_patient` for that
    patient. Otherwise `JobNotFoundError`.
  - It writes `dirs.exports/<job_id>.zip` with `ZIP_STORED`. The files are already JPEGs.
  - Case zip: the job's files by name, named `case_export_zip_name(patient, title, today)`.
  - Patient zip: one folder per case, named like the case folder, named
    `patient_export_zip_name(patient, today)`.
  - A file deleted since the export is left out.

- [ ] **Step 1: Write the failing tests**

| Test | Assertions |
|---|---|
| `test_procedure_export_names_and_metadata` | `procedure_set(gps=True)`, processed and exported → `Alex Wang_implant 16_20261016_0900.jpg`, `…_20261016_1000.jpg`, `…_20261020_1000.jpg`, and `Alex Wang_implant 16_Procedure_<today>.jpg`; each single is 1200 × 800 with EXIF `DateTimeOriginal` and no GPS IFD; `case.exports` holds four records |
| `test_gps_is_kept_when_the_setting_allows` | `strip_gps=False` → the GPS IFD is present |
| `test_keep_both_never_overwrites` | two `keep_both` exports → `_2` files; `case.exports` holds eight records |
| `test_replace_deletes_only_files_the_app_wrote` | before the first export, a user file named like this export's sequence → the first export (`keep_both`) writes the sequence as `_2`; a second export with `replace` deletes the app's four files and writes four again, with the sequence `_2` again; the user's file is untouched |
| `test_exporting_a_composite_again_does_not_count_it_twice` (Review Focus 2) | fake model; nine members; export; `set_flip` on `upper_right` to `none`; export again with `replace` → one `…_Composite_20261016.jpg` in `export/`; the sample store holds 9 samples; `flips.json` holds 9 confirmations, `upper_right` among them with `none` |
| `test_composite_export_without_the_model_still_learns_flips` | no model → `learning == {"error": "no_model", "message": None}`; 9 flip confirmations; the file is written |
| `test_an_export_waits_for_processing_of_its_case` | with the reference set and processed, upload `intra` and `post`, then call `start_case_export` at once → its result has `skipped == {"pending": 0, "failed": 0}`, and `files` holds both photos' singles |
| `test_a_photo_that_fails_to_render_does_not_stop_the_export` | monkeypatch `export.render_full` to raise for one photo → that photo is in `failures`; the job is `done` with the other files |
| `test_patient_export_isolates_failing_cases` | a patient with a procedure case, a composite case, and a third case; monkeypatch `export.export_case` to raise for the third → `failed_cases` lists it; the zip has two folders, named like the two case folders, holding their files |
| `test_zip_downloads` | the case zip holds the job's files by name and is named `Alex Wang_implant 16_Export_<today>.zip`; another case's id → `JobNotFoundError` |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_export.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/export.py`**

- [ ] **Step 4: Run the tests and all gates**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dental_image_organizer/services/export.py tests/unit/test_export.py
git commit -m "feat(services): export cases and patients, and learn from exported composites"
```

---

### Task 13: Teaching sessions

**Files:**

- Create: `src/dental_image_organizer/services/teach.py`
- Modify: `src/dental_image_organizer/services/context.py` (the `teach` field)
- Test: `tests/unit/test_teach.py`

**Interfaces:**

- Consumes: Tasks 7, 8, and 10 (`stage_upload`, `shooting_slots`, `composite_flips`).
- Produces:
  - `TeachPhoto(id: str, name: str, captured_at: datetime | None, state: PhotoState, error: str | None)`.
  - `TeachSession(id: str, state: Literal["processing", "ready", "failed"], photos: tuple[TeachPhoto, ...], proposal: Mapping[Slot, str], job_id: str)`.
  - `TeachSessions(ctx: AppContext)`:

    | Method | Behavior |
    |---|---|
    | `start(uploads: Sequence[Upload]) -> tuple[TeachSession \| None, list[Skipped]]` | `None` when nothing could be staged |
    | `get(session_id) -> TeachSession` | `TeachSessionNotFoundError` |
    | `image(session_id, photo_id, size: Literal["thumb", "preview"]) -> bytes` | |
    | `confirm(session_id, slots: Mapping[Slot, str], flips: Mapping[Slot, Flip]) -> ClassifierStatus` | |
    | `cancel(session_id) -> None` | |

  - `AppContext.teach: TeachSessions`, declared `field(init=False)`. `create_context` sets it
    to `TeachSessions(ctx)` once the context exists. Startup already empties `dirs.teach`
    (Task 8).

**Rules:**

- **`start`.**
  - It needs the model. When `ctx.ai.embedder()` is `None`, raise
    `ModelUnavailableError("Download the AI model first; teaching needs it.")` (§3.5 step 1).
  - Files are staged into `dirs.teach/<session_id>/`. Unsupported files, files that are too
    large, and repeats within the set are skipped.
  - The job is kind `teach` with key `f"teach:{session_id}"`. It:
    1. decodes each photo. The capture time is the EXIF time, else `last_modified`, else now.
       A decode failure makes that photo `failed`;
    2. writes `<photo_id>.thumb.jpg` (long edge 320) and `<photo_id>.preview.jpg` (1600) at
       `PREVIEW_QUALITY`;
    3. embeds the ready photos in one `embedder.embed` call and keeps the embeddings in memory;
    4. proposes slots with `shooting_slots(…, settings.shooting_order)`, with failed photos as
       holes, as in Task 10.
  - The session becomes `ready`, or `failed` if the job fails.
- **`confirm`.**
  - The session must be `ready`. Otherwise `NotReadyError`.
  - The slot values must be distinct ready photo ids. Otherwise `InvalidRequestError`.
  - The keys of `flips` must be slots of `slots`. Otherwise `InvalidRequestError`. A placed
    slot without a flip takes `composite_flips(ctx)[slot]`.
  - Then call `ctx.ai.learn(…, photo_ids=[SHA-256 of each file], case_id=f"teach:{session_id}", source="teach")`,
    which retrains, and `ctx.ai.record_flips(f"teach:{session_id}", flips, pins=settings.flip_pins)`.
  - Delete the session's folder and memory, and return `ctx.ai.status()`.
  - Incomplete sets are allowed (§3.5 step 4).
- **`cancel`** deletes the session.
- No teaching call reads or writes the library, and none asks for a patient name (§3.5 step 5).

- [ ] **Step 1: Write the failing tests**

| Test | Assertions |
|---|---|
| `test_three_taught_sets_train_the_classifier` (§8.2 item 4) | fake model; `view_set` seeds 70, 170, and 270, each started, waited for, and confirmed with its proposal → `status().trained` after the third; the sample store holds 27 samples, all with source `teach`; `ctx.library().listing().patients == ()`; `dirs.teach` is empty |
| `test_the_proposal_follows_the_shooting_order` | `shooting_order` reversed → `proposal["lower_left"]` is `IMG_0001.jpg`'s id |
| `test_cancel_deletes_the_session` | `cancel` → `get` raises `TeachSessionNotFoundError`; the folder is gone |
| `test_teaching_needs_the_model` | no model → `ModelUnavailableError` |
| `test_an_incomplete_set_adds_only_its_slots` | seven photos confirmed → seven slots count 1, two count 0 |
| `test_confirmed_flips_feed_flip_learning` | `flips={"upper_right": "none"}` → `ai.flips.load()` holds 9 entries for `teach:<sid>`, `upper_right` with `none` |
| `test_invalid_confirmations_are_rejected` | one id in two slots; an unknown id; a flip for an unplaced slot → `InvalidRequestError`; confirming while the job is held on an `Event` → `NotReadyError` |
| `test_thumbnails` | `image(…, "thumb")` is a JPEG with long edge 320 |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_teach.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/teach.py` and the context field**

- [ ] **Step 4: Run the tests and all gates**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dental_image_organizer/services/teach.py src/dental_image_organizer/services/context.py tests/unit/test_teach.py
git commit -m "feat(services): teach the classifier from past composite sets"
```

---

### Task 14: The API foundation: security, errors, system, settings, and jobs

**Files:**

- Create: `src/dental_image_organizer/api/__init__.py`, `api/app.py`, `api/security.py`,
  `api/errors.py`, `api/deps.py`, `api/lifecycle.py`, `api/models.py`,
  `api/routes/__init__.py`, `api/routes/system.py`, `api/routes/settings.py`,
  `api/routes/jobs.py`
- Create: `src/dental_image_organizer/services/reveal.py`
- Modify: `pyproject.toml`
  - Add `dental_image_organizer.api` to the `core`, `ml`, `storage`, and `services` forbidden
    lists.
  - Add a contract: `api` must not import `cli`.
- Create: `tests/integration/conftest.py`, with a `client` fixture
- Test: `tests/unit/test_idle.py`, `tests/integration/test_api_foundation.py`

**Interfaces:**

- Consumes: Tasks 1, 6, and 8.
- Produces:
  - `api.app`:
    - `WEB_DIR = Path(__file__).resolve().parent.parent / "web"`.
    - `create_app(ctx: AppContext, *, port: int, shutdown: Callable[[], None], web_dir: Path | None = WEB_DIR, clock: Callable[[], float] = time.monotonic) -> FastAPI`.
      It creates `FastAPI(docs_url=None, redoc_url=None, openapi_url="/api/openapi.json", lifespan=…)`.
      The Swagger page would load its scripts from a CDN, which is an outbound call the spec does
      not allow (§4.13). The schema stays available for the Plan 4 type generator.
    - It sets `app.state.ctx`, `app.state.idle`, and `app.state.shutdown`.
    - Middleware, outermost first:
      1. `TrustedHostMiddleware(allowed_hosts=["127.0.0.1", "localhost"], www_redirect=False)`;
      2. `LocalRequestGuard(port=port)`.
  - `api.security`:
    - `MAX_REQUEST_BYTES = 2 * 1024**3`;
    - `UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})`;
    - `LocalRequestGuard(app: ASGIApp, *, port: int)`, a pure ASGI middleware.
  - `api.errors`:
    - `STATUS_BY_ERROR: Mapping[type[DioError], int]`;
    - `install_error_handlers(app: FastAPI) -> None`.
  - `api.lifecycle`:
    - `IDLE_CHECK_S = 15.0`;
    - `IdleWatch(minutes: Callable[[], int], clock: Callable[[], float] = time.monotonic)`, with
      `beat()` and `expired(*, jobs_active: bool) -> bool`;
    - `lifespan`, which `create_app` wires in.
  - `api.deps`:
    - `context(request: Request) -> AppContext`, and `Ctx = Annotated[AppContext, Depends(context)]`;
    - `async run_cpu(ctx: AppContext, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T`.
      It runs `fn` in `ctx.executor` through `contextvars.copy_context().run`.
  - `api.models`:
    - `ErrorBody(code: str, message: str, details: Any | None = None)` and
      `ErrorResponse(error: ErrorBody)`;
    - `Health(status: Literal["ok"], version: str)`;
    - `RevealRequest(path: str)`;
    - `JobRef(job_id: str | None)`;
    - `JobView(id, kind, state: JobState, done: int, total: int, message: str | None, result: dict[str, Any] | None, error: ErrorBody | None)`,
      with `JobView.of(snapshot: JobSnapshot) -> JobView`.
  - `services.reveal.reveal(path: Path) -> None`.
  - `tests/integration/conftest.py`: the fixture `client(ctx)`. It yields
    `TestClient(create_app(ctx, port=8765, shutdown=<recorder>, web_dir=None), base_url="http://127.0.0.1:8765", headers={"Origin": ORIGIN})`
    inside `with`, so the lifespan runs. The recorder appends to a list, which the fixture also
    stores as `app.state.shutdown_calls` for the tests to read.

**Rules:**

- **`LocalRequestGuard`.** Each refusal uses the error body shape:

  | Condition | Status | `code` | `message` |
  |---|---|---|---|
  | an unsafe method whose `Origin` is not `http://127.0.0.1:<port>` or `http://localhost:<port>` (also when missing) | 403 | `forbidden_origin` | `Requests must come from the app's own page.` |
  | a `multipart/form-data` request without `Content-Length` | 411 | `length_required` | `Uploads need a Content-Length.` |
  | a `multipart/form-data` request whose `Content-Length` exceeds `MAX_REQUEST_BYTES` | 413 | `request_too_large` | `Uploads are limited to 2 GiB per request.` |

- **`STATUS_BY_ERROR`.** It is looked up along the exception's MRO; the first match wins:

  | Error | Status |
  |---|---|
  | `NotFoundError` | 404 |
  | `ConflictError` | 409 |
  | `InvalidRequestError`, `InvalidArchiveError`, `InvalidNameError`, `OutputTooLargeError`, `CorruptImageError` | 422 |
  | `UnsupportedFormatError` | 415 |
  | `PathOutsideLibraryError` | 400 |
  | `ModelUnavailableError` | 503 |
  | `ModelIntegrityError` | 502 |
  | `StorageError` | 500, with its own message (it says how to recover) |
  | any other `DioError` | 400 |

- **Handlers.**

  | Exception | Response | Logged |
  |---|---|---|
  | `DioError` | `{code, message: str(exc), details: exc.details}` | `request_failed` with `code` only |
  | `RequestValidationError` | 422 `invalid_request`, `"The request is not valid."`, `details=jsonable_encoder(exc.errors())` | nothing; the errors echo request values |
  | Starlette `HTTPException` | its status; `code` is `not_found`, `method_not_allowed`, or `http_<status>`; `message` is `exc.detail` | |
  | any other | 500 `internal_error`, `"An unexpected error occurred; see the log file."` | `log.exception("request_failed")`; an `OSError` is logged with `strerror` only |

- **Handlers and CPU work.**
  - A handler that does CPU-heavy work is `async def` and calls `run_cpu`.
  - A handler that only reads or writes small JSON files may be a plain `def`; FastAPI runs it
    in its own thread pool.
  - Starlette's thread pool is not limited to `WORKERS`, so it must never decode or render.
- **`IdleWatch`.**
  - `expired` is `False` when `minutes() == 0` or when `jobs_active` is true.
  - Otherwise it is `True` once `clock() − last beat ≥ minutes() × 60`.
  - The start counts as a beat.
  - `minutes` reads the settings on every call, so a change applies without a restart.
- **The lifespan** starts a task that every `IDLE_CHECK_S` seconds evaluates
  `idle.expired(jobs_active=ctx.jobs.active())`. When it is true, the task logs `idle_shutdown`
  and calls `shutdown()` once. The task is cancelled when the app stops.
- **`reveal`.**
  - macOS runs `["open", "-R", str(path)]`; Windows runs `["explorer", f"/select,{path}"]`;
    other systems run `["xdg-open", <the folder>]`.
  - It uses `subprocess.Popen` with all three streams on `DEVNULL` and never waits.
    Explorer exits with 1 even on success.
  - An `OSError` raises `DioError(f"Revealing the folder failed: {strerror}")`.
- **Static files.**
  - If `web_dir / "index.html"` exists, mount a `StaticFiles(directory=web_dir, html=True)`
    subclass at `/`. A 404 from it falls back to `index.html` (the SPA's own routes), and
    `index.html` carries `Cache-Control: no-cache`.
  - Without a build, `GET /` returns this HTML:
    `Dental Image Organizer is running, but its interface is not built. Developers: run npm run build in frontend/.`
  - An `/api/{rest:path}` route for every method (`include_in_schema=False`) raises 404, so an
    unknown API path answers JSON, never the SPA.
  - `create_app` registers, in this order: every API router, the catch-all, and the static
    mount. Tasks 15 and 16 add their routers to the first group.
- **Routes in this task** (all under `/api`):

  | Method & path | Request | Response | Notes |
  |---|---|---|---|
  | `GET /health` | | `Health` | `version` is `__version__` |
  | `POST /heartbeat` | | 204 | `idle.beat()` |
  | `POST /system/shutdown` | | 202 `{"status": "stopping"}` | `shutdown()` runs in a `BackgroundTask`, after the response is sent |
  | `POST /system/reveal` | `RevealRequest` | 204 | `library().resolve(path)`; a missing path → `NotFoundError("Nothing to reveal at that path.")` |
  | `GET /settings` | | `Settings` | |
  | `PUT /settings` | `Settings` | `Settings` | `ctx.apply_settings` |
  | `GET /jobs/{job_id}` | | `JobView` | |
  | `GET /jobs/{job_id}/events` | | `text/event-stream` | see below |

  Each event from `watch` is `f"id: {version}\nevent: {state}\ndata: {JobView json}\n\n"`. The
  stream ends after the finished snapshot, and carries `Cache-Control: no-cache`. An unknown id
  answers 404 before the stream starts: the handler calls `jobs.get` first.

- [ ] **Step 1: Write the failing tests**

`test_idle.py`:

- `test_expires_after_the_timeout_without_a_beat`;
- `test_a_beat_resets_the_timer`;
- `test_never_expires_while_a_job_runs`;
- `test_zero_minutes_never_expires`.

`test_api_foundation.py`:

| Test | Assertions |
|---|---|
| `test_health_reports_the_version` | `{"status": "ok", "version": __version__}` |
| `test_a_foreign_host_is_rejected` (§8.2 item 7) | `Host: evil.test` → 400 |
| `test_state_changes_need_the_apps_origin` (§8.2 item 7) | `POST /api/heartbeat` with no `Origin`, with `http://localhost:5173`, and with `http://127.0.0.1:9999` → 403 `forbidden_origin`; with `http://localhost:8765` → 204; `GET /api/health` without `Origin` → 200 |
| `test_paths_outside_the_library_are_rejected` (§8.2 item 7) | revealing `../outside` → 400 `path_outside_library`; revealing a case folder → 204, and the monkeypatched `reveal` received that folder |
| `test_uploads_need_a_bounded_length` | a multipart `POST` whose body is a generator (chunked, so no `Content-Length`) → 411; with `MAX_REQUEST_BYTES` monkeypatched to 10, a small multipart `POST` → 413 |
| `test_errors_have_one_shape` | `GET /api/nope` → 404 `not_found`; an unknown job → 404 `job_not_found`; `PUT /api/settings` with `jpeg_quality` 101 → 422 `invalid_request` with `details`; a test-only route that raises `RuntimeError("Alex Wang")` → 500 `internal_error`, and the body never contains `Alex Wang` |
| `test_the_api_docs_page_is_not_served` | `GET /docs` is not a Swagger page (404 without a build); `GET /api/openapi.json` → 200 with `paths` |
| `test_heartbeat_and_shutdown` | an app built with a fake clock, advanced by 31 minutes → `app.state.idle.expired(jobs_active=False)` is `True`; `POST /api/heartbeat` → 204, and it is `False`; `POST /api/system/shutdown` → 202, and the shutdown recorder holds one call |
| `test_settings_round_trip_and_refuse_moving_the_library_during_a_job` | `PUT` with `jpeg_quality` 80 → a following `GET` equals it; with a job held on an `Event`, a `PUT` with a new `library_root` → 409 `job_running` |
| `test_job_events_stream_until_done` | a job on `ctx.jobs` that reports three progress steps → `client.stream("GET", …/events)` yields events in order, the last `event: done`; every `data:` parses as a `JobView` |
| `test_the_spa_falls_back_to_index` | with a `web_dir` holding `index.html` and `assets/a.js`: `GET /cases/abc` → `index.html` with `Cache-Control: no-cache`; `GET /assets/a.js` → its content; `GET /api/nope` → JSON 404 |
| `test_without_a_build_the_root_explains_itself` | `GET /` → 200, containing `npm run build` |
| `test_idle_shutdown_runs_from_the_lifespan` | `IDLE_CHECK_S` monkeypatched to 0.01, a fake clock, `idle_shutdown_minutes=1`: advance the clock by 61 s → the shutdown recorder holds one call within 2 s; with a job held running, it holds none |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_idle.py tests/integration/test_api_foundation.py -q`
Expected: FAIL with `ModuleNotFoundError: dental_image_organizer.api`.

- [ ] **Step 3: Implement the `api` package, these routes, and `services/reveal.py`**

- [ ] **Step 4: Run the tests and all gates**

Expected: all pass, including `lint-imports` with the new contracts.

- [ ] **Step 5: Commit**

```bash
git add src/dental_image_organizer/api src/dental_image_organizer/services/reveal.py pyproject.toml tests/unit/test_idle.py tests/integration
git commit -m "feat(api): add the local-only FastAPI app with errors, settings, jobs, and SSE"
```

---

### Task 15: Library, case, photo, render, export, and archive routes

**Files:**

- Create: `src/dental_image_organizer/services/cases.py`
- Modify: `src/dental_image_organizer/api/models.py`, `src/dental_image_organizer/api/app.py`
- Create: `src/dental_image_organizer/api/routes/patients.py`, `cases.py`, `photos.py`,
  `composite.py`, `render.py`, `export.py`, `archive.py`
- Test: `tests/integration/test_api_library.py`, `tests/integration/test_api_procedure.py`,
  `tests/integration/test_api_composite.py`, `tests/integration/test_api_export.py`

**Interfaces:**

- Consumes: Tasks 5 and 8–14.
- Produces:
  - `services.cases`:
    - `CaseDetails(case: CaseRecord, patient: PatientRecord, suggested_output_scale: float | None, flip_defaults: dict[Slot, Flip] | None)`.
    - Functions:

      | Function | Returns |
      |---|---|
      | `create_case(ctx, kind: CaseType, title: str, *, patient_id: str \| None = None, patient_name: str \| None = None, patient_note: str \| None = None)` | `CaseRecord` |
      | `case_details(ctx, case_id)` | `CaseDetails` |
      | `change_case(ctx, case_id, *, title: str \| None = None, output_scale: float \| None = None, sequence: SequenceRecord \| None = None, layout: CompositeRecord \| None = None)` | `CaseRecord` |
      | `delete_case(ctx, case_id)` | `None` |
      | `update_patient(ctx, patient_id, *, name: str, note: str \| None)` | `PatientRecord` |
      | `upload_photos(ctx, case_id, uploads)` | `UploadOutcome` |
      | `delete_photo(ctx, case_id, photo_id)` | `CaseRecord` |

  - `api.models`, additions:
    - `CaseSummary(id, type, title, created_at, updated_at, photos: int, needs_review: int, pending: int, failed: int, thumbnails: list[str])`.
      `thumbnails` holds up to four ready photo ids: the reference first and then capture order
      for a procedure case, and slot order for a composite case.
    - `PatientView(id, name, note, created_at, cases: list[CaseSummary])`,
      `LibraryProblemView(folder, message)`, and
      `PatientsView(patients: list[PatientView], problems: list[LibraryProblemView])`.
    - `PatientPatch(name: str | None = None, note: str | None = None)`. Only the fields present
      in `model_fields_set` change; `"note": null` clears the note.
    - `CaseCreate(type: CaseType, title: str, patient_id: str | None = None, patient_name: str | None = None, patient_note: str | None = None)`.
    - `CaseView`, with the fields of `CaseDetails`.
    - `SequencePatch(photo_ids: list[str], gap_px: int = 0)`.
    - `CompositePatch(baseline_photo_id: str | None = None, cell_size: tuple[int, int] | None = None, gap_px: int | None = None, gap_color: str | None = None)`.
      The route merges the fields present in `model_fields_set` into the case's current block
      and passes the result as `layout`. `"baseline_photo_id": null` returns the baseline to the
      default slot.
    - `CasePatch(title: str | None = None, output_scale: float | None = None, sequence: SequencePatch | None = None, composite: CompositePatch | None = None)`.
    - `ManualEdit(transform: list[float], model: AlignModel)`.
    - `PhotoPatch(stage: Stage | None = None, slot: Slot | Literal["tray"] | None = None, flip: Flip | None = None, edit: ManualEdit | None = None, reset: Literal[True] | None = None)`.
      Exactly one field must be present; otherwise 422.
    - `SlotMap(slots: dict[Slot, str])`.
    - `ExportRequest(include: list[ExportInclude] | None = None, conflict: ConflictMode = "keep_both")`.
      The default never replaces anything.
    - `SkippedView(name, reason: UploadSkipReason, message)` and
      `UploadResult(job_id: str | None, added: list[str], skipped: list[SkippedView])`.
    - `ImportResult(status: Literal["imported", "skipped"], case_id, patient_id, title)`.

**Rules:**

- **`services.cases`.**
  - `create_case` needs exactly one of `patient_id` and `patient_name`; otherwise
    `InvalidRequestError`. A composite case gets `composite_block(settings)`.
  - `change_case` applies its arguments in parameter order:

    | Argument | Effect |
    |---|---|
    | `title` | `library.rename_case`; refused with `JobRunningError` while `jobs.active(case_id)` |
    | `output_scale` | `procedure_case.set_output_scale` |
    | `sequence` | `procedure_case.set_sequence` |
    | `layout` | `composite_case.set_layout` |

  - `delete_case` raises `JobRunningError` while a job of the case is active. Otherwise it calls
    `trash_case`.
  - `update_patient` refuses a rename while a job of any of the patient's cases, or the
    patient's export, is active.
  - `delete_photo`:
    - refuses the reference with `InvalidRequestError("Replace the reference instead of deleting it.")`;
    - removes the photo, its id in `sequence.photo_ids`, and a matching `baseline_photo_id`;
    - deletes its original when no other record uses the file;
    - replans a composite case.
  - `upload_photos` calls `procedure_case.upload` or `composite_case.upload`, by case type.
- **Uploads.**
  - The parameters are `files: list[UploadFile] = File(...)` and
    `last_modified: list[int] | None = Form(None)`.
  - When `last_modified` is given, its length must equal the number of files; otherwise 422
    `invalid_request`.
  - Each file becomes `Upload(display_name(file.filename or ""), file.file, datetime.fromtimestamp(ms / 1000))`.
  - The service runs through `run_cpu`, because it copies and hashes the files.
- **Downloads.**
  - Images return `Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})`.
  - Zips return `FileResponse(path, media_type="application/zip", filename=name, background=BackgroundTask(path.unlink, missing_ok=True))`.
    Starlette writes a `filename*=` header for non-ASCII patient names, and the zip is deleted
    after sending (§4.9).
- **Routes** (all under `/api`):

  | Method & path | Request | Response | Service |
  |---|---|---|---|
  | `GET /patients` | | `PatientsView` | `library().listing()` |
  | `PATCH /patients/{id}` | `PatientPatch` | `PatientView` | `cases.update_patient` |
  | `POST /cases` | `CaseCreate` | 201 `CaseView` | `cases.create_case` |
  | `GET /cases/{id}` | | `CaseView` | `cases.case_details` |
  | `PATCH /cases/{id}` | `CasePatch` | `CaseView` | `cases.change_case` (`run_cpu`) |
  | `DELETE /cases/{id}` | | 204 | `cases.delete_case` |
  | `POST /cases/{id}/photos` | multipart `files`, `last_modified` | `UploadResult` | `cases.upload_photos` |
  | `PUT /cases/{id}/reference` | multipart `file`, `last_modified` (optional) | `UploadResult` | `procedure_case.set_reference` |
  | `PATCH /cases/{id}/photos/{pid}` | `PhotoPatch` | `CaseView` | see the dispatch below (`run_cpu`) |
  | `DELETE /cases/{id}/photos/{pid}` | | `CaseView` | `cases.delete_photo` (`run_cpu`) |
  | `GET /cases/{id}/photos/{pid}/image` | `variant` (default `processed`), `size` (default `preview`) | JPEG | `renders.photo_image` (`run_cpu`) |
  | `PUT /cases/{id}/composite/slots` | `SlotMap` | `CaseView` | `composite_case.set_slots` (`run_cpu`) |
  | `POST /cases/{id}/composite/assign` | | `JobRef` | `composite_case.start_auto_assign` |
  | `GET /cases/{id}/render/sequence` | `size` (default `preview`) | JPEG | `renders.sequence_image` (`run_cpu`) |
  | `GET /cases/{id}/render/composite` | `size` (default `preview`) | JPEG | `renders.composite_image` (`run_cpu`) |
  | `POST /cases/{id}/export` | `ExportRequest` | `JobRef` | `export.start_case_export` |
  | `GET /cases/{id}/export/{job_id}/zip` | | zip | `export.export_zip(case_id=…)` (`run_cpu`) |
  | `POST /patients/{id}/export` | `ExportRequest` | `JobRef` | `export.start_patient_export` |
  | `GET /patients/{id}/export/{job_id}/zip` | | zip | `export.export_zip(patient_id=…)` (`run_cpu`) |
  | `GET /cases/{id}/archive` | | zip | `write_case_archive` into `dirs.exports/<new_id()>.zip`, named `case_archive_name(patient, title, created_at.date())` (`run_cpu`) |
  | `POST /archive/import` | multipart `file`; query `on_conflict` (default `ask`) | 201 `ImportResult` (200 when skipped) | copies the upload to `dirs.exports`, then `import_case_archive` and deletes the copy (`run_cpu`) |

- **`PhotoPatch` dispatch.** A field that does not fit the case type raises
  `InvalidRequestError` in the service (Tasks 9 and 10):

  | Field | Procedure case | Composite case |
  |---|---|---|
  | `stage` | `set_stage` | refused |
  | `slot` | refused | `move_photo` (`"tray"` → `None`) |
  | `flip` | refused | `set_flip` |
  | `edit` | `procedure_case.apply_manual_edit` | `composite_case.apply_manual_edit` (`edit.model` must be `similarity`) |
  | `reset` | `procedure_case.reset_edit` | `composite_case.reset_edit` |

- [ ] **Step 1: Write the failing tests**

Jobs are awaited by polling `GET /api/jobs/{id}` until finished, with a 120 s limit.

`test_api_library.py`:

| Test | Assertions |
|---|---|
| `test_patients_and_cases_lifecycle` | `POST /cases` with a new patient → 201; `GET /patients` shows the patient and a `CaseSummary`; a second case by `patient_id`; `PATCH` the patient's note; `PATCH` the case title → the folder is renamed and `GET` still works; `DELETE` → 204, a later `GET` → 404 `case_not_found`, and the folder is in `.trash` |
| `test_creating_a_case_needs_exactly_one_patient` | both or neither → 422 |
| `test_mixed_uploads_store_only_photos` (§8.2 item 5) | upload a JPEG, a HEIC (made with `pillow_heif`), a `.MOV`, a `.AAE`, and a DNG → `added` has two ids; `skipped` names the other three with reasons `video`, `sidecar`, and `raw`; the case holds two photos |
| `test_last_modified_must_match_the_files` | two files and one `last_modified` → 422 |
| `test_case_archive_round_trip` | `GET …/archive` → a zip named `Alex Wang_implant 16_Archive_<today>.zip` (the case was created today); `POST /archive/import` into a second app (another `ctx` and client) → 201; again → 409 `case_exists`, with `details.case_id`; `on_conflict=copy` → 201 with a new case id |
| `test_damaged_library_entries_are_listed_as_problems` | a damaged `case.json` → `GET /patients` → 200 with one problem |
| `test_renaming_and_deleting_wait_for_jobs` | a job on the case held on an `Event` → `PATCH` the title → 409 `job_running`; `DELETE` → 409 |
| `test_photo_changes_check_the_case_type` | `PATCH` a `slot` on a procedure photo → 422 |

`test_api_procedure.py` (§8.2 item 1):

| Test | Assertions |
|---|---|
| `test_procedure_flow` | create → `PUT reference` → wait → `POST photos` (`intra`, `post`) → wait → `GET` the case: statuses `ok`, stages `pre_op` / `intra_op` / `post_op`; `PATCH` a stage; `PATCH` an edit with `truth` → `source == "manual"`; images: thumb, preview, and full at (320, 213), (1200, 800), and (1200, 800); `GET render/sequence` → a JPEG; `POST export` → wait → the four names of Task 12; `GET` the export zip → those names inside; afterwards `dirs.exports` holds no zip |
| `test_photo_patch_needs_exactly_one_change` | `{}` and `{"stage": "post_op", "reset": true}` → 422 |
| `test_deleting_the_reference_is_refused` | 422 |
| `test_an_unprocessed_image_is_not_ready` | the processed variant of a pending photo → 409 `not_ready` |

`test_api_composite.py` (§8.2 item 2):

| Test | Assertions |
|---|---|
| `test_composite_flow` | fake model installed; create a composite case → upload nine → wait → row-major slots; `PUT composite/slots` swapping two → the `CaseView` shows the swap; `PATCH` a flip; `GET render/composite` → a (1600, 1067) JPEG; `POST export` → wait → `Alex Wang_full mouth_Composite_20261016.jpg` exists; the sample store holds 9 samples with source `export` |
| `test_reassign_returns_to_automatic_slots` | after a swap, `POST composite/assign` → wait → `slots_source == "auto"` and row-major slots |

`test_api_export.py` (§8.2 item 3):

| Test | Assertions |
|---|---|
| `test_patient_export_across_case_types` | one patient with a procedure case, a composite case, and a third case for which `export.export_case` is monkeypatched to raise → `POST /patients/{id}/export` → wait → `failed_cases` lists the third; the zip `Alex Wang_Export_<today>.zip` holds two folders, named like the two case folders, with their files |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/integration/test_api_library.py tests/integration/test_api_procedure.py tests/integration/test_api_composite.py tests/integration/test_api_export.py -q`
Expected: FAIL with 404 responses (no routes).

- [ ] **Step 3: Implement `services/cases.py`, the models, and the routes**

- [ ] **Step 4: Run the tests and all gates**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dental_image_organizer/services/cases.py src/dental_image_organizer/api tests/integration
git commit -m "feat(api): serve patients, cases, photos, renders, exports, and archives"
```

---

### Task 16: Model, classifier, and teaching routes

**Files:**

- Create: `src/dental_image_organizer/api/routes/model.py`, `api/routes/classifier.py`
- Modify: `src/dental_image_organizer/api/models.py`, `src/dental_image_organizer/api/app.py`
- Test: `tests/integration/test_api_ai.py`

**Interfaces:**

- Consumes: Tasks 7, 13, and 14.
- Produces, in `api.models`:
  - `ModelStatus(available: bool, size_bytes: int, host: str, download_job_id: str | None)`.
    `host` is `urlparse(spec.url).hostname`, so the UI can say where the download comes from
    (§4.6).
  - `FlipStatusView(effective: Flip, start: Flip, learned: Flip | None, pinned: bool, confirmations: int)`.
  - `ClassifierView`:
    - `trained: bool`, `min_samples_per_slot: int` (3);
    - `counts: dict[Slot, int]`, `stale_samples: int`, `damaged: str | None`;
    - `model_available: bool`;
    - `flips: dict[Slot, FlipStatusView]`.
  - `TeachPhotoView(id, name, captured_at, state, error)`.
  - `TeachSessionView(id, state, photos: list[TeachPhotoView], proposal: dict[Slot, str], job_id: str)`.
  - `TeachUpload(session_id: str | None, job_id: str | None, skipped: list[SkippedView])`.
  - `TeachConfirm(slots: dict[Slot, str], flips: dict[Slot, Flip] = {})`.

**Routes** (all under `/api`):

| Method & path | Request | Response | Behavior |
|---|---|---|---|
| `GET /model/status` | | `ModelStatus` | `ai.model_path() is not None`; `download_job_id` is the active job with key `model` |
| `POST /model/download` | | `JobRef` | `{job_id: null}` when the model is available; the active download's id when there is one; else a job of kind `model_download` with key `model`, whose progress is in bytes and whose result is `{"available": true}` |
| `GET /classifier/status` | | `ClassifierView` | `ai.status()` plus `flip_status(settings, ai.flips.load())` |
| `POST /classifier/retrain` | | `JobRef` | kind `retrain`, key `classifier`; result `{"trained": bool}` |
| `POST /classifier/teach` | multipart `files`, `last_modified` | 201 `TeachUpload` | `ctx.teach.start` (`run_cpu`) |
| `GET /classifier/teach/{sid}` | | `TeachSessionView` | |
| `GET /classifier/teach/{sid}/photos/{pid}/image` | `size`: `thumb` or `preview` | JPEG | |
| `POST /classifier/teach/{sid}/confirm` | `TeachConfirm` | `ClassifierView` | `ctx.teach.confirm` (`run_cpu`) |
| `DELETE /classifier/teach/{sid}` | | 204 | `ctx.teach.cancel` |

- [ ] **Step 1: Write the failing tests**

| Test | Assertions |
|---|---|
| `test_model_status_and_download` | without the model: `available` false, `host == "github.com"`; with `ctx` built with an `http_client` that serves the fake model through `httpx.MockTransport` and a fake `ModelSpec` for it, `POST /model/download` → wait → `available` true; a second `POST` → `{"job_id": null}` |
| `test_teaching_flow` (§8.2 item 4) | fake model; three `view_set` sets through `POST /classifier/teach`, wait, and confirm the proposal → `GET /classifier/status` → `trained`, every count 3; every stored sample has source `teach`; `dirs.teach` is empty; a fourth session deleted with `DELETE` → its folder is gone; `GET /patients` → no patients |
| `test_teaching_without_the_model_is_refused` | 503 `model_unavailable` |
| `test_classifier_status_shows_the_flip_defaults` | with `flip_pins={"lower_left": "none"}`: `flips.lower_left` is `{"effective": "none", "pinned": true, …}` and `flips.upper_right.effective == "horizontal"` |
| `test_retrain_rebuilds_the_classifier` | after teaching three sets, delete `model.json` → `POST /classifier/retrain` → wait → result `{"trained": true}`; `model.json` exists again |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/integration/test_api_ai.py -q`
Expected: FAIL with 404 responses.

- [ ] **Step 3: Implement the routes and models**

- [ ] **Step 4: Run the tests and all gates**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dental_image_organizer/api tests/integration/test_api_ai.py
git commit -m "feat(api): serve the AI model download, classifier status, and teaching"
```

---

### Task 17: `dio serve`: single instance, ports, `--detach`, and the log file

**Files:**

- Modify: `src/dental_image_organizer/log.py`
- Create: `src/dental_image_organizer/server.py`
- Modify: `src/dental_image_organizer/cli.py`
- Modify: `pyproject.toml`
  - Add `dental_image_organizer.server` to every forbidden list.
  - Add `server` to the `api` contract.
- Modify: `README.md`
- Modify: `tests/conftest.py` (restore the logging configuration after each test)
- Test: `tests/unit/test_log.py`, `tests/unit/test_server.py`, `tests/integration/test_serve_process.py`

**Interfaces:**

- Consumes: Tasks 5, 8, and 14.
- Produces:
  - `log.configure_logging(level: str = "INFO", *, log_file: Path | None = None) -> None`.
    - Without `log_file`, the behavior is unchanged; the three existing tests pin it.
    - With `log_file`, logging is stdlib-backed, as prototyped:
      - structlog's processors end in `ProcessorFormatter.wrap_for_formatter`, with
        `logger_factory=structlog.stdlib.LoggerFactory()`;
      - a `RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=4, encoding="utf-8")`
        renders `format_exc_info` then `JSONRenderer(ensure_ascii=False)`;
      - a `StreamHandler(sys.stderr)` renders today's `ConsoleRenderer`;
      - both formatters share the pre-chain (`merge_contextvars`, `add_log_level`,
        `TimeStamper`) as `foreign_pre_chain`, so uvicorn's stdlib records are formatted too;
      - the root logger gets exactly these two handlers.
  - `server`:
    - Constants: `PORTS = range(8765, 8776)`, `HEALTH_TIMEOUT_S = 20.0`,
      `READY_TOKEN_ENV = "DIO_READY_TOKEN"`, `POLL_S = 0.1`.
    - Functions:

      | Function | Returns |
      |---|---|
      | `candidate_ports(requested: int \| None, previous: int \| None)` | `list[int]` |
      | `bind_first_free(ports: Sequence[int])` | `socket.socket` |
      | `healthy(port: int)` | `bool` |
      | `open_ui(port: int)` | `None`: `webbrowser.open(f"http://127.0.0.1:{port}/")` |
      | `serve(*, port: int \| None, open_browser: bool, verbose: bool = False)` | `int`, the exit code |
      | `serve_detached(*, port: int \| None, open_browser: bool, verbose: bool = False)` | `int`, the exit code |

  - CLI: `dio serve [--port N] [--no-browser] [--detach] [-v/--verbose]`. `--port` must be
    1024–65535.

**Rules:**

- **`candidate_ports`** returns `[requested]` when one is given. Otherwise it returns `previous`
  (if any) followed by `PORTS`, without repeats.
- **`bind_first_free`.**
  - It sets `SO_EXCLUSIVEADDRUSE` on Windows, where `SO_REUSEADDR` would let another program take
    the port, and `SO_REUSEADDR` elsewhere, so a restart can rebind while old connections are in
    `TIME_WAIT`.
  - It binds `127.0.0.1` and calls `listen(128)`.
  - With none free, it raises `DioError(f"Ports {first}–{last} are all in use; quit the program that uses them, or pass --port.")`.
    For a single requested port the message is `f"Port {port} is in use."`.
- **`healthy`** sends `GET http://127.0.0.1:{port}/api/health` with `httpx`, a 1 s timeout, and
  `trust_env=False`. Otherwise an `HTTP_PROXY` in the environment could route the local check
  through a proxy.
- **`serve`.**
  1. Build `dirs = app_dirs()`, then call
     `configure_logging("DEBUG" if verbose else "INFO", log_file=dirs.log_file)`.
  2. If `InstanceLock(dirs.instance_lock).try_acquire()` fails, another instance owns the
     library:
     - wait up to `HEALTH_TIMEOUT_S` for `read_instance` plus `healthy(info.port)`, since the
       other instance may still be starting;
     - then call `open_ui`, or print
       `Dental Image Organizer is already running at http://127.0.0.1:{port}/`, and return 0;
     - on timeout, print
       `error: Another Dental Image Organizer process holds {dirs.instance_lock} but does not answer; see {dirs.log_file}`
       to stderr and return 1.
  3. `ctx = create_context(dirs)`. A `StorageError` prints `error: <message>` and returns 1.
  4. `sock = bind_first_free(candidate_ports(port, previous port from read_instance))`. A
     `DioError` prints and returns 1.
  5. `write_instance(…, InstanceInfo(port, os.getpid(), __version__, os.environ.get(READY_TOKEN_ENV)))`.
     The file stays after exit, because the next run reuses its port.
  6. Build `server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="on"))`
     and `app = create_app(ctx, port=…, shutdown=lambda: setattr(server, "should_exit", True))`.
     The access log is off; it would only add noise.
  7. Print `Dental Image Organizer is running at http://127.0.0.1:{port}/ (press Ctrl+C to stop)`,
     then call `open_ui` if `open_browser`. The pre-bound socket already queues connections, so
     the browser may connect before `run` starts.
  8. Call `server.run(sockets=[sock])`. Finally call `ctx.close()` and release the lock. Return
     0.
- **`serve_detached`.**
  1. If the lock is held, act as `serve` step 2; the parent never starts a second server.
     Otherwise release it at once, because the child takes it.
  2. Create `token = secrets.token_hex(16)` and start
     `[sys.executable, "-m", "dental_image_organizer", "serve", "--no-browser", …port and verbose flags]`
     with:
     - `stdin`, `stdout`, and `stderr` on `subprocess.DEVNULL`;
     - `close_fds=True`;
     - the environment plus `READY_TOKEN_ENV=token`;
     - POSIX: `start_new_session=True`. Windows:
       `creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` (§4.3).
  3. Every `POLL_S`, for up to `HEALTH_TIMEOUT_S`, check that `read_instance` carries the token
     and that its port is `healthy`. Then call `open_ui` if `open_browser`, and return 0.
  4. If the child exits early, another launch won the race. If a healthy instance is recorded,
     act as on success; otherwise stop waiting.
  5. On timeout or early exit without a healthy instance, print
     `error: The server did not start within 20 s; see {dirs.log_file}` to stderr and return 1.

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py` gets an autouse fixture that restores the root logger's handlers and the
structlog configuration after each test. `serve()` in `test_server.py` configures file logging
too, and must not leak it into later tests.

- `test_file_logging_writes_json_lines`: `configure_logging("INFO", log_file=…)`, then
  `log.info("server_started", case_id="c1")`. The file's last line is JSON with `event`,
  `level`, `case_id`, and `timestamp`, and stderr has a readable line.
- `test_file_logging_keeps_locals_out_of_tracebacks`: the existing exception test, against the
  file. `exception` contains `ValueError: boom for 9 chars` and not `Alex Wang`.
- `test_stdlib_records_reach_the_file`: `logging.getLogger("uvicorn.error").info("Uvicorn running")`
  appears in the file.

`test_server.py`:

| Test | Assertions |
|---|---|
| `test_candidate_ports` | `(None, None)` → 8765…8775; `(None, 8770)` → 8770 first, then the others once; `(None, 9000)` → 9000 first; `(8800, 8770)` → `[8800]` |
| `test_bind_first_free_skips_a_busy_port` | two free ports found by binding port 0; the first held by a listening socket → the second is returned |
| `test_bind_first_free_reports_when_all_are_busy` | → `DioError` |
| `test_serve_opens_the_running_instance_instead_of_starting` | an `InstanceLock` held by the test and an `instance.json` with port 8799; monkeypatch `server.healthy` → `True`, `server.open_ui` → a recorder, and `server.create_context` → raise → `serve(port=None, open_browser=True) == 0`, and the recorder got 8799 |

`test_serve_process.py` uses real processes. Each test sets `DIO_APP_DIR=tmp_path/"app"` and a
free port `P` (found by binding port 0). A fixture shuts down any server it started:
`POST /api/system/shutdown` with `Origin`, and failing that, kill the pid recorded in
`instance.json`.

| Test | Assertions |
|---|---|
| `test_detached_server_starts_answers_and_quits` | `python -m dental_image_organizer serve --detach --no-browser --port P` exits 0 within 30 s; `/api/health` → 200; `POST /api/system/shutdown` → 202; the port refuses connections within 10 s; a new `InstanceLock` then acquires; `dio.log` holds JSON lines |
| `test_a_second_launch_opens_the_running_server` (Review Focus 3) | with one server running, a second `serve --detach --no-browser` exits 0 within 10 s and prints `already running`; the pid in `instance.json` is unchanged |
| `test_restart_reuses_the_recorded_port` | start with `--port P`, shut down, start again without `--port` → `instance.json` records `P` |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_log.py tests/unit/test_server.py tests/integration/test_serve_process.py -q`
Expected: FAIL with `ModuleNotFoundError: dental_image_organizer.server`.

- [ ] **Step 3: Implement the log file, `server.py`, and the `serve` command**

- [ ] **Step 4: Update the README**

- The status line: the procedure and composite pipelines are available as CLI tools, and the
  local server API as `dio serve`; the app UI is not built yet.
- Under Development:
  - `uv run dio serve --no-browser`, and the schema at `/api/openapi.json`;
  - `DIO_APP_DIR` for a throw-away set of app folders;
  - `dio export-case` / `dio import-case` (Task 5).

- [ ] **Step 5: Run the tests and all gates**

Run: `uv run pytest -q && uv run ruff check && uv run ruff format --check && uv run mypy && uv run lint-imports`
Expected: all pass.

- [ ] **Step 6: Smoke-test by hand on the development host**

Run: `uv run dio serve --no-browser`. Then, in another terminal:

```bash
curl -s http://127.0.0.1:8765/api/health
curl -s -X POST -H "Origin: http://127.0.0.1:8765" http://127.0.0.1:8765/api/system/shutdown
```

Expected: `{"status":"ok","version":"…"}`, then `{"status":"stopping"}`, and the server exits.

- [ ] **Step 7: Commit**

```bash
git add src/dental_image_organizer/log.py src/dental_image_organizer/server.py src/dental_image_organizer/cli.py pyproject.toml README.md tests/conftest.py tests/unit/test_log.py tests/unit/test_server.py tests/integration/test_serve_process.py
git commit -m "feat: add dio serve with a single instance, port reuse, --detach, and a JSON log"
```
