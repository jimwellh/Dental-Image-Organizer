import { describeError } from "../api/http";
import type { SkippedView, UploadResult } from "../api/types";

export interface UploadProgress {
  done: number;
  total: number;
}

export interface UploadSummary {
  added: string[];
  skipped: SkippedView[];
  jobIds: string[];
  failed: { name: string; message: string }[];
}

const DEFAULT_CONCURRENCY = 3;

/**
 * One file per request, so a bad file fails alone (§4.13), with a fixed number of requests open
 * at once (§5.3). Results are merged in file order, whatever order the requests finish in.
 */
export async function uploadAll(
  send: (file: File) => Promise<UploadResult>,
  files: readonly File[],
  options: { concurrency?: number; onProgress?: (progress: UploadProgress) => void } = {},
): Promise<UploadSummary> {
  const { concurrency = DEFAULT_CONCURRENCY, onProgress } = options;
  const total = files.length;
  const outcomes: ({ result: UploadResult } | { error: string })[] = new Array<never>(total);
  let next = 0;
  let done = 0;

  async function worker(): Promise<void> {
    while (next < total) {
      const index = next++;
      const file = files[index];
      if (file === undefined) return;
      try {
        outcomes[index] = { result: await send(file) };
      } catch (error) {
        outcomes[index] = { error: describeError(error) };
      }
      done++;
      onProgress?.({ done, total });
    }
  }

  if (total > 0) onProgress?.({ done: 0, total });
  await Promise.all(Array.from({ length: Math.min(Math.max(concurrency, 1), total) }, worker));

  const summary: UploadSummary = { added: [], skipped: [], jobIds: [], failed: [] };
  files.forEach((file, index) => {
    const outcome = outcomes[index];
    if (outcome === undefined) return;
    if ("error" in outcome) {
      summary.failed.push({ name: file.name, message: outcome.error });
      return;
    }
    summary.added.push(...outcome.result.added);
    summary.skipped.push(...outcome.result.skipped);
    if (outcome.result.job_id !== null) summary.jobIds.push(outcome.result.job_id);
  });
  return summary;
}
