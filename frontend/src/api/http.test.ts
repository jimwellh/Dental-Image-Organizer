import { en } from "../strings/en";
import { errorReply, fakeServer } from "../test/fakeServer";
import { ApiError, apiPath, describeError, formWithFiles, getJson, sendJson } from "./http";

describe("http", () => {
  test("getJson returns the parsed body", async () => {
    fakeServer({ "GET /api/health": () => ({ status: "ok", version: "1" }) });

    await expect(getJson("/api/health")).resolves.toEqual({ status: "ok", version: "1" });
  });

  test("an error body becomes an ApiError", async () => {
    fakeServer({ "GET /api/health": () => errorReply(409, "job_running", "Busy.") });

    const failure = await getJson("/api/health").catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 409, code: "job_running", message: "Busy." });
  });

  test("a response that is not an error body becomes an http_<status> error", async () => {
    fakeServer({ "GET /api/health": () => new Response("<html>", { status: 502 }) });

    await expect(getJson("/api/health")).rejects.toMatchObject({
      status: 502,
      code: "http_502",
      message: en.errors.unexpected,
    });
  });

  test("a failed fetch becomes a network error", async () => {
    fakeServer({
      "GET /api/health": () => {
        throw new Error("connection refused");
      },
    });

    await expect(getJson("/api/health")).rejects.toMatchObject({
      status: 0,
      code: "network",
      message: en.errors.network,
    });
  });

  test("a 204 resolves to undefined", async () => {
    fakeServer({ "POST /api/heartbeat": () => undefined });

    await expect(sendJson("POST", "/api/heartbeat")).resolves.toBeUndefined();
  });

  test("sendJson sends JSON and never an Origin header", async () => {
    const server = fakeServer({ "PATCH /api/echo": (request) => request.body });

    await expect(sendJson("PATCH", "/api/echo", { reset: true })).resolves.toEqual({ reset: true });

    const [, init] = vi.mocked(fetch).mock.calls[0] ?? [];
    const headers = new Headers(init?.headers);
    expect(headers.get("content-type")).toBe("application/json");
    expect(headers.has("origin")).toBe(false);
    expect(server.calls[0]?.body).toEqual({ reset: true });
  });

  test("apiPath fills and encodes the path", () => {
    expect(
      apiPath(
        "/api/cases/{case_id}/photos/{photo_id}/image",
        { case_id: "a b", photo_id: "x/y" },
        { size: "thumb", v: undefined },
      ),
    ).toBe("/api/cases/a%20b/photos/x%2Fy/image?size=thumb");
  });

  test("apiPath refuses a missing path parameter", () => {
    expect(() => apiPath("/api/cases/{case_id}")).toThrow(/case_id/);
  });

  test("formWithFiles sends one last_modified per file", () => {
    const first = new File(["a"], "a.jpg", { lastModified: 1000 });
    const second = new File(["b"], "b.jpg", { lastModified: 2000 });

    const form = formWithFiles([first, second]);

    expect(form.getAll("files")).toHaveLength(2);
    expect(form.getAll("last_modified")).toEqual(["1000", "2000"]);
  });

  test("describeError names the first refused field", () => {
    const error = new ApiError(422, "invalid_request", "The request is not valid.", [
      { loc: ["body", "jpeg_quality"], msg: "Input should be less than or equal to 100" },
    ]);

    expect(describeError(error)).toBe(
      "The request is not valid. jpeg_quality: Input should be less than or equal to 100",
    );
  });

  test("describeError falls back to the message, then to the generic text", () => {
    expect(describeError(new ApiError(409, "job_running", "Busy."))).toBe("Busy.");
    expect(describeError(new ApiError(422, "invalid_request", "Bad.", [{}]))).toBe("Bad.");
    expect(describeError(new TypeError("boom"))).toBe(en.errors.unexpected);
  });
});
