'use client';

/**
 * Admin users (Task 8.2/8.3) - real, fully-wired user management.
 *
 * Search + filters + cursor pagination are synced to the URL (shareable,
 * back-button safe). Status toggles are optimistic w/ rollback; role change is
 * pessimistic; delete is pessimistic behind a typed-email confirm. Self-targeting
 * and last-active-admin cases are guarded in the UI mirroring the server, and
 * the server remains the authoritative boundary. Desktop table adapts to a card
 * list on mobile; the detail drawer is focus-trapped (Sheet).
 */
import * as React from 'react';
import { Suspense } from 'react';
import { useRouter, usePathname, useSearchParams } from 'next/navigation';
import SearchIcon from 'lucide-react/dist/esm/icons/search';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { LoadingSkeleton, EmptyState, ErrorState } from '@/components/atelier/states';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atelier/select';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/atelier/table';
import { Sheet, SheetContent, SheetTitle } from '@/components/atelier/sheet';
import { useToast } from '@/components/atelier/toast';
import { useSession } from '@/lib/context/session';
import { LocalTime, RelativeTime } from '@/components/admin/local-time';
import { DeleteUserDialog } from '@/components/admin/delete-user-dialog';
import {
  useAdminUsers,
  useAdminUserDetail,
  useSetUserStatus,
  useSetUserRole,
  useRestoreUser,
  useBulkDisable,
} from '@/features/admin/hooks';
import { AdminApiError, type AdminUserRow, type UserListParams } from '@/lib/api/admin';

function StatusBadge({ status }: { status: AdminUserRow['status'] }) {
  if (status === 'active') return <Badge variant="success">active</Badge>;
  if (status === 'disabled') return <Badge variant="danger">disabled</Badge>;
  return <Badge variant="ai">pending</Badge>;
}

const PAGE_SIZE = 25;

export default function AdminUsersPage() {
  return (
    <Suspense fallback={<LoadingSkeleton rows={5} />}>
      <AdminUsersPageInner />
    </Suspense>
  );
}

