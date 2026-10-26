import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cssMatrix3d,
  rotateAbout,
  scaleAbout,
  similarity,
  translateBy,
  type Mat3,
  type Point,
} from "../lib/transform";
import { stubBoundingRect } from "../test/geometry";
import {
  COMMIT_DELAY_MS,
  KEY_ZOOM,
  NUDGE,
  NUDGE_FAST,
  ROTATE_STEP_DEG,
  TransformSurface,
  WHEEL_ZOOM_RATE,
} from "./TransformSurface";

const START: Mat3 = similarity(1.2, 5, 0.1, 0.05);
const CENTER: Point = [0.5, 0.375];

function expectClose(actual: Mat3, expected: Mat3) {
  actual.forEach((value, index) => {
    expect(value).toBeCloseTo(expected[index] as number, 9);
  });
}

// The surface reports to its parent, which feeds the result back as the `matrix` prop.
function Harness(props: { onChange: (m: Mat3) => void; onCommit: (m: Mat3) => void }) {
  const [matrix, setMatrix] = useState<Mat3>(START);
  return (
    <TransformSurface
      imageUrl="/photo.jpg"
      outAspect={0.75}
      matrix={matrix}
      label="Alignment surface"
      onChange={(next) => {
        setMatrix(next);
        props.onChange(next);
      }}
      onCommit={props.onCommit}
    />
  );
}

function setup() {
  const onChange = vi.fn<(m: Mat3) => void>();
  const onCommit = vi.fn<(m: Mat3) => void>();
  const view = render(<Harness onChange={onChange} onCommit={onCommit} />);
  const surface = screen.getByRole("application", { name: "Alignment surface" });
  const last = () => onChange.mock.lastCall?.[0] as Mat3;
  return { ...view, surface, onChange, onCommit, last };
}

