import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, type ReactNode } from "react";
import { shutdown, updateStatus } from "../../api/endpoints";
import { queryKeys } from "../../api/queries";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { ApiError, describeError } from "../../api/http";
import { ClosedNotice } from "./ClosedNotice";
import { useServerConnection } from "./connection";
import { Header } from "./Header";
import { useNotify } from "./notify";
import { UpdateBanner } from "./UpdateBanner";

export function Shell({ children }: { children: ReactNode }) {
  const notify = useNotify();
  const client = useQueryClient();
  // The server came back from an update at the same version. A failed upgrade shows in the
  // banner, from the fresh status; an upgrade that found nothing newer is said here.
  const onUpdateUnchanged = useCallback(() => {
    client
      .query({ queryKey: queryKeys.update, queryFn: updateStatus, staleTime: 0 })
      .then((status) => {
        if (status.last_failure !== null) return;
        notify({
          kind: "info",
          lines: [fill(en.update.unchanged, { current: status.current, command: status.command })],
        });
      })
      // The connection watch reports a server that is gone again.
      .catch(() => undefined);
  }, [client, notify]);
  const { state, markClosed, markUpdating } = useServerConnection({ onUpdateUnchanged });

  // A drop outside every zone would make the browser open the file in this tab and lose the app.
  // Zones stop the event first, so only strays reach the window.
  useEffect(() => {
    const carriesFiles = (event: DragEvent) => event.dataTransfer?.types.includes("Files") ?? false;
    const onDragOver = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "none";
    };
    const onDrop = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      notify({ kind: "info", lines: [en.intake.dropOnZone] });
    };
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("drop", onDrop);
    };
  }, [notify]);

  if (state === "closed") return <ClosedNotice />;
  if (state === "updating") return <ClosedNotice updating />;

  const quit = async () => {
    try {
      await shutdown();
      markClosed();
    } catch (error) {
      // The server may exit before it finishes answering; that is the Quit working.
      if (error instanceof ApiError && error.code === "network") {
        markClosed();
      } else {
        notify({ kind: "error", lines: [describeError(error)] });
      }
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <Header
        onQuit={() => {
          void quit();
        }}
      />
      <UpdateBanner onUpdating={markUpdating} />
      <main className="p-4">{children}</main>
    </div>
  );
}
