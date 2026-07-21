'use client';

/** Resumes library (Task 7.1 / Req 8). List + actions + filters + add entry. */
import * as React from 'react';
import Link from 'next/link';
import Plus from 'lucide-react/dist/esm/icons/plus';
import FileText from 'lucide-react/dist/esm/icons/file-text';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import Trash2 from 'lucide-react/dist/esm/icons/trash-2';
import RefreshCw from 'lucide-react/dist/esm/icons/refresh-cw';
import PenLine from 'lucide-react/dist/esm/icons/pen-line';
import Search from 'lucide-react/dist/esm/icons/search';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Input } from '@/components/atelier/input';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/atelier/select';
import { EmptyState, LoadingSkeleton, ErrorState } from '@/components/atelier/states';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/atelier/dropdown-menu';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from '@/components/atelier/dialog';
import { useToast } from '@/components/atelier/toast';
import { useResumeLibrary, useDeleteResume, useRetryProcessing } from '@/features/resumes/hooks';
import type { ResumeListItem } from '@/lib/api/resume';

type Filter = 'all' | 'master' | 'tailored';
type SortKey = 'updated' | 'created' | 'name';

const SORT_LABELS: Record<SortKey, string> = {
  updated: 'Recently updated',
  created: 'Recently added',
  name: 'Name (A-Z)',
};

function StatusBadge({ status }: { status: string }) {
  if (status === 'ready') return <Badge variant="success">Ready</Badge>;
  if (status === 'failed') return <Badge variant="danger">Failed</Badge>;
  return <Badge variant="warning">Processing</Badge>;
}

