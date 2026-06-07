"use client";

import { useCallback, useEffect, useState } from "react";
import { getUsage, ApiError } from "@/lib/api";
import {
  formatNumber,
  formatPercent,
  formatMs,
  formatRelative,
} from "@/lib/format";

function Metric({ label, value, meta, small }) {
  return (
    <div className="metric">
      <div className="label">{label}</div>
      <div className={`value ${small ? "small" : ""}`}>{value}</div>
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
      setError(err instanceof ApiError ? err.message : "Could not load usage.");
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
        {Array.from({ length: 8 }).map((_, i) => (
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
        <div className="empty-title">No usage data yet</div>
        <div className="empty-desc">
          Once your app starts enrolling users and scoring samples, metrics will
          appear here.
        </div>
      </div>
    );
  }

  const keys = usage.api_keys || {};
  const endUsers = usage.end_users || {};
  const samples = usage.typing_samples || {};
  const scores = usage.score_requests || {};

  return (
    <div>
      <div className="section-title">
        <h2>Usage overview</h2>
        <button className="btn btn-sm" onClick={load}>
          Refresh
        </button>
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

      <div className="metric-group-title">End users</div>
      <div className="metrics-grid">
        <Metric label="Total end users" value={formatNumber(endUsers.total)} />
        <Metric
          label="Enrolled"
          value={formatNumber(endUsers.enrolled)}
          meta={
            endUsers.total
              ? `${formatPercent(
                  (endUsers.enrolled || 0) / endUsers.total
                )} of users`
              : undefined
          }
        />
      </div>

      <div className="metric-group-title">Typing samples</div>
      <div className="metrics-grid">
        <Metric label="Total samples" value={formatNumber(samples.total)} />
        <Metric
          label="Enrollment samples"
          value={formatNumber(samples.enrollment)}
        />
        <Metric
          label="Score-stored samples"
          value={formatNumber(samples.score_stored)}
        />
        <Metric label="Successful" value={formatNumber(samples.successful)} />
      </div>

      <div className="metric-group-title">Scoring</div>
      <div className="metrics-grid">
        <Metric label="Score requests" value={formatNumber(scores.total)} />
        <Metric label="Accepted" value={formatNumber(scores.accepted)} />
        <Metric label="Rejected" value={formatNumber(scores.rejected)} />
        <Metric
          label="Acceptance rate"
          value={formatPercent(scores.acceptance_rate)}
        />
        <Metric
          label="Avg latency"
          value={formatMs(scores.avg_score_duration_ms)}
          small
        />
        <Metric
          label="p95 latency"
          value={formatMs(scores.p95_score_duration_ms)}
          small
        />
      </div>

      {scores.reason_counts &&
        Object.keys(scores.reason_counts).length > 0 && (
          <div className="card mt-20">
            <h3 style={{ fontSize: 14, marginBottom: 12 }}>
              Score outcomes by reason
            </h3>
            <table className="table">
              <thead>
                <tr>
                  <th>Reason</th>
                  <th>Count</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(scores.reason_counts).map(([reason, count]) => (
                  <tr key={reason}>
                    <td className="mono">{reason}</td>
                    <td>{formatNumber(count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

      {scores.last_scored_at && (
        <p className="faint text-sm mt-20">
          Last score request {formatRelative(scores.last_scored_at)}.
        </p>
      )}
    </div>
  );
}
