// Thin, SSR-safe wrappers around localStorage for the developer session.
// All reads/writes are guarded so they never throw during server rendering or
// in private-mode browsers where storage may be unavailable.
import { STORAGE_KEYS } from "./config";

function safeGet(key) {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key, value) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* ignore quota / disabled storage */
  }
}

function safeRemove(key) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

export function getToken() {
  return safeGet(STORAGE_KEYS.token);
}

export function getDeveloper() {
  const raw = safeGet(STORAGE_KEYS.developer);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

// Persist a successful login session.
export function saveSession({ developer, session }) {
  if (session?.access_token) safeSet(STORAGE_KEYS.token, session.access_token);
  if (session?.refresh_token) safeSet(STORAGE_KEYS.refresh, session.refresh_token);
  if (developer) safeSet(STORAGE_KEYS.developer, JSON.stringify(developer));
}

export function clearSession() {
  safeRemove(STORAGE_KEYS.token);
  safeRemove(STORAGE_KEYS.refresh);
  safeRemove(STORAGE_KEYS.developer);
}
