"use client";

import { API_BASE_URL } from "@/lib/config";
import CopyButton from "./CopyButton";

function CodeBlock({ children }) {
  return (
    <div style={{ position: "relative" }}>
      <div style={{ position: "absolute", top: 8, right: 8 }}>
        <CopyButton value={children} label="Copy" />
      </div>
      <pre className="codeblock">
        <code>{children}</code>
      </pre>
    </div>
  );
}

export default function IntegrationDocs({ app }) {
  const base = API_BASE_URL;
  const authExample = `// Cadence owns user accounts and typing history.
// Send users through your Cadence-backed signup/login experience.
await fetch("${base}/signup", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    email: "user@example.com",
    username: "user_123",
    password: "Strong-password-123"
  })
});

await fetch("${base}/authenticate", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    username: "user_123",
    password: "Strong-password-123",
    raw_data: encryptedKeystrokePayload,
    is_mobile: false
  })
});`;

  const thresholdExample = `curl -X PATCH ${base}/v1/apps/${app.application_id}/threshold \\
  -H "Authorization: Bearer $CADENCE_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{ "threshold": 0.4 }'`;

  return (
    <div>
      <div className="section-title">
        <h2>How to integrate {app.name}</h2>
      </div>

      <div className="alert alert-warning">
        <span className="alert-icon">!</span>
        <div>
          <strong>Never use your server-side API key in browser code.</strong>{" "}
          Keep <span className="mono">sk_live_</span> keys in server
          environment variables only.
        </div>
      </div>

      <div className="card">
        <h3 style={{ fontSize: 14, marginBottom: 8 }}>Current auth model</h3>
        <p className="muted text-sm mt-0">
          Cadence owns user identities and stores typing history in the core
          login tables. Integrations should use Cadence signup/authenticate
          flows for users instead of creating separate external end users.
        </p>
        <CodeBlock>{authExample}</CodeBlock>
      </div>

      <div className="card">
        <div className="row between">
          <h3 style={{ fontSize: 14 }}>Tune app threshold</h3>
          <code className="mono faint">PATCH /v1/apps/{app.application_id}/threshold</code>
        </div>
        <p className="muted text-sm">
          API keys are currently used for server-side app management. This
          endpoint updates the biometric acceptance threshold for this app.
        </p>
        <CodeBlock>{thresholdExample}</CodeBlock>
      </div>

      <div className="alert alert-info" style={{ marginBottom: 0 }}>
        <span className="alert-icon">i</span>
        <div>
          Set <span className="mono">CADENCE_API_KEY</span> from this app&apos;s
          API Keys tab. Requests are authenticated with{" "}
          <span className="mono">Authorization: Bearer &lt;sk_live_...&gt;</span>{" "}
          and base URL <span className="mono">{base}</span>.
        </div>
      </div>
    </div>
  );
}
