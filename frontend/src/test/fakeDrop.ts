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
  const items = entries.map((entry) => ({ kind: "file", webkitGetAsEntry: () => entry }));
  return { items, files } as unknown as Pick<DataTransfer, "items" | "files">;
}

/** A transfer from a browser that gives no entries, only the flat file list. */
export function fakeFlatTransfer(files: File[]): Pick<DataTransfer, "items" | "files"> {
  const items = files.map(() => ({ kind: "file", webkitGetAsEntry: () => null }));
  return { items, files } as unknown as Pick<DataTransfer, "items" | "files">;
}
