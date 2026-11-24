import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { CaseView, PhotoRecord } from "../../api/types";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { fakeClipboard, fakeFile, fakeTransfer, fileEntry } from "../../test/fakeDrop";
import { fakeServer, type FakeServer } from "../../test/fakeServer";
import { editRecord, id, photoRecord, procedureView } from "../../test/records";
import { renderWithClient } from "../../test/render";
import { ProcedureScreen } from "./ProcedureScreen";

const reference = photoRecord({
  id: id(1),
  role: "reference",
  stage: "pre_op",
  source_name: "reference.jpg",
});

// The sequence builder lists the same names, so a tile is looked up inside the timeline.
const tile = (photo: PhotoRecord) =>
  within(screen.getByRole("region", { name: en.procedure.timeline.title }))
    .getByText(photo.source_name)
    .closest("li");

function serveView(view: CaseView) {
  return fakeServer({
    "PATCH /api/cases/:case_id": () => view,
    "PATCH /api/cases/:case_id/photos/:photo_id": () => view,
    "DELETE /api/cases/:case_id/photos/:photo_id": () => view,
    "PUT /api/cases/:case_id/reference": () => ({ job_id: null, added: [], skipped: [] }),
  });
}

// The view is a prop, so a change shows only as the request that carries it.
const sent = (server: FakeServer, method: string) =>
  waitFor(() => {
    expect(server.calls.filter((call) => call.method === method)).toHaveLength(1);
  });

