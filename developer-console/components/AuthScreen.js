"use client";

import { useState } from "react";
import { useAuth } from "@/app/auth-context";
import { login, signup, ApiError } from "@/lib/api";

export default function AuthScreen() {
  const { signIn } = useAuth();
  const [mode, setMode] = useState("login"); // "login" | "signup"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // When signup succeeds but the account needs email confirmation, we surface
  // a dedicated "check your email" state instead of the form.
  const [confirmPending, setConfirmPending] = useState(null);

  function switchMode(next) {
    setMode(next);
    setError(null);
    setConfirmPending(null);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "signup") {
        const res = await signup({ email, password });
        // Backend returns email_confirmed: false + session: null when the
        // developer must confirm their email before logging in.
        if (res?.session?.access_token && res?.email_confirmed !== false) {
          signIn({ developer: res.developer, session: res.session });
        } else {
          setConfirmPending({
            email: res?.developer?.email || email,
            message:
              res?.message ||
              "Check your email to confirm your developer account before logging in.",
          });
        }
      } else {
        const res = await login({ email, password });
        if (!res?.session?.access_token) {
          throw new ApiError("Login did not return an access token.");
        }
        signIn({ developer: res.developer, session: res.session });
      }
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Something went wrong. Please try again."
      );
    } finally {
      setBusy(false);
    }
  }

  // ---- Email-confirmation pending view ----
  if (confirmPending) {
    return (
      <div className="auth-screen">
        <div className="auth-card">
          <div className="brand">
            <span className="logo">C</span> Cadence
          </div>
          <div className="alert alert-warning mt-20">
            <span className="alert-icon">✉</span>
            <div>
              <strong>Check your email</strong>
              <div className="text-sm mt-0" style={{ marginTop: 4 }}>
                {confirmPending.message}
              </div>
            </div>
          </div>
          <p className="muted text-sm">
            We sent a confirmation link to{" "}
            <span className="mono">{confirmPending.email}</span>. After
            confirming, come back and log in.
          </p>
          <button
            className="btn btn-block mt-12"
            onClick={() => {
              setConfirmPending(null);
              setMode("login");
            }}
          >
            Back to login
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="brand">
          <span className="logo">C</span> Cadence
          <span className="sub">Developer Console</span>
        </div>
        <p className="auth-tagline">
          Integrate keystroke-dynamics authentication into your own apps.
        </p>

        <div className="tabs" role="tablist">
          <button
            className={mode === "login" ? "active" : ""}
            onClick={() => switchMode("login")}
            type="button"
          >
            Log in
          </button>
          <button
            className={mode === "signup" ? "active" : ""}
            onClick={() => switchMode("signup")}
            type="button"
          >
            Sign up
          </button>
        </div>

        {error && (
          <div className="alert alert-error">
            <span className="alert-icon">!</span>
            <div>{error}</div>
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="dev@example.com"
            />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete={
                mode === "signup" ? "new-password" : "current-password"
              }
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
            />
          </div>
          <button
            className="btn btn-primary btn-block"
            type="submit"
            disabled={busy}
          >
            {busy && <span className="spinner" />}
            {mode === "signup" ? "Create developer account" : "Log in"}
          </button>
        </form>

        <p className="faint text-sm mt-20" style={{ textAlign: "center" }}>
          {mode === "signup"
            ? "Already have an account? "
            : "Need a developer account? "}
          <a
            href="#"
            onClick={(e) => {
              e.preventDefault();
              switchMode(mode === "signup" ? "login" : "signup");
            }}
          >
            {mode === "signup" ? "Log in" : "Sign up"}
          </a>
        </p>
      </div>
    </div>
  );
}
