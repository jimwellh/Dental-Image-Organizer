# Delete a Patient Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the dentist delete a patient, with all of its cases, from the Library screen. The
deletion moves the patient folder into `.trash/`, where Finder can restore it.

**Architecture:**

- **Storage:** `Library.trash_patient` renames the whole patient folder into `.trash/` in one
  step, under the patient's case locks and the I/O lock.
- **Service:** `cases.delete_patient` refuses while any related job runs.
- **API:** `DELETE /patients/{id}` exposes it.
- **Frontend:** the Library screen gets a Delete button and a confirm dialog.

**Tech Stack:** Python 3.12, FastAPI, pytest, and structlog for the backend. React 19, TanStack
Query, Vitest, and Testing Library for the frontend.

**Spec:** [doc/superpowers/specs/2026-11-24-delete-patient-design.md](../specs/2026-11-24-delete-patient-design.md)
is this change's short design. Library layout and API conventions come from
[the main design](../specs/2026-10-16-dental-image-organizer-design.md), §4.11 and §4.12.

## Global Constraints

- **Code and comments:** English only. Comments explain why, not what.
- **Deletion:** move the folder into `.trash/` only. Never `shutil.rmtree` a patient folder.
- **Lock order:** take case locks, in sorted id order, before the I/O lock. Never take them the
  other way round (the `storage/library.py` module docstring).
- **Logs:** never include a patient name, note, or case title. Bind ids only.
- **Generated API files:** `frontend/src/api/openapi.json` and `frontend/src/api/schema.d.ts`
  come from `npm run gen:api` and are never edited by hand. CI diffs them.
- **UI copy:** lives in `frontend/src/strings/en.ts` only.
- **Checks:** backend `ruff check`, `ruff format --check`, `mypy`, `lint-imports`, and `pytest`
  must pass. Frontend `npm run check` and `npm run build` must pass.

## Review Focus

1. **The rename fails** because a file in the folder is open in Explorer or Preview, or access
   is denied. Expect `StorageError` telling the user to close the window. The patient and its
   cases stay listed and readable. Test: Task 1,
   `test_a_failed_trash_patient_leaves_the_patient_in_place`.
2. **`.trash/` already holds a folder of that name**, from earlier case deletions or from
   another patient with the same name. Expect a new `<name> (2)`. Nothing is merged or
   overwritten. Test: Task 1, `test_trash_patient_never_merges_into_an_existing_trash_folder`.
3. **The folder holds files added in Finder** that are not cases. They move with the patient.
   Test: Task 1, `test_trash_patient_moves_the_whole_folder`.
4. **Double-click on confirm, or a repeated request.** Expect exactly one `DELETE`. A second
   request returns 404 `patient_not_found`. Tests: Task 2,
   `test_deleting_a_patient_moves_its_folder_to_the_trash`, and Task 3,
   `deleting a patient asks first and sends one request`.
5. **Privacy in logs.** The deletion logs `patient_trashed` with no name. Test: Task 2, the
   extended `test_library_changes_never_log_names`.

---

### Task 1: `Library.trash_patient`

**Files:**
- Modify: `src/dental_image_organizer/storage/library.py`. This covers the module docstring,
  `update_patient` (lines 423-446), and a new method after `remove_empty_patient`.
- Test: `tests/unit/test_library.py`

**Interfaces:**
- Produces:
  - `Library.trash_patient(self, patient_id: str) -> Path`. It returns the patient's new folder
    inside `.trash/`. It raises `PatientNotFoundError` for an unknown id and `StorageError` when
    the folder cannot be moved.
- Internal:
  - `Library._locked_patient(self, patient_id: str) -> AbstractContextManager[PatientEntry]`.
    It is a `@contextmanager` that holds every case lock of the patient (sorted ids), then the
    I/O lock, and yields the current `PatientEntry`. `update_patient` and `trash_patient` both
    use it.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_library.py`. Add
  `PatientNotFoundError` to the `dental_image_organizer.errors` import.

```python
def test_trash_patient_moves_the_whole_folder(lib: Library) -> None:
    patient = lib.create_patient("Alex Wang", None)
    first = lib.create_case(patient.id, "procedure", "implant 16")
    lib.create_case(patient.id, "procedure", "implant 26")
    (lib.root / "Alex Wang" / "notes.txt").write_text("added in Finder")

    target = lib.trash_patient(patient.id)

    assert target == lib.root / ".trash" / "Alex Wang"
    assert not (lib.root / "Alex Wang").exists()
    assert (target / "patient.json").is_file()
    assert (target / "notes.txt").is_file()
    assert sorted(p.name for p in target.iterdir() if p.is_dir()) == [
        "20261016 implant 16",
        "20261016 implant 26",
    ]
    assert lib.listing().patients == ()
    with pytest.raises(PatientNotFoundError):
        lib.patient_record(patient.id)
    with pytest.raises(CaseNotFoundError):
        lib.read_case(first.id)


