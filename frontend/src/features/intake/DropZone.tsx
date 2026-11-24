import { useRef, type ChangeEvent } from "react";
import { Button } from "../../components/Button";
import type { UnreadableEntry } from "../../lib/dropFiles";
import { en } from "../../strings/en";
import { useFileDrop } from "./useFileDrop";

interface DropZoneProps {
  label: string;
  help?: string;
  multiple?: boolean;
  disabled?: boolean;
  /**
   * Text shown instead of the prompt while work is under way. The zone takes no new input then,
   * unless `queues` says its owner lines further drops up behind the running one.
   */
  busy?: string | null;
  queues?: boolean;
  /** `unreadable` lists dropped entries the browser could not read; the rest still arrive. */
  onFiles: (files: File[], unreadable: readonly UnreadableEntry[]) => void;
}

export function DropZone({
  label,
  help = en.intake.photosHelp,
  multiple = true,
  disabled = false,
  busy = null,
  queues = false,
  onFiles,
}: DropZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  // A disabled or busy zone still claims the drag: unclaimed, the browser would open the dropped
  // file in this tab and abandon the app and its uploads.
  const inactive = disabled || (busy !== null && !queues);

  const deliver = (files: File[], unreadable: readonly UnreadableEntry[] = []) => {
    const chosen = multiple ? files : files.slice(0, 1);
    if (chosen.length > 0) onFiles(chosen, unreadable);
  };
  const { dragging, handlers } = useFileDrop({ inactive, onFiles: deliver });

  const onChoose = (event: ChangeEvent<HTMLInputElement>) => {
    deliver(Array.from(event.target.files ?? []));
    // So choosing the same file again still fires `change`.
    event.target.value = "";
  };

  return (
    <section
      aria-label={label}
      {...handlers}
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
