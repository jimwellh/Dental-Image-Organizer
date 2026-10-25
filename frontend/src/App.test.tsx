import { render, screen } from "@testing-library/react";
import { App } from "./App";
import { en } from "./strings/en";

test("renders the app name as the page heading", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: en.app.name })).toBeInTheDocument();
});
