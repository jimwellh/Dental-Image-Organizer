import { describe, expect, it } from "vitest";
import { id, photoRecord } from "../../test/records";
import { captureOrder, groupByStage } from "./order";

describe("groupByStage", () => {
  it("groups photos by stage in capture order, the reference first", () => {
    const ref = photoRecord({
      id: id(9),
      role: "reference",
      stage: "pre_op",
      captured_at: "2026-10-16T12:00:00",
    });
    const late = photoRecord({ id: id(1), stage: "intra_op", captured_at: "2026-10-16T11:00:00" });
    const early = photoRecord({ id: id(2), stage: "intra_op", captured_at: "2026-10-16T10:00:00" });
    const post = photoRecord({ id: id(3), stage: "post_op" });
    const unstaged = photoRecord({ id: id(4), stage: null });
    const earlierPreOp = photoRecord({
      id: id(5),
      stage: "pre_op",
      captured_at: "2026-10-16T09:00:00",
    });

    const groups = groupByStage([unstaged, post, late, earlierPreOp, early, ref]);

    expect(groups.map((g) => [g.stage, g.photos.map((p) => p.id)])).toEqual([
      ["pre_op", [ref.id, earlierPreOp.id]],
      ["intra_op", [early.id, late.id]],
      ["post_op", [post.id]],
      [null, [unstaged.id]],
    ]);
  });

  it("leaves out empty groups", () => {
    expect(groupByStage([photoRecord({ stage: "post_op" })]).map((g) => g.stage)).toEqual([
      "post_op",
    ]);
  });
});

describe("captureOrder", () => {
  it("sorts photos without a capture time last", () => {
    const dated = photoRecord({ id: id(2), captured_at: "2026-10-16T10:00:00" });
    const undated = photoRecord({ id: id(1), captured_at: null });
    expect([undated, dated].sort(captureOrder).map((p) => p.id)).toEqual([dated.id, undated.id]);
  });

  it("does not let the offset change the order", () => {
    // By instant this is 04:00Z, before the other; by wall clock it is 12:00, after it.
    const withOffset = photoRecord({ id: id(2), captured_at: "2026-10-16T12:00:00+08:00" });
    const plain = photoRecord({ id: id(1), captured_at: "2026-10-16T11:00:00" });
    expect([withOffset, plain].sort(captureOrder).map((p) => p.id)).toEqual([
      plain.id,
      withOffset.id,
    ]);
  });

  it("breaks ties by id", () => {
    const a = photoRecord({ id: id(1) });
    const b = photoRecord({ id: id(2) });
    expect([b, a].sort(captureOrder).map((p) => p.id)).toEqual([a.id, b.id]);
  });
});
