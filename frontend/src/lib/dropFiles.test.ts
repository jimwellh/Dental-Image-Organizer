import { describe, expect, it } from "vitest";
import {
  directoryEntry,
  fakeFile,
  fakeFlatTransfer,
  fakeTransfer,
  fileEntry,
} from "../test/fakeDrop";
import { en } from "../strings/en";
import { filesFromDrop, partitionFiles } from "./dropFiles";

describe("filesFromDrop", () => {
  it("walks folders and reads every batch", async () => {
    const first = Array.from({ length: 100 }, (_, i) => fileEntry(fakeFile(`a${String(i)}.HEIC`)));
    const second = Array.from({ length: 3 }, (_, i) => fileEntry(fakeFile(`b${String(i)}.HEIC`)));
    const nested = directoryEntry("nested", [[fileEntry(fakeFile("deep.JPG"))]]);
    const folder = directoryEntry("Finder folder", [first, [...second, nested]]);

    const files = await filesFromDrop(fakeTransfer([folder]));
    const names = files.map((f) => f.name);

    expect(files).toHaveLength(104);
    expect(names).toContain("a99.HEIC");
    expect(names).toContain("b2.HEIC");
    expect(names).toContain("deep.JPG");
  });

  it("asks every item for its entry before the first await", async () => {
    const asked: number[] = [];
    const items = [0, 1, 2].map((n) => ({
      kind: "file",
      webkitGetAsEntry: () => {
        asked.push(n);
        return fileEntry(fakeFile(`p${String(n)}.HEIC`));
      },
    }));
    const transfer = { items, files: [] } as unknown as Pick<DataTransfer, "items" | "files">;

    const pending = filesFromDrop(transfer);
    // No microtask has run yet: the browser empties the transfer once the handler returns.
    expect(asked).toEqual([0, 1, 2]);
    expect(await pending).toHaveLength(3);
  });

  it("falls back to the file list without entries", async () => {
    const files = [fakeFile("one.HEIC"), fakeFile("two.JPG")];

    const result = await filesFromDrop(fakeFlatTransfer(files));

    expect(result.map((f) => f.name)).toEqual(["one.HEIC", "two.JPG"]);
  });

  it("rejects when a folder cannot be read", async () => {
    const broken = {
      isFile: false,
      isDirectory: true,
      name: "broken",
      createReader: () => ({
        readEntries: (_ok: unknown, fail: (error: Error) => void) => {
          fail(new Error("unreadable"));
        },
      }),
    } as unknown as FileSystemDirectoryEntry;

    await expect(filesFromDrop(fakeTransfer([broken]))).rejects.toThrow("unreadable");
  });
});

describe("partitionFiles", () => {
  it("drops hidden files silently", () => {
    const { upload, skipped } = partitionFiles([
      fakeFile(".DS_Store"),
      fakeFile("._IMG_1.JPG"),
      fakeFile("IMG_1.JPG"),
    ]);

    expect(upload.map((f) => f.name)).toEqual(["IMG_1.JPG"]);
    expect(skipped).toEqual([]);
  });

  it("filters videos and sidecars before upload", () => {
    const { upload, skipped } = partitionFiles([
      fakeFile("IMG_1.MOV"),
      fakeFile("IMG_1.aae"),
      fakeFile("IMG_1.HEIC"),
      fakeFile("clip.Mp4"),
    ]);

    expect(upload.map((f) => f.name)).toEqual(["IMG_1.HEIC"]);
    expect(skipped).toEqual([
      { name: "IMG_1.MOV", reason: "video", message: en.skip.videoMessage },
      { name: "IMG_1.aae", reason: "sidecar", message: en.skip.sidecarMessage },
      { name: "clip.Mp4", reason: "video", message: en.skip.videoMessage },
    ]);
  });

  it("keeps a file without an extension for the server to judge", () => {
    expect(partitionFiles([fakeFile("README")]).upload).toHaveLength(1);
  });
});
