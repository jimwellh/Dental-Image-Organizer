import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { en } from "../../strings/en";
import { errorReply, fakeServer } from "../../test/fakeServer";
import { caseSummary, id, patientsView, patientView } from "../../test/records";
import { renderWithClient } from "../../test/render";
import { LibraryScreen } from "./LibraryScreen";

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

// A name stored in decomposed Unicode, as a Mac file system can hand it over.
const DECOMPOSED_CAFE = "Café Wang";

const wang = patientView({ id: id(1), name: DECOMPOSED_CAFE, folder: DECOMPOSED_CAFE });
const li = patientView({
  id: id(2),
  name: "Bo Li",
  note: "chart 12",
  folder: "Bo Li",
  cases: [caseSummary({ id: id(10), title: "implant", thumbnails: [id(50), id(51)] })],
});

const heading = (name: string) => screen.queryByRole("heading", { name });

function chooseArchive(container: HTMLElement) {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]');
  return userEvent.upload(input as HTMLInputElement, new File(["zip"], "implant.zip"));
}

const conflictReply = () =>
  errorReply(409, "case_exists", "This case is already in the library.", {
    case_id: id(10),
    title: "implant",
  });

describe("LibraryScreen", () => {
  it("filters patients by name or note, whatever the Unicode form", async () => {
    fakeServer({ "GET /api/patients": () => patientsView({ patients: [wang, li] }) });
    const user = userEvent.setup();
    renderWithClient(<LibraryScreen />);
    await screen.findByRole("heading", { name: "Bo Li" });

    await user.type(screen.getByRole("searchbox", { name: en.library.search }), "café");
    expect(heading(DECOMPOSED_CAFE)).toBeInTheDocument();
    expect(heading("Bo Li")).not.toBeInTheDocument();

    await user.clear(screen.getByRole("searchbox"));
    await user.type(screen.getByRole("searchbox"), "chart 12");
    expect(heading("Bo Li")).toBeInTheDocument();
    expect(heading(DECOMPOSED_CAFE)).not.toBeInTheDocument();
  });

  it("says so when nothing matches the search", async () => {
    fakeServer({ "GET /api/patients": () => patientsView({ patients: [li] }) });
    const user = userEvent.setup();
    renderWithClient(<LibraryScreen />);
    await screen.findByRole("heading", { name: "Bo Li" });

    await user.type(screen.getByRole("searchbox"), "zzz");
    expect(screen.getByText(en.library.noMatch)).toBeInTheDocument();
  });

  it("lists folders the library could not read", async () => {
    const problem = { folder: "X/20261016 a", message: "The file is damaged…" };
    fakeServer({ "GET /api/patients": () => patientsView({ problems: [problem] }) });
    renderWithClient(<LibraryScreen />);

    expect(await screen.findByText(problem.folder)).toBeInTheDocument();
    expect(screen.getByText(problem.message)).toBeInTheDocument();
  });

  it("shows why the library could not load, with a way to try again", async () => {
    const server = fakeServer({
      "GET /api/patients": () => errorReply(500, "internal", "The library is unavailable."),
    });
    const user = userEvent.setup();
    renderWithClient(<LibraryScreen />);

    expect(await screen.findByRole("alert")).toHaveTextContent("The library is unavailable.");
    server.on("GET /api/patients", () => patientsView({ patients: [li] }));
    await user.click(screen.getByRole("button", { name: en.library.retry }));
    expect(await screen.findByRole("heading", { name: "Bo Li" })).toBeInTheDocument();
  });

  it("case cards show review, processing, and failed counts", async () => {
    const busy = patientView({
      cases: [caseSummary({ title: "implant", needs_review: 2, pending: 1, failed: 1 })],
    });
    fakeServer({ "GET /api/patients": () => patientsView({ patients: [busy] }) });
    renderWithClient(<LibraryScreen />);

    expect(await screen.findByText("Needs review 2")).toBeInTheDocument();
    expect(screen.getByText("Processing 1")).toBeInTheDocument();
    expect(screen.getByText("Failed 1")).toBeInTheDocument();
  });

  it("shows at most four thumbnails, from the processed thumb size", async () => {
    const photos = [1, 2, 3, 4, 5].map((n) => id(60 + n));
    const full = patientView({
      cases: [caseSummary({ id: id(10), title: "implant", thumbnails: photos })],
    });
    fakeServer({ "GET /api/patients": () => patientsView({ patients: [full] }) });
    const { container } = renderWithClient(<LibraryScreen />);
    await screen.findByRole("link", { name: /implant/ });

    const pictures = container.querySelectorAll("img");
    expect(pictures).toHaveLength(4);
    expect(pictures[0]?.getAttribute("src")).toContain(
      `/api/cases/${id(10)}/photos/${photos[0] ?? ""}/image?variant=processed&size=thumb`,
    );
    expect(pictures[0]).toHaveAttribute("alt", "");
  });

  it("opening a card goes to the case", async () => {
    fakeServer({ "GET /api/patients": () => patientsView({ patients: [li] }) });
    const user = userEvent.setup();
    renderWithClient(<LibraryScreen />);

    await user.click(await screen.findByRole("link", { name: /implant/ }));
    expect(window.location.pathname).toBe(`/cases/${id(10)}`);
  });

  it("Export all opens the export for the patient", async () => {
    const server = fakeServer({
      "GET /api/patients": () => patientsView({ patients: [li] }),
      "POST /api/patients/:patient_id/export": () => ({ job_id: id(9) }),
    });
    const user = userEvent.setup();
    renderWithClient(<LibraryScreen />);

    await user.click(await screen.findByRole("button", { name: en.library.exportAll }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: en.export.start }));

    await waitFor(() => {
      expect(server.calls.map((call) => `${call.method} ${call.path}`)).toContain(
        `POST /api/patients/${li.id}/export`,
      );
    });
  });

  it("an archive already in the library asks to skip or copy", async () => {
    const policies: (string | null)[] = [];
    fakeServer({
      "GET /api/patients": () => patientsView(),
      "POST /api/archive/import": (request) => {
        const policy = request.query.get("on_conflict");
        policies.push(policy);
        if (policy === "ask") return conflictReply();
        return { case_id: id(11), patient_id: id(1), status: "imported", title: "implant" };
      },
    });
    const user = userEvent.setup();
    const { container } = renderWithClient(<LibraryScreen />);
    await chooseArchive(container);

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("implant");
    await user.click(within(dialog).getByRole("button", { name: en.library.import.copy }));

    expect(await screen.findByText("Imported implant")).toBeInTheDocument();
    expect(policies).toEqual(["ask", "copy"]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("skipping an archive that is already there says so", async () => {
    const policies: (string | null)[] = [];
    fakeServer({
      "GET /api/patients": () => patientsView(),
      "POST /api/archive/import": (request) => {
        const policy = request.query.get("on_conflict");
        policies.push(policy);
        if (policy === "ask") return conflictReply();
        return { case_id: id(10), patient_id: id(1), status: "skipped", title: "implant" };
      },
    });
    const user = userEvent.setup();
    const { container } = renderWithClient(<LibraryScreen />);
    await chooseArchive(container);

    await user.click(await screen.findByRole("button", { name: en.library.import.skip }));
    expect(await screen.findByText("Skipped implant")).toBeInTheDocument();
    expect(policies).toEqual(["ask", "skip"]);
  });

  it("shows an import that failed for another reason", async () => {
    fakeServer({
      "GET /api/patients": () => patientsView(),
      "POST /api/archive/import": () =>
        errorReply(400, "bad_archive", "This is not a case archive."),
    });
    const { container } = renderWithClient(<LibraryScreen />);
    await chooseArchive(container);

    expect(await screen.findByRole("alert")).toHaveTextContent("This is not a case archive.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("clearing a note sends null", async () => {
    const server = fakeServer({
      "GET /api/patients": () => patientsView({ patients: [li] }),
      "PATCH /api/patients/:patient_id": () => ({ ...li, note: null }),
    });
    const user = userEvent.setup();
    renderWithClient(<LibraryScreen />);

    await user.click(await screen.findByRole("button", { name: en.library.edit }));
    await user.clear(screen.getByLabelText(en.library.editPatient.note));
    await user.click(screen.getByRole("button", { name: en.library.editPatient.save }));

    await waitFor(() => {
      expect(server.calls.find((call) => call.method === "PATCH")?.body).toEqual({
        name: "Bo Li",
        note: null,
      });
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("opens the new case dialogs", async () => {
    fakeServer({ "GET /api/patients": () => patientsView() });
    const user = userEvent.setup();
    renderWithClient(<LibraryScreen />);

    await user.click(screen.getByRole("button", { name: en.library.newComposite }));
    expect(screen.getByRole("dialog", { name: en.library.newCase.titleComposite })).toBeVisible();
    expect(screen.getByLabelText(en.library.newCase.compositeLabel)).toBeInTheDocument();
  });

  it("deleting a patient asks first and sends one request", async () => {
    const server = fakeServer({
      "GET /api/patients": () => patientsView({ patients: [li] }),
      "DELETE /api/patients/:patient_id": () => undefined,
    });
    const user = userEvent.setup();
    renderWithClient(<LibraryScreen />);
    const deletes = () => server.calls.filter((call) => call.method === "DELETE");

    await user.click(await screen.findByRole("button", { name: en.library.delete }));
    const asked = screen.getByRole("dialog", { name: en.library.deletePatient.title });
    expect(asked).toHaveTextContent("Bo Li and its case move to the .trash folder");
    await user.click(within(asked).getByRole("button", { name: en.common.cancel }));
    expect(deletes()).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: en.library.delete }));
    const dialog = screen.getByRole("dialog", { name: en.library.deletePatient.title });
    await user.dblClick(
      within(dialog).getByRole("button", { name: en.library.deletePatient.confirm }),
    );

    await waitFor(() => {
      expect(deletes().map((call) => call.path)).toEqual([`/api/patients/${li.id}`]);
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("the delete dialog counts the cases", async () => {
    const two = patientView({
      id: id(3),
      name: "Mei Chen",
      folder: "Mei Chen",
      cases: [caseSummary({ id: id(11) }), caseSummary({ id: id(12) })],
    });
    fakeServer({ "GET /api/patients": () => patientsView({ patients: [wang, two] }) });
    const user = userEvent.setup();
    renderWithClient(<LibraryScreen />);
    const deleteIn = async (name: string) => {
      const section = await screen.findByRole("region", { name });
      await user.click(within(section).getByRole("button", { name: en.library.delete }));
      return screen.getByRole("dialog", { name: en.library.deletePatient.title });
    };

    expect(await deleteIn("Mei Chen")).toHaveTextContent("Mei Chen and its 2 cases move");
    await user.click(screen.getByRole("button", { name: en.common.cancel }));
    const none = await deleteIn(DECOMPOSED_CAFE);
    expect(none).toHaveTextContent(`${DECOMPOSED_CAFE} moves to the .trash folder`);
    expect(none).not.toHaveTextContent("photos stay");
  });
});
