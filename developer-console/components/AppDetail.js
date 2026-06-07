"use client";

import { useState } from "react";
import CopyButton from "./CopyButton";
import UsagePanel from "./UsagePanel";
import ApiKeysPanel from "./ApiKeysPanel";
import IntegrationDocs from "./IntegrationDocs";

const TABS = [
  { id: "usage", label: "Usage" },
  { id: "keys", label: "API Keys" },
  { id: "integrate", label: "Integrate" },
];

export default function AppDetail({ app, onAuthError, onRevealKey }) {
  const [tab, setTab] = useState("usage");

  return (
    <div>
      <div className="page-header">
        <div className="row between wrap">
          <div>
            <h1>{app.name}</h1>
            <span className="slug">{app.slug}</span>
          </div>
        </div>
        <div className="row wrap" style={{ marginTop: 12, gap: 16 }}>
          <span className="copyfield text-sm">
            <span className="faint">App ID</span>
            <code>{app.application_id}</code>
            <CopyButton value={app.application_id} label="Copy" />
          </span>
          {Array.isArray(app.allowed_origins) &&
            app.allowed_origins.length > 0 && (
              <span className="text-sm faint">
                {app.allowed_origins.length} allowed origin
                {app.allowed_origins.length === 1 ? "" : "s"}
              </span>
            )}
        </div>
      </div>

      <nav className="tabnav">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={tab === t.id ? "active" : ""}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {/* key={app.application_id} forces panels to refetch when the app changes */}
      {tab === "usage" && (
        <UsagePanel
          key={app.application_id}
          appId={app.application_id}
          onAuthError={onAuthError}
        />
      )}
      {tab === "keys" && (
        <ApiKeysPanel
          key={app.application_id}
          appId={app.application_id}
          onAuthError={onAuthError}
          onRevealKey={onRevealKey}
        />
      )}
      {tab === "integrate" && <IntegrationDocs app={app} />}
    </div>
  );
}
