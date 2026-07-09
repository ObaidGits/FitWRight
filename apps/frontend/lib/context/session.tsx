'use client';

/**
 * Session provider (Task 8.1) — the real, backend-hydrated session.
 *
 * Responsibilities:
 * - Hydrate the current user from `GET /auth/session` via TanStack Query
 *   (short `staleTime`, `retry: false`), seeded with the SSR-resolved
 *   `initialUser` so there is no unauthenticated flash and no first-paint
 *   round-trip. Status is `loading | authenticated | guest`.
 * - `SINGLE_USER_MODE`: skip hydration entirely and expose the bootstrap owner
 *   (admin), so local zero-config boot behaves exactly like today (R14.3/15.5).
 * - Register the global 401 interceptor: on an expired session it clears the
 *   cached session, broadcasts a multi-tab logout, and routes to
 *   `/login?next=…` (R11.3).
 *
 * SECURITY NOTE: this is for conditional rendering + UX only. The server always
 * enforces access (`user_id` scoping, admin capability). Hiding UI is never the
 * boundary.
 */
import * as React from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchSession, logout as apiLogout, type SafeUser, type UserRole } from '@/lib/api/auth';
import { setUnauthorizedHandler } from '@/lib/api/client';
import { SINGLE_USER_MODE } from '@/lib/config/auth';
import { OWNER_USER } from '@/lib/api/session-owner';

export type { UserRole };
/** Back-compat alias — the session user is the backend `SafeUser`. */
export type AuthUser = SafeUser;
export type SessionStatus = 'authenticated' | 'loading' | 'guest';

interface SessionContextValue {
  user: SafeUser | null;
  status: SessionStatus;
  isAdmin: boolean;
  /** Re-fetch the session from the backend (after login/logout/profile change). */
  refresh: () => Promise<void>;
  /** Log out (revoke server session) and route to /login. */
  signOut: () => Promise<void>;
}

const SessionContext = React.createContext<SessionContextValue | undefined>(undefined);

/** Query key for the hydrated session. */
export const SESSION_QUERY_KEY = ['auth', 'session'] as const;

/** Auth routes where a 401 must NOT trigger another redirect (avoid loops). */
const AUTH_PATH_PREFIXES = ['/login', '/signup', '/forgot', '/reset', '/verify'];

const BROADCAST_CHANNEL = 'fitwright-auth';
const STORAGE_LOGOUT_KEY = 'fitwright-auth-logout';

export function SessionProvider({
  children,
  initialUser = null,
}: {
  children: React.ReactNode;
  initialUser?: SafeUser | null;
}) {
  if (SINGLE_USER_MODE) {
    return <SingleUserSessionProvider>{children}</SingleUserSessionProvider>;
  }
  return <MultiUserSessionProvider initialUser={initialUser}>{children}</MultiUserSessionProvider>;
}

/** Local/zero-config: the owner is always signed in (admin). No hydration. */
function SingleUserSessionProvider({ children }: { children: React.ReactNode }) {
  const value = React.useMemo<SessionContextValue>(
    () => ({
      user: OWNER_USER,
      status: 'authenticated',
      isAdmin: OWNER_USER.role === 'admin',
      refresh: async () => {},
      signOut: async () => {},
    }),
    []
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

function MultiUserSessionProvider({
  children,
  initialUser,
}: {
  children: React.ReactNode;
  initialUser: SafeUser | null;
}) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();

  const query = useQuery({
    queryKey: SESSION_QUERY_KEY,
    queryFn: fetchSession,
    // A real SSR user seeds the cache (no flash, no loading). `null` from SSR
    // (guest or backend unreachable) leaves `initialData` undefined so the
    // client does one authoritative fetch, surfacing a brief `loading` state
    // rather than a wrong `guest` flash.
    initialData: initialUser ?? undefined,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    retry: false,
    refetchOnWindowFocus: true,
  });

  const onExpiredRef = React.useRef<() => void>(() => {});

  // Keep a stable redirect+clear routine that reads the latest pathname.
  React.useEffect(() => {
    onExpiredRef.current = () => {
      queryClient.setQueryData(SESSION_QUERY_KEY, null);
      const path = typeof window !== 'undefined' ? window.location.pathname : pathname;
      const onAuthRoute = AUTH_PATH_PREFIXES.some((p) => path.startsWith(p));
      if (!onAuthRoute) {
        const next = encodeURIComponent(
          typeof window !== 'undefined' ? window.location.pathname + window.location.search : path
        );
        router.replace(`/login?next=${next}`);
      }
    };
  }, [pathname, queryClient, router]);

  // Register the single global 401 interceptor + multi-tab logout listeners.
  React.useEffect(() => {
    let channel: BroadcastChannel | null = null;
    const handleExpired = () => onExpiredRef.current();

    setUnauthorizedHandler(() => {
      // Local expiry: also tell the other tabs.
      try {
        channel?.postMessage({ type: 'logout' });
        window.localStorage.setItem(STORAGE_LOGOUT_KEY, String(Date.now()));
      } catch {
        /* storage/broadcast unavailable — local handling still runs */
      }
      handleExpired();
    });

    if (typeof BroadcastChannel !== 'undefined') {
      channel = new BroadcastChannel(BROADCAST_CHANNEL);
      channel.onmessage = (e: MessageEvent) => {
        if (e.data?.type === 'logout') handleExpired();
      };
    }
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_LOGOUT_KEY) handleExpired();
    };
    window.addEventListener('storage', onStorage);

    return () => {
      setUnauthorizedHandler(null);
      channel?.close();
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  const refresh = React.useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: SESSION_QUERY_KEY });
  }, [queryClient]);

  const signOut = React.useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      /* even if the network call fails, clear local state + redirect */
    }
    queryClient.setQueryData(SESSION_QUERY_KEY, null);
    try {
      new BroadcastChannel(BROADCAST_CHANNEL).postMessage({ type: 'logout' });
      window.localStorage.setItem(STORAGE_LOGOUT_KEY, String(Date.now()));
    } catch {
      /* best effort */
    }
    router.replace('/login');
  }, [queryClient, router]);

  const user = query.data ?? null;
  const status: SessionStatus = query.isLoading ? 'loading' : user ? 'authenticated' : 'guest';

  const value = React.useMemo<SessionContextValue>(
    () => ({
      user,
      status,
      isAdmin: user?.role === 'admin',
      refresh,
      signOut,
    }),
    [user, status, refresh, signOut]
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const ctx = React.useContext(SessionContext);
  if (!ctx) throw new Error('useSession must be used within a SessionProvider');
  return ctx;
}
