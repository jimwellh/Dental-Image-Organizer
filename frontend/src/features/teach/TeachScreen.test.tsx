import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type {
  ClassifierView,
  Flip,
  ModelStatus,
  Settings,
  Slot,
  TeachPhotoView,
  TeachSessionView,
  TeachUpload,
} from "../../api/types";
import { SLOTS } from "../../lib/slots";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { fakeFile, fakeTransfer, fileEntry } from "../../test/fakeDrop";
import { FakeEventSource } from "../../test/fakeEventSource";
import { fakeServer } from "../../test/fakeServer";
import { classifierView, id, modelStatusView, settingsRecord } from "../../test/records";
import { renderWithClient } from "../../test/render";
import { TeachScreen } from "./TeachScreen";

const SESSION_ID = id(500);
const JOB_ID = id(501);

const photo = (n: number, overrides: Partial<TeachPhotoView> = {}): TeachPhotoView => ({
  id: id(10 + n),
  name: `IMG_000${String(n)}.jpg`,
  captured_at: "2026-10-16T10:00:00",
  state: "ready",
  error: null,
  ...overrides,
});

// Three placed by the proposal, one left over, and one that could not be read.
const readySession: TeachSessionView = {
  id: SESSION_ID,
  job_id: JOB_ID,
  state: "ready",
  photos: [
    photo(1),
    photo(2),
    photo(3),
    photo(4),
    photo(5, { state: "failed", error: "Not an image." }),
  ],
  proposal: { upper_right: id(11), upper_occlusal: id(12), upper_left: id(13) },
};

// The flip each slot would get with no choice made.
const flipStatus = (effective: Flip) => ({
  confirmations: 0,
  effective,
  learned: null,
  pinned: false,
  start: effective,
});
const flips = Object.fromEntries(SLOTS.map((slot) => [slot, flipStatus("horizontal")]));

interface Options {
  model?: Partial<ModelStatus>;
  classifier?: Partial<ClassifierView>;
  settings?: Settings;
  session?: TeachSessionView;
  upload?: TeachUpload;
}

function serve({
  model,
  classifier,
  settings = settingsRecord(),
  session = readySession,
  upload = { session_id: SESSION_ID, job_id: JOB_ID, skipped: [] },
}: Options = {}) {
  return fakeServer({
    "GET /api/model/status": () => modelStatusView({ available: true, ...model }),
    "GET /api/classifier/status": () =>
      classifierView({ model_available: true, flips, ...classifier }),
    "GET /api/settings": () => settings,
    "PUT /api/settings": (request) => request.body,
    "POST /api/classifier/teach": () => upload,
    "GET /api/classifier/teach/:session_id": () => session,
    "POST /api/classifier/teach/:session_id/confirm": () =>
      classifierView({
        model_available: true,
        flips,
        counts: Object.fromEntries(SLOTS.map((slot) => [slot, 1])),
      }),
    "DELETE /api/classifier/teach/:session_id": () => undefined,
  });
}

interface TeachBody {
  slots: Record<string, string>;
  flips: Record<string, string>;
}

const dropZone = () => screen.findByRole("region", { name: en.teach.drop.label });

function dropFiles(zone: HTMLElement, names: string[]) {
  fireEvent.drop(zone, {
    dataTransfer: fakeTransfer(names.map((name) => fileEntry(fakeFile(name)))),
  });
}

/** Drops a set, lets the job finish, and waits for the board. */
async function reachBoard(names = ["IMG_0001.jpg"]) {
  dropFiles(await dropZone(), names);
  await waitFor(() => {
    expect(FakeEventSource.last).not.toBeNull();
  });
  FakeEventSource.last?.emit("done", { id: JOB_ID, done: names.length, total: names.length });
  await screen.findByRole("group", { name: en.slots.upper_right });
}

const calls = (server: ReturnType<typeof serve>, method: string, path: string) =>
  server.calls.filter((call) => call.method === method && call.path === path);

const confirmPath = `/api/classifier/teach/${SESSION_ID}/confirm`;
const sessionPath = `/api/classifier/teach/${SESSION_ID}`;

