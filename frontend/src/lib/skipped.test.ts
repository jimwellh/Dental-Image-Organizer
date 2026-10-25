import { describe, expect, it } from "vitest";
import type { SkippedView } from "../api/types";
import { skipSummary } from "./skipped";

const skip = (reason: SkippedView["reason"], count: number): SkippedView[] =>
  Array.from({ length: count }, (_, i) => ({ name: `f${String(i)}`, reason, message: "m" }));

describe("skipSummary", () => {
  it("summarizes skips by reason", () => {
    expect(skipSummary([...skip("sidecar", 3), ...skip("video", 9)])).toEqual([
      "Skipped 12 files: 9 videos, 3 edit sidecars",
    ]);
  });

  it("names RAW files explicitly", () => {
    expect(skipSummary(skip("raw", 1))).toEqual([
      "Skipped 1 file: 1 RAW file",
      "DNG/ProRAW is not supported; export as HEIC or JPEG.",
    ]);
  });

  it("orders every reason and uses singular and plural nouns", () => {
    const all = [
      ...skip("too_large", 1),
      ...skip("duplicate", 2),
      ...skip("unknown", 1),
      ...skip("raw", 2),
      ...skip("sidecar", 1),
      ...skip("video", 1),
    ];
    expect(skipSummary(all)[0]).toBe(
      "Skipped 8 files: 1 video, 1 edit sidecar, 2 RAW files, 1 other file, 2 already in the case, 1 over 200 MB",
    );
  });

  it("nothing skipped, nothing said", () => {
    expect(skipSummary([])).toEqual([]);
  });
});
