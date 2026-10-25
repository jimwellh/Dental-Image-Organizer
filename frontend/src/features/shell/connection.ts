import { useCallback, useEffect, useRef, useState } from "react";
import { health, heartbeat } from "../../api/endpoints";

export const HEARTBEAT_MS = 30_000;
export const HEALTH_MS = 5_000;
export const LOST_AFTER_MS = 10_000;
export const RECONNECT_MS = 2_000;
// How often the silence is measured; the state flips within this much of `LOST_AFTER_MS`.
const WATCH_MS = 1_000;

/** Plan 5 adds `"updating"`. */
export type ConnectionState = "connected" | "closed";

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
export function useServerConnection(options: { reload?: () => void } = {}): {
  state: ConnectionState;
  markClosed: () => void;
} {
  const [state, setState] = useState<ConnectionState>("connected");
  const markClosed = useCallback(() => {
    setState("closed");
  }, []);

  // The latest `reload`, without restarting the polling when a caller passes a new function.
  const reload = useRef(options.reload ?? reloadPage);
  useEffect(() => {
    reload.current = options.reload ?? reloadPage;
  });

  useEffect(() => {
    if (state !== "connected") return;
    let lastOk = Date.now();
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
      if (document.visibilityState === "visible") sendHeartbeat();
    };

    sendHeartbeat();
    const timers = [
      window.setInterval(sendHeartbeat, HEARTBEAT_MS),
      window.setInterval(poll, HEALTH_MS),
      window.setInterval(() => {
        if (Date.now() - lastOk >= LOST_AFTER_MS) setState("closed");
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
    const timer = window.setInterval(() => {
      health().then(() => {
        // Several polls can be in flight; the page reloads once.
        if (reloaded) return;
        reloaded = true;
        reload.current();
      }, ignore);
    }, RECONNECT_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [state]);

  return { state, markClosed };
}
