// jsdom has no `DataTransfer` or `FileSystemEntry`; these build the parts `filesFromDrop` reads.

export function fakeFile(name: string, type = "image/heic"): File {
  return new File(["x"], name, { type });
}

export function fileEntry(file: File): FileSystemFileEntry {
  return {
    isFile: true,
    isDirectory: false,
    name: file.name,
    file: (success: FileCallback) => {
      success(file);
    },
  } as unknown as FileSystemFileEntry;
}

/** A directory whose reader hands out `batches` one `readEntries` call at a time, then `[]`. */
export function directoryEntry(
  name: string,
  batches: FileSystemEntry[][],
): FileSystemDirectoryEntry {
  return {
    isFile: false,
    isDirectory: true,
    name,
    createReader: () => {
      let next = 0;
      return {
        readEntries: (success: FileSystemEntriesCallback) => {
          success(batches[next++] ?? []);
        },
      };
    },
  } as unknown as FileSystemDirectoryEntry;
}

export function fakeTransfer(
  entries: FileSystemEntry[],
  files: File[] = [],
): Pick<DataTransfer, "items" | "files"> {
  const items = entries.map((entry) => ({
    kind: "file",
    webkitGetAsEntry: () => entry,
    getAsFile: () => null,
  }));
  return { items, files } as unknown as Pick<DataTransfer, "items" | "files">;
}

/** A transfer from a browser without the entry API: only the flat file list is usable. */
export function fakeFlatTransfer(files: File[]): Pick<DataTransfer, "items" | "files"> {
  const items = files.map(() => ({ kind: "file" }));
  return { items, files } as unknown as Pick<DataTransfer, "items" | "files">;
}

/** The `clipboardData` of a paste: one file item per file, plus an optional text item. */
export function fakeClipboard(
  files: File[],
  { text }: { text?: string } = {},
): Pick<DataTransfer, "items" | "files"> {
  const items = [
    ...files.map((file) => ({ kind: "file", type: file.type, getAsFile: () => file })),
    ...(text === undefined ? [] : [{ kind: "string", type: "text/plain", getAsFile: () => null }]),
  ];
  return { items, files } as unknown as Pick<DataTransfer, "items" | "files">;
}
