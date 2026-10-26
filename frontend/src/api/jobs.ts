import { useEffect, useState } from "react";
import { en } from "../strings/en";
import { getJob, jobEventsUrl } from "./endpoints";
import { ApiError } from "./http";
import type { JobState, JobView } from "./types";

/** The part of the browser's `EventSource` that `followJob` uses, so a test can stand in for it. */
export interface EventSourceLike {
  readonly readyState: number;
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void;
  close(): void;
  onerror: ((event: Event) => void) | null;
}

// `EventSource.CLOSED`: the server refused the stream and the browser will not retry.
const CLOSED = 2;
const STATES: readonly JobState[] = ["queued", "running", "done", "failed"];

export function isFinished(job: JobView): boolean {
  return job.state === "done" || job.state === "failed";
}

function failedJob(jobId: string, code: string, message: string): JobView {
  return {
    id: jobId,
    kind: "unknown",
    state: "failed",
    done: 0,
    total: 0,
    message: null,
    result: null,
    error: { code, message },
  };
}

// The stream was refused, so the job's state has to be asked for once. A job the server no longer
// knows is one that died with a restart (jobs live in memory), which the user must start again.
async function jobAfterRefusal(jobId: string): Promise<JobView> {
  try {
    return await getJob(jobId);
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    if (error.code === "job_not_found") return failedJob(jobId, "job_lost", en.jobs.lost);
    return failedJob(jobId, error.code, error.message);
  }
}

/** Reports every state change of the job; the returned function stops following. */
export function followJob(jobId: string, onUpdate: (job: JobView) => void): () => void {
  // Looked up here, not at import, so the test stub applies.
  const source: EventSourceLike = new EventSource(jobEventsUrl(jobId));
  let stopped = false;
  let asked = false;

  const stop = () => {
    stopped = true;
    source.close();
  };

  for (const state of STATES) {
    source.addEventListener(state, (event) => {
      if (stopped) return;
      const job = JSON.parse(event.data) as JobView;
      onUpdate(job);
      if (isFinished(job)) stop();
    });
  }

  source.onerror = () => {
    // Readiness 0 means the browser is reconnecting by itself; only a closed stream needs help.
    if (stopped || asked || source.readyState !== CLOSED) return;
    asked = true;
    void jobAfterRefusal(jobId).then((job) => {
      if (!stopped) onUpdate(job);
    });
  };

  return stop;
}

/** The latest state of a job; `null` until its first event arrives. */
export function useJob(jobId: string | null): JobView | null {
  const [followed, setFollowed] = useState<{ jobId: string; job: JobView } | null>(null);

  useEffect(() => {
    if (jobId === null) return undefined;
    return followJob(jobId, (job) => {
      setFollowed({ jobId, job });
    });
  }, [jobId]);

  // A job from an earlier id must not show while the next one has had no event yet.
  return followed !== null && followed.jobId === jobId ? followed.job : null;
}

export interface ExportFailure {
  photo_id: string | null;
  message: string;
}

/** What `export_case` in `services/export.py` returns. */
export interface ExportResult {
  case_id: string;
  files: string[];
  failures: ExportFailure[];
  skipped: { pending: number; failed: number };
  learning:
    { samples: number; trained: boolean } | { error: string; message: string | null } | null;
}

/** What `_export_patient` in `services/export.py` returns. */
export interface PatientExportResult {
  patient_id: string;
  cases: ExportResult[];
  failed_cases: { case_id: string; message: string }[];
}

function resultOf(job: JobView): Record<string, unknown> {
  if (job.result === null) throw new TypeError(`Job ${job.id} has no result.`);
  return job.result;
}

// The schema types `JobView.result` as an open dict, so check the parts a screen would index.
export function exportResult(job: JobView): ExportResult {
  const result = resultOf(job);
  if (
    typeof result.case_id !== "string" ||
    !Array.isArray(result.files) ||
    !Array.isArray(result.failures) ||
    typeof result.skipped !== "object" ||
    result.skipped === null
  ) {
    throw new TypeError(`Job ${job.id} is not a case export.`);
  }
  return result as unknown as ExportResult;
}

export function patientExportResult(job: JobView): PatientExportResult {
  const result = resultOf(job);
  if (
    typeof result.patient_id !== "string" ||
    !Array.isArray(result.cases) ||
    !Array.isArray(result.failed_cases)
  ) {
    throw new TypeError(`Job ${job.id} is not a patient export.`);
  }
  return result as unknown as PatientExportResult;
}
