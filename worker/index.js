// Shim worker: routes requests to the extraction container instance.
// Auth lives in the container app (HMAC over ts.body) — the shim forwards
// verbatim, so even direct workers.dev hits without a valid signature get 401
// from the app. The shared secret is a worker secret (wrangler secret put
// EXTRACT_SHARED_SECRET) and is injected into the container env at start.
import { Container, getContainer } from "@cloudflare/containers";

export class ExtractContainer extends Container {
  defaultPort = 8080;
  sleepAfter = "15m"; // scale to zero between extractions

  constructor(ctx, env) {
    super(ctx, env);
    this.envVars = {
      EXTRACT_SHARED_SECRET: env.EXTRACT_SHARED_SECRET || "",
      MAX_PDF_MB: "80",
      // Optional Leg 2.5 + photo-fallback keys. Empty string = leg disabled;
      // the app treats absence and emptiness identically (fail-soft no-op).
      GOOGLE_MAPS_API_KEY: env.GOOGLE_MAPS_API_KEY || "",
      MAPBOX_TOKEN: env.MAPBOX_TOKEN || "",
      EDGAR_USER_AGENT: env.EDGAR_USER_AGENT || "deckora-extract mdobson@arcainc.us",
    };
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/healthz" && url.pathname !== "/v1/extract") {
      return new Response("not found", { status: 404 });
    }
    // Single named instance: extraction is stateless, one warm container
    // serves all requests; Cloudflare spins it down after sleepAfter.
    const container = getContainer(env.EXTRACT_CONTAINER, "main");
    return container.fetch(request);
  },
};
