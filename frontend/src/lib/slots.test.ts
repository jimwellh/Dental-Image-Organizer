import { describe, expect, it } from "vitest";
import { moveInMap, slotOf, SLOTS, type SlotMapDraft } from "./slots";

describe("moveInMap", () => {
  it("moving into a full slot swaps", () => {
    const map: SlotMapDraft = { upper_right: "a", upper_left: "b" };
    expect(moveInMap(map, "a", "upper_left")).toEqual({ upper_left: "a", upper_right: "b" });
  });

  it("moving from the tray sends the displaced photo to the tray", () => {
    const map: SlotMapDraft = { upper_right: "a", upper_left: "b" };
    const next = moveInMap(map, "c", "upper_left");
    expect(next).toEqual({ upper_right: "a", upper_left: "c" });
    expect(slotOf(next, "b")).toBeNull();
  });

  it("moving into an empty slot leaves the old one empty", () => {
    expect(moveInMap({ upper_right: "a" }, "a", "lower_left")).toEqual({ lower_left: "a" });
  });

  it("moving to the tray empties the slot", () => {
    expect(moveInMap({ upper_right: "a", upper_left: "b" }, "a", "tray")).toEqual({
      upper_left: "b",
    });
  });

  it("the same slot, or the tray from the tray, changes nothing", () => {
    const map: SlotMapDraft = { upper_right: "a" };
    expect(moveInMap(map, "a", "upper_right")).toEqual(map);
    expect(moveInMap(map, "c", "tray")).toEqual(map);
  });

  it("the input map is not changed", () => {
    const map: SlotMapDraft = { upper_right: "a", upper_left: "b" };
    moveInMap(map, "a", "upper_left");
    moveInMap(map, "c", "upper_left");
    moveInMap(map, "b", "tray");
    expect(map).toEqual({ upper_right: "a", upper_left: "b" });
  });
});

describe("slotOf", () => {
  it("finds the slot of a photo, or null", () => {
    expect(slotOf({ lower_left: "a" }, "a")).toBe("lower_left");
    expect(slotOf({ lower_left: "a" }, "b")).toBeNull();
  });
});

describe("SLOTS", () => {
  it("SLOTS is row-major", () => {
    // As `SLOTS` in core/slots.py: three rows of the 3 x 3 grid.
    expect([...SLOTS]).toEqual([
      "upper_right",
      "upper_occlusal",
      "upper_left",
      "occlusion_right",
      "occlusion_front",
      "occlusion_left",
      "lower_right",
      "lower_occlusal",
      "lower_left",
    ]);
  });
});
