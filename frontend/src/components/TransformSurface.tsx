import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from "react";
import {
  cssMatrix3d,
  rotateAbout,
  scaleAbout,
  translateBy,
  type Mat3,
  type Point,
} from "../lib/transform";

export const WHEEL_ZOOM_RATE = 0.002;
export const NUDGE = 0.002;
export const NUDGE_FAST = 0.02;
export const ROTATE_STEP_DEG = 0.5;
export const KEY_ZOOM = 1.02;
export const COMMIT_DELAY_MS = 400;

// Firefox reports wheel deltas in lines; one line is about this many pixels.
const LINE_PX = 16;

export interface TransformSurfaceProps {
  /** The photo's original preview. */
  imageUrl: string;
  /** Output height / output width. */
  outAspect: number;
  /** Photo to output, normalized by width on both axes (spec §4.8). */
  matrix: Mat3;
  opacity?: number;
  /** The accessible name; the surface is focusable. */
  label: string;
  /** Drawn beneath the photo: the reference, or the cell. */
  children?: ReactNode;
  /** Live, on every step of a gesture. */
  onChange: (matrix: Mat3) => void;
  /** Once per gesture. */
  onCommit: (matrix: Mat3) => void;
}

interface Drag {
  pointerId: number;
  x: number;
  y: number;
}

const KEY_STEPS: Record<string, (m: Mat3, center: Point, fast: boolean) => Mat3> = {
  ArrowLeft: (m, _center, fast) => translateBy(m, -(fast ? NUDGE_FAST : NUDGE), 0),
  ArrowRight: (m, _center, fast) => translateBy(m, fast ? NUDGE_FAST : NUDGE, 0),
  ArrowUp: (m, _center, fast) => translateBy(m, 0, -(fast ? NUDGE_FAST : NUDGE)),
  ArrowDown: (m, _center, fast) => translateBy(m, 0, fast ? NUDGE_FAST : NUDGE),
  "[": (m, center) => rotateAbout(m, -ROTATE_STEP_DEG, center),
  "]": (m, center) => rotateAbout(m, ROTATE_STEP_DEG, center),
  "+": (m, center) => scaleAbout(m, KEY_ZOOM, center),
  "=": (m, center) => scaleAbout(m, KEY_ZOOM, center),
  "-": (m, center) => scaleAbout(m, 1 / KEY_ZOOM, center),
};

/**
 * The photo over a base, moved by pointer, wheel, pinch, or keys, and shown live as a CSS
 * `matrix3d`. The parent keeps the matrix; this reports every step and, once per gesture, a commit.
 */
export function TransformSurface(props: TransformSurfaceProps) {
  const { imageUrl, outAspect, matrix, opacity = 1, label, children } = props;
  const [width, setWidth] = useState(0);

  const latest = useRef({ onChange: props.onChange, onCommit: props.onCommit });
  useEffect(() => {
    latest.current = { onChange: props.onChange, onCommit: props.onCommit };
  });

  // The matrix a step builds on. A wheel burst can deliver several events before the parent
  // renders, so a step must not wait for the `matrix` prop to come back.
  const current = useRef(matrix);
  const dirty = useRef(false);
  const timer = useRef<number | null>(null);
  useEffect(() => {
    // A reply that arrives mid-gesture must not undo the steps the user already made.
    if (!dirty.current) current.current = matrix;
  }, [matrix]);

  const drag = useRef<Drag | null>(null);

  const commit = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    if (!dirty.current) return;
    dirty.current = false;
    latest.current.onCommit(current.current);
  }, []);

  const step = useCallback((next: Mat3) => {
    current.current = next;
    dirty.current = true;
    latest.current.onChange(next);
  }, []);

  // A wheel or key gesture has no end event, so it ends when the events stop.
  const commitSoon = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(commit, COMMIT_DELAY_MS);
  }, [commit]);

  // A callback ref rather than an effect: the native `wheel` listener and the size observer both
  // need the element, and the width they set should be there on the first paint.
  const attach = useCallback(
    (node: HTMLDivElement | null) => {
      if (!node) return undefined;
      setWidth(node.getBoundingClientRect().width);

      // React's `onWheel` is passive, so its `preventDefault` would not stop the page from
      // scrolling under a pinch.
      const onWheel = (event: WheelEvent) => {
        event.preventDefault();
        const rect = node.getBoundingClientRect();
        const delta = event.deltaMode === 1 ? event.deltaY * LINE_PX : event.deltaY;
        const pivot: Point = [
          (event.clientX - rect.left) / rect.width,
          (event.clientY - rect.top) / rect.width,
        ];
        step(scaleAbout(current.current, Math.exp(-delta * WHEEL_ZOOM_RATE), pivot));
        commitSoon();
      };
      node.addEventListener("wheel", onWheel, { passive: false });

      const observer =
        typeof ResizeObserver === "undefined"
          ? null
          : new ResizeObserver(() => {
              setWidth(node.getBoundingClientRect().width);
            });
      observer?.observe(node);

      return () => {
        node.removeEventListener("wheel", onWheel);
        observer?.disconnect();
        // Closing the view right after a gesture must not lose it.
        commit();
      };
    },
    [commit, commitSoon, step],
  );

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || drag.current) return;
    drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    const target = event.currentTarget;
    // jsdom has no pointer capture.
    if (typeof target.setPointerCapture === "function") target.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const state = drag.current;
    if (state?.pointerId !== event.pointerId) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (event.shiftKey) {
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      const before = Math.atan2(state.y - cy, state.x - cx);
      const after = Math.atan2(event.clientY - cy, event.clientX - cx);
      // Across the ±180° seam the raw difference jumps by a full turn.
      let degrees = ((after - before) * 180) / Math.PI;
      if (degrees > 180) degrees -= 360;
      if (degrees <= -180) degrees += 360;
      step(rotateAbout(current.current, degrees, [0.5, outAspect / 2]));
    } else {
      step(
        translateBy(
          current.current,
          (event.clientX - state.x) / rect.width,
          (event.clientY - state.y) / rect.width,
        ),
      );
    }
    state.x = event.clientX;
    state.y = event.clientY;
  };

  const onPointerEnd = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    commit();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    // Cmd/Ctrl plus and minus zoom the page; they are not ours.
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const apply = KEY_STEPS[event.key];
    if (!apply) return;
    event.preventDefault();
    step(apply(current.current, [0.5, outAspect / 2], event.shiftKey));
    commitSoon();
  };

  return (
    <div
      ref={attach}
      role="application"
      aria-label={label}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerEnd}
      onPointerCancel={onPointerEnd}
      onKeyDown={onKeyDown}
      className="relative cursor-grab touch-none overflow-hidden bg-slate-200 select-none active:cursor-grabbing"
      style={{ aspectRatio: `1 / ${String(outAspect)}` }}
    >
      {children}
      {/* Before the first measure the width is 0, and the matrix would divide by it. */}
      {width > 0 && (
        <img
          src={imageUrl}
          alt=""
          draggable={false}
          className="pointer-events-none absolute top-0 left-0 max-w-none"
          style={{
            width,
            opacity,
            transformOrigin: "0 0",
            transform: cssMatrix3d(matrix, width, width),
          }}
        />
      )}
    </div>
  );
}
