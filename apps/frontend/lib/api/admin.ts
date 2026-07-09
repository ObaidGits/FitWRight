/**
 * Admin data interface (Req 20, 26 / Task 15) — UI-only with MOCK data now.
 * The same typed interface swaps to real APIs later with no UI change.
 * When wired, access is enforced SERVER-SIDE by admin role (hiding UI ≠ security).
 */

export interface AdminStats {
  totalUsers: number;
  activeUsers: number;
  resumesTailored: number;
  coverLettersGenerated: number;
  signups: number;
}

export interface AdminUserRow {
  id: string;
  name: string;
  email: string;
  joinedAt: string;
  status: 'active' | 'disabled';
  usageCount: number;
}

export interface UsageSeriesPoint {
  date: string; // ISO date
  value: number;
}

export interface AdminApi {
  getStats(): Promise<AdminStats>;
  listUsers(query?: string): Promise<AdminUserRow[]>;
  setUserStatus(id: string, status: AdminUserRow['status']): Promise<void>;
  getUsageSeries(metric: 'signups' | 'active' | 'tailored'): Promise<UsageSeriesPoint[]>;
}

/* -------------------- mock data -------------------- */

function seededSeries(base: number, spread: number, days = 30): UsageSeriesPoint[] {
  const out: UsageSeriesPoint[] = [];
  const now = Date.now();
  let v = base;
  for (let i = days - 1; i >= 0; i--) {
    v = Math.max(0, v + Math.round((Math.sin(i / 3) + Math.cos(i / 5)) * spread));
    out.push({ date: new Date(now - i * 86_400_000).toISOString().slice(0, 10), value: v });
  }
  return out;
}

const MOCK_USERS: AdminUserRow[] = Array.from({ length: 24 }).map((_, i) => ({
  id: `u_${i + 1}`,
  name:
    ['Ava Chen', 'Liam Park', 'Noah Ali', 'Mia Rossi', 'Kai Wong', 'Zoe Adeyemi'][i % 6] +
    ` ${i + 1}`,
  email: `user${i + 1}@example.com`,
  joinedAt: new Date(Date.now() - i * 3 * 86_400_000).toISOString().slice(0, 10),
  status: i % 7 === 0 ? 'disabled' : 'active',
  usageCount: Math.round(3 + Math.abs(Math.sin(i)) * 40),
}));

export const adminApi: AdminApi = {
  async getStats() {
    return {
      totalUsers: MOCK_USERS.length,
      activeUsers: MOCK_USERS.filter((u) => u.status === 'active').length,
      resumesTailored: 187,
      coverLettersGenerated: 96,
      signups: 24,
    };
  },
  async listUsers(query) {
    const q = (query ?? '').trim().toLowerCase();
    if (!q) return MOCK_USERS;
    return MOCK_USERS.filter(
      (u) => u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q)
    );
  },
  async setUserStatus() {
    /* mock no-op; wired to a real endpoint later */
  },
  async getUsageSeries(metric) {
    if (metric === 'signups') return seededSeries(2, 1);
    if (metric === 'active') return seededSeries(10, 2);
    return seededSeries(6, 2);
  },
};
