import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UpdateStatus } from "../../api/types";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { errorReply, fakeServer } from "../../test/fakeServer";
import { updateStatusView } from "../../test/records";
import { renderWithClient } from "../../test/render";
import { UpdateBanner } from "./UpdateBanner";

const newer = (overrides: Partial<UpdateStatus> = {}) =>
  updateStatusView({ latest: "1.2.0", available: true, ...overrides });

function serve(status: UpdateStatus, start: () => unknown = () => ({ status: "updating" })) {
  return fakeServer({
    "GET /api/system/update": () => status,
    "POST /api/system/update": start,
  });
}

test("shows nothing while the app is up to date", async () => {
  const server = serve(updateStatusView());
  renderWithClient(<UpdateBanner onUpdating={vi.fn()} />);

  await waitFor(() => {
    expect(server.calls).toHaveLength(1);
  });
  // Let the answer render before looking.
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByText(/available|Terminal/)).not.toBeInTheDocument();
});

test("Update now starts the update and hands over the running version", async () => {
  const user = userEvent.setup();
  const server = serve(newer({ can_update: true, unavailable_reason: null }));
  const onUpdating = vi.fn();
  renderWithClient(<UpdateBanner onUpdating={onUpdating} />);

  expect(
    await screen.findByText(fill(en.update.available, { latest: "1.2.0", current: "1.0.0" })),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: en.update.now }));

  await waitFor(() => {
    expect(onUpdating).toHaveBeenCalledWith("1.0.0");
  });
  expect(server.calls.filter((call) => call.method === "POST")).toHaveLength(1);
});

test("a refused update says why and keeps the page", async () => {
  const user = userEvent.setup();
  serve(newer({ can_update: true, unavailable_reason: null }), () =>
    errorReply(409, "job_running", "Wait for the running jobs to finish before updating."),
  );
  const onUpdating = vi.fn();
  renderWithClient(<UpdateBanner onUpdating={onUpdating} />);

  await user.click(await screen.findByRole("button", { name: en.update.now }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Wait for the running jobs to finish before updating.",
  );
  expect(onUpdating).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: en.update.now })).toBeEnabled();
});

test("where the app cannot update itself, the banner shows the Terminal command", async () => {
  serve(newer());
  renderWithClient(<UpdateBanner onUpdating={vi.fn()} />);

  expect(await screen.findByText("uv tool upgrade dental-image-organizer")).toBeInTheDocument();
  expect(screen.getByText(en.update.terminal, { exact: false })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: en.update.now })).not.toBeInTheDocument();
});

test("a failed last update is shown with its log until dismissed", async () => {
  const user = userEvent.setup();
  const failure = { message: "The last update failed.", log_file: "/Users/d/Library/Logs/u.log" };
  serve(updateStatusView({ last_failure: failure }));
  renderWithClient(<UpdateBanner onUpdating={vi.fn()} />);

  expect(await screen.findByRole("alert")).toHaveTextContent(
    fill(en.update.failed, { message: failure.message, log: failure.log_file }),
  );
  await user.click(screen.getByRole("button", { name: en.common.dismiss }));

  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