function AdminUsersPageInner() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { user: sessionUser } = useSession();
  const { toast } = useToast();

  // URL-synced state.
  const q = params.get('q') ?? '';
  const status = (params.get('status') ?? '') as UserListParams['status'];
  const role = (params.get('role') ?? '') as UserListParams['role'];
  const deleted = params.get('deleted') === 'true';

  const [search, setSearch] = React.useState(q);
  const [cursorStack, setCursorStack] = React.useState<string[]>([]);
  const cursor = cursorStack[cursorStack.length - 1] ?? null;

  // Reset pagination when filters/search change.
  React.useEffect(() => {
    setCursorStack([]);
  }, [q, status, role, deleted]);

  const setParam = React.useCallback(
    (patch: Record<string, string | null>) => {
      const sp = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === '') sp.delete(k);
        else sp.set(k, v);
      }
      router.replace(sp.toString() ? `${pathname}?${sp.toString()}` : pathname);
    },
    [params, pathname, router]
  );

  // Debounce the search box into the URL.
  React.useEffect(() => {
    const t = setTimeout(() => {
      if (search !== q) setParam({ q: search || null });
    }, 350);
    return () => clearTimeout(t);
  }, [search, q, setParam]);

  const listParams: UserListParams = {
    q: q || undefined,
    status: status || undefined,
    role: role || undefined,
    deleted: deleted || undefined,
    cursor,
    limit: PAGE_SIZE,
  };
  const { data, isLoading, isError, error, refetch, isFetching } = useAdminUsers(listParams);

  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<AdminUserRow | null>(null);
  const [checked, setChecked] = React.useState<Set<string>>(new Set());

  const setStatus = useSetUserStatus();
  const setRole = useSetUserRole();
  const restore = useRestoreUser();
  const bulkDisable = useBulkDisable();

  const rows = data?.items ?? [];

  const runStatus = async (u: AdminUserRow, next: 'active' | 'disabled') => {
    try {
      await setStatus.mutateAsync({ id: u.id, status: next });
      toast({
        variant: 'success',
        title: `User ${next === 'active' ? 'enabled' : 'disabled'}`,
        description: u.email,
      });
    } catch (e) {
      const code = e instanceof AdminApiError ? e.code : 'error';
      toast({
        variant: 'error',
        title: 'Action failed',
        description:
          code === 'last_active_admin'
            ? 'This would disable the last active admin.'
            : e instanceof Error
              ? e.message
              : 'Failed.',
      });
    }
  };

  const runRole = async (u: AdminUserRow, next: 'user' | 'admin') => {
    try {
      await setRole.mutateAsync({ id: u.id, role: next });
      toast({ variant: 'success', title: `Role changed to ${next}`, description: u.email });
    } catch (e) {
      const code = e instanceof AdminApiError ? e.code : 'error';
      toast({
        variant: 'error',
        title: 'Role change failed',
        description:
          code === 'last_active_admin'
            ? 'This would remove the last active admin.'
            : code === 'self_action'
              ? 'You cannot change your own role.'
              : e instanceof Error
                ? e.message
                : 'Failed.',
      });
    }
  };

  const runRestore = async (u: AdminUserRow) => {
    try {
      await restore.mutateAsync(u.id);
      toast({ variant: 'success', title: 'User restored', description: u.email });
    } catch (e) {
      toast({ variant: 'error', title: 'Restore failed', description: (e as Error)?.message });
    }
  };

  const runBulkDisable = async () => {
    const ids = [...checked];
    try {
      const res = await bulkDisable.mutateAsync(ids);
      toast({
        variant: 'success',
        title: `Disabled ${res.disabled} user(s)`,
        description: res.skipped ? `${res.skipped} skipped` : undefined,
      });
      setChecked(new Set());
    } catch (e) {
      toast({ variant: 'error', title: 'Bulk disable failed', description: (e as Error)?.message });
    }
  };

  const isSelf = (id: string) => sessionUser?.id === id;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Users</h1>
        <p className="text-sm text-[var(--muted-foreground)]">Search and manage accounts.</p>
      </div>

      {/* Filters (URL-synced) */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[240px] flex-1">
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by email or name (prefix)..."
            className="pl-9"
            aria-label="Search users"
          />
        </div>
        <div className="w-36">
          <Select
            value={status || 'all'}
            onValueChange={(v) => setParam({ status: v === 'all' ? null : v })}
          >
            <SelectTrigger aria-label="Status filter">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="active">Active</SelectItem>
              <SelectItem value="disabled">Disabled</SelectItem>
              <SelectItem value="pending_verification">Pending</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="w-32">
          <Select
            value={role || 'all'}
            onValueChange={(v) => setParam({ role: v === 'all' ? null : v })}
          >
            <SelectTrigger aria-label="Role filter">
              <SelectValue placeholder="Role" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All roles</SelectItem>
              <SelectItem value="user">User</SelectItem>
              <SelectItem value="admin">Admin</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Button
          variant={deleted ? 'primary' : 'outline'}
          size="sm"
          onClick={() => setParam({ deleted: deleted ? null : 'true' })}
          aria-pressed={deleted}
        >
          {deleted ? 'Showing deleted' : 'Show deleted'}
        </Button>
      </div>

      {checked.size > 0 && (
        <div className="flex items-center gap-3 rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] px-4 py-2">
          <span className="text-sm">{checked.size} selected</span>
          <Button
            size="sm"
            variant="destructive"
            onClick={runBulkDisable}
            disabled={bulkDisable.isPending}
          >
            Disable selected
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setChecked(new Set())}>
            Clear
          </Button>
        </div>
      )}

      {/* aria-live so async list results are announced without stealing focus. */}
      <div aria-live="polite" aria-busy={isFetching}>
        {isError ? (
          <ErrorState
            title="Couldn't load users"
            description={(error as Error)?.message}
            onRetry={() => refetch()}
          />
        ) : isLoading ? (
          <LoadingSkeleton rows={5} />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={SearchIcon}
            title={q || status || role || deleted ? 'No matching users' : 'No users yet'}
            description={q ? 'Try a different search or clear the filters.' : undefined}
          />
        ) : (
          <>
            {/* Desktop table */}
            <Card className="hidden overflow-hidden p-0 md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8" />
                    <TableHead>Name</TableHead>
                    <TableHead>Email</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Joined</TableHead>
                    <TableHead>Resumes</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((u) => (
                    <TableRow key={u.id}>
                      <TableCell>
                        <input
                          type="checkbox"
                          aria-label={`Select ${u.email}`}
                          checked={checked.has(u.id)}
                          disabled={isSelf(u.id)}
                          onChange={(e) => {
                            setChecked((prev) => {
                              const next = new Set(prev);
                              if (e.target.checked) next.add(u.id);
                              else next.delete(u.id);
                              return next;
                            });
                          }}
                        />
                      </TableCell>
                      <TableCell className="font-medium">{u.name}</TableCell>
                      <TableCell className="text-[var(--muted-foreground)]">{u.email}</TableCell>
                      <TableCell>
                        {u.role === 'admin' ? <Badge variant="ai">admin</Badge> : 'user'}
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={u.status} />
                      </TableCell>
                      <TableCell className="text-[var(--muted-foreground)]">
                        <LocalTime iso={u.createdAt} />
                      </TableCell>
                      <TableCell>{u.resumeCount}</TableCell>
                      <TableCell className="text-right">
                        <Button size="sm" variant="ghost" onClick={() => setSelectedId(u.id)}>
                          View
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>

            {/* Mobile cards */}
            <div className="space-y-3 md:hidden">
              {rows.map((u) => (
                <Card key={u.id} className="p-4">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="font-medium">{u.name}</p>
                      <p className="text-sm text-[var(--muted-foreground)]">{u.email}</p>
                    </div>
                    <StatusBadge status={u.status} />
                  </div>
                  <div className="mt-3 flex items-center justify-between">
                    <span className="text-xs text-[var(--muted-foreground)]">
                      {u.role} - {u.resumeCount} resumes
                    </span>
                    <Button size="sm" variant="outline" onClick={() => setSelectedId(u.id)}>
                      View
                    </Button>
                  </div>
                </Card>
              ))}
            </div>

            {/* Cursor pagination */}
            <div className="flex items-center justify-between">
              <Button
                variant="outline"
                size="sm"
                disabled={cursorStack.length === 0 || isFetching}
                onClick={() => setCursorStack((s) => s.slice(0, -1))}
              >
                Previous
              </Button>
              <span className="text-xs text-[var(--muted-foreground)]">
                {isFetching ? 'Loading...' : `${rows.length} shown`}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={!data?.nextCursor || isFetching}
                onClick={() => data?.nextCursor && setCursorStack((s) => [...s, data.nextCursor!])}
              >
                Next
              </Button>
            </div>
          </>
        )}
      </div>

      <UserDetailDrawer
        userId={selectedId}
        onClose={() => setSelectedId(null)}
        isSelf={isSelf}
        onToggleStatus={runStatus}
        onChangeRole={runRole}
        onRestore={runRestore}
        onDelete={(u) => setDeleteTarget(u)}
        busy={setStatus.isPending || setRole.isPending || restore.isPending}
      />

      <DeleteUserDialog
        user={deleteTarget}
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        onDeleted={() => setSelectedId(null)}
      />
    </div>
  );
}

