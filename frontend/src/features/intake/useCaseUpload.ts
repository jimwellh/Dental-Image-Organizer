import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";
import { uploadPhotos } from "../../api/endpoints";
import { queryKeys } from "../../api/queries";
import { partitionFiles, type UnreadableEntry } from "../../lib/dropFiles";
import { skipSummary } from "../../lib/skipped";
import { uploadAll, type UploadProgress } from "../../lib/uploadQueue";
import { useNotify } from "../shell/notify";

/**
 * Uploads drops into a case and says one thing about each. Drops run one after another, so the
 * limit of three requests holds however fast the user drops. Photos still being processed are
 * followed by `useCase`'s polling, so the upload's job ids are not kept.
 */
export function useCaseUpload(caseId: string): {
  upload: (files: File[], unreadable?: readonly UnreadableEntry[]) => Promise<void>;
  /** The files of every drop still queued or running together; null when all are done. */
  progress: UploadProgress | null;
} {
  const queryClient = useQueryClient();
  const notify = useNotify();
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const tail = useRef<Promise<void>>(Promise.resolve());
  const queue = useRef({ drops: 0, done: 0, total: 0 });

  const publish = useCallback(() => {
    const { drops, done, total } = queue.current;
    setProgress(drops > 0 && total > 0 ? { done, total } : null);
  }, []);

  const runDrop = useCallback(
    async (files: File[], unreadable: readonly UnreadableEntry[]) => {
      const { upload: accepted, skipped: clientSkipped } = partitionFiles(files);
      let reported = 0;
      try {
        const summary = await uploadAll((file) => uploadPhotos(caseId, [file]), accepted, {
          onProgress: ({ done }) => {
            queue.current.done += done - reported;
            reported = done;
            publish();
          },
        });
        const failed = [
          ...unreadable.map((entry) => ({ name: entry.name, message: entry.message })),
          ...summary.failed,
        ];
        const lines = [
          ...skipSummary([...clientSkipped, ...summary.skipped]),
          ...failed.map((failure) => `${failure.name}: ${failure.message}`),
        ];
        if (lines.length > 0) notify({ kind: failed.length > 0 ? "error" : "info", lines });
      } finally {
        queue.current.drops--;
        if (queue.current.drops === 0) queue.current = { drops: 0, done: 0, total: 0 };
        publish();
        await queryClient.invalidateQueries({ queryKey: queryKeys.case(caseId) });
      }
    },
    [caseId, notify, publish, queryClient],
  );

  const upload = useCallback(
    (files: File[], unreadable: readonly UnreadableEntry[] = []) => {
      // Counted when queued, so `progress` covers the waiting drops too.
      queue.current.drops++;
      queue.current.total += partitionFiles(files).upload.length;
      publish();
      const run = tail.current.then(() => runDrop(files, unreadable));
      // A failure of one drop must not stop the drops behind it.
      tail.current = run.catch(() => undefined);
      return run;
    },
    [publish, runDrop],
  );

  return { upload, progress };
}
