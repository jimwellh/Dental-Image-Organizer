import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { describeError } from "../../api/http";
import { createQueryClient } from "../../api/queries";
import type { CaseView, PhotoPatch } from "../../api/types";
import { COMMIT_DELAY_MS } from "../../components/TransformSurface";
import {
  closestSimilarity,
  fromArray,
  homographyFromPoints,
  mapPoint,
  similarity,
  type Mat3,
  type Point,
} from "../../lib/transform";
import { en } from "../../strings/en";
import { errorReply, fakeServer, type FakeServer } from "../../test/fakeServer";
import { editRecord, id, photoRecord, procedureView } from "../../test/records";
import { NoticeProvider } from "../shell/Notices";
import { useNotify } from "../shell/notify";
import { CompareView } from "./CompareView";

const PHOTO_ID = id(2);
const reference = photoRecord({
  id: id(1),
  role: "reference",
  stage: "pre_op",
  source_name: "reference.jpg",
});

// A homography that is not a similarity: the photo's right edge is nearer the camera.
const HOMOGRAPHY = homographyFromPoints(
  [
    [0.1, 0.1],
    [0.9, 0.1],
    [0.9, 0.6],
    [0.1, 0.6],
  ],
  [
    [0.15, 0.12],
    [0.85, 0.08],
    [0.95, 0.62],
    [0.08, 0.58],
  ],
);

function viewOf(edit = editRecord()): CaseView {
  return procedureView([reference, photoRecord({ id: PHOTO_ID, width: 4000, height: 3000, edit })]);
}

