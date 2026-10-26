import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ModelStatus } from "../../api/types";
import { en } from "../../strings/en";
import { FakeEventSource } from "../../test/fakeEventSource";
import { fakeServer } from "../../test/fakeServer";
import { classifierView, id, modelStatusView } from "../../test/records";
import { renderWithClient } from "../../test/render";
import { ModelConsent } from "./ModelConsent";

const JOB = id(500);

function serve(status: ModelStatus) {
  const state = { status };
  const server = fakeServer({
    "GET /api/model/status": () => state.status,
    "GET /api/classifier/status": () => classifierView(),
    "POST /api/model/download": () => ({ job_id: JOB }),
  });
  return { server, state };
}

const count = (server: ReturnType<typeof serve>["server"], method: string, path: string) =>
  server.calls.filter((call) => call.method === method && call.path === path).length;

describe("ModelConsent", () => {
  it("asks before downloading and shows progress", async () => {
    const { server, state } = serve(modelStatusView());
    const user = userEvent.setup();
    renderWithClient(<ModelConsent />);

    expect(await screen.findByText("Download AI model (~25 MB) from GitHub?")).toBeVisible();
    expect(count(server, "POST", "/api/model/download")).toBe(0);

    await user.click(screen.getByRole("button", { name: en.ai.download }));
    await waitFor(() => {
      expect(count(server, "POST", "/api/model/download")).toBe(1);
    });
    await waitFor(() => {
      expect(FakeEventSource.last).not.toBeNull();
    });
    expect(FakeEventSource.last?.url).toContain(`/jobs/${JOB}/events`);

    FakeEventSource.last?.emit("running", { done: 5e6, total: 25e6 });
    expect(await screen.findByText("20%")).toBeVisible();

    const before = count(server, "GET", "/api/model/status");
    state.status = modelStatusView({ available: true });
    FakeEventSource.last?.emit("done");
    await waitFor(() => {
      expect(count(server, "GET", "/api/model/status")).toBe(before + 1);
    });
  });

  it("names a host other than GitHub as it is", async () => {
    serve(modelStatusView({ host: "models.example.org", size_bytes: 12_600_000 }));
    renderWithClient(<ModelConsent />);

    expect(
      await screen.findByText("Download AI model (~13 MB) from models.example.org?"),
    ).toBeVisible();
  });

  it("declining keeps the capture-order note", async () => {
    const { server } = serve(modelStatusView());
    const user = userEvent.setup();
    renderWithClient(<ModelConsent />);

    await user.click(await screen.findByRole("button", { name: en.ai.notNow }));

    expect(screen.getByText(en.ai.declined)).toBeVisible();
    expect(count(server, "POST", "/api/model/download")).toBe(0);
  });

  it("follows a download that is already running", async () => {
    const { server } = serve(modelStatusView({ download_job_id: "j" }));
    renderWithClient(<ModelConsent />);

    await waitFor(() => {
      expect(FakeEventSource.last).not.toBeNull();
    });
    expect(FakeEventSource.last?.url).toContain("/jobs/j/events");
    FakeEventSource.last?.emit("running", { done: 10e6, total: 25e6 });

    expect(await screen.findByText("40%")).toBeVisible();
    expect(count(server, "POST", "/api/model/download")).toBe(0);
    expect(screen.queryByRole("button", { name: en.ai.download })).toBeNull();
  });

  it("a failed download shows why and offers another try", async () => {
    const { server } = serve(modelStatusView());
    const user = userEvent.setup();
    renderWithClient(<ModelConsent />);

    await user.click(await screen.findByRole("button", { name: en.ai.download }));
    await waitFor(() => {
      expect(FakeEventSource.last).not.toBeNull();
    });
    FakeEventSource.last?.emit("failed", {
      error: { code: "download_failed", message: "No route." },
    });

    expect(
      await screen.findByText("The AI model could not be downloaded: No route."),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: en.ai.retry }));
    await waitFor(() => {
      expect(count(server, "POST", "/api/model/download")).toBe(2);
    });
  });

  it("shows nothing once the model is there", async () => {
    const { server } = serve(modelStatusView({ available: true }));
    renderWithClient(<ModelConsent />);

    await waitFor(() => {
      expect(count(server, "GET", "/api/model/status")).toBe(1);
    });
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText(en.ai.consent(25, "GitHub"))).toBeNull();
  });
});
