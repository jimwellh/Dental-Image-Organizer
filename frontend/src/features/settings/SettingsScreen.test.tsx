import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ClassifierView, ModelStatus, Settings } from "../../api/types";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { FakeEventSource } from "../../test/fakeEventSource";
import { errorReply, fakeServer } from "../../test/fakeServer";
import { classifierView, modelStatusView, settingsRecord } from "../../test/records";
import { renderWithClient } from "../../test/render";
import { SettingsScreen } from "./SettingsScreen";

interface Options {
  settings?: Settings;
  classifier?: Partial<ClassifierView>;
  model?: Partial<ModelStatus>;
  put?: (body: unknown) => unknown;
}

function serve({ settings = settingsRecord(), classifier, model, put }: Options = {}) {
  // Like the server, a saved body is what the next read returns.
  let current = settings;
  return fakeServer({
    "GET /api/settings": () => current,
    "PUT /api/settings": (request) => {
      if (put) return put(request.body);
      current = request.body as Settings;
      return current;
    },
    "GET /api/classifier/status": () => classifierView({ model_available: true, ...classifier }),
    "GET /api/model/status": () => modelStatusView({ available: true, ...model }),
    "POST /api/classifier/retrain": () => ({ job_id: "a".repeat(32) }),
  });
}

const putBodies = (server: ReturnType<typeof fakeServer>) =>
  server.calls.filter((call) => call.method === "PUT").map((call) => call.body);