function UserDetailDrawer({
  userId,
  onClose,
  isSelf,
  onToggleStatus,
  onChangeRole,
  onRestore,
  onDelete,
  busy,
}: {
  userId: string | null;
  onClose: () => void;
  isSelf: (id: string) => boolean;
  onToggleStatus: (u: AdminUserRow, next: 'active' | 'disabled') => void;
  onChangeRole: (u: AdminUserRow, next: 'user' | 'admin') => void;
  onRestore: (u: AdminUserRow) => void;
  onDelete: (u: AdminUserRow) => void;
  busy: boolean;
}) {
  const { data, isLoading, isError, refetch } = useAdminUserDetail(userId);

  return (
    <Sheet open={!!userId} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full max-w-md overflow-y-auto p-6">
        <SheetTitle className="text-lg font-semibold">{data?.name ?? 'User'}</SheetTitle>
        {isError ? (
          <ErrorState title="Couldn't load user" onRetry={() => refetch()} className="mt-4" />
        ) : isLoading || !data ? (
          <LoadingSkeleton rows={3} className="mt-4" />
        ) : (
          <div className="mt-4 space-y-4 text-sm">
            <dl className="space-y-2">
              <Field label="Email" value={data.email} />
              <Field label="Role" value={data.role} />
              <Field label="Status" value={<StatusBadge status={data.status} />} />
              <Field label="Verified" value={data.emailVerified ? 'Yes' : 'No'} />
              <Field label="Sign-up" value={data.signupMethod} />
              <Field label="AI configured" value={data.aiConfigured ? 'Yes' : 'No'} />
              <Field label="Joined" value={<LocalTime iso={data.createdAt} />} />
              <Field label="Last active" value={<RelativeTime iso={data.lastActiveAt} />} />
              <Field label="Resumes" value={String(data.resumeCount)} />
              <Field label="Tailored" value={String(data.tailoredCount)} />
              <Field label="Applications" value={String(data.applicationCount)} />
              {data.deletedAt && (
                <Field
                  label="Deleted"
                  value={
                    <span className="text-[var(--destructive)]">
                      <LocalTime iso={data.deletedAt} /> - purge <LocalTime iso={data.purgeDueAt} />
                    </span>
                  }
                />
              )}
            </dl>

            {/* Actions (mirror server guards) */}
            <div className="flex flex-wrap gap-2 border-t border-[var(--border)] pt-4">
              {data.deletedAt ? (
                <Button size="sm" variant="outline" disabled={busy} onClick={() => onRestore(data)}>
                  Restore
                </Button>
              ) : (
                <>
                  {data.status === 'active' ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy || isSelf(data.id)}
                      title={isSelf(data.id) ? 'You cannot disable yourself' : undefined}
                      onClick={() => onToggleStatus(data, 'disabled')}
                    >
                      Disable
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => onToggleStatus(data, 'active')}
                    >
                      Enable
                    </Button>
                  )}
                  {data.role === 'user' ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => onChangeRole(data, 'admin')}
                    >
                      Make admin
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy || isSelf(data.id)}
                      title={isSelf(data.id) ? 'You cannot change your own role' : undefined}
                      onClick={() => onChangeRole(data, 'user')}
                    >
                      Revoke admin
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy || isSelf(data.id)}
                    title={isSelf(data.id) ? 'You cannot delete yourself' : undefined}
                    onClick={() => onDelete(data)}
                  >
                    Delete
                  </Button>
                </>
              )}
            </div>

            {/* Recent audit */}
            {data.recentAudit.length > 0 && (
              <div className="border-t border-[var(--border)] pt-4">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                  Recent activity
                </h3>
                <ul className="space-y-1.5">
                  {data.recentAudit.map((a) => (
                    <li key={a.id} className="flex items-center justify-between gap-2 text-xs">
                      <span className="font-mono">{a.event}</span>
                      <RelativeTime iso={a.ts} className="text-[var(--muted-foreground)]" />
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-[var(--muted-foreground)]">{label}</dt>
      <dd className="text-right">{value}</dd>
    </div>
  );
}
