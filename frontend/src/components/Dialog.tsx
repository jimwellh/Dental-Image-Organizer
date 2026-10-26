import { useEffect, useId, useRef, type ReactNode } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Open dialogs, oldest first. Only the last one handles the keyboard, so a confirmation opened
// over a dialog does not also close or trap the dialog beneath it.
const openDialogs: object[] = [];

// A `div` rather than `<dialog>`: jsdom does not implement `showModal`, and the tests need it.
export function Dialog(props: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const { title, onClose, children, wide = false } = props;
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);

  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    const token = {};
    openDialogs.push(token);
    const opener = document.activeElement;
    const element = panel.current;
    const first = element?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? element)?.focus();

    // On `document`, not on the panel: a click on the backdrop moves focus to `body`, and the
    // keys must still reach the dialog.
    const onKeyDown = (event: KeyboardEvent) => {
      if (openDialogs[openDialogs.length - 1] !== token || !element) return;
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = [...element.querySelectorAll<HTMLElement>(FOCUSABLE)];
      const firstItem = items[0];
      const lastItem = items[items.length - 1];
      if (!firstItem || !lastItem) {
        event.preventDefault();
        return;
      }
      const active = document.activeElement;
      // Focus on the panel itself or outside it has no place in the Tab order to continue from.
      const lost = active === element || !(active instanceof Node && element.contains(active));
      if (
        lost ||
        (event.shiftKey && active === firstItem) ||
        (!event.shiftKey && active === lastItem)
      ) {
        event.preventDefault();
        (event.shiftKey ? lastItem : firstItem).focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      openDialogs.splice(openDialogs.indexOf(token), 1);
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/50 p-4">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`max-h-full w-full overflow-auto rounded-lg bg-white p-6 shadow-xl ${wide ? "max-w-4xl" : "max-w-md"}`}
      >
        <h2 id={titleId} className="mb-3 text-lg font-semibold text-slate-900">
          {title}
        </h2>
        {children}
      </div>
    </div>
  );
}
