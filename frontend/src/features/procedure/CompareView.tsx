import { useId, useState } from "react";
import { patchPhoto } from "../../api/endpoints";
import { photoImageUrl } from "../../api/images";
import { useCaseChange } from "../../api/queries";
import type { CaseView, EditRecord, PhotoPatch, PhotoRecord } from "../../api/types";
import { Button } from "../../components/Button";
import { Dialog } from "../../components/Dialog";
import { TransformSurface } from "../../components/TransformSurface";
import {
  closestSimilarity,
  fitSimilarity,
  fromArray,
  homographyFromPoints,
  type Mat3,
} from "../../lib/transform";
import { en } from "../../strings/en";
import { LandmarkPicker } from "./LandmarkPicker";

type Mode = "onion" | "split" | "align";
type Method = "free" | "two" | "four";

const MODES: readonly Mode[] = ["onion", "split", "align"];
const METHODS: readonly Method[] = ["free", "two", "four"];

function Choice<T extends string>(props: {
  legend: string;
  name: string;
  options: readonly T[];
  labels: Readonly<Record<T, string>>;
  value: T;
  onChange: (value: T) => void;
}) {
  const { legend, name, options, labels, value, onChange } = props;
  return (
    <fieldset className="flex flex-wrap items-center gap-4">
      <legend className="mb-1 text-sm font-medium text-slate-900">{legend}</legend>
      {options.map((option) => (
        <label key={option} className="flex items-center gap-1 text-sm text-slate-900">
          <input
            type="radio"
            name={name}
            checked={value === option}
            onChange={() => {
              onChange(option);
            }}
          />
          {labels[option]}
        </label>
      ))}
    </fieldset>
  );
}

function Slider(props: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm text-slate-900">
      {props.label}
      <input
        type="range"
        min={0}
        max={100}
        value={props.value}
        onChange={(event) => {
          props.onChange(Number(event.target.value));
        }}
      />
    </label>
  );
}

const layer = "absolute inset-0 h-full w-full select-none";

