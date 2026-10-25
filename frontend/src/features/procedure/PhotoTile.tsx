import { useId, useRef, useState } from "react";
import { deletePhoto, patchPhoto } from "../../api/endpoints";
import { photoImageUrl } from "../../api/images";
import { useCaseChange } from "../../api/queries";
import type { CaseView, PhotoRecord, Stage } from "../../api/types";
import { Badge } from "../../components/Badge";
import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";

const STAGES: readonly Stage[] = ["pre_op", "intra_op", "post_op"];

function Picture({ view, photo }: { view: CaseView; photo: PhotoRecord }) {
  const caseId = view.case.id;
  const frame = "aspect-[4/3] w-full rounded-md bg-slate-200 object-cover";
  if (photo.state !== "ready") return <div aria-hidden="true" className={frame} />;
  // A photo without an edit has no processed image yet: the reference it aligns to is missing.
  const variant = photo.edit ? "processed" : "original";
  return (
    <img
      src={photoImageUrl(caseId, photo, variant, "thumb")}
      alt={photo.source_name}
      loading="lazy"
      className={frame}
    />
  );
}

function Badges({ photo }: { photo: PhotoRecord }) {
  const { edit } = photo;
  const isReference = photo.role === "reference";
  return (
    <div className="flex flex-wrap gap-1">
      {photo.state === "pending" && <Badge tone="neutral">{en.procedure.tile.processing}</Badge>}
      {photo.state === "failed" && (
        <Badge tone="error">{photo.error ?? en.procedure.tile.failed}</Badge>
      )}
      {photo.state === "ready" && !edit && (
        <Badge tone="neutral">{en.procedure.tile.waitingForReference}</Badge>
      )}
      {/* The reference is the target of every alignment; a score or a review flag means nothing. */}
      {!isReference && edit && (
        <>
          {typeof edit.confidence === "number" && (
            <Badge tone="neutral">{`${String(Math.round(edit.confidence * 100))}%`}</Badge>
          )}
          {edit.status === "needs_review" && (
            <Badge tone="warn">{en.procedure.tile.needsReview}</Badge>
          )}
          {edit.source === "manual" && <Badge tone="neutral">{en.procedure.tile.manual}</Badge>}
        </>
      )}
      {photo.captured_at_source === "file_mtime" && (
        <Badge tone="warn">{en.procedure.tile.captureTimeWarning}</Badge>
      )}
    </div>
  );
}

export function PhotoTile(props: {
  view: CaseView;
  photo: PhotoRecord;
  onCompare: (photoId: string) => void;
}) {
  const { view, photo, onCompare } = props;
  const caseId = view.case.id;
  const nameId = useId();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  // A ref, not `isPending`: a double click on the confirm button must send one request.
  const deleting = useRef(false);

  const setStage = useCaseChange(caseId, (stage: Stage) => patchPhoto(caseId, photo.id, { stage }));
  const remove = useCaseChange(caseId, () => deletePhoto(caseId, photo.id));

  // A failed photo can still be deleted; otherwise it would stay in the case for good.
  const stage = photo.stage ?? null;
  const editable = photo.role !== "reference" && photo.state !== "pending";

  return (
    <li className="flex flex-col gap-2 rounded-lg border border-slate-300 bg-white p-2">
      <Picture view={view} photo={photo} />
      <p id={nameId} className="truncate text-sm font-medium text-slate-900">
        {photo.source_name}
      </p>
      <Badges photo={photo} />
      {editable && (
        <div className="flex flex-wrap items-center gap-2">
          {photo.state === "ready" && (
            <>
              <select
                aria-label={fill(en.procedure.tile.stage, { name: photo.source_name })}
                value={stage ?? ""}
                disabled={setStage.isPending}
                onChange={(event) => {
                  setStage.mutate(event.target.value as Stage);
                }}
                className="rounded-md border border-slate-400 bg-white px-2 py-1 text-sm text-slate-900"
              >
                {stage === null && (
                  <option value="" disabled>
                    {en.stages.unsorted}
                  </option>
                )}
                {STAGES.map((stage) => (
                  <option key={stage} value={stage}>
                    {en.stages[stage]}
                  </option>
                ))}
              </select>
              <Button
                aria-describedby={nameId}
                onClick={() => {
                  onCompare(photo.id);
                }}
              >
                {en.procedure.tile.compare}
              </Button>
            </>
          )}
          <Button
            aria-describedby={nameId}
            onClick={() => {
              setConfirmingDelete(true);
            }}
          >
            {en.procedure.tile.delete}
          </Button>
        </div>
      )}
      {confirmingDelete && (
        <ConfirmDialog
          title={en.procedure.tile.deleteTitle}
          body={fill(en.procedure.tile.deleteBody, { name: photo.source_name })}
          confirmLabel={en.procedure.tile.deleteConfirm}
          onCancel={() => {
            setConfirmingDelete(false);
          }}
          onConfirm={() => {
            if (deleting.current) return;
            deleting.current = true;
            remove.mutate(undefined, {
              onError: () => {
                deleting.current = false;
              },
            });
          }}
        />
      )}
    </li>
  );
}
