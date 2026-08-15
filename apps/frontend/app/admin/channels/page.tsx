'use client';

/**
 * Admin AI Channels (spec: ai-provider-admin, Phase 1).
 *
 * The operator's provider routes: add, credential, test-order, bench and retire.
 * Traffic goes to the healthy active channel with the lowest priority number and
 * falls back down the list on a retryable error.
 *
 * Three things this page is careful about, each mirroring a server rule rather
 * than inventing a client-side one:
 *
 * 1. A channel key is never displayed, because the API never returns one. The
 *    column shows presence only. Re-entering a key replaces it.
 * 2. Deleting an active channel is refused - it must be drained first, so it
 *    cannot vanish from under an in-flight request. The UI leads with Drain for
 *    active channels rather than offering a Delete that will fail.
 * 3. Live health is shown next to configuration, because "why is this channel not
 *    serving traffic?" is the question an operator actually has, and the answer
 *    (cooling down, N consecutive failures, last error class) is otherwise
 *    invisible.
 */
import * as React from 'react';
import Plus from 'lucide-react/dist/esm/icons/plus';
import Trash2 from 'lucide-react/dist/esm/icons/trash-2';
import Pause from 'lucide-react/dist/esm/icons/pause';
import Play from 'lucide-react/dist/esm/icons/play';

import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atelier/select';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from '@/components/atelier/dialog';
import { LoadingSkeleton, EmptyState, ErrorState } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import {
  useAiChannels,
  useCreateAiChannel,
  useDeleteAiChannel,
  useTestAiChannel,
  useUpdateAiChannel,
} from '@/features/admin/hooks';
import type { AiChannel } from '@/lib/api/admin';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import Zap from 'lucide-react/dist/esm/icons/zap';

const PROVIDERS = [
  'openai',
  'anthropic',
  'gemini',
  'openrouter',
  'deepseek',
  'groq',
  'ollama',
  'openai_compatible',
];

/** Providers that normally run without auth - requiring a key would make a
 *  working local setup unusable. */
const KEYLESS_PROVIDERS = new Set(['ollama', 'openai_compatible']);

function StateBadge({ channel }: { channel: AiChannel }) {
  if (channel.state === 'active') {
    // Active but benched is the confusing case an operator most needs named.
    if (channel.cooling_until && new Date(channel.cooling_until) > new Date()) {
      return <Badge variant="warning">cooling down</Badge>;
    }
    return <Badge variant="success">active</Badge>;
  }
  if (channel.state === 'draining') return <Badge variant="warning">draining</Badge>;
  return <Badge variant="neutral">disabled</Badge>;
}

function VerdictBadge({ verdict }: { verdict: AiChannel['structured_verdict'] }) {
  if (verdict === 'reliable') return <Badge variant="success">JSON reliable</Badge>;
  if (verdict === 'flaky') return <Badge variant="warning">JSON flaky</Badge>;
  if (verdict === 'unsupported') return <Badge variant="danger">no JSON</Badge>;
  return <Badge variant="neutral">JSON untested</Badge>;
}

function AddChannelDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const create = useCreateAiChannel();
  const { toast } = useToast();
  const [name, setName] = React.useState('');
  const [provider, setProvider] = React.useState('openai');
  const [model, setModel] = React.useState('');
  const [apiBase, setApiBase] = React.useState('');
  const [apiKey, setApiKey] = React.useState('');
  const [priority, setPriority] = React.useState('100');

  const needsKey = !KEYLESS_PROVIDERS.has(provider);
  const canSubmit = name.trim() && model.trim() && (!needsKey || apiKey.trim());

  async function submit() {
    try {
      await create.mutateAsync({
        name: name.trim(),
        provider,
        model: model.trim(),
        api_base: apiBase.trim() || null,
        priority: Number(priority) || 100,
        api_key: apiKey.trim() || undefined,
      });
      toast({
        title: 'Channel added',
        description: 'It starts disabled. Activate it when you are ready to send traffic.',
        variant: 'success',
      });
      onOpenChange(false);
      setName('');
      setModel('');
      setApiKey('');
      setApiBase('');
    } catch (err) {
      toast({
        title: 'Could not add the channel',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a provider channel</DialogTitle>
          <DialogDescription>
            New channels start disabled, so nothing is sent to them until you activate them.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="ch-name">Name</Label>
            <Input
              id="ch-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="OpenAI primary"
            />
            <p className="text-xs text-[var(--muted-foreground)]">
              Your own label. Two channels may use the same provider with different keys.
            </p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Provider</Label>
              <Select value={provider} onValueChange={setProvider}>
                <SelectTrigger aria-label="Provider">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PROVIDERS.map((p) => (
                    <SelectItem key={p} value={p}>
                      {p}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ch-priority">Priority</Label>
              <Input
                id="ch-priority"
                type="number"
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
              />
              <p className="text-xs text-[var(--muted-foreground)]">Lower is tried first.</p>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ch-model">Model</Label>
            <Input
              id="ch-model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="gpt-5-nano-2025-08-07"
            />
          </div>
          {KEYLESS_PROVIDERS.has(provider) && (
            <div className="space-y-1.5">
              <Label htmlFor="ch-base">Base URL</Label>
              <Input
                id="ch-base"
                value={apiBase}
                onChange={(e) => setApiBase(e.target.value)}
                placeholder="http://localhost:11434"
              />
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="ch-key">API key{needsKey ? '' : ' (optional)'}</Label>
            <Input
              id="ch-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="off"
            />
            <p className="text-xs text-[var(--muted-foreground)]">
              Stored encrypted and never shown again. This app cannot display a saved key back to
              you.
            </p>
          </div>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancel</Button>
          </DialogClose>
          <Button loading={create.isPending} disabled={!canSubmit} onClick={() => void submit()}>
            Add channel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ChannelRow({ channel }: { channel: AiChannel }) {
  const update = useUpdateAiChannel();
  const del = useDeleteAiChannel();
  const test = useTestAiChannel();
  const { toast } = useToast();
  const [confirmDelete, setConfirmDelete] = React.useState(false);

  async function runTest() {
    try {
      const result = await test.mutateAsync(channel.id);
      toast({
        title: result.ok ? `Responded in ${result.latency_ms}ms` : 'The channel failed',
        // The server's own explanation is the useful part - "check the API key" versus
        // "the provider does not recognise this model" send the operator to different
        // places.
        description: result.message,
        variant: result.ok ? 'success' : 'error',
      });
    } catch (err) {
      toast({
        title: 'Could not test the channel',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  async function setState(state: AiChannel['state']) {
    try {
      await update.mutateAsync({ id: channel.id, patch: { state } });
      toast({ title: `Channel ${state}`, variant: 'success' });
    } catch (err) {
      toast({
        title: 'Could not change the channel',
        // The server's reason is the useful part - e.g. "Add an API key before
        // activating this channel."
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  async function remove() {
    try {
      await del.mutateAsync(channel.id);
      toast({ title: 'Channel deleted', variant: 'success' });
      setConfirmDelete(false);
    } catch (err) {
      toast({
        title: 'Could not delete the channel',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  const benched = channel.cooling_until && new Date(channel.cooling_until) > new Date();

  return (
    <Card className="space-y-3 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 font-medium">
            <span className="truncate">{channel.name}</span>
            <StateBadge channel={channel} />
            <VerdictBadge verdict={channel.structured_verdict} />
            {!channel.has_key && !KEYLESS_PROVIDERS.has(channel.provider) && (
              <Badge variant="danger">no key</Badge>
            )}
          </p>
          <p className="text-xs text-[var(--muted-foreground)]">
            {channel.provider} · {channel.model} · priority {channel.priority}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {/* Before activating, not after. Testing costs a few tokens and answers the
              question that would otherwise be answered by real users failing. */}
          <Button
            size="sm"
            variant="outline"
            loading={test.isPending}
            onClick={() => void runTest()}
          >
            <Zap className="h-4 w-4" /> Test
          </Button>
          {channel.state !== 'active' && (
            <Button
              size="sm"
              variant="outline"
              loading={update.isPending}
              onClick={() => void setState('active')}
            >
              <Play className="h-4 w-4" /> Activate
            </Button>
          )}
          {channel.state === 'active' && (
            // Drain, not Disable: in-flight requests finish. This is also the
            // required step before deletion, so leading with it here means the
            // operator never meets a Delete that refuses.
            <Button
              size="sm"
              variant="outline"
              loading={update.isPending}
              onClick={() => void setState('draining')}
            >
              <Pause className="h-4 w-4" /> Drain
            </Button>
          )}
          {channel.state === 'draining' && (
            <Button
              size="sm"
              variant="outline"
              loading={update.isPending}
              onClick={() => void setState('disabled')}
            >
              Disable
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            className="text-[var(--destructive)]"
            disabled={channel.state === 'active'}
            title={
              channel.state === 'active'
                ? 'Drain this channel first so in-flight requests can finish'
                : undefined
            }
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Health. Shown inline because "why is this not serving traffic?" is the
          operator's actual question, and the answer is otherwise invisible. */}
      {(benched || channel.consecutive_failures > 0 || channel.last_error_class) && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-[var(--radius-at-md)] bg-[var(--at-surface-2)] px-3 py-2 text-xs">
          {benched && (
            <span className="text-[var(--at-warning)]">
              Benched until {new Date(channel.cooling_until as string).toLocaleTimeString()}
            </span>
          )}
          {channel.consecutive_failures > 0 && (
            <span>{channel.consecutive_failures} consecutive failures</span>
          )}
          {channel.last_error_class && <span>last error: {channel.last_error_class}</span>}
          {channel.last_ok_at && (
            <span className="text-[var(--muted-foreground)]">
              last success {new Date(channel.last_ok_at).toLocaleString()}
            </span>
          )}
        </div>
      )}

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this channel?</DialogTitle>
            <DialogDescription>
              {channel.name} will be removed along with its stored credential. Traffic will move to
              the next channel in priority order.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button variant="destructive" loading={del.isPending} onClick={() => void remove()}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

export default function AdminChannelsPage() {
  const { data, isLoading, isError, refetch } = useAiChannels();
  const [addOpen, setAddOpen] = React.useState(false);

  const activeCount = (data ?? []).filter((c) => c.state === 'active').length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">AI channels</h1>
          <p className="text-sm text-[var(--muted-foreground)]">
            Provider routes, tried in priority order. If one fails or is rate-limited, traffic moves
            to the next.
          </p>
        </div>
        <Button onClick={() => setAddOpen(true)}>
          <Plus className="h-4 w-4" /> Add channel
        </Button>
      </div>

      {/* A single active channel is worth naming: it is configuration that looks
          finished but provides no failover, which is the whole point of this page. */}
      {activeCount === 1 && (
        <Card className="flex items-start gap-3 border-[var(--at-warning)]/40 bg-[var(--at-warning)]/8 p-4">
          <Sparkles className="mt-0.5 h-5 w-5 shrink-0 text-[var(--at-warning)]" />
          <div>
            <p className="text-sm font-medium">Only one active channel</p>
            <p className="text-xs text-[var(--muted-foreground)]">
              There is nowhere to fall back to, so a provider outage is an outage for your users.
              Add a second channel from a different provider.
            </p>
          </div>
        </Card>
      )}

      {isLoading ? (
        <LoadingSkeleton rows={3} />
      ) : isError ? (
        <ErrorState description="Could not load channels." onRetry={() => refetch()} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={Sparkles}
          title="No channels yet"
          description="Add a provider channel so your users can use AI without supplying their own key."
          action={<Button onClick={() => setAddOpen(true)}>Add channel</Button>}
        />
      ) : (
        <div className="space-y-3">
          {(data ?? []).map((c) => (
            <ChannelRow key={c.id} channel={c} />
          ))}
        </div>
      )}

      <AddChannelDialog open={addOpen} onOpenChange={setAddOpen} />
    </div>
  );
}
