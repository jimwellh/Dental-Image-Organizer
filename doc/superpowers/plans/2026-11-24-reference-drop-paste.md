# Reference image: drop or paste from Keynote, and always first in the sequence

## Context

Dentists want to crop a pre-op photo in Keynote (or Preview, Photos, Pages), then drag it, or copy and paste it, straight into a procedure case as the reference image.

Two things stop this today:

1. **Bug: the reference's position depends on its timestamp.**
   - An image copied or exported from Keynote has no EXIF time. `intake.pending_photo` (src/dental_image_organizer/services/intake.py:210) then falls back to `last_modified`, or to the upload time. That is the paste time or export time, which is later than every clinical photo.
   - The default sequence order sorts by `capture_order`, so the reference lands last instead of first. This happens in `procedure_case.sequence_photos` (procedure_case.py:632), the frontend `usablePhotos` (SequenceBuilder.tsx:43), and the headless CLI (`headless.py:120`).
   - When a saved custom selection exists, `set_reference` drops the old reference's id and never adds the new one (procedure_case.py:144-151). The new reference silently leaves the sequence.
2. **Missing feature.**
   - `ReferencePanel` accepts a drop only while there is no reference, or the reference failed.
   - There is no paste handling anywhere.
   - TIFF bytes are refused as "DNG/ProRAW" (`imageio.sniff_format`).

**Outcome:**
- The reference no longer needs a capture time. It is always the first frame of the default sequence.
- A replaced reference keeps its slot in a saved selection.
- On the procedure screen, ⌘V or a drop onto the Reference panel sets the reference. When a reference already exists, the app asks for confirmation first.

**Decisions already made by the user:**
- Paste works anywhere on the procedure page. It is ignored inside inputs and while any dialog is open.
- A new reference takes the old reference's position in a saved selection.

## Branch

`git switch -c feat/reference-drop-paste` from `main`. The untracked `.claude/` folder stays uncommitted.

- Follow TDD: write each failing test first.
- Commit in the repo's style, for example `fix(api): …`, `feat(ui): …`, `docs: …`.
- Also save this plan as `doc/superpowers/plans/2026-11-24-reference-drop-paste.md`, following the repo convention.

## Part 1: Backend bug fix (reference needs no time)

**`src/dental_image_organizer/services/procedure_case.py`**
- Add a public key function `sequence_order(photo) -> tuple[bool, datetime, str]` that returns `(photo.role != "reference", *capture_order(photo))`.
- `sequence_photos`: change the default branch to `sorted(usable.values(), key=sequence_order)`.
- In `set_reference.change`, replace the `sequence` block:
  - Map `previous.id → reference.id` inside `sequence.photo_ids`, then de-duplicate and keep the first occurrence.
    - A de-duplicate is needed when an existing photo, already selected, becomes the reference.
  - Drop any other ids in `gone`.
  - Keep the existing `< SEQUENCE_SIZE[0] → []` fallback.
  - If the old reference was not in the selection, nothing is added; the user excluded it on purpose.

**`src/dental_image_organizer/services/cases.py:241`** — `thumbnails` reuses `procedure_case.sequence_order` instead of its inline lambda (same ordering).

**`src/dental_image_organizer/services/headless.py:105-120`** — sort only the photo frames, then prepend the reference frame: `frames = [reference_frame, *sorted(photo_frames, key=…)]`. `reference_time` is still used for the reference's output file name.

**Tests (`tests/unit/test_procedure_case.py`)**
- New: a reference without EXIF, uploaded with `last_modified` later than the photos (the `upload(name, data, last_modified)` helper from `tests/support/app.py`), comes first in `sequence_photos`.
- Update `test_replacing_the_reference_reprocesses_and_drops_manual_edits` (line 189): expect `SequenceRecord(photo_ids=[reference.id, intra, post])`.
- Rework `test_a_selection_left_with_one_photo_falls_back_to_capture_order` (line 193). The fallback now triggers only through the de-duplicate: the selection is `[photo, old]`, and `photo` itself is set as the reference. The result is `[]`, and the default order is `[reference, …]`.
- New: a selection that excludes the old reference stays as it was after a replace.
- Headless: add a case to `tests/integration/test_cli_process.py`, or the matching unit test, where the reference has no EXIF time and a late mtime but is still the first frame. Assert on the strip order through the report, or on the frame widths/colors the existing tests use.

## Part 2: Frontend bug fix