export default function ResumesPage() {
  const { data, isLoading, isError, refetch } = useResumeLibrary();
  const del = useDeleteResume();
  const retry = useRetryProcessing();
  const { toast } = useToast();
  const [filter, setFilter] = React.useState<Filter>('all');
  const [search, setSearch] = React.useState('');
  const [sort, setSort] = React.useState<SortKey>('updated');
  const [toDelete, setToDelete] = React.useState<ResumeListItem | null>(null);

  const resumes = React.useMemo(() => data ?? [], [data]);

  function resumeName(r: ResumeListItem): string {
    return (r.title || r.filename || 'Untitled resume').toLowerCase();
  }

  // filter tab -> text search -> sort. Memoized so it scales to large libraries.
  const filtered = React.useMemo(() => {
    const q = search.trim().toLowerCase();
    const byTab = resumes.filter((r) =>
      filter === 'all' ? true : filter === 'master' ? r.is_master : !r.is_master
    );
    const bySearch = q ? byTab.filter((r) => resumeName(r).includes(q)) : byTab;
    const sorted = [...bySearch].sort((a, b) => {
      if (sort === 'name') return resumeName(a).localeCompare(resumeName(b));
      const key = sort === 'created' ? 'created_at' : 'updated_at';
      return (b[key] ?? '').localeCompare(a[key] ?? '');
    });
    return sorted;
  }, [resumes, filter, search, sort]);

  const searching = search.trim().length > 0;

  async function confirmDelete() {
    if (!toDelete) return;
    try {
      await del.mutateAsync(toDelete.resume_id);
      toast({ title: 'Resume deleted', variant: 'success' });
    } catch {
      toast({ title: 'Could not delete resume', variant: 'error' });
    } finally {
      setToDelete(null);
    }
  }

  async function onRetry(id: string) {
    try {
      await retry.mutateAsync(id);
      toast({ title: 'Reprocessing started', variant: 'info' });
    } catch {
      toast({ title: 'Retry failed', variant: 'error' });
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Resumes</h1>
          <p className="text-sm text-[var(--muted-foreground)]">
            Your master resume and tailored variants.
          </p>
        </div>
        <Button asChild>
          <Link href="/import">
            <Plus className="h-4 w-4" /> Add resume
          </Link>
        </Button>
      </div>

      {resumes.length > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex gap-1 rounded-[var(--radius-at-lg)] bg-[var(--secondary)] p-1 text-sm w-fit">
            {(['all', 'master', 'tailored'] as Filter[]).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                aria-pressed={filter === f}
                className={`rounded-[var(--radius-at-md)] px-3 py-1.5 capitalize transition-colors ${
                  filter === f
                    ? 'bg-[var(--card)] text-[var(--foreground)] shadow-[var(--shadow-at-e1)]'
                    : 'text-[var(--muted-foreground)] hover:text-[var(--foreground)]'
                }`}
              >
                {f}
              </button>
            ))}
          </div>

          <div className="relative min-w-0 flex-1 sm:max-w-xs">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
            <Input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search resumes..."
              aria-label="Search resumes"
              className="pl-9"
            />
          </div>

          <Select value={sort} onValueChange={(v) => setSort(v as SortKey)}>
            <SelectTrigger aria-label="Sort resumes" className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(SORT_LABELS) as SortKey[]).map((k) => (
                <SelectItem key={k} value={k}>
                  {SORT_LABELS[k]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {isLoading ? (
        <LoadingSkeleton rows={4} />
      ) : isError ? (
        <ErrorState description="Could not load your resumes." onRetry={() => refetch()} />
      ) : resumes.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No resumes yet"
          description="Upload a resume or build one with the wizard to get started."
          action={
            <div className="flex gap-2">
              <Button asChild>
                <Link href="/import">Upload resume</Link>
              </Button>
              <Button asChild variant="outline">
                <Link href="/wizard">Use wizard</Link>
              </Button>
            </div>
          }
        />
      ) : filtered.length === 0 && searching ? (
        <EmptyState
          icon={Search}
          title="No matches"
          description={`No resumes match "${search.trim()}".`}
          action={
            <Button variant="outline" onClick={() => setSearch('')}>
              Clear search
            </Button>
          }
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={FileText}
          title={filter === 'master' ? 'No master resume' : 'No tailored resumes'}
          description={
            filter === 'master'
              ? 'Upload or build a resume and mark it as your master.'
              : 'Tailor your resume to a job to create a tailored variant.'
          }
          action={
            <Button asChild variant="outline" onClick={() => setFilter('all')}>
              <Link href={filter === 'tailored' ? '/tailor' : '/import'}>
                {filter === 'tailored' ? 'Tailor to a job' : 'Add resume'}
              </Link>
            </Button>
          }
        />
      ) : (
        <div className="space-y-2">
          {filtered.map((r) => (
            <Card key={r.resume_id} className="flex items-center gap-3 p-4">
              <FileText className="h-5 w-5 shrink-0 text-[var(--muted-foreground)]" />
              <div className="min-w-0 flex-1">
                <Link
                  href={`/resumes/${r.resume_id}`}
                  className="block truncate font-medium hover:text-[var(--primary)]"
                >
                  {r.title || r.filename || 'Untitled resume'}
                </Link>
                <p className="text-xs text-[var(--muted-foreground)]">
                  {new Date(r.created_at).toLocaleDateString()}
                </p>
              </div>
              {r.is_master && <Badge variant="primary">Master</Badge>}
              <StatusBadge status={r.processing_status} />
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" aria-label="Resume actions">
                    ⋯
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem asChild>
                    <Link href={`/resumes/${r.resume_id}`}>
                      <PenLine className="h-4 w-4" /> Open in editor
                    </Link>
                  </DropdownMenuItem>
                  <DropdownMenuItem asChild>
                    <Link href={`/tailor?resume=${r.resume_id}`}>
                      <Sparkles className="h-4 w-4" /> Tailor to a job
                    </Link>
                  </DropdownMenuItem>
                  {r.processing_status === 'failed' && (
                    <DropdownMenuItem onClick={() => onRetry(r.resume_id)}>
                      <RefreshCw className="h-4 w-4" /> Retry processing
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem destructive onClick={() => setToDelete(r)}>
                    <Trash2 className="h-4 w-4" /> Delete
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={!!toDelete} onOpenChange={(o) => !o && setToDelete(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this resume?</DialogTitle>
            <DialogDescription>
              {toDelete?.is_master
                ? 'This is your master resume. It will be permanently deleted.'
                : 'This resume will be permanently deleted.'}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button variant="destructive" loading={del.isPending} onClick={confirmDelete}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
