import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import {
  caseExportZipUrl,
  exportCase,
  exportPatient,
  patientExportZipUrl,
  reveal,
} from "../../api/endpoints";
import {
  exportResult,
  patientExportResult,
  useJob,
  type ExportResult,
  type PatientExportResult,
} from "../../api/jobs";
import { queryKeys } from "../../api/queries";
import type { CaseType, ConflictMode, ExportInclude, JobView } from "../../api/types";
import { Button } from "../../components/Button";
import { Dialog } from "../../components/Dialog";
import { en } from "../../strings/en";

type CaseTitle = { id: string; title: string };

export type ExportTarget =
  | { kind: "case"; caseId: string; caseType: CaseType; folder: string }
  | {
      kind: "patient";
      patientId: string;
      folder: string;
      cases: readonly CaseTitle[];
    };

const ALL_PARTS: readonly ExportInclude[] = ["singles", "sequence", "composite"];

// The server leaves out what a case type has no use for, so a patient export offers all three.
function partsFor(target: ExportTarget): readonly ExportInclude[] {
  if (target.kind === "patient") return ALL_PARTS;
  return target.caseType === "composite" ? ["composite"] : ["singles", "sequence"];
}

const phrase = (forms: { one: string; other: string }, n: number): string =>
  (n === 1 ? forms.one : forms.other).replace("{n}", String(n));

const baseName = (path: string): string => path.slice(path.lastIndexOf("/") + 1);

const linkClass =
  "inline-block rounded-md bg-sky-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-800";

