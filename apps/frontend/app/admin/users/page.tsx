'use client';

/** Admin users (Task 15.3) — searchable table + status + detail drawer (mock). */
import * as React from 'react';
import SearchIcon from 'lucide-react/dist/esm/icons/search';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { LoadingSkeleton } from '@/components/atelier/states';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/atelier/table';
import { Sheet, SheetContent, SheetTitle } from '@/components/atelier/sheet';
import { useAdminUsers } from '@/features/admin/hooks';
import type { AdminUserRow } from '@/lib/api/admin';

export default function AdminUsersPage() {
  const [query, setQuery] = React.useState('');
  const { data, isLoading } = useAdminUsers(query);
  const [selected, setSelected] = React.useState<AdminUserRow | null>(null);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Users</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          Search and manage accounts. <Badge variant="ai">Demo data</Badge>
        </p>
      </div>

      <div className="relative max-w-sm">
        <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name or email…"
          className="pl-9"
          aria-label="Search users"
        />
      </div>

      {isLoading ? (
        <LoadingSkeleton rows={4} />
      ) : (
        <Card className="overflow-hidden p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Joined</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Usage</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data ?? []).map((u) => (
                <TableRow key={u.id}>
                  <TableCell className="font-medium">{u.name}</TableCell>
                  <TableCell className="text-[var(--muted-foreground)]">{u.email}</TableCell>
                  <TableCell className="text-[var(--muted-foreground)]">{u.joinedAt}</TableCell>
                  <TableCell>
                    <Badge variant={u.status === 'active' ? 'success' : 'danger'}>{u.status}</Badge>
                  </TableCell>
                  <TableCell>{u.usageCount}</TableCell>
                  <TableCell className="text-right">
                    <Button size="sm" variant="ghost" onClick={() => setSelected(u)}>
                      View
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      <Sheet open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <SheetContent side="right" className="p-6">
          <SheetTitle className="text-lg font-semibold">{selected?.name}</SheetTitle>
          {selected && (
            <dl className="mt-4 space-y-3 text-sm">
              <div>
                <dt className="text-[var(--muted-foreground)]">Email</dt>
                <dd>{selected.email}</dd>
              </div>
              <div>
                <dt className="text-[var(--muted-foreground)]">Joined</dt>
                <dd>{selected.joinedAt}</dd>
              </div>
              <div>
                <dt className="text-[var(--muted-foreground)]">Status</dt>
                <dd>
                  <Badge variant={selected.status === 'active' ? 'success' : 'danger'}>
                    {selected.status}
                  </Badge>
                </dd>
              </div>
              <div>
                <dt className="text-[var(--muted-foreground)]">Usage</dt>
                <dd>{selected.usageCount} actions</dd>
              </div>
              <div className="flex gap-2 pt-2">
                <Button size="sm" variant="outline">
                  {selected.status === 'active' ? 'Disable' : 'Enable'}
                </Button>
                <Button size="sm" variant="destructive">
                  Delete
                </Button>
              </div>
              <p className="pt-1 text-xs text-[var(--muted-foreground)]">
                Actions are stubbed — wired to real endpoints (with server-side RBAC) in Phase 2.
              </p>
            </dl>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
