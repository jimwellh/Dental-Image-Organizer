import type { PatientView } from "../api/types";

/**
 * The form two spellings of the same text share. macOS can hand over decomposed Unicode (NFD) from
 * the file system or the keyboard, and a stored name may be composed (NFC); both must find each
 * other. Spacing and case never make two names differ for a person looking one up.
 */
export function foldForSearch(text: string): string {
  return text.normalize("NFC").trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

/** The folded query in the folded name or note; an empty query matches every patient. */
export function matchesPatient(patient: PatientView, query: string): boolean {
  const needle = foldForSearch(query);
  if (needle === "") return true;
  return (
    foldForSearch(patient.name).includes(needle) ||
    foldForSearch(patient.note ?? "").includes(needle)
  );
}
