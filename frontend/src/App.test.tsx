import { render, screen } from "@testing-library/react";
import { App } from "./App";
import { en } from "./strings/en";
import { fakeServer } from "./test/fakeServer";
import { patientsView } from "./test/records";

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

function serve() {
  fakeServer({
    "POST /api/heartbeat": () => undefined,
    "GET /api/health": () => ({ status: "ok", version: "0" }),
    // Declared now so the library screen needs no change here.
    "GET /api/patients": () => patientsView(),
  });
}

test("the app frames the library", () => {
  serve();
  render(<App />);
  expect(screen.getByRole("link", { name: en.app.name })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: en.nav.quit })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: en.nav.library })).toBeInTheDocument();
});

test("an unknown address shows the not-found page with a way back", () => {
  serve();
  window.history.replaceState(null, "", "/nope");
  render(<App />);
  expect(screen.getByText(en.app.notFound)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: en.app.backToLibrary })).toHaveAttribute("href", "/");
});
