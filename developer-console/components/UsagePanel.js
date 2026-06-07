"use client";

import { useCallback, useEffect, useState } from "react";
import { getUsage, ApiError } from "@/lib/api";
import { formatNumber, formatRelative } from "@/lib/format";

function Metric({ label, value, meta }) {
  return (
    <div className="metric">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {meta && <div className="meta">{meta}</div>}
    </div>
  );
}

export default function UsagePanel({ appId, onAuthError }) {
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getUsage(appId);
      setUsage(res?.usage ?? null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onAuthError?.();
        return;
      }
      setError(err instanceof ApiError ? err.message : "Could not load app data.");
    } finally {
      setLoading(false);
    }
  }, [appId, onAuthError]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <div className="metrics-grid">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="skeleton" style={{ height: 92 }} />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="alert alert-error">
        <span className="alert-icon">!</span>
        <div>
          {error}
          <button className="btn btn-sm mt-12" onClick={load} style={{ display: "block" }}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!usage) {
    return (
      <div className="empty">
        <div className="empty-title">No app data yet</div>
        <div className="empty-desc">
          App and API key details will appear here once the app is created.
        </div>
      </div>
    );
  }

  const application = usage.application || {};
  const keys = usage.api_keys || {};

  return (
    <div>
      <div className="section-title">
        <h2>App overview</h2>
        <button className="btn btn-sm" onClick={load}>
          Refresh
        </button>
      </div>

      <div className="metric-group-title">App</div>
      <div className="metrics-grid">
        <Metric
          label="Approval"
          value={application.approved === false ? "Pending" : "Approved"}
        />
        <Metric
          label="Allowed origins"
          value={formatNumber(application.allowed_origins?.length || 0)}
        />
      </div>

      <div className="metric-group-title">API keys</div>
      <div className="metrics-grid">
        <Metric label="Active keys" value={formatNumber(keys.active)} />
        <Metric label="Revoked keys" value={formatNumber(keys.revoked)} />
        <Metric
          label="Total keys"
          value={formatNumber(keys.total)}
          meta={
            keys.last_used_at
              ? `Last used ${formatRelative(keys.last_used_at)}`
              : "Never used"
          }
        />
      </div>
    </div>
  );
}
