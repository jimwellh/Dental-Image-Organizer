import { useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { Button } from "../../components/Button";
import { filesFromDrop, type UnreadableEntry } from "../../lib/dropFiles";
import { en } from "../../strings/en";
import { useNotify } from "../shell/notify";

interface DropZoneProps {
  label: string;
  help?: string;
  multiple?: boolean;
  disabled?: boolean;
  /** Text shown instead of the prompt while work is under way; the zone ignores input then. */
  busy?: string | null;
  /** `unreadable` lists dropped entries the browser could not read; the rest still arrive. */
  onFiles: (files: File[], unreadable: readonly UnreadableEntry[]) => void;
}

export function DropZone({
  label,
  help = en.intake.photosHelp,
  multiple = true,
  disabled = false,
  busy = null,
  onFiles,
}: DropZoneProps) {
  const notify = useNotify();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const inactive = disabled || busy !== null;

  const deliver = (files: File[], unreadable: readonly UnreadableEntry[] = []) => {
    const chosen = multiple ? files : files.slice(0, 1);
    if (chosen.length > 0) onFiles(chosen, unreadable);
    // A partly readable drop is reported by the owner of the drop's single notice.
    else if (unreadable.length > 0) notify({ kind: "error", lines: [en.intake.readFailed] });
  };

  const onDragOver = (event: DragEvent<HTMLElement>) => {
    if (inactive) return;
    // Without this the browser opens the file instead of dropping it here.
    event.preventDefault();
    setDragging(true);
  };

  const onDragLeave = (event: DragEvent<HTMLElement>) => {
    // Moving over a child fires a leave on the zone; only a real exit clears the highlight.
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setDragging(false);
  };

  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    setDragging(false);
    if (inactive) return;
    // `filesFromDrop` reads the transfer synchronously before it awaits anything.
    filesFromDrop(event.dataTransfer).then(
      ({ files, unreadable }) => {
        deliver(files, unreadable);
      },
      () => {
        notify({ kind: "error", lines: [en.intake.readFailed] });
      },
    );
  };

  const onChoose = (event: ChangeEvent<HTMLInputElement>) => {
    deliver(Array.from(event.target.files ?? []));
    // So choosing the same file again still fires `change`.
    event.target.value = "";
  };

  return (
    <section
      aria-label={label}
      onDragEnter={onDragOver}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={`flex flex-col items-center gap-2 rounded-lg border-2 border-dashed p-6 text-center ${
        dragging ? "border-sky-700 bg-sky-50" : "border-slate-400 bg-white"
      }`}
    >
      <p className="font-medium text-slate-900">{label}</p>
      {busy !== null ? (
        <p role="status" className="text-sm text-slate-700">
          {busy}
        </p>
      ) : (
        <p className="text-sm text-slate-700">{help}</p>
      )}
      <Button
        disabled={inactive}
        onClick={() => {
          inputRef.current?.click();
        }}
      >
        {en.intake.chooseFiles}
      </Button>
      <input
        ref={inputRef}
        type="file"
        multiple={multiple}
        hidden
        tabIndex={-1}
        aria-hidden="true"
        disabled={inactive}
        onChange={onChoose}
      />
    </section>
  );
}
