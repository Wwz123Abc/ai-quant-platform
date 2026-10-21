/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The production build is written into the Python package, which serves it at "/" or under a path such as
// "/lab/"; asset links are relative so they follow the <base href> the server puts in the page.
// During development, `npm run dev` proxies API calls to `python -m quantlab serve` on port 8000.
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: { outDir: "../src/quantlab/web", emptyOutDir: true, chunkSizeWarningLimit: 800 },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  test: { environment: "jsdom" },
});
