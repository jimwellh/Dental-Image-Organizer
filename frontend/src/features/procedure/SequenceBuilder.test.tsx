import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CaseView, PhotoRecord } from "../../api/types";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { fakeServer } from "../../test/fakeServer";
import { id, photoRecord, procedureView } from "../../test/records";
import { renderWithClient } from "../../test/render";
import { SequenceBuilder } from "./SequenceBuilder";

// Capture order is c, a, b, so it differs from both the id order and the upload order.
const a = photoRecord({ id: id(1), source_name: "a.heic", captured_at: "2026-10-16T10:00:00" });
const b = photoRecord({ id: id(2), source_name: "b.heic", captured_at: "2026-10-16T11:00:00" });
const c = photoRecord({ id: id(3), source_name: "c.heic", captured_at: "2026-10-16T09:00:00" });

const viewOf = (photos: PhotoRecord[], photoIds: string[]): CaseView =>
  procedureView(photos, { case: { sequence: { photo_ids: photoIds, gap_px: 0 } } });

function serve(view: CaseView) {
  return fakeServer({ "PATCH /api/cases/:case_id": () => view });
}

const order = () =>
  within(screen.getByRole("list", { name: en.sequence.order }))
    .queryAllByRole("listitem")
    .map((row) => row.querySelector("[data-name]")?.textContent);

const moveUp = (name: string) => {
  const row = screen.getByRole("list", { name: en.sequence.order });
  const item = within(row)
    .getAllByRole("listitem")
    .find((li) => li.textContent.includes(name));
  if (!item) throw new Error(`no row for ${name}`);
  return within(item).getByRole("button", { name: en.sequence.moveUp });
};

const patched = (server: ReturnType<typeof serve>) =>
  waitFor(() => {
    expect(server.calls.filter((call) => call.method === "PATCH")).toHaveLength(1);
  });

