"use client";

import { useAuth } from "./auth-context";
import AuthScreen from "@/components/AuthScreen";
import Console from "@/components/Console";

export default function HomePage() {
  const { ready, isAuthenticated } = useAuth();

  // Avoid a flash of the wrong screen before localStorage is read.
  if (!ready) {
    return (
      <div className="loading-block" style={{ minHeight: "100vh" }}>
        <span className="spinner lg" />
      </div>
    );
  }

  return isAuthenticated ? <Console /> : <AuthScreen />;
}
