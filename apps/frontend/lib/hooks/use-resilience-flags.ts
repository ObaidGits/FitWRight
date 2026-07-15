'use client';

/**
 * useResilienceFlags — reads the P4 feature flags (GET /config/flags) once and
 * caches them (R6.4). Components use these to decide which durability paths to
 * activate: streaming vs non-stream, offline/SW registration, server autosave.
 * Falls back to safe defaults (autosave on, streaming/offline off) on error.
 */
import { useQuery } from '@tanstack/react-query';
import { fetchResilienceFlags, type ResilienceFlags } from '@/lib/api/config';

const DEFAULTS: ResilienceFlags = {
  streaming_ai: false,
  offline_support: false,
  advanced_autosave: true,
  stream_max_concurrent_per_user: 3,
  stream_max_lifetime_seconds: 300,
  stream_heartbeat_seconds: 15,
};

export function useResilienceFlags(): { flags: ResilienceFlags; isLoading: boolean } {
  const { data, isLoading } = useQuery({
    queryKey: ['config', 'flags'],
    queryFn: fetchResilienceFlags,
    staleTime: 5 * 60_000,
    gcTime: 30 * 60_000,
    retry: 1,
  });
  return { flags: data ?? DEFAULTS, isLoading };
}
