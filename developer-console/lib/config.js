// Central configuration. The API base URL is read from a public env var so it
// can be set per-deployment, with a sensible local fallback.
export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_CADENCE_API_BASE_URL || "http://localhost:5001"
).replace(/\/+$/, "");

// localStorage keys. Namespaced so they never collide with anything else on
// the origin during local development.
export const STORAGE_KEYS = {
  token: "cadence.dev.accessToken",
  refresh: "cadence.dev.refreshToken",
  developer: "cadence.dev.developer",
};
