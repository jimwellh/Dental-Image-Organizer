import { createEvent, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { en } from "../../strings/en";
import { errorReply, fakeServer } from "../../test/fakeServer";
import { updateStatusView } from "../../test/records";
import { renderWithClient } from "../../test/render";
import { Shell } from "./Shell";

const routes = {
  "POST /api/heartbeat": () => undefined,
  "GET /api/health": () => ({ status: "ok", version: "0" }),
  "GET /api/system/update": () => updateStatusView(),
};

test("Quit shows the closed notice at once", async () => {
  const user = userEvent.setup();
  const server = fakeServer({
    ...routes,
    "POST /api/system/shutdown": () => ({ status: "stopping" }),
  });
  renderWithClient(
    <Shell>
      <p>content</p>
    </Shell>,
  );
  await user.click(screen.getByRole("button", { name: en.nav.quit }));

  expect(
    await screen.findByText("Dental Image Organizer has closed. Click its Dock icon to reopen."),
  ).toBeInTheDocument();
  expect(server.calls.filter((call) => call.path === "/api/system/shutdown")).toHaveLength(1);
  expect(screen.queryByRole("button", { name: en.nav.quit })).not.toBeInTheDocument();
  expect(screen.queryByText("content")).not.toBeInTheDocument();
});

test("a refused Quit shows the server's message and keeps the app open", async () => {
  const user = userEvent.setup();
  fakeServer({
    ...routes,
    "POST /api/system/shutdown": () => errorReply(500, "internal", "Could not stop."),
  });
  renderWithClient(
    <Shell>
      <p>content</p>
    </Shell>,
  );
  await user.click(screen.getByRole("button", { name: en.nav.quit }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not stop.");
  expect(screen.getByText("content")).toBeInTheDocument();
});

test("a file dropped outside any zone is ignored, not opened in the tab", async () => {
  fakeServer(routes);
  renderWithClient(
    <Shell>
      <p>content</p>
    </Shell>,
  );
  const transfer = { types: ["Files"], dropEffect: "copy" };

  const over = createEvent.dragOver(window, { dataTransfer: transfer });
  fireEvent(window, over);
  const drop = createEvent.drop(window, { dataTransfer: transfer });
  fireEvent(window, drop);

  expect(over.defaultPrevented).toBe(true);
  expect(transfer.dropEffect).toBe("none");
  expect(drop.defaultPrevented).toBe(true);
  expect(await screen.findByText(en.intake.dropOnZone)).toBeInTheDocument();
});

test("a drag that carries no files is left alone", () => {
  fakeServer(routes);
  renderWithClient(
    <Shell>
      <p>content</p>
    </Shell>,
  );

  const drop = createEvent.drop(window, { dataTransfer: { types: ["text/plain"] } });
  fireEvent(window, drop);

  expect(drop.defaultPrevented).toBe(false);
});

test("Update now replaces the page with the updating notice", async () => {
  const user = userEvent.setup();
  fakeServer({
    ...routes,
    "GET /api/system/update": () =>
      updateStatusView({
        latest: "1.2.0",
        available: true,
        can_update: true,
        unavailable_reason: null,
      }),
    "POST /api/system/update": () => ({ status: "updating" }),
  });
  renderWithClient(
    <Shell>
      <p>content</p>
    </Shell>,
  );

  await user.click(await screen.findByRole("button", { name: en.update.now }));

  expect(await screen.findByText(en.shell.updating)).toBeInTheDocument();
  expect(screen.queryByText("content")).not.toBeInTheDocument();
});
