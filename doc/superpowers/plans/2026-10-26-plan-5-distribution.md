# Plan 5 — Distribution Implementation Plan

**Goal:** Put the app on a dentist's Mac and keep it current, at $0 (spec §7):

1. `scripts/install.sh`, the one-line installer (`curl … | sh`);
2. `dio install-launcher` / `dio uninstall-launcher`, which make the Dock app;
3. the daily version check, the header's update banner, "Updating…", and
   `POST /api/system/update`;
4. `release.yml` (PyPI with Trusted Publishing, GitHub Release) and the macOS smoke test, which
   CI also runs on every change.

**Spec:** [doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md](../specs/2026-10-16-dental-image-organizer-design.md).
Read §4.3, §4.10–§4.13, §5.2–§5.3, §7, and §8.2 item 6 first.

## Where This Fits

Plans 1–4 are merged (`4e61611`, `71c23a2`, `56dbc3c`, `0ab01ba`). This plan is 5 of 5. It
fills the gaps Plans 3 and 4 left open for it: `services/updates.py`, `launcher/macos.py`, the
`"updating"` connection state, and the Settings entry for the update check.

The developer has no Mac (spec §1.1). Everything macOS-specific is kept small and is built so the
logic runs anywhere:

- `install.sh` and the update script are POSIX `sh`. Tests run them under any `sh` (Git's MSYS
  `sh` on Windows) with every macOS and network command faked on `PATH`.
- `osacompile` and `codesign` are behind a runner the tests replace.
- The real install, launch through the applet, and update restart run in the macOS smoke test
  (`scripts/smoke_macos.sh`) on `macos-latest`.

## Spec Amendments

Task 1 writes these into the spec.

1. **Launcher record (§4.11, §7.3).** `uv_path` and `dio_path` live in `config/launcher.json`,
   not in `settings.json`. The page saves the settings it read (`PUT /settings`), so a server
   started before `install-launcher` ran would erase the paths on its next save, and its
   in-memory copy would not see them at all. The update reads the file when it starts.
2. **Update status (§4.12).** `GET /system/update` returns `{current, latest, available,
   checked_at, can_update, unavailable_reason, command, last_failure}`. While the check is off,
   `latest` is null and `available` false. `can_update` is false off macOS, without the launcher
   record, or when a recorded file is gone; the banner then shows `command`.
3. **Version check (§4.13).** The newest *final* release with an unyanked file is read from
   `releases` (pre-releases never count). A failed check keeps the last answer and still counts as
   the day's check. The record is `cache/update-check.json`, so restarts do not ask again.
4. **Update script (§7.3).**
   - Its output goes to `log/update.log`: `dio.log` holds JSON lines and rotates.
   - It writes `cache/update-result.json` (`ok | failed | timeout`) before it restarts the
     server; the next start reads it once and reports a failure as `last_failure`.
   - If the server does not exit within 30 s it changes nothing (`timeout`) and does not restart.
   - A second `POST` while the first update is starting is refused (409 `update_unavailable`).
5. **Page during an update (§5.3).** The page shows "Updating…" and polls `/api/health`. An
   answer counts only after a failed poll (the old server answers until it exits). A new version
   reloads the page; the same version returns to the app and shows the failure from
   `last_failure`, or says that nothing newer was installed. After 10 minutes the page shows the
   closed notice.
6. **Launcher (§7.3).**
   - The icon is drawn by code with Pillow at install time (no binary in the repository).
   - `CFBundleIconName` is removed from `Info.plist`, which would otherwise prefer the asset
     catalog's icon, and the bundle is signed again ad hoc (`codesign --sign -`) after the icon
     and `Info.plist` change.
   - The bundle is built in a temporary folder beside the target, then swapped in.
   - An existing app of the same name is replaced or removed only when its bundle id is ours.
   - When `DIO_APP_DIR` is set, the applet keeps it (the smoke test's launcher uses its own app
     folder). `--applications-dir` (hidden) puts the app elsewhere, for tests.
   - Without a `uv` on `PATH` and no `--uv-path`, the launcher is still made; the app then shows
     the Terminal command instead of "Update now".
