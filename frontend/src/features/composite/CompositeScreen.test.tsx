import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { queryKeys } from "../../api/queries";
import type { CaseView, ClassifierView, ModelStatus, PhotoRecord } from "../../api/types";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { FakeEventSource } from "../../test/fakeEventSource";
import { fakeServer } from "../../test/fakeServer";
import {
  classifierView,
  compositeView,
  editRecord,
  id,
  memberRecord,
  modelStatusView,
} from "../../test/records";
import { renderWithClient } from "../../test/render";
import { CompositeScreen } from "./CompositeScreen";

const upperRight = memberRecord({ id: id(1), source_name: "IMG_0001.jpg", slot: "upper_right" });
const lowerLeft = memberRecord({ id: id(2), source_name: "IMG_0002.jpg", slot: "lower_left" });
const loose = memberRecord({ id: id(3), source_name: "IMG_0003.jpg", slot: null, edit: null });

interface Options {
  model?: Partial<ModelStatus>;
  classifier?: Partial<ClassifierView>;
}

function serve(view: CaseView, { model, classifier }: Options = {}) {
  return fakeServer({
    "GET /api/model/status": () => modelStatusView({ available: true, ...model }),
    "GET /api/classifier/status": () =>
      classifierView({ trained: true, model_available: true, ...classifier }),
    "GET /api/cases/:case_id": () => view,
    "PATCH /api/cases/:case_id/photos/:photo_id": () => view,
    "PATCH /api/cases/:case_id": () => view,
    "POST /api/cases/:case_id/composite/assign": () => ({ job_id: id(400) }),
  });
}

const sent = (server: ReturnType<typeof serve>, method: string) =>
  server.calls.filter((call) => call.method === method && !call.path.includes("/api/model"));

const withComposite = (photos: PhotoRecord[], composite: Partial<CaseView["case"]["composite"]>) =>
  compositeView(photos, {
    case: {
      composite: {
        baseline_photo_id: null,
        cell_size: [1200, 800],
        gap_color: "#000000",
        gap_px: 0,
        slots_source: "auto",
        ...composite,
      },
    },
  });

