# Delete a patient (short design)

Date: 2026-11-24. Branch: `feat/delete-patient`. Size: small change.

## Intent

A patient, once added, cannot be removed. Deleting every case moves each case to `.trash`, but
the patient folder keeps its `patient.json`, so the Library screen lists the patient forever with
"No cases yet." The dentist needs a way to remove a patient from the app.

## Goals, non-goals, anti-goals

- **Goal:** delete a patient, with all of its cases, from the Library screen.
- **Goal:** the deletion is restorable by hand in Finder, like a deleted case (spec §4.11).
- **Non-goal:** permanent deletion, emptying `.trash`, or a restore UI.
- **Non-goal:** merging the patient into a `.trash/<name>/` folder that earlier case deletions
  created.
- **Anti-goal:** a deletion that stops halfway and leaves some cases in the library and some in
  `.trash`.

## Decided in conversation

- The patient goes to `.trash`, not permanent deletion.
- A patient with cases can be deleted; its cases go with it. Because `.trash` makes this
  restorable, an ordinary confirm dialog is enough. It names the patient and the number of
  cases, and does not ask the dentist to type the name.

## Approaches

1. **Move the whole patient folder into `.trash/` with one rename (chosen).** A single rename
   cannot stop halfway, and it carries along any file added to the folder in Finder. Restoring
   it means dragging the folder back into the library. Trade-off: if earlier case deletions
   already made `.trash/Alex Wang/`, this folder becomes `.trash/Alex Wang (2)/`, so one patient
   can have two folders in `.trash`.
2. **Move each case into `.trash/<patient>/`, then remove the empty patient folder.** This keeps
   one trash folder per patient, but it takes many renames, so a failure partway through leaves a
   split state. It also needs rules for files in the folder that are not cases. Rejected.

## Design

**Storage:** `Library.trash_patient(patient_id) -> Path`.

- It takes the case locks, then the I/O lock, using the same pattern as `update_patient`: every
  case lock of the patient in sorted id order, retried when a case appears meanwhile. This keeps
  the module's lock order.
- It creates `.trash/` if needed, picks `_unique(.trash, folder.name)`, and renames the folder.
  A failed rename raises `StorageError` and leaves the folder in place.
- The index is not cleaned by hand. `_indexed` sees that the files are gone, and the next lookup
  rescans, as after `trash_case`.
- **A trashed patient folder is a whole patient, so `trash_case` must never add to it.** Before
  this change, `trash_case` reused any `.trash/<folder name>/`. Now it reuses only a folder
  whose `patient.json` has the case's patient id, and otherwise takes the next free
  `<name> (n)`. This rule was added after review. Without it, a re-created patient with the
  same name would put its deleted cases into the old patient's trash folder, and restoring
  that folder would hand them to the wrong patient.

**Service:** `cases.delete_patient(ctx, patient_id)`.

- It refuses with `JobRunningError` (409 `job_running`) while a job of any of the patient's
  cases, or the patient's export, runs. It checks the same job keys that `update_patient` checks
  for a rename, but it always checks them.
- It then calls `trash_patient` and logs `patient_trashed` with `patient_id` bound.

**API:** `DELETE /patients/{patient_id}` returns 204.

- An unknown id returns 404 `patient_not_found`.
- The route is a plain `def`, so the folder work runs in the thread pool.
- After the change, regenerate `openapi.json` and `schema.d.ts` with `npm run gen:api`.

**Frontend:**

- Add `deletePatient(patientId)` to `api/endpoints.ts`.
- Each `PatientSection` on the Library screen gets a **Delete** button after **Edit** and
  **Export all**. It opens a `ConfirmDialog`, tracked as a new `Open` kind `"delete"` keyed by
  patient id.
- The dialog body names the patient and the number of cases. It says the folder moves to the
  `.trash` folder in the library, that the photos stay on disk, and that the patient disappears
  from the app. A patient with no cases gets wording that does not mention cases.
- A ref guards against a double click sending two requests, as in `CaseHeader`.
- On success, the dialog closes, the `patients` query is invalidated, and the cached view of
  each of the patient's cases is removed. Invalidating them is not enough: the case screen
  shows cached data over a failed refetch, so browser Back would bring a deleted case back on
  screen. Errors go through the existing global `MutationCache.onError`.

**Spec:**

- In §4.11, change the `.trash/` comment to "deleted cases and patients (restorable)".
- In §4.12, add `DELETE /patients/{id}` to the API table.

## Tests

- **Unit (`test_library.py`):**
  - The folder moves to `.trash/` whole, and the patient and its cases are no longer found.
  - A name clash with an existing `.trash/<name>/` gives `<name> (2)`.
  - `trash_patient` waits for a held case lock.
- **Integration (`test_api_library.py`):**
  - The delete returns 204, and the listing is then empty.
  - A second delete returns 404.
  - A delete returns 409 while a case job or the patient export runs.
  - `patient_trashed` is logged.
- **Frontend (`LibraryScreen.test.tsx`):**
  - Cancel sends no `DELETE`. Confirm sends exactly one.
  - The body names the patient and the number of cases.

## Left to the builder

The exact English wording, test names, and helper factoring.