- **`frontend/src/features/procedure/order.ts`**: export the existing `referenceFirst` comparator as `sequenceOrder` (the same name role as the backend's).
- **`SequenceBuilder.tsx:43`**: `usablePhotos` sorts with `sequenceOrder`, and the comment is updated to say the reference leads.
- **`PhotoTile.tsx:54`**: hide `captureTimeWarning` for `role === "reference"`, because its time no longer matters.
- **Tests:**
  - `order.test.ts`: the reference with a later or null time sorts first.
  - `SequenceBuilder.test.tsx`: a reference with a late `captured_at` is row 1 of the default order.
  - `ProcedureScreen.test.tsx`: the reference tile with `file_mtime` shows no warning.

## Part 3: Feature — drop and paste into the reference

**`frontend/src/features/intake/useFileDrop.ts` (new; extracted from `DropZone.tsx`)**
- `useFileDrop({ inactive, onFiles })` returns `{ dragging, handlers }`.
- `handlers` holds `onDragEnter`, `onDragOver`, `onDragLeave`, and `onDrop`.
- It moves the current drag/leave/drop logic over as is: `filesFromDrop`, `stopPropagation`, `dropEffect = "none"` when inactive, and the unreadable-entries notice.
- `DropZone` then uses the hook. Its behavior and tests stay unchanged.

**`frontend/src/lib/clipboardImages.ts` (new)**
- `imagesFromClipboard(data: Pick<DataTransfer, "items" | "files"> | null): File[]` takes the `kind === "file"` items through `getAsFile()`, falling back to `files`.
- It keeps files whose type is empty or `image/*`.
- On macOS, Safari (WebKit converts TIFF→PNG) and Chrome both hand a Keynote copy over as an `image/png` named `image.png`.

**`frontend/src/lib/imageFormat.ts` (new; a defensive path for "similar programs")**
- `toUploadable(file): Promise<File>` handles `image/tiff`, `image/webp`, `image/bmp`, and `image/avif`.
- These are re-encoded to PNG through `createImageBitmap` → canvas → `toBlob("image/png")`, keeping the name stem and `lastModified`. The bitmap is closed in `finally`.
- Every other type passes through untouched, so DNG still reaches the server and gets its "raw" skip message (an existing test depends on this).
- If decoding fails, it throws a typed error, and the panel notifies `en.procedure.reference.unreadableFormat`.

**`frontend/src/components/Dialog.tsx`** — export `anyDialogOpen(): boolean` (`openDialogs.length > 0`). The paste handler uses it, so it does not need to query the DOM.

**`frontend/src/features/procedure/ReferencePanel.tsx`**
- A single entry point, `receive(files)`:
  - `partitionFiles` runs, plus the existing `onlyFirstUsed` notice.
  - It takes the first file and runs `await toUploadable(file)`. The `uploading` ref is set before the await, to guard against double delivery.
  - If a reference exists that is not `failed` (ready or pending), it stores `pendingFile` in state and shows a `ConfirmDialog` (`replaceTitle`/`replaceBody`, confirm label `replaceDropConfirm` = "Replace"). Otherwise it uploads straight away.
- The whole `<section>` is a drop target through `useFileDrop`, with a ring highlight while dragging. When the inner `DropZone` is shown (no reference, or a failed one), it stops propagation itself and calls `receive` too.
- Paste: a `useEffect` registers a `document` `paste` listener, which always calls the latest `receive` through a ref. The listener returns early when:
  - the target is inside `input, textarea, select, [contenteditable]`, or
  - `anyDialogOpen()` is true, or
  - there are no clipboard images.

  Otherwise it calls `preventDefault()`, then `receive`.
- The existing "Replace reference…" button and file-picker flow stay as they are.
- Hint text:
  - The empty state's `DropZone` gets `help={en.procedure.reference.dropHelp}`.
  - The ready state shows `en.procedure.reference.replaceHint`.

**`frontend/src/strings/en.ts`** (`procedure.reference`): add `dropHelp`, `replaceHint`, `replaceDropConfirm`, and `unreadableFormat`. Example text: "Drop a file, choose one, or copy a cropped image (for example in Keynote) and press ⌘V."

**Tests**
- `useFileDrop`/`DropZone.test.tsx` stay green.
- `clipboardImages.test.ts` and `imageFormat.test.ts`: stub `createImageBitmap` and `HTMLCanvasElement.prototype.toBlob`, because jsdom has neither.
- `ProcedureScreen.test.tsx`:
  - With no reference, a paste (`fireEvent.paste(document, { clipboardData })`) uploads once to `PUT /reference`.
  - With a ready reference, a paste opens the confirm dialog: Cancel uploads nothing, and Replace uploads once.
  - A drop on the ready panel confirms first.
  - A paste inside an `<input>`, or while CompareView is open, is ignored.
  - A text-only paste is ignored.
- Extend `frontend/src/test/fakeDrop.ts` with a `fakeClipboard(files)` helper.

## Part 4: Docs

- `doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md`:
  - §3.2 step 2: the reference can be dropped, chosen, or pasted, and it needs no capture time.
  - §3.2 step 6: the default is "the reference first, then the other photos in capture order", and a replaced reference keeps its slot in a saved selection.
- README "Procedure sequences" bullet: "…or crop it in Keynote and drop or paste it."

## Critical files

- `src/dental_image_organizer/services/procedure_case.py`, `cases.py`, `headless.py`
- `frontend/src/features/procedure/{ReferencePanel,SequenceBuilder,PhotoTile}.tsx`, `order.ts`
- `frontend/src/features/intake/DropZone.tsx` and the new `useFileDrop.ts`
- `frontend/src/lib/clipboardImages.ts`, `imageFormat.ts` (new); `components/Dialog.tsx`; `strings/en.ts`
- The tests listed above

## Verification

1. Before starting the backend, check Smart App Control (per memory; it was off as of 2026-11-24).
2. Backend: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run lint-imports`, `uv run pytest`.
3. Frontend (`frontend/`):
   - `npm run gen:api`, then `git diff --exit-code src/api/` (no API change is expected).
   - `npm run lint`, `npm run typecheck`, `npm run format:check`, `npm test`, `npm run build`.
4. Manual check on Windows with the `run` skill or `dio serve`:
   - Create a procedure case and upload EXIF photos.
   - Paste a screenshot with Win+Shift+S then Ctrl+V. It becomes the reference and is first in the Sequence builder and in the preview strip.
   - Paste again, and confirm the dialog appears.
   - Drop a PNG onto the ready panel.
5. Manual check on a Mac (Windows cannot cover it; CI's macos-smoke does not test the UI):
   - In Keynote: crop, then ⌘C, then ⌘V in Safari and Chrome.
   - Drag the image from Keynote onto the Reference panel.
   - Check the pasted reference's resolution. Keynote may hand over the image at slide resolution, which would lower the output size. In that case, use the output-scale suggestion, or export from Keynote at full size.
6. Open the PR with `gh pr create` after the user approves. The body ends with the Claude Code attribution line.
