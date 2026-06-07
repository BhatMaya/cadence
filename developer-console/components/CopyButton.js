"use client";

import { useState, useCallback } from "react";

// Copy-to-clipboard button with a transient "Copied" confirmation.
// Falls back to a hidden textarea + execCommand when the async Clipboard API
// is unavailable (e.g. non-secure origins during local dev over http).
export default function CopyButton({ value, label = "Copy", className = "" }) {
  const [copied, setCopied] = useState(false);

  const copy = useCallback(async () => {
    const text = typeof value === "function" ? value() : value;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* swallow — clipboard can be blocked; user can select manually */
    }
  }, [value]);

  return (
    <button
      type="button"
      onClick={copy}
      className={`copy-btn ${copied ? "copied" : ""} ${className}`}
      aria-label={label}
    >
      {copied ? "✓ Copied" : label}
    </button>
  );
}
