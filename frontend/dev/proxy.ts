import type { ProxyOptions } from "vite";

// The origins the Vite dev server itself is reached at (it binds `localhost`; see spec §5.4).
export const DEV_ORIGINS = new Set(["http://localhost:5173", "http://127.0.0.1:5173"]);

export function apiProxy(target: string): ProxyOptions {
  return {
    target,
    changeOrigin: true,
    configure(proxy) {
      proxy.on("proxyReq", (proxyReq) => {
        // The backend's Origin check (spec §4.13) has no development bypass, so the dev
        // server's own origin would be refused on every state-changing request. Only that origin
        // is rewritten. Any other page the developer visits could otherwise POST cross-site to
        // the dev server, and the proxy would turn it into a trusted request (CSRF). A foreign
        // origin, or none, passes through unchanged so the backend still answers 403.
        const origin = proxyReq.getHeader("origin");
        if (typeof origin === "string" && DEV_ORIGINS.has(origin)) {
          proxyReq.setHeader("origin", target);
        }
      });
    },
  };
}
