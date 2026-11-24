import { useState, type DragEvent } from "react";
import { filesFromDrop, type UnreadableEntry } from "../../lib/dropFiles";
import { en } from "../../strings/en";
import { useNotify } from "../shell/notify";

export interface FileDropHandlers {
  onDragEnter: (event: DragEvent<HTMLElement>) => void;
  onDragOver: (event: DragEvent<HTMLElement>) => void;
  onDragLeave: (event: DragEvent<HTMLElement>) => void;
  onDrop: (event: DragEvent<HTMLElement>) => void;
}

/**
 * The drag handlers of an element that takes dropped files, folders included. `onFiles` receives
 * every readable file and the entries the browser could not read; a drop with none readable is
 * reported here.
 */
export function useFileDrop(props: {
  inactive: boolean;
  onFiles: (files: File[], unreadable: readonly UnreadableEntry[]) => void;
}): { dragging: boolean; handlers: FileDropHandlers } {
  const { inactive, onFiles } = props;
  const notify = useNotify();
  const [dragging, setDragging] = useState(false);

  const onDragOver = (event: DragEvent<HTMLElement>) => {
    // Without this the browser opens the file instead of dropping it here.
    event.preventDefault();
    // The element has handled the event; the window-level guard in `Shell` is for strays only,
    // and an enclosing drop target must not take it too.
    event.stopPropagation();
    if (inactive) {
      event.dataTransfer.dropEffect = "none";
      return;
    }
    setDragging(true);
  };

  const onDragLeave = (event: DragEvent<HTMLElement>) => {
    // Moving over a child fires a leave on the element; only a real exit clears the highlight.
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setDragging(false);
  };

  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setDragging(false);
    if (inactive) return;
    // `filesFromDrop` reads the transfer synchronously before it awaits anything.
    filesFromDrop(event.dataTransfer).then(
      ({ files, unreadable }) => {
        if (files.length > 0) onFiles(files, unreadable);
        // A partly readable drop is reported by the owner of the drop's single notice.
        else if (unreadable.length > 0) notify({ kind: "error", lines: [en.intake.readFailed] });
      },
      () => {
        notify({ kind: "error", lines: [en.intake.readFailed] });
      },
    );
  };

  return {
    dragging,
    handlers: { onDragEnter: onDragOver, onDragOver, onDragLeave, onDrop },
  };
}
