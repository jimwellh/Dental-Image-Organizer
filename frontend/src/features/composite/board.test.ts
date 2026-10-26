import { describe, expect, it } from "vitest";
import { compositeView, id, memberRecord } from "../../test/records";
import { cellsOf, moveRequest, trayOf } from "./board";

const placed = memberRecord({ id: id(1), slot: "upper_left" });
const loose = memberRecord({ id: id(2), slot: null });

describe("moveRequest", () => {
  it("nothing to send for the same slot or an unready photo", () => {
    const pending = memberRecord({ id: id(3), state: "pending", edit: null });
    const view = compositeView([placed, loose, pending]);
    expect(moveRequest(view, placed.id, "upper_left")).toBeNull();
    expect(moveRequest(view, loose.id, "tray")).toBeNull();
    expect(moveRequest(view, pending.id, "upper_right")).toBeNull();
    expect(moveRequest(view, id(99), "upper_right")).toBeNull();
  });

  it("asks for the target otherwise", () => {
    const view = compositeView([placed, loose]);
    expect(moveRequest(view, loose.id, "upper_right")).toEqual({ slot: "upper_right" });
    expect(moveRequest(view, placed.id, "tray")).toEqual({ slot: "tray" });
  });
});

describe("cellsOf and trayOf", () => {
  it("puts each placed photo in its cell and leaves the others null", () => {
    const cells = cellsOf(compositeView([placed, loose]));
    expect(cells.upper_left).toBe(placed);
    expect(Object.values(cells).filter((cell) => cell !== null)).toHaveLength(1);
    expect(Object.keys(cells)).toHaveLength(9);
  });

  it("the tray is in capture order", () => {
    const late = memberRecord({ id: id(4), captured_at: "2026-10-16T12:00:00" });
    const early = memberRecord({ id: id(5), captured_at: "2026-10-16T09:00:00" });
    const failed = memberRecord({
      id: id(6),
      state: "failed",
      edit: null,
      captured_at: "2026-10-16T10:00:00",
    });
    const pending = memberRecord({ id: id(7), state: "pending", edit: null, captured_at: null });
    const view = compositeView([late, placed, pending, failed, early]);
    expect(trayOf(view).map((photo) => photo.id)).toEqual([
      early.id,
      failed.id,
      late.id,
      pending.id,
    ]);
  });
});
