import type { PhotoRecord, Stage } from "../../api/types";

// The wall-clock part only, as `procedure_case.capture_order` does: camera clocks carry no
// reliable offset, and mixing aware and naive times would order photos by the zone, not the shot.
const wallClock = (photo: PhotoRecord): string | null => photo.captured_at?.slice(0, 19) ?? null;

export function captureOrder(a: PhotoRecord, b: PhotoRecord): number {
  const timeA = wallClock(a);
  const timeB = wallClock(b);
  if (timeA !== timeB) {
    // A photo without a capture time sorts last.
    if (timeA === null) return 1;
    if (timeB === null) return -1;
    return timeA < timeB ? -1 : 1;
  }
  if (a.id === b.id) return 0;
  return a.id < b.id ? -1 : 1;
}

const STAGES: readonly (Stage | null)[] = ["pre_op", "intra_op", "post_op", null];

const referenceFirst = (a: PhotoRecord, b: PhotoRecord): number =>
  Number(b.role === "reference") - Number(a.role === "reference") || captureOrder(a, b);

export function groupByStage(
  photos: readonly PhotoRecord[],
): { stage: Stage | null; photos: PhotoRecord[] }[] {
  return STAGES.map((stage) => ({
    stage,
    photos: photos.filter((photo) => (photo.stage ?? null) === stage).sort(referenceFirst),
  })).filter((group) => group.photos.length > 0);
}