export function ExportDialog(props: { target: ExportTarget; onClose: () => void }) {
  const { target, onClose } = props;
  const queryClient = useQueryClient();
  const parts = partsFor(target);
  const [chosen, setChosen] = useState<ReadonlySet<ExportInclude>>(() => new Set(parts));
  const [conflict, setConflict] = useState<ConflictMode>("keep_both");
  const [jobId, setJobId] = useState<string | null>(null);
  // A ref, not `isPending`: the second click of a double click can arrive before React has
  // rendered the disabled button.
  const requested = useRef(false);

  const job = useJob(jobId);
  const jobState = job?.state;

  const start = useMutation({
    mutationFn: (request: { include: ExportInclude[]; conflict: ConflictMode }) =>
      target.kind === "case"
        ? exportCase(target.caseId, request)
        : exportPatient(target.patientId, request),
    onSuccess: (ref) => {
      setJobId(ref.job_id);
    },
    onError: () => {
      // The global handler tells the user; the dialog stays on the choices for another try.
      requested.current = false;
    },
  });

  const reveals = useMutation({ mutationFn: reveal });

  // The export wrote files and may have taught the classifier, so what the screens show is stale.
  const caseId = target.kind === "case" ? target.caseId : null;
  useEffect(() => {
    if (jobState !== "done") return;
    void queryClient.invalidateQueries({
      queryKey: caseId === null ? queryKeys.patients : queryKeys.case(caseId),
    });
  }, [jobState, caseId, queryClient]);

  const include = parts.filter((part) => chosen.has(part));

  const onStart = () => {
    if (requested.current || include.length === 0) return;
    requested.current = true;
    start.mutate({ include, conflict });
  };

  const toggle = (part: ExportInclude) => {
    setChosen((current) => {
      const next = new Set(current);
      if (!next.delete(part)) next.add(part);
      return next;
    });
  };

  const revealPath = target.kind === "case" ? `${target.folder}/export` : target.folder;
  const zipUrl =
    jobId === null
      ? null
      : target.kind === "case"
        ? caseExportZipUrl(target.caseId, jobId)
        : patientExportZipUrl(target.patientId, jobId);

  const finished = job !== null && (job.state === "done" || job.state === "failed");

  return (
    <Dialog title={en.export.title} onClose={onClose}>
      {jobId === null ? (
        <div className="flex flex-col gap-4">
          <fieldset className="flex flex-col gap-1">
            <legend className="mb-1 text-sm font-medium text-slate-900">{en.export.include}</legend>
            {parts.map((part) => (
              <label key={part} className="flex items-center gap-2 text-sm text-slate-900">
                <input
                  type="checkbox"
                  checked={chosen.has(part)}
                  onChange={() => {
                    toggle(part);
                  }}
                />
                {en.export[part]}
              </label>
            ))}
          </fieldset>
          <fieldset className="flex flex-col gap-1">
            <legend className="mb-1 text-sm font-medium text-slate-900">
              {en.export.conflict}
            </legend>
            {(
              [
                ["keep_both", en.export.keepBoth],
                ["replace", en.export.replace],
              ] as const
            ).map(([value, label]) => (
              <label key={value} className="flex items-center gap-2 text-sm text-slate-900">
                <input
                  type="radio"
                  name="conflict"
                  checked={conflict === value}
                  onChange={() => {
                    setConflict(value);
                  }}
                />
                {label}
              </label>
            ))}
            <p className="text-sm text-slate-700">{en.export.replaceHelp}</p>
          </fieldset>
          <div className="flex justify-end gap-2">
            <Button onClick={onClose}>{en.common.cancel}</Button>
            <Button
              variant="primary"
              disabled={include.length === 0 || start.isPending}
              onClick={onStart}
            >
              {start.isPending ? en.export.starting : en.export.start}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {job !== null && job.state === "failed" ? (
            <p role="alert" className="text-sm text-red-800">
              {en.export.jobFailed.replace("{message}", job.error?.message ?? "")}
            </p>
          ) : job !== null && job.state === "done" ? (
            <Result job={job} target={target} />
          ) : (
            <Progress job={job} />
          )}
          <div className="flex flex-wrap justify-end gap-2">
            {job?.state === "done" && (
              <>
                <Button
                  disabled={reveals.isPending}
                  onClick={() => {
                    reveals.mutate(revealPath);
                  }}
                >
                  {en.export.reveal}
                </Button>
                {zipUrl !== null && (
                  <a href={zipUrl} download className={linkClass}>
                    {en.export.download}
                  </a>
                )}
              </>
            )}
            <Button variant={finished ? "primary" : "secondary"} onClick={onClose}>
              {en.export.close}
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

function Progress({ job }: { job: JobView | null }) {
  return (
    <p role="status" className="text-sm text-slate-900">
      {job !== null && job.total > 0
        ? en.export.progress
            .replace("{done}", String(job.done))
            .replace("{total}", String(job.total))
        : en.export.working}
    </p>
  );
}

type Parsed =
  | { kind: "case"; result: ExportResult }
  | { kind: "patient"; result: PatientExportResult; cases: readonly CaseTitle[] }
  | null;

// The server promises the shape; a different one is shown as an error instead of crashing.
function parseResult(job: JobView, target: ExportTarget): Parsed {
  try {
    return target.kind === "case"
      ? { kind: "case", result: exportResult(job) }
      : { kind: "patient", result: patientExportResult(job), cases: target.cases };
  } catch (error) {
    if (error instanceof TypeError) return null;
    throw error;
  }
}

function Result({ job, target }: { job: JobView; target: ExportTarget }) {
  const parsed = parseResult(job, target);
  if (parsed === null) {
    return (
      <p role="alert" className="text-sm text-red-800">
        {en.export.badResult}
      </p>
    );
  }
  return parsed.kind === "case" ? (
    <CaseResult result={parsed.result} />
  ) : (
    <PatientResult result={parsed.result} cases={parsed.cases} />
  );
}

// Never the raw error code: the user cannot act on `no_model`.
function learningLine(learning: NonNullable<ExportResult["learning"]>): string {
  if (!("error" in learning)) {
    return learning.trained
      ? phrase(en.export.learned, learning.samples)
      : en.export.savedForClassifier(learning.samples);
  }
  if (learning.error === "no_model") return en.export.learningNoModel;
  if (learning.error === "failed" && learning.message) {
    return en.export.learningFailed.replace("{message}", learning.message);
  }
  return en.export.learningUnknown;
}

function CaseResult({ result }: { result: ExportResult }) {
  const { learning, skipped } = result;
  return (
    <div className="flex flex-col gap-2 text-sm text-slate-900">
      {result.files.length > 0 ? (
        <>
          <p>{phrase(en.export.files, result.files.length)}</p>
          <ul className="list-inside list-disc break-all">
            {result.files.map((file) => (
              <li key={file}>{baseName(file)}</li>
            ))}
          </ul>
        </>
      ) : (
        <p>{en.export.noFiles}</p>
      )}
      {result.failures.map((failure, index) => (
        <p key={`${failure.photo_id ?? ""}-${String(index)}`} className="text-red-800">
          {en.export.failure.replace("{message}", failure.message)}
        </p>
      ))}
      {skipped.pending > 0 && <p>{phrase(en.export.skippedPending, skipped.pending)}</p>}
      {skipped.failed > 0 && <p>{phrase(en.export.skippedFailed, skipped.failed)}</p>}
      {learning !== null && <p>{learningLine(learning)}</p>}
    </div>
  );
}

function PatientResult(props: { result: PatientExportResult; cases: readonly CaseTitle[] }) {
  const { result, cases } = props;
  const titleOf = (caseId: string) =>
    cases.find((entry) => entry.id === caseId)?.title ?? en.export.unknownCase;
  return (
    <ul className="flex flex-col gap-1 text-sm text-slate-900">
      {result.cases.map((entry) => (
        <li key={entry.case_id}>
          {phrase(en.export.caseFiles, entry.files.length).replace(
            "{title}",
            titleOf(entry.case_id),
          )}
        </li>
      ))}
      {result.failed_cases.map((entry) => (
        <li key={entry.case_id} className="text-red-800">
          {en.export.caseFailed
            .replace("{title}", titleOf(entry.case_id))
            .replace("{message}", entry.message)}
        </li>
      ))}
    </ul>
  );
}
