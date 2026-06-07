"use client";

import { createContext, useContext, useEffect, useState, useCallback } from "react";
import {
  getToken,
  getDeveloper,
  saveSession,
  clearSession,
} from "@/lib/storage";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  // `ready` flips true once we've read localStorage on the client, which
  // avoids a hydration flash where the UI renders logged-out then logged-in.
  const [ready, setReady] = useState(false);
  const [token, setToken] = useState(null);
  const [developer, setDeveloper] = useState(null);

  useEffect(() => {
    setToken(getToken());
    setDeveloper(getDeveloper());
    setReady(true);
  }, []);

  const signIn = useCallback(({ developer, session }) => {
    saveSession({ developer, session });
    setToken(session?.access_token ?? null);
    setDeveloper(developer ?? null);
  }, []);

  const signOut = useCallback(() => {
    clearSession();
    setToken(null);
    setDeveloper(null);
  }, []);

  const value = {
    ready,
    token,
    developer,
    isAuthenticated: Boolean(token),
    signIn,
    signOut,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
