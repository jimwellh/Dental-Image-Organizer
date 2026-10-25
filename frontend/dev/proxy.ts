import type { ProxyOptions } from "vite";

export function apiProxy(target: string): ProxyOptions {
  return {
    target,
    changeOrigin: true,
    configure(proxy) {
      proxy.on("proxyReq", (proxyReq) => {
        // The backend's Origin check (spec §4.13) has no development bypass, so the browser's
        // Vite origin would be refused on every state-changing request. Only an Origin the
        // browser actually sent is rewritten: a request without one must stay refused.
        if (proxyReq.getHeader("origin") !== undefined) {
          proxyReq.setHeader("origin", target);
        }
      });
    },
  };
}
