import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type ChangeEvent } from "react";
import { patchCase, setReference } from "../../api/endpoints";
import { photoImageUrl } from "../../api/images";
import { queryKeys, useCaseChange } from "../../api/queries";
import type { CaseView } from "../../api/types";
import { Badge } from "../../components/Badge";
import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { partitionFiles } from "../../lib/dropFiles";
import { skipSummary } from "../../lib/skipped";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { DropZone } from "../intake/DropZone";
import { useNotify } from "../shell/notify";

const SCALES = [1, 1.5, 2] as const;

// The suggestion is the server's pick and may fall between the three choices.
const scaleLabel = (scale: number): string =>
  scale === 1
    ? en.procedure.output.scales["1"]
    : scale === 1.5
      ? en.procedure.output.scales["1.5"]
      : scale === 2
        ? en.procedure.output.scales["2"]
        : `${String(scale)}×`;

function OutputResolution({ view }: { view: CaseView }) {
  const caseId = view.case.id;
  const current = view.case.output_scale ?? 1;
  const suggested = view.suggested_output_scale;
  const change = useCaseChange(caseId, (scale: number) =>
    patchCase(caseId, { output_scale: scale }),
  );

  return (
    <fieldset className="flex flex-col gap-2" disabled={change.isPending}>
      <legend className="mb-1 text-sm font-medium text-slate-900">
        {en.procedure.output.legend}
      </legend>
      <div className="flex gap-4">
        {SCALES.map((scale) => (
          <label key={scale} className="flex items-center gap-1 text-sm text-slate-900">
            <input
              type="radio"
              name={`output-scale-${caseId}`}
              checked={current === scale}
              onChange={() => {
                change.mutate(scale);
              }}
            />
            {scaleLabel(scale)}
          </label>
        ))}
      </div>
      {suggested !== null && suggested > current && (
        <div className="flex flex-wrap items-center gap-2 text-sm text-slate-800">
          <p>{fill(en.procedure.output.suggestion, { suggested: scaleLabel(suggested) })}</p>
          <Button
            onClick={() => {
              change.mutate(suggested);
            }}
          >
            {fill(en.procedure.output.apply, { suggested: scaleLabel(suggested) })}
          </Button>
        </div>
      )}
    </fieldset>
  );
}

export function ReferencePanel({ view }: { view: CaseView }) {
  const caseId = view.case.id;
  const notify = useNotify();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  // A ref, not `isPending`: the picker can deliver twice before React renders the disabled state.
  const uploading = useRef(false);
  const [confirming, setConfirming] = useState(false);
  const reference = view.case.photos.find((photo) => photo.role === "reference");

  const upload = useMutation({
    mutationFn: (file: File) => setReference(caseId, file),
    onSuccess: () =>
      // The answer is the upload result, not the case: the reference is pending until refetched,
      // and `useCase` polls from then on.
      Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.case(caseId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.patients }),
      ]),
    onSettled: () => {
      uploading.current = false;
    },
  });

  const send = (files: File[]) => {
    const { upload: accepted, skipped } = partitionFiles(files);
    const lines = skipSummary(skipped);
    if (lines.length > 0) notify({ kind: "info", lines });
    const file = accepted[0];
    if (file === undefined || uploading.current) return;
    uploading.current = true;
    upload.mutate(file);
  };

  const onChoose = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    // So choosing the same file again still fires `change`.
    event.target.value = "";
    send(files);
  };

  const showDrop = reference === undefined || reference.state === "failed";

  return (
    <section
      aria-label={en.procedure.reference.title}
      className="flex flex-col gap-4 rounded-lg border border-slate-300 bg-white p-4"
    >
      <h2 className="text-lg font-semibold text-slate-900">{en.procedure.reference.title}</h2>

      {reference?.state === "failed" && (
        <p role="alert" className="text-sm text-red-900">
          {fill(en.procedure.reference.failed, { message: reference.error ?? "" })}
        </p>
      )}
      {showDrop && (
        <DropZone
          label={en.procedure.reference.drop}
          multiple={false}
          busy={upload.isPending ? en.procedure.reference.uploading : null}
          onFiles={send}
        />
      )}
      {reference?.state === "pending" && (
        <p role="status" className="text-sm text-slate-700">
          {en.procedure.reference.processing}
        </p>
      )}
      {reference?.state === "ready" && (
        <div className="flex flex-wrap items-center gap-4">
          <img
            src={photoImageUrl(caseId, reference, "processed", "thumb")}
            alt={en.procedure.reference.alt}
            className="h-24 w-32 rounded-md bg-slate-200 object-cover"
          />
          <Badge tone="ok">{en.procedure.reference.locked}</Badge>
          <Button
            disabled={upload.isPending}
            onClick={() => {
              setConfirming(true);
            }}
          >
            {en.procedure.reference.replace}
          </Button>
          <input
            ref={input}
            type="file"
            hidden
            tabIndex={-1}
            aria-hidden="true"
            disabled={upload.isPending}
            onChange={onChoose}
          />
        </div>
      )}
      {confirming && (
        <ConfirmDialog
          title={en.procedure.reference.replaceTitle}
          body={en.procedure.reference.replaceBody}
          confirmLabel={en.procedure.reference.replaceConfirm}
          onCancel={() => {
            setConfirming(false);
          }}
          onConfirm={() => {
            setConfirming(false);
            // In the same click: the picker only opens for a user gesture.
            input.current?.click();
          }}
        />
      )}

      <OutputResolution view={view} />
    </section>
  );
}
