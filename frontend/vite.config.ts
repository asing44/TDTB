import { defineConfig } from "vite";
import preact from "@preact/preset-vite";
import path from "node:path";

// Two build targets (locked decisions 12/13/15):
//  - mockup:  fixture adapter compiled in, outputs to Tasks/tdtb-app-pilot/mockups/cockpit/
//  - production: api adapter only, outputs to Tasks/tdtb-app-pilot/app/static/cockpit/
// Both are committed builds; FastAPI serves static files — no runtime CDN deps.
export default defineConfig(({ mode }) => ({
  plugins: [preact()],
  base: "./",
  define: {
    __FIXTURE__: JSON.stringify(mode !== "production"),
  },
  build: {
    outDir:
      mode === "mockup"
        ? path.resolve(__dirname, "../mockups/cockpit")
        : path.resolve(__dirname, "../app/static/cockpit"),
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
    globals: false,
    include: ["src/**/*.test.{ts,tsx}"],
  },
}));
