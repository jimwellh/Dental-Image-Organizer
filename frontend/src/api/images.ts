import { apiPath } from "./http";
import type { CaseSummary, CaseView, ImageSize, PhotoRecord, RenderSize } from "./types";

/**
 * A short fingerprint for the `v` query value. The server sends images with long-lived cache
 * headers, so a URL must change whenever the picture behind it does; this is not a security hash.
 * 32-bit FNV-1a over the UTF-8 bytes of the JSON.
 */
export function versionOf(value: unknown): string {
  let hash = 0x811c9dc5;
  for (const byte of new TextEncoder().encode(JSON.stringify(value))) {
    hash = Math.imul(hash ^ byte, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

export function photoImageUrl(
  caseId: string,
  photo: PhotoRecord,
  variant: "original" | "processed",
  size: ImageSize,
): string {
  // An original never changes, so its hash is enough; a processed image follows the alignment, and
  // the state covers the step from pending to ready.
  const v =
    variant === "original"
      ? photo.sha256.slice(0, 16)
      : versionOf([photo.state, photo.edit ?? null]);
  return apiPath(
    "/api/cases/{case_id}/photos/{photo_id}/image",
    { case_id: caseId, photo_id: photo.id },
    { variant, size, v },
  );
}

/** A case card's thumbnail; the summary has no edits, so any write of the case is the version. */
export function caseThumbUrl(summary: CaseSummary, photoId: string): string {
  return apiPath(
    "/api/cases/{case_id}/photos/{photo_id}/image",
    { case_id: summary.id, photo_id: photoId },
    { variant: "processed", size: "thumb", v: versionOf(summary.updated_at) },
  );
}

// Every write of the case changes `updated_at`, and a render depends on the whole case.
export function sequenceUrl(view: CaseView, size: RenderSize): string {
  return apiPath(
    "/api/cases/{case_id}/render/sequence",
    { case_id: view.case.id },
    { size, v: versionOf(view.case.updated_at) },
  );
}

export function compositeUrl(view: CaseView, size: RenderSize): string {
  return apiPath(
    "/api/cases/{case_id}/render/composite",
    { case_id: view.case.id },
    { size, v: versionOf(view.case.updated_at) },
  );
}

export function teachImageUrl(
  sessionId: string,
  photoId: string,
  size: "thumb" | "preview",
): string {
  // The photos of a teaching session never change, so there is nothing to version.
  return apiPath(
    "/api/classifier/teach/{session_id}/photos/{photo_id}/image",
    { session_id: sessionId, photo_id: photoId },
    { size },
  );
}
