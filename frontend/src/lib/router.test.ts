import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { createElement } from "react";
import { Link, navigate, parseRoute, routePath, useRoute, type Route } from "./router";

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

test("parses the four routes", () => {
  expect(parseRoute("/")).toEqual({ name: "library" });
  expect(parseRoute("/cases/abc")).toEqual({ name: "case", caseId: "abc" });
  expect(parseRoute("/teach")).toEqual({ name: "teach" });
  expect(parseRoute("/settings")).toEqual({ name: "settings" });
  expect(parseRoute("/cases/")).toEqual({ name: "notFound" });
  expect(parseRoute("/nope")).toEqual({ name: "notFound" });
});

test("routePath inverts parseRoute", () => {
  const routes: Route[] = [
    { name: "library" },
    { name: "case", caseId: "abc" },
    { name: "case", caseId: "a b/c" },
    { name: "teach" },
    { name: "settings" },
    { name: "notFound" },
  ];
  for (const route of routes) expect(parseRoute(routePath(route))).toEqual(route);
});

test("navigate updates useRoute", () => {
  const { result } = renderHook(() => useRoute());
  expect(result.current).toEqual({ name: "library" });
  act(() => {
    navigate({ name: "settings" });
  });
  expect(result.current).toEqual({ name: "settings" });
  expect(window.location.pathname).toBe("/settings");
});

test("a plain click navigates in place", () => {
  render(createElement(Link, { to: { name: "teach" }, children: "Teach" }));
  fireEvent.click(screen.getByRole("link", { name: "Teach" }));
  expect(window.location.pathname).toBe("/teach");
});

test("a modified click is left to the browser", () => {
  render(createElement(Link, { to: { name: "settings" }, children: "Settings" }));
  // jsdom cannot navigate; preventing the default keeps it from logging "not implemented".
  const stop = (event: Event) => {
    event.preventDefault();
  };
  document.addEventListener("click", stop);
  fireEvent.click(screen.getByRole("link", { name: "Settings" }), { ctrlKey: true });
  document.removeEventListener("click", stop);
  expect(window.location.pathname).toBe("/");
});
