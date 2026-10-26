import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { autoAssign, patchCase, patchPhoto } from "../../api/endpoints";
import { compositeUrl, photoImageUrl } from "../../api/images";
import { useJob } from "../../api/jobs";
import { queryKeys, useCaseChange } from "../../api/queries";
import type { CaseView, PhotoPatch, PhotoRecord } from "../../api/types";
import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { SlotBoard } from "../../components/SlotBoard";
import { SLOTS } from "../../lib/slots";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { AiStatusBanner } from "../ai/AiStatusBanner";
import { CaseHeader } from "../case/CaseHeader";
import { ExportDialog } from "../export/ExportDialog";
import { DropZone } from "../intake/DropZone";
import { useCaseUpload } from "../intake/useCaseUpload";
import { cellsOf, moveRequest, trayOf } from "./board";
import { CellEditor } from "./CellEditor";

const frame = "aspect-[4/3] w-full rounded bg-slate-200 object-cover";

function Picture({
  caseId,
  photo,
  placed,
}: {
  caseId: string;
  photo: PhotoRecord;
  placed: boolean;
}) {
  if (photo.state !== "ready") {
    return (
      <div
        className={`${frame} flex items-center justify-center p-1 text-center text-xs text-slate-800`}
      >
        {photo.state === "pending" ? en.composite.processing : (photo.error ?? en.composite.failed)}
      </div>
    );
  }
  // A placed photo has been flipped and fitted; one in the tray has not, and a placed photo with
  // no edit yet has no processed image to ask for.
  const variant = placed && photo.edit ? "processed" : "original";
  return (
    <img
      src={photoImageUrl(caseId, photo, variant, "thumb")}
      alt={photo.source_name}
      loading="lazy"
      className={frame}
    />
  );
}

function BaselinePicker({ view }: { view: CaseView }) {
  const caseId = view.case.id;
  const selectId = useId();
  const change = useCaseChange(caseId, (photoId: string | null) =>
    patchCase(caseId, { composite: { baseline_photo_id: photoId } }),
  );
  const placed = SLOTS.map((slot) => ({
    slot,
    photo: view.case.photos.find((photo) => photo.slot === slot),
  })).filter((entry) => entry.photo !== undefined);
  const current = view.case.composite?.baseline_photo_id ?? null;
  // A baseline that left the grid no longer has an option; the default is what the server uses.
  const value = placed.some((entry) => entry.photo?.id === current) ? current : null;

  return (
    <div className="flex items-center gap-2">
      <label htmlFor={selectId} className="text-sm font-medium text-slate-900">
        {en.composite.baseline}
      </label>
      <select
        id={selectId}
        value={value ?? ""}
        disabled={change.isPending}
        onChange={(event) => {
          change.mutate(event.target.value === "" ? null : event.target.value);
        }}
        className="rounded-md border border-slate-400 bg-white px-2 py-1 text-sm text-slate-900"
      >
        <option value="">{en.composite.baselineDefault}</option>
        {placed.map(({ slot, photo }) => (
          <option key={slot} value={photo?.id}>
            {en.slots[slot]}
          </option>
        ))}
      </select>
    </div>
  );
}

