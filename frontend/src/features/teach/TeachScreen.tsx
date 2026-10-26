import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { cancelTeaching, confirmTeaching, putSettings, startTeaching } from "../../api/endpoints";
import { describeError } from "../../api/http";
import { teachImageUrl } from "../../api/images";
import { useJob } from "../../api/jobs";
import {
  queryKeys,
  useClassifier,
  useModelStatus,
  useSettings,
  useTeachSession,
} from "../../api/queries";
import type {
  ClassifierView,
  Flip,
  Settings,
  Slot,
  SkippedView,
  TeachPhotoView,
  TeachSessionView,
} from "../../api/types";
import { Button } from "../../components/Button";
import { ShootingOrderPicker } from "../../components/ShootingOrderPicker";
import { SlotBoard } from "../../components/SlotBoard";
import { partitionFiles, type UnreadableEntry } from "../../lib/dropFiles";
import { skipSummary } from "../../lib/skipped";
import { FLIPS, SLOTS, moveInMap, type SlotMapDraft } from "../../lib/slots";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { ModelConsent } from "../ai/ModelConsent";
import { DropZone } from "../intake/DropZone";
import { useNotify } from "../shell/notify";
import { teachProgress } from "./progress";

const frame = "aspect-[4/3] w-full rounded bg-slate-200 object-cover";

const isFlip = (value: string): value is Flip => FLIPS.some((flip) => flip === value);

interface Open {
  sessionId: string;
  jobId: string | null;
}