describe("CompositeScreen", () => {
  it("moving a photo to a slot patches it", async () => {
    const view = compositeView([upperRight, lowerLeft, loose]);
    const server = serve(view);
    const user = userEvent.setup();
    renderWithClient(<CompositeScreen view={view} />);

    await user.selectOptions(
      screen.getByRole("combobox", { name: fill(en.board.moveTo, { name: "IMG_0003.jpg" }) }),
      en.slots.upper_left,
    );

    await waitFor(() => {
      expect(sent(server, "PATCH")).toHaveLength(1);
    });
    const patch = sent(server, "PATCH")[0];
    expect(patch?.path).toBe(`/api/cases/${view.case.id}/photos/${loose.id}`);
    expect(patch?.body).toEqual({ slot: "upper_left" });
  });

  it("a photo moves back to the tray from its cell", async () => {
    const view = compositeView([upperRight, loose]);
    const server = serve(view);
    const user = userEvent.setup();
    renderWithClient(<CompositeScreen view={view} />);

    await user.selectOptions(
      screen.getByRole("combobox", { name: fill(en.board.moveTo, { name: "IMG_0001.jpg" }) }),
      en.board.tray,
    );

    await waitFor(() => {
      expect(sent(server, "PATCH")).toHaveLength(1);
    });
    expect(sent(server, "PATCH")[0]?.body).toEqual({ slot: "tray" });
  });

  it("puts placed photos in their cells and the rest in the tray", () => {
    const view = compositeView([upperRight, lowerLeft, loose]);
    serve(view);
    renderWithClient(<CompositeScreen view={view} />);

    const cell = screen.getByRole("group", { name: en.slots.lower_left });
    expect(within(cell).getByText(en.flips.none)).toBeVisible();
    expect(within(cell).getByRole("img", { name: "IMG_0002.jpg" })).toBeVisible();
    const tray = screen.getByRole("group", { name: en.board.tray });
    expect(within(tray).getByRole("img", { name: "IMG_0003.jpg" })).toBeVisible();
  });

  it("shows a photo that is still processing as a placeholder in the tray", () => {
    const pending = memberRecord({ id: id(4), state: "pending", edit: null });
    const view = compositeView([pending]);
    serve(view);
    renderWithClient(<CompositeScreen view={view} />);

    const tray = screen.getByRole("group", { name: en.board.tray });
    expect(within(tray).getByText(en.composite.processing)).toBeVisible();
    expect(within(tray).queryByRole("img")).toBeNull();
    expect(
      within(tray).getByRole("combobox", {
        name: fill(en.board.moveTo, { name: pending.source_name }),
      }),
    ).toBeDisabled();
  });

  it("highlights low-confidence cells", () => {
    const doubtful = memberRecord({
      id: id(5),
      slot: "upper_left",
      edit: editRecord({ flip: "none", status: "needs_review", confidence: 0.4 }),
    });
    const view = compositeView([upperRight, doubtful]);
    serve(view);
    renderWithClient(<CompositeScreen view={view} />);

    expect(screen.getAllByText(en.board.checkPlacement)).toHaveLength(1);
    const cell = screen.getByRole("group", { name: en.slots.upper_left });
    expect(within(cell).getByText(en.board.checkPlacement)).toBeVisible();
  });

  it("the default baseline is sent as null", async () => {
    const view = withComposite([upperRight, lowerLeft], { baseline_photo_id: lowerLeft.id });
    const server = serve(view);
    const user = userEvent.setup();
    renderWithClient(<CompositeScreen view={view} />);

    const baseline = screen.getByRole("combobox", { name: en.composite.baseline });
    expect(baseline).toHaveValue(lowerLeft.id);
    await user.selectOptions(baseline, en.composite.baselineDefault);

    await waitFor(() => {
      expect(sent(server, "PATCH")).toHaveLength(1);
    });
    expect(sent(server, "PATCH")[0]?.path).toBe(`/api/cases/${view.case.id}`);
    expect(sent(server, "PATCH")[0]?.body).toEqual({ composite: { baseline_photo_id: null } });
  });

  it("a placed photo can be the baseline, named by its slot", async () => {
    const view = compositeView([upperRight, lowerLeft, loose]);
    const server = serve(view);
    const user = userEvent.setup();
    renderWithClient(<CompositeScreen view={view} />);

    const baseline = screen.getByRole("combobox", { name: en.composite.baseline });
    // The photo in the tray cannot be one.
    expect(within(baseline).getAllByRole("option")).toHaveLength(3);
    await user.selectOptions(baseline, en.slots.lower_left);

    await waitFor(() => {
      expect(sent(server, "PATCH")).toHaveLength(1);
    });
    expect(sent(server, "PATCH")[0]?.body).toEqual({
      composite: { baseline_photo_id: lowerLeft.id },
    });
  });

  it("re-running the assignment asks first after manual changes", async () => {
    const view = withComposite([upperRight, loose], { slots_source: "manual" });
    const server = serve(view);
    const user = userEvent.setup();
    renderWithClient(<CompositeScreen view={view} />);

    await user.click(screen.getByRole("button", { name: en.composite.rerun }));
    const dialog = screen.getByRole("dialog", { name: en.composite.rerunTitle });
    expect(sent(server, "POST")).toHaveLength(0);

    await user.click(within(dialog).getByRole("button", { name: en.composite.rerunConfirm }));
    await waitFor(() => {
      expect(sent(server, "POST")).toHaveLength(1);
    });
    expect(sent(server, "POST")[0]?.path).toBe(`/api/cases/${view.case.id}/composite/assign`);
  });

  it("cancelling the confirmation sends nothing", async () => {
    const view = withComposite([upperRight, loose], { slots_source: "manual" });
    const server = serve(view);
    const user = userEvent.setup();
    renderWithClient(<CompositeScreen view={view} />);

    await user.click(screen.getByRole("button", { name: en.composite.rerun }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: en.common.cancel }),
    );

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(sent(server, "POST")).toHaveLength(0);
  });

  it("re-running an automatic assignment posts at once, then reloads the case when done", async () => {
    const view = compositeView([upperRight, loose]);
    const server = serve(view);
    const user = userEvent.setup();
    const { client } = renderWithClient(<CompositeScreen view={view} />);
    const invalidate = vi.spyOn(client, "invalidateQueries");

    await user.click(screen.getByRole("button", { name: en.composite.rerun }));
    await waitFor(() => {
      expect(sent(server, "POST")).toHaveLength(1);
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => {
      expect(FakeEventSource.last).not.toBeNull();
    });
    expect(screen.getByRole("button", { name: en.composite.rerunning })).toBeDisabled();

    FakeEventSource.last?.emit("done");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: en.composite.rerun })).toBeEnabled();
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.case(view.case.id) });
  });

  it("an untrained classifier offers teaching", async () => {
    const view = compositeView([upperRight]);
    serve(view, { classifier: { trained: false } });
    renderWithClient(<CompositeScreen view={view} />);

    expect(await screen.findByText(en.ai.untrained)).toBeVisible();
    expect(screen.getByRole("link", { name: en.ai.teach })).toHaveAttribute("href", "/teach");
  });

  it("asks for the AI model when it is missing", async () => {
    const view = compositeView([upperRight]);
    serve(view, { model: { available: false } });
    renderWithClient(<CompositeScreen view={view} />);

    expect(await screen.findByText(en.ai.consent(25, "GitHub"))).toBeVisible();
    expect(screen.queryByRole("link", { name: en.ai.teach })).toBeNull();
  });

  it("shows the composite preview once a photo is placed", () => {
    const view = compositeView([upperRight, loose]);
    serve(view);
    renderWithClient(<CompositeScreen view={view} />);

    expect(screen.getByRole("img", { name: en.composite.preview }).getAttribute("src")).toContain(
      "/render/composite",
    );
  });

  it("shows no preview while nothing is placed", () => {
    const view = compositeView([loose]);
    serve(view);
    renderWithClient(<CompositeScreen view={view} />);

    expect(screen.queryByRole("img", { name: en.composite.preview })).toBeNull();
  });

  it("opens the export dialog", async () => {
    const view = compositeView([upperRight]);
    serve(view);
    const user = userEvent.setup();
    renderWithClient(<CompositeScreen view={view} />);

    await user.click(screen.getByRole("button", { name: en.case.header.export }));

    expect(screen.getByRole("dialog", { name: en.export.title })).toBeVisible();
  });
});
