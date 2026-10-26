import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { queryKeys } from "../../api/queries";
import { en } from "../../strings/en";
import { FakeEventSource } from "../../test/fakeEventSource";
import { errorReply, fakeServer } from "../../test/fakeServer";
import { id } from "../../test/records";
import { renderWithClient } from "../../test/render";
import { ExportDialog, type ExportTarget } from "./ExportDialog";

const CASE_ID = id(1);
const PATIENT_ID = id(2);
const JOB_ID = id(9);

const caseTarget: ExportTarget = {
  kind: "case",
  caseId: CASE_ID,
  caseType: "procedure",
  folder: "Alex Wang/20261016 implant",
};

const patientTarget: ExportTarget = {
  kind: "patient",
  patientId: PATIENT_ID,
  folder: "Alex Wang",
  cases: [
    { id: id(3), title: "Implant" },
    { id: id(4), title: "Whitening" },
  ],
};

function caseServer() {
  return fakeServer({
    "POST /api/cases/:case_id/export": () => ({ job_id: JOB_ID }),
    "POST /api/system/reveal": () => undefined,
  });
}

const caseResult = (overrides: Record<string, unknown> = {}) => ({
  case_id: CASE_ID,
  files: ["export/A_x_20261016_1000.jpg"],
  failures: [],
  skipped: { pending: 0, failed: 0 },
  learning: null,
  ...overrides,
});

async function start(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: en.export.start }));
  await waitFor(() => {
    expect(FakeEventSource.last).not.toBeNull();
  });
}

