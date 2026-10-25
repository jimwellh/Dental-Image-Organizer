import "@testing-library/jest-dom/vitest";
import { assertNoUnmatchedRequests } from "./fakeServer";

// Tests stub globals and fake timers; undo both so one test cannot leak into the next.
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  assertNoUnmatchedRequests();
});