7. **Installer overrides (§7.3).** `DIO_WHEEL`, `DIO_APPLICATIONS_DIR`, and `DIO_NO_OPEN=1`.
8. **Packaging (§4.1).** `artifacts` is set for both build targets, because `uv build` builds
   the wheel from the sdist, and the sdist ships only `src/` (plus the metadata files).
9. **CI (§7.5).** A `shell` job runs ShellCheck on the scripts; `macos-smoke` runs
   `scripts/smoke_macos.sh` against a freshly built wheel.

## Tasks

- [x] **Task 1 — Spec.** Write the amendments above into the spec.
- [x] **Task 2 — Paths, settings, errors.** `AppDirs.launcher_file`, `update_check_file`,
  `update_script`, `update_result_file`, `update_log_file`; `Settings.update_check` (default
  on); `LauncherPaths` with `read_launcher` / `write_launcher`; `UpdateUnavailableError` (409)
  and `LauncherError`. Tests: `tests/unit/test_config.py`.
- [x] **Task 3 — `services/updates.py`.** `latest_release`, `is_newer`, `UpdateService`
  (`check_due`, `check`, `blocker`, `info`, `start`), `update_script`, `take_outcome`. The context
  owns one service; the lifespan checks once an hour whether the daily check is due. Tests:
  `tests/unit/test_updates.py`, which also runs the generated script under `sh`.
- [x] **Task 4 — API.** `GET` and `POST /system/update`; `UpdateStatus` and `Updating`
  models. Tests: `tests/integration/test_api_update.py` (including spec §8.2 item 6).
- [x] **Task 5 — Launcher.** `launcher/macos.py`; `dio install-launcher [--uv-path]` and
  `dio uninstall-launcher`, which print "not supported" off macOS. An import-linter contract keeps
  the launcher off the API, services, CLI, and server. Tests: `tests/unit/test_launcher.py`,
  `tests/integration/test_cli_launcher.py`.
- [x] **Task 6 — `scripts/install.sh`.** Spec §7.3 steps 1–5. Tests:
  `tests/integration/test_install_script.py` (Linux, Intel, macOS 13, Rosetta from a file and
  from a pipe, missing uv, wheel override).
- [x] **Task 7 — Frontend.** Regenerated API types; `updateStatus` / `startUpdate`;
  `useUpdateStatus` (hourly); `UpdateBanner`; the `"updating"` state of `useServerConnection`
  and the "Updating…" notice; the Settings checkbox. Tests beside each file.
- [x] **Task 8 — Release and smoke.** `scripts/smoke_macos.sh`, `.github/workflows/release.yml`,
  and the `shell` and `macos-smoke` CI jobs.
- [x] **Task 9 — README.** Installation, update, and uninstall for users; the release steps for
  the maintainer.

## Verification

- `uv run ruff check && uv run ruff format --check && uv run mypy && uv run lint-imports`
- `uv run pytest` — on Windows the update-script timeout test is skipped, because MSYS
  `kill -0` cannot see Windows process ids.
- `npm --prefix frontend run check` and `npm --prefix frontend run gen:api` with no diff.
- `shellcheck --shell=sh scripts/install.sh scripts/smoke_macos.sh`
- `npm --prefix frontend run build && uv build`: the wheel and the sdist both carry
  `web/index.html`.
- On a Mac (CI `macos-smoke`, and the dentist's acceptance checklist, spec §8.4): install,
  launch from the Dock, update from the banner.

## Before the First Release (maintainer, once)

1. On PyPI, add a pending Trusted Publisher for `dental-image-organizer`: owner `jimwellh`,
   repository `Dental-Image-Organizer`, workflow `release.yml`, environment `pypi`.
2. In the GitHub repository settings, create the `pypi` environment (optionally with required
   reviewers).
3. Set `version` in `pyproject.toml` (for example `0.1.0`), merge, then tag `v0.1.0` and push
   the tag.
