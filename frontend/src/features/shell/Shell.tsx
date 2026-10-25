import type { ReactNode } from "react";
import { shutdown } from "../../api/endpoints";
import { ApiError, describeError } from "../../api/http";
import { ClosedNotice } from "./ClosedNotice";
import { useServerConnection } from "./connection";
import { Header } from "./Header";
import { useNotify } from "./notify";

export function Shell({ children }: { children: ReactNode }) {
  const { state, markClosed } = useServerConnection();
  const notify = useNotify();

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