function CompareBody(props: {
  view: CaseView;
  photo: PhotoRecord;
  edit: EditRecord;
  reference: PhotoRecord;
}) {
  const { view, photo, edit, reference } = props;
  const caseId = view.case.id;
  const groupId = useId();
  const [mode, setMode] = useState<Mode>("onion");
  const [method, setMethod] = useState<Method>("free");
  const [opacity, setOpacity] = useState(50);
  const [split, setSplit] = useState(50);

  // The live matrix follows the gesture; a new edit from the server (a commit, a reset, a
  // re-alignment) replaces it. Adjusted during render, as React recommends for state that derives
  // from a prop, so no frame shows the old matrix.
  const [matrix, setMatrix] = useState<Mat3>(() => fromArray(edit.transform));
  const [syncedEdit, setSyncedEdit] = useState(edit);
  if (edit !== syncedEdit) {
    setSyncedEdit(edit);
    setMatrix(fromArray(edit.transform));
  }

  const change = useCaseChange(caseId, (patch: PhotoPatch) => patchPhoto(caseId, photo.id, patch));
  const send = (patch: PhotoPatch) => {
    change.mutate(patch, {
      // The server kept the old alignment; the picture must not keep showing the refused one.
      onError: () => {
        setMatrix(fromArray(edit.transform));
      },
    });
  };

  const outAspect = edit.output_size[1] / edit.output_size[0];
  // A ready photo always has its size; the output's shape is the harmless fallback.
  const photoAspect = photo.width && photo.height ? photo.height / photo.width : outAspect;
  const referenceUrl = photoImageUrl(caseId, reference, "processed", "preview");
  const processedUrl = photoImageUrl(caseId, photo, "processed", "preview");
  const originalUrl = photoImageUrl(caseId, photo, "original", "preview");

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
        <Choice
          legend={en.compare.view}
          name={`${groupId}-mode`}
          options={MODES}
          labels={en.compare.modes}
          value={mode}
          onChange={setMode}
        />
        {mode === "split" ? (
          <Slider label={en.compare.splitPosition} value={split} onChange={setSplit} />
        ) : (
          <Slider label={en.compare.opacity} value={opacity} onChange={setOpacity} />
        )}
      </div>

      {mode !== "align" && (
        <div
          className="relative overflow-hidden bg-slate-200"
          style={{ aspectRatio: `1 / ${String(outAspect)}` }}
        >
          <img src={referenceUrl} alt={en.compare.referenceAlt} className={layer} />
          <img
            src={processedUrl}
            alt={photo.source_name}
            className={layer}
            style={
              mode === "onion"
                ? { opacity: opacity / 100 }
                : { clipPath: `inset(0 0 0 ${String(split)}%)` }
            }
          />
          {mode === "split" && (
            <div
              aria-hidden="true"
              className="absolute inset-y-0 w-0.5 bg-white shadow"
              style={{ left: `${String(split)}%` }}
            />
          )}
        </div>
      )}

      {mode === "align" && (
        <div className="flex flex-col gap-3">
          <Choice
            legend={en.compare.method}
            name={`${groupId}-method`}
            options={METHODS}
            labels={en.compare.methods}
            value={method}
            onChange={setMethod}
          />
          {method === "free" ? (
            <>
              <TransformSurface
                imageUrl={originalUrl}
                outAspect={outAspect}
                matrix={matrix}
                opacity={opacity / 100}
                label={en.compare.surfaceLabel}
                onChange={setMatrix}
                onCommit={(next) => {
                  send({ edit: { transform: [...next], model: edit.model } });
                }}
              >
                <img src={referenceUrl} alt="" draggable={false} className={layer} />
              </TransformSurface>
              <p className="text-sm text-slate-700">{en.compare.surface}</p>
            </>
          ) : (
            <LandmarkPicker
              // Another count starts with no points.
              key={method}
              count={method === "two" ? 2 : 4}
              referenceUrl={referenceUrl}
              photoUrl={originalUrl}
              referenceAspect={outAspect}
              photoAspect={photoAspect}
              onApply={(referencePoints, photoPoints) => {
                // Both fits throw a RangeError for degenerate points; the picker shows why.
                if (method === "two") {
                  const transform = fitSimilarity(photoPoints, referencePoints);
                  send({ edit: { transform: [...transform], model: "similarity" } });
                } else {
                  const transform = homographyFromPoints(photoPoints, referencePoints);
                  send({ edit: { transform: [...transform], model: "homography" } });
                }
              }}
            />
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          disabled={edit.model !== "homography" || change.isPending}
          onClick={() => {
            send({
              edit: {
                transform: [...closestSimilarity(matrix, 1, outAspect)],
                model: "similarity",
              },
            });
          }}
        >
          {en.compare.resetPerspective}
        </Button>
        <Button
          disabled={change.isPending}
          onClick={() => {
            send({ reset: true });
          }}
        >
          {en.compare.resetToAuto}
        </Button>
      </div>
    </div>
  );
}

export function CompareView(props: { view: CaseView; photoId: string; onClose: () => void }) {
  const { view, photoId, onClose } = props;
  const photo = view.case.photos.find((candidate) => candidate.id === photoId);
  const reference = view.case.photos.find((candidate) => candidate.role === "reference");
  const edit = photo?.edit;
  const comparable =
    photo?.state === "ready" && edit && reference?.state === "ready"
      ? { photo, edit, reference }
      : null;

  return (
    <Dialog title={en.compare.title} onClose={onClose} wide>
      <div className="flex flex-col gap-4">
        {comparable ? (
          <CompareBody view={view} {...comparable} />
        ) : (
          <p className="text-sm text-slate-800">{en.compare.unavailable}</p>
        )}
        <div>
          <Button onClick={onClose}>{en.compare.close}</Button>
        </div>
      </div>
    </Dialog>
  );
}
