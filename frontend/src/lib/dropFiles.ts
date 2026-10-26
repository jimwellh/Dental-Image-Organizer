import type { SkippedView, UploadSkipReason } from "../api/types";
import { en } from "../strings/en";

/** Files that never need a round trip: Live Photo clips and Photos.app edit sidecars (§5.3). */
export const CLIENT_SKIPS: Readonly<Record<string, UploadSkipReason>> = {
  ".mov": "video",
  ".mp4": "video",
  ".aae": "sidecar",
};

const SKIP_MESSAGES: Readonly<Partial<Record<UploadSkipReason, string>>> = {
  video: en.skip.videoMessage,
  sidecar: en.skip.sidecarMessage,
};

const readEntries = (reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> =>
  new Promise((resolve, reject) => {
    reader.readEntries(resolve, reject);
  });

const readFile = (entry: FileSystemFileEntry): Promise<File> =>
  new Promise((resolve, reject) => {
    entry.file(resolve, reject);
  });

/** An entry of a drop that the browser could not read; the rest of the drop still goes on. */
export interface UnreadableEntry {
  name: string;
  message: string;
}

export interface DroppedFiles {
  files: File[];
  unreadable: UnreadableEntry[];
}

/** One file or folder that failed to read costs only itself (batch isolation, §4.13). */
async function walk(entry: FileSystemEntry, found: DroppedFiles): Promise<void> {
  try {
    if (entry.isFile) {
      found.files.push(await readFile(entry as FileSystemFileEntry));
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      // Chrome hands out at most 100 entries per call; an empty batch marks the end.
      for (
        let batch = await readEntries(reader);
        batch.length > 0;
        batch = await readEntries(reader)
      ) {
        for (const child of batch) await walk(child, found);
      }
    }
  } catch {
    // Files of this folder read before the failure are kept.
    found.unreadable.push({ name: entry.name, message: en.intake.unreadable });
  }
}

type Source = { entry: FileSystemEntry } | { file: File };

/** Every readable file of a drop, folders included, and the entries that could not be read. */
export async function filesFromDrop(
  transfer: Pick<DataTransfer, "items" | "files">,
): Promise<DroppedFiles> {
  // Everything is taken from the transfer before the first `await`: the browser empties a
  // `DataTransfer` as soon as the drop handler returns.
  const items = Array.from(transfer.items).filter((item) => item.kind === "file");
  const flat = Array.from(transfer.files);
  const hasEntries = items.some(
    (item) => typeof (item as Partial<DataTransferItem>).webkitGetAsEntry === "function",
  );
  // A browser without the entry API only has the flat list.
  if (!hasEntries) return { files: flat, unreadable: [] };

  const sources: Source[] = [];
  for (const item of items) {
    const entry = item.webkitGetAsEntry();
    if (entry) {
      sources.push({ entry });
      continue;
    }
    // Some sources (Photos.app file promises) give no entry but still give the file.
    const file = item.getAsFile();
    if (file) sources.push({ file });
  }

  const found: DroppedFiles = { files: [], unreadable: [] };
  for (const source of sources) {
    if ("file" in source) found.files.push(source.file);
    else await walk(source.entry, found);
  }
  return found;
}

/**
 * Splits a drop into what the server should see and what is said to the user. Hidden files
 * (`.DS_Store`, AppleDouble `._IMG_1.JPG`) are not photos and not worth a mention.
 */
export function partitionFiles(files: readonly File[]): { upload: File[]; skipped: SkippedView[] } {
  const upload: File[] = [];
  const skipped: SkippedView[] = [];
  for (const file of files) {
    if (file.name.startsWith(".")) continue;
    const dot = file.name.lastIndexOf(".");
    const reason = dot < 0 ? undefined : CLIENT_SKIPS[file.name.slice(dot).toLowerCase()];
    const message = reason === undefined ? undefined : SKIP_MESSAGES[reason];
    if (reason === undefined || message === undefined) {
      upload.push(file);
    } else {
      skipped.push({ name: file.name, reason, message });
    }
  }
  return { upload, skipped };
}
