"use client";

import { useState } from "react";
import { updateAppThreshold, ApiError } from "@/lib/api";

const THRESHOLD_OPTIONS = [
  {
    id: "lenient",
    label: "Lenient",
    threshold: 0.4,
    description: "Accepts more real users when typing varies."
  },
  {
    id: "medium",
    label: "Medium",
    threshold: 0.5,
    description: "Balanced default for most apps."
  },
  {
    id: "strict",
    label: "High strictness",
    threshold: 0.65,
    description: "Requires a closer keystroke match."
  }
];

function optionForThreshold(threshold) {
  const value = Number(threshold);
  return (
    THRESHOLD_OPTIONS.find((option) => option.threshold === value) ||
    THRESHOLD_OPTIONS[1]
  );
}

export default function SettingsPanel({ app, onAuthError, onAppUpdated }) {
  const [selected, setSelected] = useState(optionForThreshold(app.threshold).id);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  async function chooseOption(option) {
    if (saving || option.id === selected) return;

    setSelected(option.id);
    setSaving(true);
    setError(null);
    setSaved(false);

    try {
      const res = await updateAppThreshold(app.application_id, {
        threshold: option.threshold
      });
      onAppUpdated?.({
        ...app,
        threshold: res?.application?.threshold ?? res?.threshold ?? option.threshold
      });
      setSaved(true);
    } catch (err) {
      setSelected(optionForThreshold(app.threshold).id);
      if (err instanceof ApiError && err.status === 401) {
        onAuthError?.();
        return;
      }
      setError(err instanceof ApiError ? err.message : "Could not update threshold.");
    } finally {
      setSaving(false);
    }
  }

  const activeOption = THRESHOLD_OPTIONS.find((option) => option.id === selected);

  return (
    <div>
      <div className="section-title">
        <h2>Settings</h2>
      </div>
      <p className="section-desc">
        Choose how closely a login attempt must match enrolled keystroke samples.
      </p>

      <div className="card">
        <div className="setting-header">
          <div>
            <h3>Keystroke checking</h3>
            <p className="setting-copy">
              Current threshold: <code>{activeOption?.threshold.toFixed(2)}</code>
            </p>
          </div>
          {saving && <span className="badge neutral">Saving</span>}
          {saved && !saving && <span className="badge active">Saved</span>}
        </div>

        {error && (
          <div className="alert alert-error">
            <span className="alert-icon">!</span>
            <div>{error}</div>
          </div>
        )}

        <div className="threshold-options" role="radiogroup" aria-label="Keystroke strictness">
          {THRESHOLD_OPTIONS.map((option) => (
            <button
              key={option.id}
              type="button"
              className={`threshold-option ${selected === option.id ? "active" : ""}`}
              onClick={() => chooseOption(option)}
              disabled={saving}
              role="radio"
              aria-checked={selected === option.id}
            >
              <span className="threshold-label">{option.label}</span>
              <span className="threshold-value">{option.threshold.toFixed(2)}</span>
              <span className="threshold-desc">{option.description}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
