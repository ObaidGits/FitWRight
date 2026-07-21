/**
 * Notifications API (P3 §B, Requirements 4-6) - wired to the real backend.
 *
 * Persistent, user-scoped notifications (parsing done, AI ready, reminder due,
 * interview upcoming, security) with an O(1) unread badge, per-category
 * preferences, grouping, and mark-read/dismiss. Transient in-session events keep
 * using the toast system directly; anything durable flows through here.
 *
 * The client polls {@link unreadCount} (active tab only) at the interval the
 * server advertises; the transport field lets a future SSE mode swap in without
 * changing these calls.
 */
import { apiFetch, apiPost, apiDelete, apiPut } from './client';

export type NotificationKind = 'transient' | 'persistent';
export type NotificationCategory = 'system' | 'reminder' | 'interview' | 'ai' | 'security';
export type NotificationPriority = 'low' | 'normal' | 'high';
export type DigestMode = 'off' | 'daily' | 'weekly';

export interface AppNotification {
  id: string;
  kind: NotificationKind;
  type: string;
  category: NotificationCategory;
  priority: NotificationPriority;
  message: string;
  body?: string;
  nodeRef?: { type: 'resume' | 'application'; id: string };
  groupKey?: string;
  read: boolean;
  createdAt: string;
}

interface RawNotification {
  id: string;
  type: string;
  category: NotificationCategory;
  priority: NotificationPriority;
  title: string;
  body: string | null;
  node_type: string | null;
  node_id: string | null;
  group_key: string | null;
  read: boolean;
  created_at: string;
}

interface RawList {
  items: RawNotification[];
  next_cursor: string | null;
}

export interface UnreadCount {
  unread: number;
  transport: 'polling' | 'sse';
  pollIntervalSeconds: number;
}

export interface CategoryPref {
  in_app: boolean;
  email: boolean;
}

export interface NotificationPrefs {
  categories: Record<NotificationCategory, CategoryPref>;
  digest: DigestMode;
}

function mapNode(nodeType: string | null, nodeId: string | null): AppNotification['nodeRef'] {
  if (!nodeType || !nodeId) return undefined;
  if (nodeType === 'resume' || nodeType === 'application') {
    return { type: nodeType, id: nodeId };
  }
  // Reminders/interviews/jobs deep-link via the agenda/tracker; the center only
  // routes resume/application directly, so leave others unlinked here.
  return undefined;
}

function mapNotification(r: RawNotification): AppNotification {
  return {
    id: r.id,
    kind: 'persistent',
    type: r.type,
    category: r.category,
    priority: r.priority,
    message: r.title,
    body: r.body ?? undefined,
    nodeRef: mapNode(r.node_type, r.node_id),
    groupKey: r.group_key ?? undefined,
    read: r.read,
    createdAt: r.created_at,
  };
}

async function asJson<T>(res: Response, fallback: string): Promise<T> {
  if (res.status === 404) throw new Error('notifications_unavailable');
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
    const detail = typeof data.detail === 'string' ? data.detail : null;
    throw new Error(detail || `${fallback} (status ${res.status}).`);
  }
  return res.json() as Promise<T>;
}

export interface NotificationsApi {
  list(): Promise<AppNotification[]>;
  dismiss(id: string): Promise<void>;
  markRead(id: string): Promise<void>;
  markAllRead(): Promise<number>;
  unreadCount(): Promise<UnreadCount>;
}

/** List non-dismissed notifications (newest first). Empty when disabled. */
export async function list(opts?: {
  cursor?: string;
  unread?: boolean;
  category?: NotificationCategory;
}): Promise<{ items: AppNotification[]; nextCursor: string | null }> {
  const params = new URLSearchParams();
  if (opts?.cursor) params.set('cursor', opts.cursor);
  if (opts?.unread) params.set('unread', 'true');
  if (opts?.category) params.set('category', opts.category);
  const qs = params.toString() ? `?${params.toString()}` : '';
  const res = await apiFetch(`/notifications${qs}`, { credentials: 'include' });
  if (res.status === 404) return { items: [], nextCursor: null };
  const body = await asJson<RawList>(res, 'Failed to load notifications');
  return { items: body.items.map(mapNotification), nextCursor: body.next_cursor };
}

export async function unreadCount(): Promise<UnreadCount> {
  const res = await apiFetch('/notifications/unread-count', { credentials: 'include' });
  if (res.status === 404) return { unread: 0, transport: 'polling', pollIntervalSeconds: 60 };
  const body = await asJson<{
    unread: number;
    transport: 'polling' | 'sse';
    poll_interval_seconds: number;
  }>(res, 'Failed to load unread count');
  return {
    unread: body.unread,
    transport: body.transport,
    pollIntervalSeconds: body.poll_interval_seconds,
  };
}

export async function markRead(id: string): Promise<void> {
  await asJson(await apiPost(`/notifications/${id}/read`, {}), 'Failed to mark read');
}

export async function markAllRead(): Promise<number> {
  const body = await asJson<{ affected: number }>(
    await apiPost('/notifications/read-all', {}),
    'Failed to mark all read'
  );
  return body.affected;
}

export async function dismiss(id: string): Promise<void> {
  await asJson(await apiDelete(`/notifications/${id}`), 'Failed to dismiss notification');
}

export async function dismissGroup(groupKey: string): Promise<number> {
  const body = await asJson<{ affected: number }>(
    await apiPost('/notifications/dismiss-group', { group_key: groupKey }),
    'Failed to dismiss group'
  );
  return body.affected;
}

export async function getPrefs(): Promise<NotificationPrefs> {
  return asJson<NotificationPrefs>(
    await apiFetch('/notifications/prefs', { credentials: 'include' }),
    'Failed to load preferences'
  );
}

export async function updatePrefs(update: {
  categories?: { category: NotificationCategory; in_app: boolean; email: boolean }[];
  digest?: DigestMode;
}): Promise<NotificationPrefs> {
  return asJson<NotificationPrefs>(
    await apiPut('/notifications/prefs', update),
    'Failed to update preferences'
  );
}

export const notificationsApi: NotificationsApi = {
  async list() {
    return (await list()).items;
  },
  dismiss,
  markRead,
  markAllRead,
  unreadCount,
};
