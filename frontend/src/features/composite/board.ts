import type { CaseView, PhotoPatch, PhotoRecord, Slot } from "../../api/types";
import { SLOTS } from "../../lib/slots";
import { captureOrder } from "../procedure/order";

export function cellsOf(view: CaseView): Record<Slot, PhotoRecord | null> {
  const cells = Object.fromEntries(SLOTS.map((slot) => [slot, null])) as Record<
    Slot,
    PhotoRecord | null
  >;
  for (const photo of view.case.photos) {
    if (photo.role === "member" && photo.slot) cells[photo.slot] = photo;
  }
  return cells;
}

/** Pending and failed photos have no slot yet, so they wait here too. */
export function trayOf(view: CaseView): PhotoRecord[] {
  return view.case.photos
    .filter((photo) => photo.role === "member" && !photo.slot)
    .sort(captureOrder);
}

/** `null` when there is nothing to send: the server refuses an unready photo, and a drop on the
 * photo's own place changes nothing. */
export function moveRequest(
  view: CaseView,
  photoId: string,
  target: Slot | "tray",
): PhotoPatch | null {
  const photo = view.case.photos.find((other) => other.id === photoId);
  if (photo?.state !== "ready") return null;
  if ((photo.slot ?? "tray") === target) return null;
  return { slot: target };
}