function Progress({ status }: { status: ClassifierView }) {
  const { set, of, trained } = teachProgress(status);
  return (
    <section className="flex flex-col gap-2" aria-label={en.teach.counts}>
      <p className="text-base font-medium text-slate-900">
        {trained ? en.teach.trained : en.teach.progress(set, of)}
      </p>
      <ul className="grid max-w-xl grid-cols-1 gap-x-6 gap-y-1 text-sm text-slate-800 sm:grid-cols-3">
        {SLOTS.map((slot) => (
          <li key={slot} className="tabular-nums">
            {fill(en.teach.count, {
              slot: en.slots[slot],
              n: String(status.counts[slot] ?? 0),
              of: String(of),
            })}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ShootingOrder({ settings, disabled }: { settings: Settings; disabled: boolean }) {
  const client = useQueryClient();
  // `PUT` replaces the settings, so the order goes out with every other setting as it was read.
  const save = useMutation({
    mutationFn: (order: Slot[]) => putSettings({ ...settings, shooting_order: order }),
    onSuccess: (saved) => {
      client.setQueryData(queryKeys.settings, saved);
    },
  });
  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-lg font-semibold text-slate-900">{en.teach.order.title}</h2>
      <p className="text-sm text-slate-800">{en.teach.order.help}</p>
      <ShootingOrderPicker
        value={settings.shooting_order}
        disabled={disabled || save.isPending}
        onChange={(order) => {
          save.mutate(order);
        }}
      />
    </section>
  );
}

function Picture({ sessionId, photo }: { sessionId: string; photo: TeachPhotoView }) {
  return (
    <img
      src={teachImageUrl(sessionId, photo.id, "thumb")}
      alt={photo.name}
      loading="lazy"
      className={frame}
    />
  );
}

function Confirmation(props: {
  session: TeachSessionView;
  classifier: ClassifierView | undefined;
  cancelling: boolean;
  onCancel: () => void;
  onConfirmed: (status: ClassifierView) => void;
}) {
  const { session, classifier, cancelling, onCancel, onConfirmed } = props;
  // The proposal only seeds the draft: from here on the dentist's moves are the truth.
  const [draft, setDraft] = useState<SlotMapDraft>(() => {
    const seeded: SlotMapDraft = {};
    for (const slot of SLOTS) {
      const photoId = session.proposal[slot];
      if (photoId !== undefined) seeded[slot] = photoId;
    }
    return seeded;
  });
  const [chosenFlips, setChosenFlips] = useState<Partial<Record<Slot, Flip>>>({});
  // A ref, not `isPending`: a double click must send one confirmation.
  const sending = useRef(false);

  const ready = session.photos.filter((photo) => photo.state === "ready");
  const failed = session.photos.filter((photo) => photo.state === "failed");
  const byId = new Map(ready.map((photo) => [photo.id, photo]));
  const cells = Object.fromEntries(
    SLOTS.map((slot) => [slot, byId.get(draft[slot] ?? "") ?? null]),
  ) as Record<Slot, TeachPhotoView | null>;
  const placed = new Set(Object.values(draft));
  const tray = ready.filter((photo) => !placed.has(photo.id));
  const filled = SLOTS.filter((slot) => cells[slot] !== null);

  const flipOf = (slot: Slot): Flip =>
    chosenFlips[slot] ?? classifier?.flips[slot]?.effective ?? "none";

  const confirm = useMutation({
    mutationFn: () => {
      const slots: Record<string, string> = {};
      const flips: Record<string, Flip> = {};
      for (const slot of filled) {
        slots[slot] = draft[slot] ?? "";
        flips[slot] = flipOf(slot);
      }
      return confirmTeaching(session.id, { slots, flips });
    },
    onSuccess: onConfirmed,
    onSettled: () => {
      sending.current = false;
    },
  });

  const busy = confirm.isPending || cancelling;

  return (
    <section aria-label={en.teach.board} className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold text-slate-900">{en.teach.board}</h2>
      <p className="text-sm text-slate-800">{en.teach.boardHelp}</p>
      <SlotBoard
        cells={cells}
        tray={tray}
        idOf={(photo) => photo.id}
        nameOf={(photo) => photo.name}
        canMove={() => !busy}
        renderItem={(photo, where) => (
          <>
            <Picture sessionId={session.id} photo={photo} />
            {where !== "tray" && (
              <select
                aria-label={fill(en.teach.flip, { name: en.slots[where] })}
                value={flipOf(where)}
                disabled={busy}
                onChange={(event) => {
                  const flip = event.target.value;
                  if (isFlip(flip)) setChosenFlips({ ...chosenFlips, [where]: flip });
                }}
                className="rounded-md border border-slate-400 bg-white px-1 py-0.5 text-xs text-slate-900 disabled:opacity-50"
              >
                {FLIPS.map((flip) => (
                  <option key={flip} value={flip}>
                    {en.flips[flip]}
                  </option>
                ))}
              </select>
            )}
          </>
        )}
        onMove={(photoId, target) => {
          setDraft(moveInMap(draft, photoId, target));
        }}
      />
      {failed.length > 0 && (
        <div className="text-sm text-slate-900">
          <p className="font-medium">{en.teach.failedTitle}</p>
          <ul className="list-disc pl-5">
            {failed.map((photo) => (
              <li key={photo.id}>
                {fill(en.teach.failedItem, {
                  name: photo.name,
                  message: photo.error ?? en.teach.unknownError,
                })}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex gap-2">
        <Button
          variant="primary"
          disabled={busy || filled.length === 0}
          onClick={() => {
            if (sending.current) return;
            sending.current = true;
            confirm.mutate();
          }}
        >
          {confirm.isPending ? en.teach.confirming : en.teach.confirm}
        </Button>
        <Button disabled={busy} onClick={onCancel}>
          {en.common.cancel}
        </Button>
      </div>
    </section>
  );
}

/** One open session: the reading of its photos, then the board to check. */
function SessionPanel(props: {
  open: Open;
  classifier: ClassifierView | undefined;
  onClose: () => void;
}) {
  const { open, classifier, onClose } = props;
  const client = useQueryClient();
  const job = useJob(open.jobId);
  const session = useTeachSession(job?.state === "done" ? open.sessionId : null);

  const cancel = useMutation({
    mutationFn: () => cancelTeaching(open.sessionId),
    // The user is leaving the set; a session the server already lost needs no notice.
    meta: { quiet: true },
    onSettled: () => {
      client.removeQueries({ queryKey: queryKeys.teach(open.sessionId) });
      onClose();
    },
  });
  const cancelButton = (
    <div>
      <Button
        disabled={cancel.isPending}
        onClick={() => {
          cancel.mutate();
        }}
      >
        {en.common.cancel}
      </Button>
    </div>
  );

  if (job?.state === "failed") {
    return (
      <div className="flex flex-col gap-3">
        <p role="alert" className="text-sm text-red-900">
          {fill(en.teach.jobFailed, { message: job.error?.message ?? job.message ?? "" })}
        </p>
        {cancelButton}
      </div>
    );
  }

  if (session.isError) {
    return (
      <div className="flex flex-col gap-3">
        <p role="alert" className="text-sm text-red-900">
          {fill(en.teach.sessionLost, { message: describeError(session.error) })}
        </p>
        {cancelButton}
      </div>
    );
  }

  if (session.data !== undefined) {
    return (
      <Confirmation
        key={session.data.id}
        session={session.data}
        classifier={classifier}
        cancelling={cancel.isPending}
        onCancel={() => {
          cancel.mutate();
        }}
        onConfirmed={(status) => {
          client.setQueryData(queryKeys.classifier, status);
          client.removeQueries({ queryKey: queryKeys.teach(open.sessionId) });
          onClose();
        }}
      />
    );
  }

  const percent =
    job !== null && job.total > 0 ? Math.min(100, Math.round((job.done / job.total) * 100)) : null;
  return (
    <div className="flex flex-col gap-3">
      <div role="status" className="flex flex-wrap items-center gap-3 text-sm text-slate-900">
        <p>
          {job !== null && job.total > 0
            ? fill(en.teach.reading, { done: String(job.done), total: String(job.total) })
            : en.teach.readingStart}
        </p>
        {percent !== null && (
          <progress
            aria-label={en.teach.readingStart}
            value={percent}
            max={100}
            className="h-2 w-40"
          />
        )}
      </div>
      {cancelButton}
    </div>
  );
}

export function TeachScreen() {
  const notify = useNotify();
  const modelStatus = useModelStatus();
  const settings = useSettings();
  const classifier = useClassifier();
  const [open, setOpen] = useState<Open | null>(null);

  // The session to cancel when the screen goes away. Kept in a ref that an effect updates, so the
  // unmount cleanup sees the latest one without re-running (and cancelling) on every change.
  const openSession = useRef<string | null>(null);
  useEffect(() => {
    openSession.current = open?.sessionId ?? null;
  }, [open]);
  useEffect(
    () => () => {
      const sessionId = openSession.current;
      // Quiet by construction: nothing is on screen to report to, and the server empties the
      // sessions at startup anyway (spec §3.5 step 5).
      if (sessionId !== null) void cancelTeaching(sessionId).catch(() => undefined);
    },
    [],
  );

  const start = useMutation({
    mutationFn: async (drop: {
      upload: File[];
      skipped: SkippedView[];
      unreadable: readonly UnreadableEntry[];
    }) => ({ ...drop, result: await startTeaching(drop.upload) }),
    onSuccess: ({ result, skipped, unreadable }) => {
      const lines = [
        ...skipSummary([...skipped, ...result.skipped]),
        ...unreadable.map((entry) => `${entry.name}: ${entry.message}`),
      ];
      const usable = result.session_id !== null;
      if (!usable) lines.push(en.teach.nothingUsable);
      if (lines.length > 0)
        notify({ kind: usable && unreadable.length === 0 ? "info" : "error", lines });
      if (result.session_id !== null)
        setOpen({ sessionId: result.session_id, jobId: result.job_id });
    },
  });

  if (modelStatus.data === undefined) return null;
  if (!modelStatus.data.available) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-xl font-semibold">{en.teach.title}</h1>
        <ModelConsent />
      </div>
    );
  }

  const onFiles = (files: File[], unreadable: readonly UnreadableEntry[]) => {
    // One request holds the whole set: a session is one set, so the files must not be split.
    const { upload, skipped } = partitionFiles(files);
    if (upload.length === 0) {
      notify({
        kind: "error",
        lines: [
          ...skipSummary(skipped),
          ...unreadable.map((entry) => `${entry.name}: ${entry.message}`),
          en.teach.nothingUsable,
        ],
      });
      return;
    }
    start.mutate({ upload, skipped, unreadable });
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold">{en.teach.title}</h1>
        <p className="text-sm text-slate-800">{en.teach.intro}</p>
      </div>
      {settings.data !== undefined && (
        <ShootingOrder settings={settings.data} disabled={open !== null} />
      )}
      {classifier.data !== undefined && <Progress status={classifier.data} />}
      {open === null ? (
        <DropZone
          label={en.teach.drop.label}
          help={`${en.teach.drop.help} ${en.intake.photosHelp}`}
          busy={start.isPending ? en.teach.uploading : null}
          onFiles={onFiles}
        />
      ) : (
        <SessionPanel
          key={open.sessionId}
          open={open}
          classifier={classifier.data}
          onClose={() => {
            setOpen(null);
          }}
        />
      )}
    </div>
  );
}
