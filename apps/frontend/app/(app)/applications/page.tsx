'use client';

/**
 * Applications pipeline (Task 9.1 + Task 12 / Req 17, 28).
 *
 * Kanban board + list view across the full lifecycle. Cards move three ways so
 * every device is served intentionally:
 *   • drag-and-drop (mouse, touch, and keyboard via dnd-kit) on the board,
 *   • a one-tap "advance to next stage" control (large touch target) — the
 *     accessible equivalent of swipe-to-advance for mobile,
 *   • an explicit stage menu (works everywhere, screen-reader friendly).
 * Auto-populated on tailor; cards open the Application Workspace.
 */
import * as React from 'react';
import Link from 'next/link';
import {
  DndContext,
  type DragEndEvent,
  PointerSensor,
  KeyboardSensor,
  closestCorners,
  useSensor,
  useSensors,
  useDroppable,
} from '@dnd-kit/core';
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import Layers from 'lucide-react/dist/esm/icons/layers';
import Columns from 'lucide-react/dist/esm/icons/columns-3';
import ListIcon from 'lucide-react/dist/esm/icons/list';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';
import MoveHorizontal from 'lucide-react/dist/esm/icons/move-horizontal';
import GripVertical from 'lucide-react/dist/esm/icons/grip-vertical';
import CircleArrowRight from 'lucide-react/dist/esm/icons/circle-arrow-right';
import Search from 'lucide-react/dist/esm/icons/search';
import ChevronLeft from 'lucide-react/dist/esm/icons/chevron-left';
import ChevronRight from 'lucide-react/dist/esm/icons/chevron-right';

import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { EmptyState, LoadingSkeleton, ErrorState } from '@/components/atelier/states';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/components/atelier/dropdown-menu';
import { useToast } from '@/components/atelier/toast';
import {
  APPLICATION_STATUS_ORDER,
  updateApplication,
  type Application,
  type ApplicationColumns,
  type ApplicationStatus,
} from '@/lib/api/tracker';
import { useApplicationsBoard, STATUS_LABELS } from '@/features/applications/hooks';
import { useQueryClient } from '@tanstack/react-query';
import { invalidateApplicationLists } from '@/lib/query/client';
import { planMove } from '@/components/tracker/reorder';
import { cn } from '@/lib/utils';

function nextStage(status: ApplicationStatus): ApplicationStatus | null {
  const i = APPLICATION_STATUS_ORDER.indexOf(status);
  return i >= 0 && i < APPLICATION_STATUS_ORDER.length - 1 ? APPLICATION_STATUS_ORDER[i + 1] : null;
}

function emptyColumns(): ApplicationColumns {
  return APPLICATION_STATUS_ORDER.reduce((acc, s) => {
    acc[s] = [];
    return acc;
  }, {} as ApplicationColumns);
}

