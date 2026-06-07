"use client";

import { useState } from "react";
import { createApp, ApiError } from "@/lib/api";
import { slugify } from "@/lib/format";

// Modal form for POST /v1/developer/apps. On success it bubbles up both the
// created application and the one-time API key to the parent.
export default function CreateAppModal({ onClose, onCreated, onAuthError }) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [origins, setOrigins] = useState("");
  const [keyName, setKeyName] = useState("production");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  function onNameChange(value) {
    setName(value);
    if (!slugTouched) setSlug(slugify(value));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);

    const allowed_origins = origins
      .split(/[\n,]+/)
      .map((o) => o.trim())
      .filter(Boolean);

    setBusy(true);
    try {
      const res = await createApp({
        name: name.trim(),
        slug: slugify(slug) || undefined,
        allowed_origins,
        key_name: keyName.trim() || "production",
      });
      onCreated(res); // { status, application, api_key }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onAuthError?.();
        return;
      }
      setError(
        err instanceof ApiError ? err.message : "Could not create the app."
      );
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Create a new app</h2>
          <button className="btn btn-ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <p className="muted text-sm mt-0" style={{ marginTop: -8 }}>
          Registers an application and issues its first server-side API key.
        </p>

        {error && (
          <div className="alert alert-error">
            <span className="alert-icon">!</span>
            <div>{error}</div>
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="app-name">App name</label>
            <input
              id="app-name"
              type="text"
              required
              value={name}
              onChange={(e) => onNameChange(e.target.value)}
              placeholder="My App"
            />
          </div>

          <div className="field">
            <label htmlFor="app-slug">Slug</label>
            <input
              id="app-slug"
              type="text"
              value={slug}
              onChange={(e) => {
                setSlugTouched(true);
                setSlug(e.target.value);
              }}
              placeholder="my-app"
            />
            <div className="hint">
              Lowercase identifier used in URLs. Auto-generated from the name.
            </div>
          </div>

          <div className="field">
            <label htmlFor="app-origins">Allowed origins</label>
            <textarea
              id="app-origins"
              value={origins}
              onChange={(e) => setOrigins(e.target.value)}
              placeholder={"https://app.example.com\nhttps://staging.example.com"}
            />
            <div className="hint">
              One origin per line (or comma-separated). Optional.
            </div>
          </div>

          <div className="field">
            <label htmlFor="app-keyname">First API key name</label>
            <input
              id="app-keyname"
              type="text"
              value={keyName}
              onChange={(e) => setKeyName(e.target.value)}
              placeholder="production"
            />
          </div>

          <div className="row" style={{ justifyContent: "flex-end", marginTop: 8 }}>
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={busy || !name.trim()}
            >
              {busy && <span className="spinner" />}
              Create app
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
