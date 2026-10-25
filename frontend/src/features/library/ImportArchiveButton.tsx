import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type ChangeEvent } from "react";
import { importArchive } from "../../api/endpoints";
import { ApiError, describeError } from "../../api/http";
import { queryKeys } from "../../api/queries";
import type { ImportPolicy } from "../../api/types";
import { Button } from "../../components/Button";
import { Dialog } from "../../components/Dialog";
import { en } from "../../strings/en";
import { useNotify } from "../shell/notify";

interface Conflict {
  file: File;
  title: string;
}

// `details` comes from the server as `unknown`; a missing title falls back to a plain phrase.
function conflictTitle(details: unknown): string {
  if (typeof details === "object" && details !== null && "title" in details) {
    if (typeof details.title === "string" && details.title !== "") return details.title;
  }
  return en.library.import.unnamed;
}

export function ImportArchiveButton() {
  const notify = useNotify();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  // A ref, not `isPending`: a double click on Skip or Import as a copy must send one request, and a
  // second copy would be a second case.
  const busy = useRef(false);
  const [conflict, setConflict] = useState<Conflict | null>(null);

  const run = useMutation({
    mutationFn: ({ file, policy }: { file: File; policy: ImportPolicy }) =>
      importArchive(file, policy),
    // The 409 is a question for the user, not an error to show; the rest is shown below.
    meta: { quiet: true },
    onSuccess: (result) => {
      setConflict(null);
      const line =
        result.status === "skipped" ? en.library.import.skipped : en.library.import.imported;
      notify({ kind: "info", lines: [line.replace("{title}", result.title)] });
      void queryClient.invalidateQueries({ queryKey: queryKeys.patients });
    },
    onError: (error, { file, policy }) => {
      if (
        policy === "ask" &&
        error instanceof ApiError &&
        error.status === 409 &&
        error.code === "case_exists"
      ) {
        setConflict({ file, title: conflictTitle(error.details) });
        return;
      }
      setConflict(null);
      notify({ kind: "error", lines: [describeError(error)] });
    },
    onSettled: () => {
      busy.current = false;
    },
  });

  const start = (file: File, policy: ImportPolicy) => {
    if (busy.current) return;
    busy.current = true;
    run.mutate({ file, policy });
  };

  const onChoose = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    // So choosing the same archive again still fires `change`.
    event.target.value = "";
    if (file) start(file, "ask");
  };

  return (
    <>
      <Button
        disabled={run.isPending}
        onClick={() => {
          input.current?.click();
        }}
      >
        {run.isPending && conflict === null
          ? en.library.import.importing
          : en.library.import.button}
      </Button>
      <input
        ref={input}
        type="file"
        accept=".zip"
        hidden
        tabIndex={-1}
        aria-hidden="true"
        onChange={onChoose}
      />
      {conflict !== null && (
        <Dialog
          title={en.library.import.conflictTitle}
          onClose={() => {
            if (!run.isPending) setConflict(null);
          }}
        >
          <p className="mb-5 text-sm text-slate-700">
            {en.library.import.conflictBody.replace("{title}", conflict.title)}
          </p>
          <div className="flex justify-end gap-2">
            <Button
              disabled={run.isPending}
              onClick={() => {
                start(conflict.file, "skip");
              }}
            >
              {en.library.import.skip}
            </Button>
            <Button
              variant="primary"
              disabled={run.isPending}
              onClick={() => {
                start(conflict.file, "copy");
              }}
            >
              {en.library.import.copy}
            </Button>
          </div>
        </Dialog>
      )}
    </>
  );
}
