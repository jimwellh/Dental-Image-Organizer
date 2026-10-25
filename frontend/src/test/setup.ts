import "@testing-library/jest-dom/vitest";
import { FakeEventSource } from "./fakeEventSource";
import { assertNoUnmatchedRequests } from "./fakeServer";

// jsdom has no `EventSource`; components that follow a job get the fake without a test-only prop.
beforeEach(() => {
  FakeEventSource.last = null;
  vi.stubGlobal("EventSource", FakeEventSource);
});

// Tests stub globals and fake timers; undo both so one test cannot leak into the next.
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  assertNoUnmatchedRequests();
});
