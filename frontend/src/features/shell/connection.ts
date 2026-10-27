import { useCallback, useEffect, useRef, useState } from "react";
import { health, heartbeat } from "../../api/endpoints";

export const HEARTBEAT_MS = 30_000;
export const HEALTH_MS = 5_000;
export const LOST_AFTER_MS = 10_000;
export const RECONNECT_MS = 2_000;
// An update that has not brought the server back by then is reported as a closed app, whose
// notice tells the user how to reopen it.
export const UPDATE_TIMEOUT_MS = 10 * 60_000;
// How often the silence is measured; the state flips within this much of `LOST_AFTER_MS`.
const WATCH_MS = 1_000;

export type ConnectionState = "connected" | "closed" | "updating";

const reloadPage = () => {
  window.location.reload();
};

// Heartbeat and health failures are expected when the server is gone; the closed screen is the
// report, so they never raise a notice.
const ignore = () => undefined;

/**
 * Keeps the server alive with heartbeats and notices when it stops answering. A hung request never
 * calls `lastOk`, so no timeout or abort is needed: silence is the signal.
 */
export function useServerConnection(
  options: {
    reload?: () => void;
    /** The server came back from an update at the version the page runs: nothing changed. */
    onUpdateUnchanged?: () => void;
  } = {},
): {
  state: ConnectionState;
  markClosed: () => void;
  /** The server is updating from `version`; the page reloads once it answers with another. */
  markUpdating: (version: string) => void;
} {
  const [state, setState] = useState<ConnectionState>("connected");
  // A deliberate close (Quit) is followed by a server that still answers for a moment while it
  // exits; the reconnect loop must see it go away before an answer means "it is back".
  const deliberateClose = useRef(false);
  const markClosed = useCallback(() => {
    deliberateClose.current = true;
    setState("closed");
  }, []);
  const updatingFrom = useRef("");
  const markUpdating = useCallback((version: string) => {
    updatingFrom.current = version;
    setState("updating");
  }, []);

  // The latest callbacks, without restarting the polling when a caller passes new functions.
  const reload = useRef(options.reload ?? reloadPage);
  const onUpdateUnchanged = useRef(options.onUpdateUnchanged);
  useEffect(() => {
    reload.current = options.reload ?? reloadPage;
    onUpdateUnchanged.current = options.onUpdateUnchanged;
  });

  useEffect(() => {
    if (state !== "connected") return;
    let lastOk = Date.now();
    let lastTick = lastOk;
    const answered = () => {
      lastOk = Date.now();
    };
    const sendHeartbeat = () => {
      heartbeat().then(answered, ignore);
    };
    const poll = () => {
      health().then(answered, ignore);
    };
    const onVisibility = () => {
      if (document.visibilityState !== "visible") return;
      // Timers were throttled while hidden, so a health answer may be in flight; restart the
      // silence window rather than judge it on stale timestamps.
      lastOk = Date.now();
      sendHeartbeat();
    };

    sendHeartbeat();
    const timers = [
      window.setInterval(sendHeartbeat, HEARTBEAT_MS),
      window.setInterval(poll, HEALTH_MS),
      window.setInterval(() => {
        const now = Date.now();
        const late = now - lastTick > 2 * WATCH_MS;
        lastTick = now;
        if (late) {
          // A background tab or a sleeping laptop paused the timers; the health answer that
          // arrived meanwhile has not been processed yet. Let the next polls decide.
          lastOk = now;
        } else if (now - lastOk >= LOST_AFTER_MS) {
          deliberateClose.current = false;
          setState("closed");
        }
      }, WATCH_MS),
    ];
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      for (const timer of timers) window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [state]);

  useEffect(() => {
    if (state !== "closed") return;
    let reloaded = false;
    // The silence that closed the page already was a failure; a Quit still needs to see one.
    let sawFailure = !deliberateClose.current;
    const timer = window.setInterval(() => {
      health().then(
        () => {
          // Several polls can be in flight; the page reloads once.
          if (reloaded || !sawFailure) return;
          reloaded = true;
          reload.current();
        },
        () => {
          sawFailure = true;
        },
      );
    }, RECONNECT_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [state]);

  useEffect(() => {
    if (state !== "updating") return;
    let settled = false;
    // The old server answers until it exits; only an answer after a failure is the new one.
    let sawFailure = false;
    const timer = window.setInterval(() => {
      health().then(
        (answer) => {
          if (settled || !sawFailure) return;
          settled = true;
          if (answer.version !== updatingFrom.current) {
            reload.current();
          } else {
            // The update script started the version the page already runs again.
            setState("connected");
            onUpdateUnchanged.current?.();
          }
        },
        () => {
          sawFailure = true;
        },
      );
    }, RECONNECT_MS);
    const giveUp = window.setTimeout(() => {
      if (settled) return;
      settled = true;
      deliberateClose.current = false;
      setState("closed");
    }, UPDATE_TIMEOUT_MS);
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(giveUp);
    };
  }, [state]);

  return { state, markClosed, markUpdating };
}
