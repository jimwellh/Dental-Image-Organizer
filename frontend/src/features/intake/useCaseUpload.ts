import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { uploadPhotos } from "../../api/endpoints";
import { queryKeys } from "../../api/queries";
import { partitionFiles } from "../../lib/dropFiles";
import { skipSummary } from "../../lib/skipped";
import { uploadAll, type UploadProgress } from "../../lib/uploadQueue";
import { useNotify } from "../shell/notify";

/**
 * Uploads one drop into a case and says one thing about it. Photos still being processed are
 * followed by `useCase`'s polling, so the upload's job ids are not kept.
 */
export function useCaseUpload(caseId: string): {
  upload: (files: File[]) => Promise<void>;
  progress: UploadProgress | null;
} {
  const queryClient = useQueryClient();
  const notify = useNotify();
  const [progress, setProgress] = useState<UploadProgress | null>(null);

  const upload = useCallback(
    async (files: File[]) => {
      const { upload: accepted, skipped: clientSkipped } = partitionFiles(files);
      try {
        const summary = await uploadAll((file) => uploadPhotos(caseId, [file]), accepted, {
          onProgress: setProgress,
        });
        const lines = [
          ...skipSummary([...clientSkipped, ...summary.skipped]),
          ...summary.failed.map((failure) => `${failure.name}: ${failure.message}`),
        ];
        if (lines.length > 0) {
          notify({ kind: summary.failed.length > 0 ? "error" : "info", lines });
        }
      } finally {
        setProgress(null);
        await queryClient.invalidateQueries({ queryKey: queryKeys.case(caseId) });
      }
    },
    [caseId, notify, queryClient],
  );

  return { upload, progress };
}
