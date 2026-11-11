import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

// Production builds are a static export served by FastAPI (same origin).
// `next dev` cannot combine `output: "export"` with rewrites, so in dev we skip
// the export and proxy /api/* to the backend instead.
const BACKEND_URL = process.env.FINALLY_BACKEND_URL ?? "http://localhost:8000";

const base: NextConfig = {
  images: { unoptimized: true },
  trailingSlash: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default function config(phase: string): NextConfig {
  if (phase === PHASE_DEVELOPMENT_SERVER) {
    return {
      ...base,
      // gzip in the dev server buffers the proxied SSE stream; keep it off.
      compress: false,
      async rewrites() {
        return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
      },
    };
  }
  return { ...base, output: "export" };
}
