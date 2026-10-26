import {
  closestCenter,
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useId, useState } from "react";
import { patchCase } from "../../api/endpoints";
import { photoImageUrl, sequenceUrl } from "../../api/images";
import { useCaseChange } from "../../api/queries";
import type { CaseView, PhotoRecord } from "../../api/types";
import { Button } from "../../components/Button";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { ExportDialog } from "../export/ExportDialog";
import { captureOrder } from "./order";

export const SEQUENCE_MIN = 2;
export const SEQUENCE_MAX = 10;

// The handle is pointer-only: the Move buttons are the keyboard path, so dnd-kit's spoken
// instructions and announcements would describe a way to drag that does not exist.
const SILENT: Announcements = {
  onDragStart: () => undefined,
  onDragOver: () => undefined,
  onDragEnd: () => undefined,
  onDragCancel: () => undefined,
};
const NO_INSTRUCTIONS = { draggable: "" };

// As `procedure_case.sequence_photos`: the reference is a frame like any other, and a photo with
// no edit yet has nothing to draw.
const usablePhotos = (view: CaseView): PhotoRecord[] =>
  view.case.photos.filter((photo) => photo.state === "ready" && photo.edit).sort(captureOrder);

function Thumb({ view, photo }: { view: CaseView; photo: PhotoRecord }) {
  return (
    <img
      src={photoImageUrl(view.case.id, photo, "processed", "thumb")}
      alt=""
      loading="lazy"
      className="h-12 w-16 shrink-0 rounded bg-slate-200 object-cover"
    />
  );
}

function Row(props: {
  view: CaseView;
  photo: PhotoRecord;
  index: number;
  count: number;
  onMove: (from: number, to: number) => void;
}) {
  const { view, photo, index, count, onMove } = props;
  const nameId = useId();
  // Only the handle gets the drag listeners. The `attributes` dnd-kit offers would make the row a
  // role="button" and hide the buttons inside it from assistive technology.
  const { listeners, setNodeRef, setActivatorNodeRef, transform, transition } = useSortable({
    id: photo.id,
  });

  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className="flex items-center gap-3 rounded-lg border border-slate-300 bg-white p-2"
    >
      <span
        ref={setActivatorNodeRef}
        {...listeners}
        aria-hidden="true"
        title={en.sequence.handle}
        className="cursor-grab touch-none select-none px-1 text-slate-600"
      >
        ⠿
      </span>
      <span className="w-6 text-sm tabular-nums text-slate-700">{index + 1}</span>
      <Thumb view={view} photo={photo} />
      <span id={nameId} data-name className="min-w-0 flex-1 truncate text-sm text-slate-900">
        {photo.source_name}
      </span>
      <Button
        aria-describedby={nameId}
        disabled={index === 0}
        onClick={() => {
          onMove(index, index - 1);
        }}
      >
        {en.sequence.moveUp}
      </Button>
      <Button
        aria-describedby={nameId}
        disabled={index === count - 1}
        onClick={() => {
          onMove(index, index + 1);
        }}
      >
        {en.sequence.moveDown}
      </Button>
    </li>
  );
}

