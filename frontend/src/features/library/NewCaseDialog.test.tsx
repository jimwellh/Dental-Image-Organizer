import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { queryKeys } from "../../api/queries";
import { en } from "../../strings/en";
import { fakeServer, type FakeServer } from "../../test/fakeServer";
import { id, patientView, procedureView } from "../../test/records";
import { renderWithClient } from "../../test/render";
import { NewCaseDialog } from "./NewCaseDialog";

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

const li = patientView({ id: id(2), name: "Bo Li", note: "chart 12" });
const wang = patientView({ id: id(1), name: "Alex Wang" });

function serve() {
  return fakeServer({ "POST /api/cases": () => procedureView([], { case: { id: id(77) } }) });
}

const created = (server: FakeServer) =>
  server.calls.find((call) => call.method === "POST" && call.path === "/api/cases");

describe("NewCaseDialog", () => {
  it("suggests existing patients with their notes", async () => {
    serve();
    const user = userEvent.setup();
    renderWithClient(<NewCaseDialog type="procedure" patients={[wang, li]} onClose={vi.fn()} />);

    await user.type(screen.getByLabelText(en.library.newCase.patientName), "bo");

    expect(screen.getByText("Bo Li")).toBeInTheDocument();
    expect(screen.getByText("chart 12")).toBeInTheDocument();
    expect(screen.queryByText("Alex Wang")).not.toBeInTheDocument();
  });

  it("offers at most eight suggestions", async () => {
    serve();
    const many = Array.from({ length: 10 }, (_, n) =>
      patientView({ id: id(100 + n), name: `Lee ${String(n)}` }),
    );
    const user = userEvent.setup();
    renderWithClient(<NewCaseDialog type="procedure" patients={many} onClose={vi.fn()} />);

    await user.type(screen.getByLabelText(en.library.newCase.patientName), "lee");
    expect(screen.getAllByRole("button", { name: /^Lee/ })).toHaveLength(8);
  });

  it("labels the title by case type", () => {
    serve();
    const { unmount } = renderWithClient(
      <NewCaseDialog type="procedure" patients={[]} onClose={vi.fn()} />,
    );
    expect(screen.getByLabelText(en.library.newCase.procedureLabel)).toBeInTheDocument();
    unmount();
    renderWithClient(<NewCaseDialog type="composite" patients={[]} onClose={vi.fn()} />);
    expect(screen.getByLabelText(en.library.newCase.compositeLabel)).toBeInTheDocument();
  });

  it("uses a picked suggestion without asking", async () => {
    const server = serve();
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithClient(<NewCaseDialog type="procedure" patients={[wang, li]} onClose={onClose} />);

    await user.type(screen.getByLabelText(en.library.newCase.patientName), "bo");
    await user.click(screen.getByRole("button", { name: /Bo Li/ }));
    await user.type(screen.getByLabelText(en.library.newCase.procedureLabel), "implant");
    await user.click(screen.getByRole("button", { name: en.library.newCase.create }));

    await waitFor(() => {
      expect(created(server)?.body).toEqual({
        type: "procedure",
        title: "implant",
        patient_id: li.id,
      });
    });
    await waitFor(() => {
      expect(window.location.pathname).toBe(`/cases/${id(77)}`);
    });
    expect(onClose).toHaveBeenCalled();
  });

  it("creates a new patient when the name matches nobody", async () => {
    const server = serve();
    const user = userEvent.setup();
    const { client } = renderWithClient(
      <NewCaseDialog type="composite" patients={[li]} onClose={vi.fn()} />,
    );
    const invalidated = vi.spyOn(client, "invalidateQueries");

    await user.type(screen.getByLabelText(en.library.newCase.patientName), " Cy Chen ");
    await user.type(screen.getByLabelText(en.library.newCase.compositeLabel), "baseline");
    await user.click(screen.getByRole("button", { name: en.library.newCase.create }));

    await waitFor(() => {
      expect(created(server)?.body).toEqual({
        type: "composite",
        title: "baseline",
        patient_name: "Cy Chen",
      });
    });
    await waitFor(() => {
      expect(invalidated).toHaveBeenCalledWith({ queryKey: queryKeys.patients });
    });
  });

  it("a name that matches an existing patient asks which one", async () => {
    const server = serve();
    const user = userEvent.setup();
    renderWithClient(<NewCaseDialog type="procedure" patients={[wang, li]} onClose={vi.fn()} />);

    await user.type(screen.getByLabelText(en.library.newCase.patientName), "Bo Li");
    await user.type(screen.getByLabelText(en.library.newCase.procedureLabel), "implant");
    await user.click(screen.getByRole("button", { name: en.library.newCase.create }));

    expect(created(server)).toBeUndefined();
    expect(screen.getByText("chart 12")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: en.library.newCase.usePatient }));

    await waitFor(() => {
      expect(created(server)?.body).toEqual({
        type: "procedure",
        title: "implant",
        patient_id: li.id,
      });
    });
  });

  it("or creates a new patient with a note", async () => {
    const server = serve();
    const user = userEvent.setup();
    renderWithClient(<NewCaseDialog type="procedure" patients={[li]} onClose={vi.fn()} />);

    // Another case and spacing still count as the same name.
    await user.type(screen.getByLabelText(en.library.newCase.patientName), "bo  LI");
    await user.type(screen.getByLabelText(en.library.newCase.procedureLabel), "implant");
    await user.click(screen.getByRole("button", { name: en.library.newCase.create }));
    await user.type(screen.getByLabelText(en.library.newCase.newPatientNote), "second");
    await user.click(screen.getByRole("button", { name: en.library.newCase.newPatient }));

    await waitFor(() => {
      expect(created(server)?.body).toEqual({
        type: "procedure",
        title: "implant",
        patient_name: "bo  LI",
        patient_note: "second",
      });
    });
  });

  it("a double click creates one case", async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const server = fakeServer({
      "POST /api/cases": async () => {
        await held;
        return procedureView([], { case: { id: id(77) } });
      },
    });
    const user = userEvent.setup();
    renderWithClient(<NewCaseDialog type="procedure" patients={[]} onClose={vi.fn()} />);

    await user.type(screen.getByLabelText(en.library.newCase.patientName), "Cy Chen");
    await user.type(screen.getByLabelText(en.library.newCase.procedureLabel), "implant");
    await user.dblClick(screen.getByRole("button", { name: en.library.newCase.create }));
    release();

    await waitFor(() => {
      expect(window.location.pathname).toBe(`/cases/${id(77)}`);
    });
    expect(server.calls.filter((call) => call.method === "POST")).toHaveLength(1);
  });
});
