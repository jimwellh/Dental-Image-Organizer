import { act, renderHook } from "@testing-library/react";
import { fakeServer } from "../../test/fakeServer";
import {
  HEARTBEAT_MS,
  LOST_AFTER_MS,
  RECONNECT_MS,
  UPDATE_TIMEOUT_MS,
  useServerConnection,
} from "./connection";

beforeEach(() => {
  // `shouldAdvanceTime` lets fetch promises settle between the fake ticks.
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

const advance = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

const healthy = () => ({ status: "ok", version: "0" });

function heartbeats(server: ReturnType<typeof fakeServer>): number {
  return server.calls.filter((call) => call.path === "/api/heartbeat").length;
}

test("sends a heartbeat at once, every 30 s, and when the tab becomes visible", async () => {
  const server = fakeServer({
    "POST /api/heartbeat": () => undefined,
    "GET /api/health": healthy,
  });
  renderHook(() => useServerConnection());
  expect(heartbeats(server)).toBe(1);

  await advance(HEARTBEAT_MS);
  expect(heartbeats(server)).toBe(2);

  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  document.dispatchEvent(new Event("visibilitychange"));
  expect(heartbeats(server)).toBe(3);
});

test("does not send a heartbeat when the tab becomes hidden", () => {
  const server = fakeServer({
    "POST /api/heartbeat": () => undefined,
    "GET /api/health": healthy,
  });
  renderHook(() => useServerConnection());
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  document.dispatchEvent(new Event("visibilitychange"));
  expect(heartbeats(server)).toBe(1);
});

test("reports the server closed after 10 s without an answer", async () => {
  const down = () => {
    throw new Error("down");
  };
  fakeServer({ "POST /api/heartbeat": down, "GET /api/health": down });
  const { result } = renderHook(() => useServerConnection());

  await advance(LOST_AFTER_MS - 1000);
  expect(result.current.state).toBe("connected");
  await advance(1000);
  expect(result.current.state).toBe("closed");
});

test("stays connected while health answers although heartbeats fail", async () => {
  fakeServer({
    "POST /api/heartbeat": () => {
      throw new Error("down");
    },
    "GET /api/health": healthy,
  });
  const { result } = renderHook(() => useServerConnection());
  await advance(LOST_AFTER_MS * 3);
  expect(result.current.state).toBe("connected");
});

test("reloads the page when the server answers again", async () => {
  let up = false;
  const gate = () => {
    if (!up) throw new Error("down");
  };
  fakeServer({
    "POST /api/heartbeat": gate,
    "GET /api/health": () => {
      gate();
      return healthy();
    },
  });
  const reload = vi.fn();
  const { result } = renderHook(() => useServerConnection({ reload }));

  await advance(LOST_AFTER_MS);
  expect(result.current.state).toBe("closed");
  await advance(RECONNECT_MS * 2);
  expect(reload).not.toHaveBeenCalled();

  up = true;
  await advance(RECONNECT_MS);
  expect(reload).toHaveBeenCalledOnce();
  await advance(RECONNECT_MS * 3);
  expect(reload).toHaveBeenCalledOnce();
});

test("markClosed closes at once", async () => {
  fakeServer({
    "POST /api/heartbeat": () => undefined,
    "GET /api/health": healthy,
  });
  const { result } = renderHook(() => useServerConnection({ reload: vi.fn() }));
  act(() => {
    result.current.markClosed();
  });
  expect(result.current.state).toBe("closed");
  // The closed state polls health; let it settle so no request outlives the test.
  await advance(0);
});

test("after a Quit, an answering server does not reload until it has been seen to go away", async () => {
  let up = true;
  fakeServer({
    "POST /api/heartbeat": () => undefined,
    "GET /api/health": () => {
      if (!up) throw new Error("down");
      return healthy();
    },
  });
  const reload = vi.fn();
  const { result } = renderHook(() => useServerConnection({ reload }));
  act(() => {
    result.current.markClosed();
  });

  await advance(RECONNECT_MS * 3);
  expect(reload).not.toHaveBeenCalled();

  up = false;
  await advance(RECONNECT_MS * 2);
  expect(reload).not.toHaveBeenCalled();

  up = true;
  await advance(RECONNECT_MS * 2);
  expect(reload).toHaveBeenCalledOnce();
});

test("a long timer gap does not close the page while the server answers", async () => {
  fakeServer({
    "POST /api/heartbeat": () => undefined,
    "GET /api/health": healthy,
  });
  const { result } = renderHook(() => useServerConnection({ reload: vi.fn() }));

  // A hidden tab or a sleeping laptop: the clock jumps, the timers do not run meanwhile.
  vi.setSystemTime(Date.now() + 60_000);
  await advance(1000);
  expect(result.current.state).toBe("connected");
  await advance(LOST_AFTER_MS * 2);
  expect(result.current.state).toBe("connected");
});

test("after a long timer gap a server that is gone still reads as closed", async () => {
  let up = true;
  const gate = () => {
    if (!up) throw new Error("down");
  };
  fakeServer({
    "POST /api/heartbeat": gate,
    "GET /api/health": () => {
      gate();
      return healthy();
    },
  });
  const { result } = renderHook(() => useServerConnection({ reload: vi.fn() }));

  up = false;
  vi.setSystemTime(Date.now() + 60_000);
  await advance(1000);
  expect(result.current.state).toBe("connected");
  await advance(LOST_AFTER_MS - 2000);
  expect(result.current.state).toBe("connected");
  await advance(4000);
  expect(result.current.state).toBe("closed");
});

/** A server that answers with `version` while `up`, and refuses otherwise. */
function updatingServer() {
  const server = { up: true, version: "1.0.0" };
  fakeServer({
    "POST /api/heartbeat": () => {
      if (!server.up) throw new Error("down");
    },
    "GET /api/health": () => {
      if (!server.up) throw new Error("down");
      return { status: "ok", version: server.version };
    },
  });
  return server;
}

test("an update reloads the page once the new version answers", async () => {
  const server = updatingServer();
  const reload = vi.fn();
  const onUpdateUnchanged = vi.fn();
  const { result } = renderHook(() => useServerConnection({ reload, onUpdateUnchanged }));
  act(() => {
    result.current.markUpdating("1.0.0");
  });
  expect(result.current.state).toBe("updating");

  // The old server still answers until it exits; that is not the update finishing.
  await advance(RECONNECT_MS * 3);
  expect(reload).not.toHaveBeenCalled();

  server.up = false;
  await advance(LOST_AFTER_MS * 3);
  expect(result.current.state).toBe("updating");

  server.up = true;
  server.version = "1.1.0";
  await advance(RECONNECT_MS);
  expect(reload).toHaveBeenCalledOnce();
  expect(onUpdateUnchanged).not.toHaveBeenCalled();
  await advance(RECONNECT_MS * 3);
  expect(reload).toHaveBeenCalledOnce();
});

test("a server back at the same version reconnects and reports the unchanged update", async () => {
  const server = updatingServer();
  const reload = vi.fn();
  const onUpdateUnchanged = vi.fn();
  const { result } = renderHook(() => useServerConnection({ reload, onUpdateUnchanged }));
  act(() => {
    result.current.markUpdating("1.0.0");
  });

  server.up = false;
  await advance(RECONNECT_MS * 2);
  server.up = true;
  await advance(RECONNECT_MS);

  expect(result.current.state).toBe("connected");
  expect(onUpdateUnchanged).toHaveBeenCalledOnce();
  expect(reload).not.toHaveBeenCalled();
});

test("an update that never brings the server back ends as a closed app", async () => {
  const server = updatingServer();
  const reload = vi.fn();
  const { result } = renderHook(() => useServerConnection({ reload }));
  act(() => {
    result.current.markUpdating("1.0.0");
  });
  server.up = false;

  await advance(UPDATE_TIMEOUT_MS);
  expect(result.current.state).toBe("closed");

  // The closed page reloads when the user reopens the app from the Dock.
  server.up = true;
  await advance(RECONNECT_MS);
  expect(reload).toHaveBeenCalledOnce();
});
