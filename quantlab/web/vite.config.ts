/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The production build is written into the Python package, which serves it at "/".
// During development, `npm run dev` proxies API calls to `python -m quantlab serve` on port 8000.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/quantlab/web", emptyOutDir: true, chunkSizeWarningLimit: 800 },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  test: { environment: "jsdom" },
});
