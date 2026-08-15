'use client';

/** Resumes library (Task 7.1 / Req 8). List + actions + filters + add entry. */
import * as React from 'react';
import Link from 'next/link';
import Plus from 'lucide-react/dist/esm/icons/plus';
import FileText from 'lucide-react/dist/esm/icons/file-text';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import Trash2 from 'lucide-react/dist/esm/icons/trash-2';
import Pencil from 'lucide-react/dist/esm/icons/pencil';
import RefreshCw from 'lucide-react/dist/esm/icons/refresh-cw';
import PenLine from 'lucide-react/dist/esm/icons/pen-line';
import Search from 'lucide-react/dist/esm/icons/search';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { useQueryClient } from '@tanstack/react-query';
import { renameResume } from '@/lib/api/resume';
import { queryKeys } from '@/lib/query/client';
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
import { ResumeThumbnail } from '@/components/resume/resume-thumbnail';
import { compareByMatchScore } from '@/lib/utils/resume-sort';
import type { ResumeListItem } from '@/lib/api/resume';

type Filter = 'all' | 'master' | 'tailored';
type SortKey = 'updated' | 'created' | 'name' | 'score';

const SORT_LABELS: Record<SortKey, string> = {
  updated: 'Recently updated',
  created: 'Recently added',
  name: 'Name (A-Z)',
  score: 'Best match first',
};

/** A score badge, or nothing. An unscored resume shows no badge rather than a
 *  zero: a master resume has no job to be measured against, so "0%" would read
 *  as a terrible match instead of an absent one. */
function ScoreBadge({ score }: { score?: number | null }) {
  if (score == null) return null;
  const rounded = Math.round(score);
  const variant = rounded >= 75 ? 'success' : rounded >= 50 ? 'warning' : 'danger';
  return (
    <Badge variant={variant} title="How well this resume matched its job description">
      {rounded}% match
    </Badge>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === 'ready') return <Badge variant="success">Ready</Badge>;
  if (status === 'failed') return <Badge variant="danger">Failed</Badge>;
  return <Badge variant="warning">Processing</Badge>;
}

