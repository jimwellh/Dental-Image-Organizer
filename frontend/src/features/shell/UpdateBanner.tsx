import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { startUpdate } from "../../api/endpoints";
import { describeError } from "../../api/http";
import { useUpdateStatus } from "../../api/queries";
import type { Updating } from "../../api/types";
import { Button } from "../../components/Button";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";

/**
 * The header's update banner (spec §5.2, §7.3): a newer release with "Update now", or the
 * Terminal command where the app cannot update itself, and the last update's failure.
 */
export function UpdateBanner({ onUpdating }: { onUpdating: (fromVersion: string) => void }) {
  const status = useUpdateStatus();
  const [failureDismissed, setFailureDismissed] = useState(false);
  // The variable is the version the page runs, so the page knows when the new one answers.
  const start = useMutation<Updating, Error, string>({
    mutationFn: () => startUpdate(),
    // Shown in the banner, beside the button.
    meta: { quiet: true },
    onSuccess: (_answer, fromVersion) => {
      onUpdating(fromVersion);
    },
  });

  const data = status.data;
  if (data === undefined) return null;
  const failure = failureDismissed ? null : data.last_failure;
  const latest = data.available ? data.latest : null;
  if (latest === null && failure === null) return null;

  return (
    <div className="flex flex-col gap-2 border-b border-sky-200 bg-sky-50 px-4 py-2 text-sm text-slate-900">
      {failure !== null && (
        <div role="alert" className="flex flex-wrap items-center gap-3 text-red-900">
          <p>{fill(en.update.failed, { message: failure.message, log: failure.log_file })}</p>
          <Button
            onClick={() => {
              setFailureDismissed(true);
            }}
          >
            {en.common.dismiss}
          </Button>
        </div>
      )}
      {latest !== null && (
        <div className="flex flex-wrap items-center gap-3">
          <p>{fill(en.update.available, { latest, current: data.current })}</p>
          {data.can_update ? (
            <Button
              variant="primary"
              disabled={start.isPending}
              onClick={() => {
                start.mutate(data.current);
              }}
            >
              {start.isPending ? en.update.starting : en.update.now}
            </Button>
          ) : (
            <p>
              {en.update.terminal}{" "}
              <code className="rounded bg-white px-1.5 py-0.5 font-mono text-slate-900">
                {data.command}
              </code>
            </p>
          )}
        </div>
      )}
      {start.isError && (
        <p role="alert" className="text-red-900">
          {fill(en.update.startFailed, { message: describeError(start.error) })}
        </p>
      )}
    </div>
  );
}