// The global mutation handler lives in `App`; the test needs the same one to see a refused edit.
function Host({ children }: { children: ReactNode }) {
  const notify = useNotify();
  const [client] = useState(() =>
    createQueryClient((error) => {
      notify({ kind: "error", lines: [describeError(error)] });
    }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function open(view: CaseView) {
  return render(
    <NoticeProvider>
      <Host>
        <CompareView view={view} photoId={PHOTO_ID} onClose={() => undefined} />
      </Host>
    </NoticeProvider>,
  );
}

function serve(view: CaseView): FakeServer {
  return fakeServer({ "PATCH /api/cases/:case_id/photos/:photo_id": () => view });
}

const patches = (server: FakeServer) => server.calls.filter((call) => call.method === "PATCH");
const bodyOf = (server: FakeServer) => patches(server)[0]?.body as PhotoPatch;
const transformOf = (server: FakeServer): Mat3 =>
  fromArray(bodyOf(server).edit?.transform as number[]);

async function startAligning(user: ReturnType<typeof userEvent.setup>, method: string) {
  await user.click(screen.getByRole("radio", { name: en.compare.modes.align }));
  await user.click(screen.getByRole("radio", { name: method }));
}

// Panels are 500 px wide in the tests, and both axes are normalized by the width.
const placeAt = (panel: HTMLElement, point: Point) => {
  fireEvent.click(panel, { clientX: point[0] * 500, clientY: point[1] * 500 });
};

function placePairs(photoPoints: readonly Point[], referencePoints: readonly Point[]) {
  photoPoints.forEach((photoPoint, index) => {
    placeAt(
      screen.getByRole("button", { name: en.compare.picker.reference }),
      referencePoints[index] as Point,
    );
    placeAt(screen.getByRole("button", { name: en.compare.picker.photo }), photoPoint);
  });
}

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0,
    top: 0,
    right: 500,
    bottom: 375,
    x: 0,
    y: 0,
    width: 500,
    height: 375,
    toJSON: () => ({}),
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CompareView", () => {
  it("lays the processed photo over the reference with an opacity slider", () => {
    serve(viewOf());
    open(viewOf());

    const images = screen.getAllByRole("img");
    expect(images[0]).toHaveAttribute(
      "src",
      expect.stringContaining("variant=processed&size=preview"),
    );
    expect(images[1]).toHaveAttribute(
      "src",
      expect.stringContaining("variant=processed&size=preview"),
    );
    expect(images[1]).toHaveStyle({ opacity: "0.5" });

    fireEvent.change(screen.getByRole("slider", { name: en.compare.opacity }), {
      target: { value: "20" },
    });
    expect(images[1]).toHaveStyle({ opacity: "0.2" });
  });

  it("splits the picture at the slider position", async () => {
    serve(viewOf());
    const user = userEvent.setup();
    open(viewOf());

    await user.click(screen.getByRole("radio", { name: en.compare.modes.split }));
    expect(screen.queryByRole("slider", { name: en.compare.opacity })).toBeNull();
    fireEvent.change(screen.getByRole("slider", { name: en.compare.splitPosition }), {
      target: { value: "30" },
    });

    const photo = screen.getAllByRole("img")[1] as HTMLElement;
    expect(photo.style.clipPath).toBe("inset(0 0 0 30%)");
  });

  it("two landmark pairs apply the similarity that maps them", async () => {
    const server = serve(viewOf());
    const user = userEvent.setup();
    open(viewOf());
    const photoPoints: Point[] = [
      [0.24, 0.16],
      [0.76, 0.66],
    ];
    const truth = similarity(1.5, 10, 0.05, 0.02);
    const referencePoints = photoPoints.map((point) => mapPoint(truth, point));

    await startAligning(user, en.compare.methods.two);
    expect(screen.getByRole("button", { name: en.compare.picker.apply })).toBeDisabled();
    placePairs(photoPoints, referencePoints);
    await user.click(screen.getByRole("button", { name: en.compare.picker.apply }));

    await waitFor(() => {
      expect(patches(server)).toHaveLength(1);
    });
    expect(bodyOf(server).edit?.model).toBe("similarity");
    photoPoints.forEach((point, index) => {
      const mapped = mapPoint(transformOf(server), point);
      const expected = referencePoints[index] as Point;
      expect(Math.abs(mapped[0] - expected[0])).toBeLessThan(1e-9);
      expect(Math.abs(mapped[1] - expected[1])).toBeLessThan(1e-9);
    });
  });

  it("four landmark pairs apply a homography", async () => {
    const server = serve(viewOf());
    const user = userEvent.setup();
    open(viewOf());
    const photoPoints: Point[] = [
      [0.1, 0.1],
      [0.9, 0.1],
      [0.9, 0.6],
      [0.1, 0.6],
    ];
    const referencePoints = photoPoints.map((point) => mapPoint(HOMOGRAPHY, point));

    await startAligning(user, en.compare.methods.four);
    placePairs(photoPoints, referencePoints);
    await user.click(screen.getByRole("button", { name: en.compare.picker.apply }));

    await waitFor(() => {
      expect(patches(server)).toHaveLength(1);
    });
    expect(bodyOf(server).edit?.model).toBe("homography");
    photoPoints.forEach((point, index) => {
      const mapped = mapPoint(transformOf(server), point);
      const expected = referencePoints[index] as Point;
      expect(Math.abs(mapped[0] - expected[0])).toBeLessThan(1e-9);
      expect(Math.abs(mapped[1] - expected[1])).toBeLessThan(1e-9);
    });
  });

  it("degenerate landmarks send nothing and say why", async () => {
    const server = serve(viewOf());
    const user = userEvent.setup();
    open(viewOf());

    await startAligning(user, en.compare.methods.two);
    // Both photo landmarks on one spot: no scale or angle can be fitted.
    placePairs(
      [
        [0.3, 0.3],
        [0.3, 0.3],
      ],
      [
        [0.2, 0.2],
        [0.7, 0.5],
      ],
    );
    await user.click(screen.getByRole("button", { name: en.compare.picker.apply }));

    expect(await screen.findByText(en.compare.degenerate)).toBeVisible();
    expect(patches(server)).toHaveLength(0);
  });

  it("places and moves landmarks from the keyboard, and removes the last one", async () => {
    serve(viewOf());
    const user = userEvent.setup();
    open(viewOf());
    await startAligning(user, en.compare.methods.two);
    const panel = screen.getByRole("button", { name: en.compare.picker.reference });

    panel.focus();
    await user.keyboard("{Enter}");
    // Enter must not also count as a click at (0, 0).
    expect(screen.getAllByText("1")).toHaveLength(1);
    expect(screen.getByText("1").style.left).toBe("50%");
    await user.keyboard("{ArrowRight}");
    expect(Number.parseFloat(screen.getByText("1").style.left)).toBeCloseTo(50.2, 9);

    await user.click(screen.getByRole("button", { name: en.compare.picker.removeLast }));
    expect(screen.queryByText("1")).toBeNull();

    await user.click(screen.getByRole("button", { name: en.compare.picker.clear }));
  });

  it("reset perspective sends the closest similarity", async () => {
    const edit = editRecord({ model: "homography", transform: [...HOMOGRAPHY], source: "manual" });
    const server = serve(viewOf(edit));
    const user = userEvent.setup();
    open(viewOf(edit));

    await user.click(screen.getByRole("button", { name: en.compare.resetPerspective }));

    await waitFor(() => {
      expect(patches(server)).toHaveLength(1);
    });
    expect(bodyOf(server).edit?.model).toBe("similarity");
    const expected = closestSimilarity(HOMOGRAPHY, 1, 0.75);
    transformOf(server).forEach((value, index) => {
      expect(value).toBeCloseTo(expected[index] as number, 9);
    });
  });

  it("offers no perspective to reset on a similarity", () => {
    serve(viewOf());
    open(viewOf());

    expect(screen.getByRole("button", { name: en.compare.resetPerspective })).toBeDisabled();
  });

  it("reset to auto asks the server to align again", async () => {
    const server = serve(viewOf());
    const user = userEvent.setup();
    open(viewOf());

    await user.click(screen.getByRole("button", { name: en.compare.resetToAuto }));

    await waitFor(() => {
      expect(patches(server)).toHaveLength(1);
    });
    expect(bodyOf(server)).toEqual({ reset: true });
  });

  it("a refused edit shows the server's reason", async () => {
    fakeServer({
      "PATCH /api/cases/:case_id/photos/:photo_id": () =>
        errorReply(422, "invalid_edit", "The transform folds the output frame over itself."),
    });
    const user = userEvent.setup();
    open(viewOf());

    await user.click(screen.getByRole("button", { name: en.compare.resetToAuto }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The transform folds the output frame over itself.",
    );
  });

  describe("free alignment", () => {
    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
    });

    it("a wheel burst sends one PATCH that keeps the model", async () => {
      const edit = editRecord({ model: "homography", transform: [...HOMOGRAPHY] });
      const server = serve(viewOf(edit));
      // `userEvent` waits on timers, so it takes the same fake clock.
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      open(viewOf(edit));
      await startAligning(user, en.compare.methods.free);
      const surface = screen.getByRole("application");

      for (let i = 0; i < 20; i++) {
        fireEvent.wheel(surface, { deltaY: -10, clientX: 250, clientY: 187 });
      }
      await act(async () => {
        // Half the delay: `shouldAdvanceTime` also lets real time through, so the edge is not exact.
        await vi.advanceTimersByTimeAsync(COMMIT_DELAY_MS / 2);
      });
      expect(patches(server)).toHaveLength(0);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(COMMIT_DELAY_MS);
      });

      await waitFor(() => {
        expect(patches(server)).toHaveLength(1);
      });
      expect(bodyOf(server).edit?.model).toBe("homography");
      expect(transformOf(server)[0]).not.toBeCloseTo(HOMOGRAPHY[0], 3);
    });

    it("puts the reference beneath the photo's original preview", async () => {
      serve(viewOf());
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      open(viewOf());
      await startAligning(user, en.compare.methods.free);

      // Decorative images have no `img` role, so the query goes by tag.
      const sources = [...screen.getByRole("application").querySelectorAll("img")].map((image) =>
        image.getAttribute("src"),
      );
      expect(sources[0]).toContain("variant=processed&size=preview");
      expect(sources[1]).toContain("variant=original&size=preview");
    });
  });

  it("says so when the photo has no alignment yet", () => {
    const view = procedureView([reference, photoRecord({ id: PHOTO_ID, edit: null })]);
    serve(view);
    open(view);

    expect(screen.getByText(en.compare.unavailable)).toBeVisible();
  });
});
