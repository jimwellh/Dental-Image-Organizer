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

async function walk(entry: FileSystemEntry, into: File[]): Promise<void> {
  if (entry.isFile) {
    into.push(await readFile(entry as FileSystemFileEntry));
    return;
  }
  if (!entry.isDirectory) return;
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  // Chrome hands out at most 100 entries per call; an empty batch marks the end.
  for (let batch = await readEntries(reader); batch.length > 0; batch = await readEntries(reader)) {
    for (const child of batch) await walk(child, into);
  }
}

/**
 * Every file of a drop, folders included. A failure to read any entry rejects: the caller tells
 * the user, rather than uploading a folder that is quietly incomplete.
 */
export async function filesFromDrop(
  transfer: Pick<DataTransfer, "items" | "files">,
): Promise<File[]> {
  // Both are read before the first `await`: the browser empties a `DataTransfer` as soon as the
  // drop handler returns.
  const entries: FileSystemEntry[] = [];
  for (const item of Array.from(transfer.items)) {
    if (item.kind !== "file") continue;
    const entry = item.webkitGetAsEntry();
    if (entry) entries.push(entry);
  }
  const flat = Array.from(transfer.files);

  if (entries.length === 0) return flat;
  const files: File[] = [];
  for (const entry of entries) await walk(entry, files);
  return files;
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