describe("TeachScreen", () => {
  it("asks for the model first", async () => {
    serve({ model: { available: false } });
    renderWithClient(<TeachScreen />);

    expect(await screen.findByText(en.ai.consent(25, "GitHub"))).toBeVisible();
    expect(screen.queryByRole("region", { name: en.teach.drop.label })).toBeNull();
    expect(screen.queryByRole("button", { name: en.teach.order.change })).toBeNull();
  });

  it("shows the progress and the count of each view", async () => {
    serve({ classifier: { counts: { upper_right: 2, lower_left: 1 } } });
    renderWithClient(<TeachScreen />);

    expect(await screen.findByText(en.teach.progress(1, 3))).toBeVisible();
    expect(
      screen.getByText(fill(en.teach.count, { slot: en.slots.upper_right, n: "2", of: "3" })),
    ).toBeVisible();
    expect(
      screen.getByText(fill(en.teach.count, { slot: en.slots.upper_left, n: "0", of: "3" })),
    ).toBeVisible();
  });

  it("says the classifier is trained once it is", async () => {
    serve({ classifier: { trained: true } });
    renderWithClient(<TeachScreen />);

    expect(await screen.findByText(en.teach.trained)).toBeVisible();
    expect(screen.queryByText(/^Set \d of/)).toBeNull();
  });

  it("one drop is one session", async () => {
    const server = serve();
    renderWithClient(<TeachScreen />);

    const names = Array.from({ length: 9 }, (_, n) => `IMG_010${String(n)}.jpg`);
    dropFiles(await dropZone(), [...names, "IMG_0100.MOV", ".DS_Store"]);

    await waitFor(() => {
      expect(calls(server, "POST", "/api/classifier/teach")).toHaveLength(1);
    });
    const form = calls(server, "POST", "/api/classifier/teach")[0]?.body as FormData;
    expect((form.getAll("files") as File[]).map((file) => file.name)).toEqual(names);
    expect(await screen.findByText("Skipped 1 file: 1 video")).toBeVisible();

    // The job's progress shows while the photos are read.
    await waitFor(() => {
      expect(FakeEventSource.last).not.toBeNull();
    });
    FakeEventSource.last?.emit("running", { id: JOB_ID, done: 3, total: 9 });
    expect(
      await screen.findByText(fill(en.teach.reading, { done: "3", total: "9" })),
    ).toBeVisible();
    expect(screen.queryByRole("region", { name: en.teach.drop.label })).toBeNull();
  });

  it("says so when the server could use nothing in the drop", async () => {
    const server = serve({ upload: { session_id: null, job_id: null, skipped: [] } });
    renderWithClient(<TeachScreen />);

    dropFiles(await dropZone(), ["IMG_0001.jpg"]);

    expect(await screen.findByText(en.teach.nothingUsable)).toBeVisible();
    expect(calls(server, "POST", "/api/classifier/teach")).toHaveLength(1);
    expect(screen.getByRole("region", { name: en.teach.drop.label })).toBeVisible();
  });

  it("does not ask the server when every file is filtered out", async () => {
    const server = serve();
    renderWithClient(<TeachScreen />);

    dropFiles(await dropZone(), ["IMG_0001.MOV"]);

    expect(await screen.findByText(en.teach.nothingUsable)).toBeVisible();
    expect(calls(server, "POST", "/api/classifier/teach")).toHaveLength(0);
  });

  it("confirming sends the board and the flips", async () => {
    const server = serve();
    const user = userEvent.setup();
    renderWithClient(<TeachScreen />);
    await reachBoard();

    // The proposal is on the board, the leftover in the tray, and the unreadable one listed.
    const upperRight = screen.getByRole("group", { name: en.slots.upper_right });
    expect(within(upperRight).getByRole("img", { name: "IMG_0001.jpg" })).toBeVisible();
    const tray = screen.getByRole("group", { name: en.board.tray });
    expect(within(tray).getByRole("img", { name: "IMG_0004.jpg" })).toBeVisible();
    expect(
      screen.getByText(
        fill(en.teach.failedItem, { name: "IMG_0005.jpg", message: "Not an image." }),
      ),
    ).toBeVisible();

    await user.selectOptions(
      screen.getByRole("combobox", { name: fill(en.board.moveTo, { name: "IMG_0004.jpg" }) }),
      en.slots.lower_left,
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: fill(en.teach.flip, { name: en.slots.lower_left }) }),
      en.flips.vertical,
    );
    await user.click(screen.getByRole("button", { name: en.teach.confirm }));

    await waitFor(() => {
      expect(calls(server, "POST", confirmPath)).toHaveLength(1);
    });
    const slots: Partial<Record<Slot, string>> = {
      upper_right: id(11),
      upper_occlusal: id(12),
      upper_left: id(13),
      lower_left: id(14),
    };
    expect(calls(server, "POST", confirmPath)[0]?.body).toEqual({
      slots,
      flips: {
        upper_right: "horizontal",
        upper_occlusal: "horizontal",
        upper_left: "horizontal",
        lower_left: "vertical",
      },
    });

    // The returned status moves the count on, and the next set can be dropped.
    expect(await screen.findByText(en.teach.progress(2, 3))).toBeVisible();
    expect(screen.getByRole("region", { name: en.teach.drop.label })).toBeVisible();
    expect(calls(server, "DELETE", sessionPath)).toHaveLength(0);
  });

  it("swapping two placed photos sends both new slots", async () => {
    const server = serve();
    const user = userEvent.setup();
    renderWithClient(<TeachScreen />);
    await reachBoard();

    await user.selectOptions(
      screen.getByRole("combobox", { name: fill(en.board.moveTo, { name: "IMG_0001.jpg" }) }),
      en.slots.upper_left,
    );
    await user.click(screen.getByRole("button", { name: en.teach.confirm }));

    await waitFor(() => {
      expect(calls(server, "POST", confirmPath)).toHaveLength(1);
    });
    expect((calls(server, "POST", confirmPath)[0]?.body as TeachBody).slots).toEqual({
      upper_right: id(13),
      upper_occlusal: id(12),
      upper_left: id(11),
    });
  });

  it("cancel deletes the session", async () => {
    const server = serve();
    const user = userEvent.setup();
    renderWithClient(<TeachScreen />);
    await reachBoard();

    await user.click(screen.getByRole("button", { name: en.common.cancel }));

    await waitFor(() => {
      expect(calls(server, "DELETE", sessionPath)).toHaveLength(1);
    });
    expect(await dropZone()).toBeVisible();
    expect(calls(server, "POST", confirmPath)).toHaveLength(0);
  });

  it("a job that failed offers a way back", async () => {
    const server = serve();
    const user = userEvent.setup();
    renderWithClient(<TeachScreen />);

    dropFiles(await dropZone(), ["IMG_0001.jpg"]);
    await waitFor(() => {
      expect(FakeEventSource.last).not.toBeNull();
    });
    FakeEventSource.last?.emit("failed", {
      id: JOB_ID,
      error: { code: "boom", message: "The model failed." },
    });

    expect(
      await screen.findByText(fill(en.teach.jobFailed, { message: "The model failed." })),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: en.common.cancel }));
    expect(await dropZone()).toBeVisible();
    expect(calls(server, "DELETE", sessionPath)).toHaveLength(1);
  });

  it("leaving the screen cancels an open session", async () => {
    const server = serve();
    const { unmount } = renderWithClient(<TeachScreen />);

    dropFiles(await dropZone(), ["IMG_0001.jpg"]);
    await screen.findByText(en.teach.readingStart);
    unmount();

    await waitFor(() => {
      expect(calls(server, "DELETE", sessionPath)).toHaveLength(1);
    });
  });

  it("leaving after confirming does not cancel anything", async () => {
    const server = serve();
    const user = userEvent.setup();
    const { unmount } = renderWithClient(<TeachScreen />);
    await reachBoard();
    await user.click(screen.getByRole("button", { name: en.teach.confirm }));
    await screen.findByText(en.teach.progress(2, 3));
    unmount();

    expect(calls(server, "DELETE", sessionPath)).toHaveLength(0);
  });

  it("a change of shooting order saves the whole settings", async () => {
    const settings = settingsRecord({ gap_px: 4, flip_pins: { upper_right: "vertical" } });
    const server = serve({ settings });
    const user = userEvent.setup();
    renderWithClient(<TeachScreen />);

    await user.click(await screen.findByRole("button", { name: en.teach.order.change }));
    const reversed = [...SLOTS].reverse();
    for (const slot of reversed) {
      await user.click(screen.getByRole("button", { name: new RegExp(`^${en.slots[slot]}`) }));
    }

    await waitFor(() => {
      expect(calls(server, "PUT", "/api/settings")).toHaveLength(1);
    });
    expect(calls(server, "PUT", "/api/settings")[0]?.body).toEqual({
      ...settings,
      shooting_order: reversed,
    });
    // The saved order is what the picker shows now.
    expect(
      await screen.findByRole("button", {
        name: fill(en.teach.order.position, { name: en.slots.lower_left, n: "1" }),
      }),
    ).toBeVisible();
  });
});
