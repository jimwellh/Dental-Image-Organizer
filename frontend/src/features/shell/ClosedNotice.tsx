import { en } from "../../strings/en";

/** Replaces the page while the server is gone: closed for good, or restarting after an update. */
export function ClosedNotice({ updating = false }: { updating?: boolean }) {
  return (
    <div
      role={updating ? "status" : "alert"}
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-50 p-8 text-center"
    >
      <p className="max-w-md text-xl text-slate-900">
        {updating ? en.shell.updating : en.shell.closed}
      </p>
    </div>
  );
}