describe("SettingsScreen", () => {
  it("saving sends the whole settings with the change", async () => {
    const server = serve();
    renderWithClient(<SettingsScreen />);
    const user = userEvent.setup();

    const quality = await screen.findByLabelText(en.settings.jpegQuality.label);
    const save = screen.getByRole("button", { name: en.settings.save });
    expect(save).toBeDisabled();

    await user.clear(quality);
    await user.type(quality, "85");
    await user.click(save);

    await waitFor(() => {
      expect(putBodies(server)).toEqual([settingsRecord({ jpeg_quality: 85 })]);
    });
    await screen.findByText(en.settings.saved);
    expect(screen.getByRole("button", { name: en.settings.save })).toBeDisabled();
  });

  it("refuses a value outside its bounds before sending it", async () => {
    const server = serve();
    renderWithClient(<SettingsScreen />);
    const user = userEvent.setup();

    const quality = await screen.findByLabelText(en.settings.jpegQuality.label);
    await user.clear(quality);
    await user.type(quality, "40");

    expect(quality).toBeInvalid();
    expect(screen.getByRole("button", { name: en.settings.save })).toBeDisabled();
    expect(putBodies(server)).toEqual([]);
  });

  it("discarding restores the saved settings", async () => {
    serve();
    renderWithClient(<SettingsScreen />);
    const user = userEvent.setup();

    const quality = await screen.findByLabelText(en.settings.jpegQuality.label);
    await user.clear(quality);
    await user.type(quality, "70");
    await user.click(screen.getByRole("button", { name: en.settings.discard }));

    expect(quality).toHaveValue(92);
    expect(screen.getByRole("button", { name: en.settings.save })).toBeDisabled();
  });

  it("pinning a slot stores its flip, unpinning removes it", async () => {
    const server = serve();
    renderWithClient(<SettingsScreen />);
    const user = userEvent.setup();

    const pin = await screen.findByRole("checkbox", {
      name: fill(en.settings.flips.pin, { name: en.slots.upper_occlusal }),
    });
    await user.click(pin);
    await user.click(screen.getByRole("button", { name: en.settings.save }));
    await waitFor(() => {
      expect(putBodies(server)).toEqual([
        settingsRecord({ flip_pins: { upper_occlusal: "vertical" } }),
      ]);
    });

    await user.click(pin);
    await user.click(screen.getByRole("button", { name: en.settings.save }));
    await waitFor(() => {
      expect(putBodies(server)).toHaveLength(2);
    });
    expect(putBodies(server)[1]).toEqual(settingsRecord({ flip_pins: {} }));
  });

  it("a learned flip wins over the start value until the slot is pinned", async () => {
    const server = serve({
      classifier: {
        flips: {
          upper_occlusal: {
            confirmations: 4,
            effective: "none",
            learned: "none",
            pinned: false,
            start: "vertical",
          },
        },
      },
    });
    renderWithClient(<SettingsScreen />);
    const user = userEvent.setup();

    const row = (await screen.findByRole("rowheader", { name: en.slots.upper_occlusal })).closest(
      "tr",
    );
    if (row === null) throw new Error("The Upper occlusal row is missing.");
    expect(row).toHaveTextContent(
      fill(en.settings.flips.learnedValue, { flip: en.flips.none, n: "4" }),
    );

    // Pinning keeps what is in effect now, which is the learned value.
    await user.click(
      screen.getByRole("checkbox", {
        name: fill(en.settings.flips.pin, { name: en.slots.upper_occlusal }),
      }),
    );
    await user.click(screen.getByRole("button", { name: en.settings.save }));
    await waitFor(() => {
      expect(putBodies(server)).toEqual([
        settingsRecord({ flip_pins: { upper_occlusal: "none" } }),
      ]);
    });
  });

  it("changes a start flip and a pinned flip", async () => {
    const server = serve();
    renderWithClient(<SettingsScreen />);
    const user = userEvent.setup();

    await user.selectOptions(
      await screen.findByLabelText(
        fill(en.settings.flips.startLabel, { name: en.slots.upper_right }),
      ),
      "rotate_180",
    );
    await user.click(
      screen.getByRole("checkbox", {
        name: fill(en.settings.flips.pin, { name: en.slots.upper_left }),
      }),
    );
    await user.selectOptions(
      screen.getByLabelText(fill(en.settings.flips.pinnedLabel, { name: en.slots.upper_left })),
      "none",
    );
    await user.click(screen.getByRole("button", { name: en.settings.save }));

    const base = settingsRecord();
    await waitFor(() => {
      expect(putBodies(server)).toEqual([
        {
          ...base,
          flip_start: { ...base.flip_start, upper_right: "rotate_180" },
          flip_pins: { upper_left: "none" },
        },
      ]);
    });
  });

  it("a refused value shows the server's reason", async () => {
    serve({
      put: () =>
        errorReply(422, "invalid_request", "The request is not valid.", [
          {
            loc: ["body", "library_root"],
            msg: "Value error, library_root must be an absolute path",
          },
        ]),
    });
    renderWithClient(<SettingsScreen />);
    const user = userEvent.setup();

    const root = await screen.findByLabelText(en.settings.libraryRoot.label);
    await user.clear(root);
    await user.type(root, "relative/folder");
    await user.click(screen.getByRole("button", { name: en.settings.save }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "library_root: Value error, library_root must be an absolute path",
    );
    // The edit stays, so the user can correct it.
    expect(root).toHaveValue("relative/folder");
  });

  it("retrain starts a job", async () => {
    const server = serve({ classifier: { trained: false } });
    renderWithClient(<SettingsScreen />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: en.settings.ai.retrain }));
    await waitFor(() => {
      expect(
        server.calls.some(
          (call) => call.method === "POST" && call.path === "/api/classifier/retrain",
        ),
      ).toBe(true);
    });
    const statusReads = () =>
      server.calls.filter((call) => call.path === "/api/classifier/status").length;
    expect(statusReads()).toBe(1);

    await waitFor(() => {
      expect(FakeEventSource.last).not.toBeNull();
    });
    expect(await screen.findByText(en.settings.ai.retraining)).toBeInTheDocument();
    FakeEventSource.last?.emit("done", { result: { trained: true } });

    await waitFor(() => {
      expect(statusReads()).toBe(2);
    });
  });

  it("a failed retrain says why", async () => {
    serve();
    renderWithClient(<SettingsScreen />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: en.settings.ai.retrain }));
    await waitFor(() => {
      expect(FakeEventSource.last).not.toBeNull();
    });
    FakeEventSource.last?.emit("failed", { error: { code: "storage", message: "Disk full." } });

    expect(await screen.findByRole("alert")).toHaveTextContent("Disk full.");
  });

  it("shows the taught counts, stale samples, and a damaged classifier", async () => {
    serve({
      classifier: {
        counts: { upper_right: 2 },
        stale_samples: 5,
        damaged: "The classifier file is damaged.",
      },
    });
    renderWithClient(<SettingsScreen />);

    expect(
      await screen.findByText(
        fill(en.teach.count, { slot: en.slots.upper_right, n: "2", of: "3" }),
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(en.settings.ai.untrained)).toBeInTheDocument();
    expect(screen.getByText(fill(en.settings.ai.stale, { n: "5" }))).toBeInTheDocument();
    expect(screen.getByText("The classifier file is damaged.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: en.ai.teach })).toHaveAttribute("href", "/teach");
  });

  it("a damaged flip history is explained", async () => {
    serve({ classifier: { flips_damaged: "The flip history is damaged; defaults are used." } });
    renderWithClient(<SettingsScreen />);

    expect(
      await screen.findByText("The flip history is damaged; defaults are used."),
    ).toBeInTheDocument();
  });

  it("offers the download while the model is missing, and says so once it is there", async () => {
    serve({ model: { available: false } });
    const first = renderWithClient(<SettingsScreen />);
    expect(await screen.findByRole("button", { name: en.ai.download })).toBeInTheDocument();
    first.unmount();

    serve({ model: { available: true } });
    renderWithClient(<SettingsScreen />);
    expect(await screen.findByText(en.settings.ai.downloaded)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.ai.download })).not.toBeInTheDocument();
  });

  it("zero minutes means never", async () => {
    serve();
    renderWithClient(<SettingsScreen />);

    const idle = await screen.findByLabelText(en.settings.idleShutdown.label);
    expect(idle).toHaveAccessibleDescription(en.settings.idleShutdown.help);
    expect(screen.getByText(en.settings.idleShutdown.help)).toBeInTheDocument();
  });
});
