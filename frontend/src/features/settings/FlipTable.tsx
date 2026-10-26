import type { ClassifierView, Flip, Settings, Slot } from "../../api/types";
import { FLIPS, SLOTS, isFlip } from "../../lib/slots";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";

type FlipSettings = Pick<Settings, "flip_start" | "flip_pins">;

interface FlipTableProps {
  settings: FlipSettings;
  status: ClassifierView["flips"];
  onChange: (next: FlipSettings) => void;
}

const selectClass = "rounded-md border border-slate-400 bg-white px-2 py-1 text-sm text-slate-900";

function FlipSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: Flip;
  onChange: (flip: Flip) => void;
}) {
  return (
    <select
      aria-label={label}
      value={value}
      className={selectClass}
      onChange={(event) => {
        if (isFlip(event.target.value)) onChange(event.target.value);
      }}
    >
      {FLIPS.map((flip) => (
        <option key={flip} value={flip}>
          {en.flips[flip]}
        </option>
      ))}
    </select>
  );
}

/**
 * One row per view. The "in effect" column is worked out here from the unsaved draft, not read
 * from the server, so it follows an edit at once: pin, then learned, then start (`services/flips.py`).
 */
export function FlipTable({ settings, status, onChange }: FlipTableProps) {
  // The generator marks a field with a server default as optional; a read always carries both.
  const starts = settings.flip_start ?? {};
  const pins = settings.flip_pins ?? {};
  const setStart = (slot: Slot, flip: Flip) => {
    onChange({ ...settings, flip_start: { ...starts, [slot]: flip } });
  };

  const setPin = (slot: Slot, flip: Flip | null) => {
    // Rebuilt without the key instead of deleting it, so the draft is never mutated.
    const kept = Object.fromEntries(Object.entries(pins).filter(([key]) => key !== slot));
    onChange({ ...settings, flip_pins: flip === null ? kept : { ...kept, [slot]: flip } });
  };

  return (
    <table className="w-full max-w-3xl border-collapse text-left text-sm text-slate-900">
      <thead>
        <tr className="border-b border-slate-300">
          <th scope="col" className="py-1 pr-3 font-medium">
            {en.settings.flips.slot}
          </th>
          <th scope="col" className="py-1 pr-3 font-medium">
            {en.settings.flips.start}
          </th>
          <th scope="col" className="py-1 pr-3 font-medium">
            {en.settings.flips.learned}
          </th>
          <th scope="col" className="py-1 pr-3 font-medium">
            {en.settings.flips.effective}
          </th>
          <th scope="col" className="py-1 font-medium">
            {en.settings.flips.pinHeading}
          </th>
        </tr>
      </thead>
      <tbody>
        {SLOTS.map((slot) => {
          const name = en.slots[slot];
          const start = starts[slot] ?? "none";
          const learned = status[slot]?.learned ?? null;
          const pinned = pins[slot] ?? null;
          const effective = pinned ?? learned ?? start;
          return (
            <tr key={slot} className="border-b border-slate-200">
              <th scope="row" className="py-1 pr-3 font-normal">
                {name}
              </th>
              <td className="py-1 pr-3">
                <FlipSelect
                  label={fill(en.settings.flips.startLabel, { name })}
                  value={start}
                  onChange={(flip) => {
                    setStart(slot, flip);
                  }}
                />
              </td>
              <td className="py-1 pr-3">
                {learned === null
                  ? en.settings.flips.none
                  : fill(en.settings.flips.learnedValue, {
                      flip: en.flips[learned],
                      n: String(status[slot]?.confirmations ?? 0),
                    })}
              </td>
              <td className="py-1 pr-3">{en.flips[effective]}</td>
              <td className="py-1">
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    aria-label={fill(en.settings.flips.pin, { name })}
                    checked={pinned !== null}
                    onChange={(event) => {
                      // Pinning freezes what is in effect now, so nothing changes until the user does.
                      setPin(slot, event.target.checked ? effective : null);
                    }}
                  />
                  {pinned !== null && (
                    <FlipSelect
                      label={fill(en.settings.flips.pinnedLabel, { name })}
                      value={pinned}
                      onChange={(flip) => {
                        setPin(slot, flip);
                      }}
                    />
                  )}
                </div>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
