import type { Flip, Slot } from "../api/types";

/** The 3 x 3 grid in row-major order, as `SLOTS` in `core/slots.py`. */
export const SLOTS = [
  "upper_right",
  "upper_occlusal",
  "upper_left",
  "occlusion_right",
  "occlusion_front",
  "occlusion_left",
  "lower_right",
  "lower_occlusal",
  "lower_left",
] as const satisfies readonly Slot[];

// `satisfies` catches a name that is not a slot; this catches a slot the list forgot, which
// would leave a cell out of the board without any error.
type MustBeNever<T extends never> = T;
export type SlotsAreComplete = MustBeNever<Exclude<Slot, (typeof SLOTS)[number]>>;

export const FLIPS = [
  "none",
  "horizontal",
  "vertical",
  "rotate_180",
] as const satisfies readonly Flip[];

/** Where each photo id sits; an id missing from the values is in the tray. */
export type SlotMapDraft = Partial<Record<Slot, string>>;

export function slotOf(map: SlotMapDraft, photoId: string): Slot | null {
  return SLOTS.find((slot) => map[slot] === photoId) ?? null;
}

/**
 * The server's `move_photo` (services/composite_case.py), so a screen can show the outcome of a
 * drop before the server has answered: the photo already in `target` takes the mover's old slot,
 * or the tray when the mover came from the tray.
 */
export function moveInMap(map: SlotMapDraft, photoId: string, target: Slot | "tray"): SlotMapDraft {
  const from = slotOf(map, photoId);
  if (from === target || (from === null && target === "tray")) return { ...map };

  // Rebuilt without the mover instead of deleting its key, so the input is never touched.
  const next: SlotMapDraft = {};
  for (const slot of SLOTS) {
    const occupant = map[slot];
    if (occupant !== undefined && occupant !== photoId) next[slot] = occupant;
  }
  if (target === "tray") return next;

  const displaced = map[target];
  next[target] = photoId;
  if (displaced !== undefined && from !== null) next[from] = displaced;
  return next;
}