def test_trash_patient_never_merges_into_an_existing_trash_folder(lib: Library) -> None:
    patient = lib.create_patient("Alex Wang", None)
    earlier = lib.create_case(patient.id, "procedure", "implant 16")
    lib.trash_case(earlier.id)  # makes .trash/Alex Wang/

    target = lib.trash_patient(patient.id)

    assert target == lib.root / ".trash" / "Alex Wang (2)"
    assert (target / "patient.json").is_file()
    assert (lib.root / ".trash" / "Alex Wang" / "20261016 implant 16" / "case.json").is_file()


def test_a_failed_trash_patient_leaves_the_patient_in_place(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    patient = lib.create_patient("Alex Wang", None)
    case = lib.create_case(patient.id, "procedure", "implant 16")

    def refuse(self: Path, target: Path) -> Path:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "rename", refuse)
    with pytest.raises(StorageError, match="Close any window"):
        lib.trash_patient(patient.id)
    monkeypatch.undo()

    assert [e.record.id for e in lib.listing().patients] == [patient.id]
    assert lib.read_case(case.id).id == case.id
```

  Add `test_trash_patient_waits_for_case_locks`. Copy
  `test_update_patient_waits_for_case_locks` (line 354), with these changes:
  - the second thread runs `lib.trash_patient(patient.id)`;
  - while the lock is held, assert `(lib.root / "Alex Wang").exists()`;
  - after release, assert `(lib.root / ".trash" / "Alex Wang").is_dir()` and
    `not (lib.root / "Alex Wang").exists()`.

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `uv run pytest tests/unit/test_library.py -v -k trash_patient`

  Expected: 4 FAIL with `AttributeError: 'Library' object has no attribute 'trash_patient'`.

- [ ] **Step 3: Extract `_locked_patient` and implement `trash_patient`**

  - **`_locked_patient`:** move the body of `update_patient`'s `while True` loop into it. That
    body lists the ids, enters each case lock and then `self._io` on one `ExitStack`, re-reads
    the entry, and retries when the ids changed. The context manager `yield`s the entry on a
    match and returns after the yield, so the locks are released when the caller's `with`
    block exits.
  - **`update_patient`:** keeps only the rename and the write, inside
    `with self._locked_patient(patient_id) as entry:`.
  - **`trash_patient`:** under `_locked_patient`:
    - `(self._root / TRASH_DIR).mkdir(parents=True, exist_ok=True)`, with an `OSError` raised
      as `StorageError(f"Moving the patient to the trash failed: {_reason(exc)}")`;
    - `target = trash / self._unique(trash, entry.folder.name)`;
    - `self._rename(entry.folder, target)`;
    - return `target`.

    Do not clean the index by hand. `_indexed` sees that the files are gone, and the next lookup
    rescans, as after `trash_case`.
  - **Module docstring:** change the `update_patient` sentence to name both callers:
    "`update_patient` and `trash_patient` move a folder that holds many cases, so they take all
    of its case locks first…"

- [ ] **Step 4: Run the tests to verify they pass**

  Run: `uv run pytest tests/unit/test_library.py -v`

  Expected: all PASS. That is 24 existing tests plus 4 new ones; the existing
  `test_update_patient_waits_for_case_locks` checks the extraction.

- [ ] **Step 5: Lint, type-check, and commit.** The design and this plan go into the first
  commit.

```bash
uv run ruff check && uv run ruff format --check && uv run mypy
git add src/dental_image_organizer/storage/library.py tests/unit/test_library.py \
  doc/superpowers/specs/2026-11-24-delete-patient-design.md \
  doc/superpowers/plans/2026-11-24-delete-patient.md