beforeEach(() => {
  stubBoundingRect();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TransformSurface", () => {
  it("draws the photo with the CSS matrix", () => {
    const { surface } = setup();
    const photo = surface.querySelector("img") as HTMLImageElement;

    expect(photo.style.transform).toBe(cssMatrix3d(START, 500, 500));
    expect(photo.style.width).toBe("500px");
    expect(surface).toHaveStyle({ aspectRatio: "1 / 0.75" });
  });

  it("dragging moves the photo by the dragged distance", () => {
    const { surface, onChange, onCommit, last } = setup();

    fireEvent.pointerDown(surface, { pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(surface, { pointerId: 1, clientX: 150, clientY: 120 });
    expect(onCommit).not.toHaveBeenCalled();
    fireEvent.pointerUp(surface, { pointerId: 1, clientX: 150, clientY: 120 });

    expectClose(last(), translateBy(START, 0.1, 0.04));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenCalledTimes(1);
    expectClose(onCommit.mock.calls[0]?.[0] as Mat3, translateBy(START, 0.1, 0.04));
  });

  it("a click without movement commits nothing", () => {
    const { surface, onCommit } = setup();

    fireEvent.pointerDown(surface, { pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerUp(surface, { pointerId: 1, clientX: 100, clientY: 100 });

    expect(onCommit).not.toHaveBeenCalled();
  });

  it("shift-dragging rotates about the center", () => {
    const { surface, onCommit, last } = setup();

    // From the right of the center to below it: a quarter turn clockwise on screen.
    fireEvent.pointerDown(surface, { pointerId: 1, clientX: 350, clientY: 187.5, shiftKey: true });
    fireEvent.pointerMove(surface, {
      pointerId: 1,
      clientX: 250,
      clientY: 287.5,
      shiftKey: true,
    });
    fireEvent.pointerUp(surface, { pointerId: 1, clientX: 250, clientY: 287.5 });

    expectClose(last(), rotateAbout(START, 90, CENTER));
    expect(onCommit).toHaveBeenCalledTimes(1);
  });

  it("a wheel burst commits once", () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { surface, onChange, onCommit, last } = setup();

    let prevented = true;
    for (let i = 0; i < 20; i++) {
      // `fireEvent` answers false when a listener called `preventDefault`.
      prevented &&= !fireEvent.wheel(surface, { deltaY: -10, clientX: 250, clientY: 187.5 });
    }

    expect(prevented).toBe(true);
    expect(onChange).toHaveBeenCalledTimes(20);
    let expected = START;
    for (let i = 0; i < 20; i++) {
      expected = scaleAbout(expected, Math.exp(10 * WHEEL_ZOOM_RATE), CENTER);
    }
    expectClose(last(), expected);

    act(() => {
      vi.advanceTimersByTime(COMMIT_DELAY_MS - 1);
    });
    expect(onCommit).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(onCommit).toHaveBeenCalledTimes(1);
    expectClose(onCommit.mock.calls[0]?.[0] as Mat3, expected);
  });

  it("a ctrl-wheel from a trackpad pinch zooms the same way", () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { surface, last } = setup();

    fireEvent.wheel(surface, { deltaY: 5, ctrlKey: true, clientX: 100, clientY: 75 });

    expectClose(last(), scaleAbout(START, Math.exp(-5 * WHEEL_ZOOM_RATE), [0.2, 0.15]));
  });

  it("arrow keys nudge and brackets rotate", () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { surface, onCommit, last } = setup();

    fireEvent.keyDown(surface, { key: "ArrowRight" });
    const nudged = translateBy(START, NUDGE, 0);
    expectClose(last(), nudged);

    fireEvent.keyDown(surface, { key: "]" });
    const rotated = rotateAbout(nudged, ROTATE_STEP_DEG, CENTER);
    expectClose(last(), rotated);

    act(() => {
      vi.advanceTimersByTime(COMMIT_DELAY_MS - 1);
    });
    expect(onCommit).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(onCommit).toHaveBeenCalledTimes(1);
    expectClose(onCommit.mock.calls[0]?.[0] as Mat3, rotated);
  });

  it("maps every key to its step", () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { surface, last } = setup();
    let expected = START;
    const press = (init: KeyboardEventInit, next: Mat3) => {
      fireEvent.keyDown(surface, init);
      expected = next;
      expectClose(last(), expected);
    };

    press({ key: "ArrowLeft" }, translateBy(expected, -NUDGE, 0));
    press({ key: "ArrowDown" }, translateBy(expected, 0, NUDGE));
    press({ key: "ArrowUp", shiftKey: true }, translateBy(expected, 0, -NUDGE_FAST));
    press({ key: "[" }, rotateAbout(expected, -ROTATE_STEP_DEG, CENTER));
    press({ key: "+" }, scaleAbout(expected, KEY_ZOOM, CENTER));
    press({ key: "=" }, scaleAbout(expected, KEY_ZOOM, CENTER));
    press({ key: "-" }, scaleAbout(expected, 1 / KEY_ZOOM, CENTER));
  });

  it("leaves browser shortcuts alone", () => {
    const { surface, onChange } = setup();

    const meta = fireEvent.keyDown(surface, { key: "=", metaKey: true });
    const ctrl = fireEvent.keyDown(surface, { key: "+", ctrlKey: true });
    fireEvent.keyDown(surface, { key: "a" });

    expect(meta).toBe(true);
    expect(ctrl).toBe(true);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("rotates with brackets typed through Option or AltGr", () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { surface, last } = setup();

    // Option on German, French, and Swiss Mac layouts.
    fireEvent.keyDown(surface, { key: "]", altKey: true });
    const first = rotateAbout(START, ROTATE_STEP_DEG, CENTER);
    expectClose(last(), first);

    // AltGr on Windows arrives as Ctrl and Alt together.
    fireEvent.keyDown(surface, { key: "]", ctrlKey: true, altKey: true });
    expectClose(last(), rotateAbout(first, ROTATE_STEP_DEG, CENTER));
  });

  it("commits a pending change when it is removed", () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { surface, unmount, onCommit } = setup();

    fireEvent.keyDown(surface, { key: "ArrowRight" });
    unmount();

    expect(onCommit).toHaveBeenCalledTimes(1);
  });
});
