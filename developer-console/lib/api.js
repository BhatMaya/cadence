// Cadence backend API client.
//
// Every authenticated developer request sends:
//     Authorization: Bearer <developer-access-token>
//
// This token is the Supabase developer access token returned by /login. It is
// NOT a server-side sk_live_ API key — those must never touch browser code.
import { API_BASE_URL } from "./config";
import { getToken } from "./storage";

// A typed-ish error so callers can branch on status (e.g. 401 -> sign out).
export class ApiError extends Error {
  constructor(message, { status, code, body } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.body = body;
  }
}

async function request(path, { method = "GET", body, auth = true, token } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const bearer = token ?? (auth ? getToken() : null);
  if (bearer) headers["Authorization"] = `Bearer ${bearer}`;

  let res;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (networkErr) {
    throw new ApiError(
      `Could not reach the Cadence API at ${API_BASE_URL}. Is the backend running?`,
      { status: 0, code: "network_error" }
    );
  }

  // Some endpoints (or errors) may return empty bodies.
  let data = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
  }

  if (!res.ok) {
    const message =
      data?.message || data?.error || `Request failed (${res.status})`;
    throw new ApiError(message, {
      status: res.status,
      code: data?.code || data?.status,
      body: data,
    });
  }

  return data;
}

// ---- Developer auth -------------------------------------------------------

export function signup({ email, password }) {
  return request("/v1/developer/signup", {
    method: "POST",
    auth: false,
    body: { email, password },
  });
}

export function login({ email, password }) {
  return request("/v1/developer/login", {
    method: "POST",
    auth: false,
    body: { email, password },
  });
}

// ---- App management -------------------------------------------------------

export function listApps() {
  return request("/v1/developer/apps");
}

export function createApp({ name, slug, allowed_origins, key_name }) {
  return request("/v1/developer/apps", {
    method: "POST",
    body: { name, slug, allowed_origins, key_name },
  });
}

// ---- API key management ---------------------------------------------------

export function listApiKeys(applicationId) {
  return request(`/v1/developer/apps/${applicationId}/api-keys`);
}

export function createApiKey(applicationId, { name }) {
  return request(`/v1/developer/apps/${applicationId}/api-keys`, {
    method: "POST",
    body: { name },
  });
}

export function revokeApiKey(apiKeyId) {
  return request(`/v1/developer/api-keys/${apiKeyId}/revoke`, {
    method: "POST",
  });
}

// ---- Usage ----------------------------------------------------------------

export function getUsage(applicationId) {
  return request(`/v1/developer/apps/${applicationId}/usage`);
}
