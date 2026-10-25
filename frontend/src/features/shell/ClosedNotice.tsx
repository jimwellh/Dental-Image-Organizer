import { en } from "../../strings/en";

export function ClosedNotice() {
  return (
    <div
      role="alert"
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-50 p-8 text-center"
    >
      <p className="max-w-md text-xl text-slate-900">{en.shell.closed}</p>
    </div>
  );
}