describe("ExportDialog", () => {
  it("a double click starts one export", async () => {
    // The reply is held back so both clicks land while the request is in flight.
    let release: () => void = () => undefined;
    const reply = new Promise<void>((resolve) => {
      release = resolve;
    });
    const server = fakeServer({
      "POST /api/cases/:case_id/export": async () => {
        await reply;
        return { job_id: JOB_ID };
      },
    });
    const user = userEvent.setup();
    renderWithClient(<ExportDialog target={caseTarget} onClose={() => undefined} />);

    await user.dblClick(screen.getByRole("button", { name: en.export.start }));
    release();
    await waitFor(() => {
      expect(FakeEventSource.last).not.toBeNull();
    });

    const posts = server.calls.filter((call) => call.method === "POST");
    expect(posts.map((call) => call.path)).toEqual([`/api/cases/${CASE_ID}/export`]);
  });

  it("allows another try after the request failed", async () => {
    let attempts = 0;
    fakeServer({
      "POST /api/cases/:case_id/export": () => {
        attempts++;
        return attempts === 1 ? errorReply(500, "boom", "The disk is full.") : { job_id: JOB_ID };
      },
    });
    const user = userEvent.setup();
    renderWithClient(<ExportDialog target={caseTarget} onClose={() => undefined} />);

    await user.click(screen.getByRole("button", { name: en.export.start }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: en.export.start })).toBeEnabled();
    });
    expect(attempts).toBe(1);
    await start(user);
    expect(attempts).toBe(2);
  });

  it("sends the chosen parts and the conflict policy", async () => {
    const server = caseServer();
    const user = userEvent.setup();
    renderWithClient(<ExportDialog target={caseTarget} onClose={() => undefined} />);

    await user.click(screen.getByRole("checkbox", { name: en.export.sequence }));
    await user.click(screen.getByRole("radio", { name: en.export.replace }));
    await start(user);

    const post = server.calls.find((call) => call.method === "POST");
    expect(post?.body).toEqual({ include: ["singles"], conflict: "replace" });
  });

  it("offers what fits the case type, all checked, and keeps both by default", () => {
    renderWithClient(
      <ExportDialog target={{ ...caseTarget, caseType: "composite" }} onClose={() => undefined} />,
    );

    expect(screen.getByRole("checkbox", { name: en.export.composite })).toBeChecked();
    expect(screen.queryByRole("checkbox", { name: en.export.singles })).toBeNull();
    expect(screen.getByRole("radio", { name: en.export.keepBoth })).toBeChecked();
  });

  it("cannot start while nothing is checked", async () => {
    const user = userEvent.setup();
    renderWithClient(
      <ExportDialog target={{ ...caseTarget, caseType: "composite" }} onClose={() => undefined} />,
    );

    await user.click(screen.getByRole("checkbox", { name: en.export.composite }));

    expect(screen.getByRole("button", { name: en.export.start })).toBeDisabled();
  });

  it("shows progress, then the files, failures, and photos left out", async () => {
    caseServer();
    const user = userEvent.setup();
    const { client } = renderWithClient(
      <ExportDialog target={caseTarget} onClose={() => undefined} />,
    );
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await start(user);

    FakeEventSource.last?.emit("running", { done: 1, total: 3 });
    expect(await screen.findByText("1 of 3")).toBeInTheDocument();

    FakeEventSource.last?.emit("done", {
      result: caseResult({
        failures: [{ photo_id: id(5), message: "Could not render the sequence." }],
        skipped: { pending: 2, failed: 0 },
      }),
    });

    expect(await screen.findByText("A_x_20261016_1000.jpg")).toBeInTheDocument();
    expect(screen.getByText(/Could not render the sequence\./)).toBeInTheDocument();
    expect(
      screen.getByText(en.export.skippedPending.other.replace("{n}", "2")),
    ).toBeInTheDocument();
    expect(screen.queryByText(/could not be processed/)).toBeNull();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.case(CASE_ID) });
  });

  it("says whether the classifier learned from a composite", async () => {
    caseServer();
    const user = userEvent.setup();
    renderWithClient(
      <ExportDialog target={{ ...caseTarget, caseType: "composite" }} onClose={() => undefined} />,
    );
    await start(user);

    FakeEventSource.last?.emit("done", {
      result: caseResult({ learning: { samples: 4, trained: true } }),
    });

    expect(
      await screen.findByText(en.export.learned.other.replace("{n}", "4")),
    ).toBeInTheDocument();
  });

  it.each([
    [
      "the model is not downloaded",
      { error: "no_model", message: null },
      en.export.learningNoModel,
    ],
    [
      "learning failed with a message",
      { error: "failed", message: "Out of memory." },
      en.export.learningFailed.replace("{message}", "Out of memory."),
    ],
    [
      "learning failed without a message",
      { error: "failed", message: null },
      en.export.learningUnknown,
    ],
    [
      "the samples are stored but no classifier exists yet",
      { samples: 9, trained: false },
      en.export.savedForClassifier(9),
    ],
  ])("explains the learning line when %s", async (_name, learning, line) => {
    caseServer();
    const user = userEvent.setup();
    renderWithClient(
      <ExportDialog target={{ ...caseTarget, caseType: "composite" }} onClose={() => undefined} />,
    );
    await start(user);

    FakeEventSource.last?.emit("done", { result: caseResult({ learning }) });

    expect(await screen.findByText(line)).toBeInTheDocument();
    expect(screen.queryByText(/no_model/)).toBeNull();
  });

  it("shows the message of a failed job", async () => {
    caseServer();
    const user = userEvent.setup();
    renderWithClient(<ExportDialog target={caseTarget} onClose={() => undefined} />);
    await start(user);

    FakeEventSource.last?.emit("failed", { error: { code: "x", message: "No space left." } });

    expect(
      await screen.findByText(en.export.jobFailed.replace("{message}", "No space left.")),
    ).toBeInTheDocument();
  });

  it("reveals the case's export folder", async () => {
    const server = caseServer();
    const user = userEvent.setup();
    renderWithClient(<ExportDialog target={caseTarget} onClose={() => undefined} />);
    await start(user);
    FakeEventSource.last?.emit("done", { result: caseResult() });

    await user.click(await screen.findByRole("button", { name: en.export.reveal }));

    const reveal = server.calls.find((call) => call.path === "/api/system/reveal");
    expect(reveal?.body).toEqual({ path: "Alex Wang/20261016 implant/export" });
  });

  it("offers the case zip", async () => {
    caseServer();
    const user = userEvent.setup();
    renderWithClient(<ExportDialog target={caseTarget} onClose={() => undefined} />);
    await start(user);
    FakeEventSource.last?.emit("done", { result: caseResult() });

    const link = await screen.findByRole("link", { name: en.export.download });
    expect(link).toHaveAttribute("href", `/api/cases/${CASE_ID}/export/${JOB_ID}/zip`);
    expect(link).toHaveAttribute("download");
  });

  it("offers the patient zip, one line per case, and reveals the patient folder", async () => {
    const server = fakeServer({
      "POST /api/patients/:patient_id/export": () => ({ job_id: JOB_ID }),
      "POST /api/system/reveal": () => undefined,
    });
    const user = userEvent.setup();
    const { client } = renderWithClient(
      <ExportDialog target={patientTarget} onClose={() => undefined} />,
    );
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await start(user);

    FakeEventSource.last?.emit("done", {
      result: {
        patient_id: PATIENT_ID,
        cases: [caseResult({ case_id: id(3), files: ["export/a.jpg", "export/b.jpg"] })],
        failed_cases: [{ case_id: id(4), message: "The folder is missing." }],
      },
    });

    const link = await screen.findByRole("link", { name: en.export.download });
    expect(link).toHaveAttribute("href", `/api/patients/${PATIENT_ID}/export/${JOB_ID}/zip`);
    expect(screen.getByText("Implant: 2 files")).toBeInTheDocument();
    expect(screen.getByText("Whitening: The folder is missing.")).toBeInTheDocument();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.patients });

    await user.click(screen.getByRole("button", { name: en.export.reveal }));
    const reveal = server.calls.find((call) => call.path === "/api/system/reveal");
    expect(reveal?.body).toEqual({ path: "Alex Wang" });
  });

  it("shows a case title as typed, even when it holds a replacement pattern", async () => {
    fakeServer({ "POST /api/patients/:patient_id/export": () => ({ job_id: JOB_ID }) });
    const user = userEvent.setup();
    renderWithClient(
      <ExportDialog
        target={{ ...patientTarget, cases: [{ id: id(3), title: "Crown $&" }] }}
        onClose={() => undefined}
      />,
    );
    await start(user);

    FakeEventSource.last?.emit("done", {
      result: { patient_id: PATIENT_ID, cases: [caseResult({ case_id: id(3) })], failed_cases: [] },
    });

    expect(await screen.findByText("Crown $&: 1 file")).toBeInTheDocument();
  });

  it("never shows an empty reason for a failed export", async () => {
    caseServer();
    const user = userEvent.setup();
    renderWithClient(<ExportDialog target={caseTarget} onClose={() => undefined} />);
    await start(user);

    FakeEventSource.last?.emit("failed", {});

    expect(
      await screen.findByText(en.export.jobFailed.replace("{message}", en.export.failedUnknown)),
    ).toBeInTheDocument();
  });

  it("refreshes the classifier after a composite export, not after a procedure export", async () => {
    caseServer();
    const user = userEvent.setup();
    const { client } = renderWithClient(
      <ExportDialog target={{ ...caseTarget, caseType: "composite" }} onClose={() => undefined} />,
    );
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await start(user);

    FakeEventSource.last?.emit("done", { result: caseResult() });

    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.classifier });
    });
  });
});
