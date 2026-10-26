import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { en } from "../../strings/en";
import { errorReply, fakeServer } from "../../test/fakeServer";
import {
  classifierView,
  compositeView,
  id,
  memberRecord,
  modelStatusView,
  photoRecord,
  procedureView,
} from "../../test/records";
import { renderWithClient } from "../../test/render";
import { CaseScreen } from "./CaseScreen";

const caseId = id(100);

beforeEach(() => {
  window.history.replaceState(null, "", `/cases/${caseId}`);
});

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("CaseScreen", () => {
  it("a case that is gone offers the way back", async () => {
    fakeServer({
      "GET /api/cases/:case_id": () => errorReply(404, "case_not_found", "No case has this id."),
    });
    renderWithClient(<CaseScreen caseId={caseId} />);

    expect(await screen.findByText(en.case.missing)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: en.app.backToLibrary })).toHaveAttribute("href", "/");
  });

  it("shows why a case could not load, with a way to try again", async () => {
    const server = fakeServer({
      "GET /api/cases/:case_id": () => errorReply(500, "internal", "The case is unavailable."),
    });
    const user = userEvent.setup();
    renderWithClient(<CaseScreen caseId={caseId} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("The case is unavailable.");
    server.on("GET /api/cases/:case_id", () => procedureView([photoRecord()]));
    await user.click(screen.getByRole("button", { name: en.case.retry }));
    expect(await screen.findByRole("heading", { name: "Case" })).toBeInTheDocument();
  });

  it("a composite case opens the composite board", async () => {
    fakeServer({
      "GET /api/cases/:case_id": () =>
        compositeView([memberRecord({ id: id(1), slot: "upper_left" })]),
      "GET /api/model/status": () => modelStatusView({ available: true }),
      "GET /api/classifier/status": () => classifierView({ trained: true }),
    });
    renderWithClient(<CaseScreen caseId={caseId} />);

    expect(await screen.findByRole("group", { name: en.slots.upper_left })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: en.composite.preview })).toBeInTheDocument();
  });

  it("deleting asks first and returns to the library", async () => {
    const server = fakeServer({
      "GET /api/cases/:case_id": () => procedureView([photoRecord()]),
      "DELETE /api/cases/:case_id": () => undefined,
    });
    const user = userEvent.setup();
    renderWithClient(<CaseScreen caseId={caseId} />);

    await user.click(await screen.findByRole("button", { name: en.case.header.delete }));
    const dialog = screen.getByRole("dialog", { name: en.case.header.deleteTitle });
    expect(server.calls.filter((call) => call.method === "DELETE")).toHaveLength(0);

    await user.click(within(dialog).getByRole("button", { name: en.case.header.deleteConfirm }));
    await waitFor(() => {
      expect(window.location.pathname).toBe("/");
    });
    expect(server.calls.filter((call) => call.method === "DELETE").map((c) => c.path)).toEqual([
      `/api/cases/${caseId}`,
    ]);
  });

  it("a title with $& is shown and sent as typed", async () => {
    const tricky = "Crown $& $1";
    const server = fakeServer({
      "GET /api/cases/:case_id": () => procedureView([photoRecord()]),
      "PATCH /api/cases/:case_id": (request) =>
        procedureView([photoRecord()], {
          case: { title: (request.body as { title: string }).title },
        }),
    });
    const user = userEvent.setup();
    renderWithClient(<CaseScreen caseId={caseId} />);

    await user.click(await screen.findByRole("button", { name: en.case.header.rename }));
    const field = screen.getByRole("textbox", { name: en.case.header.titleLabel });
    await user.clear(field);
    await user.type(field, tricky);
    await user.click(screen.getByRole("button", { name: en.case.header.save }));

    expect(await screen.findByRole("heading", { name: tricky })).toBeInTheDocument();
    expect(server.calls.find((call) => call.method === "PATCH")?.body).toEqual({ title: tricky });
  });

  it("the delete dialog names the case as typed", async () => {
    fakeServer({
      "GET /api/cases/:case_id": () =>
        procedureView([photoRecord()], { case: { title: "Crown $& done" } }),
    });
    const user = userEvent.setup();
    renderWithClient(<CaseScreen caseId={caseId} />);

    await user.click(await screen.findByRole("button", { name: en.case.header.delete }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Crown $& done moves to the .trash");
  });
});