git commit -m "feat(storage): move a patient folder to the trash"
```

---

### Task 2: `DELETE /patients/{id}`, the job check, and the spec

**Files:**
- Modify: `src/dental_image_organizer/services/cases.py`
- Modify: `src/dental_image_organizer/api/routes/patients.py`
- Modify: `doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md`. This covers the
  `.trash/` comment in §4.11 (line 748) and the `PATCH /patients/{id}` row in the §4.12 table
  (line 853).
- Regenerate: `frontend/src/api/openapi.json` and `frontend/src/api/schema.d.ts`
- Test: `tests/integration/test_api_library.py`

**Interfaces:**
- Consumes: `Library.trash_patient(patient_id: str) -> Path` (Task 1).
- Produces:
  - `cases.delete_patient(ctx: AppContext, patient_id: str) -> None`;
  - route `DELETE /api/patients/{patient_id}`, which returns 204 with no body, 404
    `patient_not_found`, or 409 `job_running`;
  - the generated `schema.d.ts` entry that Task 3's `deletePatient` type-checks against.

- [ ] **Step 1: Write the failing tests** in `tests/integration/test_api_library.py`. Import
  `from dental_image_organizer.services.export import patient_export_key`.

```python
def test_deleting_a_patient_moves_its_folder_to_the_trash(
    client: TestClient, ctx: AppContext
) -> None:
    created = _create(client)
    case_id, patient_id = created["case"]["id"], created["patient"]["id"]
    folder = ctx.library().case_dir(case_id).parent

    deleted = client.delete(f"/api/patients/{patient_id}")

    assert deleted.status_code == 204
    assert client.get("/api/patients").json() == {"patients": [], "problems": []}
    assert _error(client.get(f"/api/cases/{case_id}"))["code"] == "case_not_found"
    assert (ctx.library().root / ".trash" / folder.name / "patient.json").is_file()
    again = client.delete(f"/api/patients/{patient_id}")
    assert again.status_code == 404
    assert _error(again)["code"] == "patient_not_found"


def test_deleting_a_patient_waits_for_its_export(client: TestClient, ctx: AppContext) -> None:
    patient_id = _create(client)["patient"]["id"]
    release = threading.Event()
    job = ctx.jobs.submit("hold", _held(release), key=patient_export_key(patient_id))
    try:
        refused = client.delete(f"/api/patients/{patient_id}")
    finally:
        release.set()
    ctx.jobs.wait(job.id, WAIT_SECONDS)

    assert refused.status_code == 409
    assert _error(refused)["code"] == "job_running"
    assert client.delete(f"/api/patients/{patient_id}").status_code == 204
```

  Extend `test_renaming_and_deleting_wait_for_jobs` (line 384):
  - inside the `try`, add `patient_deleted = client.delete(f"/api/patients/{patient_id}")`;
  - add `patient_deleted` to the tuple that must be 409 `job_running`.

  Extend `test_library_changes_never_log_names` (line 410):
  - after `client.delete(f"/api/cases/{case_id}")`, add
    `client.delete(f"/api/patients/{patient_id}")`;
  - add `"patient_trashed"` to the required event set.

- [ ] **Step 2: Run the tests to verify they fail**

  Run:
  `uv run pytest tests/integration/test_api_library.py -v -k "deleting_a_patient or wait_for_jobs or never_log_names"`

  Expected: FAIL. The new delete calls return 405 Method Not Allowed, and `patient_trashed` is
  missing from the logs.

- [ ] **Step 3: Implement the service and the route**

  - **`services/cases.py`:**
    - Add `_PATIENT_DELETE_BUSY_MESSAGE = "Wait for the running jobs of this patient to finish before deleting it."`.
    - Extract `_refuse_while_patient_busy(ctx: AppContext, entry: PatientEntry, message: str) -> None`.
      It checks `[*(c.id for c in entry.cases), patient_export_key(entry.record.id)]`, which is
      the key list `update_patient` builds today. Use it in `update_patient`.
    - Add `delete_patient`. Under `bound_contextvars(patient_id=patient_id)`, it:
      - reads `library.patient(patient_id)`;
      - refuses with the new message;
      - calls `library.trash_patient(patient_id)`;
      - logs `log.info("patient_trashed")`.

      Its docstring cites spec §4.11, as `delete_case` does. Import `PatientEntry` from
      `storage.library` under `TYPE_CHECKING`.
  - **`api/routes/patients.py`:**
    - Add a plain-`def` route:
      `@router.delete("/patients/{patient_id}", status_code=204) def delete_patient(patient_id: str, ctx: Ctx) -> None`.
      It calls `cases.delete_patient`.
    - Update the module docstring to: "The library listing, and a patient's name, note, and
      deletion (spec §4.11, §4.12)."

- [ ] **Step 4: Amend the main spec**

  - **§4.11:** in the tree, change the `.trash/` comment to
    `# deleted cases and patients (restorable)`.
  - **§4.12:** replace the `PATCH /patients/{id}` row with:

    `` | `PATCH / DELETE /patients/{id}` | Update a patient's name or note; soft-delete the patient folder, with its cases, to `.trash` (409 `job_running` while a job of its cases or its export runs) | ``

