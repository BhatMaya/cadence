"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listApiKeys,
  createApiKey,
  revokeApiKey,
  ApiError,
} from "@/lib/api";
import { formatDate, formatRelative } from "@/lib/format";
import CopyButton from "./CopyButton";

export default function ApiKeysPanel({ appId, onAuthError, onRevealKey }) {
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [newKeyName, setNewKeyName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState(null);
  const [revokingId, setRevokingId] = useState(null);

  const handleApiError = useCallback(
    (err, fallback) => {
      if (err instanceof ApiError && err.status === 401) {
        onAuthError?.();
        return true;
      }
      return false;
    },
    [onAuthError]
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listApiKeys(appId);
      setKeys(res?.api_keys || []);
    } catch (err) {
      if (handleApiError(err)) return;
      setError(err instanceof ApiError ? err.message : "Could not load keys.");
    } finally {
      setLoading(false);
    }
  }, [appId, handleApiError]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreate(e) {
    e.preventDefault();
    setCreateError(null);
    setCreating(true);
    try {
      const res = await createApiKey(appId, {
        name: newKeyName.trim() || "default",
      });
      setNewKeyName("");
      // Reveal the full secret once via the shared modal.
      if (res?.api_key) {
        onRevealKey?.({
          apiKey: res.api_key,
          context: "New API key created.",
        });
      }
      await load();
    } catch (err) {
      if (handleApiError(err)) return;
      setCreateError(
        err instanceof ApiError ? err.message : "Could not create key."
      );
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(apiKeyId) {
    if (!confirm("Revoke this API key? Apps using it will stop working.")) {
      return;
    }
    setRevokingId(apiKeyId);
    try {
      await revokeApiKey(apiKeyId);
      await load();
    } catch (err) {
      if (handleApiError(err)) return;
      setError(err instanceof ApiError ? err.message : "Could not revoke key.");
    } finally {
      setRevokingId(null);
    }
  }

  return (
    <div>
      <div className="section-title">
        <h2>API keys</h2>
        <button className="btn btn-sm" onClick={load} disabled={loading}>
          Refresh
        </button>
      </div>
      <p className="section-desc">
        Server-side <span className="mono">sk_live_</span> keys for this app.
        Full keys are shown only once at creation — store them securely.
      </p>

      <div className="card">
        <form className="row wrap" onSubmit={handleCreate} style={{ gap: 10 }}>
          <input
            type="text"
            value={newKeyName}
            onChange={(e) => setNewKeyName(e.target.value)}
            placeholder="Key name (e.g. production, staging)"
            style={{ flex: 1, minWidth: 200 }}
          />
          <button className="btn btn-primary" type="submit" disabled={creating}>
            {creating && <span className="spinner" />}
            Create key
          </button>
        </form>
        {createError && (
          <div className="alert alert-error" style={{ marginTop: 12, marginBottom: 0 }}>
            <span className="alert-icon">!</span>
            <div>{createError}</div>
          </div>
        )}
      </div>

      <div className="card">
        {loading ? (
          <div className="stack">
            <div className="skeleton" style={{ height: 40 }} />
            <div className="skeleton" style={{ height: 40 }} />
          </div>
        ) : error ? (
          <div className="alert alert-error" style={{ marginBottom: 0 }}>
            <span className="alert-icon">!</span>
            <div>{error}</div>
          </div>
        ) : keys.length === 0 ? (
          <div className="empty">
            <div className="empty-title">No API keys</div>
            <div className="empty-desc">
              Create a key above to start calling the Cadence API.
            </div>
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Key</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Last used</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => {
                  const revoked = Boolean(k.revoked_at);
                  return (
                    <tr key={k.api_key_id}>
                      <td>{k.name}</td>
                      <td>
                        <span className="copyfield">
                          <code className="mono">{k.key_prefix}…</code>
                          <CopyButton value={k.api_key_id} label="Copy ID" />
                        </span>
                      </td>
                      <td>
                        {revoked ? (
                          <span className="badge revoked">
                            <span className="dot" /> Revoked
                          </span>
                        ) : (
                          <span className="badge active">
                            <span className="dot" /> Active
                          </span>
                        )}
                      </td>
                      <td className="faint text-sm">{formatDate(k.created_at)}</td>
                      <td className="faint text-sm">
                        {k.last_used_at ? formatRelative(k.last_used_at) : "never"}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        {!revoked && (
                          <button
                            className="btn btn-danger btn-sm"
                            onClick={() => handleRevoke(k.api_key_id)}
                            disabled={revokingId === k.api_key_id}
                          >
                            {revokingId === k.api_key_id ? (
                              <span className="spinner" />
                            ) : (
                              "Revoke"
                            )}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
