import { describe, expect, it } from "vitest";
import { ApiError } from "../api/http";
import type { UploadResult } from "../api/types";
import { fakeFile } from "../test/fakeDrop";
import { uploadAll, type UploadProgress } from "./uploadQueue";

interface Pending {
  file: File;
  resolve: (result: UploadResult) => void;
}

const tick = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

const ok = (name: string): UploadResult => ({ job_id: `job-${name}`, added: [name], skipped: [] });

describe("uploadAll", () => {
  it("never sends more than three at once", async () => {
    const files = Array.from({ length: 7 }, (_, i) => fakeFile(`p${String(i)}.HEIC`));
    const open: Pending[] = [];
    const sent: string[] = [];
    let inFlight = 0;
    let peak = 0;
    const send = (file: File) =>
      new Promise<UploadResult>((resolve) => {
        sent.push(file.name);
        inFlight++;
        peak = Math.max(peak, inFlight);
        open.push({
          file,
          resolve: (result) => {
            inFlight--;
            resolve(result);
          },
        });
      });

    const done = uploadAll(send, files);
    await tick();
    expect(open).toHaveLength(3);

    while (open.length > 0) {
      const next = open.shift();
      next?.resolve(ok(next.file.name));
      await tick();
    }
    const summary = await done;

    expect(peak).toBe(3);
    expect(sent).toHaveLength(7);
    expect(summary.added).toHaveLength(7);
    expect(summary.jobIds).toHaveLength(7);
  });

  it("one failed upload does not stop the rest", async () => {
    const files = Array.from({ length: 7 }, (_, i) => fakeFile(`p${String(i)}.HEIC`));
    const send = (file: File): Promise<UploadResult> =>
      file.name === "p1.HEIC"
        ? Promise.reject(new ApiError(415, "unsupported", "Not a photo."))
        : Promise.resolve(ok(file.name));

    const summary = await uploadAll(send, files);

    expect(summary.failed).toEqual([{ name: "p1.HEIC", message: "Not a photo." }]);
    expect(summary.added).toHaveLength(6);
  });

  it("collects server skips and reports progress", async () => {
    const skipped = { name: "b.HEIC", reason: "duplicate", message: "dup" } as const;
    const progress: UploadProgress[] = [];
    const send = (file: File): Promise<UploadResult> =>
      Promise.resolve(
        file.name === "b.HEIC" ? { job_id: null, added: [], skipped: [skipped] } : ok(file.name),
      );

    const summary = await uploadAll(send, [fakeFile("a.HEIC"), fakeFile("b.HEIC")], {
      concurrency: 1,
      onProgress: (p) => progress.push(p),
    });

    expect(summary.skipped).toEqual([skipped]);
    expect(summary.jobIds).toEqual(["job-a.HEIC"]);
    expect(progress.at(-1)).toEqual({ done: 2, total: 2 });
  });

  it("does nothing for no files", async () => {
    const summary = await uploadAll(() => Promise.reject(new Error("unused")), []);
    expect(summary).toEqual({ added: [], skipped: [], jobIds: [], failed: [] });
  });
});
