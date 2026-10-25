import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { en } from "../../strings/en";
import { INFO_NOTICE_MS, NoticeContext, type Notice, type Notify } from "./notify";

export function NoticeProvider({ children }: { children: ReactNode }) {
  const [notices, setNotices] = useState<Notice[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Set<number>());

  const dismiss = useCallback((id: number) => {
    setNotices((current) => current.filter((notice) => notice.id !== id));
  }, []);

  const notify = useCallback<Notify>(
    (notice) => {
      const id = nextId.current++;
      setNotices((current) => [...current, { ...notice, id }]);
      if (notice.kind === "info") {
        const timer = window.setTimeout(() => {
          timers.current.delete(timer);
          dismiss(id);
        }, INFO_NOTICE_MS);
        timers.current.add(timer);
      }
    },
    [dismiss],
  );

  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const timer of pending) window.clearTimeout(timer);
      pending.clear();
    };
  }, []);

  const infos = notices.filter((notice) => notice.kind === "info");
  const errors = notices.filter((notice) => notice.kind === "error");

  return (
    <NoticeContext value={notify}>
      {children}
      <div className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-96 max-w-[calc(100vw-2rem)] flex-col gap-2">
        {/* Always mounted so a screen reader hears the first notice. */}
        <div role="status" className="flex flex-col gap-2">
          {infos.map((notice) => (
            <div
              key={notice.id}
              className="pointer-events-auto rounded-md border border-slate-300 bg-white p-3 text-sm text-slate-900 shadow-lg"
            >
              {notice.lines.map((line, index) => (
                <p key={index}>{line}</p>
              ))}
            </div>
          ))}
        </div>
        {errors.map((notice) => (
          <div
            key={notice.id}
            role="alert"
            className="pointer-events-auto flex items-start gap-3 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900 shadow-lg"
          >
            <div className="flex-1">
              {notice.lines.map((line, index) => (
                <p key={index}>{line}</p>
              ))}
            </div>
            <button
              type="button"
              onClick={() => {
                dismiss(notice.id);
              }}
              className="rounded px-1 font-medium underline"
            >
              {en.common.dismiss}
            </button>
          </div>
        ))}
      </div>
    </NoticeContext>
  );
}