function ApplicationCard({
  app,
  draggable,
  onMove,
}: {
  app: Application;
  draggable: boolean;
  onMove: (s: ApplicationStatus) => void;
}) {
  const sortable = useSortable({ id: app.application_id, disabled: !draggable });
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = sortable;
  const advance = nextStage(app.status);

  return (
    <Card
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn('p-3.5', isDragging && 'opacity-60 shadow-[var(--shadow-at-e3)]')}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-1 items-start gap-1.5">
          {draggable && (
            <button
              type="button"
              aria-label="Drag to move"
              className="mt-0.5 cursor-grab touch-none text-[var(--muted-foreground)] active:cursor-grabbing"
              {...attributes}
              {...listeners}
            >
              <GripVertical className="h-4 w-4" />
            </button>
          )}
          <Link href={`/applications/${app.application_id}`} className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium hover:text-[var(--primary)]">
              {app.role || 'Untitled role'}
            </p>
            <p className="truncate text-xs text-[var(--muted-foreground)]">
              {app.company || 'Unknown company'}
            </p>
          </Link>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="h-7 w-7" aria-label="Move application">
              <MoveHorizontal className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>Move to</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {APPLICATION_STATUS_ORDER.filter((s) => s !== app.status).map((s) => (
              <DropdownMenuItem key={s} onClick={() => onMove(s)}>
                {STATUS_LABELS[s]}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <div className="mt-2 flex items-center justify-between">
        <span className="text-[11px] text-[var(--muted-foreground)]">
          {new Date(app.updated_at).toLocaleDateString()}
        </span>
        {advance && (
          <button
            onClick={() => onMove(advance)}
            className="inline-flex items-center gap-1 text-xs text-[var(--primary)] hover:underline"
            aria-label={`Advance to ${STATUS_LABELS[advance]}`}
          >
            {STATUS_LABELS[advance]} <CircleArrowRight className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </Card>
  );
}

function BoardColumn({
  status,
  cards,
  collapsed,
  onToggleCollapse,
  onMove,
}: {
  status: ApplicationStatus;
  cards: Application[];
  collapsed: boolean;
  onToggleCollapse: (s: ApplicationStatus) => void;
  onMove: (id: string, s: ApplicationStatus) => void;
}) {
  // The droppable stays mounted even when collapsed, so a card can still be
  // dropped onto a collapsed stage (it lands at the end of that column).
  const { setNodeRef, isOver } = useDroppable({ id: `column:${status}` });

  if (collapsed) {
    return (
      <button
        ref={setNodeRef}
        type="button"
        onClick={() => onToggleCollapse(status)}
        aria-expanded={false}
        aria-label={`Expand ${STATUS_LABELS[status]} column (${cards.length})`}
        className={cn(
          'flex w-10 shrink-0 flex-col items-center gap-2 rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--at-surface-2)] py-3 transition-colors hover:bg-[var(--accent)]',
          isOver && 'ring-2 ring-[var(--primary)]/40'
        )}
      >
        <ChevronRight className="h-4 w-4 text-[var(--muted-foreground)]" />
        <span className="[writing-mode:vertical-rl] rotate-180 text-xs font-medium">
          {STATUS_LABELS[status]}
        </span>
        <Badge variant="outline">{cards.length}</Badge>
      </button>
    );
  }

  return (
    <div className="w-72 shrink-0">
      <div className="mb-2 flex items-center justify-between px-1">
        <span className="flex items-center gap-1.5 text-sm font-medium">
          {STATUS_LABELS[status]}
          <Badge variant="outline">{cards.length}</Badge>
        </span>
        <button
          type="button"
          onClick={() => onToggleCollapse(status)}
          aria-expanded
          aria-label={`Collapse ${STATUS_LABELS[status]} column`}
          className="rounded p-0.5 text-[var(--muted-foreground)] hover:bg-[var(--accent)] hover:text-[var(--foreground)]"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
      </div>
      <SortableContext
        items={cards.map((c) => c.application_id)}
        strategy={verticalListSortingStrategy}
      >
        <div
          ref={setNodeRef}
          className={cn(
            'min-h-16 space-y-2 rounded-[var(--radius-at-lg)] bg-[var(--at-surface-2)] p-2 transition-colors',
            isOver && 'ring-2 ring-[var(--primary)]/40'
          )}
        >
          {cards.map((app) => (
            <ApplicationCard
              key={app.application_id}
              app={app}
              draggable
              onMove={(s) => onMove(app.application_id, s)}
            />
          ))}
        </div>
      </SortableContext>
    </div>
  );
}

const COLLAPSED_KEY = 'fitwright-board-collapsed';

function loadCollapsed(): Set<ApplicationStatus> {
  if (typeof window === 'undefined') return new Set();
  try {
    const raw = window.localStorage.getItem(COLLAPSED_KEY);
    return new Set(raw ? (JSON.parse(raw) as ApplicationStatus[]) : []);
  } catch {
    return new Set();
  }
}

export default function ApplicationsPage() {
  const { data, isLoading, isError, refetch } = useApplicationsBoard();
  const { toast } = useToast();
  const qc = useQueryClient();
  const [view, setView] = React.useState<'board' | 'list'>('board');
  // On phones the horizontal board is cramped, so default to the list view once
  // after mount (post-hydration to avoid a mismatch; only if the user hasn't
  // already chosen). Guarded so it's a no-op where matchMedia is unavailable.
  const autoViewApplied = React.useRef(false);
  React.useEffect(() => {
    if (autoViewApplied.current) return;
    autoViewApplied.current = true;
    try {
      if (
        typeof window !== 'undefined' &&
        typeof window.matchMedia === 'function' &&
        window.matchMedia('(max-width: 767px)').matches
      ) {
        setView('list');
      }
    } catch {
      /* matchMedia unavailable (e.g. tests) — keep the board default */
    }
  }, []);
  const [board, setBoard] = React.useState<ApplicationColumns>(emptyColumns);
  const [search, setSearch] = React.useState('');
  const [collapsed, setCollapsed] = React.useState<Set<ApplicationStatus>>(loadCollapsed);

  function toggleCollapse(status: ApplicationStatus) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      try {
        window.localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...next]));
      } catch {
        /* best-effort persistence */
      }
      return next;
    });
  }

  // Search filters visible cards by role/company across every column.
  const viewBoard = React.useMemo<ApplicationColumns>(() => {
    const q = search.trim().toLowerCase();
    if (!q) return board;
    const out = emptyColumns();
    for (const s of APPLICATION_STATUS_ORDER) {
      out[s] = (board[s] ?? []).filter((a) =>
        `${a.role ?? ''} ${a.company ?? ''}`.toLowerCase().includes(q)
      );
    }
    return out;
  }, [board, search]);
  const searching = search.trim().length > 0;

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  // Sync local board from server state (source of truth) whenever it changes.
  React.useEffect(() => {
    if (data?.columns) setBoard({ ...emptyColumns(), ...data.columns });
  }, [data?.columns]);

  const total = React.useMemo(
    () => Object.values(board).reduce((n, c) => n + c.length, 0),
    [board]
  );

  async function persistMove(id: string, status: ApplicationStatus, position?: number) {
    try {
      await updateApplication(id, position != null ? { status, position } : { status });
      // Refresh list surfaces (board + home count) so every view reflects the
      // move; the local optimistic board already updated, so this is seamless.
      invalidateApplicationLists(qc);
      toast({ title: `Moved to ${STATUS_LABELS[status]}`, variant: 'success' });
    } catch {
      toast({ title: 'Could not move application', variant: 'error' });
      refetch(); // re-sync authoritative state
    }
  }

  // Menu / quick-advance move (no reorder within column).
  async function onMenuMove(id: string, status: ApplicationStatus) {
    const plan = planMove(board, id, `column:${status}`);
    if (plan) setBoard(plan.next);
    await persistMove(id, status, plan?.position);
  }

  function onDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over) return;
    const plan = planMove(board, String(active.id), String(over.id));
    if (!plan) return;
    setBoard(plan.next);
    void persistMove(String(active.id), plan.status, plan.position);
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Applications</h1>
          <p className="text-sm text-[var(--muted-foreground)]">
            Track each pursuit from tailored resume to offer.
          </p>
        </div>
        {total > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
              <Input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search role or company…"
                aria-label="Search applications"
                className="w-full pl-9 sm:w-56"
              />
            </div>
            <div className="flex gap-1 rounded-[var(--radius-at-lg)] bg-[var(--secondary)] p-1">
              <button
                onClick={() => setView('board')}
                aria-pressed={view === 'board'}
                className={`flex items-center gap-1.5 rounded-[var(--radius-at-md)] px-3 py-1.5 text-sm ${view === 'board' ? 'bg-[var(--card)] shadow-[var(--shadow-at-e1)]' : 'text-[var(--muted-foreground)]'}`}
              >
                <Columns className="h-4 w-4" /> Board
              </button>
              <button
                onClick={() => setView('list')}
                aria-pressed={view === 'list'}
                className={`flex items-center gap-1.5 rounded-[var(--radius-at-md)] px-3 py-1.5 text-sm ${view === 'list' ? 'bg-[var(--card)] shadow-[var(--shadow-at-e1)]' : 'text-[var(--muted-foreground)]'}`}
              >
                <ListIcon className="h-4 w-4" /> List
              </button>
            </div>
          </div>
        )}
      </div>

      {isLoading ? (
        <LoadingSkeleton rows={4} />
      ) : isError ? (
        <ErrorState description="Could not load your applications." onRetry={() => refetch()} />
      ) : total === 0 ? (
        <EmptyState
          icon={Layers}
          title="No applications yet"
          description="Tailor a resume to a job and it will appear here automatically."
          action={
            <Button asChild>
              <Link href="/tailor">
                Tailor to a job <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
          }
        />
      ) : view === 'board' ? (
        <DndContext sensors={sensors} collisionDetection={closestCorners} onDragEnd={onDragEnd}>
          <div className="flex gap-3 overflow-x-auto pb-4">
            {APPLICATION_STATUS_ORDER.map((status) => (
              <BoardColumn
                key={status}
                status={status}
                cards={viewBoard[status] ?? []}
                // While searching, force-expand so matches are never hidden.
                collapsed={collapsed.has(status) && !searching}
                onToggleCollapse={toggleCollapse}
                onMove={onMenuMove}
              />
            ))}
          </div>
        </DndContext>
      ) : (
        <div className="space-y-2">
          {APPLICATION_STATUS_ORDER.flatMap((s) => viewBoard[s] ?? []).map((app) => (
            <ApplicationCard
              key={app.application_id}
              app={app}
              draggable={false}
              onMove={(s) => onMenuMove(app.application_id, s)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