- [ ] **Step 5: Run the tests to verify they pass, then the backend checks**

  Run:

  ```bash
  uv run pytest tests/integration/test_api_library.py -v
  uv run ruff check && uv run ruff format --check && uv run mypy && uv run lint-imports
  ```

  Expected: all PASS, and every checker exits 0.

- [ ] **Step 6: Regenerate the API types**

  Run: `cd frontend && npm run gen:api && git diff --stat -- src/api/openapi.json src/api/schema.d.ts`

  Expected: both files change, and the diff adds a `delete` operation under
  `/api/patients/{patient_id}`.

- [ ] **Step 7: Commit**

```bash
git add src/dental_image_organizer/services/cases.py \
  src/dental_image_organizer/api/routes/patients.py \
  tests/integration/test_api_library.py \
  doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md \
  frontend/src/api/openapi.json frontend/src/api/schema.d.ts
git commit -m "feat(api): delete a patient to the trash"
```

---

### Task 3: Delete button and confirm dialog on the Library screen

**Files:**
- Create: `frontend/src/features/library/PatientDeleteDialog.tsx`
- Modify: `frontend/src/api/endpoints.ts`, after `patchPatient`
- Modify: `frontend/src/strings/en.ts`, the `library` block
- Modify: `frontend/src/features/library/LibraryScreen.tsx`
- Test: `frontend/src/features/library/LibraryScreen.test.tsx`

**Interfaces:**
- Consumes: `DELETE /api/patients/{patient_id}` in the generated schema (Task 2).
- Produces:
  - `deletePatient(patientId: string): Promise<undefined>`, built with
    `sendJson<undefined>("DELETE", apiPath("/api/patients/{patient_id}", { patient_id: patientId }))`;
  - `PatientDeleteDialog(props: { patient: PatientView; onClose: () => void })`;
  - `Open` gains `| { kind: "delete"; patientId: string }`.

- [ ] **Step 1: Add the copy to `en.ts`**, inside `library`, after `exportAll`. The text is
  fixed by the design.

```ts
    delete: "Delete",
    deletePatient: {
      title: "Delete this patient?",
      // Spec §4.11: the patient folder, cases included, moves to `.trash` and can be restored by hand.
      body: {
        none: "{name} moves to the .trash folder in the library and disappears from the app.",
        one: "{name} and its case move to the .trash folder in the library. The photos stay on disk, but the patient disappears from the app.",
        other:
          "{name} and its {n} cases move to the .trash folder in the library. The photos stay on disk, but the patient disappears from the app.",
      },
      confirm: "Delete patient",
    },
```

- [ ] **Step 2: Write the failing tests** in `LibraryScreen.test.tsx`