function RerunAssignment({ view }: { view: CaseView }) {
  const caseId = view.case.id;
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  // A ref, not `isPending`: a double click on the confirm button must start one job.
  const starting = useRef(false);

  const start = useMutation({
    mutationFn: () => autoAssign(caseId),
    onSuccess: ({ job_id }) => {
      setJobId(job_id);
      if (job_id === null) void client.invalidateQueries({ queryKey: queryKeys.case(caseId) });
    },
    onSettled: () => {
      starting.current = false;
    },
  });
  const job = useJob(jobId);
  const state = job?.state ?? null;

  useEffect(() => {
    if (state !== "done" && state !== "failed") return;
    // A failed run may still have changed nothing, but the case is cheap to read again.
    void client.invalidateQueries({ queryKey: queryKeys.case(caseId) });
    void client.invalidateQueries({ queryKey: queryKeys.patients });
  }, [state, client, caseId]);

  const running = start.isPending || (jobId !== null && state !== "done" && state !== "failed");

  const begin = () => {
    if (starting.current) return;
    starting.current = true;
    setConfirming(false);
    setJobId(null);
    start.mutate();
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button
        disabled={running}
        onClick={() => {
          // The dentist's own placement is what a re-run replaces, so only then it asks.
          if (view.case.composite?.slots_source === "manual") setConfirming(true);
          else begin();
        }}
      >
        {running ? en.composite.rerunning : en.composite.rerun}
      </Button>
      {state === "failed" && (
        <p role="alert" className="text-sm text-red-900">
          {fill(en.composite.rerunFailed, { message: job?.error?.message ?? job?.message ?? "" })}
        </p>
      )}
      {confirming && (
        <ConfirmDialog
          title={en.composite.rerunTitle}
          body={en.composite.rerunBody}
          confirmLabel={en.composite.rerunConfirm}
          onCancel={() => {
            setConfirming(false);
          }}
          onConfirm={begin}
        />
      )}
    </div>
  );
}

export function CompositeScreen({ view }: { view: CaseView }) {
  const caseId = view.case.id;
  const [exporting, setExporting] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const { upload, progress } = useCaseUpload(caseId);
  const move = useCaseChange(caseId, (args: { photoId: string; patch: PhotoPatch }) =>
    patchPhoto(caseId, args.photoId, args.patch),
  );

  const cells = cellsOf(view);
  const tray = trayOf(view);
  const anyPlaced = SLOTS.some((slot) => cells[slot] !== null);

  return (
    <div className="flex flex-col gap-6">
      <CaseHeader
        view={view}
        onExport={() => {
          setExporting(true);
        }}
      />
      <AiStatusBanner />
      <DropZone
        queues
        label={en.composite.upload.label}
        busy={
          progress === null
            ? null
            : fill(en.composite.upload.progress, {
                done: String(progress.done),
                total: String(progress.total),
              })
        }
        onFiles={(files, unreadable) => {
          void upload(files, unreadable);
        }}
      />
      <section aria-label={en.composite.board} className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-slate-900">{en.composite.board}</h2>
        <SlotBoard
          cells={cells}
          tray={tray}
          idOf={(photo) => photo.id}
          nameOf={(photo) => photo.source_name}
          canMove={(photo) => photo.state === "ready" && !move.isPending}
          highlight={(photo) => photo.edit?.status === "needs_review"}
          renderItem={(photo, where) => (
            <>
              <Picture caseId={caseId} photo={photo} placed={where !== "tray"} />
              {where !== "tray" && (
                <span className="text-xs text-slate-800">
                  {en.flips[photo.edit?.flip ?? "none"]}
                </span>
              )}
            </>
          )}
          onMove={(photoId, target) => {
            const patch = moveRequest(view, photoId, target);
            if (patch !== null) move.mutate({ photoId, patch });
          }}
          onOpen={(photo) => {
            setEditing(photo.id);
          }}
        />
        <div className="flex flex-wrap items-center gap-6">
          <BaselinePicker view={view} />
          <RerunAssignment view={view} />
        </div>
      </section>
      {anyPlaced && (
        <img
          src={compositeUrl(view, "preview")}
          alt={en.composite.preview}
          className="max-w-full rounded-md bg-slate-200"
        />
      )}
      {editing !== null && (
        <CellEditor
          view={view}
          photoId={editing}
          onClose={() => {
            setEditing(null);
          }}
        />
      )}
      {exporting && (
        <ExportDialog
          target={{ kind: "case", caseId, caseType: view.case.type, folder: view.folder }}
          onClose={() => {
            setExporting(false);
          }}
        />
      )}
    </div>
  );
}
