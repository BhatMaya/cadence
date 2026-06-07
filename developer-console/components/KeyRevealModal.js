"use client";

import CopyButton from "./CopyButton";

// Shows a freshly-created API key exactly once. The full secret is never
// retrievable again, so this modal makes copying it unmissable.
export default function KeyRevealModal({ apiKey, context, onClose }) {
  if (!apiKey) return null;
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Save your API key</h2>
          <button className="btn-ghost btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        {context && <p className="muted text-sm mt-0">{context}</p>}

        <div className="alert alert-warning">
          <span className="alert-icon">⚠</span>
          <div>
            This is the only time the full key{" "}
            <span className="mono">{apiKey.name}</span> will be shown. Store it
            in a secure secret manager now — you won&apos;t be able to see it
            again.
          </div>
        </div>

        <label className="faint text-sm">Secret key</label>
        <div className="key-reveal" style={{ marginTop: 6 }}>
          <code>{apiKey.key}</code>
          <CopyButton value={apiKey.key} label="Copy key" />
        </div>

        <div className="divider" />

        <div className="stack text-sm">
          <div className="row between">
            <span className="faint">Key ID</span>
            <span className="copyfield">
              <code>{apiKey.api_key_id}</code>
              <CopyButton value={apiKey.api_key_id} label="Copy" />
            </span>
          </div>
          <div className="row between">
            <span className="faint">Prefix</span>
            <code className="mono">{apiKey.key_prefix}</code>
          </div>
        </div>

        <div className="alert alert-info mt-20" style={{ marginBottom: 0 }}>
          <span className="alert-icon">i</span>
          <div>
            Use this <span className="mono">sk_live_</span> key only in trusted
            server-side code. Never embed it in browser or mobile client code.
          </div>
        </div>

        <button className="btn btn-primary btn-block mt-20" onClick={onClose}>
          I&apos;ve saved my key
        </button>
      </div>
    </div>
  );
}
