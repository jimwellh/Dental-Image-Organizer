import { describe, expect, it, vi } from "vitest";
import { FakeEventSource } from "../test/fakeEventSource";
import { errorReply, fakeServer } from "../test/fakeServer";
import { id, jobView } from "../test/records";
import { exportResult, followJob, patientExportResult } from "./jobs";
import type { JobView } from "./types";

function follow(jobId = id(300)) {
  const onUpdate = vi.fn<(job: JobView) => void>();
  const stop = followJob(jobId, onUpdate);
  const source = FakeEventSource.last;
  if (source === null) throw new Error("followJob opened no EventSource");
  return { onUpdate, stop, source };
}

describe("followJob", () => {
  it("opens the job's event stream", () => {
    const { source } = follow(id(7));
    expect(source.url).toBe(`/api/jobs/${id(7)}/events`);
  });

  it("reports each event and closes when the job is done", () => {
    const { onUpdate, source } = follow();
    source.emit("running", { done: 1, total: 4 });
    expect(source.closed).toBe(false);
    source.emit("done", { done: 4, total: 4 });
    expect(onUpdate.mock.calls.map(([job]) => job.state)).toEqual(["running", "done"]);
    expect(onUpdate.mock.calls[0]?.[0]).toMatchObject({ id: id(300), done: 1, total: 4 });
    expect(source.closed).toBe(true);
  });

  it("closes when the job fails", () => {
    const { onUpdate, source } = follow();
    source.emit("failed", { error: { code: "boom", message: "Broke." } });
    expect(onUpdate).toHaveBeenCalledTimes(1);
    expect(source.closed).toBe(true);
  });

  it("asks for the job once when the stream is refused", async () => {
    const server = fakeServer({
      "GET /api/jobs/:job_id": (request) =>
        jobView({ id: request.params.job_id, state: "done", done: 2, total: 2 }),
    });
    const { onUpdate, source } = follow();
    source.fail(2);
    source.fail(2);
    await vi.waitFor(() => {
      expect(onUpdate).toHaveBeenCalledTimes(1);
    });
    expect(onUpdate.mock.calls[0]?.[0]).toMatchObject({ id: id(300), state: "done" });
    expect(server.calls).toHaveLength(1);
  });

  it("reports a lost job when the server no longer knows it", async () => {
    fakeServer({
      "GET /api/jobs/:job_id": () => errorReply(404, "job_not_found", "No such job."),
    });
    const { onUpdate, source } = follow();
    source.fail(2);
    await vi.waitFor(() => {
      expect(onUpdate).toHaveBeenCalledTimes(1);
    });
    expect(onUpdate.mock.calls[0]?.[0]).toMatchObject({
      id: id(300),
      state: "failed",
      error: { code: "job_lost" },
    });
  });

  it("reports any other failure to ask as a failed job", async () => {
    fakeServer({
      "GET /api/jobs/:job_id": () => errorReply(500, "internal", "The app broke."),
    });
    const { onUpdate, source } = follow();
    source.fail(2);
    await vi.waitFor(() => {
      expect(onUpdate).toHaveBeenCalledTimes(1);
    });
    expect(onUpdate.mock.calls[0]?.[0]).toMatchObject({
      state: "failed",
      error: { code: "internal", message: "The app broke." },
    });
  });

  it("waits while the browser reconnects", async () => {
    const server = fakeServer();
    const { onUpdate, source } = follow();
    source.fail(0);
    await Promise.resolve();
    expect(server.calls).toHaveLength(0);
    expect(onUpdate).not.toHaveBeenCalled();
    expect(source.closed).toBe(false);
  });

  it("the returned function stops following", () => {
    const { onUpdate, stop, source } = follow();
    source.emit("running");
    stop();
    expect(source.closed).toBe(true);
    source.emit("done");
    expect(onUpdate).toHaveBeenCalledTimes(1);
  });

  it("does not report the answer to a refused stream after it was stopped", async () => {
    const server = fakeServer({ "GET /api/jobs/:job_id": () => jobView({ state: "done" }) });
    const { onUpdate, stop, source } = follow();
    source.fail(2);
    stop();
    await vi.waitFor(() => {
      expect(server.calls).toHaveLength(1);
    });
    await Promise.resolve();
    expect(onUpdate).not.toHaveBeenCalled();
  });
});

describe("exportResult", () => {
  const result = {
    case_id: id(100),
    files: ["exports/a.jpg"],
    failures: [{ photo_id: null, message: "Nothing to render." }],
    skipped: { pending: 1, failed: 0 },
    learning: { samples: 9, trained: true },
  };

  it("reads an export job and refuses anything else", () => {
    expect(exportResult(jobView({ state: "done", result }))).toEqual(result);
    expect(() => exportResult(jobView({ state: "done", result: { photos: [] } }))).toThrow(
      TypeError,
    );
    expect(() => exportResult(jobView({ state: "done", result: null }))).toThrow(TypeError);
  });

  it("reads a patient export", () => {
    const patient = {
      patient_id: id(200),
      cases: [result],
      failed_cases: [{ case_id: id(101), message: "Broke." }],
    };
    expect(patientExportResult(jobView({ state: "done", result: patient }))).toEqual(patient);
    expect(() => patientExportResult(jobView({ state: "done", result }))).toThrow(TypeError);
  });
});
