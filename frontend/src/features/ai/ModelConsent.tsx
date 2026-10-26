import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { downloadModel } from "../../api/endpoints";
import { useJob } from "../../api/jobs";
import { queryKeys, useModelStatus } from "../../api/queries";
import { Button } from "../../components/Button";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";

const MEGABYTE = 1e6;

// The spec names GitHub rather than its domain, which a dentist would not recognise as a brand.
const hostLabel = (host: string): string => (host === "github.com" ? "GitHub" : host);

/**
 * Asks before the one outbound request the app makes (spec §4.6), then follows the download. A
 * download that is already running (another tab, or before a reload) is followed without asking.
 */
export function ModelConsent() {
  const client = useQueryClient();
  const status = useModelStatus();
  const [declined, setDeclined] = useState(false);
  // The job this screen started; it wins over the status, which may still name a failed one.
  const [startedJobId, setStartedJobId] = useState<string | null>(null);

  const start = useMutation({
    mutationFn: downloadModel,
    onSuccess: ({ job_id }) => {
      setStartedJobId(job_id);
      // No job means the model arrived some other way.
      if (job_id === null) void client.invalidateQueries({ queryKey: queryKeys.model });
    },
  });

  const jobId = startedJobId ?? status.data?.download_job_id ?? null;
  const job = useJob(jobId);
  const finished = job?.state === "done";

  useEffect(() => {
    if (!finished) return;
    void client.invalidateQueries({ queryKey: queryKeys.model });
    void client.invalidateQueries({ queryKey: queryKeys.classifier });
  }, [finished, client]);

  if (status.data === undefined || status.data.available) return null;

  const chooseDownload = () => {
    if (start.isPending) return;
    start.mutate();
  };

  if (job?.state === "failed") {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-red-900">
        <p>{fill(en.ai.failed, { message: job.error?.message ?? job.message ?? "" })}</p>
        <Button onClick={chooseDownload} disabled={start.isPending}>
          {en.ai.retry}
        </Button>
      </div>
    );
  }

  if (jobId !== null) {
    const percent =
      job !== null && job.total > 0
        ? Math.min(100, Math.round((job.done / job.total) * 100))
        : null;
    return (
      <div role="status" className="flex flex-wrap items-center gap-3 text-sm text-slate-900">
        <p>{en.ai.downloading}</p>
        {percent !== null && (
          <>
            <progress
              aria-label={en.ai.downloadLabel}
              value={percent}
              max={100}
              className="h-2 w-40"
            />
            <span className="tabular-nums">{`${String(percent)}%`}</span>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 text-sm text-slate-900">
      {declined ? (
        <p>{en.ai.declined}</p>
      ) : (
        <p>
          {en.ai.consent(
            Math.round(status.data.size_bytes / MEGABYTE),
            hostLabel(status.data.host),
          )}
        </p>
      )}
      <div className="flex gap-2">
        <Button variant="primary" onClick={chooseDownload} disabled={start.isPending}>
          {en.ai.download}
        </Button>
        {!declined && (
          <Button
            onClick={() => {
              setDeclined(true);
            }}
          >
            {en.ai.notNow}
          </Button>
        )}
      </div>
    </div>
  );
}
