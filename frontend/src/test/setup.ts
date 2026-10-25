import "@testing-library/jest-dom/vitest";

// Tests stub globals and fake timers; undo both so one test cannot leak into the next.
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
