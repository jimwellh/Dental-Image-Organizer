import { useState } from "react";
import type { Slot } from "../api/types";
import { SLOTS } from "../lib/slots";
import { fill } from "../lib/text";
import { en } from "../strings/en";
import { Button } from "./Button";

interface ShootingOrderPickerProps {
  value: readonly Slot[];
  onChange: (order: Slot[]) => void;
  disabled?: boolean;
}

/**
 * The 3 x 3 grid with the position of each view in the shooting order. A new order is built by
 * clicking the views one after another, so the nine clicks are the whole input and no half-edited
 * order is ever saved.
 */
export function ShootingOrderPicker({
  value,
  onChange,
  disabled = false,
}: ShootingOrderPickerProps) {
  // `null` is the resting state; a list is the views clicked so far.
  const [picked, setPicked] = useState<Slot[] | null>(null);
  const shown = picked ?? value;

  const pick = (slot: Slot) => {
    if (picked === null || picked.includes(slot)) return;
    const next = [...picked, slot];
    if (next.length < SLOTS.length) {
      setPicked(next);
      return;
    }
    setPicked(null);
    onChange(next);
  };

  return (
    <div className="flex flex-col gap-3">
      <div
        role="group"
        aria-label={en.teach.order.grid}
        className="grid max-w-md grid-cols-3 gap-2"
      >
        {SLOTS.map((slot) => {
          const position = shown.indexOf(slot) + 1;
          const name = en.slots[slot];
          return (
            <button
              key={slot}
              type="button"
              // Resting cells are not controls; they only show the order.
              disabled={disabled || picked === null}
              aria-label={
                position > 0 ? fill(en.teach.order.position, { name, n: String(position) }) : name
              }
              onClick={() => {
                pick(slot);
              }}
              className={`flex min-h-16 flex-col items-center justify-center rounded-lg border p-1 text-xs text-slate-900 ${
                picked !== null && !picked.includes(slot)
                  ? "border-sky-600 bg-white hover:bg-sky-50"
                  : "border-slate-300 bg-slate-50"
              }`}
            >
              <span>{name}</span>
              {position > 0 && (
                <span aria-hidden="true" className="text-base font-semibold tabular-nums">
                  {position}
                </span>
              )}
            </button>
          );
        })}
      </div>
      {picked === null ? (
        <div>
          <Button
            disabled={disabled}
            onClick={() => {
              setPicked([]);
            }}
          >
            {en.teach.order.change}
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-3 text-sm text-slate-900">
          <p>{en.teach.order.instruction}</p>
          <span role="status" className="tabular-nums">
            {fill(en.teach.order.chosen, { n: String(picked.length) })}
          </span>
          <Button
            onClick={() => {
              setPicked(null);
            }}
          >
            {en.common.cancel}
          </Button>
        </div>
      )}
    </div>
  );
}