export default function ResumesPage() {
  const { data, isLoading, isError, refetch } = useResumeLibrary();
  const del = useDeleteResume();
  const retry = useRetryProcessing();
  const qc = useQueryClient();
  // Which resume is mid-reprocess. The action lives in a dropdown that closes on
  // click, so without this the user clicks and watches nothing happen for a minute.
  const [retryingId, setRetryingId] = React.useState<string | null>(null);
  // Renaming. The endpoint and the API client for this already existed and nothing
  // ever called them, so a tailored resume could only ever carry its generated name.
  const [toRename, setToRename] = React.useState<ResumeListItem | null>(null);
  const [newTitle, setNewTitle] = React.useState('');
  const [renaming, setRenaming] = React.useState(false);
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
      if (sort === 'score') return compareByMatchScore(a, b);
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

  async function confirmRename() {
    if (!toRename) return;
    const title = newTitle.trim();
    if (!title) return;
    setRenaming(true);
    try {
      await renameResume(toRename.resume_id, title);
      await qc.invalidateQueries({ queryKey: queryKeys.resumes });
      toast({ title: 'Renamed', variant: 'success' });
      setToRename(null);
    } catch (err) {
      toast({
        title: 'Could not rename it',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    } finally {
      setRenaming(false);
    }
  }

  async function onRetry(id: string) {
    // The endpoint is synchronous - it runs the whole AI parse before answering,
    // which takes tens of seconds. So the card has to show that something is
    // happening, and the outcome has to come from the response rather than from
    // the request merely not throwing.
    setRetryingId(id);
    try {
      const result = await retry.mutateAsync(id);
      if (result.processing_status === 'ready') {
        toast({ title: 'Resume processed successfully', variant: 'success' });
      } else {
        // A failed parse still answers HTTP 200 with the reason. Reporting that as
        // "started" told the user it had worked when it had not.
        toast({
          title: 'Processing failed again',
          description: result.message ?? 'The AI could not read this resume.',
          variant: 'error',
        });
      }
    } catch (err) {
      toast({
        title: 'Retry failed',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    } finally {
      setRetryingId(null);
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
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {filtered.map((r) => (
            <Card key={r.resume_id} className="group relative flex flex-col overflow-hidden p-0">
              {/* The preview IS the affordance. A grid of documents is scannable by
                  shape and header the way a list of near-identical generated names
                  never is - which is the reason the thumbnail exists at all, and it
                  was wasted at row size. */}
              <Link
                href={`/builder?id=${r.resume_id}`}
                className="block bg-[var(--at-surface-2)] p-3 pb-0"
                tabIndex={-1}
                aria-hidden="true"
              >
                <ResumeThumbnail
                  resumeId={r.resume_id}
                  ready={r.processing_status === 'ready'}
                  fluid
                />
              </Link>

              <div className="flex min-w-0 flex-1 flex-col gap-2 p-3">
                <div className="min-w-0">
                  <Link
                    href={`/builder?id=${r.resume_id}`}
                    className="block truncate text-sm font-medium hover:text-[var(--primary)]"
                    title={r.title || r.filename || 'Untitled resume'}
                  >
                    {r.title || r.filename || 'Untitled resume'}
                  </Link>
                  <p className="text-xs text-[var(--muted-foreground)]">
                    {new Date(r.created_at).toLocaleDateString()}
                  </p>
                </div>

                {/* Badges wrap rather than compete for one line: at three columns a
                    card is narrow, and a master resume that also has a score and a
                    processing state would otherwise clip whichever came last. */}
                <div className="mt-auto flex flex-wrap items-center gap-1.5">
                  {r.is_master && <Badge variant="primary">Master</Badge>}
                  <ScoreBadge score={r.ats_score} />
                  <StatusBadge
                    status={retryingId === r.resume_id ? 'processing' : r.processing_status}
                  />
                </div>
              </div>

              {/* Pinned to the corner so it never shifts the card's text, and always
                  visible rather than hover-only - a menu that appears on hover is
                  unreachable by touch, which is most of this page's traffic. */}
              <div className="absolute right-1 top-1">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Actions for ${r.title || r.filename || 'this resume'}`}
                      className="h-7 w-7 bg-[var(--card)]/80 backdrop-blur-sm hover:bg-[var(--card)]"
                    >
                      ⋯
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem asChild>
                      <Link href={`/builder?id=${r.resume_id}`}>
                        <PenLine className="h-4 w-4" /> Open in editor
                      </Link>
                    </DropdownMenuItem>
                    <DropdownMenuItem asChild>
                      <Link href={`/tailor?resume=${r.resume_id}`}>
                        <Sparkles className="h-4 w-4" /> Tailor to a job
                      </Link>
                    </DropdownMenuItem>
                    {r.processing_status === 'failed' && (
                      <DropdownMenuItem
                        onClick={() => onRetry(r.resume_id)}
                        disabled={retryingId !== null}
                      >
                        <RefreshCw
                          className={`h-4 w-4 ${retryingId === r.resume_id ? 'animate-spin' : ''}`}
                        />
                        {retryingId === r.resume_id ? 'Processing…' : 'Retry processing'}
                      </DropdownMenuItem>
                    )}
                    <DropdownMenuItem
                      onClick={() => {
                        setToRename(r);
                        setNewTitle(r.title ?? '');
                      }}
                    >
                      <Pencil className="h-4 w-4" /> Rename
                    </DropdownMenuItem>
                    <DropdownMenuItem destructive onClick={() => setToDelete(r)}>
                      <Trash2 className="h-4 w-4" /> Delete
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={!!toRename} onOpenChange={(o) => !o && setToRename(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename resume</DialogTitle>
            <DialogDescription>
              Only the name changes. The resume content is untouched.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="resume-title">Name</Label>
            <Input
              id="resume-title"
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              maxLength={80}
              placeholder="Backend Engineer @ Globex"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newTitle.trim()) void confirmRename();
              }}
            />
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button
              loading={renaming}
              disabled={!newTitle.trim()}
              onClick={() => void confirmRename()}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
