import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { patchCase, setReference } from "../../api/endpoints";
import { photoImageUrl } from "../../api/images";
import { queryKeys, useCaseChange } from "../../api/queries";
import type { CaseView } from "../../api/types";
import { Badge } from "../../components/Badge";
import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { anyDialogOpen } from "../../components/dialogStack";
import { imagesFromClipboard, pastedIntoText } from "../../lib/clipboardImages";
import { partitionFiles } from "../../lib/dropFiles";
import { toUploadable } from "../../lib/imageFormat";
import { skipSummary } from "../../lib/skipped";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { DropZone } from "../intake/DropZone";
import { useFileDrop } from "../intake/useFileDrop";
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
  // A dropped or pasted image waiting for the dentist to confirm that it replaces the reference.
  const [replacement, setReplacement] = useState<File | null>(null);
  // The same file in a ref: a double click on the confirm button must send one request.
  const pendingReplacement = useRef<File | null>(null);
  const reference = view.case.photos.find((photo) => photo.role === "reference");
  // A failed reference is retried rather than replaced, so it needs no confirmation.
  const hasReference = reference !== undefined && reference.state !== "failed";

  const upload = useMutation({
    mutationFn: (file: File) => setReference(caseId, file),
    onSuccess: (result) => {
      // A non-photo answers 200 with nothing added; without this line the drop would seem to vanish.
      const lines = skipSummary(result.skipped);
      if (lines.length > 0) notify({ kind: "error", lines });
      // The answer is the upload result, not the case: the reference is pending until refetched,
      // and `useCase` polls from then on.
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.case(caseId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.patients }),
      ]);
    },
    onSettled: () => {
      uploading.current = false;
    },
  });

  /**
   * Every way in: the picker, a drop, or a paste. A drop or paste over a reference asks first;
   * the picker only opens after the dentist confirmed, so it passes `confirmed`.
   */
  const receive = async (files: File[], { confirmed = false } = {}) => {
    const { upload: accepted, skipped } = partitionFiles(files);
    const lines = skipSummary(skipped);
    if (accepted.length > 1) lines.push(en.procedure.reference.onlyFirstUsed);
    if (lines.length > 0) notify({ kind: "info", lines });
    const first = accepted[0];
    if (first === undefined || uploading.current) return;
    // Held from here until the upload settles or the replacement is cancelled: a second drop or
    // paste must not start another upload while this one is converted or confirmed.
    uploading.current = true;
    let file: File;
    try {
      file = await toUploadable(first);
    } catch {
      uploading.current = false;
      notify({
        kind: "error",
        lines: [fill(en.procedure.reference.unreadableFormat, { name: first.name })],
      });
      return;
    }
    if (hasReference && !confirmed) {
      pendingReplacement.current = file;
      setReplacement(file);
      return;
    }
    upload.mutate(file);
  };

  // The paste listener lives for the panel's lifetime and always calls the current `receive`.
  const receiveRef = useRef(receive);
  useEffect(() => {
    receiveRef.current = receive;
  });

  useEffect(() => {
    // On `document`: a crop copied in Keynote is pasted wherever focus happens to be on the page.
    const onPaste = (event: ClipboardEvent) => {
      if (pastedIntoText(event.target) || anyDialogOpen()) return;
      const files = imagesFromClipboard(event.clipboardData);
      if (files.length === 0) return;
      event.preventDefault();
      void receiveRef.current(files);
    };
    document.addEventListener("paste", onPaste);
    return () => {
      document.removeEventListener("paste", onPaste);
    };
  }, []);

  const { dragging, handlers } = useFileDrop({
    inactive: upload.isPending,
    onFiles: (files) => {
      void receive(files);
    },
  });

  const onChoose = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    // So choosing the same file again still fires `change`.
    event.target.value = "";
    void receive(files, { confirmed: true });
  };

  const showDrop = reference === undefined || reference.state === "failed";

  return (
    <section
      aria-label={en.procedure.reference.title}
      {...handlers}
      className={`flex flex-col gap-4 rounded-lg border bg-white p-4 ${
        dragging ? "border-sky-700 ring-2 ring-sky-700" : "border-slate-300"
      }`}
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
          // Not `multiple={false}`: that keeps the first dropped file before the filter, so a folder
          // starting with an `.AAE` or `.MOV` would set no reference. `receive` picks the first photo.
          multiple
          help={en.procedure.reference.dropHelp}
          busy={upload.isPending ? en.procedure.reference.uploading : null}
          onFiles={(files) => {
            void receive(files);
          }}
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
          <p className="basis-full text-sm text-slate-700">{en.procedure.reference.replaceHint}</p>
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
      {replacement !== null && (
        <ConfirmDialog
          title={en.procedure.reference.replaceTitle}
          body={en.procedure.reference.replaceBody}
          confirmLabel={en.procedure.reference.replaceDropConfirm}
          onCancel={() => {
            pendingReplacement.current = null;
            setReplacement(null);
            uploading.current = false;
          }}
          onConfirm={() => {
            const file = pendingReplacement.current;
            pendingReplacement.current = null;
            setReplacement(null);
            if (file) upload.mutate(file);
          }}
        />
      )}

      <OutputResolution view={view} />
    </section>
  );
}
