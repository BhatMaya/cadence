"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/app/auth-context";
import { listApps, ApiError } from "@/lib/api";
import CreateAppModal from "./CreateAppModal";
import KeyRevealModal from "./KeyRevealModal";
import AppDetail from "./AppDetail";

export default function Console() {
  const { developer, signOut } = useAuth();
  const [apps, setApps] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  // Holds a one-time API key (from app creation) to reveal in a modal.
  const [revealKey, setRevealKey] = useState(null);

  // Sign out on auth failure — the token is stale/invalid.
  const handleAuthError = useCallback(() => signOut(), [signOut]);

  const loadApps = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listApps();
      const list = res?.applications || [];
      setApps(list);
      // Keep current selection if still present, else select the first app.
      setSelectedId((prev) => {
        if (prev && list.some((a) => a.application_id === prev)) return prev;
        return list[0]?.application_id ?? null;
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleAuthError();
        return;
      }
      setError(
        err instanceof ApiError ? err.message : "Could not load your apps."
      );
    } finally {
      setLoading(false);
    }
  }, [handleAuthError]);

  useEffect(() => {
    loadApps();
  }, [loadApps]);

  function handleAppCreated(res) {
    setShowCreate(false);
    if (res?.api_key) {
      setRevealKey({
        apiKey: res.api_key,
        context: `New key for “${res.application?.name ?? "your app"}”.`,
      });
    }
    // Refresh list and select the new app.
    loadApps().then(() => {
      if (res?.application?.application_id) {
        setSelectedId(res.application.application_id);
      }
    });
  }

  const selectedApp = apps.find((a) => a.application_id === selectedId) || null;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="logo">C</span> Cadence
          <span className="sub">Developer Console</span>
        </div>
        <div className="topbar-right">
          {developer?.email && <span className="email">{developer.email}</span>}
          <button className="btn btn-sm" onClick={signOut}>
            Log out
          </button>
        </div>
      </header>

      <div className="workspace">
        <aside className="sidebar">
          <div className="sidebar-heading">
            <span>Apps</span>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setShowCreate(true)}
              title="Create app"
            >
              + New
            </button>
          </div>

          {loading ? (
            <div className="stack">
              <div className="skeleton" style={{ height: 44 }} />
              <div className="skeleton" style={{ height: 44 }} />
              <div className="skeleton" style={{ height: 44 }} />
            </div>
          ) : error ? (
            <div className="alert alert-error" style={{ marginBottom: 0 }}>
              <span className="alert-icon">!</span>
              <div>
                {error}
                <button
                  className="btn btn-sm mt-12"
                  onClick={loadApps}
                  style={{ display: "block" }}
                >
                  Retry
                </button>
              </div>
            </div>
          ) : apps.length === 0 ? (
            <div className="empty" style={{ padding: "24px 8px" }}>
              <div className="empty-title">No apps yet</div>
              <div className="empty-desc">
                Create your first app to get an API key.
              </div>
              <button
                className="btn btn-primary btn-sm"
                onClick={() => setShowCreate(true)}
              >
                + Create app
              </button>
            </div>
          ) : (
            <div className="app-list">
              {apps.map((app) => (
                <button
                  key={app.application_id}
                  className={`app-item ${
                    app.application_id === selectedId ? "active" : ""
                  }`}
                  onClick={() => setSelectedId(app.application_id)}
                >
                  <span className="name">{app.name}</span>
                  <span className="slug">{app.slug}</span>
                </button>
              ))}
            </div>
          )}
        </aside>

        <main className="main">
          {selectedApp ? (
            <AppDetail
              app={selectedApp}
              onAuthError={handleAuthError}
              onRevealKey={(payload) => setRevealKey(payload)}
            />
          ) : (
            !loading &&
            !error && (
              <div className="empty" style={{ marginTop: 60 }}>
                <div className="empty-title">Welcome to Cadence</div>
                <div className="empty-desc">
                  Create an app to manage API keys, monitor usage, and view
                  integration docs.
                </div>
                <button
                  className="btn btn-primary"
                  onClick={() => setShowCreate(true)}
                >
                  + Create your first app
                </button>
              </div>
            )
          )}
        </main>
      </div>

      {showCreate && (
        <CreateAppModal
          onClose={() => setShowCreate(false)}
          onCreated={handleAppCreated}
          onAuthError={handleAuthError}
        />
      )}

      {revealKey && (
        <KeyRevealModal
          apiKey={revealKey.apiKey}
          context={revealKey.context}
          onClose={() => setRevealKey(null)}
        />
      )}
    </div>
  );
}