```tsx
  it("deleting a patient asks first and sends one request", async () => {
    const server = fakeServer({
      "GET /api/patients": () => patientsView({ patients: [li] }),
      "DELETE /api/patients/:patient_id": () => undefined,
    });
    const user = userEvent.setup();
    renderWithClient(<LibraryScreen />);
    const deletes = () => server.calls.filter((call) => call.method === "DELETE");

    await user.click(await screen.findByRole("button", { name: en.library.delete }));
    const asked = screen.getByRole("dialog", { name: en.library.deletePatient.title });
    expect(asked).toHaveTextContent("Bo Li and its case move to the .trash folder");
    await user.click(within(asked).getByRole("button", { name: en.common.cancel }));
    expect(deletes()).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: en.library.delete }));
    const dialog = screen.getByRole("dialog", { name: en.library.deletePatient.title });
    await user.dblClick(
      within(dialog).getByRole("button", { name: en.library.deletePatient.confirm }),
    );

    await waitFor(() => {
      expect(deletes().map((call) => call.path)).toEqual([`/api/patients/${li.id}`]);
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("the delete dialog counts the cases", async () => {
    const two = patientView({
      id: id(3),
      name: "Mei Chen",
      folder: "Mei Chen",
      cases: [caseSummary({ id: id(11) }), caseSummary({ id: id(12) })],
    });
    fakeServer({ "GET /api/patients": () => patientsView({ patients: [wang, two] }) });
    const user = userEvent.setup();
    renderWithClient(<LibraryScreen />);
    const deleteIn = async (name: string) => {
      const section = await screen.findByRole("region", { name });
      await user.click(within(section).getByRole("button", { name: en.library.delete }));
      return screen.getByRole("dialog", { name: en.library.deletePatient.title });
    };

    expect(await deleteIn("Mei Chen")).toHaveTextContent("Mei Chen and its 2 cases move");
    await user.click(screen.getByRole("button", { name: en.common.cancel }));
    const none = await deleteIn(DECOMPOSED_CAFE);
    expect(none).toHaveTextContent(`${DECOMPOSED_CAFE} moves to the .trash folder`);
    expect(none).not.toHaveTextContent("photos stay");
  });
```

- [ ] **Step 3: Run the tests to verify they fail**

  Run: `cd frontend && npx vitest run src/features/library/LibraryScreen.test.tsx`

  Expected: the 2 new tests FAIL because no button is named "Delete". The existing tests pass.

- [ ] **Step 4: Implement**

  - **`endpoints.ts`:** add `deletePatient` as in Interfaces.
  - **`PatientDeleteDialog.tsx`:** follow `PatientEditDialog` and `CaseHeader`'s delete flow.
    - Render a `ConfirmDialog` with `title`, `confirmLabel`, and `onCancel={onClose}`. Fill the
      body with `fill(form, { name: patient.name, n: String(patient.cases.length) })`. `form` is
      `body.none`, `body.one`, or `body.other`, for 0, 1, or more cases.
    - Use a `useMutation` whose `onSuccess` does two things. It invalidates
      `queryKeys.patients` and `["case"]`, because a cached case of this patient is now gone.
      Then it calls `onClose()`.
    - A `useRef(false)` guard makes a double click send one request. `onError` resets it, and
      the global `MutationCache.onError` shows the error.
  - **`LibraryScreen.tsx`:**
    - Add the `delete` kind to `Open`.
    - Add a third `Button` in `PatientSection` after **Export all**, with
      `aria-describedby={headingId}` and the label `en.library.delete`. It calls
      `onOpen({ kind: "delete", patientId: patient.id })`.
    - Render `<PatientDeleteDialog>` when `open?.kind === "delete" && dialogPatient !== undefined`.
      This mirrors the `edit` case.

- [ ] **Step 5: Run the tests and the frontend checks**

  Run: `cd frontend && npm run check && npm run build`

  Expected: lint, typecheck, format, and every Vitest test pass, and the build succeeds. If
  prettier fails, run `npm run format`, then re-run.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/endpoints.ts frontend/src/strings/en.ts \
  frontend/src/features/library/PatientDeleteDialog.tsx \
  frontend/src/features/library/LibraryScreen.tsx \
  frontend/src/features/library/LibraryScreen.test.tsx
git commit -m "feat(ui): delete a patient from the library"
```

---

## Finish

- [ ] **Run CI's checks on the whole branch**

  Run:

  ```bash
  uv run ruff check && uv run ruff format --check && uv run mypy && uv run lint-imports && uv run pytest
  cd frontend && npm run gen:api && git diff --exit-code -- src/api/openapi.json src/api/schema.d.ts && npm run check && npm run build
  ```

  Expected: every command exits 0, and `git diff --exit-code` prints nothing.

- [ ] **See it work:** start the app on a throw-away library and check three things:
  - create a patient with a case;
  - delete the patient from the Library screen;
  - confirm that the patient disappears and that its folder is under `.trash/`.