describe("SequenceBuilder", () => {
  it("starts from the saved selection, or every photo in capture order", () => {
    const saved = viewOf([a, b, c], [b.id, a.id]);
    serve(saved);
    const { unmount } = renderWithClient(<SequenceBuilder view={saved} />);
    expect(order()).toEqual(["b.heic", "a.heic"]);
    unmount();

    const all = viewOf([a, b, c], []);
    serve(all);
    renderWithClient(<SequenceBuilder view={all} />);
    expect(order()).toEqual(["c.heic", "a.heic", "b.heic"]);
  });

  it("falls back to every usable photo when fewer than two saved ones remain", () => {
    const pending = photoRecord({ id: id(4), state: "pending", edit: null });
    const view = viewOf([a, b, c, pending], [a.id, pending.id]);
    serve(view);
    renderWithClient(<SequenceBuilder view={view} />);

    expect(order()).toEqual(["c.heic", "a.heic", "b.heic"]);
    expect(screen.queryByRole("checkbox", { name: /IMG_0004/ })).toBeNull();
  });

  it("moving a photo up and saving sends the new order", async () => {
    const view = viewOf([a, b, c], [a.id, b.id, c.id]);
    const server = serve(view);
    const user = userEvent.setup();
    renderWithClient(<SequenceBuilder view={view} />);

    await user.click(moveUp("c.heic"));
    expect(order()).toEqual(["a.heic", "c.heic", "b.heic"]);
    await user.click(screen.getByRole("button", { name: en.sequence.save }));

    await patched(server);
    expect(server.calls.find((call) => call.method === "PATCH")?.body).toEqual({
      sequence: { photo_ids: [a.id, c.id, b.id] },
    });
  });

  it("keeps the ends of the list from moving past them", () => {
    const view = viewOf([a, b, c], [a.id, b.id, c.id]);
    serve(view);
    renderWithClient(<SequenceBuilder view={view} />);

    expect(moveUp("a.heic")).toBeDisabled();
    const rows = within(screen.getByRole("list", { name: en.sequence.order })).getAllByRole(
      "listitem",
    );
    const last = rows.at(-1) as HTMLElement;
    expect(within(last).getByRole("button", { name: en.sequence.moveDown })).toBeDisabled();
  });

  it("a checkbox adds a photo to the end and removes it again", async () => {
    const view = viewOf([a, b, c], [a.id, b.id]);
    serve(view);
    const user = userEvent.setup();
    renderWithClient(<SequenceBuilder view={view} />);

    const box = screen.getByRole("checkbox", {
      name: fill(en.sequence.choose, { name: "c.heic" }),
    });
    await user.click(box);
    expect(order()).toEqual(["a.heic", "b.heic", "c.heic"]);
    await user.click(box);
    expect(order()).toEqual(["a.heic", "b.heic"]);
  });

  it("saves only a changed selection", async () => {
    const view = viewOf([a, b, c], [a.id, b.id]);
    serve(view);
    const user = userEvent.setup();
    renderWithClient(<SequenceBuilder view={view} />);

    const save = screen.getByRole("button", { name: en.sequence.save });
    expect(save).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /c\.heic/ }));
    expect(save).toBeEnabled();
  });

  it("shows no error for the unedited default of more than ten photos", () => {
    const photos = Array.from({ length: 11 }, (_, n) =>
      photoRecord({ id: id(n + 1), captured_at: `2026-10-16T10:${String(n).padStart(2, "0")}:00` }),
    );
    const view = viewOf(photos, []);
    serve(view);
    renderWithClient(<SequenceBuilder view={view} />);

    expect(screen.queryByText(/A sequence holds/)).toBeNull();
    expect(screen.getByRole("button", { name: en.sequence.save })).toBeDisabled();
  });

  it("refuses more than ten photos once the selection is edited", async () => {
    const photos = Array.from({ length: 11 }, (_, n) =>
      photoRecord({ id: id(n + 1), captured_at: `2026-10-16T10:${String(n).padStart(2, "0")}:00` }),
    );
    const view = viewOf(photos, []);
    serve(view);
    const user = userEvent.setup();
    renderWithClient(<SequenceBuilder view={view} />);

    // Unticking and ticking again leaves 11 chosen, but the dentist has now edited the list.
    const box = screen.getAllByRole("checkbox")[0] as HTMLElement;
    await user.click(box);
    await user.click(box);

    expect(screen.getByRole("button", { name: en.sequence.save })).toBeDisabled();
    expect(
      screen.getByText(fill(en.sequence.outOfRange, { min: "2", max: "10", count: "11" })),
    ).toBeVisible();
  });

  it("refuses a single photo", async () => {
    const view = viewOf([a], []);
    serve(view);
    const user = userEvent.setup();
    renderWithClient(<SequenceBuilder view={view} />);

    await user.click(screen.getByRole("checkbox", { name: /a\.heic/ }));
    await user.click(screen.getByRole("checkbox", { name: /a\.heic/ }));

    expect(screen.getByRole("button", { name: en.sequence.save })).toBeDisabled();
    expect(
      screen.getByText(fill(en.sequence.outOfRange, { min: "2", max: "10", count: "1" })),
    ).toBeVisible();
  });

  it("use all in capture order clears the selection", async () => {
    const view = viewOf([a, b, c], [b.id, a.id]);
    const server = serve(view);
    const user = userEvent.setup();
    renderWithClient(<SequenceBuilder view={view} />);

    await user.click(screen.getByRole("button", { name: en.sequence.useAll }));

    await patched(server);
    expect(server.calls.find((call) => call.method === "PATCH")?.body).toEqual({
      sequence: { photo_ids: [] },
    });
  });

  it("previews the sequence once a photo is usable", () => {
    const view = viewOf([a, b], []);
    serve(view);
    renderWithClient(<SequenceBuilder view={view} />);

    expect(screen.getByRole("img", { name: en.sequence.preview }).getAttribute("src")).toContain(
      "/render/sequence?size=preview",
    );
  });

  it("shows no preview and says why when nothing is usable", () => {
    const view = viewOf([photoRecord({ id: id(5), state: "pending", edit: null })], []);
    serve(view);
    renderWithClient(<SequenceBuilder view={view} />);

    expect(screen.queryByRole("img", { name: en.sequence.preview })).toBeNull();
    expect(screen.getByText(en.sequence.empty)).toBeVisible();
  });

  it("opens the export dialog", async () => {
    const view = viewOf([a, b], []);
    serve(view);
    const user = userEvent.setup();
    renderWithClient(<SequenceBuilder view={view} />);

    await user.click(screen.getByRole("button", { name: en.sequence.export }));

    expect(screen.getByRole("dialog", { name: en.export.title })).toBeVisible();
  });
});