export function SequenceBuilder({ view }: { view: CaseView }) {
  const caseId = view.case.id;
  const usable = usablePhotos(view);
  const usableById = new Map(usable.map((photo) => [photo.id, photo]));
  const saved = view.case.sequence?.photo_ids ?? [];

  // What the case holds now: the saved photos that can still be drawn, or every usable photo when
  // fewer than two remain (an empty list means all, in capture order; section 3.2).
  const keptSaved = saved.filter((photoId) => usableById.has(photoId));
  const fromServer = keptSaved.length >= SEQUENCE_MIN ? keptSaved : usable.map((photo) => photo.id);

  // `null` follows the server, so photos that finish processing join the default selection
  // instead of being missed by a copy taken at mount. The first edit takes over.
  const [picked, setPicked] = useState<string[] | null>(null);
  const [exporting, setExporting] = useState(false);
  const selection = picked ? picked.filter((photoId) => usableById.has(photoId)) : fromServer;

  const change = useCaseChange(caseId, (photoIds: string[]) =>
    patchCase(caseId, { sequence: { photo_ids: photoIds } }),
  );
  const send = (photoIds: string[]) => {
    change.mutate(photoIds, {
      // The saved state is the new baseline, so the list goes back to following it.
      onSuccess: () => {
        setPicked(null);
      },
    });
  };
  const sensors = useSensors(
    // A small distance keeps a click on the handle from starting a drag.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  const baseline = saved.length > 0 ? saved : usable.map((photo) => photo.id);
  const changed =
    selection.length !== baseline.length || selection.some((photoId, n) => photoId !== baseline[n]);
  const inRange = selection.length >= SEQUENCE_MIN && selection.length <= SEQUENCE_MAX;
  const reasonId = useId();

  const move = (from: number, to: number) => {
    setPicked(arrayMove(selection, from, to));
  };
  const toggle = (photoId: string) => {
    setPicked(
      selection.includes(photoId)
        ? selection.filter((other) => other !== photoId)
        : [...selection, photoId],
    );
  };
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    const from = selection.indexOf(String(active.id));
    const to = selection.indexOf(String(over.id));
    if (from >= 0 && to >= 0) move(from, to);
  };

  return (
    <section aria-label={en.sequence.title} className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold text-slate-900">{en.sequence.title}</h2>
      {usable.length === 0 ? (
        <p className="text-sm text-slate-700">{en.sequence.empty}</p>
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            <fieldset className="flex flex-col gap-1">
              <legend className="mb-1 text-sm font-medium text-slate-900">
                {en.sequence.available}
              </legend>
              {usable.map((photo) => (
                <label key={photo.id} className="flex items-center gap-2 text-sm text-slate-900">
                  <input
                    type="checkbox"
                    checked={selection.includes(photo.id)}
                    aria-label={fill(en.sequence.choose, { name: photo.source_name })}
                    onChange={() => {
                      toggle(photo.id);
                    }}
                  />
                  <Thumb view={view} photo={photo} />
                  <span className="min-w-0 truncate">{photo.source_name}</span>
                </label>
              ))}
            </fieldset>
            <div className="flex flex-col gap-2">
              <p className="text-sm font-medium text-slate-900">{en.sequence.order}</p>
              <p className="text-sm text-slate-700">{en.sequence.orderHelp}</p>
              <DndContext
                sensors={sensors}
                collisionDetection={closestCenter}
                onDragEnd={onDragEnd}
                accessibility={{ announcements: SILENT, screenReaderInstructions: NO_INSTRUCTIONS }}
              >
                <SortableContext items={selection} strategy={verticalListSortingStrategy}>
                  <ol aria-label={en.sequence.order} className="flex flex-col gap-2">
                    {selection.map((photoId, index) => {
                      const photo = usableById.get(photoId);
                      return photo ? (
                        <Row
                          key={photoId}
                          view={view}
                          photo={photo}
                          index={index}
                          count={selection.length}
                          onMove={move}
                        />
                      ) : null;
                    })}
                  </ol>
                </SortableContext>
              </DndContext>
              {selection.length === 0 && (
                <p className="text-sm text-slate-700">{en.sequence.emptyOrder}</p>
              )}
            </div>
          </div>
          {!inRange && (
            <p id={reasonId} role="status" className="text-sm text-red-700">
              {fill(en.sequence.outOfRange, {
                min: String(SEQUENCE_MIN),
                max: String(SEQUENCE_MAX),
                count: String(selection.length),
              })}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              aria-describedby={inRange ? undefined : reasonId}
              disabled={!inRange || !changed || change.isPending}
              onClick={() => {
                send(selection);
              }}
            >
              {change.isPending ? en.sequence.saving : en.sequence.save}
            </Button>
            <Button
              disabled={change.isPending}
              onClick={() => {
                send([]);
              }}
            >
              {en.sequence.useAll}
            </Button>
            <Button
              onClick={() => {
                setExporting(true);
              }}
            >
              {en.sequence.export}
            </Button>
          </div>
          <img
            src={sequenceUrl(view, "preview")}
            alt={en.sequence.preview}
            className="max-w-full rounded-md bg-slate-200"
          />
        </>
      )}
      {exporting && (
        <ExportDialog
          target={{ kind: "case", caseId, caseType: view.case.type, folder: view.folder }}
          onClose={() => {
            setExporting(false);
          }}
        />
      )}
    </section>
  );
}
