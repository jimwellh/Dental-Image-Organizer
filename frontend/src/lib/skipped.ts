import type { SkippedView, UploadSkipReason } from "../api/types";
import { en } from "../strings/en";

// The order the summary lists them in (§3.1): what the user chose not to import first.
const ORDER: readonly UploadSkipReason[] = [
  "video",
  "sidecar",
  "raw",
  "unknown",
  "duplicate",
  "too_large",
];

const phrase = (forms: { one: string; other: string }, n: number): string =>
  (n === 1 ? forms.one : forms.other).replace("{n}", String(n));

/** The lines of the one notice a drop gets; empty when nothing was skipped. */
export function skipSummary(skipped: readonly SkippedView[]): string[] {
  if (skipped.length === 0) return [];
  const parts = ORDER.flatMap((reason) => {
    const count = skipped.filter((entry) => entry.reason === reason).length;
    return count > 0 ? [phrase(en.skip.reasons[reason], count)] : [];
  });
  const lines = [`${phrase(en.skip.summary, skipped.length)} ${parts.join(", ")}`];
  if (skipped.some((entry) => entry.reason === "raw")) lines.push(en.skip.rawAdvice);
  return lines;
}
