/** Контекст авторизации: токен, роль, проверка прав. */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { api, setUnauthorizedHandler, tokenStore } from '@/api/client';
import type { Principal, UserRole, VersionInfo } from '@/api/types';

const ROLE_RANK: Record<UserRole, number> = { VIEWER: 0, ANALYST: 1, ADMIN: 2 };

interface AuthState {
  principal: Principal | null;
  version: VersionInfo | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  /** Права проверяются и на backend; здесь — только видимость элементов. */
  can: (required: UserRole) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [version, setVersion] = useState<VersionInfo | null>(null);
  const [loading, setLoading] = useState(true);

  const logout = useCallback(() => {
    tokenStore.clear();
    setPrincipal(null);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setPrincipal(null));
  }, []);

  useEffect(() => {
    let cancelled = false;

    const restore = async () => {
      try {
        // Версия читается всегда: она несет режим развертывания и дисклеймер,
        // которые нужны и на экране входа.
        const versionInfo = await api.version().catch(() => null);
        if (!cancelled && versionInfo) setVersion(versionInfo);

        if (tokenStore.get() || versionInfo?.deployment_mode === 'OPEN') {
          const me = await api.me();
          if (!cancelled) setPrincipal(me);
        }
      } catch {
        tokenStore.clear();
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void restore();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const token = await api.login(username, password);
    tokenStore.set(token.access_token);
    const me = await api.me();
    setPrincipal(me);
  }, []);

  const can = useCallback(
    (required: UserRole) =>
      principal ? ROLE_RANK[principal.role] >= ROLE_RANK[required] : false,
    [principal],
  );

  const value = useMemo(
    () => ({ principal, version, loading, login, logout, can }),
    [principal, version, loading, login, logout, can],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth должен вызываться внутри AuthProvider');
  return context;
}
