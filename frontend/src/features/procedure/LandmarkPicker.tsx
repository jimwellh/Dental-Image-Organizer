import { useState, type KeyboardEvent, type MouseEvent } from "react";
import { Button } from "../../components/Button";
import { NUDGE, NUDGE_FAST } from "../../components/TransformSurface";
import { fill } from "../../lib/text";
import type { Point } from "../../lib/transform";
import { en } from "../../strings/en";

type Side = "reference" | "photo";

interface LandmarkPickerProps {
  count: 2 | 4;
  referenceUrl: string;
  photoUrl: string;
  /** Height / width of each picture; points are normalized by the width on both axes. */
  referenceAspect: number;
  photoAspect: number;
  /** May throw a `RangeError` for points that fix no alignment; the picker then says why. */
  onApply: (reference: Point[], photo: Point[]) => void;
}

interface Placed {
  reference: Point[];
  photo: Point[];
}

const ARROWS: Record<string, readonly [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
};

const clamp = (value: number, max: number) => Math.min(Math.max(value, 0), max);

function Panel(props: {
  caption: string;
  label: string;
  url: string;
  aspect: number;
  points: readonly Point[];
  onPlace: (point: Point) => void;
  /** The point an arrow key moves, when it lies in this panel. */
  onMove: ((dx: number, dy: number) => boolean) | null;
}) {
  const { caption, label, url, aspect, points, onPlace, onMove } = props;

  const onClick = (event: MouseEvent<HTMLButtonElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    onPlace([(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.width]);
  };

  // Enter and Space are taken here, not left to the click they would raise: a keyboard click
  // carries no position.
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onPlace([0.5, aspect / 2]);
      return;
    }
    const arrow = ARROWS[event.key];
    if (!arrow || !onMove) return;
    const step = event.shiftKey ? NUDGE_FAST : NUDGE;
    if (onMove(arrow[0] * step, arrow[1] * step)) event.preventDefault();
  };

  return (
    <figure className="flex flex-col gap-1">
      <figcaption className="text-sm font-medium text-slate-900">{caption}</figcaption>
      <button
        type="button"
        aria-label={label}
        onClick={onClick}
        onKeyDown={onKeyDown}
        // Firefox raises the click for Space on key-up, after the key-down was handled.
        onKeyUp={(event) => {
          if (event.key === " ") event.preventDefault();
        }}
        className="relative block w-full cursor-crosshair overflow-hidden bg-slate-200"
        style={{ aspectRatio: `1 / ${String(aspect)}` }}
      >
        <img
          src={url}
          alt=""
          draggable={false}
          className="pointer-events-none absolute inset-0 h-full w-full"
        />
        {points.map((point, index) => (
          <span
            key={index}
            aria-hidden="true"
            className="pointer-events-none absolute flex size-6 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border-2 border-white bg-sky-700 text-xs font-semibold text-white shadow"
            style={{
              left: `${String(point[0] * 100)}%`,
              top: `${String((point[1] / aspect) * 100)}%`,
            }}
          >
            {index + 1}
          </span>
        ))}
      </button>
    </figure>
  );
}

/**
 * Landmarks are placed in turn: one on the reference, then the same one on the photo. The picker
 * only collects them; the caller decides what they mean.
 */
export function LandmarkPicker(props: LandmarkPickerProps) {
  const { count, referenceUrl, photoUrl, referenceAspect, photoAspect, onApply } = props;
  const [placed, setPlaced] = useState<Placed>({ reference: [], photo: [] });
  const [degenerate, setDegenerate] = useState(false);

  const total = placed.reference.length + placed.photo.length;
  const complete = placed.reference.length === count && placed.photo.length === count;
  // Pairs are placed reference first, so the lists differ in length only between the two clicks.
  const next: Side = placed.reference.length === placed.photo.length ? "reference" : "photo";
  const last: Side = placed.reference.length > placed.photo.length ? "reference" : "photo";

  const place = (side: Side, point: Point) => {
    if (side !== next || complete) return;
    setDegenerate(false);
    setPlaced((current) => ({ ...current, [side]: [...current[side], point] }));
  };

  const moveLast = (side: Side, aspect: number) => (dx: number, dy: number) => {
    if (side !== last || total === 0) return false;
    setPlaced((current) => {
      const points = current[side];
      const point = points[points.length - 1];
      if (!point) return current;
      const moved: Point = [clamp(point[0] + dx, 1), clamp(point[1] + dy, aspect)];
      return { ...current, [side]: [...points.slice(0, -1), moved] };
    });
    return true;
  };

  const removeLast = () => {
    setDegenerate(false);
    setPlaced((current) => ({ ...current, [last]: current[last].slice(0, -1) }));
  };

  const clear = () => {
    setDegenerate(false);
    setPlaced({ reference: [], photo: [] });
  };

  const apply = () => {
    try {
      onApply(placed.reference, placed.photo);
    } catch (error) {
      if (!(error instanceof RangeError)) throw error;
      setDegenerate(true);
      return;
    }
    // Cleared at once, so a second click cannot send the same alignment twice.
    clear();
  };

  const status = complete
    ? fill(en.compare.picker.ready, { count: String(count) })
    : fill(en.compare.picker.next, {
        n: String(next === "reference" ? placed.reference.length + 1 : placed.reference.length),
        where: next === "reference" ? en.compare.picker.nextReference : en.compare.picker.nextPhoto,
      });

  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Panel
          caption={en.compare.picker.referenceCaption}
          label={en.compare.picker.reference}
          url={referenceUrl}
          aspect={referenceAspect}
          points={placed.reference}
          onPlace={(point) => {
            place("reference", point);
          }}
          onMove={moveLast("reference", referenceAspect)}
        />
        <Panel
          caption={en.compare.picker.photoCaption}
          label={en.compare.picker.photo}
          url={photoUrl}
          aspect={photoAspect}
          points={placed.photo}
          onPlace={(point) => {
            place("photo", point);
          }}
          onMove={moveLast("photo", photoAspect)}
        />
      </div>
      <p role="status" className="text-sm text-slate-800">
        {status} {en.compare.picker.arrows}
      </p>
      {degenerate && (
        <p role="alert" className="text-sm text-red-900">
          {en.compare.degenerate}
        </p>
      )}
      <div className="flex gap-2">
        <Button variant="primary" disabled={!complete} onClick={apply}>
          {en.compare.picker.apply}
        </Button>
        <Button disabled={total === 0} onClick={removeLast}>
          {en.compare.picker.removeLast}
        </Button>
        <Button disabled={total === 0} onClick={clear}>
          {en.compare.picker.clear}
        </Button>
      </div>
    </div>
  );
}
