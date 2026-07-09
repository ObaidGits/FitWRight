'use client';

import { useQuery } from '@tanstack/react-query';
import { adminApi } from '@/lib/api/admin';

export function useAdminStats() {
  return useQuery({ queryKey: ['admin', 'stats'], queryFn: () => adminApi.getStats() });
}
export function useAdminUsers(query: string) {
  return useQuery({
    queryKey: ['admin', 'users', query],
    queryFn: () => adminApi.listUsers(query),
  });
}
export function useUsageSeries(metric: 'signups' | 'active' | 'tailored') {
  return useQuery({
    queryKey: ['admin', 'series', metric],
    queryFn: () => adminApi.getUsageSeries(metric),
  });
}
