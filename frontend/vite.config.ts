import { defineConfig } from "vite";
import preact from "@preact/preset-vite";
import path from "node:path";

// Two build targets (locked decisions 12/13/15):
//  - mockup:  fixture adapter compiled in, outputs to mockups/cockpit/
//  - production: api adapter only, outputs to app/static/cockpit/
// Production is the committed served bundle (FastAPI serves static files — no
// runtime CDN deps); mockup mode is a local preview build.
//
// `server` below is used only by `vite dev` (the `tdtb-dev` editing loop) and
// never by a build, so build:mockup and build:prod output is unchanged.

// Every backend route the cockpit calls. The production adapter builds
// requests from an empty base (frontend/src/adapters/api.ts — `fetch(this.base
// + path)` with `base = ""`), so the dev page issues RELATIVE requests that
// must reach the backend on :8746 instead of the Vite server on :5273.
// Listing prefixes (not a catch-all) keeps the Vite-owned paths — the app
// shell at "/", /src/*, /@vite/*, /node_modules/* — out of the proxy.
const API_PREFIXES = [
  "/health",
  "/version",
  "/session-token",
  "/config",
  "/calendar-drift",
  "/settings",
  "/capacities",
  "/plan-inputs",
  "/billed-ledger",
  "/capacity-preview",
  "/duration-memory",
  "/gather",
  "/day-setup",
  "/digest",
  "/adjust",
  "/sequence",
  "/validate-sequence",
  "/micro-adventure",
  "/commit",
  "/runtime-actions",
  "/runtime-batches",
];

const DEV_PORT = Number(process.env.TDTB_DEV_PORT ?? "") || 5273;
// The backend port `tdtb-dev` binds (TDTB_PORT there, default 8746), so a dev
// loop on a non-default port still proxies to the right place. An unparseable
// value falls back to the default rather than producing a broken target.
const BACKEND_PORT = /^\d+$/.test(process.env.TDTB_PORT ?? "")
  ? process.env.TDTB_PORT
  : "8746";
const DEV_BACKEND = `http://127.0.0.1:${BACKEND_PORT}`;

export default defineConfig(({ command, mode }) => {
  // `vite dev` runs in "development" mode, so `mode !== "production"` makes a
  // plain `npm run dev` compile the FIXTURE adapter — canned scenarios, no
  // real state. `tdtb-dev` exports TDTB_DEV_API=1 to compile the real API
  // adapter against the proxied backend instead. Serve-only and opt-in, so no
  // build (and therefore no committed bundle) can ever pick it up.
  const devApi = command === "serve" && process.env.TDTB_DEV_API === "1";

  return {
    plugins: [preact()],
    base: "./",
    define: {
      __FIXTURE__: JSON.stringify(mode !== "production" && !devApi),
    },
    server: {
      // Loopback-only: the backend binds 127.0.0.1 by contract and its
      // tokenless reads rely on that boundary.
      host: "127.0.0.1",
      port: DEV_PORT,
      // A silent port bump would make the URL `tdtb-dev` prints wrong.
      strictPort: true,
      proxy: Object.fromEntries(
        API_PREFIXES.map((prefix) => [
          prefix,
          { target: DEV_BACKEND, changeOrigin: true },
        ]),
      ),
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
  };
});
