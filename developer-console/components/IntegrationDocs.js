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

  const enrollExample = `curl -X POST ${base}/v1/enroll \\
  -H "Authorization: Bearer $CADENCE_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "external_user_id": "user_123",
    "source": "enrollment",
    "successful": true,
    "raw_data": {
      "events": [
        { "type": "down", "code": "KeyH", "t": 0.0 },
        { "type": "up",   "code": "KeyH", "t": 0.08 }
      ]
    }
  }'`;

  const scoreExample = `curl -X POST ${base}/v1/score \\
  -H "Authorization: Bearer $CADENCE_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "external_user_id": "user_123",
    "threshold": 0.5,
    "store_successful_sample": false,
    "raw_data": {
      "events": [
        { "type": "down", "code": "KeyH", "t": 0.0 },
        { "type": "up",   "code": "KeyH", "t": 0.07 }
      ]
    }
  }'`;

  const flowExample = `// 1. Browser — capture keystroke timings client-side
const events = [];
inputEl.addEventListener("keydown", (e) =>
  events.push({ type: "down", code: e.code, t: performance.now() / 1000 })
);
inputEl.addEventListener("keyup", (e) =>
  events.push({ type: "up", code: e.code, t: performance.now() / 1000 })
);

// 2. Send the captured sample to YOUR backend (no Cadence key in the browser)
await fetch("/api/typing/score", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ external_user_id, raw_data: { events } }),
});

// 3. Your backend forwards it to Cadence using the secret API key
//    (server-side only — keep sk_live_ keys out of client code)
await fetch("${base}/v1/score", {
  method: "POST",
  headers: {
    Authorization: \`Bearer \${process.env.CADENCE_API_KEY}\`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ external_user_id, raw_data: { events } }),
});`;

  return (
    <div>
      <div className="section-title">
        <h2>How to integrate {app.name}</h2>
      </div>

      <div className="alert alert-warning">
        <span className="alert-icon">⚠</span>
        <div>
          <strong>Never use your server-side API key in browser code.</strong>{" "}
          The <span className="mono">sk_live_</span> key grants full access to
          this app&apos;s enrollment and scoring. Anyone who reads it from your
          client bundle can impersonate your backend. Keep it in server
          environment variables only.
        </div>
      </div>

      <div className="card">
        <h3 style={{ fontSize: 14, marginBottom: 8 }}>Recommended flow</h3>
        <p className="muted text-sm mt-0">
          Browser apps should capture typing samples client-side, send them to{" "}
          <em>your own</em> backend, and have that backend call Cadence with the
          API key. The key never reaches the browser.
        </p>
        <ol className="muted text-sm" style={{ paddingLeft: 18, lineHeight: 1.9 }}>
          <li>Capture <code className="mono">keydown</code>/<code className="mono">keyup</code> events with timestamps in the browser.</li>
          <li>POST the raw sample to an endpoint on your backend.</li>
          <li>
            Your backend calls Cadence{" "}
            <code className="mono">/v1/enroll</code> or{" "}
            <code className="mono">/v1/score</code> with the secret key.
          </li>
          <li>Return the decision (accepted / rejected) to your client.</li>
        </ol>
        <CodeBlock>{flowExample}</CodeBlock>
      </div>

      <div className="card">
        <div className="row between">
          <h3 style={{ fontSize: 14 }}>Enroll a sample</h3>
          <code className="mono faint">POST /v1/enroll</code>
        </div>
        <p className="muted text-sm">
          Store a successful typing sample so future scores have a baseline to
          compare against. Call this until the user reaches the enrollment
          threshold.
        </p>
        <CodeBlock>{enrollExample}</CodeBlock>
      </div>

      <div className="card">
        <div className="row between">
          <h3 style={{ fontSize: 14 }}>Score a sample</h3>
          <code className="mono faint">POST /v1/score</code>
        </div>
        <p className="muted text-sm">
          Compare a fresh sample against the user&apos;s enrollment data. The
          response includes <code className="mono">score</code>,{" "}
          <code className="mono">accepted</code>, and a{" "}
          <code className="mono">reason</code>.
        </p>
        <CodeBlock>{scoreExample}</CodeBlock>
      </div>

      <div className="alert alert-info" style={{ marginBottom: 0 }}>
        <span className="alert-icon">i</span>
        <div>
          Set <span className="mono">CADENCE_API_KEY</span> from this app&apos;s
          API Keys tab. Requests are authenticated with{" "}
          <span className="mono">Authorization: Bearer &lt;sk_live_…&gt;</span>{" "}
          and base URL <span className="mono">{base}</span>.
        </div>
      </div>
    </div>
  );
}
