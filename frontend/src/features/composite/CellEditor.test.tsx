import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CaseView, PhotoPatch } from "../../api/types";
import { fromArray, translateBy, type Mat3 } from "../../lib/transform";
import { en } from "../../strings/en";
import { fakeServer, type FakeServer } from "../../test/fakeServer";
import { stubBoundingRect } from "../../test/geometry";
import {
  classifierView,
  compositeView,
  editRecord,
  id,
  memberRecord,
  modelStatusView,
} from "../../test/records";
import { renderWithClient } from "../../test/render";
import { CellEditor } from "./CellEditor";
import { CompositeScreen } from "./CompositeScreen";

const START: Mat3 = [1.2, 0, 0.05, 0, 1.2, 0.02, 0, 0, 1];
const cell = memberRecord({
  id: id(1),
  source_name: "IMG_0001.jpg",
  slot: "upper_right",
  edit: editRecord({ flip: "horizontal", transform: [...START], output_size: [1200, 800] }),
});
const view: CaseView = compositeView([cell], {
  flip_defaults: { upper_right: "horizontal" },
});

function serve(): FakeServer {
  return fakeServer({
    "PATCH /api/cases/:case_id/photos/:photo_id": () => view,
    "GET /api/cases/:case_id": () => view,
    // The screen around the editor asks for these.
    "GET /api/model/status": () => modelStatusView({ available: true }),
    "GET /api/classifier/status": () => classifierView({ trained: true, model_available: true }),
  });
}

const patches = (server: FakeServer) => server.calls.filter((call) => call.method === "PATCH");
const bodyOf = (server: FakeServer) => patches(server)[0]?.body as PhotoPatch;

beforeEach(() => {
  stubBoundingRect();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CellEditor", () => {
  it("a pan commits a similarity", async () => {
    const server = serve();
    renderWithClient(<CellEditor view={view} photoId={cell.id} onClose={() => undefined} />);
    const surface = screen.getByRole("application", { name: en.cell.surfaceLabel });

    fireEvent.pointerDown(surface, { pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(surface, { pointerId: 1, clientX: 150, clientY: 100 });
    fireEvent.pointerUp(surface, { pointerId: 1, clientX: 150, clientY: 100 });

    await waitFor(() => {
      expect(patches(server)).toHaveLength(1);
    });
    expect(bodyOf(server).edit?.model).toBe("similarity");
    const sent = bodyOf(server).edit?.transform as number[];
    // The gestures only ever compose similarities, so the projective row must stay exact.
    expect(sent.slice(6)).toEqual([0, 0, 1]);
    const expected = translateBy(START, 0.1, 0);
    fromArray(sent).forEach((value, index) => {
      expect(value).toBeCloseTo(expected[index] as number, 9);
    });
  });

  it("draws the original preview over the case's gap color", () => {
    serve();
    renderWithClient(<CellEditor view={view} photoId={cell.id} onClose={() => undefined} />);
    const surface = screen.getByRole("application", { name: en.cell.surfaceLabel });

    expect(surface.querySelector("img")?.getAttribute("src")).toContain(
      "variant=original&size=preview",
    );
    expect(surface.querySelector("[data-gap]")).toHaveStyle({ backgroundColor: "#000000" });
    expect(surface).toHaveStyle({ aspectRatio: "1 / 0.6666666666666666" });
  });

  it("checks the cell's flip, or the slot's default when it has none", () => {
    serve();
    const plain = memberRecord({
      id: id(2),
      slot: "upper_right",
      edit: editRecord({ flip: null }),
    });
    const defaulted = compositeView([plain], { flip_defaults: { upper_right: "vertical" } });
    const { unmount } = renderWithClient(
      <CellEditor view={view} photoId={cell.id} onClose={() => undefined} />,
    );
    expect(screen.getByRole("radio", { name: en.flips.horizontal })).toBeChecked();
    unmount();

    renderWithClient(<CellEditor view={defaulted} photoId={plain.id} onClose={() => undefined} />);
    expect(screen.getByRole("radio", { name: en.flips.vertical })).toBeChecked();
  });

  it("choosing a flip patches the photo", async () => {
    const server = serve();
    const user = userEvent.setup();
    renderWithClient(<CellEditor view={view} photoId={cell.id} onClose={() => undefined} />);

    await user.click(screen.getByRole("radio", { name: en.flips.vertical }));

    await waitFor(() => {
      expect(patches(server)).toHaveLength(1);
    });
    expect(bodyOf(server)).toEqual({ flip: "vertical" });
    expect(screen.getByText(en.cell.flipNote)).toBeVisible();
  });

  it("reset returns the cell to auto", async () => {
    const server = serve();
    const user = userEvent.setup();
    renderWithClient(<CellEditor view={view} photoId={cell.id} onClose={() => undefined} />);

    await user.click(screen.getByRole("button", { name: en.cell.reset }));

    await waitFor(() => {
      expect(patches(server)).toHaveLength(1);
    });
    expect(bodyOf(server)).toEqual({ reset: true });
  });

  it("says so when the photo has no geometry yet", () => {
    serve();
    const pending = memberRecord({ id: id(3), slot: "upper_left", state: "pending", edit: null });
    const waiting = compositeView([pending]);
    renderWithClient(<CellEditor view={waiting} photoId={pending.id} onClose={() => undefined} />);

    expect(screen.getByText(en.cell.unavailable)).toBeVisible();
  });
});

describe("CompositeScreen cell editing", () => {
  it("the cell opens with Enter", async () => {
    serve();
    const user = userEvent.setup();
    renderWithClient(<CompositeScreen view={view} />);

    screen.getByRole("group", { name: "IMG_0001.jpg" }).focus();
    await user.keyboard("{Enter}");

    const dialog = screen.getByRole("dialog", { name: en.slots.upper_right });
    expect(within(dialog).getByRole("application")).toBeVisible();
  });
});
