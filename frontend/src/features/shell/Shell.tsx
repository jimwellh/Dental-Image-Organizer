import { useEffect, type ReactNode } from "react";
import { shutdown } from "../../api/endpoints";
import { en } from "../../strings/en";
import { ApiError, describeError } from "../../api/http";
import { ClosedNotice } from "./ClosedNotice";
import { useServerConnection } from "./connection";
import { Header } from "./Header";
import { useNotify } from "./notify";

export function Shell({ children }: { children: ReactNode }) {
  const { state, markClosed } = useServerConnection();
  const notify = useNotify();

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
      <main className="p-4">{children}</main>
    </div>
  );
}
