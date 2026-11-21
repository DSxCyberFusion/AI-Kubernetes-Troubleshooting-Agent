"use client";

import type { UserSchema } from "@insforge/sdk";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import { getInsforgeClient } from "@/lib/insforge";

type AuthContextValue = {
  user: UserSchema | null;
  loading: boolean;
  configurationError: string | null;
  authError: string | null;
  signIn: (email: string, password: string) => Promise<string | null>;
  signInWithGoogle: () => Promise<string | null>;
  signOut: () => Promise<string | null>;
  getAccessToken: () => Promise<string>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "An unexpected authentication error occurred.";
}

export function AuthProvider({ children }: Readonly<{ children: React.ReactNode }>) {
  const clientRef = useRef<ReturnType<typeof getInsforgeClient> | null>(null);
  const [user, setUser] = useState<UserSchema | null>(null);
  const [loading, setLoading] = useState(true);
  const [configurationError, setConfigurationError] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let client: ReturnType<typeof getInsforgeClient>;

    try {
      client = getInsforgeClient();
      clientRef.current = client;
    } catch (error) {
      setConfigurationError(errorMessage(error));
      setLoading(false);
      return () => {
        active = false;
      };
    }

    const refreshUser = async () => {
      try {
        const timeout = new Promise<{ data: { user: null }; error: Error }>((resolve) => {
          window.setTimeout(() => {
            resolve({
              data: { user: null },
              error: new Error("Session check timed out."),
            });
          }, 8000);
        });
        const result = await Promise.race([
          client.auth.getCurrentUser(),
          timeout,
        ]);
        const { data, error } = result;
        if (!active) return;
        if (error) {
          const isUnauthorized =
            "statusCode" in error && error.statusCode === 401;
          const isInvalidCsrf = /invalid csrf token/i.test(error.message);
          setAuthError(isUnauthorized || isInvalidCsrf ? null : error.message);
          setUser(null);
        } else {
          setAuthError(null);
          setUser(data.user);
        }
      } catch (error) {
        if (active) {
          setAuthError(errorMessage(error));
          setUser(null);
        }
      } finally {
        if (active) setLoading(false);
      }
    };

    const unsubscribe = client.auth.onAuthStateChange(() => {
      void refreshUser();
    });
    void refreshUser();

    return () => {
      active = false;
      unsubscribe();
      clientRef.current = null;
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const client = clientRef.current;
    if (!client) {
      return configurationError ?? "Authentication is not configured.";
    }

    setAuthError(null);
    try {
      const { data, error } = await client.auth.signInWithPassword({ email, password });
      if (error) {
        setAuthError(error.message);
        return error.message;
      }
      setUser(data?.user ?? null);
      return null;
    } catch (error) {
      const message = errorMessage(error);
      setAuthError(message);
      return message;
    }
  }, [configurationError]);

  const signOut = useCallback(async () => {
    const client = clientRef.current;
    if (!client) {
      return configurationError ?? "Authentication is not configured.";
    }

    setAuthError(null);
    try {
      const { error } = await client.auth.signOut();
      if (error) {
        setAuthError(error.message);
        return error.message;
      }
      setUser(null);
      return null;
    } catch (error) {
      const message = errorMessage(error);
      setAuthError(message);
      return message;
    }
  }, [configurationError]);

  const signInWithGoogle = useCallback(async () => {
    const client = clientRef.current;
    if (!client) {
      return configurationError ?? "Authentication is not configured.";
    }

    setAuthError(null);
    try {
      const { error } = await client.auth.signInWithOAuth("google", {
        redirectTo: window.location.origin,
      });
      if (error) {
        setAuthError(error.message);
        return error.message;
      }
      return null;
    } catch (error) {
      const message = errorMessage(error);
      setAuthError(message);
      return message;
    }
  }, [configurationError]);

  const getAccessToken = useCallback(async () => {
    const client = clientRef.current;
    if (!client) {
      throw new Error(configurationError ?? "Authentication is not configured.");
    }

    const { data, error } = await client.auth.refreshSession();
    if (error) throw error;
    if (!data?.accessToken) {
      throw new Error("Your session has expired. Sign in again to continue.");
    }
    return data.accessToken;
  }, [configurationError]);

  return (
    <AuthContext.Provider
      value={{ user, loading, configurationError, authError, signIn, signInWithGoogle, signOut, getAccessToken }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider.");
  }
  return context;
}
