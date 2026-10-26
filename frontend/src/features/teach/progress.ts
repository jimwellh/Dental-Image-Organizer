import type { ClassifierView } from "../../api/types";
import { SLOTS } from "../../lib/slots";

export interface TeachProgress {
  /** The set the next confirmation completes, from 1 to `of`. */
  set: number;
  /** Samples a view needs before the classifier trains (`min_samples_per_slot`). */
  of: number;
  trained: boolean;
}

/**
 * "Set n of 3" counts the complete sets taught so far, which is the smallest count of any view:
 * a set that left a view out has not completed that view's sample.
 */
export function teachProgress(status: ClassifierView): TeachProgress {
  const of = status.min_samples_per_slot;
  const smallest = Math.min(...SLOTS.map((slot) => status.counts[slot] ?? 0));
  return { set: Math.min(of, smallest + 1), of, trained: status.trained };
}
