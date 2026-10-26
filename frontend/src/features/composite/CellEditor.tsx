import { useId, useState } from "react";
import { patchPhoto } from "../../api/endpoints";
import { photoImageUrl } from "../../api/images";
import { useCaseChange } from "../../api/queries";
import type { CaseView, EditRecord, PhotoPatch, PhotoRecord } from "../../api/types";
import { Button } from "../../components/Button";
import { Dialog } from "../../components/Dialog";
import { TransformSurface } from "../../components/TransformSurface";
import { FLIPS } from "../../lib/slots";
import { fromArray, type Mat3 } from "../../lib/transform";
import { en } from "../../strings/en";

function CellBody(props: {
  view: CaseView;
  photo: PhotoRecord;
  edit: EditRecord;
  cellSize: readonly [number, number];
  gapColor: string;
}) {
  const { view, photo, edit, cellSize, gapColor } = props;
  const caseId = view.case.id;
  const groupId = useId();

  // The live matrix follows the gesture; a new edit from the server (a commit, a flip, a reset)
  // replaces it. Adjusted during render so no frame shows the old matrix.
  const [matrix, setMatrix] = useState<Mat3>(() => fromArray(edit.transform));
  const [syncedEdit, setSyncedEdit] = useState(edit);
  if (edit !== syncedEdit) {
    setSyncedEdit(edit);
    setMatrix(fromArray(edit.transform));
  }

  const change = useCaseChange(caseId, (patch: PhotoPatch) => patchPhoto(caseId, photo.id, patch));
  const send = (patch: PhotoPatch) => {
    change.mutate(patch, {
      // The server kept the old geometry; the picture must not keep showing the refused one.
      onError: () => {
        setMatrix(fromArray(edit.transform));
      },
    });
  };

  const flip = edit.flip ?? (photo.slot ? view.flip_defaults?.[photo.slot] : undefined);

  return (
    <div className="flex flex-col gap-4">
      <TransformSurface
        imageUrl={photoImageUrl(caseId, photo, "original", "preview")}
        outAspect={cellSize[1] / cellSize[0]}
        matrix={matrix}
        label={en.cell.surfaceLabel}
        onChange={setMatrix}
        onCommit={(next) => {
          // A composite cell takes a similarity only (spec §4.7.2).
          send({ edit: { transform: [...next], model: "similarity" } });
        }}
      >
        {/* What shows where the photo does not reach: the grid's gap color. */}
        <div data-gap="" className="absolute inset-0" style={{ backgroundColor: gapColor }} />
      </TransformSurface>
      <p className="text-sm text-slate-700">{en.cell.surface}</p>

      <fieldset className="flex flex-wrap items-center gap-4">
        <legend className="mb-1 text-sm font-medium text-slate-900">{en.cell.flip}</legend>
        {FLIPS.map((option) => (
          <label key={option} className="flex items-center gap-1 text-sm text-slate-900">
            <input
              type="radio"
              name={`${groupId}-flip`}
              checked={flip === option}
              disabled={change.isPending}
              onChange={() => {
                send({ flip: option });
              }}
            />
            {en.flips[option]}
          </label>
        ))}
      </fieldset>
      <p className="text-sm text-slate-700">{en.cell.flipNote}</p>

      <div>
        <Button
          disabled={change.isPending}
          onClick={() => {
            send({ reset: true });
          }}
        >
          {en.cell.reset}
        </Button>
      </div>
    </div>
  );
}

export function CellEditor(props: { view: CaseView; photoId: string; onClose: () => void }) {
  const { view, photoId, onClose } = props;
  const photo = view.case.photos.find((candidate) => candidate.id === photoId);
  const edit = photo?.edit;
  const composite = view.case.composite;
  const editable =
    photo?.state === "ready" && edit && composite
      ? { photo, edit, cellSize: composite.cell_size, gapColor: composite.gap_color }
      : null;

  return (
    <Dialog title={photo?.slot ? en.slots[photo.slot] : en.cell.title} onClose={onClose} wide>
      <div className="flex flex-col gap-4">
        {editable ? (
          <CellBody view={view} {...editable} />
        ) : (
          <p className="text-sm text-slate-800">{en.cell.unavailable}</p>
        )}
        <div>
          <Button onClick={onClose}>{en.cell.close}</Button>
        </div>
      </div>
    </Dialog>
  );
}
