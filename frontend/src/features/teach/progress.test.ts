import { describe, expect, it } from "vitest";
import { SLOTS } from "../../lib/slots";
import { classifierView } from "../../test/records";
import { teachProgress } from "./progress";

const counts = (n: number, except: Partial<Record<(typeof SLOTS)[number], number>> = {}) =>
  Object.fromEntries(SLOTS.map((slot) => [slot, except[slot] ?? n]));

describe("teachProgress", () => {
  it("set n of 3 follows the smallest slot count", () => {
    const of = 3;
    const set = (c: Record<string, number>) =>
      teachProgress(classifierView({ counts: c, min_samples_per_slot: of })).set;

    expect(set(counts(0))).toBe(1);
    expect(set(counts(1, { lower_left: 0 }))).toBe(1);
    expect(set(counts(1))).toBe(2);
    expect(set(counts(2))).toBe(3);
    expect(set(counts(5))).toBe(3);
  });

  it("counts a slot with no entry as zero", () => {
    expect(teachProgress(classifierView({ counts: { upper_right: 4 } })).set).toBe(1);
    expect(teachProgress(classifierView({ counts: {} }))).toEqual({
      set: 1,
      of: 3,
      trained: false,
    });
  });

  it("reports whether the classifier is trained", () => {
    expect(teachProgress(classifierView({ counts: counts(3), trained: true })).trained).toBe(true);
  });
});
