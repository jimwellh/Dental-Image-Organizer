import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { apiProxy } from "./dev/proxy.ts";

// The backend's default port (spec §4.3); `uv run dio serve --no-browser` listens there.
const BACKEND = "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, strictPort: true, proxy: { "/api": apiProxy(BACKEND) } },
  // Outside the Vite root, so emptyOutDir must be explicit; the folder is gitignored and
  // reaches the wheel through hatch `artifacts`.
  build: { outDir: "../src/dental_image_organizer/web", emptyOutDir: true },
  test: { environment: "jsdom", globals: true, setupFiles: ["src/test/setup.ts"] },
});