describe("ProcedureScreen", () => {
  it("marks photos that need review", () => {
    const photo = photoRecord({
      id: id(2),
      edit: editRecord({ status: "needs_review", confidence: 0.42 }),
    });
    serveView(procedureView([reference, photo]));
    renderWithClient(<ProcedureScreen view={procedureView([reference, photo])} />);

    const card = within(tile(photo) as HTMLElement);
    expect(card.getByText(en.procedure.tile.needsReview)).toBeInTheDocument();
    expect(card.getByText("42%")).toBeInTheDocument();
  });

  it("shows a manual alignment and a photo that waits for the reference", () => {
    const manual = photoRecord({ id: id(2), edit: editRecord({ source: "manual" }) });
    const waiting = photoRecord({ id: id(3), source_name: "waiting.heic", edit: null });
    const view = procedureView([manual, waiting]);
    serveView(view);
    const { container } = renderWithClient(<ProcedureScreen view={view} />);

    expect(within(tile(manual) as HTMLElement).getByText(en.procedure.tile.manual)).toBeVisible();
    const card = tile(waiting) as HTMLElement;
    expect(within(card).getByText(en.procedure.tile.waitingForReference)).toBeVisible();
    expect(card.querySelector("img")?.getAttribute("src")).toContain("variant=original&size=thumb");
    expect(container.querySelector("img[src*='variant=processed']")).not.toBeNull();
  });

  it("changing a stage patches the photo", async () => {
    const photo = photoRecord({ id: id(2), stage: "intra_op" });
    const view = procedureView([reference, photo]);
    const server = serveView(view);
    const user = userEvent.setup();
    renderWithClient(<ProcedureScreen view={view} />);

    await user.selectOptions(
      screen.getByRole("combobox", {
        name: fill(en.procedure.tile.stage, { name: photo.source_name }),
      }),
      "post_op",
    );

    await sent(server, "PATCH");
    const patch = server.calls.find((call) => call.method === "PATCH");
    expect(patch?.path).toBe(`/api/cases/${view.case.id}/photos/${photo.id}`);
    expect(patch?.body).toEqual({ stage: "post_op" });
  });

  it("the reference has no stage, compare, or delete controls", () => {
    const view = procedureView([reference]);
    serveView(view);
    renderWithClient(<ProcedureScreen view={view} />);

    const card = within(tile(reference) as HTMLElement);
    expect(card.queryByRole("combobox")).toBeNull();
    expect(card.queryByRole("button")).toBeNull();
  });

  it("Compare opens the compare view of that photo, and Close returns", async () => {
    const photo = photoRecord({ id: id(2) });
    const view = procedureView([reference, photo]);
    serveView(view);
    const user = userEvent.setup();
    renderWithClient(<ProcedureScreen view={view} />);

    await user.click(
      within(tile(photo) as HTMLElement).getByRole("button", { name: en.procedure.tile.compare }),
    );
    const dialog = screen.getByRole("dialog", { name: en.compare.title });
    expect(within(dialog).getByAltText(photo.source_name)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: en.compare.close }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("deleting a photo asks first", async () => {
    const photo = photoRecord({ id: id(2) });
    const view = procedureView([reference, photo]);
    const server = serveView(view);
    const user = userEvent.setup();
    renderWithClient(<ProcedureScreen view={view} />);

    await user.click(
      within(tile(photo) as HTMLElement).getByRole("button", { name: en.procedure.tile.delete }),
    );
    expect(server.calls.filter((call) => call.method === "DELETE")).toHaveLength(0);

    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: en.procedure.tile.deleteConfirm,
      }),
    );
    await sent(server, "DELETE");
    expect(server.calls.filter((call) => call.method === "DELETE").map((c) => c.path)).toEqual([
      `/api/cases/${view.case.id}/photos/${photo.id}`,
    ]);
  });

  it("asks for the reference when there is none", () => {
    const view = procedureView([]);
    serveView(view);
    renderWithClient(<ProcedureScreen view={view} />);

    expect(screen.getByRole("region", { name: en.procedure.reference.drop })).toBeInTheDocument();
    expect(screen.queryByText(en.procedure.reference.locked)).toBeNull();
  });

  it("says a pending reference is processing", () => {
    const view = procedureView([
      photoRecord({ id: id(1), role: "reference", state: "pending", edit: null }),
    ]);
    serveView(view);
    renderWithClient(<ProcedureScreen view={view} />);

    const panel = screen.getByRole("region", { name: en.procedure.reference.title });
    expect(within(panel).getByText(en.procedure.reference.processing)).toBeInTheDocument();
  });

  it("replacing the reference asks first and uploads once", async () => {
    const view = procedureView([reference]);
    const server = serveView(view);
    const user = userEvent.setup();
    renderWithClient(<ProcedureScreen view={view} />);
    const panel = screen.getByRole("region", { name: en.procedure.reference.title });
    expect(within(panel).getByText(en.procedure.reference.locked)).toBeInTheDocument();

    await user.click(within(panel).getByRole("button", { name: en.procedure.reference.replace }));
    expect(server.calls.filter((call) => call.method === "PUT")).toHaveLength(0);

    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: en.procedure.reference.replaceConfirm,
      }),
    );
    const picker = panel.querySelector<HTMLInputElement>('input[type="file"]');
    await user.upload(picker as HTMLInputElement, fakeFile("edited.jpg", "image/jpeg"));

    await sent(server, "PUT");
    expect(server.calls.filter((call) => call.method === "PUT").map((c) => c.path)).toEqual([
      `/api/cases/${view.case.id}/reference`,
    ]);
  });

  it("sends the first photo of a drop whose first file is filtered out", async () => {
    const view = procedureView([]);
    const server = serveView(view);
    renderWithClient(<ProcedureScreen view={view} />);

    const entries = ["IMG_0001.AAE", "IMG_0001.HEIC", "IMG_0002.HEIC"].map((name) =>
      fileEntry(fakeFile(name)),
    );
    fireEvent.drop(screen.getByRole("region", { name: en.procedure.reference.drop }), {
      dataTransfer: fakeTransfer(entries),
    });

    await sent(server, "PUT");
    const put = server.calls.find((call) => call.method === "PUT");
    const sentFile = (put?.body as FormData).get("file") as File;
    expect(sentFile.name).toBe("IMG_0001.HEIC");
    expect(await screen.findByText("Skipped 1 file: 1 edit sidecar")).toBeInTheDocument();
  });

  it("says so when two photos are dropped but only the first is the reference", async () => {
    const view = procedureView([]);
    const server = serveView(view);
    renderWithClient(<ProcedureScreen view={view} />);

    const entries = ["A.HEIC", "B.HEIC"].map((name) => fileEntry(fakeFile(name)));
    fireEvent.drop(screen.getByRole("region", { name: en.procedure.reference.drop }), {
      dataTransfer: fakeTransfer(entries),
    });

    await sent(server, "PUT");
    expect(await screen.findByText(en.procedure.reference.onlyFirstUsed)).toBeInTheDocument();
  });

  it("explains a reference the server skipped", async () => {
    const view = procedureView([]);
    fakeServer({
      "PUT /api/cases/:case_id/reference": () => ({
        job_id: null,
        added: [],
        skipped: [{ name: "IMG_0001.DNG", reason: "raw", message: "RAW is not supported." }],
      }),
    });
    renderWithClient(<ProcedureScreen view={view} />);

    fireEvent.drop(screen.getByRole("region", { name: en.procedure.reference.drop }), {
      dataTransfer: fakeTransfer([fileEntry(fakeFile("IMG_0001.DNG", "image/x-adobe-dng"))]),
    });

    expect(await screen.findByText(en.skip.rawAdvice)).toBeInTheDocument();
  });

  it("a pasted image becomes the reference when there is none", async () => {
    const view = procedureView([]);
    const server = serveView(view);
    renderWithClient(<ProcedureScreen view={view} />);

    const handled = !fireEvent.paste(document, {
      clipboardData: fakeClipboard([fakeFile("image.png", "image/png")]),
    });

    expect(handled).toBe(true);
    await sent(server, "PUT");
    const put = server.calls.find((call) => call.method === "PUT");
    expect(((put?.body as FormData).get("file") as File).name).toBe("image.png");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("pasting over a reference asks first", async () => {
    const view = procedureView([reference]);
    const server = serveView(view);
    const user = userEvent.setup();
    renderWithClient(<ProcedureScreen view={view} />);
    const paste = () =>
      fireEvent.paste(document, {
        clipboardData: fakeClipboard([fakeFile("image.png", "image/png")]),
      });

    paste();
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: en.common.cancel }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(server.calls.filter((call) => call.method === "PUT")).toHaveLength(0);

    paste();
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: en.procedure.reference.replaceDropConfirm,
      }),
    );
    await sent(server, "PUT");
  });

  it("dropping an image on a locked reference asks first", async () => {
    const view = procedureView([reference]);
    const server = serveView(view);
    const user = userEvent.setup();
    renderWithClient(<ProcedureScreen view={view} />);
    const panel = screen.getByRole("region", { name: en.procedure.reference.title });
    expect(within(panel).getByText(en.procedure.reference.replaceHint)).toBeInTheDocument();

    fireEvent.drop(panel, {
      dataTransfer: fakeTransfer([fileEntry(fakeFile("crop.png", "image/png"))]),
    });
    const dialog = await screen.findByRole("dialog");
    expect(server.calls.filter((call) => call.method === "PUT")).toHaveLength(0);
    await user.dblClick(
      within(dialog).getByRole("button", { name: en.procedure.reference.replaceDropConfirm }),
    );

    // A double click on the confirm button still sends one request.
    await sent(server, "PUT");
    const put = server.calls.find((call) => call.method === "PUT");
    expect(((put?.body as FormData).get("file") as File).name).toBe("crop.png");
  });

  it("leaves a paste into a text field, a text paste, and a paste under a dialog alone", async () => {
    const view = procedureView([reference]);
    const server = serveView(view);
    const user = userEvent.setup();
    renderWithClient(<ProcedureScreen view={view} />);
    const image = () => ({ clipboardData: fakeClipboard([fakeFile("image.png", "image/png")]) });

    const field = document.createElement("input");
    document.body.append(field);
    try {
      expect(fireEvent.paste(field, image())).toBe(true);
    } finally {
      field.remove();
    }
    expect(fireEvent.paste(document, { clipboardData: fakeClipboard([], { text: "16" }) })).toBe(
      true,
    );

    await user.click(screen.getByRole("button", { name: en.procedure.reference.replace }));
    expect(fireEvent.paste(document, image())).toBe(true);
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(server.calls.filter((call) => call.method === "PUT")).toHaveLength(0);
  });

  it("says so when the browser cannot read a pasted image", async () => {
    vi.stubGlobal("createImageBitmap", () => Promise.reject(new Error("undecodable")));
    const view = procedureView([]);
    const server = serveView(view);
    renderWithClient(<ProcedureScreen view={view} />);

    fireEvent.paste(document, {
      clipboardData: fakeClipboard([fakeFile("crop.tiff", "image/tiff")]),
    });

    expect(
      await screen.findByText(fill(en.procedure.reference.unreadableFormat, { name: "crop.tiff" })),
    ).toBeInTheDocument();
    expect(server.calls.filter((call) => call.method === "PUT")).toHaveLength(0);
  });

  it("suggests a larger output when the photos carry more detail", async () => {
    const view = procedureView([reference], {
      suggested_output_scale: 1.5,
      case: { output_scale: 1 },
    });
    const server = serveView(view);
    const user = userEvent.setup();
    renderWithClient(<ProcedureScreen view={view} />);

    expect(screen.getByRole("radio", { name: "1×" })).toBeChecked();
    expect(screen.getByText(/1\.5× is suggested/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Use 1.5×" }));

    await sent(server, "PATCH");
    expect(server.calls.find((call) => call.method === "PATCH")?.body).toEqual({
      output_scale: 1.5,
    });
  });

  it("does not suggest what is already chosen", () => {
    const view = procedureView([reference], {
      suggested_output_scale: 1.5,
      case: { output_scale: 2 },
    });
    serveView(view);
    renderWithClient(<ProcedureScreen view={view} />);

    expect(screen.queryByText(/is suggested/)).toBeNull();
    expect(screen.getByRole("radio", { name: "2×" })).toBeChecked();
  });

  it("choosing a resolution patches the case", async () => {
    const view = procedureView([reference], { case: { output_scale: 1 } });
    const server = serveView(view);
    const user = userEvent.setup();
    renderWithClient(<ProcedureScreen view={view} />);

    await user.click(screen.getByRole("radio", { name: "2×" }));

    await sent(server, "PATCH");
    expect(server.calls.find((call) => call.method === "PATCH")?.body).toEqual({ output_scale: 2 });
  });

  it("warns about a photo without a capture time", () => {
    const photo = photoRecord({ id: id(2), captured_at_source: "file_mtime" });
    const view = procedureView([reference, photo]);
    serveView(view);
    renderWithClient(<ProcedureScreen view={view} />);

    expect(
      within(tile(photo) as HTMLElement).getByText(en.procedure.tile.captureTimeWarning),
    ).toBeInTheDocument();
  });

  it("does not warn about a reference without a capture time", () => {
    // The reference leads the sequence whatever its time, so a missing one changes nothing.
    const pasted = { ...reference, captured_at_source: "file_mtime" as const };
    const view = procedureView([pasted]);
    serveView(view);
    renderWithClient(<ProcedureScreen view={view} />);

    expect(
      within(tile(pasted) as HTMLElement).queryByText(en.procedure.tile.captureTimeWarning),
    ).toBeNull();
  });

  it("a failed photo shows why", () => {
    const photo = photoRecord({
      id: id(2),
      state: "failed",
      edit: null,
      error: "The file is damaged.",
    });
    const view = procedureView([reference, photo]);
    serveView(view);
    renderWithClient(<ProcedureScreen view={view} />);

    const card = within(tile(photo) as HTMLElement);
    expect(card.getByText("The file is damaged.")).toBeInTheDocument();
    expect(card.queryByRole("combobox")).toBeNull();
  });

  it("a pending photo shows a placeholder and no controls", () => {
    const photo = photoRecord({ id: id(2), state: "pending", edit: null });
    const view = procedureView([reference, photo]);
    serveView(view);
    renderWithClient(<ProcedureScreen view={view} />);

    const card = within(tile(photo) as HTMLElement);
    expect(card.getByText(en.procedure.tile.processing)).toBeInTheDocument();
    expect(card.queryByRole("img")).toBeNull();
    expect(card.queryByRole("button")).toBeNull();
  });

  it("opens the export dialog for the case", async () => {
    const view = procedureView([reference]);
    serveView(view);
    const user = userEvent.setup();
    renderWithClient(<ProcedureScreen view={view} />);

    await user.click(screen.getByRole("button", { name: en.case.header.export }));
    expect(screen.getByRole("dialog", { name: en.export.title })).toBeInTheDocument();
  });
});
