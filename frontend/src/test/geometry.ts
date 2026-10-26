import { vi } from "vitest";

/**
 * jsdom lays nothing out, so every element is 0 x 0 and a surface would never measure a width.
 * Every element answers with this box until `vi.restoreAllMocks()`; both axes are normalized by
 * the width, so a test turns pixels into transform units by dividing by 500.
 */
export function stubBoundingRect(width = 500, height = 375): void {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0,
    top: 0,
    right: width,
    bottom: height,
    x: 0,
    y: 0,
    width,
    height,
    toJSON: () => ({}),
  });
}
